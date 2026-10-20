#!/usr/bin/env bash
# Installs Imagery for the current user (no root needed).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
bin="$HOME/.local/bin"
apps="$HOME/.local/share/applications"
mkdir -p "$bin" "$apps"
install -m 755 "$here/imagery.py" "$bin/imagery"
sed "s|@EXEC@|$bin/imagery|" "$here/io.github.imagery.Viewer.desktop" > "$apps/io.github.imagery.Viewer.desktop"
update-desktop-database "$apps" 2>/dev/null || true
echo "Installed. Launch “Imagery” from Activities, or run: imagery [image-or-folder]"
echo "To make it the default viewer: right-click an image in Files → Open With… → Imagery → Always use."
