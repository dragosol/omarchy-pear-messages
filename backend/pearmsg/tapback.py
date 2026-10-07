"""Reactions that arrive as text.

Over Bluetooth the iPhone can't send a reaction as a reaction, so it sends a sentence in the
phone's own language instead:

    Loved “see you at 7”
    a attribué la mention « Adore » à « see you at 7 »
    Reacted 😂 to “see you at 7”
    Removed a heart from “see you at 7”

The wording differs per language, so recognising it is structural, not a list of sentences:
the text is a little wording and then a quote of a message in the same conversation. The quote
may be cut short with no closing mark at all, and the phone may write non-breaking spaces inside
« ». Where the phone names the reaction in its own quotes (« Rires »), that name is what's
read; otherwise the wording around the quote is. A name in no language listed here is still a
reaction - it's kept as the reaction's name rather than shown as a message - because the shape
"wording « name » wording « a message from this conversation »" is what makes it one, not the
word. Callers only accept a parse whose quote matches a real recent message, which is what keeps
an ordinary message that quotes something from being taken for a reaction.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# opening -> closing quote marks used by iOS across languages
QUOTES = {"“": "”", "«": "»", "„": "“", "‘": "’", "「": "」", "\"": "\"", "'": "'", "‹": "›"}

# What the words around the quote say, per reaction. Checked in this order: "don't like" before
# "like", "!!" before "?".
KINDS = [
    ("", r"\bremoved\b|supprim|retir[ée]|elimin[óo]|entfernt|rimoss|a eliminat|a retras|verwijderd"),
    ("dislike", r"disliked|n[’']aime pas|no le gust|gefällt .*nicht|nicht gefallen|non mi piace|nu (i )?a (mai )?plăcut|👎|dislike"),
    ("love", r"\bloved\b|adore|encant|geliebt|\bliebt\b|ha amato|adorat|iubit|❤|♥|\bheart\b|cœur|coraz[óo]n"),
    # iOS names the reaction in its own quotes - "a attribué la mention « Aime » à « … »" -
    # so the bare word appears without the verb around it. j'aime alone missed that, and the
    # whole sentence then rendered as an ordinary message. Dislike is still checked first, so
    # "Je n'aime pas" cannot be read as "Aime".
    ("like", r"\bliked\b|j[’']aime|\baime\b|gust[aóo]|gefällt|mi piace|ha apprezzato|apreciat|a plăcut|👍"),
    ("laugh", r"laughed|ha ?ha|ja ?ja|rió|lachte|gelacht|riso|risata|râs|\brires?\b|risas?\b|😂|\blaugh"),
    ("emphasize", r"emphasi[sz]ed|!!|‼|soulign|insist|enfatiz|hervorgehoben|betont|evidenziat|subliniat|exclamation"),
    ("question", r"questioned|\?|pregunt|frag|domand|întreb|question"),
]
EMOJI_VERB = re.compile(r"reacted|a réagi|reaccion|reagiert|ha reagito|a reacționat|reageerde", re.I)
MAX_AROUND = 60      # characters of wording around the quote


@dataclass
class Tapback:
    kind: str        # love like dislike laugh emphasize question, an emoji, a name the phone
                     # used that no list here knows, or "" (taken back)
    quoted: str      # the quoted message text (may be cut short, with or without "…")
    attachment: bool = False   # a reaction to a photo, video or file: nothing quoted, it names
                               # the kind of thing instead ("Liked an image")


# typographic spaces iOS puts inside « » and the like
_SPACES = re.compile(r"[\u00a0\u202f\u2007\u2009]")
# opening marks that can start a quote the phone then cut off before its closing mark. Plain
# ' and " are left out: an apostrophe in ordinary wording must not open a quote.
_OPEN_ENDED = ("«", "“", "„", "「", "‹", "‘")


def _balanced(around: str) -> bool:
    """Every quote opened in the wording is closed in it: « Rires » à - yes; Liked “It’s - no."""
    for op, cl in QUOTES.items():
        # ’ is also the apostrophe (n’aime, It’s), so the ‘ ’ pair can't be counted
        if op != cl and op != "‘" and around.count(op) != around.count(cl):
            return False
    return True


def _splits(text: str):
    """Every way the text can be wording + a quote of a message, most likely first.

    The quoted message can itself contain quotes ("I got a “yes” from her"), so the quote is not
    simply whatever the last opening mark starts: each opening mark is a candidate, from the
    right, and only a split whose wording is short and has its own quotes balanced is offered.
    Callers check each against the conversation and take the first that matches a message."""
    t = text.rstrip()
    if not t:
        return
    seen = set()

    def offer(start, op, end):
        inner = t[start + len(op):end].strip()
        around = t[:start].strip()
        if inner and around and len(around) <= MAX_AROUND and _balanced(around) and start not in seen:
            seen.add(start)
            return inner, around
        return None

    # closed: the text ends with a closing mark
    for op, cl in QUOTES.items():
        if t[-1] != cl:
            continue
        start = t.rfind(op, 0, len(t) - 1)
        while start >= 0:
            got = offer(start, op, len(t) - 1)
            if got:
                yield got
            start = t.rfind(op, 0, start)
    # cut off: the phone dropped the closing mark
    for op in _OPEN_ENDED:
        start = t.rfind(op)
        while start >= 0:
            # only a quote with no closing mark after it was cut off
            got = offer(start, op, len(t)) if QUOTES[op] not in t[start + 1:] else None
            if got:
                yield got
            start = t.rfind(op, 0, start)


def _name(around: str) -> str:
    """The reaction's own name, when the phone wrote it in quotes: « Rires », “Haha”."""
    for op, cl in QUOTES.items():
        if op in "\"'":
            continue
        a = around.find(op)
        if a >= 0:
            b = around.find(cl, a + 1)
            if b > a:
                return around[a + 1:b].strip()
    return ""


