"""BlueBubbles: iMessage through a Mac running the BlueBubbles server.

Only the server's REST API is used (no socket.io client, so nothing outside the standard
library). New messages are found by polling /message/query for anything newer than the
last one seen, which over a LAN or Tailscale costs one small request every few seconds.

Everything here runs on a worker thread and hands plain dicts back to the daemon's main
loop; nothing here touches the store.
"""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

WITH_MSG = ["chat", "chat.participants", "handle", "attachment", "attributedBody"]

# iOS 18 text formatting and animated text effects, as Messages stores them on each run of the
# message's attributed string. Effect ids are the ones imessage-exporter reverse-engineered.
TEXT_STYLES = {
    "__kIMTextBoldAttributeName": "bold",
    "__kIMTextItalicAttributeName": "italic",
    "__kIMTextUnderlineAttributeName": "underline",
    "__kIMTextStrikethroughAttributeName": "strikethrough",
}
TEXT_EFFECTS = {5: "big", 11: "small", 9: "shake", 8: "nod", 12: "explode", 4: "ripple", 6: "bloom", 10: "jitter"}
TEXT_EFFECT_IDS = {v: k for k, v in TEXT_EFFECTS.items()}


def text_runs(attributed) -> list[dict]:
    """[{start, length, styles, effect}] (UTF-16 ranges into the message text), only for runs
    that carry formatting or an effect."""
    out = []
    parts = attributed if isinstance(attributed, list) else [attributed] if attributed else []
    for part in parts[:1]:
        for r in (part or {}).get("runs", []) or []:
            attrs = r.get("attributes") or {}
            styles = [name for key, name in TEXT_STYLES.items() if attrs.get(key)]
            fx = TEXT_EFFECTS.get(attrs.get("__kIMTextEffectAttributeName"), "")
            rng = r.get("range") or [0, 0]
            if (styles or fx) and len(rng) == 2:
                out.append({"start": int(rng[0]), "length": int(rng[1]), "styles": styles, "effect": fx})
    return out

# associatedMessageType -> tapback. 2000-2005 add, 3000-3005 remove.
TAPBACKS = {0: "love", 1: "like", 2: "dislike", 3: "laugh", 4: "emphasize", 5: "question"}
TAPBACK_NAMES = {"love": 2000, "like": 2001, "dislike": 2002, "laugh": 2003, "emphasize": 2004, "question": 2005}


class BBError(Exception):
    pass



