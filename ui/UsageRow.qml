import QtQuick
import "theme" as Appearance
import QtQuick.Layouts
import "js/Tone.js" as Tone

// A labelled usage row, ported from the Plasma PopupRow: title + "· reset" +
// live countdown chip + animated %, a 20-segment bar with gradient partial
// fill, and an optional sub-label.
ColumnLayout {
    id: row

    property string label: ""
    property string detail: ""
    property string note: ""
    property string resetText: ""
    property string countdownText: ""
    property real value: 0
    property color barColor: "#7dd3fc"
    property bool showMeter: true
    // Shown in a bubble while the row is hovered (name, used share, reset time).
    property string tooltipText: ""

    readonly property color dangerColor: Tone.isLight(Appearance.Palette) ? "#c62828" : "#ff4d4d"
    readonly property color warningColor: Tone.isLight(Appearance.Palette) ? "#c2410c" : "#ffa64d"
    readonly property int segmentCount: 20

    Layout.fillWidth: true
    spacing: 5

    property real displayValue: 0
    onValueChanged: displayValue = value
    Component.onCompleted: displayValue = value
    Behavior on displayValue {
        NumberAnimation {
            duration: 600
            easing.type: Easing.OutCubic
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 6

        Text {
            text: row.label
            font.bold: true
            font.pixelSize: 13
            color: Tone.c(Appearance.Palette, "#f8fafc")
        }

        Text {
            visible: row.resetText !== ""
            text: "· " + row.resetText
            font.pixelSize: 11
            opacity: 0.5
            color: Tone.c(Appearance.Palette, "#f8fafc")
        }

        Item {
            Layout.fillWidth: true
        }

        Rectangle {
            visible: row.countdownText !== ""
            Layout.preferredHeight: 20
            Layout.preferredWidth: cdLabel.implicitWidth + 14
            radius: 4
            color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.06))
            border.width: 1
            border.color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.12))
            Layout.alignment: Qt.AlignVCenter

            Text {
                id: cdLabel
                anchors.centerIn: parent
                text: row.countdownText
                font.pixelSize: 11
                color: Tone.c(Appearance.Palette, "#f8fafc")
                opacity: 0.8
            }
        }

        Item {
            Layout.preferredWidth: 2
        }

        Text {
            visible: row.showMeter
            text: Math.round(row.displayValue) + "%"
            font.bold: true
            font.pixelSize: 14
            color: row.value >= 90 ? row.dangerColor : row.value >= 70 ? row.warningColor : Tone.ink(Appearance.Palette, row.barColor)
            Layout.alignment: Qt.AlignVCenter
        }
    }

    Item {
        visible: row.showMeter
        Layout.fillWidth: true
        Layout.preferredHeight: visible ? 8 : 0

        Row {
            anchors.fill: parent
            spacing: 3

            Repeater {
                model: row.segmentCount

                Rectangle {
                    width: (row.width - (row.segmentCount - 1) * 3) / row.segmentCount
                    height: parent.height
                    radius: 2
                    readonly property real segThresh: (index + 1) * (100 / row.segmentCount)
                    readonly property real prevThresh: index * (100 / row.segmentCount)
                    readonly property real fillRatio: {
                        if (row.value >= segThresh)
                            return 1.0;
                        if (row.value <= prevThresh)
                            return 0.0;
                        return (row.value - prevThresh) / (100 / row.segmentCount);
                    }
                    color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.06))
                    border.width: 1
                    border.color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.10))

                    Rectangle {
                        anchors {
                            left: parent.left
                            top: parent.top
                            bottom: parent.bottom
                            margins: 1
                        }
                        width: Math.max(0, (parent.width - 2) * parent.fillRatio)
                        radius: 1.5
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop {
                                position: 0.0
                                color: Qt.lighter(row.barColor, 1.15)
                            }
                            GradientStop {
                                position: 1.0
                                color: row.barColor
                            }
                        }
                        Behavior on width {
                            NumberAnimation {
                                duration: 500
                                easing.type: Easing.OutCubic
                            }
                        }
                    }
                }
            }
        }
    }

    Text {
        visible: row.detail !== ""
        Layout.fillWidth: true
        text: row.detail
        color: Tone.c(Appearance.Palette, "#94a3b8")
        font.pixelSize: 11
        wrapMode: Text.WordWrap
    }

    Text {
        visible: row.note !== ""
        Layout.fillWidth: true
        text: row.note
        color: Tone.c(Appearance.Palette, "#94a3b8")
        font.pixelSize: 10
        wrapMode: Text.WordWrap
    }

    // Hover bubble. A zero-height layout item whose mouse area reaches back up
    // over the whole row, so the row keeps its layout. Not a HoverHandler on
    // the row itself: on a ColumnLayout that a Repeater creates, it crashes
    // Qt 6.12 (QQuickItemPrivate::handlePointerEvent from addChild).
    Item {
        Layout.fillWidth: true
        Layout.preferredHeight: 0

        MouseArea {
            id: rowHover
            x: 0
            y: -row.height
            width: row.width
            height: row.height
            hoverEnabled: row.tooltipText !== ""
            acceptedButtons: Qt.NoButton

            TipBubble {
                visible: rowHover.containsMouse && row.tooltipText !== ""
                delay: 450
                text: row.tooltipText
                x: rowHover.mouseX + 10
                y: rowHover.mouseY + 20
            }
        }
    }
}
