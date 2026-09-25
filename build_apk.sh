#!/usr/bin/env bash
# Construit l'APK debug depuis WSL : copie le projet dans le système de fichiers Linux
# (bien plus rapide que /mnt/c), lance Buildozer, puis recopie l'APK dans bin/ côté Windows.
# Architecture : arm64-v8a par défaut (téléphones) ; `bash build_apk.sh x86_64` pour l'émulateur.
set -euo pipefail

if [ -n "${1:-}" ]; then
    export APP_ANDROID_ARCHS="$1"  # Buildozer lit les variables APP_* à la place du .spec
fi

SRC="$(cd "$(dirname "$0")" && pwd)"
WORK="$HOME/younegui"

mkdir -p "$WORK"
rsync -a --delete --exclude .venv --exclude .buildozer --exclude bin --exclude .git "$SRC/" "$WORK/"

# Le téléchargement de Gradle par gradlew peut se figer sans jamais échouer : on le fait nous-mêmes,
# avec reprise et délai d'expiration, dans le dossier où gradlew le cherche.
GRADLE_ZIP=gradle-8.14.3-all.zip
for dir in "$HOME"/.gradle/wrapper/dists/gradle-8.14.3-all/*/; do
    if [ -d "$dir" ] && [ ! -d "$dir/gradle-8.14.3" ] && [ ! -f "$dir/$GRADLE_ZIP" ]; then
        rm -f "$dir"/*.part "$dir"/*.lck
        curl -fL --retry 5 --retry-all-errors --connect-timeout 20 --speed-time 60 --speed-limit 1000 \
            -C - -o "$dir/$GRADLE_ZIP.dl" "https://services.gradle.org/distributions/$GRADLE_ZIP"
        mv "$dir/$GRADLE_ZIP.dl" "$dir/$GRADLE_ZIP"
    fi
done

# Activer le venv met aussi cython sur le PATH, dont Buildozer a besoin
source "$HOME/buildozer-venv/bin/activate"

cd "$WORK"
# python-for-android recrée ce venv par-dessus celui du build précédent, ce qui mélange deux versions
# de pip et casse l'installation des modules Python : on repart d'un venv neuf à chaque build.
rm -rf .buildozer/android/platform/build-*/build/venv
buildozer android debug

if ! ls bin/*.apk >/dev/null 2>&1; then
    echo "ERREUR : aucun APK produit" >&2
    exit 1
fi
mkdir -p "$SRC/bin"
cp bin/*.apk "$SRC/bin/"
ls -l "$SRC/bin/"
