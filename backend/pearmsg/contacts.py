"""Contacts from the iPhone (PBAP vCards) and matching addresses to them.

Numbers arrive in every shape: "+44 7700 900123", "07700900123", "(770) 090-0123".
Matching is on the last 9 digits, which is what carrier caller-ID does too; it is wrong
only for two numbers that differ in country code alone, which does not happen in one
person's contacts.
"""
from __future__ import annotations

import base64
import re

from .bmsg import _prop


def digits(addr: str) -> str:
    return re.sub(r"\D", "", addr or "")


def key(addr: str) -> str:
    """A stable identity for an address: email lowercased, a number's last 9 digits."""
    addr = (addr or "").strip()
    if "@" in addr:
        return addr.lower()
    d = digits(addr)
    return d[-9:] if len(d) >= 9 else d or addr.lower()


def _unfold(raw: str) -> list[str]:
    lines: list[str] = []
    for ln in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if ln[:1] in (" ", "\t") and lines:
            lines[-1] += ln[1:]
        else:
            lines.append(ln)
    return lines


def parse_vcards(raw: str | bytes) -> list[dict]:
    """[{name, tels, emails, photo(bytes|None)}] from a PBAP phonebook pull."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    out: list[dict] = []
    cur: dict | None = None
    for ln in _unfold(raw):
        s = ln.strip()
        up = s.upper()
        if up == "BEGIN:VCARD":
            cur = {"name": "", "n": "", "org": "", "tels": [], "emails": [], "photo": None}
            continue
        if up == "END:VCARD":
            if cur is not None:
                cur["name"] = cur["name"] or cur["n"] or cur["org"]
                del cur["n"], cur["org"]
                if cur["tels"] or cur["emails"]:
                    out.append(cur)
            cur = None
            continue
        if cur is None or ":" not in s:
            continue
        k, v = _prop(s)
        v = v.strip()
        if k == "FN" and v:
            cur["name"] = v
        elif k == "N":
            parts = [p.strip() for p in v.split(";")]
            cur["n"] = " ".join(p for p in (parts[1] if len(parts) > 1 else "", parts[0]) if p)
        elif k == "ORG":
            cur["org"] = v.split(";")[0]
        elif k == "TEL" and v:
            cur["tels"].append(v)
        elif k == "EMAIL" and v:
            cur["emails"].append(v)
        elif k == "PHOTO" and v:
            try:
                cur["photo"] = base64.b64decode(v.split(",")[-1], validate=False)
            except Exception:
                pass
    return out
