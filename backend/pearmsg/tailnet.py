"""Tailscale helpers for the connection assistant.

Any BlueBubbles address works (LAN, Tailscale, a tunnel). Tailscale is only used to *find*
a server ("Find my Mac" probes online tailnet peers) and to say how a connection runs:
`check_url` tells whether an address is a Tailscale one reached through the Tailscale
interface, and the settings show that as a label, never as a requirement.
"""
from __future__ import annotations

import ipaddress
import json
import shutil
import socket
import subprocess
import urllib.parse

TS_V4 = ipaddress.ip_network("100.64.0.0/10")
TS_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


class NotTailnet(Exception):
    pass


def is_ts_ip(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a in (TS_V4 if a.version == 4 else TS_V6)


def ts_interfaces() -> set[str]:
    names = {"tailscale0"}
    try:
        out = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=3).stdout
        for link in json.loads(out or "[]"):
            for a in link.get("addr_info", []):
                if is_ts_ip(a.get("local", "")):
                    names.add(link.get("ifname", ""))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return names


def route_dev(ip: str) -> str:
    try:
        out = subprocess.run(["ip", "-j", "route", "get", ip], capture_output=True, text=True, timeout=3).stdout
        r = json.loads(out or "[]")
        return r[0].get("dev", "") if r else ""
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


def check_url(url: str) -> str:
    """Returns the host if the URL is a Tailscale address routed over Tailscale; raises NotTailnet otherwise."""
    u = urllib.parse.urlsplit(url if "://" in url else "http://" + url)
    host = u.hostname or ""
    if not host:
        raise NotTailnet("Enter the server's Tailscale address, like http://100.101.102.103:1234")
    try:
        ips = {ai[4][0] for ai in socket.getaddrinfo(host, u.port or 1234, proto=socket.IPPROTO_TCP)}
    except socket.gaierror:
        raise NotTailnet(f"Can't find {host} — is Tailscale up on this computer?") from None
    bad = [ip for ip in ips if not is_ts_ip(ip.split("%")[0])]
    if bad or not ips:
        raise NotTailnet(f"{host} is not a Tailscale address. Pear Messages only connects to "
                         "BlueBubbles over Tailscale (100.x.y.z or a .ts.net name).")
    devs = ts_interfaces()
    for ip in ips:
        dev = route_dev(ip.split("%")[0])
        if dev not in devs:
            raise NotTailnet(f"The route to {host} doesn't go through Tailscale "
                             f"(it uses {dev or 'no interface'}). Is Tailscale connected?")
    return host


def peers() -> list[dict]:
    """Online Tailscale peers: [{name, ip, os}] - candidates for the assistant's 'Find my Mac'."""
    exe = shutil.which("tailscale")
    if not exe:
        return []
    try:
        st = json.loads(subprocess.run([exe, "status", "--json"], capture_output=True,
                                       text=True, timeout=5).stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    out = []
    for p in (st.get("Peer") or {}).values():
        ips = [ip for ip in p.get("TailscaleIPs", []) if ":" not in ip]
        if not ips or not p.get("Online"):
            continue
        out.append({"name": (p.get("HostName") or p.get("DNSName") or "").split(".")[0],
                    "ip": ips[0], "os": p.get("OS", "")})
    return out


def probe_bluebubbles(ip: str, port: int = 1234, timeout: float = 2.5) -> bool:
    """Does this peer run a BlueBubbles server? Asks /ping without a password: BlueBubbles
    answers with its JSON 401 shape, anything else does not."""
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{ip}:{port}/api/v1/ping", timeout=timeout) as r:
            body = r.read(2048)
    except urllib.error.HTTPError as e:
        body = e.read(2048)
    except (OSError, ValueError):
        return False
    try:
        d = json.loads(body)
    except ValueError:
        return False
    return isinstance(d, dict) and "status" in d and "message" in d


def describe(url: str) -> str:
    """'tailscale' | 'https' | 'http' - how a connection to this address runs."""
    try:
        check_url(url)
        return "tailscale"
    except NotTailnet:
        return "https" if url.lower().startswith("https://") else "http"
