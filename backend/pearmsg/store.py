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
    effect      TEXT NOT NULL DEFAULT '',    -- iMessage bubble/screen effect id
    runs        TEXT NOT NULL DEFAULT '[]',  -- text formatting / iOS 18 text effects (UTF-16 ranges)
    hidden      INTEGER NOT NULL DEFAULT 0   -- the received copy of a message you sent yourself
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
    source TEXT NOT NULL,
    card   TEXT NOT NULL DEFAULT ''    -- which contact card: addresses on one card are one person
);
-- an address can sit on more than one card; this keeps every (card, address) pair
CREATE TABLE IF NOT EXISTS card_keys (card TEXT NOT NULL, key TEXT NOT NULL, PRIMARY KEY (card, key));

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
# Messaging yourself, the received copy follows the sent one - later still when the send went
# the long way round (a BlueBubbles timeout, then the iPhone).
SELF_MIRROR_WINDOW = 120.0

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
        resync = False
        for col, ddl in (("effect", "TEXT NOT NULL DEFAULT ''"), ("runs", "TEXT NOT NULL DEFAULT '[]'"),
                         ("hidden", "INTEGER NOT NULL DEFAULT 0")):
            if col not in cols:   # databases from older versions
                self.db.execute(f"ALTER TABLE messages ADD COLUMN {col} {ddl}")
                resync = resync or col in ("effect", "runs")
        if "card" not in {r[1] for r in self.db.execute("PRAGMA table_info(contacts)")}:
            self.db.execute("ALTER TABLE contacts ADD COLUMN card TEXT NOT NULL DEFAULT ''")
        if resync:
            # re-read recent history from BlueBubbles once, to fill in what older versions dropped
            self.db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('bb_since_ms','0')")
        self.db.commit()
        self._identity: dict[str, str] | None = None
        self.newly_hidden: list[int] = []
        self.removed_reaction_texts = 0
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
        self.db.execute("DELETE FROM card_keys WHERE card LIKE ?", (source + ":%",))
        n = 0
        for i, c in enumerate(cards):
            if not c.get("name"):
                continue
            card = f"{source}:{i}"
            for a in list(c.get("tels", [])) + list(c.get("emails", [])):
                k = C.key(a)
                if not k:
                    continue
                self.db.execute(
                    "INSERT OR IGNORE INTO contacts(key,name,addr,source,card) VALUES(?,?,?,?,?)",
                    (k, c["name"], a, source, card))
                self.db.execute("INSERT OR IGNORE INTO card_keys(card,key) VALUES(?,?)", (card, k))
                n += 1
        self.db.commit()
        self._identity = None
        return n

    # --------------------------------------------------------------- identity
    # One person, one conversation. Two things make several addresses one person:
    #   - they are all yours: addresses you message yourself at ("self")
    #   - they sit together on one contact card (and on no other card - a landline shared by
    #     a household must not fuse two people)
    def self_keys(self) -> set[str]:
        return set(json.loads(self.get_meta("self_keys", "[]")))

    def add_self(self, addrs) -> bool:
        cur = self.self_keys()
        new = cur | {C.key(a) for a in addrs if a}
        if new == cur:
            return False
        self.set_meta("self_keys", json.dumps(sorted(new)))
        self._identity = None
        return True

    def _build_identity(self) -> dict[str, str]:
        ident: dict[str, str] = {}
        cards: dict[str, set[str]] = {}
        owners: dict[str, set[str]] = {}
        for r in self.db.execute("SELECT card, key FROM card_keys"):
            cards.setdefault(r["card"], set()).add(r["key"])
            owners.setdefault(r["key"], set()).add(r["card"])
        # the same card synced from both the iPhone and the Mac is one card: group by content
        for card, keys in cards.items():
            solo = sorted(k for k in keys if len({frozenset(cards[c]) for c in owners[k]}) == 1)
            if len(solo) > 1:
                for k in solo:
                    ident[k] = solo[0]
        for k in self.self_keys():
            ident[k] = "self"
        return ident

    def canonical(self, tid: str) -> str:
        if not tid.startswith("addr:"):
            return tid
        if self._identity is None:
            self._identity = self._build_identity()
        k = self._identity.get(tid[5:])
        return "addr:" + k if k else tid

    def rethread(self) -> int:
        """Move conversations onto their person's one thread. Returns threads merged."""
        self._identity = None
        moved = 0
        for (tid,) in self.db.execute("SELECT id FROM threads WHERE id LIKE 'addr:%'").fetchall():
            new = self.canonical(tid)
            if new == tid:
                continue
            old = self.db.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()
            self.ensure_thread(new, participants=json.loads(old["participants"]),
                               bb_chat=old["bb_chat"], service=old["service"])
            self.db.execute("UPDATE messages SET thread=? WHERE thread=?", (new, tid))
            self.db.execute("DELETE FROM threads WHERE id=?", (tid,))
            moved += 1
        if moved:
            self.hide_self_mirrors()
        self.db.commit()
        return moved

    def learn_self(self) -> bool:
        """An address is yours if a message you sent to it came straight back from it: that's
        messaging yourself, where Messages records a sent and a received copy together."""
        rows = self.db.execute(
            "SELECT DISTINCT i.sender FROM messages i JOIN messages o "
            "ON o.from_me=1 AND i.from_me=0 AND o.norm=i.norm AND o.norm!='' "
            "AND ABS(o.ts-i.ts) <= 3 AND (o.thread='addr:'||i.sender OR o.thread='addr:self')").fetchall()
        return self.add_self(r["sender"] for r in rows if r["sender"])

    def hide_self_mirrors(self) -> int:
        """In your own conversation each message exists twice (sent + received). Show it once."""
        ids = [r[0] for r in self.db.execute(
            "SELECT id FROM messages WHERE thread='addr:self' AND from_me=0 AND hidden=0 "
            "AND EXISTS (SELECT 1 FROM messages o WHERE o.thread='addr:self' AND o.from_me=1 "
            "AND o.norm=messages.norm AND messages.ts - o.ts BETWEEN -3 AND ?)", (SELF_MIRROR_WINDOW,))]
        for i in ids:
            self.db.execute("UPDATE messages SET hidden=1, unread=0 WHERE id=?", (i,))
        self.db.commit()
        # the daemon tells open windows, which may already be showing them
        self.newly_hidden.extend(ids)
        return len(ids)

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
        if participants:
            have = json.loads(row["participants"])
            merged = have + [p for p in participants if C.key(p) not in {C.key(h) for h in have}]
            if merged != have:
                upd["participants"] = json.dumps(merged)
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
        if r["id"] == "addr:self":
            # your own card is the one with the most of your addresses on it
            best = self.db.execute(
                "SELECT c.name, (SELECT COUNT(*) FROM card_keys k WHERE k.card=c.card) n FROM contacts c "
                "WHERE c.key IN (%s) ORDER BY n DESC, c.name LIMIT 1"
                % ",".join("?" * len(parts)), [C.key(p) for p in parts]).fetchone() if parts else None
            return (best["name"] if best else "You")
        if r["id"].startswith("addr:") and len(parts) > 1:
            names = {self.contact_name(p) for p in parts} - {""}
            if len(names) == 1:
                return names.pop()
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
            "SELECT text, ts, from_me, attachments FROM messages WHERE thread=? AND hidden=0 ORDER BY ts DESC LIMIT 1",
            (r["id"],)).fetchone()
        unread = self.db.execute(
            "SELECT COUNT(*) FROM messages WHERE thread=? AND unread=1 AND hidden=0", (r["id"],)).fetchone()[0]
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
            "addresses": parts if r["id"].startswith("addr:") else [],
            "self": r["id"] == "addr:self",
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
            "SELECT t.* FROM threads t JOIN (SELECT thread, MAX(ts) m FROM messages WHERE hidden=0 GROUP BY thread) x "
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
            "segments": _segments(r["text"], json.loads(r["runs"])) or (
                # a text effect we sent, before the Mac has reported it back
                [{"text": r["text"].replace(_OBJ, "").strip(), "styles": [], "effect": r["effect"][5:]}]
                if r["effect"].startswith("text:") and r["text"].strip() else []),
            "hidden": bool(r["hidden"]),
        }

    def message(self, mid: int) -> dict | None:
        r = self.db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        return self._row(r) if r else None

    def messages(self, tid: str, limit: int = 300, before: float | None = None) -> list[dict]:
        if before is None:
            rows = self.db.execute(
                "SELECT * FROM messages WHERE thread=? AND hidden=0 ORDER BY ts DESC LIMIT ?", (tid, limit)).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM messages WHERE thread=? AND hidden=0 AND ts<? ORDER BY ts DESC LIMIT ?",
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
        m = dict(m, thread=self.canonical(m["thread"]))
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
            "bb_guid,map_handle,temp_id,attachments,unread,effect,runs) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (m["thread"], int(from_me), sender, "" if from_me else m.get("sender_addr", ""),
             m.get("text", ""), norm, float(m["ts"]), m.get("status", ""), m.get("error", ""), source,
             m.get("bb_guid"), m.get("map_handle"), m.get("temp_id"),
             json.dumps(m.get("attachments") or []), int(bool(m.get("unread")) and not from_me),
             m.get("effect") or "", json.dumps(m.get("runs") or [])))
        rid = cur.lastrowid
        if m["thread"] == "addr:self":
            self.hide_self_mirrors()
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
                atts = [dict(a) for a in m["attachments"]]
                # a file we sent: keep pointing at the copy on this computer, no need to download it
                local = [a for a in json.loads(r["attachments"]) if a.get("path")]
                for a, l in zip(atts, local):
                    a.setdefault("path", l["path"])
                upd["attachments"] = json.dumps(atts)
            if m.get("effect") and m["effect"] != r["effect"]:
                upd["effect"] = m["effect"]
            if m.get("runs") and json.dumps(m["runs"]) != r["runs"]:
                upd["runs"] = json.dumps(m["runs"])
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

    def find_quoted(self, thread: str, quoted: str, before: float) -> sqlite3.Row | None:
        """The recent message a reaction text quotes, newest first."""
        from .tapback import quote_matches
        for r in self.db.execute(
                "SELECT * FROM messages WHERE thread=? AND hidden=0 AND ts<=? ORDER BY ts DESC LIMIT 80",
                (thread, before + 5)).fetchall():
            if quote_matches(quoted, r["text"]):
                return r
        return None

    def react_row(self, rid: int, sender: str, kind: str) -> None:
        r = self.db.execute("SELECT reactions FROM messages WHERE id=?", (rid,)).fetchone()
        if r is None:
            return
        reactions = json.loads(r["reactions"])
        if kind:
            reactions[sender] = kind
        else:
            reactions.pop(sender, None)
        self.db.execute("UPDATE messages SET reactions=? WHERE id=?", (json.dumps(reactions), rid))
        self.db.commit()

    def cleanup_reaction_texts(self) -> list[tuple[str, int]]:
        """Reactions that were stored as messages (in any language) become reactions again.
        Returns (thread, message id) of each message whose reactions changed."""
        from .tapback import parse
        changed = []
        for r in self.db.execute("SELECT * FROM messages WHERE from_me=0 AND bb_guid IS NULL "
                                 "AND map_handle IS NOT NULL").fetchall():
            tb = parse(r["text"])
            if not tb:
                continue
            target = self.find_quoted(r["thread"], tb.quoted, r["ts"])
            if target is None or target["id"] == r["id"]:
                continue
            have = json.loads(target["reactions"])
            # BlueBubbles may already have delivered the real one, under its own sender key
            if tb.kind and tb.kind not in have.values():
                self.react_row(target["id"], r["sender"], tb.kind)
                changed.append((target["thread"], target["id"]))
            self.db.execute("DELETE FROM messages WHERE id=?", (r["id"],))
            self.removed_reaction_texts += 1
            changed.append((r["thread"], r["id"]))
        self.db.commit()
        return changed

    def latest_ts(self, via_col: str) -> float:
        r = self.db.execute(f"SELECT MAX(ts) FROM messages WHERE {via_col} IS NOT NULL").fetchone()
        return r[0] or 0.0


