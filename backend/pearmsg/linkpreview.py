"""Text-only link previews: the site, the page's title and its short description.

Fetched by this computer, only when a message with a link scrolls into view, and cached as a
few lines of text. Read from the tags pages publish for exactly this (Open Graph / Twitter
cards), falling back to <title> and the meta description. No images.

Safety: only http(s); only public addresses - a message must not be able to make this
computer probe your router, localhost or your tailnet, so every hop (redirects included) is
resolved and checked; at most MAX_BYTES of HTML are read.
"""
from __future__ import annotations

import html
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

MAX_BYTES = 600 * 1024
TIMEOUT = 8
# Many sites only hand their preview tags to link-preview crawlers.
UA = ("Mozilla/5.0 (compatible; PearMessages/1.0 link preview; "
      "+https://github.com/dragosol/omarchy-pear-messages) facebookexternalhit/1.1")


class Blocked(Exception):
    pass


def _public(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise Blocked("can't resolve") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified or ip in ipaddress.ip_network("100.64.0.0/10")):
            raise Blocked("not a public address")


def _check(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise Blocked("not a web link")
    _public(u.hostname)
    return url


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_Redirects)


class _Meta(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = a["content"]
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and len(self.title) < 300:
            self.title += data


def _clean(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", html.unescape(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def fetch(url: str) -> dict:
    """{title, description, site} - raises on anything that isn't a readable public web page."""
    _check(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
                                               "Accept-Language": "en;q=0.9, *;q=0.5"})
    with _opener.open(req, timeout=TIMEOUT) as r:
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype and "xml" not in ctype:
            raise Blocked("not a web page")
        raw = r.read(MAX_BYTES)
        charset = r.headers.get_content_charset()
        final = r.geturl()
    if not charset:
        m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:4096], re.I)
        charset = m.group(1).decode() if m else "utf-8"
    try:
        text = raw.decode(charset, "replace")
    except LookupError:
        text = raw.decode("utf-8", "replace")
    p = _Meta()
    try:
        p.feed(text)
    except Exception:
        pass
    m = p.meta
    title = m.get("og:title") or m.get("twitter:title") or p.title
    desc = m.get("og:description") or m.get("twitter:description") or m.get("description") or ""
    site = m.get("og:site_name") or urllib.parse.urlsplit(final).hostname or ""
    if site.startswith("www."):
        site = site[4:]
    title, desc = _clean(title, 160), _clean(desc, 300)
    if not title and not desc:
        raise Blocked("no preview on the page")
    return {"title": title, "description": desc if desc != title else "", "site": _clean(site, 60)}
