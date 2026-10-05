"""Text-only link previews: the site, the page's title and its short description.

Fetched by this computer, only when a message with a link scrolls into view, and cached as a
few lines of text. Read from the tags pages publish for exactly this (Open Graph / Twitter
cards), falling back to <title> and the meta description. No images.

Safety: only http(s); only public addresses - a message must not be able to make this
computer probe your router, localhost or your tailnet, so every hop (redirects included) is
resolved and checked; at most MAX_BYTES of HTML are read.

The address that is checked is the address that is connected to. Resolving the name once to
check it and then handing the NAME to urllib would resolve it a second time, and a sender who
controls that name's DNS can answer differently each time: a public address for the check, a
private one for the connection. So the socket is pinned to the address that passed, and the
hostname is kept only for TLS and the Host header, leaving certificate validation unchanged.
"""
from __future__ import annotations

import html
import http.client
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


CGNAT = ipaddress.ip_network("100.64.0.0/10")        # tailnets live here


def _is_public(ip: ipaddress._BaseAddress) -> bool:
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified or ip in CGNAT)


def _resolve(host: str) -> tuple[int, str]:
    """One resolution, fully checked, returning the single address to connect to.

    Every address the name currently resolves to must be public. Refusing the whole name when
    any answer is private means a name that returns both cannot be used to reach the private
    one, and the caller cannot accidentally connect to an address that was never checked.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise Blocked("can't resolve") from None
    chosen = None
    for info in infos:
        raw = info[4][0].split("%")[0]
        if not _is_public(ipaddress.ip_address(raw)):
            raise Blocked("not a public address")
        if chosen is None:
            chosen = (info[0], raw)
    if chosen is None:
        raise Blocked("can't resolve")
    return chosen


def _check(url: str) -> str:
    """Shape only. The address check happens at connect time, on the address used."""
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise Blocked("not a web link")
    return url


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        _family, ip = _resolve(self.host)
        self.sock = socket.create_connection((ip, self.port), self.timeout, self.source_address)
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        _family, ip = _resolve(self.host)
        sock = socket.create_connection((ip, self.port), self.timeout, self.source_address)
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
            sock = self.sock
        # server_hostname stays the name, so the certificate is still checked against it.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check(newurl)                                   # the hop's own connect re-checks its address
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_PinnedHTTPHandler, _PinnedHTTPSHandler, _Redirects)


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
