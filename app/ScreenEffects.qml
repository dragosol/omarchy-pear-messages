import QtQuick
import QtQuick.Particles

// iMessage screen effects, drawn over the conversation (never the sidebar), and never taking
// a click. play(id, text) runs one.
//
// Every effect has the same envelope: the layer fades in, particles are emitted for the first
// part, emission stops so what's in the air finishes its flight, and the whole layer fades
// out before it disappears - nothing just blinks off. The pieces are rendered sprites (fx/,
// made by tools/make_fx_sprites.py) moved by real physics: gravity, air drag, drift.
Item {
    id: fx
    anchors.fill: parent
    clip: true
    z: 50
    opacity: 0
    visible: current !== ""

    property string current: ""
    property string text: ""
    property int nonce: 0
    property bool emitting: false

    readonly property var colors: ["red", "orange", "yellow", "green", "blue", "purple", "pink"]
    readonly property var hex: ({ red: "#ff5c5c", orange: "#ffaa3c", yellow: "#ffe150", green: "#5ce07a",
                                  blue: "#4db5ff", purple: "#a66bff", pink: "#ff6bd0" })
    readonly property var names: ({
        "com.apple.messages.effect.CKEchoEffect": "echo",
        "com.apple.messages.effect.CKSpotlightEffect": "spotlight",
        "com.apple.messages.effect.CKHappyBirthdayEffect": "balloons",
        "com.apple.messages.effect.CKConfettiEffect": "confetti",
        "com.apple.messages.effect.CKHeartEffect": "love",
        "com.apple.messages.effect.CKLasersEffect": "lasers",
        "com.apple.messages.effect.CKFireworksEffect": "fireworks",
        "com.apple.messages.effect.CKSparklesEffect": "celebration",
        "com.apple.messages.effect.CKShootingStarEffect": "shootingstar"
    })
    // [total ms, emitting ms]
    readonly property var timing: ({ confetti: [4400, 1500], balloons: [6000, 2600], fireworks: [4800, 0],
                                     celebration: [4000, 1900], love: [3800, 2000], lasers: [3800, 0],
                                     spotlight: [3600, 0], echo: [3800, 1400], shootingstar: [3600, 0] })

    function isScreen(id) { return !!names[id]; }
    function rnd(a, b) { return a + Math.random() * (b - a); }
    function pick(list) { return list[Math.floor(Math.random() * list.length)]; }
    function sprite(kind, color) { return Qt.resolvedUrl("fx/" + kind + "-" + color + ".png"); }

    function play(id, msgText) {
        const n = names[id];
        if (!n) return;
        finish();
        fx.text = msgText || "";
        fx.current = n;
        fx.nonce++;
        const t = timing[n];
        fx.emitting = t[1] > 0;
        emitTimer.interval = Math.max(1, t[1]);
        emitTimer.restart();
        outTimer.interval = t[0] - fadeOut.duration;
        outTimer.restart();
        fadeOut.stop();
        fadeIn.restart();
        if (n === "confetti") { cannonL.burst(70); cannonR.burst(70); }
        if (n === "balloons") balloonEmitter.burst(5);
        if (n === "fireworks") rockets.launch();
        if (n === "echo") echo.start();
    }
    function finish() {
        emitTimer.stop(); outTimer.stop(); fadeIn.stop(); fadeOut.stop();
        echo.stop();
        rockets.stop();
        sys.reset();
        fx.emitting = false;
        fx.opacity = 0;
        fx.current = "";
    }

    NumberAnimation { id: fadeIn; target: fx; property: "opacity"; to: 1; duration: 220; easing.type: Easing.OutQuad }
    NumberAnimation {
        id: fadeOut; target: fx; property: "opacity"; to: 0; duration: 850; easing.type: Easing.InOutQuad
        onFinished: fx.finish()
    }
    Timer { id: emitTimer; onTriggered: fx.emitting = false }
    Timer { id: outTimer; onTriggered: { fx.emitting = false; fadeOut.restart(); } }

    ParticleSystem { id: sys; running: fx.current !== "" }

    // =============================================================== confetti
    // Paper falling from above, plus two cannons at the bottom corners. Each piece flips as it
    // falls (its width follows cos of a spinning angle, and it darkens edge-on), drifts, and
    // meets air resistance, so it flutters down instead of dropping.
    Emitter {
        system: sys; group: "confetti"
        enabled: fx.current === "confetti" && fx.emitting
        x: -40; y: -30; width: fx.width + 80; height: 1
        emitRate: 150; lifeSpan: 3800; lifeSpanVariation: 600
        velocity: AngleDirection { angle: 90; angleVariation: 25; magnitude: 140; magnitudeVariation: 90 }
    }
    Emitter {
        id: cannonL
        system: sys; group: "confetti"; enabled: false
        x: 0; y: fx.height; width: 1; height: 1
        lifeSpan: 3800; lifeSpanVariation: 500
        velocity: AngleDirection { angle: 295; angleVariation: 14; magnitude: 820; magnitudeVariation: 220 }
    }
    Emitter {
        id: cannonR
        system: sys; group: "confetti"; enabled: false
        x: fx.width; y: fx.height; width: 1; height: 1
        lifeSpan: 3800; lifeSpanVariation: 500
        velocity: AngleDirection { angle: 245; angleVariation: 14; magnitude: 820; magnitudeVariation: 220 }
    }
    Gravity { system: sys; groups: ["confetti"]; angle: 90; magnitude: 420 }
    Friction { system: sys; groups: ["confetti"]; factor: 1.6; threshold: 70 }
    Wander { system: sys; groups: ["confetti"]; xVariance: 60; pace: 140; affectedParameter: Wander.Velocity }
    ItemParticle {
        system: sys; groups: ["confetti"]; fade: true
        delegate: Item {
            id: paper
            readonly property color c: fx.hex[fx.pick(fx.colors)]
            readonly property bool round: Math.random() < 0.25
            width: round ? 8 : fx.rnd(6, 9); height: round ? 8 : fx.rnd(11, 16)
            property real phase: 0
            NumberAnimation on phase { from: 0; to: Math.PI * 2 * (Math.random() < 0.5 ? 1 : -1); duration: fx.rnd(450, 1000); loops: Animation.Infinite }
            RotationAnimation on rotation { from: fx.rnd(0, 360); to: fx.rnd(0, 360) + fx.pick([360, -360]); duration: fx.rnd(1400, 2600); loops: Animation.Infinite }
            Rectangle {
                anchors.fill: parent
                radius: paper.round ? width / 2 : 1.5
                color: Qt.darker(paper.c, 1 + 0.6 * (1 - Math.abs(Math.cos(paper.phase))))
                transform: Scale { origin.x: paper.width / 2; origin.y: paper.height / 2; xScale: Math.max(0.08, Math.abs(Math.cos(paper.phase))) }
            }
        }
    }

    // =============================================================== balloons
    Emitter {
        id: balloonEmitter
        system: sys; group: "balloons"
        enabled: fx.current === "balloons" && fx.emitting
        x: 20; y: fx.height + 30; width: fx.width - 40; height: 1
        emitRate: 5; lifeSpan: 6200
        velocity: AngleDirection { angle: 270; angleVariation: 5; magnitude: 175; magnitudeVariation: 45 }
    }
    Wander { system: sys; groups: ["balloons"]; xVariance: 26; pace: 40; affectedParameter: Wander.Velocity }
    ItemParticle {
        system: sys; groups: ["balloons"]; fade: false
        delegate: Image {
            readonly property real s: fx.rnd(0.8, 1.25)
            source: fx.sprite("balloon", fx.pick(fx.colors))
            width: 64 * s; height: width * 340 / 180
            z: s
            smooth: true; mipmap: true
            transformOrigin: Item.Bottom
            SequentialAnimation on rotation {
                loops: Animation.Infinite
                NumberAnimation { from: -5; to: 5; duration: fx.rnd(1100, 1600); easing.type: Easing.InOutSine }
                NumberAnimation { from: 5; to: -5; duration: fx.rnd(1100, 1600); easing.type: Easing.InOutSine }
            }
        }
    }

    // =============================================================== fireworks
    Rectangle { anchors.fill: parent; color: "black"; opacity: fx.current === "fireworks" ? 0.35 : 0 }
    Item {
        id: rockets
        anchors.fill: parent
        property var plan: []
        function launch() {
            plan = [0, 380, 820, 1250, 1750, 2250];
            for (let i = 0; i < rocketRepeater.count; i++) rocketRepeater.itemAt(i).go(plan[i]);
        }
        function stop() { for (let i = 0; i < rocketRepeater.count; i++) rocketRepeater.itemAt(i).halt(); }
        Repeater {
            id: rocketRepeater
            model: 6
            Item {
                id: rocket
                width: 1; height: 1
                visible: false
                property string color: "gold"
                property real tx: 0
                property real ty: 0
                function go(delay) {
                    color = fx.pick(fx.colors);
                    tx = fx.rnd(fx.width * 0.15, fx.width * 0.85);
                    ty = fx.rnd(fx.height * 0.12, fx.height * 0.45);
                    x = tx + fx.rnd(-40, 40); y = fx.height + 10;
                    wait.duration = delay;
                    flight.restart();
                }
                function halt() { flight.stop(); visible = false; }
                Image { anchors.centerIn: parent; width: 22; height: 22; source: fx.sprite("glow", "white") }
                Emitter {
                    system: sys; group: "trail"
                    enabled: rocket.visible
                    anchors.centerIn: parent; width: 2; height: 2
                    emitRate: 90; lifeSpan: 450; lifeSpanVariation: 150
                    velocity: AngleDirection { angle: 90; angleVariation: 25; magnitude: 40 }
                }
                SequentialAnimation {
                    id: flight
                    PauseAnimation { id: wait; duration: 0 }
                    PropertyAction { target: rocket; property: "visible"; value: true }
                    ParallelAnimation {
                        NumberAnimation { target: rocket; property: "y"; to: rocket.ty; duration: 620; easing.type: Easing.OutQuad }
                        NumberAnimation { target: rocket; property: "x"; to: rocket.tx; duration: 620; easing.type: Easing.OutQuad }
                    }
                    ScriptAction {
                        script: {
                            rocket.visible = false;
                            fx.burstColor = rocket.color;
                            burstEmitter.x = rocket.x; burstEmitter.y = rocket.y;
                            burstEmitter.burst(150);
                            flash.x = rocket.x - flash.width / 2; flash.y = rocket.y - flash.height / 2;
                            flash.source = fx.sprite("glow", rocket.color);
                            flashAnim.restart();
                        }
                    }
                }
            }
        }
    }
    property string burstColor: "gold"
    Emitter {
        id: burstEmitter
        system: sys; group: "spark"; enabled: false
        width: 2; height: 2
        lifeSpan: 1600; lifeSpanVariation: 400
        velocity: AngleDirection { angle: 0; angleVariation: 360; magnitude: 300; magnitudeVariation: 60 }
    }
    Friction { system: sys; groups: ["spark"]; factor: 2.2 }
    Gravity { system: sys; groups: ["spark", "trail"]; angle: 90; magnitude: 70 }
    ItemParticle {
        system: sys; groups: ["spark"]; fade: true
        delegate: Image {
            Component.onCompleted: source = fx.sprite("glow", Math.random() < 0.15 ? "white" : fx.burstColor)
            width: fx.rnd(12, 22); height: width
            SequentialAnimation on opacity {
                loops: Animation.Infinite
                PauseAnimation { duration: fx.rnd(300, 700) }
                NumberAnimation { to: 0.35; duration: fx.rnd(80, 160) }
                NumberAnimation { to: 1; duration: fx.rnd(80, 160) }
            }
        }
    }
    ItemParticle {
        system: sys; groups: ["trail"]; fade: true
        delegate: Image { source: fx.sprite("glow", "gold"); width: fx.rnd(6, 11); height: width }
    }
    Image {
        id: flash
        width: 160; height: 160
        opacity: 0
        ParallelAnimation {
            id: flashAnim
            NumberAnimation { target: flash; property: "scale"; from: 0.3; to: 1.6; duration: 420; easing.type: Easing.OutQuad }
            NumberAnimation { target: flash; property: "opacity"; from: 0.95; to: 0; duration: 420 }
        }
    }

    // =============================================================== celebration
    // Gold sparkles pouring from the top corner, slowing as they spread, twinkling.
    Emitter {
        system: sys; group: "gold"
        enabled: fx.current === "celebration" && fx.emitting
        x: fx.width - 16; y: -16; width: 24; height: 24
        emitRate: 110; lifeSpan: 2400; lifeSpanVariation: 600
        velocity: AngleDirection { angle: 140; angleVariation: 28; magnitude: 440; magnitudeVariation: 200 }
    }
    Friction { system: sys; groups: ["gold"]; factor: 1.1 }
    Gravity { system: sys; groups: ["gold"]; angle: 90; magnitude: 60 }
    ItemParticle {
        system: sys; groups: ["gold"]; fade: true
        delegate: Image {
            readonly property bool star: Math.random() < 0.55
            source: star ? Qt.resolvedUrl("fx/sparkle-gold.png") : fx.sprite("glow", "gold")
            width: star ? fx.rnd(14, 30) : fx.rnd(8, 16); height: width
            RotationAnimation on rotation { from: 0; to: fx.pick([90, -90]); duration: fx.rnd(900, 1800); loops: Animation.Infinite }
            SequentialAnimation on scale {
                loops: Animation.Infinite
                NumberAnimation { to: 0.55; duration: fx.rnd(180, 380); easing.type: Easing.InOutSine }
                NumberAnimation { to: 1.1; duration: fx.rnd(180, 380); easing.type: Easing.InOutSine }
            }
        }
    }

    // =============================================================== love
    Emitter {
        system: sys; group: "hearts"
        enabled: fx.current === "love" && fx.emitting
        x: fx.width * 0.28; y: fx.height * 0.62; width: fx.width * 0.44; height: 10
        emitRate: 16; lifeSpan: 2600; lifeSpanVariation: 400
        velocity: AngleDirection { angle: 270; angleVariation: 20; magnitude: 95; magnitudeVariation: 40 }
    }
    Wander { system: sys; groups: ["hearts"]; xVariance: 30; pace: 60; affectedParameter: Wander.Velocity }
    ItemParticle {
        system: sys; groups: ["hearts"]; fade: true
        delegate: Image {
            source: Qt.resolvedUrl("fx/heart-small.png")
            width: fx.rnd(16, 32); height: width
            rotation: fx.rnd(-18, 18)
            mipmap: true
        }
    }
    Image {
        id: bigHeart
        source: Qt.resolvedUrl("fx/heart.png")
        width: Math.min(fx.width, fx.height) * 0.55; height: width
        anchors.centerIn: parent
        mipmap: true
        visible: fx.current === "love"
        scale: 0
        property int n: fx.nonce
        onNChanged: if (fx.current === "love") heartAnim.restart()
        transform: Translate { id: heartLift }
        SequentialAnimation {
            id: heartAnim
            PropertyAction { target: heartLift; property: "y"; value: 0 }
            PropertyAction { target: bigHeart; property: "opacity"; value: 1 }
            NumberAnimation { target: bigHeart; property: "scale"; from: 0; to: 1; duration: 620; easing.type: Easing.OutBack; easing.overshoot: 2 }
            SequentialAnimation {
                loops: 2
                NumberAnimation { target: bigHeart; property: "scale"; to: 1.12; duration: 150; easing.type: Easing.OutQuad }
                NumberAnimation { target: bigHeart; property: "scale"; to: 1.0; duration: 240; easing.type: Easing.InOutQuad }
            }
            PauseAnimation { duration: 500 }
            ParallelAnimation {
                NumberAnimation { target: heartLift; property: "y"; to: -60; duration: 1100; easing.type: Easing.InQuad }
                NumberAnimation { target: bigHeart; property: "scale"; to: 0.85; duration: 1100 }
                NumberAnimation { target: bigHeart; property: "opacity"; to: 0; duration: 1100; easing.type: Easing.InQuad }
            }
        }
    }

    // =============================================================== lasers
    Rectangle { anchors.fill: parent; color: "black"; opacity: fx.current === "lasers" ? 0.72 : 0 }
    Item {
        anchors.fill: parent
        visible: fx.current === "lasers"
        Repeater {
            model: fx.current === "lasers" ? 8 : 0
            Item {
                id: beam
                required property int index
                x: fx.width / 2; y: fx.height * 0.42
                width: Math.hypot(fx.width, fx.height); height: 1
                transformOrigin: Item.Left
                property color c: fx.hex[fx.colors[index % fx.colors.length]]
                Rectangle {   // glow
                    y: -7; width: parent.width; height: 14; radius: 7
                    gradient: Gradient { orientation: Gradient.Horizontal
                        GradientStop { position: 0; color: Qt.rgba(beam.c.r, beam.c.g, beam.c.b, 0.55) }
                        GradientStop { position: 1; color: Qt.rgba(beam.c.r, beam.c.g, beam.c.b, 0) } }
                }
                Rectangle {   // core
                    y: -1.5; width: parent.width; height: 3; radius: 1.5
                    gradient: Gradient { orientation: Gradient.Horizontal
                        GradientStop { position: 0; color: Qt.lighter(beam.c, 1.6) }
                        GradientStop { position: 1; color: Qt.rgba(beam.c.r, beam.c.g, beam.c.b, 0) } }
                }
                SequentialAnimation on rotation {
                    loops: 2
                    NumberAnimation { from: beam.index * 45; to: beam.index * 45 + (beam.index % 2 ? 110 : -110); duration: 900; easing.type: Easing.InOutSine }
                    NumberAnimation { to: beam.index * 45; duration: 900; easing.type: Easing.InOutSine }
                }
                SequentialAnimation on c {
                    loops: Animation.Infinite
                    ColorAnimation { to: fx.hex[fx.colors[(beam.index + 2) % 7]]; duration: 500 }
                    ColorAnimation { to: fx.hex[fx.colors[(beam.index + 4) % 7]]; duration: 500 }
                    ColorAnimation { to: fx.hex[fx.colors[beam.index % 7]]; duration: 500 }
                }
            }
        }
        Image {   // the source of the beams
            x: fx.width / 2 - width / 2; y: fx.height * 0.42 - height / 2
            width: 90; height: 90
            source: fx.sprite("glow", "white")
            SequentialAnimation on scale {
                loops: Animation.Infinite
                NumberAnimation { to: 1.35; duration: 220 }
                NumberAnimation { to: 0.9; duration: 220 }
            }
        }
    }

    // =============================================================== spotlight
    Canvas {
        id: spot
        anchors.fill: parent
        visible: fx.current === "spotlight"
        property real cx: width / 2
        property real cy: height / 2
        property real r: Math.min(width, height) * 0.24
        property real dark: 0
        onCxChanged: requestPaint()
        onCyChanged: requestPaint()
        onDarkChanged: requestPaint()
        onPaint: {
            const g = getContext("2d");
            g.reset();
            g.fillStyle = Qt.rgba(0, 0, 0, dark);
            g.fillRect(0, 0, width, height);
            g.globalCompositeOperation = "destination-out";
            const grad = g.createRadialGradient(cx, cy, r * 0.45, cx, cy, r);
            grad.addColorStop(0, "rgba(0,0,0,1)");
            grad.addColorStop(1, "rgba(0,0,0,0)");
            g.fillStyle = grad;
            g.beginPath(); g.arc(cx, cy, r, 0, Math.PI * 2); g.fill();
        }
        property int n: fx.nonce
        onNChanged: if (fx.current === "spotlight") spotAnim.restart()
        SequentialAnimation {
            id: spotAnim
            PropertyAction { target: spot; property: "cx"; value: spot.width * 0.15 }
            PropertyAction { target: spot; property: "cy"; value: spot.height * 0.2 }
            NumberAnimation { target: spot; property: "dark"; from: 0; to: 0.8; duration: 450; easing.type: Easing.OutQuad }
            ParallelAnimation {
                NumberAnimation { target: spot; property: "cx"; to: spot.width * 0.72; duration: 1100; easing.type: Easing.InOutSine }
                NumberAnimation { target: spot; property: "cy"; to: spot.height * 0.7; duration: 1100; easing.type: Easing.InOutSine }
            }
            ParallelAnimation {
                NumberAnimation { target: spot; property: "cx"; to: spot.width * 0.5; duration: 700; easing.type: Easing.InOutSine }
                NumberAnimation { target: spot; property: "cy"; to: spot.height * 0.5; duration: 700; easing.type: Easing.InOutSine }
            }
        }
    }

    // =============================================================== echo
    // The message itself, in bubbles fanning out from the middle.
    Item {
        id: echo
        anchors.fill: parent
        visible: fx.current === "echo"
        function start() { echoModel.clear(); echoTimer.start(); }
        function stop() { echoTimer.stop(); echoModel.clear(); }
        Timer {
            id: echoTimer
            interval: 45; repeat: true
            onTriggered: {
                if (!fx.emitting || echoModel.count > 36) { stop(); return; }
                echoModel.append({ ex: fx.rnd(0.02, 0.8), ey: fx.rnd(0.03, 0.92), es: fx.rnd(0.75, 1.35),
                                   ec: fx.colors[echoModel.count % 7] });
            }
        }
        ListModel { id: echoModel }
        Repeater {
            model: echoModel
            Rectangle {
                id: chip
                required property real ex
                required property real ey
                required property real es
                required property string ec
                radius: height / 2
                color: fx.hex[ec]
                width: chipText.implicitWidth + 22; height: chipText.implicitHeight + 10
                x: fx.width / 2 - width / 2; y: fx.height / 2 - height / 2
                opacity: 0; scale: 0.3
                Text {
                    id: chipText
                    anchors.centerIn: parent
                    text: fx.text.length > 34 ? fx.text.slice(0, 34) + "…" : fx.text
                    color: "white"
                    font.pixelSize: 14 * chip.es
                    font.weight: Font.DemiBold
                }
                ParallelAnimation {
                    running: true
                    NumberAnimation { target: chip; property: "x"; to: chip.ex * fx.width; duration: 900; easing.type: Easing.OutCubic }
                    NumberAnimation { target: chip; property: "y"; to: chip.ey * fx.height; duration: 900; easing.type: Easing.OutCubic }
                    NumberAnimation { target: chip; property: "scale"; to: 1; duration: 900; easing.type: Easing.OutBack }
                    NumberAnimation { target: chip; property: "opacity"; to: 0.92; duration: 300 }
                }
            }
        }
    }

    // =============================================================== shooting star
    Item {
        id: star
        width: 1; height: 1
        visible: fx.current === "shootingstar"
        property int n: fx.nonce
        onNChanged: if (fx.current === "shootingstar") starAnim.restart()
        Image { anchors.centerIn: parent; width: 110; height: 110; source: fx.sprite("glow", "yellow"); opacity: 0.8 }
        Image {
            id: starCore
            anchors.centerIn: parent; width: 52; height: 52
            source: Qt.resolvedUrl("fx/sparkle-white.png")
            RotationAnimation on rotation { from: 0; to: 360; duration: 1600; loops: Animation.Infinite }
        }
        Emitter {
            system: sys; group: "stardust"
            enabled: star.visible && starAnim.running
            anchors.centerIn: parent; width: 10; height: 10
            emitRate: 170; lifeSpan: 1000; lifeSpanVariation: 350
            velocity: AngleDirection { angle: 0; angleVariation: 360; magnitude: 28; magnitudeVariation: 20 }
        }
        PathAnimation {
            id: starAnim
            target: star
            duration: 2200
            easing.type: Easing.InOutSine
            path: Path {
                startX: -60; startY: fx.height * 0.18
                PathCubic { x: fx.width + 60; y: fx.height * 0.3
                            control1X: fx.width * 0.3; control1Y: -fx.height * 0.05
                            control2X: fx.width * 0.7; control2Y: fx.height * 0.55 }
            }
        }
    }
    Gravity { system: sys; groups: ["stardust"]; angle: 90; magnitude: 30 }
    ItemParticle {
        system: sys; groups: ["stardust"]; fade: true
        delegate: Image {
            source: Math.random() < 0.3 ? Qt.resolvedUrl("fx/sparkle-gold.png") : fx.sprite("glow", fx.pick(["yellow", "white", "gold"]))
            width: fx.rnd(6, 16); height: width
        }
    }
}
