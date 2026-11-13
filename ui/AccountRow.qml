import QtQuick
import "theme" as Appearance
import QtQuick.Layouts
import "js/Tone.js" as Tone

// The line under the provider tabs: who is signed in, a few chips (plan, model,
// effort, …) and the service status. Everything comes from the provider's
// `account` ({name, chips: [{text, tone, tooltip}]}, backend/tidesi/contract.py),
// so one component serves every provider.
RowLayout {
    id: row

    property var shell
    property var account: ({})
    property var status: ({})
    property color accent: "#cc785c"

    readonly property var chips: (account && account.chips) || []
    readonly property bool hasAccount: ((account && account.name) || "") !== "" || chips.length > 0
    // Shown when there is anything to say: an account, or a status page.
    readonly property bool hasContent: hasAccount || statusChip.visible

    spacing: 8

    // The person icon, drawn: no icon theme needed.
    Canvas {
        id: userIcon
        visible: row.hasAccount
        Layout.preferredWidth: 26
        Layout.preferredHeight: 26
        Layout.alignment: Qt.AlignVCenter
        opacity: 0.7
        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            ctx.scale(26 / 14, 26 / 14);
            ctx.strokeStyle = row.accent;
            ctx.lineWidth = 1.2;
            ctx.beginPath();
            ctx.arc(7, 4.6, 2.7, 0, Math.PI * 2);
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(1.8, 13);
            ctx.bezierCurveTo(1.8, 8.6, 12.2, 8.6, 12.2, 13);
            ctx.stroke();
        }
        Connections {
            target: row
            function onAccentChanged() {
                userIcon.requestPaint();
            }
        }
    }

    Text {
        visible: row.hasAccount
        text: row.shell ? row.shell.tr(row.account, "name") : ""
        font.pixelSize: 10
        opacity: 0.6
        color: Tone.c(Appearance.Palette, "#f8fafc")
        elide: Text.ElideRight
        Layout.fillWidth: true
    }

    Item {
        visible: !row.hasAccount
        Layout.fillWidth: true
    }

    Repeater {
        model: row.chips

        Item {
            id: chipItem
            required property var modelData
            readonly property string tone: modelData.tone || "muted"
            readonly property bool isPlan: tone === "plan"
            readonly property color base: {
                if ((modelData.color || "") !== "")
                    return modelData.color;
                switch (tone) {
                case "plan":
                case "accent":
                    return row.accent;
                case "good":
                    return "#34d399";
                case "warn":
                    return "#fbbf24";
                case "danger":
                    return "#f87171";
                case "off":
                    return Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.38));
                default:
                    return Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.62));
                }
            }
            readonly property string text: row.shell ? row.shell.tr(modelData, "text") : (modelData.text || "")
            readonly property string tip: row.shell ? row.shell.tr(modelData, "tooltip") : ""

            Layout.alignment: Qt.AlignVCenter
            implicitWidth: chipLabel.implicitWidth + (isPlan ? 16 : 12)
            implicitHeight: isPlan ? 18 : 16

            Rectangle {
                anchors.fill: parent
                radius: chipItem.isPlan ? 4 : 3
                color: Qt.rgba(chipItem.base.r, chipItem.base.g, chipItem.base.b, chipItem.tone === "off" || chipItem.tone === "muted" ? 0.10 : 0.16)
                border.width: 1
                border.color: Qt.rgba(chipItem.base.r, chipItem.base.g, chipItem.base.b, 0.35)
            }
            Text {
                id: chipLabel
                anchors.centerIn: parent
                text: chipItem.text
                font.pixelSize: chipItem.isPlan ? 10 : 9
                font.bold: chipItem.tone !== "off"
                color: Tone.ink(Appearance.Palette, chipItem.base)
            }
            HoverHandler {
                id: chipHover
                enabled: chipItem.tip !== ""
            }
            TipBubble {
                visible: chipHover.hovered
                y: chipItem.height + 5
                x: Math.min(0, chipItem.width - width)
                text: chipItem.tip
            }
        }
    }

    StatusChip {
        id: statusChip
        Layout.alignment: Qt.AlignVCenter
        shell: row.shell
        status: row.status
    }
}
