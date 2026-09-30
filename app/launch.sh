#!/bin/sh
# Opens Pear Messages. The launcher entry runs this, not quickshell directly.
#
# The window floats centred at the 1040x680 it is laid out for, at full opacity. Hyprland decides
# that when a window maps, so the rules are registered here, just before it opens, through
# `hyprctl eval` - nothing is written to your Hyprland config, nothing needs a reload, and
# uninstalling leaves nothing behind. They are registered once per Hyprland session.
here="$(dirname "$(readlink -f "$0")")"

# Already open: bring it forward instead of opening a second window.
if command -v hyprctl >/dev/null 2>&1 && hyprctl clients -j 2>/dev/null | grep -q '"title": "Pear Messages"'; then
  hyprctl dispatch focuswindow 'title:^Pear Messages$' >/dev/null 2>&1
  exit 0
fi

if [ -n "$HYPRLAND_INSTANCE_SIGNATURE" ] && command -v hyprctl >/dev/null 2>&1; then
  hyprctl eval '
if not _G.__pear_messages_rules then
  local m = { class = [[^org\.quickshell$]], title = [[^Pear Messages$]] }
  hl.window_rule({ match = m, tag = [[-default-opacity]] })
  hl.window_rule({ match = m, opacity = [[1 override 1 override]] })
  hl.window_rule({ match = m, float = true })
  hl.window_rule({ match = m, size = [[1040 680]] })
  hl.window_rule({ match = m, center = true })
  _G.__pear_messages_rules = true
end' >/dev/null 2>&1 || true
fi

# The service should already be running; start it if not (it is what the window talks to).
systemctl --user start pear-messages.service >/dev/null 2>&1 || true

exec quickshell -p "$here"
