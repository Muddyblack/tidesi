import QtQuick
import ".."
import "../theme" as Appearance
import "Theme.js" as Theme
import "../js/Tone.js" as Tone

// A small square button holding one StudioIcon; `danger` tints it red for
// removing things.
Rectangle {
    id: control

    property string icon: ""
    property bool danger: false
    // For screen readers and the hover tooltip.
    property string tip: ""
    signal clicked

    implicitWidth: 30
    implicitHeight: 30
    width: implicitWidth
    height: implicitHeight
    radius: 8
    opacity: enabled ? 1 : 0.45
    Accessible.role: Accessible.Button
    Accessible.name: control.tip
    Accessible.onPressAction: control.clicked()
    color: danger ? Qt.rgba(0.97, 0.44, 0.44, area.containsMouse ? 0.3 : 0.16) : area.containsMouse ? Tone.c(Appearance.Palette, "#26ffffff") : Tone.c(Appearance.Palette, Theme.hover)

    StudioIcon {
        anchors.centerIn: parent
        name: control.icon
        tint: control.danger ? Tone.ink(Appearance.Palette, Theme.bad) : area.containsMouse ? Tone.c(Appearance.Palette, Theme.text) : Tone.c(Appearance.Palette, Theme.muted)
    }
    MouseArea {
        id: area
        anchors.fill: parent
        enabled: control.enabled
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: control.clicked()
    }
    TipBubble {
        visible: area.containsMouse && control.tip !== ""
        y: control.height + 4
        x: control.width - width
        text: control.tip
    }
}
