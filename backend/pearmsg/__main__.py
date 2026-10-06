"""pear-messages: the daemon, plus a small CLI that talks to it.

    pear-messages daemon                 run the daemon (the systemd user unit does this)
    pear-messages status                 connection state, as JSON
    pear-messages threads                conversations, newest first
    pear-messages send <to|thread> <text...>
    pear-messages open [thread]          open the app window
"""
from __future__ import annotations

import json
import socket
import sys


def _call(req: dict, want: str, timeout: float = 30.0) -> dict:
    from .daemon import SOCK
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(SOCK)
    except OSError:
        sys.exit("pear-messages: the daemon isn't running. It starts with your Omarchy shell; "
                 "open Pear Messages, or run it directly:  python3 -B -u -m pearmsg daemon")
    req = {**req, "id": 1}
    s.sendall((json.dumps(req) + "\n").encode())
    buf = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            sys.exit("pear-messages: the daemon closed the connection")
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            ev = json.loads(line)
            if ev.get("re") == 1 and ev.get("ev") in (want, "error"):
                return ev


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    if cmd == "daemon":
        from .daemon import main as run
        run()
    elif cmd == "status":
        print(json.dumps(_call({"op": "status"}, "status"), indent=2, ensure_ascii=False))
    elif cmd == "threads":
        for t in _call({"op": "threads"}, "threads")["threads"]:
            flag = f" ({t['unread']})" if t["unread"] else ""
            print(f"{t['id']:<40} {t['title']}{flag}")
    elif cmd == "send" and len(argv) >= 3:
        target, text = argv[1], " ".join(argv[2:])
        req = {"op": "send", "text": text}
        if target.startswith(("addr:", "chat:")):
            req["thread"] = target
        else:
            req["to"] = target
        print(json.dumps(_call(req, "sent")))
    elif cmd == "open":
        from .daemon import SOCK
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(SOCK)
        s.sendall((json.dumps({"op": "open_app", "thread": argv[1] if len(argv) > 1 else ""}) + "\n").encode())
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
