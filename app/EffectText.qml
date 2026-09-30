import QtQuick

// A message with iOS 18 text effects: each character is its own item so it can move. Text
// without effects never comes here (it stays a selectable TextEdit). Effects loop while the
// message is on screen, as they do on the phone.
//
// segments: [{text, styles: [bold italic underline strikethrough], effect: big|small|shake|nod|
//            explode|ripple|bloom|jitter|""}]
Flow {
    id: et
    property var segments: []
    property color color: "white"
    property font font
    property bool running: visible

    spacing: 0

    // Qt's JavaScript splits a string into UTF-16 halves; keep each emoji whole, with its
    // variation selector, skin tone and zero-width-joined parts.
    function graphemes(str) {
        const re = /(?:[\uD800-\uDBFF][\uDC00-\uDFFF]|[^\uD800-\uDFFF])(?:\uFE0F|\u20E3|\uD83C[\uDFFB-\uDFFF]|\u200D(?:[\uD800-\uDBFF][\uDC00-\uDFFF]|[^\uD800-\uDFFF])\uFE0F?)*/g;
        return str.match(re) || [];
    }

    // Words stay together (a Flow of single characters would wrap mid-word).
    readonly property var words: {
        const out = [];
        let cur = [];
        let idx = 0;
        for (const seg of et.segments) {
            for (const ch of et.graphemes(seg.text)) {
                const t = { ch: ch, styles: seg.styles || [], effect: seg.effect || "", i: idx++ };
                if (ch === " " || ch === "\n") {
                    if (cur.length) out.push(cur);
                    out.push([t]);
                    cur = [];
                } else cur.push(t);
            }
        }
        if (cur.length) out.push(cur);
        return out;
    }

    Repeater {
        model: et.words
        Row {
            required property var modelData
            // a newline ends the line
            width: modelData.length === 1 && modelData[0].ch === "\n" ? et.width : implicitWidth
            Repeater {
                model: parent.modelData
                Glyph {}
            }
        }
    }

    component Glyph: Text {
        id: g
        required property var modelData
        readonly property string fx: modelData.effect
        readonly property var st: modelData.styles
        text: modelData.ch === "\n" ? "" : modelData.ch
        color: et.color
        font.family: et.font.family
        font.pixelSize: et.font.pixelSize
        font.bold: st.indexOf("bold") >= 0
        font.italic: st.indexOf("italic") >= 0
        font.underline: st.indexOf("underline") >= 0
        font.strikeout: st.indexOf("strikethrough") >= 0
        transformOrigin: Item.Bottom
        transform: Translate { id: tr }
        readonly property int stagger: modelData.i * 45
        readonly property bool go: et.running && fx !== ""

        // Big / Small: the whole run swells or shrinks, then settles.
        SequentialAnimation {
            running: g.go && (g.fx === "big" || g.fx === "small")
            loops: Animation.Infinite
            NumberAnimation { target: g; property: "scale"; to: g.fx === "big" ? 1.7 : 0.55; duration: 380; easing.type: Easing.OutBack }
            PauseAnimation { duration: 700 }
            NumberAnimation { target: g; property: "scale"; to: 1; duration: 320; easing.type: Easing.InOutQuad }
            PauseAnimation { duration: 1400 }
        }
        // Shake: a quick side-to-side burst.
        SequentialAnimation {
            running: g.go && g.fx === "shake"
            loops: Animation.Infinite
            SequentialAnimation {
                loops: 4
                NumberAnimation { target: tr; property: "x"; to: -3; duration: 45 }
                NumberAnimation { target: tr; property: "x"; to: 3; duration: 45 }
            }
            NumberAnimation { target: tr; property: "x"; to: 0; duration: 40 }
            PauseAnimation { duration: 1300 }
        }
        // Nod: each letter dips and comes back, one after another.
        SequentialAnimation {
            running: g.go && g.fx === "nod"
            PauseAnimation { duration: g.stagger % 600 }
            SequentialAnimation {
                loops: Animation.Infinite
                NumberAnimation { target: tr; property: "y"; to: 4; duration: 160; easing.type: Easing.InOutSine }
                NumberAnimation { target: tr; property: "y"; to: -2; duration: 160; easing.type: Easing.InOutSine }
                NumberAnimation { target: tr; property: "y"; to: 0; duration: 140 }
                PauseAnimation { duration: 1200 }
            }
        }
        // Ripple: a wave travelling along the text.
        SequentialAnimation {
            running: g.go && g.fx === "ripple"
            PauseAnimation { duration: g.stagger % 900 }
            SequentialAnimation {
                loops: Animation.Infinite
                ParallelAnimation {
                    NumberAnimation { target: tr; property: "y"; to: -6; duration: 180; easing.type: Easing.OutSine }
                    NumberAnimation { target: g; property: "scale"; to: 1.25; duration: 180 }
                }
                ParallelAnimation {
                    NumberAnimation { target: tr; property: "y"; to: 0; duration: 220; easing.type: Easing.InSine }
                    NumberAnimation { target: g; property: "scale"; to: 1; duration: 220 }
                }
                PauseAnimation { duration: 1500 }
            }
        }
        // Bloom: letters open up from small and faint, one by one.
        SequentialAnimation {
            running: g.go && g.fx === "bloom"
            PauseAnimation { duration: g.stagger % 700 }
            SequentialAnimation {
                loops: Animation.Infinite
                ParallelAnimation {
                    NumberAnimation { target: g; property: "scale"; from: 0.3; to: 1.2; duration: 420; easing.type: Easing.OutQuad }
                    NumberAnimation { target: g; property: "opacity"; from: 0.2; to: 1; duration: 420 }
                }
                NumberAnimation { target: g; property: "scale"; to: 1; duration: 200 }
                PauseAnimation { duration: 1800 }
            }
        }
        // Explode: letters burst outward and fall back into place.
        readonly property real ex: (Math.random() - 0.5) * 60
        readonly property real ey: -10 - Math.random() * 30
        readonly property real er: (Math.random() - 0.5) * 120
        SequentialAnimation {
            running: g.go && g.fx === "explode"
            loops: Animation.Infinite
            ParallelAnimation {
                NumberAnimation { target: tr; property: "x"; to: g.ex; duration: 260; easing.type: Easing.OutQuad }
                NumberAnimation { target: tr; property: "y"; to: g.ey; duration: 260; easing.type: Easing.OutQuad }
                NumberAnimation { target: g; property: "rotation"; to: g.er; duration: 260 }
                NumberAnimation { target: g; property: "scale"; to: 1.4; duration: 260 }
            }
            ParallelAnimation {
                NumberAnimation { target: tr; property: "x"; to: 0; duration: 520; easing.type: Easing.OutBounce }
                NumberAnimation { target: tr; property: "y"; to: 0; duration: 520; easing.type: Easing.OutBounce }
                NumberAnimation { target: g; property: "rotation"; to: 0; duration: 520 }
                NumberAnimation { target: g; property: "scale"; to: 1; duration: 520 }
            }
            PauseAnimation { duration: 1600 }
        }
        // Jitter: never quite still.
        SequentialAnimation {
            running: g.go && g.fx === "jitter"
            loops: Animation.Infinite
            NumberAnimation { target: tr; property: "x"; to: 1.4; duration: 70 }
            NumberAnimation { target: tr; property: "y"; to: -1.2; duration: 70 }
            NumberAnimation { target: tr; property: "x"; to: -1.3; duration: 70 }
            NumberAnimation { target: tr; property: "y"; to: 1.1; duration: 70 }
        }
    }
}
