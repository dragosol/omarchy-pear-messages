pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Io

// Shell-side half of Pear Messages.
//
// The app window is NOT loaded into omarchy-shell: it runs as its own Quickshell process
// (app/shell.qml), because plugins inside the shell share one QML scene and that is no place
// for your messages. What lives here has two jobs:
//
// 1. Make `omarchy plugin add` alone give you the whole app. On first load, and whenever this
//    checkout's manifest differs from the one last installed (an `omarchy plugin update`), it
//    runs the repository's own install.sh, which only copies the service and the window into
//    ~/.local/share/pear-messages and writes the launcher: as your own user, no services,
//    nothing downloaded. Exactly what the README tells people to run by hand.
// 2. Keep `pear-messagesd` running for as long as you are logged in, so messages still arrive
//    while the window is closed. This used to be a systemd user unit; a plugin is already a
//    supervised, session-length process, so the unit was duplicating what the shell does.
//    Restart-on-failure moved here, and the daemon sets its own private umask.
QtObject {
  id: root

  readonly property string home: Quickshell.env("HOME")
  readonly property string installedRoot: home + "/.local/share/pear-messages"
  // Where `omarchy plugin add` put this checkout.
  readonly property string checkout: decodeURIComponent(
      Qt.resolvedUrl("..").toString().replace(/^file:\/\//, "").replace(/\/$/, ""))
  // The manifest of the version last installed, so an update re-installs instead of leaving
  // the old window and service in place.
  readonly property string stamp: installedRoot + "/.plugin-manifest"

  // Run the installed copy when there is one, so the service and the window are always the
  // same version; fall back to the checkout so messages still arrive if installing failed.
  property bool useInstalled: false
  readonly property string backendPath: useInstalled ? installedRoot + "/backend" : checkout + "/backend"

  property int restarts: 0
  property bool stopping: false

  // systemd gave this Restart=on-failure with RestartSec=5. Same here, with a ceiling so a
  // daemon that cannot start (missing python-dbus, say) does not spin for the whole session.
  readonly property int restartLimit: 20

  // Is the installed copy missing, or from another version than this checkout? Paths go in
  // as arguments, never spliced into the script.
  property Process check: Process {
    command: ["sh", "-c", 'test -x "$1/app/launch.sh" && test -d "$1/backend/pearmsg" && cmp -s "$2" "$3/manifest.json"',
              "check", root.installedRoot, root.stamp, root.checkout]
    running: true
    onExited: function (code) {
      if (code === 0) {
        root.useInstalled = true
        root.daemon.running = true
      } else {
        root.install.running = true
      }
    }
  }

  property Process install: Process {
    running: false
    command: [root.checkout + "/install.sh"]
    onExited: function (code) {
      if (code === 0) {
        root.stampIt.running = true
        return
      }
      // Run what we have rather than nothing, and say how to see why.
      root.useInstalled = false
      root.daemon.running = true
      Quickshell.execDetached(["notify-send", "-a", "Pear Messages",
        "Pear Messages could not finish installing",
        "Run ./install.sh in " + root.checkout + " to see what went wrong."])
    }
  }

  property Process stampIt: Process {
    running: false
    command: ["cp", root.checkout + "/manifest.json", root.stamp]
    onExited: function (code) {
      root.useInstalled = true
      root.daemon.running = true
    }
  }

  property Process daemon: Process {
    running: false
    // `python3 -m` puts the working directory on sys.path, so running from the backend folder
    // replaces PYTHONPATH, and -u replaces PYTHONUNBUFFERED. No environment to pass at all.
    command: ["python3", "-B", "-u", "-m", "pearmsg", "daemon"]
    workingDirectory: root.backendPath
    onExited: function (code, status) {
      if (root.stopping || root.restarts >= root.restartLimit)
        return
      root.restarts += 1
      root.retry.restart()
    }
  }

  property Timer retry: Timer {
    interval: 5000
    repeat: false
    onTriggered: if (!root.stopping) root.daemon.running = true
  }

  Component.onDestruction: {
    root.stopping = true
    root.daemon.running = false
  }
}
