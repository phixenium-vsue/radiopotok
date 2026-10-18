#!/bin/sh
# RadioPotok (Web) one-line installer
set -e

REPO="phixenium-vsue/radiopotok"
ASSET="radiopotok-web.tar.gz"
DEST="$HOME/.local/bin/radiopotok-web"
APPS="$HOME/.local/share/applications"
DESKTOP="$APPS/radiopotok-web.desktop"
ICON="$DEST/icon.svg"
RUN="$DEST/run-web.sh"

usage() {
  echo "Usage: $0"
}

# create dirs
mkdir -p "$DEST" "$APPS" "$HOME/.local/bin"

# download tarball (prefer explicit override via RADIOPOTOK_TARBALL)
TARBALL="${RADIOPOTOK_TARBALL:-}"
if [ -z "$TARBALL" ]; then
  TARBALL="$(mktemp -t radiopotok-XXXX.tar.gz)"
  URL="https://github.com/$REPO/releases/latest/download/$ASSET"
  echo "Downloading $URL..."
  if command -v wget >/dev/null 2>&1; then
    wget -q --show-progress -O "$TARBALL" "$URL" || { echo "wget failed"; exit 1; }
  else
    echo "wget not found"; exit 1
  fi
else
  [ -f "$TARBALL" ] || { echo "RADIOPOTOK_TARBALL not found: $TARBALL"; exit 1; }
fi

# extract
echo "Extracting to $DEST..."
rm -rf "$DEST.tmp"
mkdir -p "$DEST.tmp"
tar xzf "$TARBALL" -C "$DEST.tmp"
# find app dir (should be app/)
if [ -d "$DEST.tmp/app" ]; then
  rm -rf "$DEST"
  mv "$DEST.tmp/app" "$DEST"
else
  # maybe tar contains app at root
  rm -rf "$DEST"
  mv "$DEST.tmp" "$DEST"
fi

# make executable
chmod +x "$RUN" 2>/dev/null || true

# create desktop entry
cat > "$DESKTOP" << DESK
[Desktop Entry]
Name=RadioPotok (Web)
Comment=Радио онлайн (веб-версия)
Exec=$RUN
Icon=$ICON
Type=Application
Categories=Audio;Music;
Terminal=false
X-Ubuntu-Touch=true
X-Lomiri-Touch=true
DESK

chmod +x "$DESKTOP" 2>/dev/null || true

echo "Done."
echo "Installed to: $DEST"
echo "Launcher: $DESKTOP"
