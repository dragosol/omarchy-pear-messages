import QtQuick

// Pear Passwords' scrolling, shared by every scrolling view here. Declare it inside a
// Flickable/ListView with `flick: <that view>`, set the view's `interactive: false`, and hand it
// the view's wheel events:
//
//     WheelHandler { acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
//                    onWheel: ev => physics.wheel(ev) }
//
// A mouse notch glides instead of jumping; a touchpad follows the fingers 1:1 and stretches
// past the ends with resistance, then springs back when the fingers lift. interactive:false is
// what hands wheel events to the handler - with it on, Flickable took them too and scrolled twice.
Item {
    id: phys
    visible: false
    width: 0
    height: 0

    required property Flickable flick
    readonly property real minY: flick.originY
    readonly property real maxY: Math.max(flick.originY, flick.originY + flick.contentHeight - flick.height)

    // ---- Apple's scroll physics --------------------------------------------
    // decelRate: UIScrollView.DecelerationRate.normal, per millisecond. Speed
    //   decays exponentially, v = v0 * 0.998^t - fast start, long smooth tail.
    // bandC: the rubber-band constant from UIScrollView,
    //   f(x) = (1 - 1/(x*c/d + 1)) * d - the further past an end, the less it gives.
    // springOmega: bounce-back is a critically damped spring (damping 1.0, as in
    //   WWDC "Designing Fluid Interfaces"), 0.4 s response, carrying the flick's speed.
    // accelMax: the one part that is NOT Apple's published curve. macOS accelerates
    //   fast scrolls in the OS and hasn't documented how; libinput deliberately
    //   doesn't accelerate touchpad scrolling at all. Slow stays 1:1, fast gains up
    //   to (1 + accelMax)x. If sensitivity feels off, this is the knob.
    readonly property real decelRate: 0.998
    readonly property real bandC: 0.55
    readonly property real springOmega: 2 * Math.PI / 400
    readonly property real accelMax: 1.6

    property string mode: "idle"      // idle | drag | coast | bounce
    property real vel: 0              // px/ms along contentY
    property real rawY: 0             // where the fingers put it, before the band
    property real bounceTarget: 0
    property var samples: []
    property real lastT: 0
    property real glideTo: 0
    readonly property bool busy: mode !== "idle" || glide.running

    function band(x) { const d = flick.height; return (1 - 1 / (x * bandC / d + 1)) * d; }
    function unband(y) {
        const d = flick.height;
        return y >= d * 0.999 ? y * 50 : y * d / (bandC * (d - y));
    }
    function banded(raw) {
        if (raw < minY) return minY - band(minY - raw);
        if (raw > maxY) return maxY + band(raw - maxY);
        return raw;
    }
    function unbanded(y) {
        if (y < minY) return minY - unband(minY - y);
        if (y > maxY) return maxY + unband(y - maxY);
        return y;
    }

    // Continuous input (touchpad) is tracked through the physics above; a
    // discrete mouse notch glides to an accumulating target. A value that isn't a
    // multiple of 120 is continuous even when pixelDelta is empty.
    function wheel(ev) {
        const px = ev.pixelDelta.y, ad = ev.angleDelta.y;
        // The lift carries no movement, so catch it before "did it move".
        if (ev.phase === Qt.ScrollEnd) { letGo(); return; }
        if (px !== 0 || ad % 120 !== 0) {
            glide.stop();
            pushBy(px !== 0 ? -px : -ad / 120 * 60);
            endTimer.restart();
        } else if (ad !== 0) {
            mode = "idle";
            const base = glide.running ? glideTo : flick.contentY;
            glideTo = Math.max(minY, Math.min(maxY, base - ad / 120 * 110));
            glide.to = glideTo;
            glide.restart();
        }
    }

    NumberAnimation { id: glide; target: phys.flick; property: "contentY"
                      duration: 240; easing.type: Easing.OutCubic }
    // Fingers lifted, for input paths that never send ScrollEnd.
    Timer { id: endTimer; interval: 90; onTriggered: phys.letGo() }
    // Integrated per frame, not fitted to an easing curve: exact at any refresh rate.
    FrameAnimation {
        running: phys.mode === "coast" || phys.mode === "bounce"
        onTriggered: phys.step(Math.min(frameTime * 1000, 34))
    }

    function pushBy(dy) {
        const now = Date.now();
        if (mode !== "drag") {     // fingers down: take over from any coast
            mode = "drag";
            vel = 0;
            rawY = unbanded(flick.contentY);
            samples = [];
            lastT = now;
        }
        const dt = Math.max(1, now - lastT);
        lastT = now;
        const speed = Math.abs(dy) / dt;                      // px/ms
        const gain = 1 + accelMax * Math.max(0, Math.min(1, (speed - 0.35) / 2.2));
        const moved = dy * gain;
        samples = samples.filter(function (s) { return now - s.t < 160; })
                         .concat([{ t: now, dy: moved }]);
        rawY += moved;
        flick.contentY = banded(rawY);
    }

    function letGo() {
        endTimer.stop();
        if (mode !== "drag") return;
        const s = samples;
        samples = [];
        let v = 0;
        if (s.length >= 3) {
            // speed between the first and last movement, never to "now"
            const last = s[s.length - 1].t;
            const w = s.filter(function (x) { return last - x.t <= 110; });
            if (w.length >= 3) {
                let travelled = 0;
                for (let k = 1; k < w.length; k++) travelled += w[k].dy;
                v = travelled / Math.max(8, last - w[0].t);
            }
        }
        vel = Math.max(-8, Math.min(8, v));
        if (flick.contentY < minY || flick.contentY > maxY) startBounce();
        else mode = Math.abs(vel) > 0.02 ? "coast" : "idle";
    }

    function startBounce() {
        bounceTarget = flick.contentY < minY ? minY : maxY;
        mode = "bounce";
    }

    function step(dt) {
        if (mode === "coast") {
            const decay = Math.pow(decelRate, dt);
            flick.contentY += vel * (decay - 1) / Math.log(decelRate);
            vel *= decay;
            if (flick.contentY < minY || flick.contentY > maxY) startBounce();
            else if (Math.abs(vel) < 0.01) { vel = 0; mode = "idle"; }
            return;
        }
        if (mode === "bounce") {
            // exact critically damped step: x(t) = (x0 + (v0 + w*x0) t) e^(-wt)
            const w = springOmega;
            const x0 = flick.contentY - bounceTarget, v0 = vel;
            const e = Math.exp(-w * dt), B = v0 + w * x0;
            const x = (x0 + B * dt) * e;
            vel = (v0 - w * B * dt) * e;
            flick.contentY = bounceTarget + x;
            if (Math.abs(x) < 0.3 && Math.abs(vel) < 0.02) {
                flick.contentY = bounceTarget;
                vel = 0;
                mode = "idle";
            }
        }
    }

    // Fingers landed mid-coast (touch_watch.py): stop dead. A bounce is let
    // through, or it would strand the list stretched past an end.
    function catchCoast() {
        glide.stop();
        if (mode === "coast") {
            vel = 0;
            if (flick.contentY < minY || flick.contentY > maxY) startBounce();
            else mode = "idle";
        }
    }

    function stopPhysics() {
        glide.stop();
        vel = 0;
        mode = "idle";
    }
}
