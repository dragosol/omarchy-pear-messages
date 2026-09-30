import QtQuick
import qs.Commons
import qs.Ui as O

// Omarchy's own Button - same surface, border, focus ring and hover as the rest of the
// desktop - sized for a window you work in. Omapager Pro's buttons are compact on purpose:
// they sit on a notification banner. Here they are the primary controls, so they get room.
// One place to change it, rather than a padding override on every button.
O.Button {
    bordered: true
    horizontalPadding: Math.round(Style.spacing.controlPaddingX * 1.6)
    verticalPadding: Math.round(Style.spacing.controlPaddingY * 1.5)
    fontSize: Theme.fBody
    opacity: enabled ? 1 : 0.4
}
