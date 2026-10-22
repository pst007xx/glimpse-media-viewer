#!/usr/bin/env bash
# Installs Glimpse Image Viewer for the current user (no root needed).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
bin="$HOME/.local/bin"
apps="$HOME/.local/share/applications"
mkdir -p "$bin" "$apps"
install -m 755 "$here/glimpse.py" "$bin/glimpse"
sed "s|@EXEC@|$bin/glimpse|" "$here/io.github.pst007xx.Glimpse.desktop" > "$apps/io.github.pst007xx.Glimpse.desktop"
icons="$HOME/.local/share/icons/hicolor"
for size in 48 64 128 256; do
    install -Dm 644 "$here/icons/io.github.pst007xx.Glimpse-$size.png" "$icons/${size}x${size}/apps/io.github.pst007xx.Glimpse.png"
done
gtk-update-icon-cache -q -t "$icons" 2>/dev/null || true
update-desktop-database "$apps" 2>/dev/null || true
echo "Installed. Launch “Glimpse Image Viewer” from Activities, or run: glimpse [file-or-folder]"
echo "To make it the default viewer: right-click an image in Files → Open With… → Glimpse Image Viewer → Always use."
