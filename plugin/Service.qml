pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Io

// Shell-side half of Pear Messages.
//
// The app window is NOT loaded into omarchy-shell: it runs as its own Quickshell process
// (app/shell.qml), because plugins inside the shell share one QML scene and that is no place
// for your messages. What lives here is small and has one job: keep `pear-messagesd` running
// for as long as you are logged in, so messages still arrive while the window is closed.
//
// This used to be a systemd user unit that install.sh enabled. It is not any more. A plugin is
// already a supervised, session-length process, so the unit was duplicating what the shell
// does, and installing it meant the listing could never offer a plain copy-paste install.
// Restart-on-failure and the private umask moved with it: the restart is below, and the daemon
// sets its own umask now rather than inheriting one from a unit file.
QtObject {
  id: root

  readonly property string home: Quickshell.env("HOME")
  readonly property string installedRoot: home + "/.local/share/pear-messages"
  // Where `omarchy plugin add` put this checkout. The backend ships inside it, so the service
  // can run even before install.sh has laid down the app window and its launcher.
  readonly property string checkout: decodeURIComponent(
      Qt.resolvedUrl("..").toString().replace(/^file:\/\//, "").replace(/\/$/, ""))

  // Prefer the installed copy when there is one, so the service and the app window are always
  // the same version; fall back to the checkout so a fresh `plugin add` still receives messages.
  property bool useInstalled: false
  readonly property string backendPath: useInstalled ? installedRoot + "/backend" : checkout + "/backend"

  property int restarts: 0
  property bool stopping: false

  // systemd gave this Restart=on-failure with RestartSec=5. Same here, with a ceiling so a
  // daemon that cannot start (missing python-dbus, say) does not spin for the whole session.
  readonly property int restartLimit: 20

  property Process probe: Process {
    command: ["test", "-d", root.installedRoot + "/backend/pearmsg"]
    running: true
    onExited: function (code) {
      root.useInstalled = (code === 0)
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

  // Tell the user once if the app window has not been installed. The service above is already
  // running by then, so messages are arriving; what is missing is the launcher.
  property Process check: Process {
    command: ["test", "-x", root.installedRoot + "/app/launch.sh"]
    running: true
    onExited: function (code) {
      if (code !== 0)
        Quickshell.execDetached(["notify-send", "-a", "Pear Messages",
          "Pear Messages needs one more step",
          "Run ./install.sh in " + root.checkout + " to add the app window and its launcher."])
    }
  }

  Component.onDestruction: {
    root.stopping = true
    root.daemon.running = false
  }
}
