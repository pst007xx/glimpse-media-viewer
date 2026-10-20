#!/usr/bin/env bash
# Installs Image2 for the current user (no root needed).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
bin="$HOME/.local/bin"
apps="$HOME/.local/share/applications"
mkdir -p "$bin" "$apps"
install -m 755 "$here/image2.py" "$bin/image2"
sed "s|@EXEC@|$bin/image2|" "$here/io.github.imagery.Image2.desktop" > "$apps/io.github.imagery.Image2.desktop"
icons="$HOME/.local/share/icons/hicolor"
install -Dm 644 "$here/icons/io.github.imagery.Image2.svg" "$icons/scalable/apps/io.github.imagery.Image2.svg"
install -Dm 644 "$here/icons/io.github.imagery.Image2-256.png" "$icons/256x256/apps/io.github.imagery.Image2.png"
gtk-update-icon-cache -q -t "$icons" 2>/dev/null || true
update-desktop-database "$apps" 2>/dev/null || true
echo "Installed. Launch “Image2” from Activities, or run: image2 [image-or-folder]"
echo "To make it the default viewer: right-click an image in Files → Open With… → Image2 → Always use."
