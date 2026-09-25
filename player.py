"""Lecteur vidéo affiché dans une texture Kivy (pour pouvoir dessiner le cœur et la note par-dessus).

- Android : ExoPlayer (Media3) lit le manifeste HLS maître ; la résolution est imposée par les
  paramètres de sélection de piste. L'image est décodée dans une SurfaceTexture liée à une texture
  OpenGL "externe", recopiée à chaque image dans un Fbo Kivy.
  ExoPlayer doit être manipulé depuis le thread UI Android : chaque appel passe par run_on_ui_thread,
  et l'état est relu périodiquement (poll) puis recopié dans les propriétés Kivy.
- PC (développement) : lecteur Kivy/ffpyplayer sur le fichier vidéo direct, sans le son.
"""
from kivy.clock import Clock
from kivy.event import EventDispatcher
from kivy.properties import BooleanProperty, NumericProperty, ObjectProperty, StringProperty
from kivy.utils import platform


class BasePlayer(EventDispatcher):
    texture = ObjectProperty(None, allownone=True)
    position = NumericProperty(0)   # secondes
    duration = NumericProperty(0)   # secondes
    playing = BooleanProperty(False)
    state = StringProperty("idle")  # idle, loading, ready, ended, error
    error = StringProperty("")

    __events__ = ("on_end",)

    def on_end(self):
        pass


if platform == "android":
    from android.runnable import run_on_ui_thread
    from jnius import autoclass
    from kivy.graphics import Callback, Fbo, Rectangle
    from kivy.graphics.texture import Texture

    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    ExoPlayerBuilder = autoclass("androidx.media3.exoplayer.ExoPlayer$Builder")
    MediaItem = autoclass("androidx.media3.common.MediaItem")
    HttpFactory = autoclass("androidx.media3.datasource.DefaultHttpDataSource$Factory")
    HlsFactory = autoclass("androidx.media3.exoplayer.hls.HlsMediaSource$Factory")
    # HlsMediaSource.Factory a deux constructeurs : pyjnius prend celui qui attend un HlsDataSourceFactory
    HlsDataSourceFactory = autoclass("androidx.media3.exoplayer.hls.DefaultHlsDataSourceFactory")
    SurfaceTexture = autoclass("android.graphics.SurfaceTexture")
    Surface = autoclass("android.view.Surface")
    HashMap = autoclass("java.util.HashMap")
    GL_TEXTURE_EXTERNAL_OES = autoclass("android.opengl.GLES11Ext").GL_TEXTURE_EXTERNAL_OES

    STATE_BUFFERING, STATE_READY, STATE_ENDED = 2, 3, 4

    FRAGMENT_SHADER = """
    #extension GL_OES_EGL_image_external : require
    #ifdef GL_ES
        precision highp float;
    #endif
    varying vec4 frag_color;
    varying vec2 tex_coord0;
    uniform sampler2D texture0;
    uniform samplerExternalOES texture1;
    void main()
    {
        gl_FragColor = texture2D(texture1, tex_coord0);
    }
    """

    class Player(BasePlayer):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._exo = None
            self._polled = {}
            self._ended_sent = False
            self._fbo = None
            self._fbo_size = (0, 0)
            # Texture OpenGL externe dans laquelle Android écrit les images décodées
            self._ext_tex = Texture(width=16, height=16, target=GL_TEXTURE_EXTERNAL_OES, colorfmt="rgba")
            # Kivy ne crée l'identifiant OpenGL qu'au premier bind : sans ça, .id vaut 0
            self._ext_tex.bind()
            self._surface_texture = SurfaceTexture(int(self._ext_tex.id))
            self._surface = Surface(self._surface_texture)
            self._create()
            Clock.schedule_interval(self._poll, 0.25)
            Clock.schedule_interval(self._draw, 0)

        @run_on_ui_thread
        def _create(self):
            self._exo = ExoPlayerBuilder(PythonActivity.mActivity).build()
            self._exo.setVideoSurface(self._surface)

        @run_on_ui_thread
        def load(self, stream, start=0.0, max_height=720, autoplay=True):
            self._ended_sent = False
            headers = HashMap()
            for key, value in (stream.headers or {}).items():
                if key.lower() != "user-agent":
                    headers.put(key, value)
            http = HttpFactory()
            http.setUserAgent(stream.headers.get("User-Agent", "Mozilla/5.0"))
            http.setDefaultRequestProperties(headers)
            http.setAllowCrossProtocolRedirects(True)
            source = HlsFactory(HlsDataSourceFactory(http)).createMediaSource(MediaItem.fromUri(stream.hls_url))
            self._exo.setMediaSource(source)
            self._apply_max_height(max_height)
            self._exo.prepare()
            if start > 0:
                self._exo.seekTo(int(start * 1000))
            self._exo.setPlayWhenReady(autoplay)

        def _apply_max_height(self, height):
            params = (self._exo.getTrackSelectionParameters().buildUpon()
                      .setMaxVideoSize(8192, int(height))
                      .setForceHighestSupportedBitrate(True)
                      .build())
            self._exo.setTrackSelectionParameters(params)

        @run_on_ui_thread
        def set_max_height(self, height):
            if self._exo:
                self._apply_max_height(height)

        @run_on_ui_thread
        def play(self):
            if self._exo:
                if self._exo.getPlaybackState() == STATE_ENDED:
                    self._exo.seekTo(0)
                self._exo.setPlayWhenReady(True)

        @run_on_ui_thread
        def pause(self):
            if self._exo:
                self._exo.setPlayWhenReady(False)

        def toggle(self):
            self.pause() if self.playing else self.play()

        @run_on_ui_thread
        def seek(self, seconds):
            if self._exo:
                self._exo.seekTo(int(seconds * 1000))

        @run_on_ui_thread
        def stop(self):
            if self._exo:
                self._exo.stop()
                self._exo.clearMediaItems()

        @run_on_ui_thread
        def _read_state(self):
            exo = self._exo
            if exo is None:
                return
            err = exo.getPlayerError()
            size = exo.getVideoSize()
            self._polled = {
                "position": max(exo.getCurrentPosition(), 0) / 1000.0,
                "duration": max(exo.getDuration(), 0) / 1000.0,
                "state": exo.getPlaybackState(),
                "playing": exo.isPlaying() or (exo.getPlayWhenReady() and exo.getPlaybackState() == STATE_BUFFERING),
                "size": (size.width, size.height),
                "error": err.getMessage() if err is not None else "",
            }

        def _poll(self, dt):
            self._read_state()
            p = self._polled
            if not p:
                return
            self.position = p["position"]
            self.duration = p["duration"]
            self.playing = p["playing"]
            if p["error"]:
                self.state, self.error = "error", p["error"]
            elif p["state"] == STATE_ENDED:
                self.state = "ended"
                if not self._ended_sent:
                    self._ended_sent = True
                    self.dispatch("on_end")
            elif p["state"] == STATE_READY:
                self.state = "ready"
            elif p["state"] == STATE_BUFFERING:
                self.state = "loading"
            if p["size"][0] > 0 and p["size"] != self._fbo_size:
                self._setup_fbo(*p["size"])

        def _setup_fbo(self, w, h):
            self._fbo_size = (w, h)
            self._fbo = Fbo(size=(w, h))
            self._fbo.shader.fs = FRAGMENT_SHADER
            with self._fbo:
                Callback(lambda instr: self._ext_tex.bind())
                Rectangle(size=(w, h))
            self.texture = self._fbo.texture

        def _draw(self, dt):
            if self._fbo is None:
                return
            self._surface_texture.updateTexImage()
            self._fbo.ask_update()
            self._fbo.draw()
            self.texture = self._fbo.texture
            self.property("texture").dispatch(self)

