import QtQuick
import QtQuick.Templates as T
import "theme" as Appearance
import "js/Tone.js" as Tone

// A hover bubble drawn in the window's overlay layer, so it sits above all
// the content and no parent clips it. Built on QtQuick.Templates, which has no
// style and so needs no Kirigami: the QQC2 ToolTip's style is missing under
// some hosts (Quickshell).
T.ToolTip {
    id: tip

    // Kept inside the window rather than clipped at its edge.
    margins: 4
    leftPadding: 9
    rightPadding: 9
    topPadding: 6
    bottomPadding: 6
    implicitWidth: implicitContentWidth + leftPadding + rightPadding
    implicitHeight: implicitContentHeight + topPadding + bottomPadding
    closePolicy: T.Popup.NoAutoClose

    contentItem: Text {
        text: tip.text
        font.pixelSize: 11
        // Only wraps when the bubble is given a width; otherwise it sizes to
        // the text.
        wrapMode: Text.Wrap
        color: Tone.c(Appearance.Palette, "#e2e8f0")
    }

    background: Rectangle {
        radius: 6
        color: Tone.c(Appearance.Palette, Qt.rgba(0.04, 0.045, 0.06, 0.96))
        border.width: 1
        border.color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.14))
    }
}
