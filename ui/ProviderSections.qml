import QtQuick
import "theme" as Appearance
import QtQuick.Layouts
import "js/Tone.js" as Tone

// What a provider shows beyond its quota rows, drawn from its `sections`
// (backend/tidesi/contract.py): groups of thin bars (per-model quotas), cards
// of label/value lines (balances, rates, limits) and grey footnotes. The
// component knows no provider; the backend says what to put in it.
ColumnLayout {
    id: sections

    property var shell
    property var provider: null
    property color accent: "#cc785c"

    spacing: 10

    readonly property color dangerColor: "#ff4d4d"
    readonly property color warningColor: "#ffa64d"

    function toneColor(tone, fallback) {
        switch (tone) {
        case "plan":
        case "accent":
            return sections.accent;
        case "good":
            return "#34d399";
        case "warn":
            return sections.warningColor;
        case "danger":
            return sections.dangerColor;
        default:
            return fallback;
        }
    }

    // Used-share colouring shared by every bar: the group's colour until it
    // gets close to the limit.
    function levelColor(pct, exhausted, base) {
        if (exhausted || pct >= 90)
            return sections.dangerColor;
        if (pct >= 70)
            return sections.warningColor;
        return base;
    }

    Repeater {
        model: (sections.provider && sections.provider.sections) || []

        ColumnLayout {
            id: block
            required property var modelData
            readonly property string kind: modelData.kind || ""

            Layout.fillWidth: true
            spacing: 8

            // ── Bars: grouped thin bars (per-model quotas) ──────────────────
            ColumnLayout {
                visible: block.kind === "bars"
                Layout.fillWidth: true
                spacing: 10

                Rectangle {
                    visible: (block.modelData.title || "") !== ""
                    Layout.fillWidth: true
                    Layout.preferredHeight: 1
                    color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.08))
                }
                Text {
                    visible: (block.modelData.title || "") !== ""
                    text: sections.shell.tr(block.modelData, "title")
                    font.bold: true
                    font.pixelSize: 11
                    opacity: 0.7
                    color: Tone.c(Appearance.Palette, "#f8fafc")
                }

                Repeater {
                    model: block.kind === "bars" ? (block.modelData.groups || []) : []

                    ColumnLayout {
                        id: group
                        required property var modelData
                        readonly property color groupColor: modelData.color ? modelData.color : sections.accent
                        readonly property string countdown: modelData.resetAt > 0 ? sections.shell.countdownFor(modelData.resetAt) : ""

                        Layout.fillWidth: true
                        spacing: 5

                        // Group header: name, the shared reset countdown, the pooled usage.
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Rectangle {
                                Layout.preferredWidth: 7
                                Layout.preferredHeight: 7
                                radius: 3.5
                                color: group.groupColor
                                Layout.alignment: Qt.AlignVCenter
                            }
                            Text {
                                text: sections.shell.tr(group.modelData, "label")
                                font.pixelSize: 10
                                font.bold: true
                                opacity: 0.85
                                color: Tone.c(Appearance.Palette, "#f8fafc")
                            }
                            Text {
                                visible: group.countdown !== ""
                                text: group.countdown === "resetting..." ? sections.shell.i18n("· resetting…") : "· " + sections.shell.i18n("resets in %1", group.countdown)
                                font.pixelSize: 9
                                opacity: 0.45
                                color: Tone.c(Appearance.Palette, "#f8fafc")
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Item {
                                visible: group.countdown === ""
                                Layout.fillWidth: true
                            }
                            Text {
                                text: Math.round(group.modelData.pct || 0) + "%"
                                font.pixelSize: 10
                                font.bold: true
                                color: sections.levelColor(group.modelData.pct || 0, false, group.groupColor)
                            }
                        }

                        // One thin bar per model.
                        Repeater {
                            model: group.modelData.rows || []

                            Item {
                                id: barRow
                                required property var modelData
                                readonly property string tip: sections.shell.tr(modelData, "tooltip")

                                Layout.fillWidth: true
                                Layout.leftMargin: 13
                                implicitHeight: barLine.implicitHeight

                                HoverHandler {
                                    id: barHover
                                    enabled: barRow.tip !== ""
                                }

                                RowLayout {
                                    id: barLine
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: 8

                                    Text {
                                        text: sections.shell.tr(barRow.modelData, "label")
                                        font.pixelSize: 10
                                        color: barRow.modelData.exhausted ? sections.dangerColor : Tone.c(Appearance.Palette, "#f8fafc")
                                        opacity: barRow.modelData.exhausted ? 1.0 : 0.65
                                        Layout.preferredWidth: 120
                                        elide: Text.ElideRight
                                    }
                                    Rectangle {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 6
                                        radius: 3
                                        color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.06))
                                        border.width: 1
                                        border.color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.10))
                                        Rectangle {
                                            anchors.left: parent.left
                                            anchors.top: parent.top
                                            anchors.bottom: parent.bottom
                                            anchors.margins: 1
                                            width: Math.max(0, (parent.width - 2) * ((barRow.modelData.exhausted ? 100 : barRow.modelData.pct) / 100))
                                            radius: 2
                                            color: sections.levelColor(barRow.modelData.pct, barRow.modelData.exhausted, barRow.modelData.color ? barRow.modelData.color : group.groupColor)
                                            Behavior on width {
                                                NumberAnimation {
                                                    duration: 500
                                                    easing.type: Easing.OutCubic
                                                }
                                            }
                                        }
                                    }
                                    Text {
                                        text: barRow.modelData.exhausted ? "100%" : Math.round(barRow.modelData.pct) + "%"
                                        font.pixelSize: 10
                                        font.bold: true
                                        color: sections.levelColor(barRow.modelData.pct, barRow.modelData.exhausted, barRow.modelData.color ? barRow.modelData.color : group.groupColor)
                                        Layout.preferredWidth: 35
                                        horizontalAlignment: Text.AlignRight
                                    }
                                }

                                TipBubble {
                                    visible: barHover.hovered
                                    y: barRow.height + 3
                                    x: 0
                                    text: barRow.tip
                                }
                            }
                        }
                    }
                }
            }

            // ── Facts: label / value lines, in a card ───────────────────────
            ColumnLayout {
                visible: block.kind === "facts"
                Layout.fillWidth: true
                spacing: 6

                Text {
                    visible: (block.modelData.title || "") !== ""
                    text: sections.shell.tr(block.modelData, "title")
                    font.bold: true
                    font.pixelSize: 11
                    opacity: 0.7
                    color: Tone.c(Appearance.Palette, "#f8fafc")
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: factColumn.implicitHeight + (block.modelData.boxed === false ? 0 : 20)
                    radius: 8
                    color: block.modelData.boxed === false ? "transparent" : (block.modelData.tinted ? Qt.rgba(sections.accent.r, sections.accent.g, sections.accent.b, 0.08) : Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.04)))
                    border.width: block.modelData.boxed === false ? 0 : 1
                    border.color: block.modelData.tinted ? Qt.rgba(sections.accent.r, sections.accent.g, sections.accent.b, 0.22) : Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.08))

                    ColumnLayout {
                        id: factColumn
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: block.modelData.boxed === false ? 0 : 10
                        spacing: 6

                        Repeater {
                            model: block.kind === "facts" ? (block.modelData.rows || []) : []

                            RowLayout {
                                id: factRow
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 8
                                Text {
                                    text: sections.shell.tr(factRow.modelData, "label")
                                    font.pixelSize: 11
                                    opacity: 0.75
                                    color: Tone.c(Appearance.Palette, "#f8fafc")
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: sections.shell.tr(factRow.modelData, "value")
                                    font.pixelSize: 11
                                    font.bold: true
                                    color: sections.toneColor(factRow.modelData.tone || "", Tone.c(Appearance.Palette, "#f8fafc"))
                                    horizontalAlignment: Text.AlignRight
                                    elide: Text.ElideLeft
                                    Layout.maximumWidth: 260
                                }
                            }
                        }
                    }
                }
            }

            // ── Note: a grey footnote ───────────────────────────────────────
            Text {
                visible: block.kind === "note"
                Layout.fillWidth: true
                text: sections.shell.tr(block.modelData, "text")
                font.pixelSize: 9
                color: Tone.c(Appearance.Palette, "#94a3b8")
                opacity: 0.85
                wrapMode: Text.WordWrap
            }
        }
    }
}