else:
    from kivy.core.video import Video as CoreVideo

    class Player(BasePlayer):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._video = None
            Clock.schedule_interval(self._poll, 0.25)

        def load(self, stream, start=0.0, max_height=720, autoplay=True):
            self.stop()
            fmt = stream.direct_video(min(max_height, 480))
            if fmt is None:
                self.state, self.error = "error", "Pas de flux vidéo direct"
                return
            self.state = "loading"
            self._video = CoreVideo(filename=fmt["url"])
            self._video.bind(on_frame=self._on_frame, on_eos=self._on_eos)
            self._start = start
            if autoplay:
                self._video.play()

        def _on_frame(self, *args):
            if self._video is None:
                return
            self.texture = self._video.texture
            self.property("texture").dispatch(self)
            if self.state == "loading":
                self.state = "ready"
                if self._start and self._video.duration:
                    self._video.seek(self._start / self._video.duration)
                    self._start = 0

        def _on_eos(self, *args):
            self.state = "ended"
            self.playing = False
            self.dispatch("on_end")

        def _poll(self, dt):
            if self._video:
                self.position = self._video.position or 0
                self.duration = self._video.duration or 0
                self.playing = self._video.state == "playing"

        def set_max_height(self, height):
            pass

        def play(self):
            if self._video:
                self._video.play()

        def pause(self):
            if self._video:
                self._video.pause()

        def toggle(self):
            self.pause() if self.playing else self.play()

        def seek(self, seconds):
            if self._video and self._video.duration:
                self._video.seek(min(seconds / self._video.duration, 1.0))

        def stop(self):
            if self._video:
                self._video.unbind(on_frame=self._on_frame, on_eos=self._on_eos)
                self._video.unload()
                self._video = None
            self.texture = None
            self.state = "idle"
