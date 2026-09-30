pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Io

// Shell-side half of Pear Messages.
//
// The app is NOT loaded into omarchy-shell. It runs as its own Quickshell window (app/shell.qml)
// next to a small background service (pear-messagesd) that keeps receiving messages when the
// window is closed. Plugins inside the shell share one QML scene - no place for your messages.
//
// `omarchy plugin add` only clones files; it never runs an installer. So this service does one
// thing: if the app has not been installed yet, it says so once, with where to go.
QtObject {
  id: root

  readonly property string home: Quickshell.env("HOME")
  readonly property string installed: home + "/.local/share/pear-messages/app/launch.sh"
  readonly property string checkout: decodeURIComponent(
      Qt.resolvedUrl("..").toString().replace(/^file:\/\//, "").replace(/\/$/, ""))

  property Process check: Process {
    command: ["test", "-x", root.installed]
    running: false
    onExited: function (code) {
      if (code !== 0)
        Quickshell.execDetached(["notify-send", "-a", "Pear Messages",
          "Pear Messages needs one more step",
          "Run ./install.sh in " + root.checkout + " to set up the app and its launcher."])
    }
  }

  Component.onCompleted: check.running = true
}
