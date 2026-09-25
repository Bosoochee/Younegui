"""Accès à YouTube via yt-dlp : recherche, vidéos d'une chaîne, flux d'une vidéo.

Sans moteur JavaScript (impossible à embarquer sur Android), YouTube fournit quand même :
- un manifeste HLS "maître" contenant toutes les résolutions + l'audio (utilisé pour la lecture) ;
- des fichiers directs : audio AAC (format 140) et vidéo H.264 sans son (133..137), pour les téléchargements.
Tous les appels sont bloquants : les lancer hors du thread de l'interface.
"""
import itertools
import ssl
from concurrent.futures import ThreadPoolExecutor
import urllib.request

import yt_dlp

try:
    import certifi

    # L'OpenSSL embarqué sur Android ignore SSL_CERT_FILE : on passe les certificats de certifi explicitement
    _SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL = ssl.create_default_context()

WATCH_URL = "https://www.youtube.com/watch?v="
SEARCH_COUNT = 25
CHANNEL_COUNT = 30
RELATED_COUNT = 25
HOME_COUNT = 30
HOME_QUERY = "musique populaire"  # dernier recours quand il n'y a ni historique ni abonnement

_FLAT = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
_FULL = {"quiet": True, "no_warnings": True, "skip_download": True}


def thumbnail(video_id):
    return "https://i.ytimg.com/vi/%s/mqdefault.jpg" % video_id


def _entry(e):
    return {
        "id": e.get("id"),
        "title": e.get("title") or "",
        "channel": e.get("channel") or e.get("uploader") or "",
        "channel_id": e.get("channel_id") or "",
        "duration": int(e.get("duration") or 0),
        "live": e.get("live_status") == "is_live",
    }


def _flat(url, count):
    opts = dict(_FLAT, playlistend=count)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return [_entry(e) for e in info.get("entries") or [] if e.get("id") and len(e["id"]) == 11]


def search(query):
    return _flat("ytsearch%d:%s" % (SEARCH_COUNT, query), SEARCH_COUNT)


def channel_videos(channel_id, name=""):
    videos = _flat("https://www.youtube.com/channel/%s/videos" % channel_id, CHANNEL_COUNT)
    for v in videos:
        v["channel"] = v["channel"] or name
        v["channel_id"] = v["channel_id"] or channel_id
    return videos


def related(video_id):
    """Vidéos proposées après video_id : la « mix » YouTube, sans video_id lui-même."""
    videos = _flat("%s%s&list=RD%s" % (WATCH_URL, video_id, video_id), RELATED_COUNT)
    return [v for v in videos if v["id"] != video_id]


def home(seed_ids, channel_ids=(), exclude=()):
    """Suggestions d'accueil (YouTube n'a pas de page d'accueil sans compte) : les « mix » des vidéos
    seed_ids, à défaut les dernières vidéos des chaînes suivies, à défaut une recherche générique."""
    def safe(fn, arg):
        try:
            return fn(arg)
        except Exception:
            return []

    with ThreadPoolExecutor(4) as pool:
        lists = list(pool.map(lambda vid: safe(related, vid), seed_ids))
        if not any(lists):
            lists = list(pool.map(lambda cid: safe(channel_videos, cid)[:10], list(channel_ids)[:4]))
    if not any(lists):
        lists = [search(HOME_QUERY)]
    # On entrelace les listes pour mélanger les sources
    seen, videos = set(exclude), []
    for group in itertools.zip_longest(*lists):
        for v in group:
            if v and v["id"] not in seen and not v.get("live"):
                seen.add(v["id"])
                videos.append(v)
    return videos[:HOME_COUNT]


class Stream:
    """Tout ce qu'il faut pour lire ou télécharger une vidéo."""

    def __init__(self, info):
        self.video = _entry(info)
        self.headers = {}
        self.hls_url = None
        self.audio = None          # format audio direct (dict yt-dlp)
        self.videos_direct = []    # formats vidéo H.264 directs, triés par hauteur croissante
        self.heights = []          # résolutions disponibles en lecture

        formats = info.get("formats") or []
        for f in formats:
            if not f.get("url"):
                continue
            proto = f.get("protocol") or ""
            if proto.startswith("m3u8") and f.get("manifest_url"):
                self.hls_url = self.hls_url or f["manifest_url"]
                self.headers = self.headers or dict(f.get("http_headers") or {})
                if f.get("height"):
                    self.heights.append(f["height"])
            elif proto == "https":
                vcodec, acodec = f.get("vcodec") or "none", f.get("acodec") or "none"
                if vcodec == "none" and acodec.startswith("mp4a"):
                    if self.audio is None or (f.get("abr") or 0) > (self.audio.get("abr") or 0):
                        self.audio = f
                elif acodec == "none" and vcodec.startswith("avc1") and f.get("height"):
                    self.videos_direct.append(f)
        self.videos_direct.sort(key=lambda f: f["height"])
        self.heights = sorted(set(self.heights))
        if not self.headers and formats:
            self.headers = dict(formats[0].get("http_headers") or {})

    def direct_video(self, max_height):
        """Meilleur fichier vidéo H.264 ne dépassant pas max_height (ou le plus petit)."""
        candidates = [f for f in self.videos_direct if f["height"] <= max_height]
        if candidates:
            return candidates[-1]
        return self.videos_direct[0] if self.videos_direct else None


def resolve(video_id):
    with yt_dlp.YoutubeDL(_FULL) as ydl:
        info = ydl.extract_info(WATCH_URL + video_id, download=False)
    stream = Stream(info)
    if not stream.hls_url and not stream.audio:
        raise RuntimeError("Aucun flux lisible pour cette vidéo")
    return stream


def download(url_format, dest_path, progress=None):
    """Télécharge un format (dict yt-dlp contenant url + http_headers) vers dest_path."""
    req = urllib.request.Request(url_format["url"], headers=url_format.get("http_headers") or {})
    total = url_format.get("filesize") or url_format.get("filesize_approx") or 0
    done = 0
    # YouTube limite le débit des grosses requêtes : on télécharge par tranches de 5 Mo
    chunk = 5 * 1024 * 1024
    with open(dest_path, "wb") as out:
        while True:
            rng = urllib.request.Request(req.full_url, headers=dict(req.headers))
            rng.add_header("Range", "bytes=%d-%d" % (done, done + chunk - 1))
            with urllib.request.urlopen(rng, timeout=30, context=_SSL) as resp:
                data = resp.read()
                if not total:
                    content_range = resp.headers.get("Content-Range", "")
                    if "/" in content_range:
                        total = int(content_range.rsplit("/", 1)[1])
            out.write(data)
            done += len(data)
            if progress and total:
                progress(min(done / total, 1.0))
            if not data or len(data) < chunk or (total and done >= total):
                break
    return dest_path
