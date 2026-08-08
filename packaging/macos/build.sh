#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$ROOT"

if [ "$(uname -m)" != "arm64" ]; then
    echo "This build targets macOS arm64. Run it on an Apple Silicon Python environment." >&2
    exit 1
fi

if [ "${PYTHON:-}" ]; then
    :
elif [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON="$ROOT/.venv/bin/python"
else
    PYTHON=$(command -v python3)
fi
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
VERSION=$($PYTHON -c 'from dlp import __version__; print(__version__)')

rm -rf build/dlp build/licenses dist/dlp dist/dlp-macos-arm64.pkg
$PYTHON packaging/collect_licenses.py build/licenses
"$PYTHON" -m PyInstaller \
    --clean \
    --noconfirm \
    --onedir \
    --console \
    --name dlp \
    --paths src \
    --add-data "src/dlp/ui/styles.tcss:dlp/ui" \
    --add-data "THIRD_PARTY_NOTICES.md:." \
    --add-data "build/licenses:licenses" \
    --collect-all textual \
    --collect-all yt_dlp \
    --collect-all yt_dlp_ejs \
    packaging/entrypoint.py

# pkgbuild preserves macOS extended attributes as AppleDouble (._*) files.
# They are not part of the application and make the payload noisy, so strip
# them before creating the installer. COPYFILE_DISABLE also prevents cp from
# recreating them while staging the bundle.
if command -v xattr >/dev/null 2>&1; then
    xattr -rc dist/dlp
fi

STAGE=$(mktemp -d "${TMPDIR:-/tmp}/dlp-pkg.XXXXXX")
SANITIZE=""
trap 'rm -rf "$STAGE" ${SANITIZE:+"$SANITIZE"}' EXIT
mkdir -p "$STAGE/usr/local/libexec/dlp" "$STAGE/usr/local/bin"
COPYFILE_DISABLE=1 cp -R dist/dlp/. "$STAGE/usr/local/libexec/dlp/"
ln -s ../libexec/dlp/dlp "$STAGE/usr/local/bin/dlp"
if command -v xattr >/dev/null 2>&1; then
    xattr -rc "$STAGE"
fi
find "$STAGE" -name '._*' -print -exec rm -rf {} +
pkgbuild \
    --root "$STAGE" \
    --identifier com.dlp.cli \
    --version "$VERSION" \
    --install-location / \
    "$ROOT/dist/dlp-macos-arm64.pkg"

# Some macOS filesystems attach com.apple.provenance to every generated file.
# pkgbuild serializes those attributes as AppleDouble (._*) entries inside the
# payload. Repack only when they are present so the installer stays clean on
# both local and CI builders.
if pkgutil --payload-files "$ROOT/dist/dlp-macos-arm64.pkg" | grep -Eq '(^|/)\._[^/]*$'; then
    SANITIZE=$(mktemp -d "${TMPDIR:-/tmp}/dlp-pkg-sanitize.XXXXXX")
    xar -xf "$ROOT/dist/dlp-macos-arm64.pkg" -C "$SANITIZE"
    mkdir "$SANITIZE/root"
    gunzip -c "$SANITIZE/Payload" > "$SANITIZE/Payload.cpio"
    (cd "$SANITIZE/root" && cpio -idm < "$SANITIZE/Payload.cpio")
    find "$SANITIZE/root" -name '._*' -delete
    mkbom "$SANITIZE/root" "$SANITIZE/Bom"
    (
        cd "$SANITIZE/root"
        find . -print | cpio -o -H odc --owner 0:0 > "$SANITIZE/Payload.clean.cpio"
    )
    gzip -n -c "$SANITIZE/Payload.clean.cpio" > "$SANITIZE/Payload"
    (
        cd "$SANITIZE"
        xar -c --compression=none --prop-exclude '.*' \
            -f "$ROOT/dist/dlp-macos-arm64.pkg" Bom Payload PackageInfo
    )
fi

if pkgutil --payload-files "$ROOT/dist/dlp-macos-arm64.pkg" | grep -Eq '(^|/)\._[^/]*$'; then
    echo "macOS package still contains AppleDouble payload entries" >&2
    exit 1
fi

echo "Created dist/dlp-macos-arm64.pkg"
