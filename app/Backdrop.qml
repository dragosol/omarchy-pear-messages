import QtQuick
import QtQuick.Effects

// Frosted glass behind a header or footer that floats over a list: whatever of the list has
// scrolled under it shows through, blurred and veiled in the background colour. Place it
// exactly over the header or footer (behind its contents) and say where that is relative to
// the list's top with `offsetX`/`offsetY`. The list needs displayMarginBeginning/End covering this area,
// or the rows out there are culled and nothing shows.
Item {
    id: bd
    clip: true
    required property Flickable target
    // where this sits relative to the list's top-left corner
    property real offsetX: 0
    property real offsetY: 0
    property color tint: "black"
    property real veil: 0.55
    property real blurMax: 64
    // how far past its own edges the blur may sample, so it doesn't fade at the seams
    readonly property real pad: 64
    // "top" or "bottom": that side fades out to the sharp list (a seam band inside the list)
    property string feather: ""
    // how much of the band, from the far edge, stays at full strength before fading
    property real plateau: 0.3

    layer.enabled: feather !== "" && visible
    layer.effect: MultiEffect {
        maskEnabled: true
        maskSource: fade
        // smoothstep(min*(1+spread) - spread, min*(1+spread)): 0..1 across the gradient
        maskThresholdMin: 0.5
        maskSpreadAtMin: 1.0
    }
    Rectangle {
        id: fade
        anchors.fill: parent
        visible: false
        layer.enabled: bd.feather !== ""
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.rgba(0, 0, 0, bd.feather === "top" ? 0.0 : 0.99) }
            GradientStop { position: bd.feather === "top" ? 1 - bd.plateau : bd.plateau; color: Qt.rgba(0, 0, 0, 0.99) }
            GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, bd.feather === "top" ? 0.99 : 0.0) }
        }
    }

    // opaque underneath, or wherever the blurred copy is thin the sharp list shows through it
    Rectangle { anchors.fill: parent; color: bd.tint }

    ShaderEffectSource {
        id: src
        sourceItem: bd.visible ? bd.target.contentItem : null
        live: true
        visible: false
        // this area, in the scrolling content's coordinates
        sourceRect: Qt.rect(bd.offsetX - bd.target.contentItem.x - bd.pad, bd.offsetY - bd.target.contentItem.y - bd.pad,
                            bd.width + 2 * bd.pad, bd.height + 2 * bd.pad)
        width: bd.width + 2 * bd.pad
        height: bd.height + 2 * bd.pad
    }
    MultiEffect {
        source: src
        x: -bd.pad
        y: -bd.pad
        width: src.width
        height: src.height
        autoPaddingEnabled: false
        blurEnabled: true
        blur: 1.0
        blurMax: bd.blurMax
        saturation: 0.2
    }
    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(bd.tint.r, bd.tint.g, bd.tint.b, bd.veil)
    }
}
