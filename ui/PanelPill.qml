import QtQuick
import "theme" as Appearance
import "js/Tone.js" as Tone
import QtQuick.Layouts

// The panel item. Mirrors the Plasma compact representation: one PanelSlot per
// usage window (e.g. Claude 5h | 7d), separated by thin dividers, plus a
// blinking error dot. A translucent pill background stands in for the panel
// frame Plasma provides.
Rectangle {
    id: pill

    property string iconSource: ""
    // [{pct, color, text?, tooltip?}] from the backend's provider.slots
    property var slots: []
    property bool stale: false
    property bool hasError: false
    // Several providers side by side (pinned on the panel): [{icon, slots,
    // stale, hasError}], from AppState.panelGroups. When empty, the single
    // provider above is shown.
    property var groups: []
    readonly property var shownGroups: groups.length > 0 ? groups : [
        {
            icon: pill.iconSource,
            slots: pill.slots,
            stale: pill.stale,
            hasError: pill.hasError
        }
    ]
    // Every slot of every group, in order, with what it is drawn with.
    readonly property var items: {
        var out = [];
        for (var g = 0; g < shownGroups.length; g++) {
            var group = shownGroups[g] || {};
            var slots = group.slots || [];
            for (var i = 0; i < slots.length; i++)
                out.push({
                    slot: slots[i],
                    icon: group.icon || "",
                    stale: group.stale === true,
                    groupStart: g > 0 && i === 0
                });
        }
        return out;
    }
    readonly property bool anyError: {
        for (var g = 0; g < shownGroups.length; g++)
            if (shownGroups[g] && shownGroups[g].hasError)
                return true;
        return false;
    }
    property bool active: false      // popup open → keep the hover tint
    // false inside a desktop panel (KDE) that already draws the frame: only a
    // faint hover tint remains.
    property bool framed: true
    readonly property bool light: Tone.isLight(Appearance.Palette)
    property color textColor: framed && light ? "#172033" : "#f8fafc"

    signal clicked

    readonly property color dangerColor: "#ff4d4d"
    readonly property bool hovered: mouse.containsMouse
    // Combined slot tooltips; the shell shows these in a hover popup since the
    // QQC2 ToolTip style isn't available under Quickshell.
    readonly property string tooltipText: {
        var lines = [];
        for (var i = 0; i < pill.items.length; i++) {
            var t = pill.items[i].slot.tooltip;
            if (t !== undefined && t !== "")
                lines.push(t);
        }
        return lines.join("\n");
    }

    // Stable floor + animated width so a provider/error swap (fewer slots →
    // narrower content) doesn't make the right-anchored pill jump sideways.
    implicitWidth: Math.max(96, row.implicitWidth + 20)
    implicitHeight: 30
    width: implicitWidth
    height: implicitHeight
    radius: 8

    Behavior on implicitWidth {
        NumberAnimation {
            duration: 220
            easing.type: Easing.OutCubic
        }
    }
    color: framed && light ? (mouse.containsMouse || active ? "#f4f6fa" : "#e8edf3") : !framed ? (mouse.containsMouse || active ? Qt.rgba(1, 1, 1, 0.08) : "transparent") : (mouse.containsMouse || active ? Qt.rgba(0.10, 0.11, 0.14, 0.92) : Qt.rgba(0.06, 0.07, 0.09, 0.86))
    border.width: framed ? 1 : 0
    border.color: light && framed ? "#b8c1cd" : Qt.rgba(1, 1, 1, 0.12)

    Behavior on color {
        ColorAnimation {
            duration: 150
        }
    }

    RowLayout {
        id: row
        anchors.centerIn: parent
        spacing: 8

        Rectangle {
            visible: pill.anyError
            Layout.preferredWidth: 6
            Layout.preferredHeight: 6
            radius: 3
            color: pill.dangerColor
            Layout.alignment: Qt.AlignVCenter
            // Static on purpose: an endless opacity animation redraws the pill
            // window every frame for as long as any provider has an error.
            opacity: 0.85
        }

        Repeater {
            model: pill.items

            RowLayout {
                required property var modelData
                required property int index
                spacing: 8

                // Between two providers a wider gap than between two values.
                Item {
                    visible: modelData.groupStart
                    Layout.preferredWidth: 2
                }

                PanelSlot {
                    pct: modelData.slot.pct !== undefined ? modelData.slot.pct : 0
                    iconColor: modelData.slot.color !== undefined ? modelData.slot.color : "#cc785c"
                    costText: modelData.slot.text !== undefined && modelData.slot.text !== null ? modelData.slot.text : ""
                    stale: modelData.stale
                    iconSource: modelData.icon
                    textColor: pill.textColor
                    surfaceColor: pill.framed ? pill.color : (pill.textColor.hslLightness < 0.5 ? "#f4f6fa" : "#10141c")
                }

                Rectangle {
                    visible: index < pill.items.length - 1
                    Layout.preferredWidth: pill.items[index + 1] && pill.items[index + 1].groupStart ? 2 : 1
                    Layout.preferredHeight: 14
                    color: Qt.rgba(pill.textColor.r, pill.textColor.g, pill.textColor.b, pill.items[index + 1] && pill.items[index + 1].groupStart ? 0.28 : 0.16)
                    Layout.alignment: Qt.AlignVCenter
                }
            }
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: pill.clicked()
    }
}
