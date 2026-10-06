"""The iPhone over Bluetooth: messages (MAP), contacts (PBAP), and keeping its audio its own.

What iOS gives a paired computer, once "Show Notifications" is on for it:
  - MAP inbox: the last few received messages (about 10), with sender and text, and a
    notification the moment a new one arrives. No history, no sent folder.
  - MAP outbox: a message pushed there is sent by the iPhone, as iMessage or SMS as it
    sees fit. One-to-one only.
  - PBAP (with "Sync Contacts" on): the contacts, for names.

The iPhone also connects its audio profiles to any computer it pairs with, and then plays
through it. `AudioGuard` disconnects exactly those profiles whenever they appear, and leaves
the link itself up, so the phone keeps its own audio.

All D-Bus calls here are asynchronous; the daemon's GLib loop never waits on the phone.
"""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time

import dbus
import dbus.service
from gi.repository import GLib

from . import bmsg, contacts
from .store import addr_thread

BLUEZ = "org.bluez"
OBEX = "org.bluez.obex"
MAP_MSE = "00001132-0000-1000-8000-00805f9b34fb"
PBAP_PSE = "0000112f-0000-1000-8000-00805f9b34fb"
# The iPhone's side of audio: A2DP source, AVRCP both ways, hands-free (both roles), headset.
AUDIO_UUIDS = [
    "0000110a-0000-1000-8000-00805f9b34fb",  # A2DP source (the phone plays to us)
    "0000110c-0000-1000-8000-00805f9b34fb",  # AVRCP target
    "0000110e-0000-1000-8000-00805f9b34fb",  # AVRCP controller
    "0000111f-0000-1000-8000-00805f9b34fb",  # HFP audio gateway (phone calls)
    "0000111e-0000-1000-8000-00805f9b34fb",  # HFP hands-free
    "00001112-0000-1000-8000-00805f9b34fb",  # HSP audio gateway
    "00001108-0000-1000-8000-00805f9b34fb",  # HSP headset
]
AUDIO_SET = {u.lower() for u in AUDIO_UUIDS} | {
    "0000110b-0000-1000-8000-00805f9b34fb",  # A2DP sink
    "0000110d-0000-1000-8000-00805f9b34fb",  # A2DP (generic)
}


def dev_path(address: str, adapter: str = "hci0") -> str:
    return f"/org/bluez/{adapter}/dev_" + address.upper().replace(":", "_")


