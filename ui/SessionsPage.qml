import QtQuick
import "theme" as Appearance
import QtQuick.Layouts
import QtQuick.Controls.Basic as QC
import "js/Accounts.js" as Accounts
import "js/FeatureTabs.js" as FeatureTabs
import "js/SessionSources.js" as SessionSources
import "js/Tone.js" as Tone

ColumnLayout {
    id: page

    property var shell
    spacing: 10

    readonly property var sessions: shell.sessions || []
    readonly property bool loading: shell.sessionsLoading === true
    readonly property string errorText: shell.sessionsError || ""
    readonly property string notice: shell.sessionsNotice || ""
    readonly property string cacheStatus: shell.sessionsCacheStatus || "unknown"
    readonly property var cacheAgeSeconds: shell.sessionsCacheAgeSeconds === undefined ? null : shell.sessionsCacheAgeSeconds
    readonly property string refreshStatus: shell.sessionsRefreshStatus || "not-run"
    readonly property int removedSourceCount: shell.sessionsRemovedSourceCount || 0
    property string filterText: ""
    readonly property string searchQuery: (filterText || "").trim()
    readonly property int sessionsTotal: shell.sessionsTotal || 0
    property double clockMs: Date.now()
    readonly property int sessionsLimit: shell.sessionsLimit || 60
    readonly property int sessionsOffset: shell.sessionsOffset || 0
    readonly property int totalPages: Math.max(1, Math.ceil(sessionsTotal / sessionsLimit))
    readonly property int currentPage: Math.min(totalPages, Math.max(1, Math.floor(sessionsOffset / sessionsLimit) + 1))

    function paginationItems(current, total) {
        if (total <= 1)
            return [];
        if (total <= 7) {
            var items = [];
            for (var i = 1; i <= total; i++)
                items.push(i);
            return items;
        }
        if (current <= 4)
            return [1, 2, 3, 4, 5, "…", total];
        if (current >= total - 3)
            return [1, "…", total - 4, total - 3, total - 2, total - 1, total];
        return [1, "…", current - 1, current, current + 1, "…", total];
    }

    function goToPage(pageNumber) {
        if (page.loading)
            return;
        var p = Math.max(1, Math.min(pageNumber, totalPages));
        var targetOffset = (p - 1) * sessionsLimit;
        if (targetOffset === sessionsOffset && sessions.length > 0)
            return;
        listFlick.contentY = 0;
        if (typeof shell.querySessions === "function")
            shell.querySessions(page.searchQuery, targetOffset, false, page.selectedSourceIds);
    }
    readonly property var sessionSources: shell.sessionsSources || []
    readonly property var selectedSourceIds: shell.sessionsSourceIds || []
    readonly property bool sourceSelectionIsAll: selectedSourceIds.length === 0
    property var pendingSourceIds: []
    readonly property bool pendingSourceSelectionIsAll: page.pendingSourceIds.length === 0
    readonly property var sourceOptions: {
        var options = [];
        if (page.sessionSources.length > 1)
            options.push({
                id: "",
                label: shell.i18n("All sources"),
                isAll: true
            });
        for (var i = 0; i < page.sessionSources.length; i++)
            options.push({
                id: page.sessionSources[i].id,
                label: page.sessionSources[i].label,
                isAll: false
            });
        return options;
    }
    readonly property string sourceSummary: {
        if (page.sessionSources.length === 0 || page.sourceSelectionIsAll) {
            if (page.sessionSources.length === 1)
                return page.sessionSources[0].label;
            return shell.i18n("All sources");
        }
        if (page.selectedSourceIds.length === 1) {
            for (var i = 0; i < page.sessionSources.length; i++)
                if (page.sessionSources[i].id === page.selectedSourceIds[0])
                    return page.sessionSources[i].label;
        }
        return page.shell.i18np("%1 source selected", "%1 sources selected", page.selectedSourceIds.length);
    }

    readonly property var displayedSessions: sessions

    function setSourceSelection(ids) {
        if (typeof shell.setSessionsSourceIds === "function")
            shell.setSessionsSourceIds(ids);
        if (typeof shell.querySessions === "function")
            shell.querySessions(page.searchQuery);
    }

    function stageSourceSelection(ids) {
        page.pendingSourceIds = SessionSources.normalizeIds(ids, page.sessionSources);
    }

    function stageToggleSource(id, checked) {
        if (page.sessionSources.length <= 1)
            return;
        page.stageSourceSelection(SessionSources.toggled(page.pendingSourceIds, id, checked, page.sessionSources));
    }

    function commitSourceSelection() {
        if (SessionSources.signature(page.pendingSourceIds) === SessionSources.signature(page.selectedSourceIds))
            return;
        page.setSourceSelection(page.pendingSourceIds);
    }

    function toggleSource(id, checked) {
        if (page.sessionSources.length <= 1)
            return;
        page.setSourceSelection(SessionSources.toggled(page.selectedSourceIds, id, checked, page.sessionSources));
    }

    function sourceIcon(id) {
        if (!id)
            return shell.iconSource || "";
        var iconDir = shell.iconDir || (Qt.resolvedUrl("../assets/icons/").toString());
        if (id === "codex")
            return iconDir + "codex.svg";
        if (id === "junie")
            return iconDir + "junie.svg";
        if (id === "mimo")
            return iconDir + "mimo.svg";
        if (id === "opencode")
            return iconDir + "opencode-color.svg";
        var p = shell.providerById ? shell.providerById(id) : null;
        if (p && shell.providerIcon) {
            var icon = shell.providerIcon(p);
            if (icon)
                return icon;
        }
        var fileMap = {
            "claude": "claude-color.svg",
            "antigravity": "antigravity-color.svg",
            "openai": "codex.svg",
            "cline": "cline.svg",
            "muse": "muse-color.svg",
            "grok": "grok.svg",
            "cursor": "cursor.svg",
            "copilot": "githubcopilot.svg",
            "kimi": "kimi.svg",
            "kiro": "kiro.svg",
            "deepseek": "deepseek-color.svg",
            "mistral": "mistral-color.svg",
            "openrouter": "openrouter.svg",
            "zai": "zai.svg"
        };
        // An extra account (claude@work) wears its provider's logo.
        var base = Accounts.baseId(id);
        if (fileMap[base])
            return iconDir + fileMap[base];
        return "";
    }

    function sourceColor(id) {
        if (!id)
            return "#a78bfa";
        var p = shell.providerById ? shell.providerById(id) : null;
        if (p && p.accent)
            return p.accent;
        var colorMap = {
            "claude": "#cc785c",
            "antigravity": "#4285f4",
            "openai": "#10a37f",
            "codex": "#10a37f",
            "cline": "#007acc",
            "muse": "#0064e0",
            "grok": "#ef4444",
            "opencode": Tone.c(Appearance.Palette, "#B7B1B1"),
            "mimo": Tone.c(Appearance.Palette, "#E8E8E8"),
            "junie": "#48e054",
            "cursor": Tone.c(Appearance.Palette, "#e6e6e6"),
            "copilot": "#8b5cf6",
            "kimi": "#1e3a8a",
            "kiro": "#8b5cf6",
            "deepseek": "#4f8cff",
            "mistral": "#ff7000",
            "openrouter": "#9333ea",
            "zai": "#126ef4"
        };
        return colorMap[Accounts.baseId(id)] || "#a78bfa";
    }

    function reconcileDisplayedSessions() {
        if (typeof shell.reconcileSessions === "function")
            shell.reconcileSessions(page.searchQuery, page.selectedSourceIds);
        else if (typeof shell.refreshSessions === "function")
            shell.refreshSessions(page.searchQuery, undefined, false, page.selectedSourceIds);
    }

    function refreshDisplayedSessionsOnVisibility() {
        if (typeof shell.reconcileSessions === "function") {
            if (shell.sessionsViewVisible === true && shell.sessionsLoading !== true)
                shell.reconcileSessions(page.searchQuery, page.selectedSourceIds);
        } else if (typeof shell.refreshSessions === "function") {
            shell.refreshSessions(page.searchQuery, undefined, false, page.selectedSourceIds);
        }
    }

    onVisibleChanged: {
        if (visible) {
            clockMs = Date.now();
            page.refreshDisplayedSessionsOnVisibility();
        }
    }

    Timer {
        id: searchTimer
        interval: 300
        repeat: false
        onTriggered: {
            if (typeof page.shell.querySessions === "function")
                page.shell.querySessions(page.searchQuery, undefined, false, page.selectedSourceIds);
            else if (typeof page.shell.refreshSessions === "function")
                page.shell.refreshSessions(page.searchQuery, undefined, false, page.selectedSourceIds);
        }
    }

    Timer {
        interval: 30000
        repeat: true
        running: page.visible && page.sessions.length > 0
        onTriggered: page.clockMs = Date.now()
    }

    RowLayout {
        visible: page.sessions.length > 0 || page.searchQuery !== "" || searchTimer.running || page.loading || page.sessionSources.length > 0
        Layout.fillWidth: true
        spacing: 6

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 28
            radius: 6
            color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.06))
            border.width: 1
            border.color: searchField.activeFocus ? Qt.rgba(0.31, 0.62, 0.87, 0.6) : Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.12))

            QC.TextField {
                id: searchField
                anchors.fill: parent
                anchors.leftMargin: 8
                anchors.rightMargin: 8
                text: page.filterText
                font.pixelSize: 11
                color: Tone.c(Appearance.Palette, "#f8fafc")
                placeholderText: page.shell.i18n("Search sessions…")
                placeholderTextColor: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.35))
                verticalAlignment: TextInput.AlignVCenter
                background: null
                selectByMouse: true
                Accessible.name: page.shell.i18n("Search sessions")
                onTextEdited: {
                    page.filterText = searchField.text;
                    if (typeof page.shell.setSessionsQuery === "function")
                        page.shell.setSessionsQuery(page.searchQuery);
                    searchTimer.restart();
                }
            }
        }

        SettingsButton {
            id: sourceSelectorButton
            visible: page.sessionSources.length > 0
            Layout.preferredWidth: 124
            Layout.minimumWidth: 86
            Layout.maximumWidth: 145
            text: page.sourceSummary + "  ▾"
            Accessible.name: page.shell.i18n("Filter sessions by source")
            onClicked: sourcePopup.open()

            QC.Popup {
                id: sourcePopup
                x: Math.max(-sourceSelectorButton.x, sourceSelectorButton.width - sourcePopup.width)
                y: sourceSelectorButton.height + 4
                width: 204
                height: Math.min(320, popupCol.implicitHeight + 16)
                padding: 6
                focus: true
                modal: false
                closePolicy: QC.Popup.CloseOnEscape | QC.Popup.CloseOnPressOutside
                onOpened: page.pendingSourceIds = page.selectedSourceIds.slice(0)
                onClosed: page.commitSourceSelection()

                background: Rectangle {
                    radius: 10
                    color: Tone.c(Appearance.Palette, Qt.rgba(0.08, 0.09, 0.12, 0.98))
                    border.width: 1
                    border.color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.14))

                    Rectangle {
                        anchors.top: parent.top
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.margins: 1
                        height: 1
                        color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.12))
                        radius: 10
                    }
                }

                contentItem: QC.ScrollView {
                    clip: true
                    QC.ScrollBar.horizontal.policy: QC.ScrollBar.AlwaysOff
                    QC.ScrollBar.vertical: QC.ScrollBar {
                        width: 6
                        policy: QC.ScrollBar.AsNeeded
                    }

                    ColumnLayout {
                        id: popupCol
                        width: sourcePopup.availableWidth
                        spacing: 2

                        Repeater {
                            model: page.sourceOptions

                            delegate: ColumnLayout {
                                id: itemCol
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 0

                                Rectangle {
                                    id: itemRow
                                    Layout.fillWidth: true
                                    implicitHeight: 30
                                    Layout.preferredHeight: 30
                                    radius: 6
                                    color: itemMouse.containsMouse ? Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.08)) : "transparent"

                                    readonly property bool isChecked: modelData.isAll ? page.pendingSourceSelectionIsAll : (page.pendingSourceSelectionIsAll || page.pendingSourceIds.indexOf(modelData.id) >= 0)
                                    readonly property color accent: page.sourceColor(modelData.id)

                                    Behavior on color {
                                        ColorAnimation {
                                            duration: 120
                                        }
                                    }

                                    MouseArea {
                                        id: itemMouse
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: modelData.isAll ? page.stageSourceSelection([]) : page.stageToggleSource(modelData.id, !itemRow.isChecked)
                                    }

                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 8
                                        anchors.rightMargin: 8
                                        spacing: 8

                                        // Custom styled checkbox
                                        Rectangle {
                                            Layout.preferredWidth: 16
                                            Layout.preferredHeight: 16
                                            radius: 4
                                            color: itemRow.isChecked ? Qt.rgba(itemRow.accent.r, itemRow.accent.g, itemRow.accent.b, 0.22) : "transparent"
                                            border.width: itemRow.isChecked ? 1.5 : 1
                                            border.color: itemRow.isChecked ? itemRow.accent : Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.25))

                                            Text {
                                                anchors.centerIn: parent
                                                visible: itemRow.isChecked
                                                text: "✓"
                                                font.pixelSize: 10
                                                font.bold: true
                                                color: itemRow.accent
                                            }
                                        }

                                        // Provider Icon
                                        Image {
                                            id: srcIcon
                                            Layout.preferredWidth: 14
                                            Layout.preferredHeight: 14
                                            sourceSize.width: 14
                                            sourceSize.height: 14
                                            fillMode: Image.PreserveAspectFit
                                            smooth: true
                                            source: page.sourceIcon(modelData.id)
                                            visible: source !== "" && status !== Image.Error
                                            opacity: itemRow.isChecked ? 1.0 : 0.55
                                        }

                                        // Fallback dot if no icon
                                        Rectangle {
                                            visible: !srcIcon.visible
                                            Layout.preferredWidth: 8
                                            Layout.preferredHeight: 8
                                            radius: 4
                                            color: itemRow.accent
                                            opacity: itemRow.isChecked ? 1.0 : 0.55
                                        }

                                        Text {
                                            Layout.fillWidth: true
                                            text: modelData.label
                                            font.pixelSize: 11
                                            font.bold: modelData.isAll && itemRow.isChecked
                                            color: itemRow.isChecked ? Tone.c(Appearance.Palette, "#f8fafc") : Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.65))
                                            elide: Text.ElideRight
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                    }
                                }

                                // Subtle divider below "All sources"
                                Rectangle {
                                    visible: modelData.isAll
                                    Layout.fillWidth: true
                                    Layout.topMargin: 4
                                    Layout.bottomMargin: 4
                                    implicitHeight: 1
                                    Layout.preferredHeight: 1
                                    color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.08))
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    Text {
        visible: page.cacheStatus === "no-cache" || page.cacheStatus === "stale" || page.cacheStatus === "empty" || page.refreshStatus === "incomplete" || page.refreshStatus === "failed" || page.removedSourceCount > 0
        Layout.fillWidth: true
        text: {
            if ((page.refreshStatus === "incomplete" || page.refreshStatus === "failed") && page.cacheStatus === "no-cache")
                return shell.i18n("Refresh failed; no cached sessions are available.");
            if (page.refreshStatus === "incomplete" || page.refreshStatus === "failed")
                return shell.i18n("Refresh incomplete; showing cached sessions.");
            if (page.cacheStatus === "no-cache")
                return shell.i18n("No cached session data yet.");
            if (page.removedSourceCount > 0)
                return shell.i18np("%1 session source was removed.", "%1 session sources were removed.", page.removedSourceCount);
            if (page.cacheAgeSeconds !== null)
                return shell.i18np("Cached session data · %1 min old", "Cached session data · %1 min old", Math.floor(page.cacheAgeSeconds / 60));
            return shell.i18n("Session cache status unavailable.");
        }
        wrapMode: Text.WordWrap
        opacity: 0.65
        color: Tone.c(Appearance.Palette, "#f8fafc")
        font.pixelSize: 10
    }

    Text {
        visible: page.errorText !== ""
        Layout.fillWidth: true
        text: page.errorText
        wrapMode: Text.WordWrap
        color: "#f87171"
        font.pixelSize: 11
    }

    Text {
        visible: page.notice !== ""
        Layout.fillWidth: true
        text: page.notice
        wrapMode: Text.WordWrap
        opacity: 0.7
        color: Tone.c(Appearance.Palette, "#f8fafc")
        font.pixelSize: 11
    }

    Text {
        visible: !page.loading && !searchTimer.running && page.searchQuery === "" && page.sessions.length === 0 && page.errorText === ""
        Layout.fillWidth: true
        text: shell.i18n("No local agent sessions found. They appear after Claude Code, Codex, Muse, Cline or Grok CLI records activity.")
        wrapMode: Text.WordWrap
        opacity: 0.55
        color: Tone.c(Appearance.Palette, "#f8fafc")
        font.pixelSize: 11
    }

    Text {
        visible: !page.loading && !searchTimer.running && page.searchQuery !== "" && page.sessions.length === 0 && page.errorText === ""
        Layout.fillWidth: true
        text: shell.i18n("No sessions match your search.")
        wrapMode: Text.WordWrap
        opacity: 0.55
        color: Tone.c(Appearance.Palette, "#f8fafc")
        font.pixelSize: 11
    }

    // Bounded and independently scrollable: the header above (title, count,
    // search) stays put instead of scrolling away with a long list — the
    // popup as a whole only grows to fit this box, not every row in it.
    Flickable {
        id: listFlick
        Layout.fillWidth: true
        Layout.rightMargin: 8
        Layout.preferredHeight: Math.min(360, listColumn.implicitHeight)
        visible: page.displayedSessions.length > 0
        clip: true
        contentWidth: width
        contentHeight: listColumn.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        QC.ScrollBar.vertical: QC.ScrollBar {
            id: verticalScrollBar
            policy: listFlick.interactive ? QC.ScrollBar.AsNeeded : QC.ScrollBar.AlwaysOff
            width: 12
            contentItem: Rectangle {
                implicitWidth: 6
                implicitHeight: 32
                radius: 3
                color: Tone.c(Appearance.Palette, "#f8fafc")
                opacity: verticalScrollBar.pressed ? 0.6 : (verticalScrollBar.hovered ? 0.45 : 0.25)
                Behavior on opacity {
                    NumberAnimation {
                        duration: 150
                    }
                }
            }
            background: Rectangle {
                implicitWidth: 12
                color: Tone.c(Appearance.Palette, "#f8fafc")
                opacity: verticalScrollBar.hovered ? 0.08 : 0.04
                radius: 6
            }
        }

        ColumnLayout {
            id: listColumn
            width: listFlick.width - (verticalScrollBar.visible ? (verticalScrollBar.width + 4) : 0)
            spacing: 10

            Repeater {
                model: page.displayedSessions

                Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: body.implicitHeight + 14
                    radius: 8
                    color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.04))
                    border.width: 1
                    border.color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.08))

                    readonly property bool activeSession: modelData.state === "active" || modelData.state === "running"
                    readonly property color accent: {
                        var p = shell.providerById ? shell.providerById(modelData.provider) : null;
                        return (p && p.accent) ? p.accent : "#a78bfa";
                    }

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (modelData.provider) {
                                shell.activeId = modelData.provider;
                                if (typeof shell.refreshTab === "function")
                                    shell.refreshTab();
                            }
                        }
                    }

                    RowLayout {
                        id: body
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 10

                        Rectangle {
                            Layout.preferredWidth: 8
                            Layout.preferredHeight: 8
                            radius: 4
                            color: accent
                            Layout.alignment: Qt.AlignTop
                            Layout.topMargin: 4
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Text {
                                text: modelData.title || modelData.provider || shell.i18n("Session")
                                textFormat: Text.PlainText
                                font.bold: true
                                font.pixelSize: 12
                                color: Tone.c(Appearance.Palette, "#f8fafc")
                                elide: Text.ElideRight
                                wrapMode: Text.NoWrap
                                Layout.fillWidth: true
                            }
                            Text {
                                visible: (modelData.sessionName || "") !== "" && modelData.sessionName !== modelData.title
                                text: modelData.sessionName || ""
                                textFormat: Text.PlainText
                                font.pixelSize: 10
                                opacity: 0.55
                                color: Tone.c(Appearance.Palette, "#f8fafc")
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Text {
                                visible: (modelData.detail || "") !== ""
                                text: modelData.detail || ""
                                textFormat: Text.PlainText
                                font.pixelSize: 10
                                opacity: 0.45
                                color: Tone.c(Appearance.Palette, "#f8fafc")
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Text {
                                text: page.sessionCostText(modelData)
                                font.pixelSize: 9
                                opacity: (modelData.costStatus || "unavailable") === "unavailable" ? 0.4 : 0.7
                                color: page.sessionCostColor(modelData)
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                        }

                        ColumnLayout {
                            spacing: 2
                            Layout.alignment: Qt.AlignTop
                            Text {
                                text: activeSession ? shell.i18n("Active") : shell.i18n("Idle")
                                font.bold: true
                                font.pixelSize: 11
                                color: activeSession ? accent : Tone.c(Appearance.Palette, "#f8fafc")
                                horizontalAlignment: Text.AlignRight
                                Layout.alignment: Qt.AlignRight
                            }
                            Text {
                                text: page.ageText(modelData.lastActivityAt)
                                font.pixelSize: 10
                                opacity: 0.45
                                color: Tone.c(Appearance.Palette, "#f8fafc")
                                horizontalAlignment: Text.AlignRight
                                Layout.alignment: Qt.AlignRight
                            }
                        }

                        Rectangle {
                            visible: (modelData.openKey || "") !== ""
                            Layout.preferredWidth: 26
                            Layout.preferredHeight: 24
                            Layout.alignment: Qt.AlignTop
                            radius: 6
                            color: resumeMouse.containsMouse ? Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.11)) : "transparent"
                            Rectangle {
                                anchors.centerIn: parent
                                width: 15
                                height: 12
                                radius: 2
                                color: "transparent"
                                border.width: 1
                                border.color: Tone.c(Appearance.Palette, "#e2e8f0")
                                opacity: resumeMouse.containsMouse ? 1 : 0.6
                                Text {
                                    anchors.centerIn: parent
                                    text: ">_"
                                    font.family: "monospace"
                                    font.pixelSize: 9
                                    font.bold: true
                                    color: Tone.c(Appearance.Palette, "#e2e8f0")
                                }
                            }
                            MouseArea {
                                id: resumeMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    if (typeof shell.openSession === "function")
                                        shell.openSession(modelData.openKey);
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // Numbered pages rather than an ever-growing "load more" list: the popup
    // keeps one page of rows, so its height stays put no matter how deep the
    // history goes. Mirrors the Plasma tab's control.
    RowLayout {
        id: paginationRow
        Layout.fillWidth: true
        Layout.topMargin: 2
        visible: page.totalPages > 1 && page.sessions.length > 0
        spacing: 4

        Item {
            Layout.fillWidth: true
        }

        Rectangle {
            id: prevBtn
            implicitWidth: 26
            implicitHeight: 26
            radius: 6
            readonly property bool active: !page.loading && page.currentPage > 1
            color: prevMouse.containsMouse && active ? Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.12)) : Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.04))
            border.width: 1
            border.color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.08))
            opacity: active ? 1.0 : 0.35

            Text {
                anchors.centerIn: parent
                text: "‹"
                font.pixelSize: 14
                font.bold: true
                color: Tone.c(Appearance.Palette, "#f8fafc")
            }

            MouseArea {
                id: prevMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: prevBtn.active ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: {
                    if (prevBtn.active)
                        page.goToPage(page.currentPage - 1);
                }
            }
        }

        Repeater {
            model: page.paginationItems(page.currentPage, page.totalPages)

            Rectangle {
                id: pageBtn
                required property var modelData
                readonly property bool isEllipsis: modelData === "…"
                readonly property bool isCurrent: !isEllipsis && Number(modelData) === page.currentPage
                implicitWidth: isEllipsis ? 18 : 26
                implicitHeight: 26
                radius: 6
                color: isCurrent ? "#38bdf8" : ((pageMouse.containsMouse && !isEllipsis && !page.loading) ? Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.12)) : (isEllipsis ? "transparent" : Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.04))))
                border.width: isEllipsis ? 0 : 1
                border.color: isCurrent ? "transparent" : Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.08))

                Text {
                    anchors.centerIn: parent
                    text: pageBtn.modelData
                    font.bold: pageBtn.isCurrent
                    font.pixelSize: pageBtn.isEllipsis ? 12 : 11
                    color: pageBtn.isCurrent ? Tone.c(Appearance.Palette, "#0b1220") : Tone.c(Appearance.Palette, "#f8fafc")
                    opacity: pageBtn.isEllipsis ? 0.45 : (page.loading ? 0.5 : 1.0)
                }

                MouseArea {
                    id: pageMouse
                    anchors.fill: parent
                    hoverEnabled: !pageBtn.isEllipsis
                    cursorShape: (!pageBtn.isEllipsis && !pageBtn.isCurrent && !page.loading) ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: {
                        if (!pageBtn.isEllipsis && !page.loading)
                            page.goToPage(Number(pageBtn.modelData));
                    }
                }
            }
        }

        Rectangle {
            id: nextBtn
            implicitWidth: 26
            implicitHeight: 26
            radius: 6
            readonly property bool active: !page.loading && page.currentPage < page.totalPages
            color: nextMouse.containsMouse && active ? Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.12)) : Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.04))
            border.width: 1
            border.color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.08))
            opacity: active ? 1.0 : 0.35

            Text {
                anchors.centerIn: parent
                text: "›"
                font.pixelSize: 14
                font.bold: true
                color: Tone.c(Appearance.Palette, "#f8fafc")
            }

            MouseArea {
                id: nextMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: nextBtn.active ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: {
                    if (nextBtn.active)
                        page.goToPage(page.currentPage + 1);
                }
            }
        }

        Item {
            Layout.fillWidth: true
        }
    }

    function ageText(epochSec) {
        var sec = Number(epochSec) || 0;
        if (sec <= 0)
            return "";
        var age = Math.max(0, (clockMs / 1000) - sec);
        if (age < 60)
            return shell.i18n("just now");
        if (age < 3600)
            return shell.i18np("%1 min ago", "%1 min ago", Math.floor(age / 60));
        if (age < 86400)
            return shell.i18np("%1 h ago", "%1 h ago", Math.floor(age / 3600));
        return shell.i18np("%1 d ago", "%1 d ago", Math.floor(age / 86400));
    }

    function sessionCostText(entry) {
        var info = FeatureTabs.sessionCostInfo(entry);
        if (!info.available)
            return shell.i18n("Cost unavailable");
        if (info.cost === 0 && info.provenance === "estimated")
            return shell.i18n("Free model");
        var cost = info.cost.toFixed(4);
        if (info.billing === "subscription")
            return info.status === "exact" ? shell.i18n("Covered by plan · $%1 on API", cost) : shell.i18n("Covered by plan · ~$%1 on API", cost);
        if (info.provenance === "actual")
            return info.status === "exact" ? shell.i18n("Actual provider cost: $%1 (exact)", cost) : shell.i18n("Actual provider cost: $%1 (partial)", cost);
        if (info.provenance === "estimated")
            return info.status === "exact" ? shell.i18n("Calculated estimate: $%1 (exact)", cost) : shell.i18n("Calculated estimate: $%1 (partial)", cost);
        if (info.provenance === "mixed")
            return info.status === "exact" ? shell.i18n("Mixed cost: $%1 (exact)", cost) : shell.i18n("Mixed cost: $%1 (partial)", cost);
        return info.status === "exact" ? shell.i18n("Cost: $%1 (exact)", cost) : shell.i18n("Cost: $%1 (partial)", cost);
    }

    function sessionCostColor(entry) {
        var info = FeatureTabs.sessionCostInfo(entry);
        if (!info.available)
            return Tone.c(Appearance.Palette, "#f8fafc");
        if (info.billing === "subscription")
            return Tone.c(Appearance.Palette, "#f8fafc");
        if (info.status === "partial" || info.provenance === "mixed")
            return "#f5a623";
        if (info.provenance === "estimated")
            return FeatureTabs.accent("spend");
        if (info.status === "exact") {
            var provider = shell.providerById ? shell.providerById(entry.provider) : null;
            return provider && provider.accent ? provider.accent : "#34d399";
        }
        return Tone.c(Appearance.Palette, "#f8fafc");
    }
}
