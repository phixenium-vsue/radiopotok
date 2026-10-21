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

# самолечение: прошлый запуск оборвался между сохранением и возвратом данных
if [ -d "$DEST.data.keep" ] && [ ! -d "$DEST/data" ]; then
  mkdir -p "$DEST/data"
  cp -a "$DEST.data.keep/." "$DEST/data/" 2>/dev/null || true
  rm -rf "$DEST.data.keep"
fi

# download tarball (prefer explicit override via RADIOPOTOK_TARBALL)
TARBALL="${RADIOPOTOK_TARBALL:-}"
# авто-детект: архив, лежащий рядом со скриптом (оффлайн-режим, сеть не нужна)
if [ -z "$TARBALL" ] && [ -f "$0" ]; then
  NEAR="$(dirname "$0")/$ASSET"
  if [ -f "$NEAR" ]; then
    echo "Найден локальный архив: $NEAR"
    TARBALL="$NEAR"
  fi
fi
if [ -z "$TARBALL" ]; then
  TARBALL="$(mktemp -t radiopotok-XXXX.tar.gz)"
  TARBALL_MINE=1
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
  SRC="$DEST.tmp/app"
else
  # maybe tar contains app at root
  SRC="$DEST.tmp"
fi

# сохранить пользовательские данные (избранное/скрытые/статус/скан) при установке поверх
if [ -d "$DEST/data" ]; then
  rm -rf "$DEST.data.keep"
  mv "$DEST/data" "$DEST.data.keep"
fi

rm -rf "$DEST"
mv "$SRC" "$DEST"
rm -rf "$DEST.tmp"

# вернуть сохранённые данные
if [ -d "$DEST.data.keep" ]; then
  mkdir -p "$DEST/data"
  cp -a "$DEST.data.keep/." "$DEST/data/" 2>/dev/null || true
  rm -rf "$DEST.data.keep"
fi

# убрать временный тарболл (только свой из mktemp; пользовательский RADIOPOTOK_TARBALL не трогаем)
if [ "$TARBALL_MINE" = "1" ]; then
  rm -f "$TARBALL"
fi

# make executable
chmod +x "$RUN" 2>/dev/null || true
chmod +x "$DEST/uninstall.sh" 2>/dev/null || true

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
