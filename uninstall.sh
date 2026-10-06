#!/usr/bin/env bash
# Removes what install.sh added. Your messages (~/.local/share/pear-messages/messages.db) and
# settings (~/.config/pear-messages, including the BlueBubbles password) are kept unless you
# pass --purge. The iPhone stays paired; unpair it in Bluetooth settings if you want.
set -euo pipefail
[ "$(id -u)" -ne 0 ] || { echo "run this as your own user, not root" >&2; exit 1; }

data="$HOME/.local/share/pear-messages"

# Older versions installed a systemd user unit; this one does not. Remove it if it is there,
# without needing any service manager to be present.
rm -f "$HOME/.config/systemd/user/pear-messages.service"
pkill -f "pearmsg daemon" 2>/dev/null || true
rm -f "$HOME/.local/share/applications/pear-messages.desktop"
rm -rf "$data/app" "$data/backend"
rm -f "$data/.plugin-manifest"
rm -rf "$HOME/.cache/pear-messages"
echo "Pear Messages removed."

if [ "${1:-}" = "--purge" ]; then
  read -r -p "Also delete your stored messages and settings (including the BlueBubbles password)? [y/N] " yn
  if [ "$yn" = "y" ] || [ "$yn" = "Y" ]; then
    rm -rf "$data" "$HOME/.config/pear-messages"
    echo "Messages and settings deleted."
  fi
else
  echo "Your messages and settings were kept. Run with --purge to delete them too."
fi