def parse_map_time(s: str) -> float:
    """MAP timestamps: YYYYMMDDTHHMMSS, local time, sometimes with +HHMM / Z."""
    s = (s or "").strip()
    if not s:
        return time.time()
    try:
        base = time.strptime(s[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return time.time()
    rest = s[15:]
    if rest in ("", None):
        return time.mktime(base)
    import calendar
    utc = calendar.timegm(base)
    if rest.upper() == "Z":
        return float(utc)
    sign = -1 if rest[0] == "-" else 1
    hh, mm = int(rest[1:3] or 0), int(rest[3:5] or 0)
    return float(utc - sign * (hh * 3600 + mm * 60))


def list_phones(bus: dbus.SystemBus) -> list[dict]:
    """Paired devices that offer message access (MAP): the candidates for 'your iPhone'."""
    om = dbus.Interface(bus.get_object(BLUEZ, "/"), "org.freedesktop.DBus.ObjectManager")
    out = []
    for path, ifaces in om.GetManagedObjects().items():
        d = ifaces.get("org.bluez.Device1")
        if not d:
            continue
        uuids = [str(u).lower() for u in d.get("UUIDs", [])]
        if MAP_MSE in uuids and d.get("Paired"):
            out.append({"address": str(d.get("Address")), "name": str(d.get("Alias") or d.get("Name") or ""),
                        "connected": bool(d.get("Connected")), "path": str(path)})
    return out


def _restart_obexd() -> None:
    """Bring obexd back after its MAP server wedges, without a service manager.

    obexd is D-Bus activated on org.bluez.obex, so ending the process is enough: the next call
    into that name starts a fresh one. This used to go through the user service manager,
    which did the same thing by a longer route and made the plugin look as though it
    manages system services. Only this user's own obexd is signalled, and none running
    is not an error.
    """
    try:
        found = subprocess.run(["pgrep", "-x", "-u", str(os.getuid()), "obexd"],
                               capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return
    for pid in found.stdout.split():
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (ValueError, ProcessLookupError, PermissionError):
            pass


class AudioGuard:
    """Keeps the phone's audio on the phone: drops its audio profiles the moment they connect."""

    def __init__(self, sysbus: dbus.SystemBus, log):
        self.bus = sysbus
        self.log = log
        self.path = ""
        self.enabled = True
        self._pending = False
        sysbus.add_signal_receiver(self._props, "PropertiesChanged",
                                   "org.freedesktop.DBus.Properties", BLUEZ, path_keyword="path")
        sysbus.add_signal_receiver(self._added, "InterfacesAdded",
                                   "org.freedesktop.DBus.ObjectManager", BLUEZ)

    def watch(self, address: str) -> None:
        self.path = dev_path(address) if address else ""
        self.kick()

    def _props(self, iface, changed, invalidated, path=None):
        if not self.path or not path or not str(path).startswith(self.path):
            return
        if iface in ("org.bluez.MediaTransport1", "org.bluez.MediaControl1", "org.bluez.MediaPlayer1") \
                or (iface == "org.bluez.Device1" and ("Connected" in changed or "ServicesResolved" in changed)):
            self.kick()

    def _added(self, path, ifaces):
        if self.path and str(path).startswith(self.path + "/") and any(
                i.startswith("org.bluez.Media") for i in ifaces):
            self.kick()

    def kick(self) -> None:
        """Drop the audio profiles soon. Debounced: a connect fires a burst of signals."""
        if not self.enabled or not self.path or self._pending:
            return
        self._pending = True
        GLib.timeout_add(400, self._drop)

    def _drop(self) -> bool:
        self._pending = False
        try:
            dev = self.bus.get_object(BLUEZ, self.path)
            props = dbus.Interface(dev, "org.freedesktop.DBus.Properties")
            if not props.Get("org.bluez.Device1", "Connected"):
                return False
            if not self._audio_up():
                return False
            d = dbus.Interface(dev, "org.bluez.Device1")
            for u in AUDIO_UUIDS:
                d.DisconnectProfile(u, reply_handler=lambda: None, error_handler=lambda e: None)
            self.log("audio guard: dropped the iPhone's audio profiles")
        except dbus.DBusException:
            pass
        return False

    def _audio_up(self) -> bool:
        om = dbus.Interface(self.bus.get_object(BLUEZ, "/"), "org.freedesktop.DBus.ObjectManager")
        for path, ifaces in om.GetManagedObjects().items():
            if not str(path).startswith(self.path + "/"):
                continue
            if "org.bluez.MediaTransport1" in ifaces:
                return True
            mc = ifaces.get("org.bluez.MediaControl1")
            if mc and mc.get("Connected"):
                return True
        # Hands-free has no BlueZ object when PipeWire owns it; its card shows up there instead.
        return True


class PairAgent(dbus.service.Object):
    """A pairing agent that exists only while the connection assistant is pairing.

    It shows a code in the app for the user to compare with the iPhone, refuses the iPhone's
    audio profiles, and authorises services only for the device whose code the user actually
    confirmed.

    While this agent is the default one, BlueZ routes every incoming pairing attempt on the
    machine to it, not only the one the user started. So "allow it because we are pairing" is
    not safe: during the discoverable window any nearby device can try. Two rules follow.
    Pairing without a code to compare ("Just Works") is refused outright, because the whole
    promise made to the user is that they check a code on both screens, and Just Works offers
    nothing to check. And a service is authorised only for a device the user confirmed, or one
    that was already paired before this window opened.
    """

    PATH = "/io/github/dragosol/pearmessages/agent"

    def __init__(self, sysbus, on_code, on_event, allowed):
        super().__init__(sysbus, self.PATH)
        self.on_code = on_code      # (device_path, device_name, code, ok_cb, err_cb)
        self.on_event = on_event    # (text)
        self.allowed = allowed      # (device_path) -> bool
        self.bus = sysbus

    def _name(self, dev):
        try:
            p = dbus.Interface(self.bus.get_object(BLUEZ, dev), "org.freedesktop.DBus.Properties")
            return str(p.Get("org.bluez.Device1", "Alias"))
        except dbus.DBusException:
            return "your iPhone"

    @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="",
                         async_callbacks=("ok", "err"))
    def RequestConfirmation(self, device, passkey, ok, err):
        self.on_code(device, self._name(device), "%06d" % passkey, ok,
                     lambda: err(dbus.DBusException("Rejected", name="org.bluez.Error.Rejected")))

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        # "Just Works": BlueZ is asking whether to pair with no code on either side. There is
        # nothing for the user to compare, so accepting here would pair a nearby device during
        # the discoverable window with no confirmation at all. Always refuse; an iPhone always
        # supports the numeric comparison this agent asks for instead.
        self.on_event("refused a pairing attempt that offered no code to compare")
        raise dbus.DBusException("This computer only pairs with a code you can compare",
                                 name="org.bluez.Error.Rejected")

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        if str(uuid).lower() in AUDIO_SET:
            raise dbus.DBusException("Audio stays on the phone", name="org.bluez.Error.Rejected")
        if not self.allowed(str(device)):
            raise dbus.DBusException("Not a device you confirmed on this computer",
                                     name="org.bluez.Error.Rejected")

    @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        self.on_code(device, self._name(device), "%06d" % passkey, None, None)

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        raise dbus.DBusException("PIN pairing not supported", name="org.bluez.Error.Rejected")

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        raise dbus.DBusException("Passkey entry not supported", name="org.bluez.Error.Rejected")

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Cancel(self):
        self.on_event("cancelled")

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Release(self):
        pass


class Pairing:
    """The connection assistant's 'Pair iPhone' step."""

    def __init__(self, sysbus, emit, log, on_paired):
        self.bus = sysbus
        self.emit = emit
        self.log = log
        self.on_paired = on_paired
        self.agent: PairAgent | None = None
        self.pending = None
        self.timer = 0
        self.known: set[str] = set()
        self.approved: set[str] = set()   # device paths the user confirmed this session
        sysbus.add_signal_receiver(self._props, "PropertiesChanged",
                                   "org.freedesktop.DBus.Properties", BLUEZ, path_keyword="path")

    @property
    def active(self) -> bool:
        return self.agent is not None

    def _adapter(self):
        return dbus.Interface(self.bus.get_object(BLUEZ, "/org/bluez/hci0"), "org.freedesktop.DBus.Properties")

    def start(self, seconds: int = 180) -> None:
        if self.agent is None:
            self.approved.clear()
            self.agent = PairAgent(self.bus, self._code, self._agent_event, self._allowed)
            mgr = dbus.Interface(self.bus.get_object(BLUEZ, "/org/bluez"), "org.bluez.AgentManager1")
            mgr.RegisterAgent(PairAgent.PATH, "DisplayYesNo")
            mgr.RequestDefaultAgent(PairAgent.PATH)
        self.known = {p["address"] for p in list_phones(self.bus)}
        ad = self._adapter()
        ad.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
        ad.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(seconds))
        ad.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(True))
        alias = str(ad.Get("org.bluez.Adapter1", "Alias"))
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add_seconds(seconds, self._timeout)
        self.emit({"ev": "pair", "stage": "waiting", "computer": alias, "seconds": seconds})

    def stop(self, stage: str = "stopped") -> None:
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        try:
            self._adapter().Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(False))
        except dbus.DBusException:
            pass
        if self.agent is not None:
            try:
                mgr = dbus.Interface(self.bus.get_object(BLUEZ, "/org/bluez"), "org.bluez.AgentManager1")
                mgr.UnregisterAgent(PairAgent.PATH)
            except dbus.DBusException:
                pass
            self.agent.remove_from_connection()
            self.agent = None
        self.pending = None
        self.emit({"ev": "pair", "stage": stage})

    def answer(self, accept: bool) -> None:
        p, self.pending = self.pending, None
        if not p:
            return
        ok, err, device = p
        if accept:
            # Only now may this device have its services authorised.
            self.approved.add(str(device))
            ok()
        else:
            err()
        self.emit({"ev": "pair", "stage": "pairing" if accept else "rejected"})

    def _allowed(self, device: str) -> bool:
        """Services are authorised for a device the user confirmed here, or one that was
        already paired before this window opened (it was confirmed on some earlier run)."""
        if device in self.approved:
            return True
        try:
            props = dbus.Interface(self.bus.get_object(BLUEZ, device),
                                   "org.freedesktop.DBus.Properties")
            paired = bool(props.Get("org.bluez.Device1", "Paired"))
            address = str(props.Get("org.bluez.Device1", "Address"))
        except dbus.DBusException:
            return False
        return paired and address in self.known

    def _timeout(self) -> bool:
        self.timer = 0
        self.stop("timeout")
        return False

    def _code(self, device, name, code, ok, err):
        if ok is not None:
            self.pending = (ok, err, device)
        self.emit({"ev": "pair", "stage": "confirm", "device": name, "code": code})

    def _agent_event(self, what):
        self.pending = None
        self.emit({"ev": "pair", "stage": what})

    def _props(self, iface, changed, invalidated, path=None):
        if not self.active or iface != "org.bluez.Device1" or not ("Paired" in changed or "ServicesResolved" in changed):
            return
        for p in list_phones(self.bus):
            if p["address"] not in self.known:
                self.known.add(p["address"])
                self.log(f"paired a new phone ({p['name']})")
                self.stop("paired")
                self.on_paired(p)
                return


