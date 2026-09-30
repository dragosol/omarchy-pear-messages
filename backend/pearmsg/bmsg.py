"""bMessage: the text format MAP uses for one message, in both directions.

A bMessage is nested BEGIN/END blocks. The parts we care about:

    BEGIN:BMSG                 header: VERSION, STATUS, TYPE, FOLDER
    BEGIN:VCARD ... END:VCARD  originator (who sent it) - may be absent or empty
    BEGIN:BENV
      BEGIN:VCARD ... END:VCARD  recipient(s)
      BEGIN:BBODY
        CHARSET, LENGTH
        BEGIN:MSG
        the text
        END:MSG
      END:BBODY
    END:BENV
    END:BMSG

Phones differ in line endings and in how strictly they fill LENGTH, so parsing is by
structure, never by LENGTH.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Card:
    name: str = ""
    tels: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)

    @property
    def address(self) -> str:
        return (self.tels or self.emails or [""])[0]


@dataclass
class BMessage:
    type: str = ""
    folder: str = ""
    status: str = ""
    originator: Card | None = None
    recipients: list[Card] = field(default_factory=list)
    text: str = ""


def _prop(line: str) -> tuple[str, str]:
    """'TEL;TYPE=CELL:+4412' -> ('TEL', '+4412')."""
    key, _, value = line.partition(":")
    return key.split(";", 1)[0].strip().upper(), value


def _card(lines: list[str]) -> Card:
    c = Card()
    fn = n = ""
    for ln in lines:
        k, v = _prop(ln)
        if k == "FN":
            fn = v.strip()
        elif k == "N":
            parts = [p.strip() for p in v.split(";")]
            # N is family;given;... - show "given family"
            n = " ".join(p for p in (parts[1] if len(parts) > 1 else "", parts[0]) if p)
        elif k == "TEL" and v.strip():
            c.tels.append(v.strip())
        elif k == "EMAIL" and v.strip():
            c.emails.append(v.strip())
    c.name = fn or n
    return c


def parse(raw: str | bytes) -> BMessage:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    msg = BMessage()
    stack: list[str] = []
    card: list[str] | None = None
    body: list[str] | None = None
    bodies: list[str] = []
    for ln in lines:
        if body is not None:
            # Inside BEGIN:MSG the text is verbatim, except its own terminator.
            if ln.strip().upper() == "END:MSG":
                bodies.append("\n".join(body))
                body = None
            else:
                body.append(ln)
            continue
        s = ln.strip()
        up = s.upper()
        if up.startswith("BEGIN:"):
            what = up[6:]
            if what == "MSG":
                body = []
                continue
            stack.append(what)
            if what == "VCARD":
                card = []
            continue
        if up.startswith("END:"):
            what = up[4:]
            if what == "VCARD" and card is not None:
                c = _card(card)
                card = None
                if "BENV" in stack:
                    msg.recipients.append(c)
                else:
                    msg.originator = c
            if stack and stack[-1] == what:
                stack.pop()
            continue
        if card is not None:
            card.append(s)
            continue
        if stack and stack[-1] == "BMSG":
            k, v = _prop(s)
            if k == "TYPE":
                msg.type = v.strip()
            elif k == "FOLDER":
                msg.folder = v.strip()
            elif k == "STATUS":
                msg.status = v.strip()
    # A long message can arrive as several MSG parts; they are one text.
    msg.text = "".join(bodies).strip("\n")
    return msg


def build(to: str, text: str, name: str = "", folder: str = "telecom/msg/outbox") -> str:
    """A bMessage for PushMessage. `to` is a phone number or an Apple Account email."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    msg_block = "BEGIN:MSG\r\n" + text.replace("\n", "\r\n") + "\r\nEND:MSG\r\n"
    kind = "EMAIL" if "@" in to else "TEL"
    recipient = (
        "BEGIN:VCARD\r\nVERSION:2.1\r\n"
        f"N:{name}\r\n"
        + (f"FN:{name}\r\n" if name else "")
        + f"{kind}:{to}\r\n"
        "END:VCARD\r\n"
    )
    return (
        "BEGIN:BMSG\r\n"
        "VERSION:1.0\r\n"
        "STATUS:READ\r\n"
        "TYPE:SMS_GSM\r\n"
        f"FOLDER:{folder}\r\n"
        "BEGIN:BENV\r\n"
        + recipient
        + "BEGIN:BBODY\r\n"
        "CHARSET:UTF-8\r\n"
        f"LENGTH:{len(msg_block.encode('utf-8'))}\r\n"
        + msg_block
        + "END:BBODY\r\n"
        "END:BENV\r\n"
        "END:BMSG\r\n"
    )
