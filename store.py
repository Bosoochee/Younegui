"""Données persistantes de l'appli (favoris, chaînes, historique, téléchargements, paramètres).

Tout est stocké en JSON dans le dossier privé de l'appli. Une vidéo est un dict
{"id", "title", "channel", "channel_id", "duration"} ; une chaîne {"id", "name"}.
"""
import json
import os
import threading
import time

HISTORY_MAX = 200
RESOLUTIONS = [144, 240, 360, 480, 720, 1080]

DEFAULTS = {
    "favorites": [],
    "channels": [],
    "history": [],
    "audio_done": [],
    "resume": None,  # dernière vidéo lue, reprise au lancement
    "settings": {"resolution": 720, "autoplay": True},
}

VIDEO_KEYS = ("id", "title", "channel", "channel_id", "duration")


def slim(video):
    return {k: video.get(k) for k in VIDEO_KEYS}


class Store:
    def __init__(self, folder):
        os.makedirs(folder, exist_ok=True)
        self.path = os.path.join(folder, "younegui.json")
        self._lock = threading.Lock()
        self.data = json.loads(json.dumps(DEFAULTS))
        try:
            with open(self.path, encoding="utf-8") as fh:
                saved = json.load(fh)
            for key in DEFAULTS:
                if key in saved:
                    self.data[key] = saved[key]
        except (OSError, ValueError):
            pass

    def save(self):
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False)
            os.replace(tmp, self.path)

    # Favoris (servent aussi de playlist, dans l'ordre d'ajout)
    def is_favorite(self, video_id):
        return any(v["id"] == video_id for v in self.data["favorites"])

    def toggle_favorite(self, video):
        """Ajoute ou retire la vidéo ; renvoie True si elle est maintenant en favori."""
        favs = self.data["favorites"]
        if self.is_favorite(video["id"]):
            favs[:] = [v for v in favs if v["id"] != video["id"]]
            added = False
        else:
            favs.append(slim(video))
            added = True
        self.save()
        return added

    # Chaînes suivies
    def is_subscribed(self, channel_id):
        return any(c["id"] == channel_id for c in self.data["channels"])

    def toggle_subscription(self, channel_id, name):
        chans = self.data["channels"]
        if self.is_subscribed(channel_id):
            chans[:] = [c for c in chans if c["id"] != channel_id]
            subscribed = False
        else:
            chans.append({"id": channel_id, "name": name})
            subscribed = True
        self.save()
        return subscribed

    # Historique (le plus récent en premier, sans doublon)
    def add_history(self, video):
        hist = [v for v in self.data["history"] if v["id"] != video["id"]]
        entry = slim(video)
        entry["watched_at"] = int(time.time())
        hist.insert(0, entry)
        self.data["history"] = hist[:HISTORY_MAX]
        self.save()

    def clear_history(self):
        self.data["history"] = []
        self.save()

    # Audios déjà téléchargés (pour afficher la note en vert)
    def is_audio_done(self, video_id):
        return video_id in self.data["audio_done"]

    def mark_audio_done(self, video_id):
        if video_id not in self.data["audio_done"]:
            self.data["audio_done"].append(video_id)
            self.save()

    # Reprise au lancement
    def set_resume(self, video, position, queue, queue_index):
        def entry(v):
            e = slim(v)
            if v.get("suggested"):
                e["suggested"] = True
            return e

        self.data["resume"] = {
            "video": slim(video),
            "position": round(position or 0, 1),
            "queue": [entry(v) for v in queue],
            "queue_index": queue_index,
        }
        self.save()

    def clear_resume(self):
        if self.data["resume"] is not None:
            self.data["resume"] = None
            self.save()

    # Paramètres
    @property
    def resolution(self):
        return self.data["settings"].get("resolution", 720)

    @resolution.setter
    def resolution(self, value):
        self.data["settings"]["resolution"] = int(value)
        self.save()

    @property
    def autoplay(self):
        return self.data["settings"].get("autoplay", True)

    @autoplay.setter
    def autoplay(self, value):
        self.data["settings"]["autoplay"] = bool(value)
        self.save()
