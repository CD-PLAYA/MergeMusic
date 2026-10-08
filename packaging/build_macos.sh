#!/bin/bash
# Builds dist/MergeMusic.app and a zip of it, for the Mac this runs on (Intel or Apple Silicon).
#   bash packaging/build_macos.sh
# Needs Python 3.10+ (python.org or Homebrew). The app is not signed with a Developer ID;
# see the README for opening it the first time.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
ARCH="$(uname -m)"
MIN_MACOS="13.0"
VERSION="$("$PY" -c 'import re;print(re.search(r"__version__ = \"(.+)\"", open("mergemusic/__init__.py").read()).group(1))')"
PYVER="$("$PY" -c 'import sys;print("%d%d" % sys.version_info[:2])')"

echo "== MergeMusic $VERSION for $ARCH (macOS $MIN_MACOS and later)"
rm -rf build dist
"$PY" -m venv build/venv
build/venv/bin/pip install -q --upgrade pip

# pip picks wheels for the Mac doing the build. NumPy ships a separate wheel for macOS 14+,
# so fetch the one that also runs on older systems before anything else pulls in numpy.
if [ "$ARCH" = "arm64" ]; then NP_PLAT=macosx_11_0_arm64; else NP_PLAT=macosx_10_13_x86_64; fi
build/venv/bin/pip download -q --only-binary=:all: --no-deps --platform "$NP_PLAT" \
    --python-version "$PYVER" -d build/wheels numpy
build/venv/bin/pip install -q build/wheels/numpy-*.whl
build/venv/bin/pip install -q ".[tags]" pyinstaller pillow

echo "== Fetching fpcalc (Chromaprint) to bundle"
build/venv/bin/python - <<'PY'
import os, shutil
from mergemusic.core import fingerprint
fingerprint.user_data_dir = lambda: os.path.abspath('build/fpcalc-dl')
path = fingerprint.download_fpcalc()
shutil.copy(path, 'build/fpcalc')
os.chmod('build/fpcalc', 0o755)
PY
build/fpcalc -version

echo "== Building the app"
build/venv/bin/pyinstaller --noconfirm --clean --distpath dist --workpath build/pyi packaging/MergeMusic.spec

echo "== Checking every binary runs on macOS $MIN_MACOS"
build/venv/bin/python packaging/check_min_macos.py dist/MergeMusic.app "$MIN_MACOS"

echo "== Smoke test of the built app"
APP=dist/MergeMusic.app/Contents/MacOS/MergeMusic
"$APP" --version
"$APP" fpcalc
BD="$(mktemp -d)"
printf 'directory: %s\nlibrary: %s/lib.db\nplugins: [musicbrainz, chroma, fetchart, embedart, inline]\n' "$BD" "$BD" > "$BD/config.yaml"
BEETSDIR="$BD" "$APP" beets -c "$BD/config.yaml" version | tee "$BD/out.txt"
grep -q 'plugins: chroma, embedart, fetchart, inline, musicbrainz' "$BD/out.txt"
QT_QPA_PLATFORM=offscreen "$APP" gui --selftest
# the real window system; reported, not fatal, since build machines may have no screen session
perl -e 'alarm 90; exec @ARGV' "$APP" gui --selftest || echo "warning: on-screen self-test did not finish"

ZIP="dist/MergeMusic-$VERSION-macOS-$ARCH.zip"
ditto -c -k --keepParent dist/MergeMusic.app "$ZIP"
echo "== Built $ZIP"
