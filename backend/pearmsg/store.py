"""The local message history, merged from every connection.

Two connections can report the same message: BlueBubbles (from the Mac's Messages
database) and the iPhone over Bluetooth (MAP). Neither shares an id with the other, so a
message is recognised by what it is - who sent it, which way it went, its text, and when -
and each connection's own id is attached to the one row:

    bb_guid     BlueBubbles' message guid
    map_handle  the iPhone's MAP handle

`ingest()` is the only way in. It first looks for the connection's own id (an update of a
known message), then for the same message from the other connection (a merge), and only
then inserts. BlueBubbles' copy wins on the details it knows better: the exact time, the
group chat a message belongs to, attachments.

Threads: a one-to-one conversation is keyed by the other person's address ("addr:<key>"),
so an iMessage chat and an SMS chat with the same person, and the iPhone's copy of either,
are one thread. A group chat only BlueBubbles knows about is keyed by its chat guid.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import unicodedata

from . import contacts as C

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY,
    thread      TEXT NOT NULL,
    from_me     INTEGER NOT NULL,
    sender      TEXT NOT NULL,          -- address key, or 'me'
    sender_addr TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL DEFAULT '',
    norm        TEXT NOT NULL DEFAULT '',
    ts          REAL NOT NULL,
    status      TEXT NOT NULL DEFAULT '',   -- sending sent delivered read failed ('' = received)
    error       TEXT NOT NULL DEFAULT '',
    via         TEXT NOT NULL DEFAULT '',   -- bluebubbles | iphone | both | local
    bb_guid     TEXT UNIQUE,
    map_handle  TEXT UNIQUE,
    temp_id     TEXT UNIQUE,
    attachments TEXT NOT NULL DEFAULT '[]',
    reactions   TEXT NOT NULL DEFAULT '{}',
    unread      INTEGER NOT NULL DEFAULT 0,
    effect      TEXT NOT NULL DEFAULT ''     -- iMessage bubble/screen effect id
);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread, ts);
CREATE INDEX IF NOT EXISTS messages_match ON messages(sender, from_me, ts);

CREATE TABLE IF NOT EXISTS threads (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '',
    participants TEXT NOT NULL DEFAULT '[]',   -- addresses
    is_group     INTEGER NOT NULL DEFAULT 0,
    bb_chat      TEXT NOT NULL DEFAULT '',     -- BlueBubbles chat guid, when known
    service      TEXT NOT NULL DEFAULT ''      -- iMessage | SMS
);

CREATE TABLE IF NOT EXISTS contacts (
    key    TEXT PRIMARY KEY,
    name   TEXT NOT NULL,
    addr   TEXT NOT NULL,
    source TEXT NOT NULL
);

-- Reactions whose message has not arrived yet.
CREATE TABLE IF NOT EXISTS pending_reactions (
    target TEXT NOT NULL, sender TEXT NOT NULL, kind TEXT NOT NULL,
    PRIMARY KEY (target, sender)
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""

# Two copies of one message must land within this many seconds of each other. The iPhone
# stamps MAP messages to the second in local time; BlueBubbles to the millisecond. A
# message can also reach the Mac late if it was asleep, which is what the margin is for.
MATCH_WINDOW = 180.0
# Without text (a photo on its own) there is less to go on, so the window is tighter.
MATCH_WINDOW_EMPTY = 45.0
# A truncated copy must still carry at least this much of the text to be recognised.
PREFIX_MIN = 40
# The two connections can name one person differently: the iPhone may report an Apple Account
# email where the Mac reports a phone number. The same text from "two people" this close
# together is the same message.
CROSS_HANDLE_WINDOW = 20.0

_OBJ = "￼"  # what Messages puts in the text where an attachment sits
_TAPBACK_PREFIX = re.compile(
    r"^(Loved|Liked|Disliked|Laughed at|Emphasized|Questioned|Reacted \S+ to)\s+[“\"]", re.I)


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").replace(_OBJ, "")
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip().lower()


def addr_thread(addr: str) -> str:
    return "addr:" + C.key(addr)


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.execute("PRAGMA journal_mode=WAL")
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(messages)")}
        if "effect" not in cols:   # databases from 1.0.0
            self.db.execute("ALTER TABLE messages ADD COLUMN effect TEXT NOT NULL DEFAULT ''")
            # re-read recent history from BlueBubbles once, to fill in effects already sent
            self.db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('bb_since_ms','0')")
            self.db.commit()
        os.chmod(path, 0o600)

    # ------------------------------------------------------------------ meta
    def get_meta(self, k: str, default: str = "") -> str:
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set_meta(self, k: str, v: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, v))
        self.db.commit()

    # -------------------------------------------------------------- contacts
    def set_contacts(self, cards: list[dict], source: str) -> int:
        """Replace one source's contacts. Returns how many addresses were stored."""
        self.db.execute("DELETE FROM contacts WHERE source=?", (source,))
        n = 0
        for c in cards:
            if not c.get("name"):
                continue
            for a in list(c.get("tels", [])) + list(c.get("emails", [])):
                k = C.key(a)
                if not k:
                    continue
                self.db.execute(
                    "INSERT OR IGNORE INTO contacts(key,name,addr,source) VALUES(?,?,?,?)",
                    (k, c["name"], a, source))
                n += 1
        self.db.commit()
        return n

    def contact_name(self, addr: str) -> str:
        r = self.db.execute("SELECT name FROM contacts WHERE key=?", (C.key(addr),)).fetchone()
        return r["name"] if r else ""

    def search_contacts(self, q: str, limit: int = 12) -> list[dict]:
        q = (q or "").strip()
        if not q:
            return []
        like = f"%{q}%"
        d = C.digits(q)
        rows = self.db.execute(
            "SELECT name, addr FROM contacts WHERE name LIKE ? OR addr LIKE ? "
            + ("OR key LIKE ? " if len(d) >= 3 else "")
            + "ORDER BY name LIMIT ?",
            (like, like, f"%{d}%", limit) if len(d) >= 3 else (like, like, limit)).fetchall()
        return [{"name": r["name"], "addr": r["addr"]} for r in rows]

    # --------------------------------------------------------------- threads
    def ensure_thread(self, tid: str, *, name: str = "", participants: list[str] | None = None,
                      is_group: bool = False, bb_chat: str = "", service: str = "") -> None:
        row = self.db.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()
        if row is None:
            self.db.execute(
                "INSERT INTO threads(id,name,participants,is_group,bb_chat,service) VALUES(?,?,?,?,?,?)",
                (tid, name, json.dumps(participants or []), int(is_group), bb_chat, service))
            return
        upd = {}
        if name and name != row["name"]:
            upd["name"] = name
        if participants and json.loads(row["participants"]) != participants:
            upd["participants"] = json.dumps(participants)
        # Prefer the iMessage chat as the one to send into; keep an SMS one only as a fallback.
        if bb_chat and (not row["bb_chat"] or (service == "iMessage" and row["service"] != "iMessage")):
            upd["bb_chat"] = bb_chat
            upd["service"] = service
        if is_group and not row["is_group"]:
            upd["is_group"] = 1
        if upd:
            self.db.execute(
                "UPDATE threads SET " + ",".join(f"{k}=?" for k in upd) + " WHERE id=?",
                (*upd.values(), tid))

    def thread(self, tid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()
        return self._thread_dict(r) if r else None

    def _thread_title(self, r: sqlite3.Row) -> str:
        if r["name"]:
            return r["name"]
        parts = json.loads(r["participants"])
        if not parts and r["id"].startswith("addr:"):
            last = self.db.execute(
                "SELECT sender_addr FROM messages WHERE thread=? AND sender_addr!='' ORDER BY ts DESC LIMIT 1",
                (r["id"],)).fetchone()
            parts = [last["sender_addr"]] if last else [r["id"][5:]]
        names = [self.contact_name(p) or p for p in parts]
        if len(names) > 1:
            return ", ".join(n.split(" ")[0] for n in names)
        return names[0] if names else r["id"]

    def _thread_dict(self, r: sqlite3.Row) -> dict:
        last = self.db.execute(
            "SELECT text, ts, from_me, attachments FROM messages WHERE thread=? ORDER BY ts DESC LIMIT 1",
            (r["id"],)).fetchone()
        unread = self.db.execute(
            "SELECT COUNT(*) FROM messages WHERE thread=? AND unread=1", (r["id"],)).fetchone()[0]
        parts = json.loads(r["participants"])
        preview = ""
        if last:
            preview = last["text"].replace(_OBJ, "").strip()
            if not preview and json.loads(last["attachments"]):
                preview = "Attachment"
        return {
            "id": r["id"],
            "title": self._thread_title(r),
            "participants": parts,
            "address": parts[0] if len(parts) == 1 else (r["id"][5:] if r["id"].startswith("addr:") and not parts else ""),
            "group": bool(r["is_group"]),
            "service": r["service"],
            "bbChat": r["bb_chat"],
            "preview": preview,
            "fromMe": bool(last["from_me"]) if last else False,
            "ts": last["ts"] if last else 0,
            "unread": unread,
        }

    def threads(self, limit: int = 300) -> list[dict]:
        rows = self.db.execute(
            "SELECT t.* FROM threads t JOIN (SELECT thread, MAX(ts) m FROM messages GROUP BY thread) x "
            "ON x.thread=t.id ORDER BY x.m DESC LIMIT ?", (limit,)).fetchall()
        return [self._thread_dict(r) for r in rows]

    def mark_read(self, tid: str) -> int:
        n = self.db.execute("UPDATE messages SET unread=0 WHERE thread=? AND unread=1", (tid,)).rowcount
        self.db.commit()
        return n

    # -------------------------------------------------------------- messages
    def _row(self, r: sqlite3.Row) -> dict:
        return {
            "id": r["id"], "thread": r["thread"], "fromMe": bool(r["from_me"]),
            "sender": r["sender_addr"], "senderName": ("" if r["from_me"] else self.contact_name(r["sender_addr"])),
            "text": r["text"].replace(_OBJ, "").strip(), "ts": r["ts"], "status": r["status"],
            "error": r["error"], "via": r["via"], "attachments": json.loads(r["attachments"]),
            "reactions": json.loads(r["reactions"]), "unread": bool(r["unread"]),
            "effect": r["effect"], "guid": r["bb_guid"] or "",
        }

    def message(self, mid: int) -> dict | None:
        r = self.db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        return self._row(r) if r else None

    def messages(self, tid: str, limit: int = 300, before: float | None = None) -> list[dict]:
        if before is None:
            rows = self.db.execute(
                "SELECT * FROM messages WHERE thread=? ORDER BY ts DESC LIMIT ?", (tid, limit)).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM messages WHERE thread=? AND ts<? ORDER BY ts DESC LIMIT ?",
                (tid, before, limit)).fetchall()
        return [self._row(r) for r in reversed(rows)]

    def _find_twin(self, *, from_me: bool, sender: str, norm: str, ts: float, id_col: str) -> sqlite3.Row | None:
        win = MATCH_WINDOW if norm else MATCH_WINDOW_EMPTY
        base = (f"SELECT * FROM messages WHERE from_me=? AND {id_col} IS NULL "
                "AND ts BETWEEN ? AND ? " + ("" if from_me else "AND sender=? "))
        args = (int(from_me), ts - win, ts + win) + (() if from_me else (sender,))
        r = self.db.execute(base + "AND norm=? ORDER BY ABS(ts-?) LIMIT 1", args + (norm, ts)).fetchone()
        if r is not None:
            return r
        if len(norm) < PREFIX_MIN:
            return self._cross_handle_twin(from_me, sender, norm, ts, id_col)
        # The iPhone cuts a long text short in some places (its listing stops at 128
        # characters). A text that is the start of the other copy is the same message.
        for c in self.db.execute(base + "ORDER BY ABS(ts-?) LIMIT 20", args + (ts,)).fetchall():
            a, b = c["norm"], norm
            if len(a) >= PREFIX_MIN and (a.startswith(b) or b.startswith(a)):
                return c
        return self._cross_handle_twin(from_me, sender, norm, ts, id_col)

    def _cross_handle_twin(self, from_me, sender, norm, ts, id_col):
        if from_me or not norm:
            return None
        return self.db.execute(
            f"SELECT * FROM messages WHERE from_me=0 AND {id_col} IS NULL AND norm=? AND sender!=? "
            "AND ts BETWEEN ? AND ? ORDER BY ABS(ts-?) LIMIT 1",
            (norm, sender, ts - CROSS_HANDLE_WINDOW, ts + CROSS_HANDLE_WINDOW, ts)).fetchone()

    def ingest(self, m: dict, source: str) -> tuple[str, int]:
        """Add or merge one message. Returns (what happened, row id).

        m: thread, from_me, sender_addr, text, ts, and at least one of bb_guid / map_handle /
        temp_id. Optional: status, error, attachments, thread_meta (kwargs for ensure_thread),
        unread.
        """
        id_col = {"bluebubbles": "bb_guid", "iphone": "map_handle", "local": "temp_id"}[source]
        own_id = m.get(id_col)
        from_me = bool(m["from_me"])
        sender = "me" if from_me else C.key(m.get("sender_addr", ""))
        norm = normalize(m.get("text", ""))
        meta = m.get("thread_meta") or {}
        self.ensure_thread(m["thread"], **meta)

        # 1. Already known by this connection's own id: an update (delivered, read, error).
        if own_id:
            r = self.db.execute(f"SELECT * FROM messages WHERE {id_col}=?", (own_id,)).fetchone()
            if r is not None:
                self._update(r, m, source)
                return "updated", r["id"]
        # A send we started carries our temp id through BlueBubbles, so it matches exactly.
        if m.get("temp_id") and source != "local":
            r = self.db.execute("SELECT * FROM messages WHERE temp_id=?", (m["temp_id"],)).fetchone()
            if r is not None:
                self._update(r, m, source, merge_id=(id_col, own_id))
                return "merged", r["id"]

        # 2. The same message from the other connection.
        twin = self._find_twin(from_me=from_me, sender=sender, norm=norm, ts=float(m["ts"]), id_col=id_col)
        if twin is not None:
            self._update(twin, m, source, merge_id=(id_col, own_id))
            return "merged", twin["id"]

        # 3. New.
        cur = self.db.execute(
            "INSERT INTO messages(thread,from_me,sender,sender_addr,text,norm,ts,status,error,via,"
            "bb_guid,map_handle,temp_id,attachments,unread,effect) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (m["thread"], int(from_me), sender, "" if from_me else m.get("sender_addr", ""),
             m.get("text", ""), norm, float(m["ts"]), m.get("status", ""), m.get("error", ""), source,
             m.get("bb_guid"), m.get("map_handle"), m.get("temp_id"),
             json.dumps(m.get("attachments") or []), int(bool(m.get("unread")) and not from_me),
             m.get("effect") or ""))
        rid = cur.lastrowid
        if m.get("bb_guid"):
            self._apply_pending_reactions(rid, m["bb_guid"])
        self.db.commit()
        return "new", rid

    def _update(self, r: sqlite3.Row, m: dict, source: str, merge_id: tuple | None = None) -> None:
        upd: dict = {}
        if merge_id and merge_id[1]:
            upd[merge_id[0]] = merge_id[1]
            if r["via"] not in (source, "both"):
                upd["via"] = "both" if r["via"] in ("bluebubbles", "iphone") else source
        if source == "bluebubbles":
            # BlueBubbles knows the exact time and the real chat (a group, say).
            upd["ts"] = float(m["ts"])
            if m["thread"] != r["thread"] and (m["thread"].startswith("chat:") or r["thread"].startswith("addr:")):
                upd["thread"] = m["thread"]
            if m.get("attachments"):
                upd["attachments"] = json.dumps(m["attachments"])
            if m.get("effect") and m["effect"] != r["effect"]:
                upd["effect"] = m["effect"]
        if m.get("text") and len(m["text"]) > len(r["text"]) and normalize(m["text"]).startswith(r["norm"]):
            upd["text"] = m["text"]
            upd["norm"] = normalize(m["text"])
        for k in ("status", "error"):
            if m.get(k) is not None and m.get(k) != "" and m.get(k) != r[k]:
                # never step a status backwards (read -> delivered)
                order = ["", "sending", "sent", "delivered", "read"]
                if k == "status" and r[k] in order and m[k] in order and order.index(m[k]) < order.index(r[k]):
                    continue
                upd[k] = m[k]
        if m.get("unread") is False and r["unread"]:
            upd["unread"] = 0
        if upd:
            self.db.execute("UPDATE messages SET " + ",".join(f"{k}=?" for k in upd) + " WHERE id=?",
                            (*upd.values(), r["id"]))
            if "bb_guid" in upd:
                self._apply_pending_reactions(r["id"], upd["bb_guid"])
            self.db.commit()

    def merge_orphans(self) -> int:
        """Merge iPhone-only rows into BlueBubbles-only rows that are the same message.
        Returns how many duplicates were removed."""
        n = 0
        for r in self.db.execute("SELECT * FROM messages WHERE map_handle IS NOT NULL AND bb_guid IS NULL").fetchall():
            twin = self._find_twin(from_me=bool(r["from_me"]), sender=r["sender"], norm=r["norm"],
                                   ts=r["ts"], id_col="map_handle")
            if twin is None or twin["id"] == r["id"] or not twin["bb_guid"]:
                continue
            self.db.execute("DELETE FROM messages WHERE id=?", (r["id"],))
            self.db.execute("UPDATE messages SET map_handle=?, via='both', "
                            "unread=CASE WHEN ? THEN unread ELSE 0 END WHERE id=?",
                            (r["map_handle"], r["unread"], twin["id"]))
            n += 1
        self.db.commit()
        return n

    def set_effect(self, mid: int, effect: str) -> None:
        self.db.execute("UPDATE messages SET effect=? WHERE id=?", (effect, mid))
        self.db.commit()

    def set_status(self, mid: int, status: str, error: str = "", via: str | None = None) -> None:
        if via:
            self.db.execute("UPDATE messages SET status=?, error=?, via=? WHERE id=?", (status, error, via, mid))
        else:
            self.db.execute("UPDATE messages SET status=?, error=? WHERE id=?", (status, error, mid))
        self.db.commit()

    # ------------------------------------------------------------- reactions
    def react(self, target_guid: str, sender: str, kind: str) -> int | None:
        """kind '' removes. Returns the message row the reaction landed on."""
        r = self.db.execute("SELECT id, reactions FROM messages WHERE bb_guid=?", (target_guid,)).fetchone()
        if r is None:
            if kind:
                self.db.execute("INSERT OR REPLACE INTO pending_reactions VALUES(?,?,?)", (target_guid, sender, kind))
            else:
                self.db.execute("DELETE FROM pending_reactions WHERE target=? AND sender=?", (target_guid, sender))
            self.db.commit()
            return None
        reactions = json.loads(r["reactions"])
        if kind:
            reactions[sender] = kind
        else:
            reactions.pop(sender, None)
        self.db.execute("UPDATE messages SET reactions=? WHERE id=?", (json.dumps(reactions), r["id"]))
        self.db.commit()
        return r["id"]

    def _apply_pending_reactions(self, rid: int, guid: str) -> None:
        rows = self.db.execute("SELECT sender, kind FROM pending_reactions WHERE target=?", (guid,)).fetchall()
        if not rows:
            return
        r = self.db.execute("SELECT reactions FROM messages WHERE id=?", (rid,)).fetchone()
        reactions = json.loads(r["reactions"])
        for p in rows:
            reactions[p["sender"]] = p["kind"]
        self.db.execute("UPDATE messages SET reactions=? WHERE id=?", (json.dumps(reactions), rid))
        self.db.execute("DELETE FROM pending_reactions WHERE target=?", (guid,))

    def is_tapback_text(self, text: str) -> bool:
        """The iPhone (over MAP) delivers a reaction as a plain message like 'Loved “hi”'."""
        return bool(_TAPBACK_PREFIX.match(text or ""))

    def latest_ts(self, via_col: str) -> float:
        r = self.db.execute(f"SELECT MAX(ts) FROM messages WHERE {via_col} IS NOT NULL").fetchone()
        return r[0] or 0.0


def now() -> float:
    return time.time()
