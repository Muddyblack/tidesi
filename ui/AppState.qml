import QtQuick
import "theme" as Appearance
import QtCore
import "js/ProviderRegistry.js" as ProviderRegistry
import "js/ProviderSources.js" as ProviderSources
import "js/Format.js" as Format
import "js/FeatureTabs.js" as FeatureTabs
import "js/RefreshCoalescer.js" as RefreshCoalescer
import "js/SessionSources.js" as SessionSources
import "js/SessionRefreshPolicy.js" as SessionRefreshPolicy
import "js/UsageHistory.js" as UsageHistory
import "js/I18n.js" as I18n
import "js/PanelRotation.js" as PanelRotation
import "js/Tone.js" as Tone

// The one application state every host shares: providers, settings, tabs,
// sessions, pricing, history and translations. PopupContent.qml and every page
// under it read this object as `shell`.
//
// A host (hosts/<platform>/) supplies only what differs per platform:
//
//   backend        the object that runs the provider backend, reads and writes
//                  the settings file and the history. hosts/windows and
//                  hosts/macos pass the Python `Backend` from app.py; the QML-only
//                  hosts (Quickshell, KDE) pass ui/CommandBackend.qml with their
//                  own process runner. Its interface is documented there.
//   popupVisible   whether the popup is on screen (drives the Sessions tab's
//                  live refresh).
//   the host*      properties below, which the settings page reads to decide
//                  which rows apply on this platform.
Item {
    id: root

    visible: false

    required property var backend
    property bool popupVisible: false

    // ── Host capabilities ────────────────────────────────────────────────────
    // A floating pill to place and a monitor to pin it to (Quickshell).
    property bool pillControls: false
    // A Python interpreter to choose (hosts that start the backend themselves).
    property bool interpreterControls: false
    // Tray styles and the floating pill (Windows / macOS tray apps).
    property bool trayOptions: false
    property var screenNames: []
    readonly property string monitorMode: root.settings.monitor || "focused"
    // The terminal frontend, shown in Settings → About for copy-paste.
    property string cliPath: ""
    readonly property bool autostartAvailable: !!backend.autostartAvailable
    readonly property bool autostart: !!backend.autostart
    function setAutostart(enabled) {
        backend.setAutostart(enabled);
    }

    // Kept for pages written against the older roots.
    readonly property string baseDir: ""

    // ── Environment ──────────────────────────────────────────────────────────
    readonly property string iconSource: backend.appIcon || ""
    readonly property string iconDir: backend.iconDir || ""
    readonly property string configPath: backend.configPath || ""
    readonly property var allProviders: ProviderRegistry.providers

    function providerIcon(provider) {
        var file = provider && provider.icon ? provider.icon : "";
        return file === "" ? "" : root.iconDir + file;
    }

    // ── Translations ─────────────────────────────────────────────────────────
    // translate/<lang>.po for the language chosen in settings ("" follows the
    // system), which the backend finds and reads, parsed by I18n.js. Strings
    // in every page go through shell.i18n(…), which xgettext extracts.
    property var catalog: I18n.empty()
    property var availableLanguages: []
    readonly property string language: root.settings.language || ""
    onLanguageChanged: if (root.settingsLoaded)
        backend.requestCatalog(root.language)
    onCatalogChanged: root.publishTrayLabels()

    function i18n(text) {
        return I18n.i18n.apply(null, [root.catalog].concat(Array.prototype.slice.call(arguments)));
    }
    function i18nc(context, text) {
        return I18n.i18nc.apply(null, [root.catalog].concat(Array.prototype.slice.call(arguments)));
    }
    function i18np(singular, plural, n) {
        return I18n.i18np.apply(null, [root.catalog].concat(Array.prototype.slice.call(arguments)));
    }
    function i18ncp(context, singular, plural, n) {
        return I18n.i18ncp.apply(null, [root.catalog].concat(Array.prototype.slice.call(arguments)));
    }

    // Text the backend words itself (row labels, details, errors, tooltips),
    // translated. The envelope carries a field's template beside it as
    // `<key>I18n` ({id, args}, or {lines: [...]} for multi-line text; see
    // backend/tidesi/messages.py); without one the English text itself is
    // looked up, which still catches fixed messages that crossed a cache.
    function tr(obj, key) {
        if (!obj)
            return "";
        var text = obj[key];
        if (text === undefined || text === null || text === "")
            return "";
        var msg = obj[key + "I18n"];
        if (msg && Array.isArray(msg.lines))
            return msg.lines.map(function (line) {
                return line.plain ? line.id : root.trMessage(line);
            }).join("\n");
        if (msg && msg.id !== undefined)
            return root.trMessage(msg);
        return root.i18n(String(text));
    }

    function trMessage(msg) {
        return I18n.i18n.apply(null, [root.catalog, String(msg.id)].concat((msg.args || []).map(String)));
    }

    // ── Settings ─────────────────────────────────────────────────────────────
    // The JSON file the backend reads its toggles and keys from, shared by
    // every host on a machine. Fields a host has no use for are carried
    // through untouched: saveSettings() writes the whole object back.
    property var settings: ({
            providers: {},
            keys: {},
            sources: {},
            proxy: {},
            pollSec: 300,
            showChart: true,
            museQuota: false,
            overviewEnabled: false,
            spendEnabled: false,
            sessionsEnabled: true,
            antigravityChartFilter: "both",
            pillMode: "always",
            position: "top-right",
            monitor: "focused",
            pythonPath: "",
            trayStyle: "",
            floatingPill: false,
            language: ""
        })
    property bool settingsLoaded: false
    property string settingsSaveError: ""
    Connections {
        target: root.backend
        ignoreUnknownSignals: true
        function onSettingsSaveFailed(reason) {
            root.settingsSaveError = reason || root.i18n("Check file permissions and free disk space.");
        }
        function onSettingsSaved() {
            root.settingsSaveError = "";
        }
    }
    property bool showSettings: false
    // How wide the popup is: the settings studio brings a sidebar, which needs
    // the room. Each host binds its window to this.
    // The settings section on screen, shared by the page and the popup's header.
    property string settingsSection: "providers"
    property string settingsProviderView: "list"

    function settingsBack() {
        if (settingsSection === "providers" && settingsProviderView !== "list")
            settingsProviderView = "list";
        else
            showSettings = false;
    }
    readonly property int popupWidth: showSettings ? 760 : 460
    onSettingsChanged: {
        if (root.settingsLoaded)
            root.dropDisabledProviders();
        root.publishTray();
    }
    readonly property string antigravityChartFilter: root.settings.antigravityChartFilter || "both"

    function normalizeSettings(d) {
        var s = Object.assign({}, d);
        s.providers = d.providers || {};
        s.keys = d.keys || {};
        s.sources = d.sources || {};
        s.proxy = d.proxy || {};
        s.pollSec = d.pollSec || 300;
        s.showChart = d.showChart !== false;
        s.museQuota = d.museQuota === true;
        s.overviewEnabled = d.overviewEnabled === true;
        s.spendEnabled = d.spendEnabled === true;
        s.sessionsEnabled = d.sessionsEnabled !== false;
        s.antigravityChartFilter = d.antigravityChartFilter || "both";
        s.pillMode = d.pillMode || (d.floatingPill === false ? "tray" : "always");
        s.position = d.position || "top-right";
        s.monitor = d.monitor || "focused";
        s.pythonPath = d.pythonPath || "";
        // trayNumbers was the on/off switch before there were three styles;
        // with neither, the platform's default.
        s.trayStyle = d.trayStyle || (d.trayNumbers === false ? "ring" : d.trayNumbers === true ? "icons" : (backend.defaultTrayStyle || "icons"));
        delete s.trayNumbers;
        s.floatingPill = d.floatingPill === true;
        s.language = d.language || "";
        s.appearance = d.appearance || "auto";
        return s;
    }

    function applyLoadedSettings(text) {
        var d = {};
        try {
            d = JSON.parse((text || "").trim() || "{}") || {};
        } catch (e) {}
        root.settings = root.normalizeSettings(d);
        root.settingsLoaded = true;
        root.dropDisabledProviders();
        if (root.pendingLastSnapshot !== "") {
            var pending = root.pendingLastSnapshot;
            root.pendingLastSnapshot = "";
            root.applyLastSnapshot(pending);
        }
        backend.requestCatalog(root.language);
        // The first start (no settings file yet): write one now, so whatever
        // a host does only on a first start happens once.
        if (backend.firstRun)
            root.saveSettings();
        root.initializeProviderDefaults();
    }

    function saveSettings() {
        backend.saveSettings(JSON.stringify(root.settings));
    }

    // Mutate one nested settings field ("providers" or "keys") and persist.
    // Assigning a fresh object makes every binding re-evaluate.
    function setSetting(section, key, value) {
        var s = JSON.parse(JSON.stringify(root.settings));
        if (!s[section])
            s[section] = {};
        s[section][key] = value;
        root.settings = s;
        root.saveSettings();
    }

    // ── Per-widget settings ──────────────────────────────────────────────────
    // A host that can show several instances (KDE: one widget per panel) keeps
    // these keys per instance instead of in the shared file: it lists them in
    // hostSettingsKeys, binds hostSettings to its own store, and saves what
    // hostSettingChanged reports. Read them with settingValue().
    property var hostSettingsKeys: []
    property var hostSettings: ({})
    signal hostSettingChanged(string key, var value)

    function settingValue(key) {
        if (root.hostSettingsKeys.indexOf(key) >= 0)
            return root.hostSettings[key];
        return root.settings[key];
    }

    // Mutate a top-level settings field and persist.
    function setSetting2(key, value) {
        if (root.hostSettingsKeys.indexOf(key) >= 0) {
            var h = Object.assign({}, root.hostSettings);
            h[key] = value;
            root.hostSettings = h;
            root.hostSettingChanged(key, value);
            return;
        }
        var s = JSON.parse(JSON.stringify(root.settings));
        s[key] = value;
        root.settings = s;
        root.saveSettings();
    }

    function providerEnabled(id) {
        return ProviderRegistry.enabled(root.settings, id);
    }

    // Snapshots (cached or live) can still carry a provider the user has since
    // turned off; keep only the configured ones.
    function dropDisabledProviders() {
        root.providers = root.providers.filter(function (provider) {
            return root.providerEnabled(provider.id);
        });
    }

    // ── Provider sources ─────────────────────────────────────────────────────
    // A provider that can be read more than one way (a CLI, an IDE, a local
    // server, an API key…) reports its ways in its snapshot's `sources` block;
    // which one is used is the user's choice, "auto" meaning the first that works.
    // The backend honours the choice from the settings file (tidesi/sources.py).
    function sourceChoice(id) {
        return (root.settings.sources || {})[id] || ProviderSources.AUTO;
    }

    function chooseSource(id, source) {
        root.setSetting("sources", id, source);
        root.refresh();
    }

    // The one line a provider's row shows: which source is answering, or what
    // is wrong. ProviderSources.statusLine decides; this words it.
    function providerStatusText(id) {
        var line = ProviderSources.statusLine(root.providerById(id));
        switch (line.code) {
        case "using":
            return root.i18n("Using %1", line.label);
        case "failing":
            return line.text !== "" ? line.label + ": " + line.text : root.i18n("%1 is not working", line.label);
        case "unset":
            return root.i18n("Not set up");
        case "ok":
            return root.i18n("Working");
        case "error":
            return line.text;
        }
        return root.i18n("Waiting for data");
    }

    // "ok" | "bad" | "muted", for colouring that line.
    function providerStatusTone(id) {
        var line = ProviderSources.statusLine(root.providerById(id));
        return line.code === "using" || line.code === "ok" ? "ok" : line.code === "failing" || line.code === "error" ? "bad" : "muted";
    }

    // ── Provider defaults ────────────────────────────────────────────────────
    // The first start detects what is installed and turns those providers on;
    // the backend latches that (providerDefaultsApplied) so it runs once. No
    // usage refresh goes out before it has answered.
    property bool providerDefaultsReady: false
    property bool providerDefaultsInitializing: false

    function initializeProviderDefaults() {
        if (root.providerDefaultsReady || root.providerDefaultsInitializing)
            return;
        if (root.settings.providerDefaultsApplied === true) {
            root.providerDefaultsReady = true;
            root.refresh();
            return;
        }
        root.providerDefaultsInitializing = true;
        backend.initializeProviderDefaults();
    }

    function finishProviderDefaults(text) {
        try {
            var result = JSON.parse((text || "").trim());
            if (result.ok === true && result.data) {
                var merged = Object.assign({}, root.settings || {}, result.data);
                merged.providers = Object.assign({}, (root.settings || {}).providers || {}, result.data.providers || {});
                root.settings = root.normalizeSettings(merged);
            }
        } catch (e) {}
        root.providerDefaultsInitializing = false;
        root.providerDefaultsReady = true;
        root.refresh();
    }

    // ── Provider state ───────────────────────────────────────────────────────
    property var providers: []
    property var localSpend: ({})
    property string activeId: ""
    // The last real provider selected (never a feature tab id) — what the
    // panel pill shows while a feature tab (Overview/Spend/Sessions) is
    // active, since those have no percentage of their own to display.
    property string lastProviderId: ""
    property string errorText: ""
    readonly property bool loading: !!backend.busy
    property int updatedAt: 0
    property double nowTick: new Date().getTime()
    readonly property color dangerColor: "#ff4d4d"

    // Feature tabs (Overview / Spend / Sessions) sit ahead of providers.
    readonly property var popupTabs: {
        var tabs = [];
        var features = FeatureTabs.enabledFeatureTabs(root.settings);
        for (var i = 0; i < features.length; i++) {
            var id = features[i];
            tabs.push({
                id: id,
                label: FeatureTabs.label(id, root.i18n),
                accent: FeatureTabs.accent(id) || "#38bdf8",
                icon: "tab-" + id + ".svg",
                feature: true
            });
        }
        for (var j = 0; j < root.providers.length; j++)
            tabs.push(root.providers[j]);
        return tabs;
    }
    readonly property bool activeIsFeature: FeatureTabs.isFeatureTab(root.activeId)

    // Chart state: the remembered granularity carries across tabs.
    property string chartWindow: "weekly"
    property string chartGranularity: "7d"
    // Inner sub-tab for providers with activity stats: "usage" vs "stats".
    property string activeSubTab: "usage"
    readonly property bool activeHasStats: {
        if (root.activeIsFeature)
            return false;
        var p = activeProvider();
        return p && (p.id === "claude" || p.id === "antigravity" || p.id === "kiro" || p.id === "openai" || p.id === "copilot" || p.id === "muse" || p.id === "cursor" || p.id === "cline" || p.id === "opencode" || p.id === "mimo" || p.id === "junie" || p.id === "pi");
    }

    function providerById(id) {
        for (var i = 0; i < root.providers.length; i++) {
            if (root.providers[i].id === id)
                return root.providers[i];
        }
        return null;
    }

    function activeProvider() {
        if (root.activeIsFeature || root.providers.length === 0)
            return null;
        for (var i = 0; i < root.providers.length; i++) {
            if (root.providers[i].id === root.activeId)
                return root.providers[i];
        }
        return root.providers[0];
    }

    // ── Panel pins and rotation ──────────────────────────────────────────────
    // Providers pinned on the panel stay there whatever tab the popup shows;
    // with several pinned, the panel shows them side by side — or one at a
    // time, rotating, when panelRotationSec is set.
    readonly property var pinnedTabs: {
        var raw = root.settingValue("pinnedTabs");
        var list = Array.isArray(raw) ? raw : (typeof raw === "string" ? raw.split(",") : []);
        var pins = [];
        for (var i = 0; i < list.length; i++) {
            var id = String(list[i]).trim();
            // Before the first answer every pin stands; after it, only the
            // providers that are still enabled.
            if (id !== "" && pins.indexOf(id) < 0 && !FeatureTabs.isFeatureTab(id) && (root.providers.length === 0 || root.providerById(id)))
                pins.push(id);
        }
        return pins;
    }
    readonly property int panelRotationSec: PanelRotation.normalizeIntervalSec(Number(root.settingValue("panelRotationSec") || 0))
    readonly property bool panelRotationEnabled: PanelRotation.isEnabled(root.panelRotationSec, root.pinnedTabs)
    property string rotationProviderId: ""
    onPinnedTabsChanged: root.rotationProviderId = PanelRotation.normalizeSelection(root.pinnedTabs, root.rotationProviderId)

    Timer {
        interval: Math.max(1, root.panelRotationSec) * 1000
        running: root.panelRotationEnabled && !root.popupVisible
        repeat: true
        onTriggered: root.rotationProviderId = PanelRotation.nextSelection(root.pinnedTabs, root.rotationProviderId)
    }

    function isPinned(id) {
        return root.pinnedTabs.indexOf(id) >= 0;
    }

    function togglePin(id) {
        if (!id || FeatureTabs.isFeatureTab(id))
            return;
        var pins = root.pinnedTabs.slice();
        var at = pins.indexOf(id);
        if (at >= 0)
            pins.splice(at, 1);
        else
            pins.push(id);
        root.setSetting2("pinnedTabs", pins);
    }

    // The providers the panel shows right now, in order.
    readonly property var panelProviderIds: {
        if (root.pinnedTabs.length === 0) {
            var p = root.unpinnedPillProvider();
            return p ? [p.id] : [];
        }
        if (root.panelRotationEnabled)
            return [PanelRotation.normalizeSelection(root.pinnedTabs, root.rotationProviderId)];
        return root.pinnedTabs;
    }
    onPanelProviderIdsChanged: root.refreshRotatedZedUsage()

    function refreshRotatedZedUsage() {
        if (root.panelRotationEnabled && !root.popupVisible && root.panelProviderIds.indexOf("zed") >= 0)
            root.refreshZedUsage();
    }

    // What the panel pill shows: the active provider normally, or the last
    // real provider seen while a feature tab is active — never "no data".
    function unpinnedPillProvider() {
        if (!root.activeIsFeature)
            return root.activeProvider();
        return root.providerById(root.lastProviderId) || root.providers[0] || null;
    }

    // The one provider a single readout stands for (a tray icon, the menu bar
    // item): the first the panel shows.
    function pillProvider() {
        if (root.pinnedTabs.length > 0)
            return root.providerById(root.panelProviderIds[0]) || root.unpinnedPillProvider();
        return root.unpinnedPillProvider();
    }

    // One group per provider the panel shows, for PanelPill.groups.
    readonly property var panelGroups: {
        var groups = [];
        var ids = root.panelProviderIds;
        for (var i = 0; i < ids.length; i++) {
            var p = root.providerById(ids[i]);
            if (!p)
                continue;
            groups.push({
                id: p.id,
                icon: root.providerIcon(p) || root.iconSource,
                slots: root.slotsFor(p).length > 0 ? root.slotsFor(p) : [root.noDataSlot()],
                stale: root.errorText !== "" || !!p.stale || root.replaying,
                hasError: (p.error || "") !== ""
            });
        }
        if (groups.length === 0)
            groups.push({
                id: "",
                icon: root.iconSource,
                slots: root.pillSlots,
                stale: root.pillStale,
                hasError: root.pillHasError
            });
        return groups;
    }

    // A host with a system accent colour (Plasma) offers to use it everywhere
    // instead of each provider's own colour.
    property bool themeAccentAvailable: false
    property color themeAccentColor: "#3daee9"

    // Settings → Appearance → Theme (settings.appearance): "auto" follows the
    // desktop's light or dark, which the host reports in systemLight; "light"
    // and "dark" pin it.
    property bool systemLight: false
    // A host whose popup background is the desktop's own (Plasma's dialog,
    // which the widget cannot recolour) always follows it: no setting there.
    property bool appearanceFollowsHost: false
    readonly property bool light: root.appearanceFollowsHost ? root.systemLight : (root.settings.appearance === "light" || (root.settings.appearance !== "dark" && root.systemLight))
    // Tone.js reads ordinary colour properties instead of Qt item palettes.
    readonly property color windowColor: root.light ? "#f4f6fa" : "#10141c"
    Binding {
        target: Appearance.Palette
        property: "window"
        value: root.windowColor
    }

    // Fill of the chart card.
    readonly property color cardColor: {
        var c = Qt.color(root.settings.cardBgColor || "#100a1a");
        var a = root.settings.cardBgOpacity === undefined ? 0 : Number(root.settings.cardBgOpacity);
        // Light under the light theme, like every other neutral (Tone.js).
        return Tone.c({
            window: root.windowColor
        }, Qt.rgba(c.r, c.g, c.b, Math.max(0, Math.min(1, a))));
    }

    // A host whose popup frame has a style to choose (Plasma's background hints).
    property bool backgroundStyleAvailable: false
    // ...and whether that style reaches what the user sees. Plasma reads
    // backgroundHints only for a widget on the desktop; a panel popup always
    // takes the theme's dialog frame, blurred by KWin if the theme allows.
    property bool backgroundStyleApplies: backgroundStyleAvailable
    // The popup draws its own glass (ui/PopupBackground.qml) instead of
    // Plasma's frame: every host without a frame style, and a Plasma desktop
    // widget with Blur off (backgroundHints 0, no frame, so no KWin blur).
    readonly property bool ownGlass: !backgroundStyleAvailable || (backgroundStyleApplies && Number(root.settingValue("backgroundHints")) === 0)
    // A host whose compositor can blur behind the popup on request (Quickshell
    // on Hyprland, via hosts/quickshell/glass.conf): settings.compositorGlass.
    property bool compositorGlassAvailable: false
    // A host that has the system's own blur behind the popup, always (macOS,
    // hosts/macos/vibrancy.py): Settings shows Blur as on, with no switch.
    property bool systemBlur: false

    readonly property color activeAccent: {
        if (root.themeAccentAvailable && root.settings.themeAccent === true)
            return root.themeAccentColor;
        if (root.activeIsFeature)
            return FeatureTabs.accent(root.activeId) || "#38bdf8";
        var p = activeProvider();
        return p ? p.accent : "#cc785c";
    }

    // The pill's slots, with the loading and no-data placeholders every host
    // shows. The backend sends text: null for "show the percentage", which a
    // string property cannot take.
    function noDataSlot() {
        return {
            pct: 0,
            color: "#cc785c",
            text: "—",
            tooltip: root.i18n("No data")
        };
    }

    function slotsFor(p) {
        var raw = p && p.slots ? p.slots : [];
        var out = [];
        for (var i = 0; i < raw.length; i++)
            out.push({
                pct: raw[i].pct || 0,
                color: raw[i].color || "#cc785c",
                text: root.tr(raw[i], "text"),
                tooltip: root.tr(raw[i], "tooltip")
            });
        return out;
    }

    readonly property var pillSlots: {
        var p = root.pillProvider();
        if (root.loading && root.providers.length === 0)
            return [
                {
                    pct: 0,
                    color: "#cc785c",
                    text: "…",
                    tooltip: root.i18n("Loading")
                }
            ];
        var out = root.slotsFor(p);
        return out.length > 0 ? out : [root.noDataSlot()];
    }
    readonly property bool pillStale: {
        var p = root.pillProvider();
        return root.errorText !== "" || root.replaying || (p ? !!p.stale : false);
    }
    readonly property bool pillHasError: {
        var p = root.pillProvider();
        return root.errorText !== "" || (p ? (p.error || "") !== "" : false);
    }
    readonly property string pillIcon: {
        var logo = root.providerIcon(root.pillProvider());
        return logo !== "" ? logo : root.iconSource;
    }

    // ── Chart ranges ─────────────────────────────────────────────────────────
    // Which history series a provider has, and how wide each range is, comes
    // from the backend (chartWindows in the contract) — no table here.
    function windowsForProvider(id) {
        for (var i = 0; i < root.providers.length; i++) {
            if (root.providers[i].id === id)
                return root.providers[i].chartWindows || [];
        }
        return [];
    }

    // Window ID for a provider at the remembered granularity, so the selected
    // range carries across tabs.
    function windowForProvider(id, gran) {
        var wins = windowsForProvider(id);
        if (wins.length === 0)
            return root.chartWindow;
        for (var i = 0; i < wins.length; i++) {
            if (wins[i].granularity === gran)
                return wins[i].id;
        }
        return wins[wins.length - 1].id;
    }

    function windowGranularity(win) {
        var wins = windowsForProvider(root.activeId);
        for (var i = 0; i < wins.length; i++) {
            if (wins[i].id === win)
                return wins[i].granularity;
        }
        return "";
    }

    onActiveIdChanged: {
        activeSubTab = "usage";
        var win = windowForProvider(root.activeId, root.chartGranularity);
        if (root.chartWindow !== win)
            root.chartWindow = win;
        if (root.activeId !== "" && !FeatureTabs.isFeatureTab(root.activeId))
            root.lastProviderId = root.activeId;
        // Reopen on this tab next time.
        if (root.providerDefaultsReady && root.activeId !== "" && (root.settings || {}).lastTab !== root.activeId)
            root.setSetting2("lastTab", root.activeId);
        root.publishTray();
        if (root.popupVisible && root.activeId === "zed")
            root.refreshZedUsage();
    }
    onPopupVisibleChanged: {
        if (root.popupVisible && !root.showSettings && root.activeId === "zed")
            root.refreshZedUsage();
    }

    function selectChartWindow(id) {
        root.chartWindow = id;
        var gran = windowGranularity(id);
        if (gran !== "")
            root.chartGranularity = gran;
    }

    function countdownFor(resetAt) {
        return Format.countdownFromEpoch(resetAt, root.nowTick);
    }

    // ── Tray ─────────────────────────────────────────────────────────────────
    // Hosts with a tray icon (Windows, macOS) draw it from this: the pill's
    // slots in the chosen trayStyle, plus a tooltip listing every provider.
    // Hosts without one ignore the call.
    function publishTray() {
        var p = root.pillProvider();
        var lines = ["tideSI"];
        for (var i = 0; i < root.providers.length; i++) {
            var q = root.providers[i];
            if (q.label && q.summary && q.summary.text)
                lines.push(q.label + ": " + root.tr(q.summary, "text"));
        }
        var slots = [];
        var raw = p && p.slots ? p.slots : [];
        for (var j = 0; j < raw.length; j++) {
            var s = raw[j] || {};
            var text = s.text === null || s.text === undefined ? "" : root.tr(s, "text");
            slots.push({
                pct: s.pct || 0,
                color: String(s.color || ""),
                text: text,
                // Windows cuts a tray tooltip at 127 characters, so each icon
                // names only its own value.
                tooltip: s.tooltip ? root.tr(s, "tooltip") : p.label + ": " + (text || Math.round(s.pct || 0) + "%")
            });
        }
        backend.publishTrayState(JSON.stringify({
            style: root.settings.trayStyle || backend.defaultTrayStyle || "icons",
            floatingPill: root.settings.floatingPill === true,
            // The active provider's logo leads the numbers, or sits inside the ring.
            icon: p ? root.providerIcon(p) : "",
            tooltip: lines.join("\n"),
            slots: slots
        }));
    }

    // A tray menu built by the host takes its words from here — again whenever
    // the language changes.
    function publishTrayLabels() {
        backend.setTrayLabels(JSON.stringify({
            open: root.i18n("Open tideSI"),
            refresh: root.i18n("Refresh"),
            settings: root.i18n("Settings"),
            trayStyle: root.i18n("Tray style"),
            icons: root.i18n("Logo and percent"),
            // TRANSLATORS: tray icon style: only the percentages, as digits
            numbers: root.i18n("Numbers"),
            // TRANSLATORS: tray icon style: one circular progress ring
            ring: root.i18n("Ring"),
            floatingPill: root.i18n("Floating pill"),
            startWithWindows: root.i18n("Start with Windows"),
            startAtLogin: root.i18n("Start at login"),
            quit: root.i18n("Quit")
        }));
    }

    // ── Backend fetch ────────────────────────────────────────────────────────
    // At start the last good answer (saved by every refresh) is shown at once,
    // marked stale, until the first live one replaces it; a replay that
    // arrives after a live answer is dropped.
    property bool replaying: false
    property bool liveSnapshotSeen: false
    property bool incrementalSnapshot: false
    property string pendingLastSnapshot: ""

    function applyLastSnapshot(text) {
        if (root.liveSnapshotSeen && !root.incrementalSnapshot)
            return;
        if (!root.settingsLoaded) {
            root.pendingLastSnapshot = text;
            return;
        }
        var data;
        try {
            data = JSON.parse((text || "").trim() || "{}");
        } catch (e) {
            return;
        }
        if (!data || !Array.isArray(data.providers) || data.providers.length === 0)
            return;
        if (root.liveSnapshotSeen) {
            data.providers = data.providers.filter(function (provider) {
                return !root.providerById(provider.id);
            });
            data.partial = true;
            root.applySnapshot(JSON.stringify(data));
            return;
        }
        root.replaying = true;
        root.applySnapshot(text, true);
    }

    function applySnapshot(text, replayed) {
        if (replayed !== true) {
            root.liveSnapshotSeen = true;
            root.replaying = false;
        }
        try {
            var data = JSON.parse((text || "").trim());
            if (replayed !== true)
                root.incrementalSnapshot = data.partial === true;
            var incoming = data.providers || [];
            if (data.partial === true) {
                var merged = root.providers.slice();
                incoming.forEach(function (provider) {
                    var index = merged.findIndex(function (previous) {
                        return previous.id === provider.id;
                    });
                    if (index === -1)
                        merged.push(provider);
                    else
                        merged[index] = provider;
                });
                var order = root.allProviders.map(function (provider) {
                    return provider.id;
                });
                root.providers = merged.sort(function (left, right) {
                    return order.indexOf(left.id) - order.indexOf(right.id);
                });
                if (data.recordHistory !== undefined && incoming.some(function (provider) {
                    return provider.id === "zed";
                }))
                    root.zedUsageLoading = false;
            } else {
                root.providers = incoming;
                root.localSpend = data.localSpend || ({});
                root.updatedAt = data.updatedAt || 0;
            }
            if (root.settingsLoaded)
                root.dropDisabledProviders();
            var fallback = root.providerById(data.active) ? data.active : (root.providers[0] || {}).id || "";
            // Seed (or heal, if the remembered one got disabled) the pill's
            // fallback provider — needed even before the user ever leaves a
            // feature tab, since Sessions is the default-enabled one.
            if (!root.providerById(root.lastProviderId))
                root.lastProviderId = fallback;
            // Keep the active tab if it's still present; otherwise reopen the
            // tab used last time, else the backend's suggestion or the first
            // provider — never a feature tab by default, so the pill mirrors a
            // real provider from the first start.
            var features = FeatureTabs.enabledFeatureTabs(root.settings);
            var isOpenable = function (id) {
                return features.indexOf(id) !== -1 || !!root.providerById(id);
            };
            if (!isOpenable(root.activeId)) {
                var last = (root.settings || {}).lastTab || "";
                root.activeId = isOpenable(last) ? last : fallback;
            }
            root.nowTick = new Date().getTime();
            root.publishTray();
            // A replay is not a reading: no history point, no freshness.
            if (replayed === true)
                return;
            if (data.partial === true) {
                if (data.recordHistory === true) {
                    root.errorText = "";
                    root.recordHistory();
                }
                return;
            }
            root.errorText = "";
            root.lastFetched = root.nowTick;
            root.recordHistory();
        } catch (e) {
            root.errorText = root.i18n("usage backend returned no data");
        }
    }

    function refresh(forceScan) {
        if (!root.providerDefaultsReady || root.providerDefaultsInitializing)
            return;
        if (root.settingsSaveError)
            root.saveSettings();
        if (forceScan === true && root.spendViewVisible && !root.sessionsLoading)
            root.reconcileSessions(root.sessionsQuery, root.sessionsSourceIds);
        backend.refresh();
    }

    property double zedUsageLastRequested: 0
    property bool zedUsageLoading: false

    function refreshZedUsage() {
        if (!root.providerDefaultsReady || root.providerDefaultsInitializing || root.zedUsageLoading)
            return;
        var now = Date.now();
        if (now - root.zedUsageLastRequested < 60000)
            return;
        if (typeof backend.refreshProvider !== "function")
            return;
        root.zedUsageLastRequested = now;
        root.zedUsageLoading = true;
        backend.refreshProvider("zed");
    }

    // A tab click only fetches when the data is over a minute old: every
    // refresh asks every provider, and clicking back and forth between tabs
    // would otherwise keep one running all the time.
    property double lastFetched: 0
    function refreshTab() {
        if (root.activeId === "zed") {
            root.refreshZedUsage();
            return;
        }
        if (new Date().getTime() - root.lastFetched > 60000)
            root.refresh();
    }

    // One successful pricing refresh triggers at most one usage refresh,
    // deferred while a usage request is already in flight.
    property bool usageRefreshPending: false

    function requestUsageRefreshAfterPricing() {
        var action = RefreshCoalescer.nextAction(true, root.loading, root.usageRefreshPending);
        if (action === "refresh-now")
            root.refresh();
        else if (action === "mark-pending")
            root.usageRefreshPending = true;
    }

    onLoadingChanged: {
        if (!root.loading && root.usageRefreshPending) {
            root.usageRefreshPending = false;
            root.refresh();
        }
    }

    // ── Pricing ──────────────────────────────────────────────────────────────
    readonly property bool pricingLoading: !!backend.pricingBusy
    property string pricingStatus: ""
    property string pricingError: ""

    function refreshPricing() {
        if (root.pricingLoading)
            return;
        backend.refreshPricing();
    }

    property int ratesRequestId: 0
    property var ratesCallback: null

    function queryRates(filter, limit, offset, callback) {
        // The catalog parse runs off the GUI thread; the result comes back
        // with a generation so a superseded query is dropped.
        root.ratesRequestId += 1;
        root.ratesCallback = typeof callback === "function" ? callback : null;
        backend.requestRates((filter || "").trim(), limit || 40, offset || 0, root.ratesRequestId);
    }

    function deliverRates(text) {
        var callback = root.ratesCallback;
        root.ratesCallback = null;
        if (typeof callback === "function")
            callback(text === "" ? null : FeatureTabs.parseRateTable(text));
    }

    // ── Terminal frontend ────────────────────────────────────────────────────
    // Settings → Advanced → "Open in terminal": the backend starts
    // tidesi-cli in the user's terminal (hosts that ship it; see cliPath).
    property string cliMessage: ""
    readonly property bool canOpenCli: typeof backend.openCli === "function"

    function openCli() {
        root.cliMessage = "";
        if (typeof backend.openCli === "function")
            backend.openCli();
    }

    function finishOpenCli(text) {
        try {
            var data = JSON.parse((text || "").trim());
            root.cliMessage = data.ok === true ? "" : root.i18n("Could not open the terminal frontend: %1", data.message || "");
        } catch (e) {
            root.cliMessage = root.i18n("Could not open the terminal frontend.");
        }
    }

    // ── Popup picture (PNG / SVG) ────────────────────────────────────────────
    // The popup content grabs itself (PopupContent's save button) to a
    // temporary PNG; the backend then finishes it as a PNG or SVG in Downloads.
    // `exporting` lets the content draw an opaque backdrop for the grab, since
    // the glass behind it is the host's window and is not part of the picture.
    property bool exporting: false
    property string snapshotMsg: ""

    function localPath(url) {
        return decodeURIComponent(String(url).replace(/^file:\/\//, ""));
    }

    function exportSnapshot(item, format) {
        if (!item || typeof backend.exportSnapshot !== "function")
            return;
        var stamp = Qt.formatDateTime(new Date(), "yyyyMMdd-HHmmss");
        var base = "tidesi-" + (root.showSettings ? "settings" : (root.activeId || "popup")) + "-" + stamp;
        var tmp = root.localPath(StandardPaths.writableLocation(StandardPaths.TempLocation)) + "/" + base + ".png";
        var downloads = StandardPaths.writableLocation(StandardPaths.DownloadLocation);
        var dest = root.localPath(downloads !== "" ? downloads : StandardPaths.writableLocation(StandardPaths.HomeLocation)) + "/" + base + "." + format;
        var width = Math.round(item.width);
        var height = Math.round(item.implicitHeight > 0 ? item.implicitHeight : item.height);
        root.snapshotMsg = "";
        root.exporting = true;
        // One frame for the backdrop to appear before the grab.
        Qt.callLater(function () {
            item.grabToImage(function (result) {
                root.exporting = false;
                if (!result.saveToFile(tmp)) {
                    root.snapshotMsg = root.i18n("Export failed: could not capture the image");
                    snapshotMsgTimer.restart();
                    return;
                }
                backend.exportSnapshot(format, tmp, dest, width, height);
            });
        });
    }

    function finishExportSnapshot(text) {
        try {
            var data = JSON.parse((text || "").trim());
            root.snapshotMsg = data.path ? root.i18n("Saved to %1", data.path) : root.i18n("Export failed: %1", data.error || "");
        } catch (e) {
            root.snapshotMsg = root.i18n("Export failed");
        }
        snapshotMsgTimer.restart();
    }

    Timer {
        id: snapshotMsgTimer
        interval: 8000
        onTriggered: root.snapshotMsg = ""
    }

    // ── Provider detection ───────────────────────────────────────────────────
    // Settings → Providers → "Detect installed providers": re-runs the
    // stat-only detection and syncs the toggles with what is installed.
    property bool providerDetectBusy: false
    property string providerDetectStatus: ""

    function redetectProviders() {
        if (root.providerDetectBusy)
            return;
        root.providerDetectBusy = true;
        root.providerDetectStatus = "";
        backend.detectProviders();
    }

    function applyProviderDetection(text) {
        root.providerDetectBusy = false;
        var result = null;
        try {
            result = JSON.parse((text || "").trim());
        } catch (e) {}
        if (!result || result.ok !== true || !Array.isArray(result.data)) {
            root.providerDetectStatus = root.i18n("Detection failed.");
            return;
        }
        var applied = ProviderRegistry.applyDetected(root.settings, result.data);
        if (applied.added.length === 0 && applied.removed.length === 0) {
            root.providerDetectStatus = root.i18n("No changes: enabled providers match what is installed.");
            return;
        }
        root.settings = applied.settings;
        root.saveSettings();
        var parts = [];
        if (applied.added.length)
            parts.push(root.i18n("Enabled: %1", ProviderRegistry.labels(applied.added)));
        if (applied.removed.length)
            parts.push(root.i18n("Disabled (not installed): %1", ProviderRegistry.labels(applied.removed)));
        root.providerDetectStatus = parts.join(" · ");
        root.refresh();
    }

    // ── Sessions ─────────────────────────────────────────────────────────────
    property var sessions: []
    property bool sessionsLoading: false
    property string sessionsCacheStatus: "unknown"
    property var sessionsCacheAgeSeconds: null
    property string sessionsRefreshStatus: "not-run"
    property int sessionsRemovedSourceCount: 0
    property string sessionsError: ""
    // Last `--open-session` result, shown as a status line under the list.
    property string sessionsNotice: ""
    property string sessionsQuery: ""
    property var sessionsSources: []
    property var sessionsSourceIds: []
    property string sessionsSourceSignature: ""
    property string sessionsSourceResetSignature: ""
    property var sessionsActiveSourceIds: []
    property string sessionsActiveSourceSignature: ""
    property bool sessionsActiveRefresh: false
    property double sessionsLastReconcile: 0
    property int sessionsRequestId: 0
    readonly property int sessionsLimit: 60
    property int sessionsTotal: 0
    property bool sessionsTotalExact: false
    property bool sessionsHasMore: false
    property int sessionsOffset: 0
    property int sessionsActiveOffset: 0
    property double previousSessionClockMs: Date.now()
    readonly property bool sessionsViewVisible: root.popupVisible && !root.showSettings && root.activeId === "sessions"
    readonly property bool spendViewVisible: root.popupVisible && !root.showSettings && root.activeId === "spend"

    onSpendViewVisibleChanged: {
        if (root.spendViewVisible && !root.sessionsLoading)
            root.reconcileSessions(root.sessionsQuery, root.sessionsSourceIds);
    }

    // Load as soon as the view is shown, rather than at the next poll tick.
    onSessionsViewVisibleChanged: {
        if (sessionsViewVisible && !sessionsLoading)
            querySessions(sessionsQuery, 0, false, sessionsSourceIds);
    }

    function normalizeSessionSources(raw) {
        return SessionSources.normalizeDescriptors(raw);
    }

    function sessionSourceSignature(ids) {
        return SessionSources.signature(ids);
    }

    function setSessionsSourceIds(ids) {
        var normalized = SessionSources.normalizeIds(ids, root.sessionsSources);
        var signature = root.sessionSourceSignature(normalized);
        if (signature === root.sessionsSourceSignature)
            return;
        root.sessionsSourceIds = normalized;
        root.sessionsSourceSignature = signature;
    }

    function sessionSourceSelectionHasStaleIds(available) {
        return SessionSources.hasStaleIds(root.sessionsSourceIds, available);
    }

    function setSessionsQuery(query) {
        query = (query || "").trim();
        if (query === root.sessionsQuery)
            return;
        root.requestSessions(query, false);
    }

    function sendSessionsRequest(reconcile) {
        var ids = root.sessionsActiveSourceIds;
        if (reconcile) {
            if (ids.length > 0)
                backend.refreshSessionsAndQuery(root.sessionsQuery, root.sessionsRequestId, root.sessionsActiveOffset, ids);
            else
                backend.refreshSessionsAndQuery(root.sessionsQuery, root.sessionsRequestId, root.sessionsActiveOffset);
        } else if (ids.length > 0) {
            backend.refreshSessions(root.sessionsQuery, root.sessionsRequestId, root.sessionsActiveOffset, ids);
        } else {
            backend.refreshSessions(root.sessionsQuery, root.sessionsRequestId, root.sessionsActiveOffset);
        }
    }

    function requestSessions(query, reconcile, sourceIds, offset) {
        if (sourceIds !== undefined)
            root.setSessionsSourceIds(sourceIds);
        root.sessionsQuery = (query || "").trim();
        root.sessionsActiveOffset = offset === undefined ? 0 : offset;
        root.sessionsActiveRefresh = reconcile === true;
        root.sessionsActiveSourceIds = root.sessionsSourceIds.slice(0);
        root.sessionsActiveSourceSignature = root.sessionsSourceSignature;
        root.sessionsRequestId += 1;
        root.sessionsLoading = true;
        root.sessionsError = "";
        root.sessionsNotice = "";
        root.sendSessionsRequest(reconcile === true);
    }

    function refreshSessions(query, offset, append, sourceIds) {
        root.requestSessions(query, false, sourceIds);
    }

    function querySessions(query, offset, append, sourceIds) {
        root.requestSessions(query, false, sourceIds, offset);
    }

    function reconcileSessions(query, sourceIds) {
        root.requestSessions(query, true, sourceIds, root.sessionsOffset);
    }

    function handleSessions(text, query, requestId) {
        if (requestId !== root.sessionsRequestId || query !== root.sessionsQuery || root.sessionsActiveSourceSignature !== root.sessionsSourceSignature)
            return;
        root.sessionsLoading = false;
        try {
            var data = JSON.parse((text || "").trim());
            if (data.error) {
                root.sessionsError = data.error;
                root.sessionsRefreshStatus = "failed";
                sessionsReconcileTimer.restart();
            } else if (data.cacheStatus === "failed") {
                root.sessionsError = root.i18n("Could not load cached sessions.");
                root.sessionsCacheStatus = "failed";
                root.sessionsRefreshStatus = "failed";
                sessionsReconcileTimer.restart();
            } else {
                var page = data.sessions || [];
                var responseSources = root.normalizeSessionSources(data.sources);
                var staleSelection = root.sessionSourceSelectionHasStaleIds(responseSources);
                root.sessionsSources = responseSources;
                if (staleSelection) {
                    var staleSignature = root.sessionsSourceSignature;
                    if (root.sessionsSourceResetSignature === staleSignature)
                        return;
                    root.sessionsSourceIds = [];
                    root.sessionsSourceSignature = "";
                    root.sessionsSourceResetSignature = staleSignature;
                    root.sessions = [];
                    root.sessionsOffset = 0;
                    root.sessionsTotal = 0;
                    root.sessionsHasMore = false;
                    root.requestSessions(root.sessionsQuery, false, [], 0);
                    return;
                }
                root.sessionsSourceResetSignature = "";
                root.sessionsTotal = Number(data.total) || 0;
                root.sessionsTotalExact = data.totalExact === true;
                root.sessionsOffset = Number(data.offset) || root.sessionsActiveOffset;
                root.sessionsHasMore = data.hasMore === true;
                root.sessions = page;
                root.sessionsError = "";
                root.sessionsCacheStatus = data.cacheStatus || "unknown";
                root.sessionsCacheAgeSeconds = data.cacheAgeSeconds === undefined ? null : data.cacheAgeSeconds;
                // A cache-only query never ran a refresh ("not-run"); keep the
                // status of the last scan instead of erasing it.
                if (data.refreshStatus && data.refreshStatus !== "not-run")
                    root.sessionsRefreshStatus = data.refreshStatus;
                if (root.sessionsActiveRefresh)
                    root.sessionsRemovedSourceCount = Number(data.removedSourceCount) || 0;
                sessionsReconcileTimer.restart();
                // An expired cache starts one scan, but a scan that just failed
                // waits for the reconcile timer instead of retrying in a loop.
                var lastScanFailed = root.sessionsRefreshStatus === "failed" || root.sessionsRefreshStatus === "incomplete";
                if (root.sessionsActiveRefresh) {
                    root.sessionsLastReconcile = Date.now();
                    if (root.spendViewVisible)
                        root.refresh();
                } else if ((root.sessionsViewVisible || root.spendViewVisible) && ((!lastScanFailed && (root.sessionsCacheStatus === "no-cache" || SessionRefreshPolicy.cacheExpired(root.sessionsCacheAgeSeconds))) || (root.sessionsRefreshStatus === "incomplete" && SessionRefreshPolicy.incompleteRetryDue(root.sessionsLastReconcile, Date.now()))))
                    root.reconcileSessions(root.sessionsQuery, root.sessionsSourceIds);
                else if (root.spendViewVisible)
                    root.refresh();
            }
        } catch (e) {
            root.sessionsError = root.i18n("Could not load sessions.");
            root.sessionsRefreshStatus = "failed";
            sessionsReconcileTimer.restart();
        }
    }

    // Rows with an empty openKey (Muse) render no button at all.
    function openSession(key) {
        if (!key)
            return;
        root.sessionsNotice = "";
        backend.openSession(key);
    }

    // ── History ──────────────────────────────────────────────────────────────
    // The state machine in UsageHistory.js over the shared history file, which
    // the backend's historyio owns: it takes a lock, unions the payload into
    // whatever is on disk and hands the merged series back. Persistence is
    // coalesced into one run per window while the chart stays current from the
    // in-memory store; a controlled exit flushes instead of waiting.
    property var usageHistory: []
    readonly property int historyLimit: 10000
    property var historyStore: UsageHistory.newStore(root.historyLimit)
    property bool historySaving: false
    property int historyChangesPending: 0
    property string historyMsg: ""
    // The prune in flight is the automatic one at startup: no message for it.
    property bool historyAutoPrune: false
    readonly property int historyDebounceMs: 300000

    // The store replaces `history` rather than patching it in place, so an
    // unchanged reference means there is nothing to repaint.
    function syncUsageHistory() {
        if (root.usageHistory !== root.historyStore.history)
            root.usageHistory = root.historyStore.history;
    }

    // Recorded on every poll even when no provider reported, which is what
    // releases a save that failed.
    function recordHistory() {
        UsageHistory.record(root.historyStore, UsageHistory.collect(root.providers), new Date().getTime());
        root.syncUsageHistory();
        root.saveHistory();
    }

    function saveHistory() {
        if (UsageHistory.arm(root.historyStore, new Date().getTime(), root.historyDebounceMs))
            historyDebounceTimer.restart();
    }

    function sendHistory() {
        if (root.historySaving || root.historyChangesPending > 0)
            return;
        var batch = UsageHistory.take(root.historyStore);
        if (!batch)
            return;
        root.historySaving = true;
        historySaveTimeout.restart();
        backend.history(batch.op, JSON.stringify(batch.points));
    }

    function flushHistory() {
        historyDebounceTimer.stop();
        if (root.historyChangesPending > 0)
            return;
        var batch = UsageHistory.takeForExit(root.historyStore);
        if (!batch)
            return;
        // Synchronous where the host can manage it: the process is going
        // away, so the write has to land before it exits.
        backend.flushHistory(batch.op, JSON.stringify(batch.points));
    }

    function finishHistorySave(result) {
        historySaveTimeout.stop();
        root.historySaving = false;
        var res = null;
        try {
            res = JSON.parse(result);
        } catch (e) {}
        if (!res || res.error) {
            UsageHistory.failed(root.historyStore);
            return;
        }
        // A degraded write answers {"ok":true} with no series, which leaves
        // nothing to adopt.
        UsageHistory.done(root.historyStore, res.data);
        root.syncUsageHistory();
        root.saveHistory();
    }

    function exportHistory() {
        backend.history("export", "");
    }

    // Delete the whole recorded history.
    function clearHistory() {
        root.historyChangesPending += 1;
        historyDebounceTimer.stop();
        backend.history("clear", "");
    }

    // Keep the last `days` days of history and delete the rest.
    function pruneHistory(days) {
        root.historyChangesPending += 1;
        historyDebounceTimer.stop();
        backend.history("prune", String(days));
    }

    // The file is now `data`: start the store over from it, so nothing deleted
    // is still held here to be written back by the next save.
    function resetHistoryStore(data) {
        root.historyStore = UsageHistory.newStore(root.historyLimit);
        UsageHistory.adopt(root.historyStore, data);
        UsageHistory.opened(root.historyStore);
        root.syncUsageHistory();
    }

    Timer {
        id: historyDebounceTimer
        interval: root.historyDebounceMs
        onTriggered: root.sendHistory()
    }

    // A save that never answers would hold its batch in flight for the rest of
    // the session. failed() is idempotent, so a late answer after this is fine.
    Timer {
        id: historySaveTimeout
        interval: 30000
        onTriggered: {
            root.historySaving = false;
            UsageHistory.failed(root.historyStore);
        }
    }

    Timer {
        id: historyMsgTimer
        interval: 6000
        onTriggered: root.historyMsg = ""
    }

    // ── Timers ───────────────────────────────────────────────────────────────
    Timer {
        interval: Math.max(30, root.settings.pollSec || 300) * 1000
        running: root.providerDefaultsReady
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    Timer {
        id: sessionsReconcileTimer
        interval: SessionRefreshPolicy.refreshDelayMs(root.sessionsCacheAgeSeconds, root.sessionsRefreshStatus)
        running: root.sessionsViewVisible || root.spendViewVisible
        onTriggered: {
            if (!root.sessionsLoading)
                root.reconcileSessions(root.sessionsQuery, root.sessionsSourceIds);
        }
    }

    // Drives the countdown chips, and catches a resume from suspend.
    Timer {
        interval: 30000
        running: true
        repeat: true
        onTriggered: {
            var now = Date.now();
            if ((root.sessionsViewVisible || root.spendViewVisible) && SessionRefreshPolicy.resumed(root.previousSessionClockMs, now) && !root.sessionsLoading)
                root.reconcileSessions(root.sessionsQuery, root.sessionsSourceIds);
            root.previousSessionClockMs = now;
            root.nowTick = now;
        }
    }

    // ── Backend answers ──────────────────────────────────────────────────────
    Connections {
        target: root.backend

        function onSettingsLoaded(text) {
            root.applyLoadedSettings(text);
        }

        function onCatalogLoaded(text) {
            root.catalog = I18n.parsePo(text || "");
        }

        function onLanguagesLoaded(text) {
            try {
                root.availableLanguages = JSON.parse(text || "[]");
            } catch (e) {
                root.availableLanguages = [];
            }
        }

        function onProviderDefaultsLoaded(text) {
            root.finishProviderDefaults(text);
        }

        function onProvidersDetected(text) {
            root.applyProviderDetection(text);
        }

        function onLastSnapshotLoaded(text) {
            root.applyLastSnapshot(text);
        }

        function onSnapshotExported(text) {
            root.finishExportSnapshot(text);
        }

        function onCliOpened(text) {
            root.finishOpenCli(text);
        }

        function onSnapshotReady(text) {
            root.applySnapshot(text);
        }

        function onProviderReady(text) {
            root.applySnapshot(text);
        }

        function onRefreshFailed(message) {
            root.zedUsageLoading = false;
            root.errorText = message;
        }

        function onRatesReady(text) {
            root.deliverRates(text);
        }

        function onSessionsReady(text, query, requestId) {
            root.handleSessions(text, query, requestId);
        }

        function onOpenSessionFinished(text) {
            try {
                var data = JSON.parse((text || "").trim());
                root.sessionsNotice = data.message || "";
            } catch (e) {
                root.sessionsNotice = root.i18n("Could not resume session.");
            }
        }

        function onPricingRefreshFinished(text) {
            try {
                var data = JSON.parse((text || "").trim());
                root.pricingStatus = data.status || (data.ok === true ? "refreshed" : "no-cache");
                root.pricingError = data.error || "";
                if (data.ok === true)
                    root.requestUsageRefreshAfterPricing();
            } catch (e) {
                root.pricingStatus = "no-cache";
                root.pricingError = root.i18n("Could not refresh pricing.");
            }
        }

        function onPricingRefreshFailed(message) {
            root.pricingStatus = "no-cache";
            root.pricingError = message || root.i18n("Could not refresh pricing.");
        }

        // A setting changed from a host's own menu (tray style, floating pill),
        // JSON-encoded.
        function onSettingRequested(key, value) {
            root.setSetting2(key, JSON.parse(value));
        }

        function onHistoryFinished(op, result) {
            if (op === "autoload") {
                try {
                    var r = JSON.parse(result);
                    if (r && Array.isArray(r.data)) {
                        // Samples taken while this read was in flight are this
                        // host's own and stay on top of the file's.
                        UsageHistory.adopt(root.historyStore, r.data);
                        root.syncUsageHistory();
                    }
                } catch (e) {}
                UsageHistory.opened(root.historyStore);
                root.saveHistory();
                // Settings → Data → Auto-delete: trim what the file holds on every start.
                var keepDays = Number(root.settings.historyKeepDays || 0);
                if (keepDays > 0) {
                    root.historyAutoPrune = true;
                    root.pruneHistory(keepDays);
                }
            } else if (op === "clear" || op === "prune") {
                root.historyChangesPending = Math.max(0, root.historyChangesPending - 1);
                try {
                    var c = JSON.parse(result);
                    if (c.error) {
                        root.historyMsg = c.error;
                    } else {
                        root.resetHistoryStore(c.data || []);
                        if (op === "clear")
                            root.historyMsg = root.i18np("Deleted %1 point.", "Deleted %1 points.", c.removed);
                        else if (c.removed > 0)
                            root.historyMsg = root.i18np("Removed %1 older point.", "Removed %1 older points.", c.removed);
                        else if (!root.historyAutoPrune)
                            root.historyMsg = root.i18n("Nothing older to remove.");
                    }
                    root.historyAutoPrune = false;
                } catch (e) {
                    root.historyAutoPrune = false;
                    root.historyMsg = root.i18n("Could not change the history.");
                }
                historyMsgTimer.restart();
            } else if (op === "export") {
                try {
                    var x = JSON.parse(result);
                    root.historyMsg = x.path ? root.i18n("Saved to %1", x.path) : (x.error || root.i18n("Export failed"));
                } catch (e) {
                    root.historyMsg = root.i18n("Export failed");
                }
                historyMsgTimer.restart();
            } else {
                root.finishHistorySave(result);
            }
        }
    }

    Component.onCompleted: {
        root.publishTrayLabels();
        backend.requestLanguages();
        backend.requestSettings();
        backend.requestLastSnapshot();
        backend.history("autoload", "");
    }

    Component.onDestruction: root.flushHistory()
}
