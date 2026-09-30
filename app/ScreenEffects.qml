import QtQuick
import QtQuick.Particles
import QtQuick.Shapes

// iMessage screen effects, drawn over the conversation. play(id, text) runs one for about
// three seconds and then gets out of the way; the overlay never takes a click.
Item {
    id: fx
    anchors.fill: parent
    visible: current !== ""
    z: 50

    property string current: ""
    property string text: ""
    property int nonce: 0
    readonly property var palette: ["#ff5c5c", "#ffb13b", "#ffe14d", "#5ce07a", "#4db5ff", "#a66bff", "#ff6bd0"]

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

    function isScreen(id) { return !!names[id]; }

    function play(id, msgText) {
        const n = names[id];
        if (!n) return;
        stop();
        fx.text = msgText || "";
        fx.current = n;
        fx.nonce++;
        endTimer.interval = n === "balloons" ? 4200 : n === "fireworks" ? 3600 : 3000;
        endTimer.restart();
        if (n === "fireworks") fireworkTimer.start();
        if (n === "echo") echoTimer.start();
    }
    function stop() {
        endTimer.stop(); fireworkTimer.stop(); echoTimer.stop();
        echoModel.clear();
        fx.current = "";
    }
    Timer { id: endTimer; onTriggered: fx.stop() }

    function rnd(a, b) { return a + Math.random() * (b - a); }
    property color burstColor: "#ffd24d"

    // A filled heart, drawn (the UI font's ♥ is only an outline).
    component Heart: Shape {
        id: heartShape
        property color fill: "#ff3b5c"
        width: 100; height: 90
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            fillColor: heartShape.fill
            strokeWidth: -1
            PathSvg { path: "M50 88 C 20 65 0 48 0 28 C 0 12 12 0 27 0 C 38 0 46 6 50 15 C 54 6 62 0 73 0 C 88 0 100 12 100 28 C 100 48 80 65 50 88 Z" }
        }
    }
    // A glowing spark for fireworks and trails: an item, so it draws everywhere
    // (the particle system's bundled images don't on every setup).
    component Spark: Rectangle {
        width: 6; height: 6; radius: 3
        Rectangle { anchors.centerIn: parent; width: 14; height: 14; radius: 7; color: parent.color; opacity: 0.25 }
    }
    function pick(list) { return list[Math.floor(Math.random() * list.length)]; }

    // ------------------------------------------------------------------ particles
    ParticleSystem { id: sys; running: fx.current !== "" }

    // confetti: paper bits tumbling down
    Emitter {
        system: sys; group: "confetti"
        enabled: fx.current === "confetti"
        x: 0; y: -20; width: fx.width; height: 1
        emitRate: 110; lifeSpan: 3200; lifeSpanVariation: 500
        velocity: AngleDirection { angle: 90; angleVariation: 25; magnitude: 170; magnitudeVariation: 90 }
        acceleration: PointDirection { y: 60; xVariation: 40 }
    }
    ItemParticle {
        system: sys; groups: ["confetti"]; fade: false
        delegate: Rectangle {
            width: fx.rnd(5, 9); height: fx.rnd(9, 15); radius: 1
            color: fx.pick(fx.palette)
            RotationAnimation on rotation { from: 0; to: fx.pick([360, -360]); duration: fx.rnd(700, 1600); loops: Animation.Infinite }
        }
    }

    // balloons: rising from the bottom
    Emitter {
        system: sys; group: "balloons"
        enabled: fx.current === "balloons"
        x: 0; y: fx.height + 60; width: fx.width; height: 1
        emitRate: 9; lifeSpan: 4200
        velocity: AngleDirection { angle: 270; angleVariation: 8; magnitude: 190; magnitudeVariation: 60 }
    }
    ItemParticle {
        system: sys; groups: ["balloons"]; fade: false
        delegate: Item {
            width: 54; height: 110
            readonly property color c: fx.pick(fx.palette)
            Rectangle { width: 54; height: 66; radius: 27; color: parent.c
                Rectangle { x: 12; y: 10; width: 10; height: 18; radius: 5; color: "white"; opacity: 0.35 } }
            Rectangle { x: 26; y: 66; width: 1.5; height: 44; color: "#cccccc"; opacity: 0.7 }
            SequentialAnimation on rotation { loops: Animation.Infinite
                NumberAnimation { from: -6; to: 6; duration: 900; easing.type: Easing.InOutSine }
                NumberAnimation { from: 6; to: -6; duration: 900; easing.type: Easing.InOutSine } }
        }
    }

    // fireworks: bursts at random points
    Emitter {
        id: fireworkEmitter
        system: sys; group: "spark"
        // burst() needs an enabled emitter; a rate of 0 keeps it quiet between bursts
        enabled: fx.current === "fireworks"
        emitRate: 0
        width: 1; height: 1
        lifeSpan: 1300; lifeSpanVariation: 300
        size: 7; endSize: 2
        velocity: AngleDirection { angle: 0; angleVariation: 360; magnitude: 160; magnitudeVariation: 70 }
        acceleration: PointDirection { y: 90 }
    }
    Timer {
        id: fireworkTimer
        interval: 420; repeat: true
        onTriggered: {
            fireworkEmitter.x = fx.rnd(fx.width * 0.15, fx.width * 0.85);
            fireworkEmitter.y = fx.rnd(fx.height * 0.12, fx.height * 0.5);
            fx.burstColor = fx.pick(fx.palette);
            fireworkEmitter.burst(70);
        }
    }
    ItemParticle {
        system: sys; groups: ["spark"]; fade: true
        delegate: Spark { Component.onCompleted: color = fx.current === "shootingstar" ? "#fff3a0" : fx.burstColor }
    }

    // celebration: golden sparkles pouring from the top corner
    Emitter {
        system: sys; group: "gold"
        enabled: fx.current === "celebration"
        x: fx.width - 30; y: 0; width: 30; height: 30
        emitRate: 90; lifeSpan: 2200; lifeSpanVariation: 400
        velocity: AngleDirection { angle: 135; angleVariation: 30; magnitude: 330; magnitudeVariation: 140 }
        acceleration: PointDirection { y: 40 }
    }
    ItemParticle {
        system: sys; groups: ["gold"]; fade: true
        delegate: Text {
            text: "✦"
            color: fx.pick(["#ffd35a", "#ffe89a", "#ffc233"])
            font.pixelSize: fx.rnd(10, 22)
            RotationAnimation on rotation { from: 0; to: 360; duration: fx.rnd(800, 1600); loops: Animation.Infinite }
        }
    }

    // love: small hearts drifting up behind the big one
    Emitter {
        system: sys; group: "hearts"
        enabled: fx.current === "love"
        x: fx.width * 0.3; y: fx.height * 0.65; width: fx.width * 0.4; height: 10
        emitRate: 16; lifeSpan: 2200
        velocity: AngleDirection { angle: 270; angleVariation: 25; magnitude: 90; magnitudeVariation: 40 }
    }
    ItemParticle {
        system: sys; groups: ["hearts"]; fade: true
        delegate: Heart { fill: "#ff4d6d"; scale: fx.rnd(0.14, 0.26); transformOrigin: Item.TopLeft }
    }

    // shooting star: a trail of sparks behind it
    Emitter {
        id: starTrail
        system: sys; group: "spark"
        enabled: fx.current === "shootingstar"
        x: star.x + star.width / 2; y: star.y + star.height / 2; width: 4; height: 4
        emitRate: 90; lifeSpan: 700
        velocity: AngleDirection { angle: 180; angleVariation: 20; magnitude: 40 }
    }

    // ------------------------------------------------------------------ drawn effects
    // Love: one big heart that swells, beats twice and fades.
    // The path is drawn in 100x90 units; this item scales it to the screen.
    Item {
        anchors.centerIn: parent
        width: 100; height: 90
        scale: Math.min(fx.width, fx.height) * 0.42 / 100
        visible: fx.current === "love"
    Heart {
        id: bigHeart
        fill: "#ff3b5c"
        scale: 0
        property int n: fx.nonce
        onNChanged: if (fx.current === "love") heartAnim.restart()
        SequentialAnimation {
            id: heartAnim
            ParallelAnimation {
                NumberAnimation { target: bigHeart; property: "scale"; from: 0.2; to: 1; duration: 520; easing.type: Easing.OutBack }
                NumberAnimation { target: bigHeart; property: "opacity"; from: 0; to: 0.95; duration: 300 }
            }
            NumberAnimation { target: bigHeart; property: "scale"; to: 1.12; duration: 160 }
            NumberAnimation { target: bigHeart; property: "scale"; to: 1.0; duration: 160 }
            NumberAnimation { target: bigHeart; property: "scale"; to: 1.12; duration: 160 }
            NumberAnimation { target: bigHeart; property: "scale"; to: 1.0; duration: 160 }
            PauseAnimation { duration: 700 }
            NumberAnimation { target: bigHeart; property: "opacity"; to: 0; duration: 600 }
        }
    }
    }
    // Lasers: a dark room and coloured beams sweeping from the centre.
    Rectangle {
        anchors.fill: parent
        visible: fx.current === "lasers" || fx.current === "spotlight"
        color: "black"
        opacity: fx.current === "spotlight" ? 0 : 0.7
    }
    Repeater {
        model: fx.current === "lasers" ? 6 : 0
        Rectangle {
            required property int index
            x: fx.width / 2
            y: fx.height / 2 - height / 2
            width: Math.hypot(fx.width, fx.height)
            height: 4
            radius: 2
            transformOrigin: Item.Left
            color: fx.palette[index % fx.palette.length]
            opacity: 0.85
            layer.enabled: true
            RotationAnimation on rotation {
                from: index * 60; to: index * 60 + (index % 2 ? 300 : -300)
                duration: 2800; easing.type: Easing.InOutSine
            }
            SequentialAnimation on height {
                loops: Animation.Infinite
                NumberAnimation { from: 3; to: 7; duration: 120 }
                NumberAnimation { from: 7; to: 3; duration: 120 }
            }
        }
    }

    // Spotlight: darkness everywhere except a moving circle of light.
    Canvas {
        id: spot
        anchors.fill: parent
        visible: fx.current === "spotlight"
        property real cx: width / 2
        property real cy: height / 2
        property real r: Math.min(width, height) * 0.22
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
            const grad = g.createRadialGradient(cx, cy, r * 0.6, cx, cy, r);
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
            NumberAnimation { target: spot; property: "dark"; from: 0; to: 0.78; duration: 400 }
            ParallelAnimation {
                NumberAnimation { target: spot; property: "cx"; to: spot.width * 0.7; duration: 1100; easing.type: Easing.InOutQuad }
                NumberAnimation { target: spot; property: "cy"; to: spot.height * 0.72; duration: 1100; easing.type: Easing.InOutQuad }
            }
            NumberAnimation { target: spot; property: "cx"; to: spot.width * 0.5; duration: 600; easing.type: Easing.InOutQuad }
            PauseAnimation { duration: 300 }
            NumberAnimation { target: spot; property: "dark"; to: 0; duration: 500 }
        }
    }

    // Echo: the message itself, repeated across the screen.
    Timer {
        id: echoTimer
        interval: 60; repeat: true
        property int count: 0
        onTriggered: {
            if (echoModel.count > 34) { stop(); return; }
            echoModel.append({ ex: fx.rnd(0.05, 0.85), ey: fx.rnd(0.05, 0.9), es: fx.rnd(0.8, 1.6) });
        }
    }
    ListModel { id: echoModel }
    Repeater {
        id: echoRepeater
        model: echoModel
        Text {
            required property real ex
            required property real ey
            required property real es
            x: ex * fx.width
            y: ey * fx.height
            text: fx.text.length > 40 ? fx.text.slice(0, 40) + "…" : fx.text
            color: fx.palette[Math.floor(ex * 100) % fx.palette.length]
            font.pixelSize: 16 * es
            font.weight: Font.DemiBold
            NumberAnimation on opacity { from: 0; to: 0.9; duration: 250 }
            NumberAnimation on scale { from: 0.3; to: 1; duration: 350; easing.type: Easing.OutBack }
        }
    }

    // Shooting star: one star across the top, trailing sparks.
    Text {
        id: star
        visible: fx.current === "shootingstar"
        text: "★"
        color: "#fff3a0"
        font.pixelSize: 34
        property int n: fx.nonce
        onNChanged: if (fx.current === "shootingstar") starAnim.restart()
        ParallelAnimation {
            id: starAnim
            NumberAnimation { target: star; property: "x"; from: -40; to: fx.width + 40; duration: 2200; easing.type: Easing.InOutQuad }
            NumberAnimation { target: star; property: "y"; from: fx.height * 0.1; to: fx.height * 0.38; duration: 2200; easing.type: Easing.InOutQuad }
            RotationAnimation { target: star; from: 0; to: 360; duration: 2200 }
        }
    }
}
