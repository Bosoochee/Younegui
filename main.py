"""Younegui : lecteur YouTube léger pour Android (Kivy + yt-dlp)."""
import faulthandler
import os
import re
import shutil
import signal
import threading
import time
import traceback

# Diagnostic : `kill -USR1 <pid>` écrit la pile de tous les threads Python dans faulthandler.txt
if hasattr(signal, "SIGUSR1"):
    _fault_file = open("faulthandler.txt", "w")
    faulthandler.register(signal.SIGUSR1, file=_fault_file, all_threads=True)

try:
    import certifi

    # Le Python embarqué sur Android n'a pas de certificats racine : on utilise ceux de certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

from kivy.animation import Animation
from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.core.text import LabelBase
from kivy.core.window import Window
from kivy.factory import Factory
from kivy.graphics import Color, Rectangle
from kivy.properties import BooleanProperty, ListProperty, NumericProperty, ObjectProperty, StringProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.recycleview.views import RecycleDataViewBehavior
from kivy.uix.slider import Slider
from kivy.uix.widget import Widget
from kivy.utils import get_color_from_hex, platform

import media
import yt
from background import Background
from player import Player
from store import RESOLUTIONS, Store

__version__ = "0.2.0"
AUTHOR = "Bosoochee"
DESCRIPTION = ("Lecteur YouTube léger : recherche, favoris servant de playlist, abonnements, "
               "lecture en arrière-plan et écran éteint, téléchargement vidéo et audio.")

RECENT_HISTORY = 30  # vidéos récemment vues, écartées des suggestions

HERE = os.path.dirname(os.path.abspath(__file__))
LabelBase.register(name="Icons", fn_regular=os.path.join(HERE, "assets", "mdi.ttf"))

# Codes des glyphes de la police Material Design Icons (assets/mdi.ttf)
ICONS = {
    "heart": 0xF02D1, "heart-outline": 0xF02D5, "music-note": 0xF0387, "history": 0xF02DA,
    "download": 0xF01DA, "playlist-play": 0xF0411, "cog": 0xF0493, "magnify": 0xF0349,
    "play": 0xF040A, "pause": 0xF03E4, "close": 0xF0156, "account-plus": 0xF0014,
    "account-check": 0xF0008, "delete-outline": 0xF09E7, "arrow-left": 0xF004D,
    "information-outline": 0xF02FD, "skip-next": 0xF04AD, "skip-previous": 0xF04AE,
    "subscription": 0xF0D40, "fullscreen": 0xF0293, "fullscreen-exit": 0xF0294, "check": 0xF012C, "power": 0xF0425,
}

if platform == "android":
    from android.runnable import run_on_ui_thread
    from jnius import autoclass

    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    BuildVersion = autoclass("android.os.Build$VERSION")
    FLAG_KEEP_SCREEN_ON = 128
    ORIENTATION_FULL_USER = 13
    ORIENTATION_SENSOR_LANDSCAPE = 6

    @run_on_ui_thread
    def keep_screen_on(on):
        window = PythonActivity.mActivity.getWindow()
        if on:
            window.addFlags(FLAG_KEEP_SCREEN_ON)
        else:
            window.clearFlags(FLAG_KEEP_SCREEN_ON)

    @run_on_ui_thread
    def force_landscape(on):
        PythonActivity.mActivity.setRequestedOrientation(
            ORIENTATION_SENSOR_LANDSCAPE if on else ORIENTATION_FULL_USER)

    def request_permissions():
        from android.permissions import request_permissions as request

        wanted = []
        if BuildVersion.SDK_INT >= 33:
            wanted.append("android.permission.POST_NOTIFICATIONS")
        if BuildVersion.SDK_INT < 29:
            wanted.append("android.permission.WRITE_EXTERNAL_STORAGE")
        if wanted:
            request(wanted)

    def move_to_back():
        PythonActivity.mActivity.moveTaskToBack(True)

    @run_on_ui_thread
    def exit_app():
        # Ferme l'activité et la retire des applis récentes ; SDL arrête ensuite Kivy
        PythonActivity.mActivity.finishAndRemoveTask()

    @run_on_ui_thread
    def plain_nav_bar():
        # Bord à bord, Android pose un voile clair derrière les boutons de navigation : on le retire
        if BuildVersion.SDK_INT >= 29:
            PythonActivity.mActivity.getWindow().setNavigationBarContrastEnforced(False)

    @run_on_ui_thread
    def read_insets(callback):
        """Android 15+ affiche l'appli bord à bord, sous les barres système : on mesure leur place (px)."""
        if BuildVersion.SDK_INT < 35:
            return
        insets = PythonActivity.mActivity.getWindow().getDecorView().getRootWindowInsets()
        if insets is None:
            return
        kind = autoclass("android.view.WindowInsets$Type")
        bars = insets.getInsets(kind.systemBars() | kind.displayCutout())
        values = [bars.left, bars.top, bars.right, bars.bottom]
        Clock.schedule_once(lambda _dt: callback(values))