def now() -> float:
    return time.time()


def _segments(text: str, runs: list[dict]) -> list[dict]:
    """The text cut into [{text, styles, effect}] pieces by its formatting runs, or [] when it
    has none. Runs are UTF-16 ranges (Messages stores NSStrings); slicing is done in UTF-16 so
    emoji before a run don't shift it."""
    if not runs:
        return []
    u = (text or "").encode("utf-16-le")
    n = len(u) // 2
    cuts = sorted({0, n} | {max(0, min(n, r["start"])) for r in runs}
                  | {max(0, min(n, r["start"] + r["length"])) for r in runs})
    out = []
    for a, b in zip(cuts, cuts[1:]):
        if a >= b:
            continue
        piece = u[2 * a:2 * b].decode("utf-16-le", "replace").replace(_OBJ, "")
        if not piece:
            continue
        styles, effect = [], ""
        for r in runs:
            if r["start"] <= a and b <= r["start"] + r["length"]:
                styles += [x for x in r.get("styles", []) if x not in styles]
                effect = r.get("effect") or effect
        if out and out[-1]["styles"] == styles and out[-1]["effect"] == effect:
            out[-1]["text"] += piece
        else:
            out.append({"text": piece, "styles": styles, "effect": effect})
    # trim like the plain text is trimmed
    if out:
        out[0]["text"] = out[0]["text"].lstrip()
        out[-1]["text"] = out[-1]["text"].rstrip()
    return [o for o in out if o["text"]]
