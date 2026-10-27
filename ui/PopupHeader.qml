import QtQuick
import "theme" as Appearance
import QtQuick.Window
import QtQuick.Layouts
import QtQuick.Effects
import QtQuick.Controls.Basic as QC
import "js/FeatureTabs.js" as FeatureTabs
import "js/Tone.js" as Tone

// The popup's top: title bar and provider tabs. Split from PopupContent so a host
// can keep it fixed above the scrolling body (see hosts/*). `shell` is the same
// frontend root PopupContent reads.
ColumnLayout {
    id: content

    property var shell
    // What "Save as picture" captures: the whole popup, set by the host.
    property Item snapshotTarget: content

    // Leave Backspace to editable text controls while entering keys or searches.
    Shortcut {
        sequence: "Backspace"
        enabled: content.shell.showSettings && !content.editingText
        onActivated: content.shell.settingsBack()
    }
    readonly property bool editingText: {
        var window = content.Window.window;
        var item = window ? window.activeFocusItem : null;
        return item !== null && typeof item.cursorPosition !== "undefined" && item.readOnly !== true;
    }

    spacing: 12

    // ── Header ──────────────────────────────────────────────────
    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        // Above the rows below, which the status chip's
        // hover card overlaps.
        z: 2

        Item {
            id: headerBadge
            Layout.preferredWidth: 22
            Layout.preferredHeight: 22

            // Brand logos carry their own colours, so they render as-is.
            // The generic app icon has none, so it is tinted to the active
            // accent over a soft halo — the settings page always uses it.
            readonly property string brandLogo: shell.showSettings || shell.activeIsFeature ? "" : shell.providerIcon(shell.activeProvider())

            Image {
                id: headerHalo
                anchors.centerIn: parent
                width: 22
                height: 22
                source: shell.iconSource
                sourceSize.width: 22
                sourceSize.height: 22
                visible: false
            }
            MultiEffect {
                anchors.fill: headerHalo
                source: headerHalo
                visible: !shell.showSettings && headerBadge.brandLogo === ""
                colorization: 1
                colorizationColor: shell.activeAccent
                opacity: 0.22
            }
            Image {
                id: headerIcon
                anchors.centerIn: parent
                width: 18
                height: 18
                source: shell.iconSource
                sourceSize.width: 18
                sourceSize.height: 18
                visible: false
            }
            MultiEffect {
                anchors.fill: headerIcon
                source: headerIcon
                visible: !shell.showSettings && headerBadge.brandLogo === ""
                colorization: 1
                colorizationColor: shell.activeAccent
            }
            Image {
                anchors.centerIn: parent
                width: 18
                height: 18
                source: headerBadge.brandLogo !== "" ? headerBadge.brandLogo : shell.iconSource
                layer.enabled: Tone.isLight(Appearance.Palette) && Tone.paleLogo(source)
                layer.effect: MultiEffect {
                    brightness: -0.8
                }
                sourceSize.width: 18
                sourceSize.height: 18
                fillMode: Image.PreserveAspectFit
                visible: shell.showSettings || headerBadge.brandLogo !== ""
            }
        }

        ColumnLayout {
            spacing: 0
            Text {
                text: {
                    if (shell.settingsSaveError)
                        return shell.i18n("Settings not saved");
                    if (shell.showSettings)
                        return shell.i18n("Settings");
                    if (shell.activeIsFeature)
                        return FeatureTabs.label(shell.activeId, shell.i18n);
                    var p = shell.activeProvider();
                    return shell.i18n("%1 Usage", p ? p.label : "AI");
                }
                font.bold: true
                font.pixelSize: 15
                color: shell.settingsSaveError ? shell.dangerColor : Tone.c(Appearance.Palette, "#f8fafc")
            }
            Text {
                visible: shell.showSettings || shell.activeIsFeature || !!shell.settingsSaveError
                text: {
                    if (shell.settingsSaveError)
                        return shell.settingsSaveError;
                    if (shell.showSettings) {
                        if (shell.settingsSection === "panel")
                            return shell.pillControls ? shell.i18n("Views, pill, position and chart") : shell.i18n("Views, language and usage chart");

                        if (shell.settingsSection === "appearance")
                            return shell.i18n("Popup glass, tint and colours");

                        if (shell.settingsSection === "info")
                            return shell.i18n("About tideSI and project links");

                        if (shell.settingsSection === "local")
                            return shell.i18n("Local endpoints: Ollama, vLLM and llama.cpp");

                        if (shell.settingsSection === "data")
                            return shell.i18n("Refresh interval and usage history");

                        if (shell.settingsSection === "advanced")
                            return shell.interpreterControls ? shell.i18n("Python interpreter and terminal tool") : shell.i18n("Start at login");

                        return shell.i18n("Turn providers on and set their keys");
                    }
                    if (shell.activeId === "overview") {
                        var pList = shell.providers || [];
                        return shell.i18np("%1 provider", "%1 providers", pList.length);
                    }
                    if (shell.activeId === "sessions")
                        return shell.sessionsLoading ? shell.i18n("Refreshing…") : shell.i18np("%1 local session", "%1 local sessions", shell.sessionsTotal || 0);
                    if (shell.activeId === "spend") {
                        // Provider/API total: metered spend and plan-inclusive spend
                        var providerRows = FeatureTabs.spendProviderRows(shell.providers, shell.localSpend);
                        var allRows = providerRows.concat(FeatureTabs.localSpendRows(shell.localSpend, providerRows, shell.providers));
                        // The same timeframe the Spend view below is cut to.
                        allRows = FeatureTabs.spendRowsForWindow(allRows, Number(shell.settings.spendWindowDays || 0));
                        return FeatureTabs.spendSummaryText(allRows, "USD", shell.i18n);
                    }
                    return "";
                }
                font.pixelSize: 10
                opacity: shell.settingsSaveError ? 1 : 0.5
                color: shell.settingsSaveError ? shell.dangerColor : Tone.c(Appearance.Palette, "#f8fafc")
            }
        }

        Item {
            Layout.fillWidth: true
        }

        // Save a picture of the popup (PNG or SVG, into Downloads).
        Rectangle {
            id: saveButton
            visible: !shell.showSettings && typeof shell.exportSnapshot === "function"
            Layout.preferredWidth: 32
            Layout.preferredHeight: 32
            radius: 6
            color: saveMouse.containsMouse || saveMenu.visible ? Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.11)) : "transparent"

            Image {
                anchors.centerIn: parent
                width: 20
                height: 20
                sourceSize.width: 40
                sourceSize.height: 40
                source: shell.iconDir + "header-save.svg"
                // Light-grey line icons: darkened under the light theme.
                layer.enabled: Tone.isLight(Appearance.Palette)
                layer.effect: MultiEffect {
                    brightness: -0.8
                }
                opacity: saveMouse.containsMouse || saveMenu.visible ? 1.0 : 0.7
            }
            MouseArea {
                id: saveMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: saveMenu.visible ? saveMenu.close() : saveMenu.open()
            }

            // Tooltip, drawn here: QQC2's is not available under every host.
            Rectangle {
                visible: saveMouse.containsMouse && !saveMenu.visible
                y: parent.height + 4
                x: parent.width - width
                width: saveTip.implicitWidth + 16
                height: saveTip.implicitHeight + 10
                radius: 5
                z: 10
                color: Tone.c(Appearance.Palette, Qt.rgba(0.04, 0.045, 0.06, 0.96))
                border.width: 1
                border.color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.14))
                Text {
                    id: saveTip
                    anchors.centerIn: parent
                    text: shell.i18n("Save this view as a picture")
                    font.pixelSize: 11
                    color: Tone.c(Appearance.Palette, "#e2e8f0")
                }
            }

            QC.Popup {
                id: saveMenu
                y: parent.height + 4
                x: parent.width - width
                padding: 4
                closePolicy: QC.Popup.CloseOnEscape | QC.Popup.CloseOnPressOutside
                background: Rectangle {
                    radius: 6
                    color: Tone.c(Appearance.Palette, "#12141a")
                    border.width: 1
                    border.color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.14))
                }
                contentItem: Column {
                    spacing: 2
                    Repeater {
                        model: [
                            {
                                format: "png",
                                label: shell.i18n("Save as PNG")
                            },
                            {
                                format: "svg",
                                label: shell.i18n("Save as SVG")
                            }
                        ]
                        Rectangle {
                            required property var modelData
                            width: Math.max(menuLabel.implicitWidth + 24, 120)
                            height: 26
                            radius: 4
                            color: menuMouse.containsMouse ? Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.10)) : "transparent"
                            Text {
                                id: menuLabel
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.left: parent.left
                                anchors.leftMargin: 10
                                text: parent.modelData.label
                                font.pixelSize: 11
                                color: Tone.c(Appearance.Palette, "#f8fafc")
                            }
                            MouseArea {
                                id: menuMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    saveMenu.close();
                                    shell.exportSnapshot(content.snapshotTarget, parent.modelData.format);
                                }
                            }
                        }
                    }
                }
            }
        }

        // Settings gear / back toggle
        Rectangle {
            Layout.preferredWidth: 32
            Layout.preferredHeight: 32
            radius: 6
            color: gearMouse.containsMouse || shell.showSettings ? Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.11)) : "transparent"

            Image {
                anchors.centerIn: parent
                width: 20
                height: 20
                sourceSize.width: 40
                sourceSize.height: 40
                source: shell.iconDir + (shell.showSettings ? "header-back.svg" : "header-settings.svg")
                // Light-grey line icons: darkened under the light theme.
                layer.enabled: Tone.isLight(Appearance.Palette)
                layer.effect: MultiEffect {
                    brightness: -0.8
                }
                opacity: gearMouse.containsMouse || shell.showSettings ? 1.0 : 0.7
            }
            MouseArea {
                id: gearMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                    if (shell.showSettings)
                        shell.settingsBack();
                    else
                        shell.showSettings = true;
                }
            }
        }

        Rectangle {
            visible: !shell.showSettings
            Layout.preferredWidth: 32
            Layout.preferredHeight: 32
            radius: 6
            color: refreshMouse.containsMouse ? Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.11)) : "transparent"

            Image {
                anchors.centerIn: parent
                width: 20
                height: 20
                sourceSize.width: 40
                sourceSize.height: 40
                source: shell.iconDir + "header-refresh.svg"
                // Light-grey line icons: darkened under the light theme.
                layer.enabled: Tone.isLight(Appearance.Palette)
                layer.effect: MultiEffect {
                    brightness: -0.8
                }
                opacity: refreshMouse.containsMouse ? 1.0 : 0.7
                rotation: shell.loading ? refreshSpin.value : 0
            }
            // simple spin while a refresh is running
            Item {
                id: refreshSpin
                property real value: 0
                NumberAnimation on value {
                    running: shell.loading
                    from: 0
                    to: 360
                    duration: 900
                    loops: Animation.Infinite
                }
            }

            MouseArea {
                id: refreshMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                    shell.refresh(true);
                    if (shell.sessionsViewVisible && typeof shell.reconcileSessions === "function")
                        shell.reconcileSessions(shell.sessionsQuery || "");
                }
            }
        }
    }

    // ── Tab bar ─────────────────────────────────────────────────
    // One row of equal-width tabs, whatever their number: a tab narrower than
    // 62 px drops its name and keeps its logo (the name is a hover tooltip), so
    // eleven providers still fit one row instead of wrapping onto four. The pin
    // sits in a tab's corner (always while pinned, on hover otherwise); a right
    // click pins too.
    RowLayout {
        id: tabBar
        Layout.fillWidth: true
        spacing: 4
        visible: shell.popupTabs.length > 1 && !shell.showSettings

        Repeater {
            model: shell.popupTabs

            Item {
                id: tabCell
                required property var modelData
                required property int index
                readonly property bool isActive: shell.activeId === modelData.id
                readonly property bool labelFits: width > 62
                readonly property bool canPin: !modelData.feature && typeof shell.togglePin === "function"
                readonly property bool pinned: canPin && typeof shell.isPinned === "function" && shell.isPinned(modelData.id)

                // Equal shares of the row.
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.minimumWidth: 0
                Layout.preferredHeight: 32
                // Above its neighbours while its tooltip is out.
                z: tabMouse.containsMouse ? 5 : 0

                Rectangle {
                    id: tab
                    anchors.fill: parent
                    radius: 6
                    clip: true
                    color: tabCell.isActive ? Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.10)) : "transparent"
                    border.width: 1
                    border.color: tabCell.isActive ? Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.20)) : Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.08))
                    Behavior on color {
                        ColorAnimation {
                            duration: 150
                        }
                    }

                    MouseArea {
                        id: tabMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        acceptedButtons: Qt.LeftButton | Qt.RightButton
                        onClicked: mouse => {
                            if (mouse.button === Qt.RightButton) {
                                if (tabCell.canPin)
                                    shell.togglePin(tabCell.modelData.id);
                                return;
                            }
                            var wasSessions = tabCell.modelData.id === "sessions" && shell.activeId === "sessions";
                            shell.activeId = tabCell.modelData.id;
                            // A shell may throttle what a tab switch fetches
                            // (the Windows app does); the ⟳ button always refreshes.
                            if (tabCell.modelData.feature) {
                                if (wasSessions) {
                                    if (typeof shell.reconcileSessions === "function")
                                        shell.reconcileSessions();
                                    else if (typeof shell.refreshSessions === "function")
                                        shell.refreshSessions();
                                }
                                return;
                            }
                            if (typeof shell.refreshTab === "function")
                                shell.refreshTab();
                            else
                                shell.refresh();
                        }
                        Rectangle {
                            anchors.fill: parent
                            radius: 6
                            color: parent.containsMouse && !tabCell.isActive ? Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.05)) : "transparent"
                        }
                    }

                    RowLayout {
                        id: tabContent
                        // Centred, but never wider than the tab, so the content
                        // cannot spill onto its neighbours.
                        anchors.centerIn: parent
                        width: Math.min(implicitWidth, parent.width - 14)
                        spacing: 5

                        Image {
                            readonly property string logo: shell.providerIcon(tabCell.modelData)
                            visible: logo !== "" && status !== Image.Error
                            source: logo
                            layer.enabled: Tone.isLight(Appearance.Palette) && Tone.paleLogo(source)
                            layer.effect: MultiEffect {
                                brightness: -0.8
                            }
                            Layout.preferredWidth: 13
                            Layout.preferredHeight: 13
                            Layout.alignment: Qt.AlignVCenter
                            sourceSize.width: 26
                            sourceSize.height: 26
                            fillMode: Image.PreserveAspectFit
                            smooth: true
                            opacity: tabCell.isActive ? 1.0 : (Tone.isLight(Appearance.Palette) ? 0.85 : 0.55)
                        }
                        // Providers without a logo get their accent as a dot.
                        Rectangle {
                            visible: shell.providerIcon(tabCell.modelData) === ""
                            Layout.preferredWidth: 8
                            Layout.preferredHeight: 8
                            Layout.alignment: Qt.AlignVCenter
                            radius: 4
                            color: tabCell.modelData.accent
                            opacity: tabCell.isActive ? 1.0 : 0.5
                        }
                        Text {
                            visible: tabCell.labelFits
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignVCenter
                            text: tabCell.modelData.label
                            elide: Text.ElideRight
                            horizontalAlignment: Text.AlignHCenter
                            font.pixelSize: 12
                            font.bold: tabCell.isActive
                            color: Tone.c(Appearance.Palette, "#f8fafc")
                            opacity: tabCell.isActive ? 1.0 : 0.6
                        }
                    }

                    // Pin toggle: a pushpin drawn as head + needle, so it does
                    // not depend on an icon theme or an emoji font.
                    Item {
                        id: pinButton
                        visible: tabCell.canPin && (tabCell.pinned || tabMouse.containsMouse || pinMouse.containsMouse)
                        anchors.top: parent.top
                        anchors.right: parent.right
                        anchors.topMargin: 2
                        anchors.rightMargin: 2
                        width: 10
                        height: 12
                        opacity: tabCell.pinned ? 1.0 : (pinMouse.containsMouse ? 0.9 : 0.45)
                        Rectangle {
                            x: 1
                            y: 0
                            width: 8
                            height: 8
                            radius: 4
                            color: tabCell.pinned ? tabCell.modelData.accent : "transparent"
                            border.width: 1.5
                            border.color: tabCell.pinned ? tabCell.modelData.accent : Tone.c(Appearance.Palette, "#f8fafc")
                        }
                        Rectangle {
                            x: 4
                            y: 8
                            width: 2
                            height: 4
                            color: tabCell.pinned ? tabCell.modelData.accent : Tone.c(Appearance.Palette, "#f8fafc")
                        }
                        MouseArea {
                            id: pinMouse
                            anchors.fill: parent
                            anchors.margins: -3
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: shell.togglePin(tabCell.modelData.id)
                        }
                    }
                }

                // The name of an icon-only tab (QQC2's ToolTip is not available
                // under every host, so it is drawn here).
                Rectangle {
                    id: tabTip
                    visible: !tabCell.labelFits && tabMouse.containsMouse
                    y: tabCell.height + 4
                    x: Math.max(-tabCell.x, Math.min(tabBar.width - tabCell.x - width, (tabCell.width - width) / 2))
                    width: tipLabel.implicitWidth + 16
                    height: tipLabel.implicitHeight + 10
                    radius: 5
                    color: Tone.c(Appearance.Palette, Qt.rgba(0.04, 0.045, 0.06, 0.96))
                    border.width: 1
                    border.color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.14))
                    Text {
                        id: tipLabel
                        anchors.centerIn: parent
                        text: (tabCell.modelData.label || "") + (tabCell.pinned ? "  ·  " + shell.i18n("Pinned on panel") : "")
                        font.pixelSize: 11
                        color: Tone.c(Appearance.Palette, "#e2e8f0")
                    }
                }
            }
        }
    }

    Rectangle {
        visible: !shell.showSettings
        Layout.fillWidth: true
        Layout.preferredHeight: 1
        color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.08))
    }
}
