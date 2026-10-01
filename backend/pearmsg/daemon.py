"""pear-messagesd: the one process that talks to BlueBubbles and the iPhone.

The app window is only a view: it connects to this daemon's socket, gets the threads and
messages, and asks it to send. The daemon keeps running when the window is closed, so
messages still arrive, are stored, and show up as notifications.

Routing a send:
  1. BlueBubbles, when it is online (the only way into a group chat).
  2. The iPhone over Bluetooth, for one-to-one chats, when BlueBubbles is offline or the
     send through it fails.
A message that went out one way and is later reported by the other is merged by the
store, so it appears once.

Socket protocol: one JSON object per line, both ways. Requests carry "op"; the daemon
answers with events carrying "ev" (and "re" = the request's "id" when it is an answer).
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

from . import bluebubbles as BB
from . import contacts as C
from . import iphone as IP
from . import tailnet
from . import tapback
from . import media
from . import linkpreview
from .store import Store, addr_thread

HOME = os.path.expanduser("~")
CONF = os.path.join(os.environ.get("XDG_CONFIG_HOME", HOME + "/.config"), "pear-messages")
DATA = os.path.join(os.environ.get("XDG_DATA_HOME", HOME + "/.local/share"), "pear-messages")
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME", HOME + "/.cache"), "pear-messages")
RUNTIME = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "pear-messages")
SOCK = os.path.join(RUNTIME, "sock")
SETTINGS = os.path.join(CONF, "settings.json")
BB_CREDS = os.path.join(CONF, "bluebubbles.json")

DEFAULTS = {
    "iphone": {"address": "", "enabled": True, "keepAudio": True},
    "bluebubbles": {"enabled": True},
    "notifications": True,
    "notificationPreview": True,
    "linkPreviews": True,
    "typingAnimation": True,
}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def _read_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path: str, data, mode: int = 0o600) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def load_settings() -> dict:
    s = _read_json(SETTINGS, {})
    out = json.loads(json.dumps(DEFAULTS))
    for k, v in s.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


class Conn:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buf = b""
        self.viewing = ""
        self.active = False
        self.watch = 0


class Daemon:
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.sys = dbus.SystemBus()
        self.ses = dbus.SessionBus()
        self.loop = GLib.MainLoop()
        self.settings = load_settings()
        self.store = Store(os.path.join(DATA, "messages.db"))
        self.conns: list[Conn] = []
        self._link_waiting: dict[str, list] = {}
        self._in_flight: set[int] = set()
        self._retry_at: dict[int, float] = {}
        self._mac_tries: dict[int, int] = {}
        self.bb_state = {"state": "off", "detail": ""}
        self.bb_info: dict = {}
        self.bb_online_at = 0.0
        self.poller: BB.Poller | None = None
        self.bb_link = ""
        self.client: BB.Client | None = None
        self.phone_state = {"state": "off", "detail": ""}
        self.guard = IP.AudioGuard(self.sys, log)
        self.phone = IP.Phone(self.sys, self.ses, self._from_phone_thread_safe, log, os.path.join(CACHE, "tmp"))
        self.pairing = IP.Pairing(self.sys, self._broadcast, log, self._paired)

    # ------------------------------------------------------------ transports
    def apply_settings(self) -> None:
        s = self.settings
        # iPhone
        addr = s["iphone"]["address"] if s["iphone"]["enabled"] else ""
        if not s["iphone"]["address"]:
            phones = IP.list_phones(self.sys)
            if len(phones) == 1:
                s["iphone"]["address"] = phones[0]["address"]
                addr = phones[0]["address"] if s["iphone"]["enabled"] else ""
                _write_json(SETTINGS, s, 0o644)
        self.guard.enabled = bool(s["iphone"].get("keepAudio", True))
        self.guard.watch(s["iphone"]["address"])
        self.phone.set_phone(addr)
        if not addr:
            self.phone_state = {"state": "off", "detail": "" if s["iphone"]["enabled"] else "Turned off"}
        # BlueBubbles
        creds = _read_json(BB_CREDS, {})
        want = bool(s["bluebubbles"]["enabled"] and creds.get("url") and creds.get("password"))
        same = self.client and want and self.client.url == BB.Client(creds["url"], "").url \
            and self.client.password == creds.get("password")
        if self.poller and not same:
            self.poller.stop()
            self.poller = None
            self.client = None
        if want and not self.poller:
            self.client = BB.Client(creds["url"], creds["password"])
            since = int(float(self.store.get_meta("bb_since_ms", "0") or 0))
            # "backfill_v2": the default history (100 messages for the 20 latest chats, 20 for the
            # rest) - taken once, including by installs that synced before it existed.
            self.poller = BB.Poller(self.client, since, self._from_bb_thread,
                                    backfill=self.store.get_meta("backfill_v2") != "done")
            self.poller.start()
        if not want:
            self.bb_state = {"state": "off", "detail": "" if creds.get("url") else "Not set up"}
        self._broadcast_status()

    @property
    def bb_online(self) -> bool:
        return self.bb_state.get("state") == "online"

    def _from_bb_thread(self, kind, payload):
        GLib.idle_add(self._on_bb, kind, payload)

    def _from_phone_thread_safe(self, kind, payload):
        self._on_phone(kind, payload)

    def _on_bb(self, kind, payload):
        if kind == "status":
            prev = self.bb_state.get("state")
            self.bb_state = {"state": payload["state"], "detail": payload.get("detail", "")}
            if payload.get("info"):
                self.bb_info = payload["info"]
                # the Mac's own iMessage address is yours
                own = [a for a in (self.bb_info.get("detected_imessage"), self.bb_info.get("detected_icloud")) if a]
                if own and self.store.add_self(own):
                    self._identity_changed()
            if payload["state"] == "online":
                self.bb_online_at = time.time()
                if self.client:
                    self.bb_link = tailnet.describe(self.client.url)
            if prev != payload["state"]:
                log(f"BlueBubbles: {payload['state']} {payload.get('detail', '')}")
                self._broadcast_status()
                if payload["state"] == "online":
                    GLib.timeout_add_seconds(2, lambda: (self._flush(), False)[1])
        elif kind == "messages":
            self._ingest_bb(payload)
        elif kind == "backfill_failed":
            log(f"history from the Mac didn't finish ({payload.get('error')}); will try again later")
        elif kind == "backfilled":
            self.store.set_meta("backfill_v2", "done")
            log(f"history from the Mac: {payload.get('chats')} conversations")
        elif kind == "contacts":
            n = self.store.set_contacts(payload, "bluebubbles")
            log(f"BlueBubbles contacts: {n} addresses")
            self._identity_changed()
        return False

    def _identity_changed(self) -> None:
        """Contacts or your own addresses changed: conversations may now belong together."""
        n = self.store.rethread()
        if n:
            log(f"merged {n} conversation(s) into their person's thread")
        self._broadcast_threads()

    def _on_phone(self, kind, payload):
        if kind == "status":
            if payload != self.phone_state:
                self.phone_state = payload
                log(f"iPhone: {payload['state']} {payload.get('detail', '')}")
                self._broadcast_status()
                if payload["state"] == "online":
                    GLib.timeout_add_seconds(2, lambda: (self._flush(), False)[1])
        elif kind == "messages":
            self._ingest_phone(payload["messages"], payload.get("initial", False))
        elif kind == "contacts":
            n = self.store.set_contacts(payload, "iphone")
            log(f"iPhone contacts: {n} addresses")
            self._identity_changed()

    # ---------------------------------------------------------------- ingest
    def _ingest_bb(self, raw: list[dict]) -> None:
        changed: dict[str, list[int]] = {}
        fresh = []
        newest = 0
        for m in raw:
            newest = max(newest, m.get("dateCreated") or 0)
            n = BB.to_message(m)
            if n is None:
                continue
            if n["kind"] == "reaction":
                rid = self.store.react(n["target"], n["sender"] or "?", n["reaction"])
                if rid:
                    msg = self.store.message(rid)
                    changed.setdefault(msg["thread"], []).append(rid)
                continue
            what, rid = self.store.ingest(n, "bluebubbles")
            msg = self.store.message(rid)
            changed.setdefault(msg["thread"], []).append(rid)
            # Old history on first sync is not "new"; only recent incoming messages notify.
            if what == "new" and not n["from_me"] and time.time() - n["ts"] < 120:
                fresh.append(rid)
        if self.store.merge_orphans():
            self._broadcast_threads()
        if self.store.learn_self():
            self._identity_changed()
        if newest:
            cur = int(float(self.store.get_meta("bb_since_ms", "0") or 0))
            self.store.set_meta("bb_since_ms", str(max(cur, int(newest))))
        self.store.db.commit()
        self._after_ingest(changed, fresh)

    def _ingest_phone(self, msgs: list[dict], initial: bool) -> None:
        changed: dict[str, list[int]] = {}
        fresh = []
        bb_recent = self.bb_online or time.time() - self.bb_online_at < 600
        for n in msgs:
            # Over Bluetooth a reaction arrives as a sentence in the phone's language ('Loved
            # “hi”', 'a attribué la mention « Adore » à « hi »'). When BlueBubbles is around it
            # delivers the real reaction, so the sentence is dropped; otherwise it becomes a
            # reaction on the message it quotes.
            tb = tapback.parse(n["text"])
            if tb:
                target = self.store.find_quoted(self.store.canonical(n["thread"]), tb.quoted, n["ts"])
                if target is not None:
                    if not bb_recent:
                        self.store.react_row(target["id"], C.key(n.get("sender_addr", "")), tb.kind)
                        changed.setdefault(target["thread"], []).append(target["id"])
                    continue
            what, rid = self.store.ingest(n, "iphone")
            msg = self.store.message(rid)
            changed.setdefault(msg["thread"], []).append(rid)
            if what == "new" and not initial and time.time() - n["ts"] < 600:
                fresh.append(rid)
            elif what == "new" and initial and time.time() - n["ts"] < 60:
                fresh.append(rid)
        self._after_ingest(changed, fresh)

    def _after_ingest(self, changed: dict, fresh: list[int]) -> None:
        if self.store.newly_hidden:
            changed = {k: list(v) for k, v in changed.items()}
            for i in self.store.newly_hidden:
                changed.setdefault("addr:self", []).append(i)
            self.store.newly_hidden = []
        if not changed:
            return
        for tid, ids in changed.items():
            rows = [self.store.message(i) for i in dict.fromkeys(ids)]
            self._broadcast({"ev": "messages", "thread": tid, "messages": [r for r in rows if r]})
        self._broadcast_threads()
        for rid in fresh:
            self._notify(rid)

    # ----------------------------------------------------------------- send
    # ------------------------------------------------------------------ sending
    # Every outgoing message goes through _dispatch, which picks its route now:
    #   BlueBubbles when it's online (the only way for files, group chats and effects);
    #   the iPhone for one-to-one text when BlueBubbles can't;
    #   otherwise it waits - status "queued", with what it's waiting for in words - and goes
    #   by itself as soon as a connection that can carry it is back.
    # "failed" is kept for real refusals, and for a Mac that keeps not sending.
    MAX_MAC_TRIES = 4

    def send(self, thread: str, text: str, to: str = "", effect: str = "", files: list | None = None) -> dict:
        import mimetypes
        text = (text or "").strip()
        files = [f for f in (files or []) if f and os.path.isfile(f)]
        if not text and not files:
            return {"ok": False, "error": "Nothing to send"}
        if files and not self.abilities()["attachments"]:
            return {"ok": False, "error": self.abilities()["attachReason"]}
        if not thread:
            thread = self.store.canonical(addr_thread(to))
        t = self.store.thread(thread)
        if not t:
            self.store.ensure_thread(thread, participants=[to] if to else [])
        elif to:
            self.store.ensure_thread(thread, participants=[to])
        if effect and not self.abilities()["effects"]:
            effect = ""
        ids = []
        # Each file is its own message, then the text - the order Messages uses.
        for f in files:
            name = os.path.basename(f)
            ids.append(self._new_outgoing(thread, "", attachments=[{
                "guid": "", "name": name, "mime": mimetypes.guess_type(name)[0] or "",
                "size": os.path.getsize(f), "path": f}]))
        if text:
            ids.append(self._new_outgoing(thread, text, effect=effect))
        for rid in ids:
            self._dispatch(rid)
        return {"ok": True, "id": ids[-1], "thread": thread}

    def _new_outgoing(self, thread: str, text: str, effect: str = "", attachments=None) -> int:
        _, rid = self.store.ingest({
            "temp_id": BB.new_temp_guid(), "thread": thread, "from_me": True, "text": text,
            "attachments": attachments or [], "ts": time.time(), "status": "sending", "effect": effect}, "local")
        self._after_ingest({thread: [rid]}, [])
        return rid

    def _outgoing(self, rid: int) -> dict | None:
        row = self.store.db.execute("SELECT * FROM messages WHERE id=?", (rid,)).fetchone()
        if row is None or not row["from_me"]:
            return None
        t = self.store.thread(row["thread"]) or {}
        files = [a for a in json.loads(row["attachments"]) if a.get("path") and not a.get("guid")]
        parts = t.get("participants") or []
        temp = row["temp_id"]
        if not temp:
            temp = BB.new_temp_guid()
            self.store.db.execute("UPDATE messages SET temp_id=? WHERE id=?", (temp, rid))
            self.store.db.commit()
        return {"rid": rid, "thread": row["thread"], "text": row["text"], "effect": row["effect"], "temp": temp,
                "file": files[0]["path"] if files else "", "group": bool(t.get("group")),
                "address": parts[0] if parts and not t.get("group") else "", "bb_chat": t.get("bbChat", "")}

    def _changed(self, o: dict) -> None:
        m = self.store.message(o["rid"])
        self._after_ingest({m["thread"] if m else o["thread"]: [o["rid"]]}, [])

    def _waiting_for(self, o: dict) -> str:
        if o["file"]:
            return "Sends when BlueBubbles connects (photos and files go through your Mac)"
        if o["group"]:
            return "Sends when BlueBubbles connects (group chats go through your Mac)"
        return "Sends when BlueBubbles or your iPhone connects"

    def _dispatch(self, rid: int) -> None:
        o = self._outgoing(rid)
        if o is None or rid in self._in_flight:
            return
        self._retry_at.pop(rid, None)
        if self.bb_online and self.client:
            self._via_bb(o)
        elif self._phone_can_take(o):
            self._via_phone(o)
        else:
            self._queue(o, self._waiting_for(o))

    def _phone_can_take(self, o: dict) -> bool:
        return (not o["file"] and not o["group"] and bool(o["address"]) and bool(o["text"])
                and self.phone_state.get("state") == "online")

    def _queue(self, o: dict, why: str, retry_in: float = 0) -> None:
        self.store.set_status(o["rid"], "queued", why)
        if retry_in:
            self._retry_at[o["rid"]] = time.time() + retry_in
        self._changed(o)

    def _fail(self, o: dict, why: str) -> None:
        self._mac_tries.pop(o["rid"], None)
        self._retry_at.pop(o["rid"], None)
        self.store.set_status(o["rid"], "failed", why)
        self._changed(o)

    def _via_bb(self, o: dict) -> None:
        rid = o["rid"]
        self._in_flight.add(rid)
        self.store.set_status(rid, "sending", "")
        self._changed(o)
        client = self.client
        private = bool(self.bb_info.get("private_api"))
        # A conversation the Mac hasn't seen yet can still be addressed by its would-be chat id.
        chat = o["bb_chat"] or (f"any;-;{o['address']}" if o["address"] else "")

        def work():
            started_ms = int(time.time() * 1000)
            try:
                if not chat:
                    raise BB.BBError("No address for this conversation")
                if o["file"]:
                    res = client.send_attachment(chat, o["file"], o["temp"], private)
                elif o["bb_chat"]:
                    res = client.send(o["bb_chat"], o["text"], o["temp"], private, o["effect"])
                else:
                    res = client.new_chat(o["address"], o["text"], o["temp"], private, o["effect"])
                GLib.idle_add(done, res, "", False)
            except BB.BBError as e:
                slow = "timed out" in str(e) or "Can't reach" in str(e)
                # A timeout doesn't mean it wasn't sent: the Mac can be slow to answer while
                # Messages sends. Look before going another way, or it arrives twice.
                if slow and o["text"]:
                    sent = _sent_meanwhile(client, o["text"], started_ms)
                    if sent is not None:
                        GLib.idle_add(done, sent, "", False)
                        return
                GLib.idle_add(done, None, str(e), slow)
            except OSError as e:
                GLib.idle_add(done, None, f"Can't read the file: {e.strerror or e}", False)

        def done(res, err, slow):
            self._in_flight.discard(rid)
            if not err:
                if isinstance(res, dict) and res.get("guid"):
                    n = BB.to_message({**res, "tempGuid": o["temp"]})
                    if n and n.get("kind") == "message":
                        n["temp_id"] = o["temp"]
                        self.store.ingest(n, "bluebubbles")
                self._mac_tries.pop(rid, None)
                self.store.set_status(rid, "sent", "", via="bluebubbles")
                self._changed(o)
                return False
            log(f"BlueBubbles didn't send #{rid}: {err}")
            if not slow:
                # the Mac answered and said no
                if self._phone_can_take(o):
                    self._via_phone(o)
                else:
                    self._fail(o, f"Your Mac refused it: {err}")
                return False
            tries = self._mac_tries[rid] = self._mac_tries.get(rid, 0) + 1
            if self._phone_can_take(o):
                self._via_phone(o)
            elif tries >= self.MAX_MAC_TRIES:
                self._fail(o, "Your Mac isn't sending messages. On the Mac, give BlueBubbles Accessibility "
                              "and Automation → Messages permission, then retry.")
            else:
                self._queue(o, "Your Mac hasn't sent it yet. Trying again in a moment", retry_in=40 * tries)
            return False

        threading.Thread(target=work, daemon=True).start()

    def _via_phone(self, o: dict) -> None:
        rid = o["rid"]
        self._in_flight.add(rid)
        self.store.set_status(rid, "sending", "Sending through your iPhone")
        self._changed(o)

        def done(ok, err):
            self._in_flight.discard(rid)
            if ok:
                note = ""
                if o["effect"]:
                    # the iPhone's Bluetooth link carries plain text only
                    self.store.set_effect(rid, "")
                    note = "Sent without the effect (your iPhone can't send effects over Bluetooth)"
                self._mac_tries.pop(rid, None)
                self.store.set_status(rid, "sent", note, via="iphone")
                self._changed(o)
            elif _read_json(BB_CREDS, {}).get("url"):
                self._queue(o, "Your iPhone didn't take it. Sends when BlueBubbles connects")
            else:
                self._fail(o, "Your iPhone didn't accept it")
        self.phone.send(o["address"], o["text"], done)

    def _flush(self) -> bool:
        """Send whatever is waiting and now has a way to go (and is due, if it's a retry)."""
        now = time.time()
        for (rid,) in self.store.db.execute("SELECT id FROM messages WHERE status='queued' ORDER BY ts").fetchall():
            if self._retry_at.get(rid, 0) > now:
                continue
            o = self._outgoing(rid)
            if o and ((self.bb_online and self.client) or self._phone_can_take(o)):
                self._dispatch(rid)
        return True

    def retry(self, rid: int) -> None:
        m = self.store.message(rid)
        if not m or not m["fromMe"] or m["status"] not in ("failed", "queued"):
            return
        self._mac_tries.pop(rid, None)
        self._dispatch(rid)

    def cancel(self, rid: int) -> None:
        """Take back a message that hasn't gone yet."""
        row = self.store.db.execute("SELECT thread, status, bb_guid, map_handle FROM messages WHERE id=?", (rid,)).fetchone()
        if row is None or row["status"] not in ("queued", "failed") or row["bb_guid"] or row["map_handle"] \
                or rid in self._in_flight:
            return
        self.store.db.execute("DELETE FROM messages WHERE id=?", (rid,))
        self.store.db.commit()
        self._broadcast({"ev": "messages", "thread": row["thread"],
                         "messages": [{"id": rid, "thread": row["thread"], "hidden": True}]})
        self._broadcast_threads()

    # ------------------------------------------------------------ picking files
    def _pick_files(self, c, rid) -> None:
        """The desktop's own file chooser, through xdg-desktop-portal."""
        import random
        try:
            portal = self.ses.get_object("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop")
            fc = dbus.Interface(portal, "org.freedesktop.portal.FileChooser")
        except dbus.DBusException as e:
            self._send(c, {"ev": "picked", "re": rid, "files": [], "error": f"No file chooser: {e}"})
            return
        token = "pearmessages%d" % random.randint(0, 1 << 30)
        sender = self.ses.get_unique_name()[1:].replace(".", "_")
        handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        def response(code, results, _match=[None]):
            if _match[0] is not None:
                _match[0].remove()
            files = []
            if code == 0:
                import urllib.parse
                for u in results.get("uris", []):
                    u = str(u)
                    if u.startswith("file://"):
                        files.append(urllib.parse.unquote(urllib.parse.urlparse(u).path))
            self._send(c, {"ev": "picked", "re": rid, "files": [self._file_info(f) for f in files]})

        match = self.ses.add_signal_receiver(response, "Response", "org.freedesktop.portal.Request", path=handle)
        response.__defaults__[0][0] = match
        fc.OpenFile("", "Attach to message", {"multiple": dbus.Boolean(True), "handle_token": token},
                    reply_handler=lambda h: None,
                    error_handler=lambda e: self._send(c, {"ev": "picked", "re": rid, "files": [], "error": str(e)}))

    def _file_info(self, path: str) -> dict:
        import mimetypes
        return {"path": path, "name": os.path.basename(path), "mime": mimetypes.guess_type(path)[0] or "",
                "size": os.path.getsize(path) if os.path.isfile(path) else 0}

    def _clipboard_image(self, c, rid) -> None:
        """An image on the clipboard (a screenshot, say) saved to a file so it can be attached."""
        def work():
            info = None
            try:
                types = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True, timeout=3).stdout.split()
                kind = next((t for t in ("image/png", "image/jpeg", "image/gif", "image/webp") if t in types), "")
                if kind:
                    d = os.path.join(RUNTIME, "outgoing")
                    os.makedirs(d, mode=0o700, exist_ok=True)
                    path = os.path.join(d, time.strftime("Pasted image %Y-%m-%d at %H.%M.%S.") + kind.split("/")[1].replace("jpeg", "jpg"))
                    with open(path, "wb") as f:
                        f.write(subprocess.run(["wl-paste", "--type", kind], capture_output=True, timeout=10).stdout)
                    info = self._file_info(path)
            except (OSError, subprocess.SubprocessError):
                info = None
            GLib.idle_add(lambda: (self._send(c, {"ev": "clipboard_image", "re": rid, "file": info}), False)[1])
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------- reactions / effects
    def abilities(self) -> dict:
        """What can be sent beyond text right now, and if nothing, why - in words for the UI."""
        creds = _read_json(BB_CREDS, {})
        # Attachments go through BlueBubbles' ordinary send (AppleScript is enough); the
        # iPhone's Bluetooth link carries plain SMS text only.
        bb_set_up = bool(creds.get("url")) and self.settings["bluebubbles"]["enabled"]
        attach = {"attachments": self.bb_online or bb_set_up,
                  "attachNote": "" if self.bb_online else "BlueBubbles is offline. Files will send when it connects.",
                  "attachReason": "" if (self.bb_online or bb_set_up) else (
            "Attachments need BlueBubbles. Over Bluetooth your iPhone only sends text."
            + (" BlueBubbles is offline right now." if creds.get("url") and self.settings["bluebubbles"]["enabled"] else "")
            if self.phone_state.get("state") == "online" else "Attachments need BlueBubbles, and it isn't connected.")}
        if self.bb_online and self.bb_info.get("private_api"):
            return {"reactions": True, "effects": True, "reason": "", **attach}
        if self.bb_online:
            reason = ("Reactions and effects need BlueBubbles' Private API, which is off on your Mac. "
                      "Turn it on in BlueBubbles Server → Settings → Private API (it needs System "
                      "Integrity Protection turned off on the Mac).")
        elif self.phone_state.get("state") == "online":
            reason = ("Reactions and effects need BlueBubbles. You're connected through your iPhone "
                      "over Bluetooth, which only carries plain text.")
            if creds.get("url") and self.settings["bluebubbles"]["enabled"]:
                reason += " BlueBubbles is offline right now."
        else:
            reason = "Not connected."
        return {"reactions": False, "effects": False, "reason": reason, **attach}

    def react(self, c, rid, mid: int, kind: str) -> None:
        m = self.store.message(mid)
        ab = self.abilities()
        if not m:
            return
        err = ""
        if not ab["reactions"]:
            err = ab["reason"]
        elif not m["guid"]:
            err = ("This message only came through your iPhone, so the Mac doesn't know it yet. "
                   "Try again in a moment.")
        t = self.store.thread(m["thread"]) or {}
        if not err and not t.get("bbChat"):
            err = "BlueBubbles doesn't know this conversation yet."
        if err:
            self._send(c, {"ev": "error", "re": rid, "error": err})
            return
        before = (m["reactions"] or {}).get("me", "")
        new = "" if kind == before else kind
        wire = new or ("-" + before)
        self.store.react(m["guid"], "me", new)
        self._after_ingest({m["thread"]: [mid]}, [])
        client = self.client

        def work():
            try:
                if before and new:           # swap: take the old one back first
                    client.react(t["bbChat"], m["guid"], m["text"], "-" + before)
                client.react(t["bbChat"], m["guid"], m["text"], wire)
            except BB.BBError as e:
                def undo(e=e):
                    self.store.react(m["guid"], "me", before)
                    self._after_ingest({m["thread"]: [mid]}, [])
                    self._send(c, {"ev": "error", "re": rid, "error": f"Couldn't react: {e}"})
                    return False
                GLib.idle_add(undo)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ notifications
    def _notify(self, rid: int) -> None:
        if not self.settings.get("notifications", True):
            return
        m = self.store.message(rid)
        if not m or m["fromMe"]:
            return
        if any(c.active and c.viewing == m["thread"] for c in self.conns):
            return
        t = self.store.thread(m["thread"]) or {}
        title = t.get("title") or m["senderName"] or m["sender"]
        if t.get("group") and m["senderName"]:
            title = f"{m['senderName']} in {title}"
        body = m["text"] or ("Attachment" if m["attachments"] else "")
        if not self.settings.get("notificationPreview", True):
            body = "New message"
        if not shutil.which("notify-send"):
            return
        icon = os.path.join(DATA, "app", "icon.svg")
        launcher = os.path.join(DATA, "app", "launch.sh")
        # A click opens this conversation. Omarchy's notifications (and Omapager) run the
        # omarchy-exec-argv hint straight away, and it still works for a card restored after a
        # shell restart; other notification daemons use the "default" action instead.
        args = ["notify-send", "-a", "Pear Messages", "-i", icon, "--action=default=Open",
                "-h", "string:omarchy-exec-argv:" + json.dumps([launcher, m["thread"]]),
                "-h", "string:x-canonical-private-synchronous:pear-" + m["thread"], title, body]

        def run():
            try:
                out = subprocess.run(args, capture_output=True, text=True, timeout=3600).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                return
            if out == "default":
                GLib.idle_add(lambda: (self.open_app(m["thread"]), False)[1])
        threading.Thread(target=run, daemon=True).start()

    def open_app(self, thread: str = "") -> None:
        if self.conns:
            self._broadcast({"ev": "open", "thread": thread})
            subprocess.Popen(["hyprctl", "dispatch", "focuswindow", "title:^Pear Messages$"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        launcher = os.path.join(DATA, "app", "launch.sh")
        subprocess.Popen([launcher] + ([thread] if thread else []), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)

    # ---------------------------------------------------------------- pairing
    def _paired(self, p: dict) -> None:
        self.settings["iphone"]["address"] = p["address"]
        self.settings["iphone"]["enabled"] = True
        _write_json(SETTINGS, self.settings, 0o644)
        self.apply_settings()

    # ----------------------------------------------------------------- status
    def status(self) -> dict:
        creds = _read_json(BB_CREDS, {})
        phones = []
        try:
            phones = IP.list_phones(self.sys)
        except dbus.DBusException:
            pass
        addr = self.settings["iphone"]["address"]
        name = next((p["name"] for p in phones if p["address"] == addr), "")
        route = "bluebubbles" if self.bb_online else "iphone" if self.phone_state.get("state") == "online" else "none"
        return {
            "ev": "status",
            "route": route,
            "bluebubbles": {
                **self.bb_state, "configured": bool(creds.get("url") and creds.get("password")),
                "enabled": self.settings["bluebubbles"]["enabled"], "url": creds.get("url", ""),
                "link": self.bb_link,
                "privateApi": bool(self.bb_info.get("private_api")),
                "serverVersion": self.bb_info.get("server_version", ""),
                "macOS": self.bb_info.get("os_version", ""),
            },
            "iphone": {
                **self.phone_state, "address": addr, "name": name,
                "enabled": self.settings["iphone"]["enabled"],
                "keepAudio": self.settings["iphone"].get("keepAudio", True),
                "paired": phones,
            },
            "settings": {k: self.settings[k] for k in ("notifications", "notificationPreview", "linkPreviews", "typingAnimation")},
            "abilities": self.abilities(),
            "pairing": self.pairing.active,
        }

    # ------------------------------------------------------------------ socket
    def serve(self) -> None:
        os.makedirs(RUNTIME, mode=0o700, exist_ok=True)
        os.chmod(RUNTIME, 0o700)
        try:
            os.unlink(SOCK)
        except FileNotFoundError:
            pass
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(SOCK)
        os.chmod(SOCK, 0o600)
        self.srv.listen(8)
        self.srv.setblocking(False)
        GLib.io_add_watch(self.srv.fileno(), GLib.IO_IN, self._accept)

    def _accept(self, fd, cond):
        try:
            s, _ = self.srv.accept()
        except BlockingIOError:
            return True
        s.setblocking(False)
        c = Conn(s)
        self.conns.append(c)
        c.watch = GLib.io_add_watch(s.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._readable, c)
        return True

    def _drop(self, c: Conn) -> None:
        if c in self.conns:
            self.conns.remove(c)
        try:
            c.sock.close()
        except OSError:
            pass

    def _readable(self, fd, cond, c: Conn):
        try:
            data = c.sock.recv(65536)
        except BlockingIOError:
            return True
        except OSError:
            data = b""
        if not data:
            self._drop(c)
            return False
        c.buf += data
        while b"\n" in c.buf:
            line, c.buf = c.buf.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                req = json.loads(line)
            except ValueError:
                continue
            try:
                self._handle(c, req)
            except Exception as e:  # a bad request must never take the daemon down
                log(f"request {req.get('op')!r} failed: {type(e).__name__}: {e}")
                self._send(c, {"ev": "error", "re": req.get("id"), "error": str(e)})
        return True

    def _send(self, c: Conn, obj: dict) -> None:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode()
        try:
            c.sock.setblocking(True)
            c.sock.sendall(data)
            c.sock.setblocking(False)
        except OSError:
            self._drop(c)

    def _broadcast(self, obj: dict) -> None:
        for c in list(self.conns):
            self._send(c, obj)

    def _broadcast_status(self) -> None:
        self._broadcast(self.status())

    def _broadcast_threads(self) -> None:
        if self.conns:
            self._broadcast({"ev": "threads", "threads": self.store.threads()})

    def _handle(self, c: Conn, req: dict) -> None:
        op = req.get("op")
        rid = req.get("id")
        reply = lambda obj: self._send(c, {**obj, "re": rid})

        if op == "hello":
            reply(self.status())
            self._send(c, {"ev": "threads", "threads": self.store.threads()})
        elif op == "status":
            reply(self.status())
        elif op == "threads":
            reply({"ev": "threads", "threads": self.store.threads()})
        elif op == "view":
            c.viewing = req.get("thread", "")
            # Read means you looked: the window has focus and this conversation is on screen.
            # Anything missing counts as not looking.
            c.active = req.get("active") is True
            if c.viewing and c.active:
                self._mark_read(c.viewing)
        elif op == "open":
            tid = req["thread"]
            c.viewing = tid
            reply({"ev": "thread", "thread": tid, "info": self.store.thread(tid),
                   "messages": self.store.messages(tid, int(req.get("limit", 100)))})
            # Opening is not reading: the window opens conversations on its own (at start, on
            # reconnect, from a notification). Only a focused view marks read.
            if c.active and c.viewing == tid:
                self._mark_read(tid)
        elif op == "older":
            self._older(c, rid, req["thread"], float(req["before"]), int(req.get("count", 100)))
        elif op == "send":
            reply({"ev": "sent", **self.send(req.get("thread", ""), req.get("text", ""), req.get("to", ""),
                                             req.get("effect", ""), req.get("files") or [])})
        elif op == "link_preview":
            self._link_preview(c, req.get("url", ""))
        elif op == "pick_files":
            self._pick_files(c, rid)
        elif op == "clipboard_image":
            self._clipboard_image(c, rid)
        elif op == "file_info":
            reply({"ev": "file_info", "files": [self._file_info(f) for f in req.get("files", []) if os.path.isfile(f)]})
        elif op == "react":
            self.react(c, rid, int(req["message"]), req.get("reaction", ""))
        elif op == "retry":
            self.retry(int(req["message"]))
        elif op == "cancel":
            self.cancel(int(req["message"]))
        elif op == "search":
            reply({"ev": "search", "q": req.get("q", ""), "results": self.store.search_contacts(req.get("q", ""))})
        elif op == "attachment":
            self._attachment(c, rid, req["guid"], req.get("name", ""), req.get("mime", ""),
                             None if req.get("force") else bool(req.get("temp")),
                             req.get("kind", "preview"), req.get("size", 0))
        elif op == "preview":
            self._preview(c, rid, req.get("path", ""))
        # ---- settings / connection assistant
        elif op == "settings":
            for k in ("notifications", "notificationPreview", "linkPreviews", "typingAnimation"):
                if k in req:
                    self.settings[k] = bool(req[k])
            for group in ("iphone", "bluebubbles"):
                for k, v in (req.get(group) or {}).items():
                    if k in DEFAULTS[group]:
                        self.settings[group][k] = v
            _write_json(SETTINGS, self.settings, 0o644)
            self.apply_settings()
        elif op == "bb_test":
            self._bb_test(c, rid, req.get("url", ""), req.get("password"), bool(req.get("save")))
        elif op == "bb_find":
            self._bb_find(c, rid)
        elif op == "bb_forget":
            try:
                os.unlink(BB_CREDS)
            except FileNotFoundError:
                pass
            self.apply_settings()
        elif op == "pair_start":
            self.pairing.start(int(req.get("seconds", 180)))
        elif op == "pair_answer":
            self.pairing.answer(bool(req.get("accept")))
        elif op == "pair_stop":
            self.pairing.stop()
        elif op == "iphone_use":
            self.settings["iphone"]["address"] = req.get("address", "")
            _write_json(SETTINGS, self.settings, 0o644)
            self.apply_settings()
        elif op == "iphone_reconnect":
            self.phone.close()
            self.phone.address = ""
            self.apply_settings()
        elif op == "open_app":
            self.open_app(req.get("thread", ""))
        elif op == "sync":
            if self.poller:
                self.poller.wake.set()
            self.phone.sync()
        else:
            reply({"ev": "error", "error": f"unknown op {op!r}"})

    def _older(self, c, rid, tid: str, before: float, count: int) -> None:
        """Earlier messages for a conversation: what's stored here first; when that runs out,
        ask the Mac (BlueBubbles) for the next ones. The iPhone keeps no history over Bluetooth."""
        local = self.store.messages(tid, count, before)
        if len(local) >= count:
            self._send(c, {"ev": "older", "re": rid, "thread": tid, "messages": local, "more": True})
            return
        t = self.store.thread(tid) or {}
        if not (self.bb_online and self.client):
            self._send(c, {"ev": "older", "re": rid, "thread": tid, "messages": local, "more": False,
                           "note": "" if local else "That's everything stored here. Earlier messages come from your Mac, through BlueBubbles, which isn't connected."})
            return
        # every chat on the Mac that belongs to this conversation
        guids = [t.get("bbChat")] if t.get("bbChat") else []
        if not t.get("group"):
            for a in t.get("participants") or []:
                guids += [f"iMessage;-;{a}", f"SMS;-;{a}", f"RCS;-;{a}"]
        guids = list(dict.fromkeys(g for g in guids if g))
        oldest = local[0]["ts"] if local else before
        client = self.client

        def work():
            got = []
            for g in guids:
                try:
                    got += client.chat_messages(g, limit=count, before_ms=int(oldest * 1000))
                except BB.BBError:
                    pass
            GLib.idle_add(done, got)

        def done(got):
            if got:
                self._ingest_bb(list(reversed(got)))
            msgs = self.store.messages(tid, count, before)
            self._send(c, {"ev": "older", "re": rid, "thread": tid, "messages": msgs,
                           "more": bool(got) or len(msgs) >= count,
                           "note": "" if msgs else "No earlier messages."})
            return False

        threading.Thread(target=work, daemon=True).start()

    def _link_preview(self, c, url: str) -> None:
        """A link's title/description, from the cache or fetched once (whoever else asks for the
        same link while it's being fetched gets the same answer)."""
        if not url or not self.settings.get("linkPreviews", True):
            return
        cached = self.store.link_preview(url)
        if cached is not None:
            self._send(c, {"ev": "link_preview", **cached})
            return
        waiting = self._link_waiting.setdefault(url, [])
        waiting.append(c)
        if len(waiting) > 1:
            return

        def work():
            try:
                p = linkpreview.fetch(url)
            except Exception:
                p = None
            GLib.idle_add(done, p)

        def done(p):
            self.store.save_link_preview(url, p)
            ev = {"ev": "link_preview", "url": url, "ok": bool(p), **(p or {})}
            for conn in self._link_waiting.pop(url, []):
                if conn in self.conns:
                    self._send(conn, ev)
            return False
        threading.Thread(target=work, daemon=True).start()

    def _mark_read(self, tid: str) -> None:
        if self.store.mark_read(tid):
            self._broadcast_threads()
            t = self.store.thread(tid)
            if t and t.get("bbChat") and self.client and self.bb_info.get("private_api"):
                client = self.client
                threading.Thread(target=lambda: _quiet(client.mark_read, t["bbChat"]), daemon=True).start()

    def _bb_test(self, c, rid, url, password, save) -> None:
        """Checks a server (Tailscale-only), and saves it if asked. The password never
        leaves this process except to the server itself."""
        creds = _read_json(BB_CREDS, {})
        if password is None or password == "":
            password = creds.get("password", "") if (url or "").rstrip("/") == creds.get("url", "").rstrip("/") else ""

        def work():
            client = BB.Client(url, password)
            try:
                if not password:
                    raise BB.BBError("Enter the server password")
                info = client.info()
                result = {"ok": True, "serverVersion": info.get("server_version", ""),
                          "macOS": info.get("os_version", ""), "privateApi": bool(info.get("private_api")),
                          "url": client.url, "link": tailnet.describe(client.url)}
                if save:
                    _write_json(BB_CREDS, {"url": client.url, "password": password})
                    self.settings["bluebubbles"]["enabled"] = True
                    _write_json(SETTINGS, self.settings, 0o644)
            except BB.BBError as e:
                result = {"ok": False, "error": str(e)}
            GLib.idle_add(lambda: (self._send(c, {"ev": "bb_test", "re": rid, **result}),
                                   save and result["ok"] and self.apply_settings(), False)[2])
        threading.Thread(target=work, daemon=True).start()

    def _bb_find(self, c, rid) -> None:
        def work():
            found = []
            for p in tailnet.peers():
                if tailnet.probe_bluebubbles(p["ip"]):
                    found.append({**p, "url": f"http://{p['ip']}:1234"})
            GLib.idle_add(lambda: (self._send(c, {"ev": "bb_found", "re": rid, "servers": found}), False)[1])
        threading.Thread(target=work, daemon=True).start()

    def _attachment(self, c, rid, guid, name, mime="", temp=False, kind="preview", size=0) -> None:
        """A photo/video preview (kind "preview") or the original file (kind "full"), from the
        Mac, on demand. Previews of the newest photos in a conversation are kept (temp=False);
        everything else, and every original, goes to the temporary folder (memory-backed,
        cleared at logout). See media.py."""
        safe = "".join(ch for ch in (name or "file") if ch.isalnum() or ch in "._- ")[:80] or "file"
        is_video = (mime or "").startswith("video/") or safe.lower().endswith(
            (".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm", ".3gp"))
        is_image = (mime or "").startswith("image/")
        keep_dir, temp_dir = os.path.join(CACHE, "previews"), os.path.join(RUNTIME, "previews")
        if kind == "full":
            fname, dirs = f"{guid[:16]}-{safe}", (os.path.join(RUNTIME, "attachments"),)
            keep_dir = temp_dir = dirs[0]
        else:
            fname, dirs = f"{guid[:16]}-preview.jpg", (keep_dir, temp_dir)
        for d in dirs + (os.path.join(CACHE, "attachments"),) if kind == "full" else dirs:
            if os.path.exists(os.path.join(d, fname)):
                self._send(c, {"ev": "attachment", "re": rid, "guid": guid, "kind": kind, "path": os.path.join(d, fname)})
                return
        reply = lambda res: self._send(c, {"ev": "attachment", "re": rid, "guid": guid, "kind": kind, **res})
        if kind == "preview" and not (is_image or is_video):
            reply({"error": "no preview"})
            return
        client = self.client
        if not client or not self.bb_online:
            reply({"error": "Pictures come from your Mac through BlueBubbles, which isn't connected"})
            return
        d = temp_dir if (temp or kind == "full") else keep_dir
        os.makedirs(d, mode=0o700, exist_ok=True)
        path = os.path.join(d, fname)

        def work():
            try:
                if kind == "full":
                    res = {"path": media.original(client, guid, path)}
                elif is_video:
                    scratch = os.path.join(RUNTIME, "previews")
                    os.makedirs(scratch, mode=0o700, exist_ok=True)
                    res = {"path": media.video_thumb(client, guid, int(size or 0), path, scratch,
                                                     allow_big=bool(temp is None))}
                else:
                    res = {"path": media.photo_preview(client, guid, path)}
            except media.MediaError as e:
                res = {"error": str(e), "big": str(e) == "big"}
            except (OSError, subprocess.SubprocessError) as e:
                res = {"error": str(e)}
            GLib.idle_add(lambda: (reply(res), False)[1])
        threading.Thread(target=work, daemon=True).start()

    def _preview(self, c, rid, path: str) -> None:
        """Quick Look: GNOME's previewer (Sushi) when it's there. Calling it again on the same
        file closes it, which is what a second press of Space should do. Without Sushi the app
        shows its own preview."""
        if not path or not os.path.exists(path):
            return
        if self._has_previewer():
            import urllib.parse
            uri = "file://" + urllib.parse.quote(path)
            try:
                prev = self.ses.get_object("org.gnome.NautilusPreviewer", "/org/gnome/NautilusPreviewer")
                dbus.Interface(prev, "org.gnome.NautilusPreviewer2").ShowFile(
                    uri, "", True, "", reply_handler=lambda: None,
                    error_handler=lambda e: self._send(c, {"ev": "preview", "re": rid, "path": path, "builtin": True}))
                return
            except dbus.DBusException:
                pass
        self._send(c, {"ev": "preview", "re": rid, "path": path, "builtin": True})

    def _has_previewer(self) -> bool:
        try:
            d = dbus.Interface(self.ses.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus"),
                               "org.freedesktop.DBus")
            return "org.gnome.NautilusPreviewer" in (list(d.ListActivatableNames()) + list(d.ListNames()))
        except dbus.DBusException:
            return False

    # -------------------------------------------------------------------- run
    def run(self) -> None:
        n = self.store.merge_orphans()
        if n:
            log(f"merged {n} duplicate message(s) seen through both connections")
        fixed = self.store.cleanup_reaction_texts()
        if self.store.removed_reaction_texts:
            log(f"turned {self.store.removed_reaction_texts} reaction text(s) back into reactions")
        self.store.learn_self()
        n = self.store.rethread()
        if n:
            log(f"merged {n} conversation(s) into their person's thread")
        # A send in flight when the daemon stopped never finished: it waits and goes again as
        # soon as a connection can carry it.
        stuck = self.store.db.execute(
            "UPDATE messages SET status='queued', error='Interrupted. Sending again' "
            "WHERE status='sending'").rowcount
        self.store.db.commit()
        if stuck:
            log(f"{stuck} unfinished send(s) queued again")
        GLib.timeout_add_seconds(15, self._flush)
        self.serve()
        self.apply_settings()
        # Transfers finishing (sends, contact pulls) are reported by the phone module; this
        # routes obex transfer completions that are not message bodies.
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGHUP, self._reload)
        log(f"listening on {SOCK}")
        self.loop.run()

    def _reload(self):
        self.settings = load_settings()
        self.apply_settings()
        return True

    def _quit(self):
        log("stopping")
        if self.pairing.active:
            self.pairing.stop()
        if self.poller:
            self.poller.stop()
        self.phone.close()
        self.loop.quit()
        return False


def _sent_meanwhile(client, text: str, since_ms: int) -> dict | None:
    """The message BlueBubbles sent after all, if it did (checked for up to ~10 s)."""
    from .store import normalize
    want = normalize(text)
    for _ in range(4):
        try:
            for m in client.messages_after(since_ms - 5000, limit=50):
                if m.get("isFromMe") and normalize(m.get("text") or "") == want:
                    return m
        except BB.BBError:
            pass
        time.sleep(2.5)
    return None


def _quiet(fn, *a):
    try:
        fn(*a)
    except Exception:
        pass


def main() -> None:
    Daemon().run()


if __name__ == "__main__":
    main()
