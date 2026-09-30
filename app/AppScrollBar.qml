import QtQuick
import QtQuick.Controls

// The stock ScrollBar handle is a dark grey on a dark background - effectively invisible - and
// it only lights up while the Flickable reports `moving`. The list's scrolling is our own
// physics setting contentY directly, so it never reports moving and the stock bar never woke.
// This one wakes on any change of position, however it happened.
//
// macOS-style: hidden when idle, visible while scrolling, wider and stronger under the pointer.
// The handle is the theme's text colour at 50%, which clears the 3:1 non-text contrast minimum
// against the background; the stock grey did not.
ScrollBar {
    id: bar
    property bool awake: false
    minimumSize: 0.06
    padding: 3

    onPositionChanged: { bar.awake = true; sleep.restart(); }
    Timer { id: sleep; interval: 900; onTriggered: bar.awake = false }

    // grabbing the bar must stop a coast, or the two fight over the list
    onPressedChanged: if (pressed && parent && typeof parent.stopPhysics === "function") parent.stopPhysics()

    contentItem: Rectangle {
        implicitWidth: bar.hovered || bar.pressed ? 9 : 6
        radius: width / 2
        color: Theme.fg
        opacity: bar.pressed ? 0.8 : bar.hovered ? 0.65 : bar.awake ? 0.5 : 0.0
        Behavior on opacity { NumberAnimation { duration: 200 } }
        Behavior on implicitWidth { NumberAnimation { duration: 120 } }
    }
    background: Item {}
}
