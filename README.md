# Younegui

Lecteur YouTube léger pour Android, écrit en Python avec [Kivy](https://kivy.org) et [yt-dlp](https://github.com/yt-dlp/yt-dlp), empaqueté avec [Buildozer](https://buildozer.readthedocs.io).

- Barre de recherche en haut, résultats en liste compacte
- Sur la vidéo : cœur (favoris, contour jaune → rose) en haut à gauche, note de musique (audio M4A : noir → orange → vert) en haut à droite
- En bas : historique, téléchargement de la vidéo, favoris (servant aussi de playlist, avec abonnements aux chaînes)
- Lecture en arrière-plan et écran éteint (audio seul), reprise de la vidéo au retour au premier plan
- Portrait et paysage (plein écran)
- Paramètres : résolution par défaut, infos de l'appli

Les fichiers téléchargés vont dans `Musique/Younegui` (audio) et `Films/Younegui` (vidéo).
Télécharger des vidéos YouTube est contraire aux conditions d'utilisation de YouTube : usage personnel uniquement, l'appli ne peut pas être publiée sur le Play Store.

## Lancer sur le PC (Windows)

Prérequis : Python 3.12.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Sur PC, la vidéo est lue sans le son (fichier vidéo direct) : c'est seulement pour tester l'interface.

## Construire l'APK Android

Buildozer ne fonctionne pas directement sous Windows : il faut WSL (Ubuntu).

```powershell
wsl --install -d Ubuntu   # une seule fois, puis redémarrer
```

Dans Ubuntu :

```bash
sudo apt update
sudo apt install -y git zip unzip rsync openjdk-17-jdk python3-pip python3-venv autoconf automake libtool pkg-config zlib1g-dev libncurses-dev cmake libffi-dev libssl-dev build-essential
python3 -m venv ~/buildozer-venv
~/buildozer-venv/bin/pip install buildozer cython setuptools
```

Puis, depuis PowerShell dans le dossier du projet :

```powershell
wsl -d Ubuntu -- bash build_apk.sh
```

`build_apk.sh` copie le projet dans `~/younegui` côté Linux (compiler dans `/mnt/c` est très lent), lance `buildozer android debug`, puis recopie l'APK dans `bin/`.
Le premier build télécharge le SDK et le NDK Android, ce qui prend longtemps.

Pour installer l'APK : copie `bin/younegui-*.apk` sur le téléphone et ouvre-le (autoriser l'installation d'applis de sources inconnues).

## Mettre à jour yt-dlp

YouTube change régulièrement son fonctionnement. Si la recherche ou la lecture cesse de marcher, reconstruire l'APK suffit en général : Buildozer récupère la dernière version de yt-dlp.
