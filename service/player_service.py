"""Service Android de premier plan (type mediaPlayback) : lecture audio quand l'appli est en arrière-plan.

Tourne dans son propre processus, qu'Android ne gèle pas. Reçoit ses ordres via bg_cmd.json et
publie son état dans bg_state.json (voir background.py). Le dossier d'échange est passé en argument.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

from jnius import autoclass  # noqa: E402

from background import CMD_FILE, STATE_FILE, read_json, write_json  # noqa: E402

PythonService = autoclass("org.kivy.android.PythonService")
MediaPlayer = autoclass("android.media.MediaPlayer")
AudioAttributesBuilder = autoclass("android.media.AudioAttributes$Builder")
AudioAttributes = autoclass("android.media.AudioAttributes")
PowerManager = autoclass("android.os.PowerManager")
Uri = autoclass("android.net.Uri")
HashMap = autoclass("java.util.HashMap")

service = PythonService.mService
folder = os.environ.get("PYTHON_SERVICE_ARGUMENT") or os.getcwd()
cmd_path = os.path.join(folder, CMD_FILE)
state_path = os.path.join(folder, STATE_FILE)

mp = None
# position = dernière position connue, conservée après l'arrêt pour que l'appli reprenne au bon endroit
current = {"video": None, "queue": [], "queue_index": -1, "position": 0.0, "autoplay": True}


def release():
    global mp
    if mp is not None:
        try:
            mp.stop()
        except Exception:
            pass
        mp.release()
        mp = None


def position():
    if mp is not None:
        try:
            current["position"] = mp.getCurrentPosition() / 1000.0
        except Exception:
            pass
    return current["position"]


def start(url, headers, start_pos):
    global mp
    release()
    mp = MediaPlayer()
    mp.setWakeMode(service, PowerManager.PARTIAL_WAKE_LOCK)
    mp.setAudioAttributes(
        AudioAttributesBuilder()
        .setUsage(AudioAttributes.USAGE_MEDIA)
        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
        .build()
    )
    jheaders = HashMap()
    for key, value in (headers or {}).items():
        jheaders.put(key, value)
    mp.setDataSource(service, Uri.parse(url), jheaders)
    mp.prepare()
    if start_pos > 0:
        mp.seekTo(int(start_pos * 1000))
    mp.start()


def play_next():
    """Vidéo terminée : on passe à la suivante de la playlist, s'il y en a une."""
    import yt

    queue, idx = current["queue"], current["queue_index"] + 1
    if idx >= len(queue) and current["video"] and current["autoplay"]:
        # Fin de la file : on continue avec les vidéos proposées par YouTube, comme l'appli
        try:
            seen = {v["id"] for v in queue} | {current["video"]["id"]}
            queue.extend(dict(v, suggested=True) for v in yt.related(current["video"]["id"]) if v["id"] not in seen)
        except Exception as exc:
            print("Younegui service: suggestions indisponibles", exc)
    while 0 <= idx < len(queue):
        if queue[idx].get("suggested") and not current["autoplay"]:
            break
        try:
            stream = yt.resolve(queue[idx]["id"])
            if stream.audio:
                current.update(video=queue[idx], queue_index=idx, position=0.0)
                start(stream.audio["url"], stream.audio.get("http_headers") or stream.headers, 0)
                return True
        except Exception as exc:
            print("Younegui service: vidéo ignorée", queue[idx].get("id"), exc)
        idx += 1
    release()
    return False


def main():
    last_seq = None
    finished = False
    while True:
        cmd = read_json(cmd_path)
        if cmd and cmd.get("seq") != last_seq:
            last_seq = cmd["seq"]
            action = cmd.get("action")
            if action == "play":
                current.update(video=cmd["video"], queue=cmd.get("queue") or [],
                               queue_index=cmd.get("queue_index", -1), position=cmd.get("position", 0),
                               autoplay=cmd.get("autoplay", True))
                finished = False
                try:
                    start(cmd["audio_url"], cmd.get("headers"), cmd.get("position", 0))
                except Exception as exc:
                    print("Younegui service: lecture impossible", exc)
                    release()
            elif action in ("handback", "quit"):
                position()
                release()
                write_json(state_path, dict(current, ack=last_seq, playing=False, finished=finished))
                if action == "quit":
                    break
                continue

        if mp is not None:
            try:
                ended = (not mp.isPlaying()) and mp.getDuration() > 0 and \
                    mp.getCurrentPosition() >= mp.getDuration() - 1000
            except Exception:
                ended = False
            if ended and not play_next():
                finished = True

        position()
        write_json(state_path, dict(current, ack=last_seq, playing=mp is not None, finished=finished))
        time.sleep(0.3)

    service.stopSelf()


main()
