#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/dist}"
VERSION="${HPP_VERSION:-0.1.0}"
PYTHON="${PYTHON:-python3}"
DEB_ONLY=0
if [[ "${2:-}" == "--deb-only" ]]; then DEB_ONLY=1; fi
mkdir -p "$OUT"
command -v "$PYTHON" >/dev/null || { echo "python3 is required" >&2; exit 2; }
if ! "$PYTHON" -m PyInstaller --version >/dev/null 2>&1; then
  echo "PyInstaller is required: install the optional packaging extra with '$PYTHON -m pip install -e \"$ROOT[packaging]\"'." >&2
  exit 2
fi
command -v dpkg-deb >/dev/null || { echo "dpkg-deb is required for the .deb" >&2; exit 2; }
if [[ "$DEB_ONLY" -eq 0 ]] && ! command -v appimagetool >/dev/null; then
  echo "appimagetool is required for the AppImage (use --deb-only for a Debian-only development build)." >&2
  exit 2
fi
BUILD="$OUT/.native-build"
rm -rf "$BUILD"
mkdir -p "$BUILD" "$BUILD/launchers"
cat > "$BUILD/launchers/cli.py" <<EOF
import sys
sys.path.insert(0, ${ROOT@Q}/src)
from hotpepperpodcast.cli import main
raise SystemExit(main())
EOF
cat > "$BUILD/launchers/web.py" <<EOF
import sys
sys.path.insert(0, ${ROOT@Q}/src)
from hotpepperpodcast.web_cli import main
raise SystemExit(main())
EOF
ADD_DATA="$ROOT/src/hotpepperpodcast/static:hotpepperpodcast/static"
"$PYTHON" -m PyInstaller --noconfirm --clean --onefile --name hotpepperpodcast --distpath "$BUILD" --workpath "$BUILD/work-cli" --specpath "$BUILD" --paths "$ROOT/src" --add-data "$ADD_DATA" "$BUILD/launchers/cli.py"
"$PYTHON" -m PyInstaller --noconfirm --clean --onefile --name hotpepperpodcast-web --distpath "$BUILD" --workpath "$BUILD/work-web" --specpath "$BUILD" --paths "$ROOT/src" --add-data "$ADD_DATA" "$BUILD/launchers/web.py"
for binary in hotpepperpodcast hotpepperpodcast-web; do test -x "$BUILD/$binary"; done

DEBROOT="$BUILD/hotpepperpodcast_${VERSION}_amd64"
rm -rf "$DEBROOT"
mkdir -p "$DEBROOT/DEBIAN" "$DEBROOT/opt/hotpepperpodcast" "$DEBROOT/usr/bin"
cat > "$DEBROOT/DEBIAN/control" <<EOF
Package: hotpepperpodcast
Version: $VERSION
Section: sound
Priority: optional
Architecture: amd64
Maintainer: MaximumHeat
Depends: ffmpeg
Recommends: espeak-ng
Description: Local authored-script podcast renderer
 HotPepperPodcast renders authored scripts into local podcast audio.
 Piper voice models and optional XTTS models remain in the user's data directory.
EOF
cat > "$DEBROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -eu
if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != root ]; then
  home_dir=$(getent passwd "$SUDO_USER" | cut -d: -f6 || true)
  if [ -n "$home_dir" ]; then
    install -d -m 0755 "$home_dir/.local/share/hotpepperpodcast/voices"
    chown "$SUDO_USER" "$home_dir/.local/share/hotpepperpodcast" "$home_dir/.local/share/hotpepperpodcast/voices" || true
  fi
fi
exit 0
EOF
chmod 0755 "$DEBROOT/DEBIAN/postinst"
cp "$BUILD/hotpepperpodcast" "$BUILD/hotpepperpodcast-web" "$DEBROOT/opt/hotpepperpodcast/"
ln -s /opt/hotpepperpodcast/hotpepperpodcast "$DEBROOT/usr/bin/hotpepperpodcast"
ln -s /opt/hotpepperpodcast/hotpepperpodcast-web "$DEBROOT/usr/bin/hotpepperpodcast-web"
cp "$ROOT/LICENSE" "$DEBROOT/opt/hotpepperpodcast/LICENSE"
dpkg-deb --build --root-owner-group "$DEBROOT" "$OUT/hotpepperpodcast_${VERSION}_amd64.deb" >/dev/null

if [[ "$DEB_ONLY" -eq 0 ]]; then
  APPROOT="$BUILD/HotPepperPodcast.AppDir"
  rm -rf "$APPROOT"
  mkdir -p "$APPROOT/usr/bin"
  cp "$BUILD/hotpepperpodcast" "$BUILD/hotpepperpodcast-web" "$APPROOT/usr/bin/"
  cat > "$APPROOT/AppRun" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "web" ]]; then shift; exec "${APPDIR}/usr/bin/hotpepperpodcast-web" "$@"; fi
exec "${APPDIR}/usr/bin/hotpepperpodcast" "$@"
EOF
  chmod 0755 "$APPROOT/AppRun"
  # Desktop integration is deliberately deferred until the AppImage has
  # been validated on clean hosts; AppRun remains the portable entry point.
  appimagetool "$APPROOT" "$OUT/HotPepperPodcast-x86_64.AppImage" >/dev/null
fi
rm -rf "$BUILD"
printf 'Built native artifacts in %s\n' "$OUT"