def _emoji(s: str) -> str:
    for ch in s:
        if unicodedata.category(ch) == "So" and ch not in "«»“”„":
            return ch
    return ""


# A reaction to a photo, video or file quotes nothing; the phone names the thing instead, after
# the reaction: "Liked an image", "a attribué la mention « Adore » à une image".
_THING = re.compile(
    r"\s(?:an?|the|une?|la|le|l[’']|una?|el|ein(?:e|en)?|das|den|die|un[’']?|lo|il|uma?|o)\s*"
    r"(image|photo|picture|video|movie|attachment|sticker|audio message|gif|"
    r"vid[ée]o|pi[èe]ce jointe|imagen|foto|v[íi]deo|archivo adjunto|adjunto|bild|anhang|"
    r"immagine|allegato|anexo)s?\s*[.!]?$", re.I)


# ...and with nothing quoted to check against a real message, the sentence has to open the way
# the phone opens one ("Liked", "Reacted 😂 to", "a attribué la mention", "Le gustó"...), so
# "My dad liked the image" stays a message.
_ATT_LEAD = re.compile(
    r"^(loved|liked|disliked|laughed at|emphasi[sz]ed|questioned|reacted \S+ to|removed\b|"
    r"a attribué la mention|a réagi|a retiré|a aimé|a adoré|"
    r"le gust[óo]|le encant[óo]|no le gust[óo]|se rió|reaccionó|elimin[óo]|"
    r"hat\b|gefällt|mag\b|ha (?:messo|reagito|rimosso))", re.I)


def candidates(text: str) -> list[Tapback]:
    """Every reading of the text as a reaction, most likely first. Use the first whose quote
    matches a message in the conversation."""
    out = []
    t = _SPACES.sub(" ", text or "").strip()
    for quoted, around in _splits(t):
        tb = _classify(quoted, around)
        if tb:
            out.append(tb)
    thing = _THING.search(t)
    if thing and 0 < thing.start() <= MAX_AROUND and _ATT_LEAD.match(t):
        tb = _classify("", t[:thing.start()].strip())
        if tb:
            tb.attachment = True
            out.append(tb)
    return out


def parse(text: str) -> Tapback | None:
    c = candidates(text)
    return c[0] if c else None


def _classify(quoted: str, around: str) -> Tapback | None:
    low = around.lower()
    name = _name(around)
    if name:
        # The name says which reaction; the wording around it only says whether it was taken
        # back ("a retiré la mention « Rires » de « … »").
        removed = re.search(KINDS[0][1], re.sub(re.escape(name), "", low, flags=re.I), re.I)
        if removed:
            return Tapback("", quoted)
        for kind, pattern in KINDS[1:]:
            if re.search(pattern, name.lower(), re.I):
                return Tapback(kind, quoted)
        e = _emoji(name)
        return Tapback(e or name, quoted)
    # "Reacted 😂 to …" is an emoji reaction, even when the emoji is also a keyword below
    if EMOJI_VERB.search(around) and not re.search(KINDS[0][1], low, re.I):
        e = _emoji(around)
        if e:
            return Tapback(e, quoted)
    for kind, pattern in KINDS:
        if re.search(pattern, low, re.I):
            if kind == "":
                return Tapback("", quoted)
            if kind in ("love", "like", "dislike", "laugh", "emphasize", "question"):
                return Tapback(kind, quoted)
    if EMOJI_VERB.search(around):
        e = _emoji(around)
        if e:
            return Tapback(e, quoted)
    return None


def quote_matches(quoted: str, message: str) -> bool:
    """Does a quote refer to this message? Quotes of long messages are cut short with '…'."""
    from .store import normalize
    q, m = normalize(quoted), normalize(message)
    if not q or not m:
        return False
    if q == m:
        return True
    q = q.rstrip("…").rstrip(".").strip()
    return len(q) >= 10 and m.startswith(q)
