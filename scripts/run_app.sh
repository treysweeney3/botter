#!/usr/bin/env bash
# Rebuild the Botter macOS app and relaunch it.
#
# Quitting and reopening the app from the Dock does NOT pick up source changes —
# it relaunches the binary already on disk. Run this instead after any edit.
#
#   scripts/run_app.sh            build + relaunch
#   scripts/run_app.sh --clean    wipe DerivedData first, then build + relaunch
#   scripts/run_app.sh --no-launch  build only, leave the app closed

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT/app"
SCHEME="Botter"
CONFIG="Debug"
BUNDLE_ID="com.treysweeney.botter"

LAUNCH=1
CLEAN=0
for arg in "$@"; do
  case "$arg" in
    --clean) CLEAN=1 ;;
    --no-launch) LAUNCH=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

cd "$APP_DIR"

# project.yml is the source of truth; regenerate if it moved ahead of the
# .xcodeproj (new/renamed/deleted source files need this or they won't compile).
if [[ project.yml -nt Botter.xcodeproj ]]; then
  echo "==> project.yml changed, regenerating Botter.xcodeproj"
  xcodegen generate --quiet
fi

if [[ $CLEAN -eq 1 ]]; then
  echo "==> clean build"
  xcodebuild -project Botter.xcodeproj -scheme "$SCHEME" -configuration "$CONFIG" clean >/dev/null
fi

echo "==> building $SCHEME ($CONFIG)"
# Stream errors/warnings, but keep the usual xcodebuild firehose out of the way.
set +e
BUILD_LOG="$(xcodebuild -project Botter.xcodeproj -scheme "$SCHEME" -configuration "$CONFIG" build 2>&1)"
BUILD_STATUS=$?
set -e

if [[ $BUILD_STATUS -ne 0 ]]; then
  echo "$BUILD_LOG" | grep -E "error:" | sort -u
  echo "==> BUILD FAILED (app not relaunched, still running the previous build)"
  exit 1
fi

APP_PATH="$(xcodebuild -project Botter.xcodeproj -scheme "$SCHEME" -configuration "$CONFIG" \
  -showBuildSettings 2>/dev/null \
  | awk -F' = ' '/ BUILT_PRODUCTS_DIR = /{d=$2} / FULL_PRODUCT_NAME = /{n=$2} END{print d "/" n}')"

echo "==> built $APP_PATH"

[[ $LAUNCH -eq 1 ]] || exit 0

# Quit the running copy so the relaunch actually loads the new binary.
if pgrep -x "$SCHEME" >/dev/null; then
  echo "==> quitting running instance"
  osascript -e "quit app id \"$BUNDLE_ID\"" 2>/dev/null || pkill -x "$SCHEME" || true
  for _ in $(seq 1 25); do
    pgrep -x "$SCHEME" >/dev/null || break
    sleep 0.2
  done
  pgrep -x "$SCHEME" >/dev/null && pkill -9 -x "$SCHEME" || true
fi

echo "==> launching"
open -a "$APP_PATH"
