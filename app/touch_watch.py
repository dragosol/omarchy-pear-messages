#!/usr/bin/env python3
"""Report when fingers land on the touchpad, so Pear Messages can catch a coasting scroll.

Why this exists at all: resting fingers on a touchpad sends no scroll event until they move
past libinput's threshold. The signal for "fingers down, still" is the Wayland hold gesture
(zwp_pointer_gesture_hold_v1). Hyprland sends it; Qt 6.11's Wayland client never binds it -
it implements pinch and swipe only - so a Qt app is never told. GTK is, which is why a flick
can be caught in Zen and not here. This reads the one fact needed straight off the device.

Deliberately minimal (the same helper Pear Passwords uses):
  * opens only the touchpad - a touchpad cannot carry keystrokes, the keyboard is another device
  * prints the single word `touch` on a finger landing; never a coordinate, never anything else
  * read-only, standard library only, exits when its parent (the app) goes away
If there is no readable touchpad it exits quietly and the app simply loses the catch.

Run with the system interpreter: it needs nothing else.
"""

from __future__ import annotations

import glob
import os
import select
import struct
import sys

EV_KEY = 0x01
BTN_TOUCH = 0x14A          # any finger in contact
EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit: timeval, type, code, value


def find_touchpad() -> str | None:
    for name_file in sorted(glob.glob("/sys/class/input/event*/device/name")):
        try:
            with open(name_file) as fh:
                name = fh.read()
        except OSError:
            continue
        if "touchpad" in name.lower():
            return "/dev/input/" + name_file.split("/")[4]
    return None


def main() -> int:
    dev = find_touchpad()
    if dev is None:
        return 0
    try:
        fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return 0
    parent = os.getppid()
    buf = b""
    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 2.0)
            if os.getppid() != parent:          # the app is gone; don't linger
                return 0
            if not ready:
                continue
            try:
                buf += os.read(fd, EVENT.size * 64)
            except BlockingIOError:
                continue
            while len(buf) >= EVENT.size:
                _, _, etype, code, value = EVENT.unpack_from(buf)
                buf = buf[EVENT.size:]
                if etype == EV_KEY and code == BTN_TOUCH and value == 1:
                    sys.stdout.write("touch\n")
                    sys.stdout.flush()
    except (OSError, BrokenPipeError):
        return 0
    finally:
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
