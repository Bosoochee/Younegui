[app]
title = Younegui
package.name = younegui
package.domain = org.younegui
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,json
source.exclude_dirs = .venv,bin,.buildozer,tests,tools,.git
version.regex = __version__ = ['"](.*)['"]
version.filename = %(source.dir)s/main.py

# Dépendances embarquées dans l'APK (séparées par des virgules, pas de requirements.txt ici).
# charset_normalizer (dépendance de requests, donc de Kivy) est figé sur une version en pur Python :
# les versions récentes publient des wheels Android que python-for-android n'arrive pas à installer.
# Il faut l'écrire avec un tiret bas, sinon python-for-android rajoute quand même la wheel Android.
requirements = python3,kivy==2.3.1,pyjnius,android,yt-dlp,certifi,charset_normalizer==3.3.2

# Portrait et paysage, selon la rotation du téléphone
orientation = portrait, landscape, portrait-reverse, landscape-reverse
fullscreen = 0

icon.filename = %(source.dir)s/assets/icon.png
presplash.filename = %(source.dir)s/assets/presplash.png
android.presplash_color = #0E0E13

# Service de premier plan qui continue la lecture audio en arrière-plan / écran éteint
services = Player:service/player_service.py:foreground:foregroundServiceType=mediaPlayback

android.permissions = INTERNET, WAKE_LOCK, FOREGROUND_SERVICE, FOREGROUND_SERVICE_MEDIA_PLAYBACK, POST_NOTIFICATIONS, (name=android.permission.WRITE_EXTERNAL_STORAGE;maxSdkVersion=28)

# Lecteur vidéo ExoPlayer (Media3), piloté depuis Python avec pyjnius
android.enable_androidx = True
android.gradle_dependencies = androidx.media3:media3-exoplayer:1.4.1, androidx.media3:media3-exoplayer-hls:1.4.1

android.api = 35
android.minapi = 24
# arm64 uniquement : construire plusieurs architectures fait échouer python-for-android
android.archs = arm64-v8a
android.accept_sdk_license = True
android.allow_backup = False

[buildozer]
log_level = 2
warn_on_root = 1
