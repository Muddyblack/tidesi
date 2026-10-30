import QtQuick
import "theme" as Appearance
import QtQuick.Layouts
import QtQuick.Effects
import QtQuick.Controls.Basic as QC
import "js/FeatureTabs.js" as FeatureTabs
import "js/Tone.js" as Tone

// The popup's content — header, tabs, usage rows, stats, chart and footer —
// shared by every frontend that draws the popup itself: the Quickshell panel
// (TidesiShell.qml) and the Windows tray app (hosts/desktop/qml/Main.qml). Only the
// window around it differs, so this file is the one place the popup is laid out.
//
// `shell` is the frontend's root object. Everything read here — providers,
// settings, chart state, refresh() — is part of the interface both roots
// implement; SettingsPage.qml reads the same object.
ColumnLayout {
    id: content

    property var shell

    // The active provider's Remote Control state, or null where it has none
    // (Antigravity 2, and Claude Code while a session is open); the Remote sub-tab exists with it.
    readonly property var activeRemote: {
        var p = shell.activeIsFeature ? null : shell.activeProvider();
        return p && p.details && p.details.remote ? p.details.remote : null;
    }
    onActiveRemoteChanged: {
        if (activeRemote === null && shell.activeSubTab === "remote")
            shell.activeSubTab = "usage";
    }

    spacing: 12

    // ── Settings page ────────────────────────────────────────────
    SettingsPage {
        id: settingsPage

        // Found by name from hosts/desktop/app.py --selftest, which opens each section.
        objectName: "settingsPage"
        visible: shell.showSettings
        Layout.fillWidth: true
        shell: content.shell
    }

    // ── Account line: who is signed in, plan / model chips, service status ──
    AccountRow {
        id: accountRow
        z: 10
        shell: content.shell
        account: shell.activeProvider() ? (shell.activeProvider().account || ({})) : ({})
        status: {
            var p = shell.activeProvider();
            return p && p.details ? (p.details.status || ({})) : ({});
        }
        accent: shell.activeAccent
        visible: !shell.showSettings && !shell.activeIsFeature && shell.activeProvider() !== null && accountRow.hasContent
        Layout.fillWidth: true
    }

    // ── Error banner ────────────────────────────────────────────
    Rectangle {
        visible: {
            if (shell.showSettings || shell.activeIsFeature)
                return false;
            var p = shell.activeProvider();
            return p && p.error !== "";
        }
        Layout.fillWidth: true
        Layout.preferredHeight: errText.implicitHeight + 18
        radius: 6
        color: Tone.isLight(Appearance.Palette) ? "#fee2e2" : Qt.rgba(0.45, 0.06, 0.06, 0.32)
        border.width: 1
        border.color: Qt.rgba(0.95, 0.30, 0.30, 0.32)

        Text {
            id: errText
            anchors.fill: parent
            anchors.margins: 9
            text: {
                var p = shell.activeProvider();
                return p ? shell.tr(p, "error") : "";
            }
            color: Tone.isLight(Appearance.Palette) ? "#7f1d1d" : "#fecaca"
            font.pixelSize: 12
            wrapMode: Text.WordWrap
        }
    }

    // ── Usage / Stats sub-tab toggle ────────────────────────────
    Rectangle {
        visible: !shell.showSettings && shell.activeHasStats
        Layout.fillWidth: true
        Layout.preferredHeight: 26
        radius: 6
        color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.04))
        border.width: 1
        border.color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.07))

        RowLayout {
            anchors.fill: parent
            anchors.margins: 2
            spacing: 2

            Repeater {
                model: {
                    var tabs = [
                        {
                            id: "usage",
                            label: shell.i18n("Usage")
                        },
                        {
                            id: "stats",
                            // TRANSLATORS: sub-tab with activity statistics (tokens, sessions, models)
                            label: shell.i18n("Stats")
                        }
                    ];
                    if (content.activeRemote !== null)
                        // TRANSLATORS: sub-tab for working with the app from another device (Remote Control)
                        tabs.push({
                            id: "remote",
                            label: shell.i18n("Remote")
                        });
                    return tabs;
                }

                Rectangle {
                    required property var modelData
                    readonly property bool active: shell.activeSubTab === modelData.id
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    radius: 5
                    color: active ? Qt.rgba(shell.activeAccent.r, shell.activeAccent.g, shell.activeAccent.b, 0.20) : "transparent"
                    border.width: active ? 1 : 0
                    border.color: Qt.rgba(shell.activeAccent.r, shell.activeAccent.g, shell.activeAccent.b, 0.35)

                    Row {
                        anchors.centerIn: parent
                        spacing: 5

                        // The Remote tab says at a glance whether it is live.
                        Rectangle {
                            visible: modelData.id === "remote"
                            anchors.verticalCenter: parent.verticalCenter
                            width: 7
                            height: 7
                            radius: 3.5
                            color: content.activeRemote !== null && content.activeRemote.state === "online" ? "#34a853" : "#ea4335"
                        }

                        Text {
                            text: modelData.label
                            font.pixelSize: 11
                            font.bold: parent.parent.active
                            color: parent.parent.active ? Tone.ink(Appearance.Palette, shell.activeAccent) : Tone.c(Appearance.Palette, "#f8fafc")
                            opacity: parent.parent.active ? 1.0 : 0.6
                        }
                    }

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: shell.activeSubTab = modelData.id
                    }
                }
            }
        }
    }

    // ── Feature tabs (Overview / Spend / Sessions) ──────────────
    OverviewPage {
        visible: !shell.showSettings && shell.activeId === "overview"
        Layout.fillWidth: true
        shell: content.shell
    }

    SpendPage {
        visible: !shell.showSettings && shell.activeId === "spend"
        Layout.fillWidth: true
        shell: content.shell
    }

    SessionsPage {
        visible: !shell.showSettings && shell.activeId === "sessions"
        Layout.fillWidth: true
        shell: content.shell
    }

    // ── Usage rows ──────────────────────────────────────────────
    ColumnLayout {
        visible: !shell.showSettings && !shell.activeIsFeature && (!shell.activeHasStats || shell.activeSubTab === "usage")
        Layout.fillWidth: true
        spacing: 12

        Text {
            visible: shell.activeId === "opencode"
            Layout.fillWidth: true
            text: {
                var provider = shell.activeProvider();
                var mode = provider && provider.details ? provider.details.accountMode : "zen";
                return mode === "go" ? shell.i18n("OpenCode Go account limits. Local statistics cover Zen/Go only; other routed providers appear in Usage & Spend. Local costs are recorded or catalog estimates, not invoices.") : shell.i18n("OpenCode Zen/Go local usage only. Other routed providers appear in Usage & Spend. Local costs are recorded or catalog estimates, not invoices.");
            }
            color: Tone.c(Appearance.Palette, "#94a3b8")
            font.pixelSize: 11
            wrapMode: Text.WordWrap
        }

        UsageRows {
            visible: shell.activeId !== "mistral"
            Layout.fillWidth: true
            // The last known values, shown at start until live ones arrive.
            opacity: shell.replaying ? 0.55 : 1
            provider: shell.activeProvider()
            activeId: shell.activeId
            accent: shell.activeAccent
            countdown: shell.countdownFor
            translate: shell.tr
            // Hover: the row's name, used and remaining share, detail and reset time.
            tooltip: function (w) {
                var parts = [shell.tr(w, "label")];
                if (w.showMeter !== false) {
                    var used = Math.round(w.pct || 0);
                    // xgettext:no-javascript-format — "%1%" is a Qt placeholder and a percent sign, not printf
                    var usedText = shell.i18n("Used: %1%", used);
                    // xgettext:no-javascript-format — as above
                    var leftText = shell.i18n("%1% left", 100 - used);
                    parts.push(usedText + "  ·  " + leftText);
                }
                var detail = shell.tr(w, "detail");
                if (detail !== "")
                    parts.push(detail);
                if ((w.resetAt || 0) > 0)
                    parts.push(shell.i18n("Resets: %1", Qt.formatDateTime(new Date(w.resetAt * 1000), "MMM d, hh:mm")));
                return parts.join("\n");
            }
        }

        // Per-model quota groups, balances, rates and footnotes the provider
        // reports beyond its rows.
        ProviderSections {
            Layout.fillWidth: true
            shell: content.shell
            provider: shell.activeProvider()
            accent: shell.activeAccent
        }

        MistralUsage {
            Layout.fillWidth: true
            visible: shell.activeId === "mistral" && shell.activeProvider() && !shell.activeProvider().error
            shell: content.shell
            vibe: shell.activeId === "mistral" && shell.activeProvider() ? (shell.activeProvider().details.vibe || ({})) : ({})
        }

        // Muse is the only provider whose plan bars cost
        // money to fetch, so the tab says where they went
        // rather than looking like it failed to load.
        Text {
            readonly property string quotaError: shell.activeProvider() ? (shell.activeProvider().details.quotaError || "") : ""

            Layout.fillWidth: true
            visible: shell.activeId === "muse" && quotaError !== ""
            text: {
                if (quotaError === "disabled")
                    return shell.i18n("Plan quota is off: Meta reports it only on a billed model call. Everything above is read from Muse's own local files.");
                if (quotaError === "rejected")
                    return shell.i18n("Plan quota: Meta refused the credential.");
                if (quotaError === "unreachable")
                    return shell.i18n("Plan quota: could not reach Meta — the local numbers above are unaffected.");
                if (quotaError === "no-credential")
                    return shell.i18n("Plan quota needs a Meta API key, or a `muse login` that stored one.");
                if (quotaError === "no-model")
                    return shell.i18n("Plan quota needs a model: run Muse once so it caches its catalog.");
                return "";
            }
            font.pixelSize: 9
            color: Tone.c(Appearance.Palette, "#94a3b8")
            opacity: 0.8
            wrapMode: Text.WordWrap
        }

        Text {
            Layout.fillWidth: true
            visible: shell.activeId === "muse" && shell.activeProvider() && (shell.activeProvider().details.quotaError || "") === "" && !(shell.activeProvider().details.current || {}).available && !(shell.activeProvider().details.weekly || {}).available
            text: shell.i18n("No plan windows on this account — pay-as-you-go has none.")
            font.pixelSize: 9
            color: Tone.c(Appearance.Palette, "#94a3b8")
            opacity: 0.8
            wrapMode: Text.WordWrap
        }

        // Local ledgers use the same navigable chart, with a token scale.
        OpenCodeUsageChart {
            visible: (shell.activeId === "opencode" || shell.activeId === "mimo" || shell.activeId === "junie" || shell.activeId === "pi") && shell.settings.showChart && shell.activeProvider() !== null && ((shell.activeProvider().details || {}).stats || {}).available === true
            shell: content.shell
            stats: shell.activeProvider() ? ((shell.activeProvider().details || {}).stats || ({})) : ({})
            accent: shell.activeAccent
        }
    }

    // ── Remote section ──────────────────────────────────────────
    RemotePage {
        visible: !shell.showSettings && !shell.activeIsFeature && content.activeRemote !== null && shell.activeSubTab === "remote"
        remote: content.activeRemote
        accent: shell.activeAccent
        shell: content.shell
    }

    // ── Stats section ───────────────────────────────────────────
    StatsSection {
        visible: !shell.showSettings && !shell.activeIsFeature && shell.activeHasStats && shell.activeSubTab === "stats"
        stats: shell.activeProvider() ? (shell.activeProvider().details.stats || ({})) : ({})
        providerId: shell.activeId
        accent: shell.activeAccent
        shell: content.shell
        currency: shell.activeProvider() ? (shell.activeProvider().details.currency || "USD") : "USD"
    }

    // ── Usage chart ─────────────────────────────────────────────
    UsageChart {
        extraVisible: !shell.showSettings && !shell.activeIsFeature && shell.activeId !== "zed" && (!shell.activeHasStats || shell.activeSubTab === "usage") && shell.settings.showChart && shell.activeProvider() && (shell.activeProvider().error || "") === "" && shell.activeProvider().ok !== false && shell.activeProvider().summary.hasChart !== false
        shell: content.shell
        usageHistory: shell.usageHistory
        windows: shell.windowsForProvider(shell.activeId)
        chartWindow: shell.chartWindow
        accent: shell.activeAccent
        currency: shell.activeProvider() ? (shell.activeProvider().details.currency || "") : ""
        activeId: shell.activeId
        antigravityFilter: shell.antigravityChartFilter
        onWindowSelected: function (id) {
            shell.selectChartWindow(id);
        }
        onAntigravityFilterSelected: function (filter) {
            shell.setSetting2("antigravityChartFilter", filter);
        }
    }

    ZedUsageChart {
        visible: !shell.showSettings && !shell.activeIsFeature && shell.activeId === "zed" && (!shell.activeHasStats || shell.activeSubTab === "usage") && shell.settings.showChart && shell.activeProvider() !== null
        Layout.fillWidth: true
        shell: content.shell
        usage: shell.activeProvider() ? ((shell.activeProvider().details || {}).agentUsage || ({})) : ({})
    }

    // ── Footer ──────────────────────────────────────────────────
    RowLayout {
        visible: !shell.showSettings
        Layout.fillWidth: true

        Rectangle {
            visible: shell.errorText !== ""
            Layout.preferredWidth: 6
            Layout.preferredHeight: 6
            radius: 3
            color: shell.dangerColor
            Layout.alignment: Qt.AlignVCenter
        }
        Text {
            visible: shell.errorText !== ""
            text: shell.errorText
            color: shell.dangerColor
            font.pixelSize: 10
            elide: Text.ElideRight
        }
        Text {
            visible: shell.snapshotMsg !== ""
            text: shell.snapshotMsg
            color: "#86efac"
            font.pixelSize: 10
            elide: Text.ElideMiddle
            Layout.maximumWidth: 300
        }
        Item {
            Layout.fillWidth: true
        }
        Text {
            visible: shell.updatedAt > 0 && shell.errorText === ""
            text: shell.replaying ? shell.i18n("last known %1 · refreshing…", new Date(shell.updatedAt * 1000).toLocaleTimeString(Qt.locale(), Locale.ShortFormat)) : shell.i18n("updated %1", new Date(shell.updatedAt * 1000).toLocaleTimeString(Qt.locale(), Locale.ShortFormat))
            color: Tone.c(Appearance.Palette, "#f8fafc")
            opacity: 0.45
            font.pixelSize: 10
        }
    }

    // Behind everything while the popup is being saved as a picture, so the
    // picture is opaque: the glass behind the content belongs to the host's
    // window and is not part of the grab. A zero-size layout item (it only
    // takes part in the layout while exporting) whose child draws outside it.
    Item {
        id: exportBackdrop
        visible: shell.exporting === true
        Layout.preferredWidth: 0
        Layout.preferredHeight: 0
        z: -1
        Rectangle {
            // The whole content rectangle, whatever the item's own position.
            x: -exportBackdrop.x
            y: -exportBackdrop.y
            width: content.width
            height: exportBackdrop.y + exportBackdrop.height
            radius: 12
            color: Tone.c(Appearance.Palette, "#0d0f14")
        }
    }
}
