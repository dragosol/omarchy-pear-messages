# Pear Messages

iMessage and SMS in a native Omarchy window.

![Pear Messages](preview.png)

Pear Messages talks to your messages two ways and shows them as one:

- **BlueBubbles**: a Mac signed in to Messages, running the free
  [BlueBubbles Server](https://bluebubbles.app). Your full history, group chats, photos,
  reactions, delivered and read.
- **Your iPhone over Bluetooth**: no Mac needed. New messages arrive the moment they come
  in, and you can reply to one-to-one chats. Your iPhone decides whether each one goes as
  iMessage or SMS.

When the Mac can be reached, Pear Messages uses BlueBubbles. When it can't, because the Mac
is asleep, off or away, it falls back to your iPhone. A message that arrives both ways shows
once.

![The window](docs/window.png)

> Pear Messages is an independent project. It is not made, endorsed or supported by Apple.
> iMessage and iPhone are Apple's trademarks, used here only to say what this works with.

## What it does

- **One history from two connections.** Each message is recognised by who sent it, which way
  it went, its text and when. The copy that arrives second is merged into the first, even
  when the iPhone has cut a long text short. BlueBubbles' copy wins on the details it knows
  better: the exact time, the group chat it belongs to, photos.
- **Falls back without double-sending.** A send goes through BlueBubbles first. If that fails,
  it goes through the iPhone instead. If BlueBubbles only timed out, Pear Messages first checks
  whether the Mac sent it anyway, so the other person never gets it twice.
- **Your iPhone keeps its own audio.** A paired iPhone normally starts playing music and calls
  through the computer. Pear Messages drops those audio connections the moment they appear, and
  leaves the link for messages up.
- **A connection assistant in Settings.**
  - **BlueBubbles:** *Find my Mac* looks for a server on your Tailscale network. You can also
    type any address: LAN, Tailscale, or a tunnel. It checks the server and saves it.
  - **iPhone:** *Pair iPhone* makes this computer visible and shows the pairing code to compare
    with your phone. It then tells you the one switch to flip on the iPhone.
- **Notifications even when the window is closed.** A small background service keeps
  receiving. Click a notification to open that conversation.
- **Follows your Omarchy theme.** It is built from Omarchy's own shell components.
- **Keyboard first.** Ctrl+N starts a new message, Ctrl+F searches, and Alt+↑/↓ moves between
  conversations. Enter sends, Shift+Enter adds a new line, Esc goes back.

### What each connection can do

| | BlueBubbles | iPhone over Bluetooth |
| --- | --- | --- |
| Needs | A Mac with Messages + BlueBubbles Server | Nothing but your iPhone nearby |
| History | Everything | Only messages that arrive while connected |
| Receive | ✓ | ✓ instantly |
| Send, one-to-one | ✓ iMessage | ✓ iMessage or SMS, your iPhone decides |
| Group chats | ✓ | Messages arrive; no replies |
| Photos and files | ✓ | — |
| Reactions, delivered/read | ✓ | — |
| Contact names | From the Mac | From the iPhone |

## Install

```bash
omarchy plugin add https://github.com/dragosol/omarchy-pear-messages.git --enable
cd ~/.config/omarchy/plugins/io.github.dragosol.pear-messages
./install.sh
```

Then search **Pear Messages** in the launcher. The first launch opens Settings, where the
connection assistant sets up either connection, or both.

`install.sh` runs as your user and never uses sudo. It installs, all under your home directory:

| What | Where |
| --- | --- |
| The background service (Python) | `~/.local/share/pear-messages/backend` |
| The app window (Quickshell) | `~/.local/share/pear-messages/app` |
| The launcher | `~/.local/share/applications/pear-messages.desktop` |
| The service's systemd user unit | `~/.config/systemd/user/pear-messages.service` |

Requirements: `python3`, `python-dbus`, `python-gobject` and `quickshell`, plus `bluez-obex` for
the iPhone connection:

```bash
sudo pacman -S --needed python-dbus python-gobject bluez-obex
```

After `omarchy plugin update`, run `./install.sh` again to pick up the new version.
`./uninstall.sh` removes it and keeps your messages and settings; add `--purge` to delete those too.

### Setting up BlueBubbles

1. On the Mac, install [BlueBubbles Server](https://bluebubbles.app/downloads/) and follow its setup.
   Grant it **Full Disk Access**, plus **Accessibility** and **Automation → Messages**, which it
   needs to send. Set a server password.
2. Make the Mac reachable from this computer. [Tailscale](https://tailscale.com) on both is the
   easiest, and *Find my Mac* will find it. A LAN address or a BlueBubbles tunnel URL works too.
3. In Pear Messages → Settings → BlueBubbles, enter the address and password, then press **Connect**.
   Your history loads straight away.

On Tailscale, you can make the server answer *only* over Tailscale with a firewall rule on the
Mac. BlueBubbles itself always listens on every network:

```
# /etc/pf.anchors/bluebubbles-tailscale
pass in quick on lo0 proto tcp to port 1234
pass in quick inet  proto tcp from 100.64.0.0/10 to port 1234
pass in quick inet6 proto tcp from fd7a:115c:a1e0::/48 to port 1234
block drop in quick proto tcp to port 1234
```

```bash
sudo pfctl -a com.apple/250.bluebubbles -f /etc/pf.anchors/bluebubbles-tailscale && sudo pfctl -E
```

### Setting up the iPhone

1. In Pear Messages → Settings → iPhone over Bluetooth, press **Pair iPhone**.
2. On the iPhone, open **Settings → Bluetooth** and tap this computer under *Other Devices*.
   Check that the code matches on both screens.
3. On the iPhone, tap **ⓘ** next to this computer and turn on **Show Notifications** and
   **Sync Contacts**. Without the first, the iPhone refuses to share messages.

The iPhone stays paired like any Bluetooth device. It reconnects by itself whenever it is in range.

## How it works

```
 Pear Messages window  ──socket──  pear-messagesd  ──HTTP──────────▶  BlueBubbles Server (Mac)
   (Quickshell)                    (systemd user)  ──Bluetooth MAP──▶  iPhone: messages
                                         │         ──Bluetooth PBAP─▶  iPhone: contacts
                                   messages.db
```

- The window only shows things. The service, `pear-messagesd`, holds both connections, keeps the
  merged history in `~/.local/share/pear-messages/messages.db`, and sends the notifications.
- BlueBubbles is polled every few seconds through its REST API.
- The iPhone link uses the standard Bluetooth Message Access and Phone Book Access profiles,
  through BlueZ's `obexd`. It is the same route car kits and Windows' Phone Link use.
- Your settings are in `~/.config/pear-messages/`. The BlueBubbles password is in
  `bluebubbles.json`, readable only by you.

The `pear-messages` command talks to the service too:

```bash
PYTHONPATH=~/.local/share/pear-messages/backend python3 -m pearmsg status    # connections, as JSON
PYTHONPATH=~/.local/share/pear-messages/backend python3 -m pearmsg threads   # conversations
```

## Development

```bash
python3 -B -m unittest discover -s backend/tests          # parser + merge tests
# render the window with made-up data, off-screen:
PEAR_MESSAGES_PREVIEW=threads PEAR_MESSAGES_SNAPSHOT=/tmp/shot.png \
  QT_QPA_PLATFORM=offscreen quickshell -p <a copy of app/ with Ui and Commons linked>
```

`PEAR_MESSAGES_PREVIEW` also takes `settings`, `pair` and `compose`.

## License

MIT
