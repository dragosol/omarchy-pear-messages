#!/bin/sh
# Opens Pear Messages, optionally on one conversation:  launch.sh [thread-id]
# The launcher entry runs this without a thread; a notification click runs it with one.
#
# It is a regular window: Hyprland tiles it like any other app when tiling is on. It asks for
# 1040x680, which is the size it gets whenever it floats.
here="$(dirname "$(readlink -f "$0")")"
backend="$here/../backend"
thread="${1:-}"
run="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/pear-messages"
mkdir -p "$run"

focus_running() {
  if [ -n "$thread" ]; then
    PYTHONPATH="$backend" python3 -B -m pearmsg open "$thread" >/dev/null 2>&1
  fi
  command -v hyprctl >/dev/null 2>&1 && hyprctl dispatch focuswindow 'title:^Pear Messages$' >/dev/null 2>&1
}

# One window. The open window holds this lock for as long as it runs (quickshell inherits it),
# so a second launch - a notification click racing the launcher, say - just brings it forward.
exec 9>"$run/window.lock"
if ! flock -n 9; then
  focus_running
  exit 0
fi

# The service should already be running; start it if not (it is what the window talks to).
systemctl --user start pear-messages.service >/dev/null 2>&1 || true

PEAR_MESSAGES_OPEN="$thread" exec quickshell -p "$here"
