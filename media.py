"""Enregistrement des fichiers téléchargés dans les dossiers publics du téléphone.

Les fichiers bruts de YouTube sont des MP4 "DASH" fragmentés, mal lus par certains lecteurs.
Sur Android on les réécrit avec MediaMuxer (qui sait aussi fusionner la vidéo sans son et l'audio)
dans Musique/Younegui ou Films/Younegui. Sur PC (développement), on copie simplement les fichiers.
"""
import os
import re
import shutil

from kivy.utils import platform

APP_FOLDER = "Younegui"


def safe_name(title):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", title).strip().rstrip(".")
    return (name or "video")[:120]


if platform == "android":
    from jnius import autoclass

    MediaExtractor = autoclass("android.media.MediaExtractor")
    MediaMuxer = autoclass("android.media.MediaMuxer")
    MuxerOutputFormat = autoclass("android.media.MediaMuxer$OutputFormat")
    BufferInfo = autoclass("android.media.MediaCodec$BufferInfo")
    ByteBuffer = autoclass("java.nio.ByteBuffer")
    BuildVersion = autoclass("android.os.Build$VERSION")
    Environment = autoclass("android.os.Environment")
    ContentValues = autoclass("android.content.ContentValues")
    MediaStoreAudio = autoclass("android.provider.MediaStore$Audio$Media")
    MediaStoreVideo = autoclass("android.provider.MediaStore$Video$Media")
    MediaScannerConnection = autoclass("android.media.MediaScannerConnection")
    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    Integer = autoclass("java.lang.Integer")

    def _mux(sources, open_muxer):
        """Copie toutes les pistes audio/vidéo des fichiers `sources` dans un nouveau MP4."""
        extractors = []
        muxer = open_muxer()
        try:
            for path in sources:
                ex = MediaExtractor()
                ex.setDataSource(path)
                for i in range(ex.getTrackCount()):
                    fmt = ex.getTrackFormat(i)
                    mime = fmt.getString("mime") or ""
                    if mime.startswith("audio/") or mime.startswith("video/"):
                        ex.selectTrack(i)
                        extractors.append((ex, muxer.addTrack(fmt)))
                        break
            muxer.start()
            buf = ByteBuffer.allocate(4 * 1024 * 1024)
            info = BufferInfo()
            # On entrelace les pistes par horodatage pour que MediaMuxer n'accumule rien en mémoire
            pending = [[ex, track, ex.getSampleTime()] for ex, track in extractors]
            while pending:
                cur = min(pending, key=lambda p: p[2])
                ex, track, sample_time = cur
                size = ex.readSampleData(buf, 0)
                if size < 0 or sample_time < 0:
                    pending.remove(cur)
                    continue
                # SAMPLE_FLAG_SYNC et BUFFER_FLAG_KEY_FRAME valent tous deux 1
                info.set(0, size, sample_time, ex.getSampleFlags() & 1)
                muxer.writeSampleData(track, buf, info)
                ex.advance()
                cur[2] = ex.getSampleTime()
            muxer.stop()
        finally:
            muxer.release()
            for ex, _ in extractors:
                ex.release()

    def _save(sources, filename, mime, kind):
        resolver = PythonActivity.mActivity.getContentResolver()
        if BuildVersion.SDK_INT >= 29:
            # Android 10+ : MediaStore, sans permission de stockage
            base = Environment.DIRECTORY_MUSIC if kind == "audio" else Environment.DIRECTORY_MOVIES
            collection = (MediaStoreAudio if kind == "audio" else MediaStoreVideo).EXTERNAL_CONTENT_URI
            values = ContentValues()
            values.put("_display_name", filename)
            values.put("mime_type", mime)
            values.put("relative_path", base + "/" + APP_FOLDER)
            values.put("is_pending", Integer(1))
            uri = resolver.insert(collection, values)
            pfd = resolver.openFileDescriptor(uri, "rw")
            try:
                _mux(sources, lambda: MediaMuxer(pfd.getFileDescriptor(), MuxerOutputFormat.MUXER_OUTPUT_MPEG_4))
            except Exception:
                pfd.close()
                resolver.delete(uri, None, None)
                raise
            pfd.close()
            done = ContentValues()
            done.put("is_pending", Integer(0))
            resolver.update(uri, done, None, None)
            return "%s/%s/%s" % (base, APP_FOLDER, filename)
        # Android 7 à 9 : chemin direct (permission WRITE_EXTERNAL_STORAGE)
        base = Environment.DIRECTORY_MUSIC if kind == "audio" else Environment.DIRECTORY_MOVIES
        folder = os.path.join(Environment.getExternalStoragePublicDirectory(base).getAbsolutePath(), APP_FOLDER)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        _mux(sources, lambda: MediaMuxer(path, MuxerOutputFormat.MUXER_OUTPUT_MPEG_4))
        MediaScannerConnection.scanFile(PythonActivity.mActivity, [path], [mime], None)
        return path

    def save_audio(audio_path, title):
        return _save([audio_path], safe_name(title) + ".m4a", "audio/mp4", "audio")

    def save_video(video_path, audio_path, title):
        return _save([video_path, audio_path], safe_name(title) + ".mp4", "video/mp4", "video")

else:
    def _desktop_folder(kind):
        home = os.path.expanduser("~")
        folder = os.path.join(home, "Music" if kind == "audio" else "Videos", APP_FOLDER)
        os.makedirs(folder, exist_ok=True)
        return folder

    def save_audio(audio_path, title):
        dest = os.path.join(_desktop_folder("audio"), safe_name(title) + ".m4a")
        shutil.copyfile(audio_path, dest)
        return dest

    def save_video(video_path, audio_path, title):
        # Pas de MediaMuxer sur PC : la vidéo et l'audio sont copiés côte à côte.
        base = os.path.join(_desktop_folder("video"), safe_name(title))
        shutil.copyfile(video_path, base + ".mp4")
        shutil.copyfile(audio_path, base + ".m4a")
        return base + ".mp4"