else:
    def keep_screen_on(on):
        pass

    def force_landscape(on):
        pass

    def request_permissions():
        pass

    def move_to_back():
        App.get_running_app().stop()

    def exit_app():
        App.get_running_app().stop()

    def read_insets(callback):
        pass

    def plain_nav_bar():
        pass


def fmt_time(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


# Emojis et pictogrammes : absents de la police Roboto, ils s'afficheraient en carrés
_NO_GLYPH = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE00-\uFE0F\u200D\u20E3"
                       "\U000E0000-\U000E007F]+")


def clean_text(text):
    return " ".join(_NO_GLYPH.sub(" ", text or "").split())


def fmt_ago(ts):
    delta = max(0, int(time.time() - (ts or 0)))
    if delta < 3600:
        return "il y a %d min" % max(1, delta // 60)
    if delta < 86400:
        return "il y a %d h" % (delta // 3600)
    return "il y a %d j" % (delta // 86400)


class VideoView(Widget):
    """Affiche la texture du lecteur en conservant ses proportions, sur fond noir."""
    texture = ObjectProperty(None, allownone=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas:
            Color(0, 0, 0, 1)
            self._bg = Rectangle()
            Color(1, 1, 1, 1)
            self._img = Rectangle()
        self.bind(pos=self._update, size=self._update, texture=self._update)

    def _update(self, *args):
        self._bg.pos, self._bg.size = self.pos, self.size
        tex = self.texture
        if tex is None or not tex.width:
            self._img.size = (0, 0)
            return
        scale = min(self.width / tex.width, self.height / tex.height)
        w, h = tex.width * scale, tex.height * scale
        self._img.texture = tex
        self._img.size = (w, h)
        self._img.pos = (self.x + (self.width - w) / 2, self.y + (self.height - h) / 2)


class SeekBar(Slider):
    """Barre de progression : suit la lecture, sauf pendant que l'utilisateur la fait glisser."""
    dragging = BooleanProperty(False)

    def on_kv_post(self, base_widget):
        App.get_running_app().player.bind(position=self._sync, duration=self._sync)

    def _sync(self, player, _value):
        if not self.dragging:
            self.max = max(player.duration, 1)
            self.value = min(player.position, self.max)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self.dragging = True
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        if self.dragging and touch.grab_current is self:
            self.dragging = False
            App.get_running_app().player.seek(self.value)
        return super().on_touch_up(touch)


class VideoRow(RecycleDataViewBehavior, ButtonBehavior, BoxLayout):
    index = NumericProperty(0)
    video_id = StringProperty("")
    title = StringProperty("")
    subtitle = StringProperty("")
    thumb = StringProperty("")
    mode = StringProperty("result")  # result, channel, history, fav
    subscribed = BooleanProperty(False)
    current = BooleanProperty(False)

    def refresh_view_attrs(self, rv, index, data):
        self.index = index
        return super().refresh_view_attrs(rv, index, data)

    def on_release(self):
        App.get_running_app().open_row(self.mode, self.index)


class ChannelRow(RecycleDataViewBehavior, ButtonBehavior, BoxLayout):
    index = NumericProperty(0)
    name = StringProperty("")

    def refresh_view_attrs(self, rv, index, data):
        self.index = index
        return super().refresh_view_attrs(rv, index, data)

    def on_release(self):
        App.get_running_app().open_channel(self.index)


class Root(FloatLayout):
    pass


class YouneguiApp(App):
    title = "Younegui"
    version = __version__
    author = AUTHOR
    description = DESCRIPTION
    resolutions = RESOLUTIONS

    # Palette
    c_bg = ListProperty(get_color_from_hex("#0E0E13"))
    c_surface = ListProperty(get_color_from_hex("#1A1A23"))
    c_surface2 = ListProperty(get_color_from_hex("#262631"))
    c_text = ListProperty(get_color_from_hex("#F3F3F6"))
    c_muted = ListProperty(get_color_from_hex("#9C9CAB"))
    c_accent = ListProperty(get_color_from_hex("#FF4F8B"))
    c_yellow = ListProperty(get_color_from_hex("#FFD23F"))
    c_orange = ListProperty(get_color_from_hex("#FF8A00"))
    c_green = ListProperty(get_color_from_hex("#2BD67B"))

    player = ObjectProperty(None)
    queue = ListProperty([])               # playlist en cours (favoris, vidéos d'une chaîne)
    queue_index = NumericProperty(-1)
    panel = StringProperty("results")      # results, history, favorites, settings, channel
    fav_tab = StringProperty("videos")     # videos, channels
    has_video = BooleanProperty(False)
    immersive = BooleanProperty(False)     # paysage + vidéo : le lecteur occupe tout l'écran
    forced_landscape = BooleanProperty(False)
    busy = BooleanProperty(False)          # recherche / chaîne en cours de chargement
    video_busy = BooleanProperty(False)    # flux de la vidéo en cours de résolution
    message = StringProperty("")
    insets = ListProperty([0, 0, 0, 0])    # place des barres système (gauche, haut, droite, bas)
    results_mode = StringProperty("suggest")   # suggest (accueil / à suivre) ou search
    suggest_title = StringProperty("Suggestions")

    video_title = StringProperty("")
    video_channel = StringProperty("")
    is_favorite = BooleanProperty(False)
    subscribed = BooleanProperty(False)
    audio_state = StringProperty("idle")   # idle (noir), busy (orange), done (vert)
    resolution = NumericProperty(720)
    autoplay = BooleanProperty(True)       # enchaîner sur les vidéos proposées par YouTube
    channel_title = StringProperty("")
    toast_text = StringProperty("")

    def glyph(self, name):
        return chr(ICONS[name])

    def fmt_time(self, seconds):
        return fmt_time(seconds)

    # ------------------------------------------------------------------ cycle de vie
    def build(self):
        self.store = Store(self.user_data_dir)
        self.bg = Background(self.user_data_dir)
        self.resolution = self.store.resolution
        self.autoplay = self.store.autoplay
        self.player = Player()
        self.player.bind(on_end=self._on_video_end, state=self._on_player_state)
        self.current = None
        self.stream = None
        self.results, self.channel_list = [], []
        self.home, self.suggestions = [], []
        self._home_loading = False
        self._suggest_from_queue = False
        self._suggest_trigger = Clock.create_trigger(lambda _dt: self.refresh_suggestions())
        self.channels_view = []
        self._load_token = 0
        self._audio_busy = set()
        Window.clearcolor = self.c_bg
        Window.softinput_mode = "below_target"
        Window.bind(on_resize=self._update_immersive, on_keyboard=self._on_key)
        return Root()

    def on_start(self):
        shutil.rmtree(os.path.join(self.user_data_dir, "tmp"), ignore_errors=True)
        request_permissions()
        for value in RESOLUTIONS:
            chip = Factory.ResChip()
            chip.value = value
            self.root.ids.res_chips.add_widget(chip)
        self._update_immersive()
        plain_nav_bar()
        self._schedule_insets()
        self.refresh_favorites()
        self.refresh_history()
        self.load_home()
        self._resume_last()
        Clock.schedule_interval(self._save_resume_if_playing, 10)

    def on_pause(self):
        self._save_resume()
        # Arrière-plan / écran éteint : résolution minimale, et le service prend le relais en audio seul
        if self.has_video and self.player.playing:
            self.player.set_max_height(RESOLUTIONS[0])
            self.player.pause()
            self.bg.take_over(self.current, self.stream, self.player.position, self.queue, self.queue_index,
                              self.autoplay)
        keep_screen_on(False)
        return True

    def on_resume(self):
        self.player.set_max_height(self.resolution)
        state = self.bg.hand_back()
        if not state or not state.get("video"):
            return
        self.queue, self.queue_index = state.get("queue") or self.queue, state.get("queue_index", self.queue_index)
        if state.get("finished"):
            return
        if self.current and state["video"]["id"] == self.current["id"]:
            self.player.seek(state.get("position", 0))
            self.player.play()
            keep_screen_on(True)
        else:
            # Le service est passé à la vidéo suivante de la playlist pendant l'arrière-plan
            self.play_video(state["video"], self.queue, self.queue_index, start=state.get("position", 0))

    def on_stop(self):
        self.bg.stop()

    # ------------------------------------------------------------------ reprise au lancement
    def _resume_last(self):
        """Réaffiche la dernière vidéo lue et reprend la lecture là où elle en était."""
        state = self.bg.recover()
        last = self.store.data["resume"]
        if state and state.get("video"):
            video, position = state["video"], state.get("position", 0)
            queue, index = state.get("queue") or [], state.get("queue_index", -1)
        elif last:
            video, position = last["video"], last["position"]
            queue, index = last["queue"], last["queue_index"]
        else:
            return
        if video.get("duration") and position > video["duration"] - 5:
            position = 0  # vidéo terminée : on la reprend du début
        self.play_video(video, queue, index, start=position)

    def _save_resume(self):
        if self.current and self.stream is not None:
            self.store.set_resume(self.current, self.player.position, self.queue, self.queue_index)

    def _save_resume_if_playing(self, _dt):
        if self.player.playing:
            self._save_resume()

    def _on_key(self, window, key, *args):
        if key != 27:  # bouton retour Android / Échap
            return False
        if self.forced_landscape:
            self.toggle_fullscreen()
        elif self.panel == "channel":
            self.show_panel("favorites")
        elif self.panel != "results":
            self.show_panel("results")
        elif self.results_mode == "search":
            self.show_suggestions()
        else:
            move_to_back()  # on garde l'appli vivante (lecture en arrière-plan)
        return True

    def _update_immersive(self, _window=None, width=None, height=None):
        # Pendant on_resize, Window.size n'est pas encore à jour : on prend la taille transmise
        width, height = (width, height) if width else Window.size
        self.immersive = self.has_video and width > height
        if _window is not None:
            self._schedule_insets()  # rotation : les barres système changent de côté

    def _schedule_insets(self):
        # Les barres ne sont mesurables qu'une fois la fenêtre affichée : on relit un peu après
        for delay in (0.3, 1.5):
            Clock.schedule_once(lambda _dt: read_insets(self._set_insets), delay)

    def _set_insets(self, values):
        self.insets = values

    # ------------------------------------------------------------------ navigation
    def on_panel(self, _app, name):
        if self.root:
            self.root.ids.sm.current = name

    def show_panel(self, name):
        self.panel = "results" if self.panel == name and name != "results" else name
        if self.panel == "history":
            self.refresh_history()
        elif self.panel == "favorites":
            self.refresh_favorites()

    def toast(self, text, duration=2.5):
        self.toast_text = text
        label = self.root.ids.toast
        Animation.cancel_all(label)
        label.opacity = 1
        (Animation(d=duration) + Animation(opacity=0, d=0.4)).start(label)

    # ------------------------------------------------------------------ recherche
    def search(self, query):
        query = query.strip()
        if not query:
            return
        self.panel = "results"
        self.results_mode = "search"
        self.busy = True
        self.message = "Recherche…"
        self.root.ids.results_rv.data = []
        threading.Thread(target=self._search_worker, args=(query,), daemon=True).start()

    def _search_worker(self, query):
        try:
            self._show_results(yt.search(query), None)
        except Exception as exc:
            self._show_results([], exc)

    @mainthread
    def _show_results(self, videos, error):
        self.busy = False
        self.results = [v for v in videos if not v.get("live")] or videos
        if self.results_mode != "search":
            return  # retour aux suggestions pendant la recherche
        if error is not None:
            self.message = "Recherche impossible. Vérifie la connexion."
        elif not self.results:
            self.message = "Aucun résultat"
        else:
            self.message = ""
        self._set_rows([self._row(v, "result") for v in self.results])

    def _set_rows(self, rows):
        """Remplit la liste principale ; si son début change (autre liste), on remonte en haut."""
        rv = self.root.ids.results_rv
        first = rv.data[0]["video_id"] if rv.data else None
        rv.data = rows
        if rows and rows[0]["video_id"] != first:
            rv.scroll_y = 1

    def _row(self, video, mode, subtitle=None):
        if subtitle is None:
            parts = [clean_text(video.get("channel"))]
            if video.get("duration"):
                parts.append(fmt_time(video["duration"]))
            subtitle = "  ·  ".join(p for p in parts if p)
        return {
            "video_id": video["id"],
            "title": clean_text(video.get("title")),
            "subtitle": subtitle,
            "thumb": yt.thumbnail(video["id"]),
            "mode": mode,
            "subscribed": self.store.is_subscribed(video.get("channel_id") or ""),
            "current": bool(self.current and self.current["id"] == video["id"]),
        }

    def open_row(self, mode, index):
        if mode == "result":
            self.play_video(self.results[index])
        elif mode == "channel":
            self.play_video(self.channel_list[index], self.channel_list, index)
        elif mode == "history":
            self.play_video(self.store.data["history"][index])
        elif mode == "suggest":
            if self._suggest_from_queue:
                idx = self.queue_index + 1 + index
                self.play_video(self.queue[idx], self.queue, idx)
            else:
                self.play_video(self.home[index], self.home, index)
        elif mode == "fav":
            self.play_video(self.store.data["favorites"][index], list(self.store.data["favorites"]), index)

    # ------------------------------------------------------------------ lecture
    def play_video(self, video, queue=None, queue_index=-1, start=0.0):
        self._load_token += 1
        token = self._load_token
        self.current = dict(video)
        self.queue, self.queue_index = (list(queue), queue_index) if queue else ([], -1)
        self.stream = None
        self.has_video = True
        self._update_immersive()
        self.player.stop()
        self.video_title = clean_text(video.get("title"))
        self.video_channel = clean_text(video.get("channel"))
        self._refresh_current_flags()
        self.video_busy = True
        threading.Thread(target=self._resolve_worker, args=(token, video["id"], start), daemon=True).start()

    def _resolve_worker(self, token, video_id, start):
        try:
            stream, error = yt.resolve(video_id), None
        except Exception as exc:
            stream, error = None, exc
        self._on_resolved(token, stream, error, start)

    @mainthread
    def _on_resolved(self, token, stream, error, start):
        if token != self._load_token:
            return  # une autre vidéo a été choisie entre-temps
        self.video_busy = False
        if error is not None:
            self.toast("Lecture impossible : %s" % str(error)[:80], 4)
            return
        self.stream = stream
        # Les métadonnées complètes (chaîne notamment) viennent de la page de la vidéo
        for key, value in stream.video.items():
            if value and key != "live":
                self.current[key] = value
        self.video_title = clean_text(self.current["title"])
        self.video_channel = clean_text(self.current.get("channel"))
        self._refresh_current_flags()
        self.player.load(stream, start=start, max_height=self.resolution)
        self.store.add_history(self.current)
        self.bg.ensure_started()
        keep_screen_on(True)
        self._refresh_lists()
        self._fetch_related(token)

    def _on_player_state(self, player, state):
        if state == "error":
            self.toast("Erreur de lecture : %s" % player.error[:80], 4)
        keep_screen_on(state in ("loading", "ready") and player.playing)

    def _on_video_end(self, *args):
        nxt = self.queue_index + 1
        if 0 < nxt < len(self.queue) and (self.autoplay or not self.queue[nxt].get("suggested")):
            self.play_video(self.queue[nxt], self.queue, nxt)
        else:
            keep_screen_on(False)

    # ------------------------------------------------------------------ lecture automatique
    def _fetch_related(self, token):
        """Dernière vidéo de la file : on y ajoute les vidéos proposées par YouTube, comme son autoplay."""
        if not self.autoplay or self.queue_index < len(self.queue) - 1:
            return
        threading.Thread(target=self._related_worker, args=(token, self.current["id"]), daemon=True).start()

    def _related_worker(self, token, video_id):
        try:
            videos = yt.related(video_id)
        except Exception:
            traceback.print_exc()
            return
        self._add_related(token, videos)

    @mainthread
    def _add_related(self, token, videos):
        if token != self._load_token or not self.current:
            return
        queue, index = (list(self.queue), self.queue_index) if self.queue else ([self.current], 0)
        # On évite de reproposer les vidéos déjà dans la file ou regardées récemment
        seen = {v["id"] for v in queue} | {v["id"] for v in self.store.data["history"][:RECENT_HISTORY]}
        fresh = [dict(v, suggested=True) for v in videos if v["id"] not in seen]
        if not fresh:
            return
        self.queue, self.queue_index = queue + fresh, index
        if self.player.state == "ended":  # la vidéo s'est terminée avant l'arrivée des suggestions
            self._on_video_end()

    # ------------------------------------------------------------------ suggestions
    def load_home(self):
        self._home_loading = True
        history = self.store.data["history"]
        seeds = [v["id"] for v in history[:3]]
        exclude = {v["id"] for v in history[:RECENT_HISTORY]}
        channels = [c["id"] for c in self.store.data["channels"]]
        self.refresh_suggestions()
        threading.Thread(target=self._home_worker, args=(seeds, channels, exclude), daemon=True).start()

    def _home_worker(self, seeds, channels, exclude):
        try:
            videos = yt.home(seeds, channels, exclude)
        except Exception:
            traceback.print_exc()
            videos = []
        self._show_home(videos)

    @mainthread
    def _show_home(self, videos):
        self._home_loading = False
        # Marquées comme suggestions : sans lecture automatique, on ne les enchaîne pas
        self.home = [dict(v, suggested=True) for v in videos]
        self.refresh_suggestions()

    def on_queue(self, *_args):
        self._suggest_trigger()

    def on_queue_index(self, *_args):
        self._suggest_trigger()

    def refresh_suggestions(self):
        """Liste affichée hors recherche : la suite de la file pendant la lecture, sinon l'accueil."""
        upcoming = self.queue[self.queue_index + 1:] if self.current and self.queue_index >= 0 else []
        if not self.autoplay:
            # Les suggestions sont toujours en fin de file : l'index dans la file reste valable
            upcoming = [v for v in upcoming if not v.get("suggested")]
        self._suggest_from_queue = bool(upcoming)
        self.suggestions = upcoming or self.home
        self.suggest_title = "À suivre" if upcoming else "Suggestions"
        if self.results_mode != "suggest" or not self.root:
            return
        if self.suggestions:
            self.message = ""
        elif self._home_loading:
            self.message = "Chargement des suggestions…"
        else:
            self.message = "Recherche une vidéo pour commencer"
        self._set_rows([self._row(v, "suggest") for v in self.suggestions])

    def show_suggestions(self):
        self.results_mode = "suggest"
        self.panel = "results"
        self.refresh_suggestions()

    def play_next(self, step=1):
        idx = self.queue_index + step
        if self.queue and 0 <= idx < len(self.queue):
            self.play_video(self.queue[idx], self.queue, idx)

    def close_video(self, keep_resume=False):
        if not keep_resume:
            self.store.clear_resume()  # vidéo fermée à la main : rien à reprendre au prochain lancement
        self._load_token += 1
        self.player.stop()
        self.bg.stop()
        self.current, self.stream = None, None
        self.queue, self.queue_index = [], -1
        self.has_video = False
        self.video_busy = False
        if self.forced_landscape:
            self.toggle_fullscreen()
        self._update_immersive()
        keep_screen_on(False)
        self._suggest_trigger()

    def quit_app(self):
        """Arrête tout : vidéo, service d'arrière-plan (sinon le son continuerait) et appli."""
        self._save_resume()
        self.close_video(keep_resume=True)
        exit_app()

    def toggle_fullscreen(self):
        self.forced_landscape = not self.forced_landscape
        force_landscape(self.forced_landscape)

    def _refresh_current_flags(self):
        cur = self.current
        if not cur:
            return
        self.is_favorite = self.store.is_favorite(cur["id"])
        self.subscribed = self.store.is_subscribed(cur.get("channel_id") or "")
        if cur["id"] in self._audio_busy:
            self.audio_state = "busy"
        elif self.store.is_audio_done(cur["id"]):
            self.audio_state = "done"
        else:
            self.audio_state = "idle"

    # ------------------------------------------------------------------ favoris / chaînes
    def toggle_favorite(self):
        if not self.current:
            return
        self.is_favorite = self.store.toggle_favorite(self.current)
        self.toast("Ajoutée aux favoris" if self.is_favorite else "Retirée des favoris", 1.5)
        self.refresh_favorites()

    def toggle_subscription(self, channel_id=None, name=None):
        if channel_id is None:
            if not self.current or not self.current.get("channel_id"):
                self.toast("Chaîne inconnue pour cette vidéo")
                return
            channel_id, name = self.current["channel_id"], self.current.get("channel") or ""
        now = self.store.toggle_subscription(channel_id, name)
        self.toast(("Abonné à %s" if now else "Désabonné de %s") % name, 1.5)
        self._refresh_current_flags()
        self.refresh_favorites()

    def toggle_fav_row_subscription(self, index):
        video = self.store.data["favorites"][index]
        if video.get("channel_id"):
            self.toggle_subscription(video["channel_id"], video.get("channel") or "")

    def remove_favorite(self, index):
        video = self.store.data["favorites"][index]
        self.store.toggle_favorite(video)
        self._refresh_current_flags()
        self.refresh_favorites()

    def play_all_favorites(self):
        favs = list(self.store.data["favorites"])
        if favs:
            self.play_video(favs[0], favs, 0)
        else:
            self.toast("Aucun favori pour l'instant")

    def refresh_favorites(self):
        if not self.root:
            return
        favs = self.store.data["favorites"]
        self.root.ids.fav_rv.data = [self._row(v, "fav") for v in favs]
        self.channels_view = list(self.store.data["channels"])
        self.root.ids.chan_rv.data = [{"name": clean_text(c["name"]) or c["id"]} for c in self.channels_view]

    def unsubscribe_channel(self, index):
        chan = self.channels_view[index]
        self.toggle_subscription(chan["id"], chan["name"])

    def open_channel(self, index):
        chan = self.channels_view[index]
        self.channel_title = clean_text(chan["name"])
        self.panel = "channel"
        self.channel_list = []
        self.root.ids.channel_rv.data = []
        self.busy = True
        threading.Thread(target=self._channel_worker, args=(chan,), daemon=True).start()

    def _channel_worker(self, chan):
        try:
            videos, error = yt.channel_videos(chan["id"], chan["name"]), None
        except Exception as exc:
            videos, error = [], exc
        self._show_channel(videos, error)

    @mainthread
    def _show_channel(self, videos, error):
        self.busy = False
        if error is not None:
            self.toast("Impossible de charger la chaîne")
        self.channel_list = videos
        self.root.ids.channel_rv.data = [self._row(v, "channel") for v in videos]

    # ------------------------------------------------------------------ historique
    def refresh_history(self):
        if not self.root:
            return
        rows = []
        for v in self.store.data["history"]:
            subtitle = "  ·  ".join(p for p in (clean_text(v.get("channel")), fmt_ago(v.get("watched_at"))) if p)
            rows.append(self._row(v, "history", subtitle))
        self.root.ids.history_rv.data = rows

    def clear_history(self):
        self.store.clear_history()
        self.refresh_history()
        self.toast("Historique effacé", 1.5)

    def _refresh_lists(self):
        self.refresh_history()
        self.refresh_favorites()
        if self.results_mode == "search":
            self.root.ids.results_rv.data = [self._row(v, "result") for v in self.results]
        else:
            self.refresh_suggestions()

    # ------------------------------------------------------------------ paramètres
    def toggle_autoplay(self):
        self.autoplay = not self.autoplay
        self.store.autoplay = self.autoplay
        self._suggest_trigger()
        if self.autoplay and self.current:
            self._fetch_related(self._load_token)

    def set_resolution(self, value):
        self.resolution = int(value)
        self.store.resolution = self.resolution
        self.player.set_max_height(self.resolution)

    # ------------------------------------------------------------------ téléchargements
    def _tmp_dir(self):
        folder = os.path.join(self.user_data_dir, "tmp")
        os.makedirs(folder, exist_ok=True)
        return folder

    def download_audio(self):
        if not self.current:
            return
        if self.stream is None or self.stream.audio is None:
            self.toast("Audio indisponible pour cette vidéo")
            return
        video = dict(self.current)
        if video["id"] in self._audio_busy:
            return
        self._audio_busy.add(video["id"])
        self.audio_state = "busy"
        threading.Thread(target=self._audio_worker, args=(video, self.stream.audio), daemon=True).start()

    def _audio_worker(self, video, audio_fmt):
        tmp = os.path.join(self._tmp_dir(), video["id"] + ".m4a.part")
        try:
            yt.download(audio_fmt, tmp)
            dest = media.save_audio(tmp, video["title"])
            self._audio_finished(video, dest, None)
        except Exception as exc:
            traceback.print_exc()
            self._audio_finished(video, None, exc)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    @mainthread
    def _audio_finished(self, video, dest, error):
        self._audio_busy.discard(video["id"])
        if error is None:
            self.store.mark_audio_done(video["id"])
            self.toast("Musique enregistrée : %s" % dest, 3)
        else:
            self.toast("Échec du téléchargement audio : %s" % str(error)[:80], 4)
        self._refresh_current_flags()

    def download_video(self):
        if not self.current or self.stream is None:
            self.toast("Lance d'abord une vidéo")
            return
        fmt = self.stream.direct_video(self.resolution)
        if fmt is None or self.stream.audio is None:
            self.toast("Téléchargement indisponible pour cette vidéo")
            return
        self.toast("Téléchargement de la vidéo en %dp…" % fmt["height"], 2)
        threading.Thread(target=self._video_worker, args=(dict(self.current), fmt, self.stream.audio),
                         daemon=True).start()

    def _video_worker(self, video, vfmt, afmt):
        vtmp = os.path.join(self._tmp_dir(), video["id"] + ".mp4.part")
        atmp = os.path.join(self._tmp_dir(), video["id"] + ".v.m4a.part")
        last = [0]

        def progress(ratio):
            pct = int(ratio * 100)
            if pct >= last[0] + 10:
                last[0] = pct
                self._progress_toast("Téléchargement vidéo : %d %%" % pct)

        try:
            yt.download(vfmt, vtmp, progress)
            yt.download(afmt, atmp)
            dest = media.save_video(vtmp, atmp, video["title"])
            self._progress_toast("Vidéo enregistrée : %s" % dest, 3)
        except Exception as exc:
            traceback.print_exc()
            self._progress_toast("Échec du téléchargement vidéo : %s" % str(exc)[:80], 4)
        finally:
            for path in (vtmp, atmp):
                if os.path.exists(path):
                    os.remove(path)

    @mainthread
    def _progress_toast(self, text, duration=2.5):
        self.toast(text, duration)


if __name__ == "__main__":
    YouneguiApp().run()
