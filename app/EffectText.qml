import QtQuick
import QtQuick.Effects
import Quickshell

// A message with iOS 18 text effects. Each character is its own item so it can move; text
// without effects never comes here (it stays a selectable TextEdit).
//
// Messages animates these with springs - things overshoot, bounce and settle - so every effect
// here is written as a function of time built from a damped spring, evaluated for each letter
// from one shared per-frame clock. An effect performs, rests a moment, and performs again for
// as long as the message is on screen (Jitter never rests: it's a tremble).
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

    // The text's width on one line, as it is right now (it changes while Big/Small play).
    readonly property real naturalWidth: {
        let w = 0;
        for (let k = 0; k < children.length; k++) w += children[k].width || 0;
        return w;
    }

    // ---- the clock: seconds since this message started animating
    property real t: 0
    property real started: 0
    readonly property real frozen: Number(Quickshell.env("PEAR_MESSAGES_FX_T") || -1)
    FrameAnimation {
        running: et.running && et.hasEffect
        onRunningChanged: if (running && et.started === 0) et.started = Date.now()
        // PEAR_MESSAGES_FX_T=<seconds> freezes the clock there (for checking a pose off-screen)
        onTriggered: et.t = et.frozen >= 0 ? et.frozen : (Date.now() - et.started) / 1000
    }
    readonly property bool hasEffect: segments.some(s => !!s.effect)

    // ---- springs
    // Step response of a damped spring released at time 0: 0 -> 1 with overshoot.
    // f = natural frequency (Hz), z = damping ratio (< 1 bounces).
    function spring(t, f, z) {
        if (t <= 0) return 0;
        const w = 2 * Math.PI * f;
        if (z >= 1) return 1 - Math.exp(-w * t) * (1 + w * t);
        const wd = w * Math.sqrt(1 - z * z);
        return 1 - Math.exp(-z * w * t) * (Math.cos(wd * t) + (z * w / wd) * Math.sin(wd * t));
    }
    // Out, hold, back: spring to 1, and at `hold` spring back to 0.
    function pulse(t, hold, f, z, fBack, zBack) {
        return spring(t, f, z) - spring(t - hold, fBack || f, zBack || z);
    }
    // A damped oscillation kicked at time 0 (for shake / nod).
    function wobble(t, f, decay) {
        return t <= 0 ? 0 : Math.exp(-decay * t) * Math.sin(2 * Math.PI * f * t);
    }
    // Smooth pseudo-random wander in [-1, 1] (jitter).
    function noise(seed, t) {
        return (Math.sin(t * 13.1 + seed * 1.7) * 0.5 + Math.sin(t * 23.7 + seed * 4.1) * 0.3
                + Math.sin(t * 41.3 + seed * 7.3) * 0.2);
    }

    // How long one performance lasts before it repeats, per effect, for a run of n letters.
    function period(fx, n) {
        switch (fx) {
        case "big": case "small": return 1.1 + n * 0.03 + 1.3;
        case "shake": return 1.0 + 1.4;
        case "nod": return 1.1 + 1.3;
        case "explode": return 1.3 + 1.5;
        case "ripple": return 0.5 + n * 0.055 + 1.1;
        case "bloom": return 0.9 + n * 0.06 + 1.1;
        }
        return 1e9;
    }

    // Words stay together (a Flow of single characters would wrap mid-word). Each letter knows
    // its place in its own effect run (for staggering) and gets its own random seed.
    readonly property var layout: {
        const out = [];
        const runs = [];
        let cur = [];
        let prevFx = null, runIdx = 0;
        for (const seg of et.segments) {
            const fx = seg.effect || "";
            if (fx !== prevFx) { runIdx = 0; runs.push({ fx: fx, n: 0 }); prevFx = fx; }
            for (const ch of et.graphemes(seg.text)) {
                const run = runs[runs.length - 1];
                const t = { ch: ch, styles: seg.styles || [], effect: fx, i: runIdx++, run: runs.length - 1,
                            seed: Math.random() * 1000, dir: Math.random() * Math.PI * 2,
                            dist: 0.6 + Math.random() * 0.8, spin: (Math.random() - 0.5) * 70 };
                run.n = runIdx;
                if (ch === " " || ch === "\n") {
                    if (cur.length) out.push(cur);
                    out.push([t]);
                    cur = [];
                } else cur.push(t);
            }
        }
        if (cur.length) out.push(cur);
        return { words: out, runs: runs };
    }
    readonly property var words: layout.words
    readonly property var runs: layout.runs

    // Qt's JavaScript splits a string into UTF-16 halves; keep each emoji whole, with its
    // variation selector, skin tone and zero-width-joined parts.
    function graphemes(str) {
        const re = /(?:[\uD800-\uDBFF][\uDC00-\uDFFF]|[^\uD800-\uDFFF])(?:️|⃣|\uD83C[\uDFFB-\uDFFF]|‍(?:[\uD800-\uDBFF][\uDC00-\uDFFF]|[^\uD800-\uDFFF])️?)*/g;
        return str.match(re) || [];
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

    component Glyph: Item {
        id: g
        required property var modelData
        readonly property var m: modelData
        readonly property string fx: m.effect
        readonly property var st: m.styles
        // Big and Small change the letter's real size - neighbours make room and the bubble grows,
        // as on the phone. Other effects only move the letter where it stands.
        readonly property bool resizes: fx === "big" || fx === "small"
        width: label.implicitWidth * (resizes ? pose[2] : 1)
        height: label.implicitHeight * (resizes ? Math.max(1, pose[2]) : 1)

        // Where this letter is in its performance.
        readonly property int n: (et.runs[m.run] || { n: 1 }).n
        readonly property real local: fx ? (et.t % et.period(fx, n)) : 0

        // ---- the motion, per effect: [dx, dy, scale, rotation, glow]
        readonly property var pose: {
            const t = local, i = m.i;
            switch (fx) {
            case "big": {
                const p = et.pulse(t - i * 0.03, 0.75, 3.2, 0.32, 3.0, 0.5);
                return [0, 0, 1 + 0.95 * p, 0, 0];
            }
            case "small": {
                const p = et.pulse(t - i * 0.03, 0.75, 3.2, 0.4, 3.0, 0.5);
                return [0, 0, 1 - 0.55 * p, 0, 0];
            }
            case "shake": {
                const w = et.wobble(t, 7, 3.4);
                return [9 * w, 0, 1, 4 * w, 0];
            }
            case "nod": {
                const w = et.wobble(t - i * 0.012, 3.2, 3.0);
                return [0, 7 * w, 1, 0, 0];
            }
            case "explode": {
                // letters puff up and burst outward a little, then snap back and settle
                const p = et.pulse(t - i * 0.008, 0.38, 3.0, 0.55, 2.4, 0.36);
                const d = 20 * m.dist * p;
                return [Math.cos(m.dir) * d, Math.sin(m.dir) * d * 0.8 - 4 * p, 1 + 0.85 * p, m.spin * 0.6 * p, 0];
            }
            case "ripple": {
                const ti = t - i * 0.055;
                const b = ti > 0 && ti < 0.42 ? Math.sin(Math.PI * ti / 0.42) : 0;
                const settle = ti >= 0.42 ? et.wobble(ti - 0.42, 4, 7) * 0.25 : 0;
                return [0, -10 * (b - settle), 1 + 0.32 * b, 0, 0];
            }
            case "bloom": {
                const p = et.pulse(t - i * 0.06, 0.5, 2.4, 0.5, 2.0, 0.8);
                return [0, -2 * p, 1 + 0.42 * p, 0, Math.max(0, p)];
            }
            case "jitter":
                return [1.8 * et.noise(m.seed, et.t), 1.6 * et.noise(m.seed + 50, et.t), 1,
                        5 * et.noise(m.seed + 100, et.t * 0.8), 0];
            }
            return [0, 0, 1, 0, 0];
        }

        // Bloom's glow: the letter, blurred and brightened, behind itself.
        Text {
            textFormat: Text.PlainText
            id: glowText
            visible: g.fx === "bloom" && g.pose[4] > 0.02
            anchors.centerIn: label
            text: label.text
            font: label.font
            color: Qt.lighter(et.color, 1.4)
            opacity: 0.85 * g.pose[4]
            scale: label.scale * (1 + 0.25 * g.pose[4])
            layer.enabled: visible
            layer.effect: MultiEffect { blurEnabled: true; blur: 1.0; blurMax: 20; brightness: 0.3 }
        }
        Text {
            textFormat: Text.PlainText
            id: label
            text: g.m.ch === "\n" ? "" : g.m.ch
            color: et.color
            font.family: et.font.family
            font.pixelSize: et.font.pixelSize
            font.bold: g.st.indexOf("bold") >= 0
            font.italic: g.st.indexOf("italic") >= 0
            font.underline: g.st.indexOf("underline") >= 0
            font.strikeout: g.st.indexOf("strikethrough") >= 0
            x: (g.width - implicitWidth) / 2 + g.pose[0]
            y: g.height - implicitHeight + g.pose[1]
            // letters grow from their baseline, as on the phone
            transformOrigin: Item.Bottom
            scale: g.pose[2]
            rotation: g.pose[3]
            antialiasing: true
        }
    }
}
