import QtQuick
import "theme" as Appearance
import QtQuick.Window
import QtQuick.Layouts
import "js/Tone.js" as Tone

// Service-status chip for the popup header: the Quickshell twin of the Plasma
// widget's StatusChip.qml, fed by the same details.status block; hovering it
// shows the details in a TipBubble.
Rectangle {
    id: chip

    // { indicator, description, components, incidents, latestUpdate, url }
    property var status: ({})
    property var shell

    // "none" | "minor" | "major" | "critical" | "" (no live feed)
    readonly property string indicator: (status && status.indicator) || ""
    readonly property string statusUrl: (status && status.url) || ""
    // A page without a machine-readable feed is still worth a link.
    readonly property bool linkOnly: indicator === "" && statusUrl !== ""

    visible: indicator !== "" || linkOnly
    implicitHeight: 20
    implicitWidth: chipRow.implicitWidth + 16
    radius: 5

    readonly property color statusColor: {
        if (linkOnly)
            return Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.5));
        if (indicator === "critical")
            return Qt.rgba(1.0, 0.3, 0.3, 0.95);
        if (indicator === "major")
            return Qt.rgba(1.0, 0.5, 0.1, 0.95);
        if (indicator === "minor")
            return Qt.rgba(1.0, 0.75, 0.2, 0.95);
        return Qt.rgba(0.2, 0.8, 0.4, 0.95);
    }

    color: Qt.rgba(statusColor.r, statusColor.g, statusColor.b, chipMouse.containsMouse ? 0.2 : (linkOnly ? 0.06 : 0.12))
    border.width: 1
    border.color: Qt.rgba(statusColor.r, statusColor.g, statusColor.b, linkOnly ? 0.18 : 0.32)

    readonly property string tooltipText: {
        var s = chip.status || {};
        var lines = [chip.shell.i18n("Status") + "  ·  " + (s.description || chip.shell.i18n("Unknown"))];
        var comps = s.components || [];
        if (comps.length > 0) {
            lines.push("", chip.shell.i18n("Affected:"));
            for (var c = 0; c < comps.length; c++)
                lines.push("  · " + comps[c]);
        }
        var inc = s.incidents || [];
        if (inc.length > 0) {
            lines.push("", chip.shell.i18np("Incident:", "Incidents:", inc.length));
            for (var i = 0; i < inc.length; i++)
                lines.push("  · " + inc[i]);
        }
        if (s.latestUpdate)
            lines.push("", chip.shell.i18n("Latest update:"), s.latestUpdate);
        if (chip.statusUrl !== "")
            lines.push("", chip.shell.i18n("Click to open status page"));
        return lines.join("\n");
    }

    RowLayout {
        id: chipRow
        anchors.centerIn: parent
        spacing: 5

        Rectangle {
            visible: !chip.linkOnly
            Layout.preferredWidth: 6
            Layout.preferredHeight: 6
            radius: 3
            color: Tone.ink(Appearance.Palette, chip.statusColor)

            SequentialAnimation on opacity {
                objectName: "statusPulse"
                running: chip.visible && chip.Window.window !== null && chip.Window.window.visible && !chip.linkOnly && chip.indicator !== "none"
                loops: Animation.Infinite
                NumberAnimation {
                    to: 0.35
                    duration: 700
                    easing.type: Easing.InOutSine
                }
                NumberAnimation {
                    to: 1.0
                    duration: 700
                    easing.type: Easing.InOutSine
                }
            }
        }

        Text {
            text: {
                if (chip.linkOnly)
                    return chip.shell.i18n("Status ↗");
                if (chip.indicator === "critical")
                    return chip.shell.i18n("Major Outage");
                if (chip.indicator === "major")
                    return chip.shell.i18n("Partial Outage");
                if (chip.indicator === "minor")
                    return chip.shell.i18n("Minor Issues");
                return chip.shell.i18n("Operational");
            }
            font.pixelSize: 10
            font.bold: !chip.linkOnly && chip.indicator !== "none"
            color: Tone.ink(Appearance.Palette, chip.statusColor)
        }
    }

    MouseArea {
        id: chipMouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: chip.statusUrl !== "" ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: {
            if (chip.statusUrl !== "")
                Qt.openUrlExternally(chip.statusUrl);
        }
    }

    TipBubble {
        visible: chipMouse.containsMouse && !chip.linkOnly
        delay: 300
        y: chip.height + 6
        x: chip.width - width
        width: 280
        text: chip.tooltipText
    }
}
