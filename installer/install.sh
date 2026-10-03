#!/usr/bin/env bash
# WinMeh for Linux - one-command install, no root needed.
#
#   From an extracted release:   ./install.sh
#   Straight from GitHub:        curl -fsSL https://raw.githubusercontent.com/0xAshraFF/WinMeh/main/installer/install.sh | bash
#   Remove:                      ./install.sh --uninstall
set -euo pipefail

REPO="0xAshraFF/WinMeh"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/winmeh-app"
BIN="$HOME/.local/bin"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

if [[ "${1:-}" == "--uninstall" ]]; then
  pkill -f "$DEST/" 2>/dev/null || true
  rm -rf "$DEST" "$BIN/winmeh" "$APPS/winmeh.desktop" "$AUTOSTART/winmeh.desktop"
  echo "WinMeh removed. Your settings stay in ~/.local/share/winmeh (delete it to wipe everything)."
  exit 0
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "")"
if [[ -z "$SRC" || ! -x "$SRC/WinMeh/WinMeh" ]]; then
  arch="$(uname -m)"; [[ "$arch" == "x86_64" ]] || { echo "Only x86_64 builds are published right now."; exit 1; }
  echo "Downloading the latest WinMeh release..."
  tmp="$(mktemp -d)"
  curl -fL --progress-bar "https://github.com/$REPO/releases/latest/download/WinMeh-linux-x64.tar.gz" -o "$tmp/w.tgz"
  tar -xzf "$tmp/w.tgz" -C "$tmp"
  SRC="$tmp"
fi

echo "Installing to $DEST"
pkill -f "$DEST/" 2>/dev/null || true
rm -rf "$DEST"
mkdir -p "$DEST" "$BIN" "$APPS" "$AUTOSTART"
cp -a "$SRC/WinMeh/." "$DEST/"
cp "$SRC/winmeh.png" "$DEST/winmeh.png" 2>/dev/null || true
ln -sf "$DEST/WinMeh" "$BIN/winmeh"

desktop="[Desktop Entry]
Type=Application
Name=WinMeh
Comment=Tiny local desktop assistant
Exec=$DEST/WinMeh
Icon=$DEST/winmeh.png
Terminal=false
Categories=Utility;
StartupNotify=false"
echo "$desktop" > "$APPS/winmeh.desktop"
echo "$desktop" > "$AUTOSTART/winmeh.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" >/dev/null 2>&1 || true

# System libraries Qt and the microphone need, which some minimal installs lack.
missing=()
ldconfig -p 2>/dev/null | grep -q libxcb-cursor.so.0 || missing+=("libxcb-cursor0")
ldconfig -p 2>/dev/null | grep -q libportaudio.so.2 || missing+=("libportaudio2")
if (( ${#missing[@]} )); then
  echo
  echo "Almost done - your system is missing: ${missing[*]}"
  if command -v apt-get >/dev/null; then echo "  sudo apt install ${missing[*]}"
  elif command -v dnf >/dev/null; then echo "  sudo dnf install xcb-util-cursor portaudio"
  elif command -v pacman >/dev/null; then echo "  sudo pacman -S xcb-util-cursor portaudio"
  fi
fi

echo
echo "Installed. Start it from your app menu or run: winmeh"
echo "It also starts automatically when you log in."
echo "Hotkeys: add keyboard shortcuts in your desktop settings for  'winmeh --talk'  and  'winmeh --toggle'."
nohup "$DEST/WinMeh" >/dev/null 2>&1 &