class Phone:
    """One long-lived MAP session (plus a PBAP pull now and then) to the chosen iPhone.

    emit(kind, payload): "status" {state, detail}, "messages" [neutral dicts],
    "contacts" [cards].
    """

    RETRY = (5, 10, 20, 40, 60)

    def __init__(self, sysbus, sesbus, emit, log, cache_dir: str):
        self.sys = sysbus
        self.ses = sesbus
        self.emit = emit
        self.log = log
        self.cache = cache_dir
        # Files pulled off the phone land here, so it is private, and tightened if an older
        # version left it at 0755.
        os.makedirs(cache_dir, mode=0o700, exist_ok=True)
        try:
            os.chmod(cache_dir, 0o700)
        except OSError:
            pass
        self.address = ""
        self.session = ""
        self.state = "off"
        self.detail = ""
        self.tries = 0
        self.timer = 0
        self.seen: set[str] = set()
        self.last_contacts = 0.0
        self.busy = False
        self.sync_again = False
        self.transfers: dict[str, tuple] = {}
        self.first_sync = True
        self.keep_unread: set[str] = set()
        sesbus.add_signal_receiver(self._obex_added, "InterfacesAdded",
                                   "org.freedesktop.DBus.ObjectManager", OBEX)
        sesbus.add_signal_receiver(self._obex_removed, "InterfacesRemoved",
                                   "org.freedesktop.DBus.ObjectManager", OBEX)
        sesbus.add_signal_receiver(self._transfer_props, "PropertiesChanged",
                                   "org.freedesktop.DBus.Properties", OBEX, path_keyword="path")
        sysbus.add_signal_receiver(self._dev_props, "PropertiesChanged",
                                   "org.freedesktop.DBus.Properties", BLUEZ, path_keyword="path")

    # ------------------------------------------------------------- lifecycle
    def set_phone(self, address: str) -> None:
        if address == self.address:
            return
        self.close()
        self.address = address
        self.seen.clear()
        self.first_sync = True
        if address:
            self._schedule(0)
        else:
            self._status("off")

    def _status(self, state: str, detail: str = "") -> None:
        if (state, detail) != (self.state, self.detail):
            self.state, self.detail = state, detail
            self.emit("status", {"state": state, "detail": detail})

    def close(self) -> None:
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if self.session:
            try:
                cl = dbus.Interface(self.ses.get_object(OBEX, "/org/bluez/obex"), "org.bluez.obex.Client1")
                cl.RemoveSession(self.session, reply_handler=lambda: None, error_handler=lambda e: None)
            except dbus.DBusException:
                pass
        self.session = ""

    def _schedule(self, delay: float) -> None:
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(int(delay * 1000), self._connect)

    def _dev_props(self, iface, changed, invalidated, path=None):
        if not self.address or path != dev_path(self.address) or iface != "org.bluez.Device1":
            return
        if changed.get("Connected") is True and not self.session:
            self._schedule(2)  # it came back into range: try straight away

    def _connect(self) -> bool:
        self.timer = 0
        if not self.address or self.session:
            return False
        self._status("connecting")
        cl = dbus.Interface(self.ses.get_object(OBEX, "/org/bluez/obex"), "org.bluez.obex.Client1")
        cl.CreateSession(self.address, {"Target": "map"}, timeout=40,
                         reply_handler=self._connected, error_handler=self._connect_failed)
        return False

    def _connect_failed(self, e) -> None:
        msg = e.get_dbus_message() if hasattr(e, "get_dbus_message") else str(e)
        if "0x43" in msg or "Forbidden" in msg:
            self._status("needs_permission",
                         "On the iPhone: Settings → Bluetooth → ⓘ next to this computer → turn on Show Notifications.")
            delay = 10
        elif "Host is down" in msg or "not available" in msg or "Timed out" in msg or "timed out" in msg.lower():
            self._status("away", "The iPhone is out of range or its Bluetooth is off.")
            delay = self.RETRY[min(self.tries, len(self.RETRY) - 1)]
        else:
            self._status("error", msg)
            delay = self.RETRY[min(self.tries, len(self.RETRY) - 1)]
        self.tries += 1
        self._schedule(delay)

    def _connected(self, path) -> None:
        self.session = str(path)
        self.tries = 0
        self.log(f"MAP session open ({self.session})")
        mas = self._mas()
        # Start in telecom/msg, where inbox/outbox live.
        mas.SetFolder("/telecom/msg", reply_handler=self._ready,
                      error_handler=lambda e: self._broken(f"SetFolder: {e}"))

    def _ready(self) -> None:
        self._status("online")
        self.sync()
        if time.time() - self.last_contacts > 6 * 3600:
            GLib.timeout_add_seconds(3, self._pull_contacts)

    def _broken(self, why: str) -> None:
        """The phone's MAP server wedged (a stale notification link does this). Start over."""
        self.log(f"MAP session broken: {why}; restarting obexd")
        self.session = ""
        self._status("connecting", "Reconnecting to the iPhone")
        _restart_obexd()
        self.tries += 1
        self._schedule(4)

    def _mas(self):
        return dbus.Interface(self.ses.get_object(OBEX, self.session), "org.bluez.obex.MessageAccess1")

    def _obex_removed(self, path, ifaces):
        if self.session and str(path) == self.session:
            self.log("MAP session closed by the phone")
            self.session = ""
            self._status("away", "Lost the connection to the iPhone.")
            self._schedule(5)

    def _obex_added(self, path, ifaces):
        # A new Message1 under our session = the iPhone announced a new message.
        if self.session and str(path).startswith(self.session + "/") and "org.bluez.obex.Message1" in ifaces:
            GLib.timeout_add(300, lambda: (self.sync(), False)[1])

    # ------------------------------------------------------------------ sync
    def sync(self) -> None:
        if not self.session:
            return
        if self.busy:
            self.sync_again = True
            return
        self.busy = True
        self._mas().ListMessages("inbox", {"MaxCount": dbus.UInt16(50)},
                                 reply_handler=self._listed,
                                 error_handler=lambda e: (setattr(self, "busy", False),
                                                          self._broken(f"ListMessages: {e}")))

    def _listed(self, msgs) -> None:
        fresh = []
        for path, p in msgs.items():
            h = str(path).rsplit("message", 1)[-1]
            if h in self.seen:
                continue
            fresh.append((str(path), h, {str(k): v for k, v in p.items()}))
        fresh.sort(key=lambda x: str(x[2].get("Timestamp", "")))
        self._fetch_next(fresh, [])

    def _fetch_next(self, todo: list, done: list) -> None:
        if not todo:
            self.busy = False
            first, self.first_sync = self.first_sync, False
            if done:
                self.emit("messages", {"messages": done, "initial": first})
            if self.sync_again:
                self.sync_again = False
                self.sync()
            return
        path, h, p = todo[0]
        rest = todo[1:]
        subject = str(p.get("Subject", ""))
        # The listing's Subject is the text cut short (iOS stops at 128 characters). Anything
        # near that length is fetched in full.
        if len(subject) < 120:
            done.append(self._neutral(h, p, subject))
            self.seen.add(h)
            GLib.idle_add(lambda: (self._fetch_next(rest, done), False)[1])
            return
        fd, target = tempfile.mkstemp(prefix="map-", suffix=".bmsg", dir=self.cache)
        os.close(fd)

        def got(tpath, props):
            self.transfers[str(tpath)] = (target, h, p, rest, done, subject)
        msg = dbus.Interface(self.ses.get_object(OBEX, path), "org.bluez.obex.Message1")
        if not p.get("Read", False):
            # Downloading a message can mark it read on the phone (MAP GetMessage). It isn't -
            # you haven't seen it - so put it back once the download is done.
            self.keep_unread.add(path)
        msg.Get(target, False, reply_handler=got,
                error_handler=lambda e: self._got_body(target, h, p, rest, done, subject, ok=False))

    def _transfer_props(self, iface, changed, invalidated, path=None):
        if iface != "org.bluez.obex.Transfer1" or str(path) not in self.transfers:
            return
        st = changed.get("Status")
        if st in ("complete", "error"):
            if self.transfer_done(str(path), st == "complete"):
                return
            args = self.transfers.pop(str(path))
            self._got_body(*args, ok=(st == "complete"))

    def _restore_unread(self, handle: str) -> None:
        path = f"{self.session}/message{handle}"
        if path not in self.keep_unread or not self.session:
            return
        self.keep_unread.discard(path)
        try:
            props = dbus.Interface(self.ses.get_object(OBEX, path), "org.freedesktop.DBus.Properties")
            props.Set("org.bluez.obex.Message1", "Read", dbus.Boolean(False),
                      reply_handler=lambda: None, error_handler=lambda e: None)
        except dbus.DBusException:
            pass

    def _got_body(self, target, h, p, rest, done, subject, ok=True):
        self._restore_unread(h)
        text = subject
        if ok:
            try:
                with open(target, "rb") as f:
                    parsed = bmsg.parse(f.read())
                text = parsed.text or subject
            except OSError:
                pass
        try:
            os.unlink(target)
        except OSError:
            pass
        done.append(self._neutral(h, p, text))
        self.seen.add(h)
        self._fetch_next(rest, done)

    def _neutral(self, handle: str, p: dict, text: str) -> dict:
        addr = str(p.get("SenderAddress") or p.get("ReplyTo") or "")
        return {
            "map_handle": handle,
            "thread": addr_thread(addr),
            "from_me": False,
            "sender_addr": addr,
            "sender_name": str(p.get("Sender") or ""),
            "text": text,
            "ts": parse_map_time(str(p.get("Timestamp", ""))),
            "unread": not bool(p.get("Read", False)),
            "thread_meta": {"participants": [addr] if addr else []},
        }

    # ------------------------------------------------------------------ send
    def send(self, to: str, text: str, done) -> None:
        """done(ok: bool, error: str)."""
        if not self.session:
            done(False, "The iPhone isn't connected")
            return
        fd, src = tempfile.mkstemp(prefix="out-", suffix=".bmsg", dir=self.cache)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(bmsg.build(to, text))

        def pushed(tpath, props):
            self.transfers[str(tpath)] = ("__send__", src, done)

        self._mas().PushMessage(src, "outbox", {"Charset": "utf8"}, reply_handler=pushed,
                                error_handler=lambda e: (self._unlink(src), done(False, str(e))))

    def _unlink(self, p):
        try:
            os.unlink(p)
        except OSError:
            pass

    # ------------------------------------------------------------- contacts
    def _pull_contacts(self) -> bool:
        if not self.address:
            return False
        cl = dbus.Interface(self.ses.get_object(OBEX, "/org/bluez/obex"), "org.bluez.obex.Client1")

        def opened(spath):
            pb = dbus.Interface(self.ses.get_object(OBEX, spath), "org.bluez.obex.PhonebookAccess1")
            fd, target = tempfile.mkstemp(prefix="pb-", suffix=".vcf", dir=self.cache)
            os.close(fd)

            def pulled(tpath, props):
                self.transfers[str(tpath)] = ("__pb__", target, spath)

            def selected():
                pb.PullAll(target, {"Format": "vcard30", "Fields": dbus.Array(["FN", "N", "TEL", "EMAIL", "ORG"], "s")},
                           reply_handler=pulled, error_handler=lambda e: fail(e, spath))
            pb.Select("int", "pb", reply_handler=selected, error_handler=lambda e: fail(e, spath))

        def fail(e, spath=None):
            self.log(f"contacts: {e}")
            if spath:
                cl.RemoveSession(spath, reply_handler=lambda: None, error_handler=lambda e: None)

        cl.CreateSession(self.address, {"Target": "pbap"}, timeout=40,
                         reply_handler=opened, error_handler=fail)
        return False

    def transfer_done(self, path: str, ok: bool) -> bool:
        """Transfers that are not message bodies (sends, contact pulls). True if handled."""
        t = self.transfers.get(path)
        if not t or t[0] not in ("__send__", "__pb__"):
            return False
        del self.transfers[path]
        if t[0] == "__send__":
            _, src, done = t
            self._unlink(src)
            done(ok, "" if ok else "The iPhone didn't accept the message")
        else:
            _, target, spath = t
            if ok:
                try:
                    with open(target, "rb") as f:
                        cards = contacts.parse_vcards(f.read())
                    self.last_contacts = time.time()
                    self.emit("contacts", cards)
                except OSError:
                    pass
            self._unlink(target)
            cl = dbus.Interface(self.ses.get_object(OBEX, "/org/bluez/obex"), "org.bluez.obex.Client1")
            cl.RemoveSession(spath, reply_handler=lambda: None, error_handler=lambda e: None)
        return True