class Client:
    def __init__(self, url: str, password: str, timeout: float = 15.0):
        url = (url or "").strip().rstrip("/")
        if url and "://" not in url:
            url = "http://" + url
        self.url = url
        self.password = password or ""
        self.timeout = timeout

    def _req(self, method: str, path: str, body: dict | None = None, *, raw: bool = False,
             timeout: float | None = None):
        q = urllib.parse.urlencode({"password": self.password})
        req = urllib.request.Request(
            f"{self.url}/api/v1{path}{'&' if '?' in path else '?'}{q}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", "User-Agent": "PearMessages/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("message") or str(e)
            except Exception:
                msg = str(e)
            if e.code == 401:
                raise BBError("The server refused the password") from None
            raise BBError(f"{msg} (HTTP {e.code})") from None
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise BBError(f"Can't reach the server: {reason}") from None
        if raw:
            return data
        try:
            d = json.loads(data)
        except ValueError:
            raise BBError("That address answered, but not like a BlueBubbles server") from None
        if isinstance(d, dict) and d.get("status", 200) >= 400:
            raise BBError(d.get("message") or d.get("error", {}).get("message") or "server error")
        return d.get("data") if isinstance(d, dict) else d

    # ---------------------------------------------------------------- API
    def ping(self) -> None:
        self._req("GET", "/ping", timeout=6)

    def info(self) -> dict:
        return self._req("GET", "/server/info") or {}

    def chats(self, limit: int = 200) -> list[dict]:
        return self._req("POST", "/chat/query", {
            "limit": limit, "offset": 0, "with": ["participants", "lastMessage"], "sort": "lastmessage"}) or []

    def messages_after(self, after_ms: int, limit: int = 200) -> list[dict]:
        return self._req("POST", "/message/query", {
            "limit": limit, "offset": 0, "sort": "ASC", "after": int(after_ms), "with": WITH_MSG}) or []

    def recent_messages(self, limit: int = 1000) -> list[dict]:
        return self._req("POST", "/message/query", {
            "limit": limit, "offset": 0, "sort": "DESC", "with": WITH_MSG}) or []

    def chat_messages(self, chat_guid: str, limit: int = 100, before_ms: int | None = None) -> list[dict]:
        body = {"limit": limit, "offset": 0, "sort": "DESC", "chatGuid": chat_guid, "with": WITH_MSG}
        if before_ms:
            body["before"] = int(before_ms)
        return self._req("POST", "/message/query", body) or []

    def contacts(self) -> list[dict]:
        out = []
        for c in self._req("GET", "/contact") or []:
            name = c.get("displayName") or " ".join(
                p for p in (c.get("firstName"), c.get("lastName")) if p)
            out.append({
                "name": name or "",
                "tels": [p.get("address", "") for p in c.get("phoneNumbers", []) if p.get("address")],
                "emails": [p.get("address", "") for p in c.get("emails", []) if p.get("address")],
            })
        return out

    def send(self, chat_guid: str, text: str, temp_guid: str, private_api: bool, effect: str = "") -> dict:
        body = {"chatGuid": chat_guid, "tempGuid": temp_guid, "message": text,
                "method": "private-api" if private_api else "apple-script"}
        if effect.startswith("text:"):
            # an iOS 18 text effect over the whole message (Private API only)
            body["textFormatting"] = [{"start": 0, "length": len(text.encode("utf-16-le")) // 2,
                                       "styles": [effect[5:]]}]
        elif effect:
            body["effectId"] = effect   # only the Private API can send effects
        return self._req("POST", "/message/text", body, timeout=20) or {}

    def new_chat(self, address: str, text: str, temp_guid: str, private_api: bool, effect: str = "") -> dict:
        body = {"addresses": [address], "message": text, "tempGuid": temp_guid, "service": "iMessage",
                "method": "private-api" if private_api else "apple-script"}
        if effect.startswith("text:"):
            body["textFormatting"] = [{"start": 0, "length": len(text.encode("utf-16-le")) // 2,
                                       "styles": [effect[5:]]}]
        elif effect:
            body["effectId"] = effect
        return self._req("POST", "/chat/new", body, timeout=20) or {}

    def react(self, chat_guid: str, message_guid: str, message_text: str, reaction: str) -> dict:
        """reaction: love like dislike laugh emphasize question, or '-love' etc. to take one back.
        BlueBubbles can only do this through its Private API."""
        return self._req("POST", "/message/react", {
            "chatGuid": chat_guid, "selectedMessageGuid": message_guid,
            "selectedMessageText": message_text, "reaction": reaction, "partIndex": 0}, timeout=20) or {}

    def mark_read(self, chat_guid: str) -> None:
        self._req("POST", f"/chat/{urllib.parse.quote(chat_guid, safe='')}/read", {})

    def attachment(self, guid: str) -> bytes:
        return self._req("GET", f"/attachment/{urllib.parse.quote(guid, safe='')}/download",
                         raw=True, timeout=120)


# iMessage effects, by the id Messages stores. Bubble effects animate one message; screen
# effects fill the conversation.
EFFECTS = {
    "com.apple.MobileSMS.expressivesend.impact": "Slam",
    "com.apple.MobileSMS.expressivesend.loud": "Loud",
    "com.apple.MobileSMS.expressivesend.gentle": "Gentle",
    "com.apple.MobileSMS.expressivesend.invisibleink": "Invisible Ink",
    "com.apple.messages.effect.CKEchoEffect": "Echo",
    "com.apple.messages.effect.CKSpotlightEffect": "Spotlight",
    "com.apple.messages.effect.CKHappyBirthdayEffect": "Balloons",
    "com.apple.messages.effect.CKConfettiEffect": "Confetti",
    "com.apple.messages.effect.CKHeartEffect": "Love",
    "com.apple.messages.effect.CKLasersEffect": "Lasers",
    "com.apple.messages.effect.CKFireworksEffect": "Fireworks",
    "com.apple.messages.effect.CKSparklesEffect": "Celebration",
    "com.apple.messages.effect.CKShootingStarEffect": "Shooting Star",
}


def _emoji_from_text(text: str) -> str:
    import re
    m = re.match(r"^Reacted (\S+) to", text or "")
    return m.group(1) if m else ""


def new_temp_guid() -> str:
    return "temp-" + uuid.uuid4().hex


def chat_is_group(chat: dict) -> bool:
    # style 43 = group, 45 = one-to-one; guid "iMessage;+;chat123" vs "iMessage;-;+44..."
    if chat.get("style") in (43, 45):
        return chat["style"] == 43
    return ";+;" in (chat.get("guid") or "")


def chat_service(chat: dict) -> str:
    g = chat.get("guid") or ""
    return g.split(";", 1)[0] if ";" in g else ""


def to_message(m: dict) -> dict | None:
    """A BlueBubbles message as the daemon's neutral dict, or a reaction, or None to skip.

    Returns {"kind": "message", ...} or {"kind": "reaction", target, sender, reaction}.
    """
    chats = m.get("chats") or []
    chat = chats[0] if chats else (m.get("chat") or {})
    handle = (m.get("handle") or {}).get("address", "") if m.get("handle") else ""
    from_me = bool(m.get("isFromMe"))
    assoc = m.get("associatedMessageGuid")
    atype = m.get("associatedMessageType")
    if assoc and atype not in (None, 0, "0"):
        target = assoc.split("/", 1)[-1]  # "p:0/GUID" -> GUID
        # Servers send the type as a number (2000) or a string ("love", "-love", "2006").
        if isinstance(atype, str) and atype.lstrip("-").isdigit():
            atype = int(atype)
        if isinstance(atype, int):
            if 2000 <= atype <= 2005:
                kind = TAPBACKS[atype - 2000]
            elif atype == 2006:
                # iOS 18 emoji reaction: the emoji is only in the text, "Reacted 😂 to “…”"
                kind = m.get("associatedMessageEmoji") or _emoji_from_text(m.get("text") or "") or "👍"
            elif 3000 <= atype <= 3006:
                kind = ""
            else:
                return None  # stickers, games etc.
        else:
            s = str(atype)
            kind = "" if s.startswith("-") else s  # "love", "-love"
        return {"kind": "reaction", "target": target, "sender": "me" if from_me else handle,
                "reaction": kind}
    if m.get("itemType") not in (None, 0):
        return None  # group renames, members joining, etc.
    atts = [{
        "guid": a.get("guid", ""), "mime": a.get("mimeType") or "", "name": a.get("transferName") or "",
        "size": a.get("totalBytes") or 0,
    } for a in (m.get("attachments") or []) if not a.get("hideAttachment")]
    group = chat_is_group(chat) if chat else False
    participants = [p.get("address", "") for p in (chat.get("participants") or []) if p.get("address")]
    if group:
        thread = "chat:" + chat.get("guid", "")
    else:
        other = handle or (participants[0] if participants else (chat.get("chatIdentifier") or ""))
        from .store import addr_thread
        thread = addr_thread(other)
        participants = [other] if other else participants
    status = ""
    if from_me:
        status = "read" if m.get("dateRead") else "delivered" if m.get("dateDelivered") or m.get("isDelivered") else "sent"
        if m.get("error"):
            status = "failed"
    return {
        "kind": "message",
        "bb_guid": m.get("guid"),
        "temp_id": m.get("tempGuid"),
        "thread": thread,
        "from_me": from_me,
        "sender_addr": "" if from_me else handle,
        "text": m.get("text") or "",
        "ts": (m.get("dateCreated") or time.time() * 1000) / 1000.0,
        "status": status,
        "error": f"Not delivered (error {m['error']})" if from_me and m.get("error") else "",
        "attachments": atts,
        "effect": m.get("expressiveSendStyleId") or "",
        "runs": text_runs(m.get("attributedBody")),
        "unread": (not from_me) and not m.get("dateRead"),
        "thread_meta": {
            "name": chat.get("displayName") or "" if group else "",
            "participants": participants,
            "is_group": group,
            "bb_chat": chat.get("guid", ""),
            "service": chat_service(chat),
        },
    }


class Poller(threading.Thread):
    """Keeps a BlueBubbles connection alive and reports to `emit(kind, payload)`.

    kind: "status" {state, detail, info}, "messages" [dicts], "contacts" [cards].
    `emit` is called on this thread; the daemon marshals it onto its main loop.
    """

    def __init__(self, client: Client, since_ms: int, emit, interval: float = 3.0):
        super().__init__(daemon=True, name="bluebubbles")
        self.client = client
        self.since_ms = since_ms
        self.emit = emit
        self.interval = interval
        self._stop = threading.Event()
        self.wake = threading.Event()
        self.info: dict = {}

    def stop(self) -> None:
        self._stop.set()
        self.wake.set()

    def run(self) -> None:
        backoff = 5.0
        synced_contacts = 0.0
        online = False
        while not self._stop.is_set():
            try:
                if not online:
                    self.emit("status", {"state": "connecting"})
                    self.info = self.client.info()
                    online = True
                    backoff = 5.0
                    self.emit("status", {"state": "online", "info": self.info})
                    if not self.since_ms:
                        # First connection ever: take the recent history in one go.
                        msgs = self.client.recent_messages(1000)
                        self.emit("messages", list(reversed(msgs)))
                        self.since_ms = max((m.get("dateCreated") or 0) for m in msgs) if msgs else int(time.time() * 1000)
                if time.time() - synced_contacts > 6 * 3600:
                    try:
                        self.emit("contacts", self.client.contacts())
                    except Exception:
                        pass
                    synced_contacts = time.time()
                # Overlap by a minute: a message can be written to the Mac's database a little
                # after its own dateCreated; the store drops the repeats by guid.
                msgs = self.client.messages_after(self.since_ms - 60_000)
                if msgs:
                    self.since_ms = max(self.since_ms, max((m.get("dateCreated") or 0) for m in msgs))
                    self.emit("messages", msgs)
                self.wake.wait(self.interval)
                self.wake.clear()
            except BBError as e:
                if online:
                    online = False
                self.emit("status", {"state": "error", "detail": str(e)})
                self.wake.wait(backoff)
                self.wake.clear()
                backoff = min(backoff * 2, 60.0)
            except Exception as e:  # never let the thread die
                online = False
                self.emit("status", {"state": "error", "detail": f"{type(e).__name__}: {e}"})
                self.wake.wait(backoff)
                self.wake.clear()
                backoff = min(backoff * 2, 60.0)
