#!/usr/bin/env bash
# Puts ShowStack in your application menu (and on the desktop). --startup also opens ShowStack when you sign in.
#   bash install.sh [--startup]
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"
icon="$repo/assets/icon-256.png"
startup=false
[[ "${1:-}" == "--startup" ]] && startup=true

if ! python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' 2>/dev/null; then
    echo "ShowStack needs Python's GTK 3 bindings, which Linux Mint normally has. Installing them:"
    echo "  sudo apt install python3-gi gir1.2-gtk-3.0"
    sudo apt install -y python3-gi gir1.2-gtk-3.0
fi

chmod +x "$here/showstack.py" "$here/showstack-report.py"
mkdir -p "$HOME/.local/share/applications"

# A launcher entry; StartupWMClass lets the panel group the window under its own icon.
entry="$HOME/.local/share/applications/showstack.desktop"
cat > "$entry" <<EOF
[Desktop Entry]
Type=Application
Name=ShowStack
Comment=Your whole local dev stack in one small window
Exec=python3 "$here/showstack.py"
Path=$here
Icon=$icon
Terminal=false
Categories=Development;
StartupWMClass=showstack
StartupNotify=false
EOF
chmod +x "$entry"
echo "Menu entry created: $entry"

desktop_dir="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [[ -d "$desktop_dir" ]]; then
    cp "$entry" "$desktop_dir/showstack.desktop"
    chmod +x "$desktop_dir/showstack.desktop"
    # Nemo and Nautilus only run a desktop file they trust.
    gio set "$desktop_dir/showstack.desktop" metadata::trusted true 2>/dev/null || true
    gio set "$desktop_dir/showstack.desktop" metadata::nemo-trusted true 2>/dev/null || true
    echo "Shortcut created: $desktop_dir/showstack.desktop (if it asks, choose Allow Launching / Trust and Launch)"
fi

if $startup; then
    mkdir -p "$HOME/.config/autostart"
    cp "$entry" "$HOME/.config/autostart/showstack.desktop"
    echo "ShowStack will also open when you sign in: $HOME/.config/autostart/showstack.desktop"
fi

update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo 'Open "ShowStack" from the menu or the desktop. Settings: ~/.config/showstack/showstack.json (created on first run).'
