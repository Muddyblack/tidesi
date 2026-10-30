import QtQuick
import "theme" as Appearance
import "studio"
import "studio/Theme.js" as Theme
import "js/Tone.js" as Tone
import "js/ProviderOrder.js" as ProviderOrder
import "js/ProviderSources.js" as ProviderSources

// The providers that are switched on, each with how it is being read, and the
// way to add another: Settings → Providers. A long list of switches does not
// scale to a couple of dozen providers; this shows only what is in use, and
// puts the rest behind a search. Drag a row by its grip to change the order the
// providers are listed in here and in the popup.
Column {
    id: list

    property var shell

    // Emitted for the page to navigate on.
    signal addRequested
    signal opened(string id)

    // Local Models has a page of its own (Settings → Local Models).
    readonly property var rows: list.shell.arrangeProviders(ProviderSources.added((list.shell.allProviders || []).filter(function (p) {
        return p.id !== "selfhosted";
    }), list.shell.providerEnabled))
    readonly property real rowHeight: 56
    // The row being dragged and the place it would land, or -1 while none is.
    property int dragFrom: -1
    property int dragTo: -1

    function moveRow(from, to) {
        list.shell.setProviderOrder(ProviderOrder.move(list.rows.map(function (p) {
            return p.id;
        }), from, to));
    }

    width: parent ? parent.width : implicitWidth
    spacing: 12

    // Detection first: it is what fills the list on a new install.
    Item {
        width: parent.width
        height: 30

        Row {
            spacing: 12
            anchors.verticalCenter: parent.verticalCenter
            StudioButton {
                text: list.shell.providerDetectBusy ? list.shell.i18n("Detecting…") : list.shell.i18n("Detect installed providers")
                enabled: !list.shell.providerDetectBusy
                onClicked: list.shell.redetectProviders()
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                width: Math.min(implicitWidth, 260)
                visible: list.shell.providerDetectStatus !== ""
                text: list.shell.providerDetectStatus
                color: Tone.c(Appearance.Palette, Theme.muted)
                font.pixelSize: 10
                elide: Text.ElideRight
            }
        }

        StudioButton {
            anchors.right: parent.right
            primary: true
            text: "+  " + list.shell.i18n("Add provider")
            onClicked: list.addRequested()
        }
    }

    StudioCard {
        title: list.shell.i18n("Providers")
        caption: list.shell.i18n("Keys are stored in %1; leave one blank to use env vars or an existing CLI login.", list.shell.configPath)

        StudioNote {
            visible: list.rows.length === 0
            text: list.shell.i18n("No providers yet. Use Add provider, or Detect installed providers to switch on what is installed.")
        }
        // Keeps the card's padding when it holds only the note above.
        Item {
            width: 1
            height: list.rows.length === 0 ? 12 : 0
        }

        Repeater {
            model: list.rows

            Item {
                id: row

                required property var modelData
                required property int index

                width: parent.width
                height: list.rowHeight
                z: grip.dragging ? 1 : 0
                transform: Translate {
                    y: grip.offset
                }

                Rectangle {
                    anchors.fill: parent
                    radius: 8
                    visible: grip.dragging
                    color: Tone.c(Appearance.Palette, Theme.lifted)
                    border.width: 1
                    border.color: Tone.c(Appearance.Palette, Theme.line2)
                }
                Rectangle {
                    width: parent.width
                    height: 1
                    color: Tone.c(Appearance.Palette, Theme.line)
                    visible: row.index > 0 && !grip.dragging
                }
                MouseArea {
                    id: rowArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: list.opened(row.modelData.id)
                }

                StudioReorderHandle {
                    id: grip
                    anchors.verticalCenter: parent.verticalCenter
                    index: row.index
                    count: list.rows.length
                    rowHeight: list.rowHeight
                    onMoved: (from, to) => list.moveRow(from, to)
                    onDraggingChanged: {
                        list.dragFrom = dragging ? row.index : -1;
                        list.dragTo = dragging ? target : -1;
                    }
                    onTargetChanged: {
                        if (dragging)
                            list.dragTo = target;
                    }
                }
                // Where the dragged row will land: on the near edge of the row it
                // would take the place of.
                Rectangle {
                    readonly property bool above: list.dragTo < list.dragFrom
                    visible: list.dragFrom !== -1 && list.dragTo !== list.dragFrom && row.index === list.dragTo
                    x: 0
                    y: above ? -height / 2 : parent.height - height / 2
                    width: parent.width
                    height: 3
                    radius: 1.5
                    color: Tone.c(Appearance.Palette, Theme.brand)
                }
                ProviderBadge {
                    id: badge
                    x: grip.width + 8
                    anchors.verticalCenter: parent.verticalCenter
                    provider: row.modelData
                    shell: list.shell
                }
                Column {
                    anchors.left: badge.right
                    anchors.leftMargin: 12
                    anchors.right: buttons.left
                    anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 2
                    Text {
                        width: parent.width
                        text: row.modelData.label
                        color: Tone.c(Appearance.Palette, Theme.text)
                        font.pixelSize: 12
                        font.weight: Font.Medium
                        elide: Text.ElideRight
                    }
                    Text {
                        width: parent.width
                        text: list.shell.providerStatusText(row.modelData.id)
                        color: Tone.ink(Appearance.Palette, Theme.tone(list.shell.providerStatusTone(row.modelData.id)))
                        font.pixelSize: 10
                        elide: Text.ElideRight
                    }
                }
                Row {
                    id: buttons
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6
                    StudioIconButton {
                        icon: "edit"
                        onClicked: list.opened(row.modelData.id)
                    }
                    StudioIconButton {
                        icon: "trash"
                        danger: true
                        onClicked: {
                            list.shell.setSetting("providers", row.modelData.id, false);
                            list.shell.refresh();
                        }
                    }
                }
            }
        }
    }
}
