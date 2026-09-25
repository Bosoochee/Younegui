# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Younegui: a lightweight YouTube client for Android written in Python (Kivy UI + yt-dlp), packaged as an APK with Buildozer. The user writes in French; UI strings, comments and replies are in French.

## Commands

- Desktop run (Windows, venv in `.venv`): `.venv\Scripts\python.exe main.py`
- Install desktop deps: `.venv\Scripts\python.exe -m pip install -r requirements.txt`
- Regenerate icon/presplash: `.venv\Scripts\python.exe tools\make_icons.py` (needs Pillow)
- Build debug APK (from PowerShell in the project dir): `wsl -d Ubuntu -- bash build_apk.sh` → APK copied to `bin/`
  - Buildozer only runs in WSL (Ubuntu). The script rsyncs the project to `~/younegui` inside WSL and builds there (building on `/mnt/c` is very slow). Buildozer lives in a venv at `~/buildozer-venv`.
  - Edit files only in the Windows project dir; `~/younegui` is a throwaway copy overwritten on each build (its `.buildozer/` cache is kept).
  - The script deletes p4a's `build/venv` before each build: p4a re-creates it over the old one, which breaks pip (`BuildDependencyInstallError`).
- Phone debugging over USB: Windows adb is at `%LOCALAPPDATA%\Android\platform-tools\adb.exe` (WSL's adb can't see USB). Install with `adb install -r bin\younegui-0.2.0-arm64-v8a-debug.apk`, read logs with `adb logcat -s python:*`, screenshots with `adb shell screencap`. The app ignores `adb shell input` taps/clicks (SDL drops the virtual device), so the user must do the taps. `SIGUSR1`/faulthandler doesn't work on Android (ART catches it). Worker exceptions are only shown as toasts unless `traceback.print_exc()` is called.
- No test suite. UI changes are checked by scripting the app on desktop (schedule actions with `Clock`, take `Window.screenshot`).

## Architecture

- `main.py`: `YouneguiApp` holds all UI state as Kivy properties (current video, queue/playlist, panel, favorite/subscription/audio-download flags). `build()` creates the `Player` before `Root()` because kv rules bind to `app.player`. yt-dlp calls run in threads and come back through `@mainthread`.
- `younegui.kv`: class rules only (no root rule); `Root` is instantiated in `build()`. Icons are glyphs of the Material Design Icons font `assets/mdi.ttf`, looked up via `app.glyph(name)` (codes in `ICONS` in main.py). Don't name an App method `icon` — it shadows `App.icon`.
- `yt.py`: yt-dlp wrapper. Without a JS runtime (none on Android), YouTube only yields an HLS master manifest (used for playback) plus direct AAC audio (itag 140) and H.264 video-only files (used for downloads). `Stream` picks these.
- `player.py`: two implementations behind one interface. Android: ExoPlayer (Media3, added via `android.gradle_dependencies`) plays the HLS master; resolution is set with track-selection params; frames go to a `SurfaceTexture` on a GL_TEXTURE_EXTERNAL_OES texture, copied each frame into a Kivy `Fbo` so widgets can be drawn over the video. ExoPlayer must be touched only on the Android UI thread (`run_on_ui_thread`); its state is polled into Kivy properties. Desktop: Kivy/ffpyplayer on the direct video file (no sound).
- Background playback: Android freezes the app process when it isn't visible, so `background.py` hands playback to the p4a foreground service `service/player_service.py` (separate process, `foregroundServiceType=mediaPlayback`), which plays audio only with `MediaPlayer` and walks the queue. The service is started as soon as a video plays (it can't be started from the background) and idles until `on_pause`. The two processes talk through JSON files in `user_data_dir` (`bg_cmd.json` / `bg_state.json`). `background.py` must not import Kivy at module level since the service imports it.
- `media.py`: on Android, remuxes downloaded DASH fragments with `MediaExtractor`/`MediaMuxer` (also merges video+audio) straight into MediaStore (`Music/Younegui`, `Movies/Younegui`); API < 29 writes to the public dir directly.
- `store.py`: JSON persistence (favorites = playlist, subscribed channels, history, downloaded-audio ids, settings).

## Buildozer gotchas

- Android deps are declared in `buildozer.spec` `requirements`, not `requirements.txt`. `charset-normalizer` is pinned to 3.3.2 because newer versions ship Android wheels that p4a's host pip refuses.
- Only `arm64-v8a` is built.
- New asset file types must be added to `source.include_exts`, otherwise they are silently left out of the APK.
