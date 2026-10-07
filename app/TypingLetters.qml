import QtQuick

// Animated typing for a TextArea, the same feel as hyprdrive's overview search (itself a port of
// Serpantinum's Input.qml): a typed letter pops in (420 ms, scale 0.3 -> 1, rising 10 px, OutBack
// 3.2), a deleted one shrinks away (160 ms, InBack, to 0.3, lifting 8 px), and letters pushed
// along by an edit glide to their new place (260 ms, OutBack 1.6).
//
// The TextArea keeps doing everything real - editing, the cursor, selection, wrapping - with its
// own text drawn transparent; this draws each letter on top, at exactly the place the TextArea
// lays it out (positionToRectangle). Declare it inside the TextArea and set `field` to it.
Item {
    id: tl
    anchors.fill: parent
    required property TextEdit field
    property bool enabled: true
    property color color: "white"
    readonly property bool active: enabled && graphemes.length <= 400

    property var graphemes: []      // [{ch, at}] - each grapheme and its UTF-16 index
    property int nextKey: 1
    property int layoutTick: 0      // bump to re-read positions (wrapping, scrolling, resizing)

    function split(str) {
        const re = /(?:[\uD800-\uDBFF][\uDC00-\uDFFF]|[^\uD800-\uDFFF])(?:️|⃣|\uD83C[\uDFFB-\uDFFF]|‍(?:[\uD800-\uDBFF][\uDC00-\uDFFF]|[^\uD800-\uDFFF])️?)*/g;
        const out = [];
        let m;
        while ((m = re.exec(str)) !== null) out.push({ ch: m[0], at: m.index });
        return out;
    }

    // Work out what changed (common head and tail) and update the letter list in place, so the
    // letters that stayed keep their items - and their animations.
    function sync() {
        const now = split(field.text);
        const before = graphemes;
        let a = 0;
        while (a < before.length && a < now.length && before[a].ch === now[a].ch) a++;
        let b = 0;
        while (b < before.length - a && b < now.length - a
               && before[before.length - 1 - b].ch === now[now.length - 1 - b].ch) b++;
        const removed = before.length - a - b;
        const added = now.length - a - b;
        // the gone ones leave as ghosts, from where they were
        // (from where each letter was last drawn: by now the text has changed, and asking the
        // field where a removed letter was is asking for a position that may no longer exist -
        // clearing the box after a send asked for every one of them)
        for (let k = 0; k < removed && k < 40; k++) {
            const r = before[a + k].rect;
            if (r) ghosts.append({ ch: before[a + k].ch, gx: r.x, gy: r.y, gh: r.height, key: nextKey++ });
        }
        // the new layout first, so every letter reads its place from the text as it is now
        graphemes = now;
        if (removed > 0) letters.remove(a, removed);
        const animate = added <= 40;          // a paste just appears
        for (let k = 0; k < added; k++)
            letters.insert(a + k, { ch: now[a + k].ch, key: nextKey++, fresh: animate });
        layoutTick++;
    }

    Connections {
        target: tl.field
        function onTextChanged() { if (tl.active || tl.letters.count) tl.sync(); }
        function onWidthChanged() { tl.layoutTick++; }
        function onContentHeightChanged() { tl.layoutTick++; }
    }
    onEnabledChanged: { letters.clear(); ghosts.clear(); graphemes = []; if (enabled) sync(); }
    Component.onCompleted: sync()

    property alias letters: letters
    ListModel { id: letters }
    ListModel { id: ghosts }

    Repeater {
        model: tl.active ? letters : null
        Text {
            textFormat: Text.PlainText
            id: glyph
            required property int index
            required property string ch
            required property bool fresh
            readonly property var g: index >= 0 && index < tl.graphemes.length ? tl.graphemes[index] : null
            readonly property rect r: (tl.layoutTick, g && g.at <= tl.field.length ? tl.field.positionToRectangle(g.at) : Qt.rect(x, y, 0, 0))
            // remembered on the grapheme, for the ghost it leaves if it is deleted
            onRChanged: if (g) g.rect = r
            text: ch === "\n" ? "" : ch
            color: tl.color
            font: tl.field.font
            x: r.x
            y: r.y + (r.height - implicitHeight) / 2
            transformOrigin: Item.Bottom
            transform: Translate { id: lift }
            property bool settled: false
            Behavior on x { enabled: glyph.settled; NumberAnimation { duration: 260; easing.type: Easing.OutBack; easing.overshoot: 1.6 } }
            Behavior on y { enabled: glyph.settled; NumberAnimation { duration: 260; easing.type: Easing.OutBack; easing.overshoot: 1.6 } }
            Component.onCompleted: {
                if (fresh) pop.start();
                Qt.callLater(() => glyph.settled = true);
            }
            ParallelAnimation {
                id: pop
                NumberAnimation { target: glyph; property: "scale"; from: 0.3; to: 1; duration: 420; easing.type: Easing.OutBack; easing.overshoot: 3.2 }
                NumberAnimation { target: lift; property: "y"; from: 10; to: 0; duration: 420; easing.type: Easing.OutBack; easing.overshoot: 3.2 }
                NumberAnimation { target: glyph; property: "opacity"; from: 0; to: 1; duration: 120 }
            }
        }
    }
    Repeater {
        model: tl.active ? ghosts : null
        Text {
            textFormat: Text.PlainText
            id: ghost
            required property string ch
            required property real gx
            required property real gy
            required property real gh
            required property int key
            text: ch
            color: tl.color
            font: tl.field.font
            x: gx
            y: gy + (gh - implicitHeight) / 2
            transformOrigin: Item.Bottom
            transform: Translate { id: rise }
            SequentialAnimation {
                running: true
                ParallelAnimation {
                    NumberAnimation { target: ghost; property: "scale"; to: 0.3; duration: 160; easing.type: Easing.InBack }
                    NumberAnimation { target: rise; property: "y"; to: -8; duration: 160; easing.type: Easing.InBack }
                    NumberAnimation { target: ghost; property: "opacity"; to: 0; duration: 160 }
                }
                ScriptAction {
                    script: {
                        for (let i = 0; i < ghosts.count; i++)
                            if (ghosts.get(i).key === ghost.key) { ghosts.remove(i); break; }
                    }
                }
            }
        }
    }
}
