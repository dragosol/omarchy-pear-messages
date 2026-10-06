"""Reactions that arrive as text.

Over Bluetooth the iPhone can't send a reaction as a reaction, so it sends a sentence in the
phone's own language instead:

    Loved “see you at 7”
    a attribué la mention « Adore » à « see you at 7 »
    Reacted 😂 to “see you at 7”
    Removed a heart from “see you at 7”

The wording differs per language, so recognising it is structural first: the text ends with a
quote of a message in the same conversation, and what's around the quote is short. Only then
are the words around it read, to tell which reaction it is. A normal message that happens to
quote something doesn't match a recent message word for word, or has more to say.
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
    ("laugh", r"laughed|ha ?ha|ja ?ja|rió|lachte|gelacht|riso|risata|râs|😂|\blaugh"),
    ("emphasize", r"emphasi[sz]ed|!!|‼|soulign|enfatiz|hervorgehoben|betont|evidenziat|subliniat|exclamation"),
    ("question", r"questioned|\?|pregunt|frag|domand|întreb|question"),
]
EMOJI_VERB = re.compile(r"reacted|a réagi|reaccion|reagiert|ha reagito|a reacționat|reageerde", re.I)
MAX_AROUND = 60      # characters of wording around the quote


@dataclass
class Tapback:
    kind: str        # love like dislike laugh emphasize question, an emoji, or "" (taken back)
    quoted: str      # the quoted message text (may be cut short with "…")


def _last_quote(text: str) -> tuple[str, str] | None:
    """(quoted text, the rest) for the quotation the text ends with."""
    t = text.rstrip()
    if not t:
        return None
    close = t[-1]
    for op, cl in QUOTES.items():
        if cl != close:
            continue
        start = t.rfind(op, 0, len(t) - 1)
        if start < 0:
            continue
        inner = t[start + len(op):-1].strip()
        if inner:
            return inner, (t[:start]).strip()
    return None


def _emoji(s: str) -> str:
    for ch in s:
        if unicodedata.category(ch) == "So" and ch not in "«»“”„":
            return ch
    return ""


def parse(text: str) -> Tapback | None:
    q = _last_quote(text or "")
    if not q:
        return None
    quoted, around = q
    if not around or len(around) > MAX_AROUND:
        return None
    low = around.lower()
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
