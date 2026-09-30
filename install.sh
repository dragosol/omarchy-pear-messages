#!/usr/bin/env bash
# Pear Messages installer. User-level only: it never uses sudo and refuses to run as root.
#
# What it does, all under your home directory:
#   ~/.local/share/pear-messages/backend   the service (Python, standard library + system D-Bus bindings)
#   ~/.local/share/pear-messages/app       the app window (Quickshell), copied from ./app
#   ~/.local/share/applications/pear-messages.desktop   the "Pear Messages" launcher
#   ~/.config/systemd/user/pear-messages.service        runs the service in your session
# Your messages live in ~/.local/share/pear-messages/messages.db and your settings in
# ~/.config/pear-messages; this script never touches either.
#
# Re-run it after `omarchy plugin update` to pick up a new version.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
data="$HOME/.local/share/pear-messages"
apps="$HOME/.local/share/applications"
units="$HOME/.config/systemd/user"
omarchy_shell="/usr/share/omarchy/shell"

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "run this as your own user, not root - nothing here needs root"

for cmd in python3 quickshell systemctl; do
  command -v "$cmd" >/dev/null 2>&1 || die "'$cmd' is required but not installed"
done
[ -d "$omarchy_shell/Ui" ] && [ -d "$omarchy_shell/Commons" ] \
  || die "Omarchy's shell components were not found in $omarchy_shell"
python3 -c 'import dbus, gi; gi.require_version("GLib", "2.0"); from gi.repository import GLib' 2>/dev/null \
  || die "the D-Bus bindings for Python are missing: sudo pacman -S --needed python-dbus python-gobject"
command -v notify-send >/dev/null 2>&1 || warn "notify-send is not installed: new messages will not pop up"

missing_obex=0
if [ ! -x /usr/lib/bluetooth/obexd ]; then
  missing_obex=1
  warn "bluez-obex is not installed. BlueBubbles works without it; the iPhone-over-Bluetooth"
  warn "connection needs it:  sudo pacman -S --needed bluez-obex"
fi

mkdir -p "$data" "$apps" "$units"

say "Installing the service into $data/backend"
rm -rf "$data/backend.new"
mkdir -p "$data/backend.new"
cp -r "$here/backend/pearmsg" "$data/backend.new/"
find "$data/backend.new" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$data/backend.old"
[ ! -d "$data/backend" ] || mv "$data/backend" "$data/backend.old"
mv "$data/backend.new" "$data/backend"
rm -rf "$data/backend.old"

say "Installing the app into $data/app"
# Built beside the old copy and swapped in, so a failed copy never leaves a half-installed app.
rm -rf "$data/app.new"
cp -r "$here/app" "$data/app.new"
# The app draws with Omarchy's own components, so it follows your theme.
ln -s "$omarchy_shell/Ui" "$data/app.new/Ui"
ln -s "$omarchy_shell/Commons" "$data/app.new/Commons"
rm -rf "$data/app.old"
[ ! -d "$data/app" ] || mv "$data/app" "$data/app.old"
mv "$data/app.new" "$data/app"
rm -rf "$data/app.old"

say "Adding the Pear Messages launcher"
cat > "$apps/pear-messages.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Pear Messages
Comment=iMessage and SMS through BlueBubbles or your iPhone
Exec=$data/app/launch.sh
Icon=$data/app/icon.svg
Terminal=false
Categories=Network;InstantMessaging;Chat;
Keywords=imessage;messages;sms;text;chat;iphone;bluebubbles;pear;
DESKTOP
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$apps" || true

say "Starting the Pear Messages service"
install -m 644 "$here/systemd/pear-messages.service" "$units/"
systemctl --user daemon-reload
[ "$missing_obex" -eq 1 ] || systemctl --user enable --now obex.service >/dev/null 2>&1 || true
systemctl --user enable pear-messages.service
systemctl --user restart pear-messages.service

cat <<DONE

Pear Messages is installed. Open it from the launcher: search "Pear Messages".
The first launch opens Settings, where the connection assistant sets up BlueBubbles
and/or pairs your iPhone.
DONE
