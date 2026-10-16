import QtQuick
import Quickshell.Io
import "TextEdit.js" as Edit

// Paste for a read-only field (see TextEdit.js): the clipboard's text, read by wl-paste,
// at most `limit` characters and for at most `deadline` ms. Whatever came in by then is
// what gets pasted; the rest is never read.
Process {
    id: paste
    property Item field
    property bool multiline: false
    property int limit: 4096
    property int deadline: 2000
    property string got: ""
    property bool over: false

    function start() {
        if (running) return;
        got = "";
        over = false;
        running = true;
        timeout.restart();
    }

    command: ["wl-paste", "--no-newline", "--type", "text/plain;charset=utf-8"]
    stdout: SplitParser {
        splitMarker: ""
        onRead: function (data) {
            if (paste.over) return;
            paste.got += data;
            if (paste.got.length > paste.limit) {
                paste.over = true;
                paste.got = paste.got.slice(0, paste.limit);
                paste.running = false;
            }
        }
    }
    onExited: {
        timeout.stop();
        let t = got;
        got = "";
        if (!t || !field) return;
        if (!multiline) t = t.replace(/\r?\n/g, " ");
        Edit.insert(field, t);
    }
    property Timer timeout: Timer {
        interval: paste.deadline
        onTriggered: if (paste.running) { paste.over = true; paste.running = false; }
    }
}
