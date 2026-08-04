#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$ROOT"

if [ "$(uname -m)" != "arm64" ]; then
    echo "This build targets macOS arm64. Run it on an Apple Silicon Python environment." >&2
    exit 1
fi

PYTHON=${PYTHON:-"$ROOT/.venv/bin/python"}
VERSION=$($PYTHON -c 'from dlp import __version__; print(__version__)')

rm -rf build/dlp dist/dlp dist/dlp-macos-arm64.pkg
"$PYTHON" -m PyInstaller \
    --clean \
    --noconfirm \
    --onedir \
    --console \
    --name dlp \
    --paths src \
    --add-data "src/dlp/ui/styles.tcss:dlp/ui" \
    --add-data "THIRD_PARTY_NOTICES.md:." \
    --collect-all textual \
    --collect-all yt_dlp \
    --collect-all yt_dlp_ejs \
    packaging/entrypoint.py

STAGE=$(mktemp -d "${TMPDIR:-/tmp}/dlp-pkg.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/usr/local/libexec/dlp" "$STAGE/usr/local/bin"
cp -R dist/dlp/. "$STAGE/usr/local/libexec/dlp/"
ln -s ../libexec/dlp/dlp "$STAGE/usr/local/bin/dlp"
pkgbuild \
    --root "$STAGE" \
    --identifier com.dlp.cli \
    --version "$VERSION" \
    --install-location / \
    "dist/dlp-macos-arm64.pkg"

echo "Created dist/dlp-macos-arm64.pkg"
