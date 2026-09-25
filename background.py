"""Lecture en arrière-plan / écran éteint, côté appli.

Android "gèle" le processus de l'appli quand elle n'est plus visible. La lecture est donc confiée
au service de premier plan `Player` (service/player_service.py, processus séparé), qui ne lit
que l'audio : c'est la qualité minimale, rien n'étant affiché écran éteint.

Le service est démarré dès qu'une vidéo est lue (Android interdit de le démarrer une fois l'appli
en arrière-plan) et attend. Les deux processus communiquent par deux fichiers JSON :
- cmd.json   (appli -> service) : {"seq", "action": "play" | "handback" | "quit", ...}
- state.json (service -> appli) : {"ack", "video", "position", "queue_index", "playing"}
"""
import json
import os
import time

# Pas d'import Kivy ici : ce module est aussi chargé par le service, qui n'a pas d'interface.

CMD_FILE = "bg_cmd.json"
STATE_FILE = "bg_state.json"


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


class Background:
    def __init__(self, folder):
        self.folder = folder
        self.cmd_path = os.path.join(folder, CMD_FILE)
        self.state_path = os.path.join(folder, STATE_FILE)
        self.seq = int(time.time() * 1000)
        self.running = False
        self.active = False  # le service est-il en train de lire à notre place ?

    def _send(self, action, **payload):
        self.seq += 1
        payload.update(seq=self.seq, action=action)
        write_json(self.cmd_path, payload)
        return self.seq

    def ensure_started(self):
        from kivy.utils import platform

        if self.running or platform != "android":
            return
        from jnius import autoclass

        self._send("idle")
        service = autoclass("org.younegui.younegui.ServicePlayer")
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        service.start(activity, "", "Younegui", "Prêt pour la lecture en arrière-plan", self.folder)
        self.running = True

    def take_over(self, video, stream, position, queue, queue_index, autoplay=True):
        """L'appli passe en arrière-plan : le service continue en audio seul."""
        if not self.running or stream is None or stream.audio is None:
            return False
        self._send(
            "play",
            video=video,
            audio_url=stream.audio["url"],
            headers=stream.audio.get("http_headers") or stream.headers,
            position=position,
            queue=queue,
            queue_index=queue_index,
            autoplay=autoplay,
        )
        self.active = True
        return True

    def recover(self, max_age=5):
        """Au lancement : si le service joue encore (appli fermée par Android entre-temps), on lui
        reprend la main et on renvoie son état, plus récent que celui mémorisé par l'appli."""
        try:
            age = time.time() - os.path.getmtime(self.state_path)
        except OSError:
            return None
        state = read_json(self.state_path)
        if age > max_age or not state or not state.get("playing"):
            return None  # le service réécrit son état toutes les 0,3 s : sinon il est arrêté
        self.active = True
        return self.hand_back()

    def hand_back(self, timeout=1.5):
        """L'appli revient au premier plan : on arrête l'audio du service et on récupère où il en est."""
        if not self.active:
            return None
        self.active = False
        seq = self._send("handback")
        end = time.time() + timeout
        while time.time() < end:
            state = read_json(self.state_path)
            if state and state.get("ack") == seq:
                return state
            time.sleep(0.05)
        return read_json(self.state_path)

    def stop(self):
        if not self.running:
            return
        self._send("quit")
        from jnius import autoclass

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        autoclass("org.younegui.younegui.ServicePlayer").stop(activity)
        self.running = False
        self.active = False
