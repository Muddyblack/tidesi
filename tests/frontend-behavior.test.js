const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const UsageHistory = require("../ui/js/UsageHistory.js");
const SessionSources = require("../ui/js/SessionSources.js");
const PanelColor = require("../ui/js/PanelColor.js");
const rootDir = path.resolve(__dirname, "..");
const staleStateFixtures = require("./behavior/stale-state.json");
const FeatureTabs = {};
vm.runInNewContext(
    fs.readFileSync(path.join(rootDir, "ui/js/FeatureTabs.js"), "utf8")
        .replace(/^\.pragma library\s*/, ""),
    FeatureTabs,
);

const ProviderRegistry = {};
vm.runInNewContext(
    fs.readFileSync(path.join(rootDir, "ui/js/ProviderRegistry.js"), "utf8")
        .replace(/^\.pragma library\s*/, ""),
    ProviderRegistry,
);

function spendDateDaysAgo(days) {
    const now = new Date();
    const date = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
    date.setUTCDate(date.getUTCDate() - days);
    return date.toISOString().slice(0, 10);
}

// Execute the production QML adapter functions, without starting the desktop
// shell or its network polling. No copies of their mapping logic live here.
function qmlFunction(file, name) {
    const source = fs.readFileSync(path.join(rootDir, file), "utf8");
    const start = source.indexOf("    function " + name + "(");
    assert.notEqual(start, -1, name);
    const end = source.indexOf("\n    }", start);
    assert.notEqual(end, -1, name);
    return source.slice(start, end + 6);
}

function qmlFunctionBlock(file, name) {
    const source = fs.readFileSync(path.join(rootDir, file), "utf8");
    const start = source.indexOf("function " + name + "(");
    assert.notEqual(start, -1, `${file}: ${name}`);
    const opening = source.indexOf("{", start);
    assert.notEqual(opening, -1, `${file}: ${name} body`);
    let depth = 0;
    for (let index = opening; index < source.length; index++) {
        if (source[index] === "{") depth += 1;
        if (source[index] === "}") {
            depth -= 1;
            if (depth === 0) return source.slice(start, index + 1);
        }
    }
    assert.fail(`${file}: unterminated ${name}`);
}

function qmlPropertyBody(file, declaration) {
    const source = qmlSource(file);
    const start = source.indexOf(declaration);
    assert.notEqual(start, -1, `${file}: ${declaration}`);
    const opening = source.indexOf("{", start);
    assert.notEqual(opening, -1, `${file}: ${declaration} body`);
    let depth = 0;
    for (let index = opening; index < source.length; index++) {
        if (source[index] === "{") depth += 1;
        if (source[index] === "}") {
            depth -= 1;
            if (depth === 0) return source.slice(opening, index + 1);
        }
    }
    assert.fail(`${file}: unterminated ${declaration}`);
}

function qmlSource(file) {
    return fs.readFileSync(path.join(rootDir, file), "utf8");
}

function countOccurrences(source, text) {
    return source.split(text).length - 1;
}

function replaySourcePopupSelection(file, owner) {
    const sources = [
        { id: "codex", label: "Codex" },
        { id: "claude", label: "Claude" },
        { id: "muse", label: "Muse" },
        { id: "cline", label: "Cline" }
    ];
    const state = {
        sessionSources: sources,
        selectedSourceIds: ["codex"],
        pendingSourceIds: ["codex"],
        sessions: ["existing row"],
        searchQuery: "",
        queryCount: 0,
        querySourceIds: [],
        loading: false,
        normalizedSourceIds: ids => SessionSources.normalizeIds(ids, sources),
        sourceSignature: ids => SessionSources.signature(ids),
        queryOnly: () => {
            state.queryCount += 1;
            state.querySourceIds.push(state.selectedSourceIds.slice(0));
            state.loading = true;
        }
    };
    const sandbox = { [owner]: state, SessionSources };
    if (owner === "page") {
        sandbox.shell = {
            setSessionsSourceIds: ids => {
                state.selectedSourceIds = SessionSources.normalizeIds(ids, sources);
            },
            querySessions: () => {
                state.queryCount += 1;
                state.querySourceIds.push(state.selectedSourceIds.slice(0));
                state.loading = true;
            }
        };
    }
    const functions = ["setSourceSelection", "stageSourceSelection", "commitSourceSelection", "stageToggleSource"]
        .map(name => qmlFunctionBlock(file, name))
        .join("\n");
    vm.runInNewContext(`${functions}
        ${owner}.setSourceSelection = setSourceSelection;
        ${owner}.stageSourceSelection = stageSourceSelection;
        ${owner}.commitSourceSelection = commitSourceSelection;
        ${owner}.stageToggleSource = stageToggleSource;`, sandbox);
    return state;
}

function assertSourcePopupWiring(file, owner) {
    const source = qmlSource(file);
    assert.match(source, new RegExp(`onOpened:\\s*(?:\\{\\s*)?${owner}\\.pendingSourceIds = ${owner}\\.selectedSourceIds\\.slice\\(0\\);?\\s*(?:\\})?`, "s"));
    assert.match(source, new RegExp(`onClosed:\\s*(?:\\{\\s*)?${owner}\\.commitSourceSelection\\(\\);?\\s*(?:\\})?`, "s"));
    // Either a QQC2.CheckBox `checked:` binding or a custom row's `isChecked:`
    // — both must read the staged selection, not the committed one.
    assert.match(source, new RegExp(`(?:checked|isChecked): [^\\n]*${owner}\\.pendingSourceIds`));
    assert.doesNotMatch(qmlFunctionBlock(file, "stageToggleSource"), /queryOnly|querySessions/);
    assert.match(source, /ScrollView|Flickable/);
    assert.match(source, /availableHeight|maximumHeight|height: Math\.min/);
}

test("source popup stages multiple selections and commits once on close", () => {
    const frontends = [
        {
            file: "ui/SessionsPage.qml",
            owner: "page"
        }
    ];
    for (const frontend of frontends) {
        assertSourcePopupWiring(frontend.file, frontend.owner);
        const state = replaySourcePopupSelection(frontend.file, frontend.owner);

        state.stageToggleSource("claude", true);
        state.stageToggleSource("muse", true);
        assert.deepEqual(state.pendingSourceIds, ["codex", "claude", "muse"]);
        assert.deepEqual(state.selectedSourceIds, ["codex"]);
        assert.equal(state.queryCount, 0);
        assert.deepEqual(state.sessions, ["existing row"]);

        state.commitSourceSelection();
        assert.deepEqual(state.selectedSourceIds, ["codex", "claude", "muse"]);
        assert.equal(state.queryCount, 1);
        assert.deepEqual(state.querySourceIds, [["codex", "claude", "muse"]]);
        assert.deepEqual(state.sessions, ["existing row"]);

        state.commitSourceSelection();
        assert.equal(state.queryCount, 1);
    }
});

test("provider registries require an explicit true toggle once defaults are applied", () => {
    const applied = { providerDefaultsApplied: true };
    assert.equal(ProviderRegistry.enabled(Object.assign({}, applied), "claude"), false);
    assert.equal(ProviderRegistry.enabled(Object.assign({ providers: {} }, applied), "cursor"), false);
    assert.equal(ProviderRegistry.enabled(Object.assign({ providers: { claude: false } }, applied), "claude"), false);
    assert.equal(ProviderRegistry.enabled(Object.assign({ providers: { claude: true } }, applied), "claude"), true);
});

test("re-detection switches detected providers on and reports what changed", () => {
    const settings = { providerDefaultsApplied: true, providers: { claude: true, cursor: false }, keys: { x: "k" } };
    const applied = ProviderRegistry.applyDetected(settings, ["claude", "cursor", "opencode"]);
    assert.deepEqual(Array.from(applied.added), ["cursor", "opencode"]);
    assert.deepEqual(Array.from(applied.removed), []);
    assert.equal(applied.settings.providers.cursor, true);
    assert.equal(applied.settings.providers.opencode, true);
    assert.equal(applied.settings.keys.x, "k");
    assert.equal(settings.providers.cursor, false, "input settings are not mutated");
    const none = ProviderRegistry.applyDetected(applied.settings, ["claude", "cursor", "opencode"]);
    assert.equal(none.added.length + none.removed.length, 0);
    assert.equal(ProviderRegistry.labels(["claude", "opencode"]), "Claude, OpenCode");
});

test("re-detection switches off uninstalled tools but keeps keyed and manual providers", () => {
    const settings = {
        providerDefaultsApplied: true,
        providers: { claude: true, cursor: true, openai: true, openrouter: true },
        keys: { openai: "sk-proj-test" }
    };
    const applied = ProviderRegistry.applyDetected(settings, ["claude"]);
    assert.deepEqual(Array.from(applied.removed), ["cursor"]);
    assert.equal(applied.settings.providers.cursor, false);
    assert.equal(applied.settings.providers.openai, true, "an API key keeps the provider usable");
    assert.equal(applied.settings.providers.openrouter, true, "manual-only providers are never touched");
});

test("provider registries keep legacy defaults until defaults are applied", () => {
    assert.equal(ProviderRegistry.enabled({}, "claude"), true);
    assert.equal(ProviderRegistry.enabled({ providers: {} }, "cursor"), false);
    assert.equal(ProviderRegistry.enabled({ providers: { claude: false } }, "claude"), false);
    assert.equal(ProviderRegistry.enabled({ providers: { cursor: true } }, "cursor"), true);
});

test("startup initializes provider defaults before normal provider refresh", () => {
    const state = qmlSource("ui/AppState.qml");
    const command = qmlSource("ui/CommandBackend.qml");
    const windows = qmlSource("hosts/desktop/app.py");

    // Every host: no usage refresh before the defaults have answered.
    assert.match(qmlFunctionBlock("ui/AppState.qml", "refresh"), /providerDefaultsReady/);
    assert.match(qmlFunctionBlock("ui/AppState.qml", "applyLoadedSettings"), /initializeProviderDefaults\(\)/);
    assert.match(command, /--initialize-provider-defaults/);
    assert.match(windows, /config\.initialize_provider_defaults\(\)/);
    assert.ok(windows.indexOf("config.initialize_provider_defaults()") < windows.indexOf("backend = Backend(first_run)"));
    assert.match(state, /running: root\.providerDefaultsReady/);
});

test("source changes keep normal rows and preserve stale-response recovery clears", () => {
    const state = qmlSource("ui/AppState.qml");
    assert.doesNotMatch(qmlFunctionBlock("ui/AppState.qml", "setSessionsSourceIds"), /root\.sessions\s*=\s*\[\]/);
    assert.equal(countOccurrences(state, "root.sessions = [];"), 1);
});

test("session refresh policy uses deterministic cache-age and resume boundaries", () => {
    const policy = {};
    vm.runInNewContext(
        fs.readFileSync(path.join(rootDir, "ui/js/SessionRefreshPolicy.js"), "utf8")
            .replace(/^\.pragma library\s*/, ""),
        policy,
    );
    assert.equal(policy.RECONCILE_INTERVAL_MS, 600000);
    assert.equal(policy.cacheExpired(null), true);
    assert.equal(policy.cacheExpired(599), false);
    assert.equal(policy.cacheExpired(600), true);
    assert.equal(policy.refreshDelayMs(null), 600000);
    assert.equal(policy.refreshDelayMs(590), 10000);
    assert.equal(policy.refreshDelayMs(601), 600000);
    assert.equal(policy.resumed(10000, 99999), false);
    assert.equal(policy.resumed(10000, 100001), true);
});

test("every host queries cached pages first and preserves page state in flight", () => {
    const state = qmlSource("ui/AppState.qml");
    const request = qmlFunctionBlock("ui/AppState.qml", "requestSessions");
    const send = qmlFunctionBlock("ui/AppState.qml", "sendSessionsRequest");

    assert.match(state, /onSessionsViewVisibleChanged:[\s\S]{0,120}querySessions\(sessionsQuery, 0, false/);
    assert.match(send, /if \(reconcile\)[\s\S]*refreshSessionsAndQuery[\s\S]*else[\s\S]*backend\.refreshSessions/);
    assert.match(state, /function reconcileSessions\(query, sourceIds\)\s*\{\s*root\.requestSessions\(query, true, sourceIds, root\.sessionsOffset\)/);
    assert.match(state, /if \(requestId !== root\.sessionsRequestId[\s\S]*return;/);
    assert.doesNotMatch(request, /sessionsTotal\s*=\s*0|sessionsHasMore\s*=\s*false/);
});

test("session response metadata exposes safe freshness and source-removal status", () => {
    const cache = fs.readFileSync(path.join(rootDir, "backend/tidesi/session_cache.py"), "utf8");
    const state = qmlSource("ui/AppState.qml");

    assert.match(cache, /"cacheStatus"/);
    assert.match(cache, /"cacheAgeSeconds"/);
    assert.match(cache, /\["refreshStatus"\] = "incomplete" if incomplete else self\.refresh_status/);
    assert.match(cache, /\["removedSourceCount"\] = self\.removed_sources/);
    assert.match(state, /cacheStatus/);
    assert.match(state, /cacheAgeSeconds/);
    assert.match(state, /refreshStatus/);
});

test("the shared state rejects superseded pages and keeps last-good pagination metadata on cache failure", () => {
    const handle = qmlFunctionBlock("ui/AppState.qml", "handleSessions");
    const state = {
        sessionsRequestId: 5,
        sessionsQuery: "new",
        sessionsActiveSourceSignature: "",
        sessionsSourceSignature: "",
        sessionsLoading: true,
        sessions: [{ title: "last good" }],
        sessionsTotal: 60,
        sessionsTotalExact: true,
        sessionsHasMore: true,
        sessionsOffset: 0,
        sessionsSources: [{ id: "claude", label: "Claude Code" }],
        sessionsError: "",
        sessionsCacheStatus: "fresh",
        sessionsRefreshStatus: "not-run",
        normalizeSessionSources: values => values || [],
        sessionSourceSelectionHasStaleIds: () => false,
        i18n: value => value,
    };
    const sessionsReconcileTimer = { restart() {} };
    const sandbox = { root: state, JSON, Number, sessionsReconcileTimer, SessionRefreshPolicy: { cacheExpired: () => false } };
    vm.runInNewContext(`${handle}\nroot.handleSessions = handleSessions;`, sandbox);

    // An answer to an older request (or query) changes nothing.
    state.handleSessions(JSON.stringify({ sessions: [{ title: "superseded" }] }), "new", 4);
    state.handleSessions(JSON.stringify({ sessions: [{ title: "superseded" }] }), "old", 5);
    assert.deepEqual(state.sessions, [{ title: "last good" }]);
    assert.equal(state.sessionsTotal, 60);
    assert.equal(state.sessionsLoading, true);

    // A failed cache read keeps the last good page and its pagination.
    state.handleSessions(JSON.stringify({ cacheStatus: "failed", sessions: [], sources: [], total: 0 }), "new", 5);
    assert.deepEqual(state.sessions, [{ title: "last good" }]);
    assert.equal(state.sessionsTotal, 60);
    assert.equal(state.sessionsHasMore, true);
    assert.equal(state.sessionsTotalExact, true);
    assert.deepEqual(state.sessionsSources, [{ id: "claude", label: "Claude Code" }]);
    assert.equal(state.sessionsRefreshStatus, "failed");
    assert.equal(state.sessionsLoading, false);
});

test("panel thresholds and stale opacity remain panel contracts", () => {
    const slot = qmlSource("ui/PanelSlot.qml");
    assert.match(slot, /PanelColor\.colorFor\(slot\.pct/);
    assert.match(slot, /stale \? 0\.55/);
    for (const [pct, expected] of [[0, "normal"], [69, "normal"], [70, "warning"], [89, "warning"], [90, "danger"], [100, "danger"]])
        assert.equal(PanelColor.level(pct), expected, `${pct}%`);

    // The pill falls back to the last real provider while a feature tab is open.
    const state = qmlSource("ui/AppState.qml");
    assert.match(qmlFunctionBlock("ui/AppState.qml", "unpinnedPillProvider"), /lastProviderId/);
    assert.match(state, /readonly property var pillSlots/);
});

// A replay only paints a widget that has no live answer yet: once one arrived,
// a replay that resolves late must leave the live state (and its freshness) alone.
const hyprlandCost = qmlFunction("ui/SessionsPage.qml", "sessionCostText");
const windows = ["providerById", "activeProvider", "unpinnedPillProvider", "pillProvider", "publishTray", "tr", "trMessage"]
    .map(name => qmlFunction("ui/AppState.qml", name)
        + "\nroot." + name + " = " + name + ";")
    .join("\n");

function publishWindowsTray(state) {
    let published;
    const root = { providers: [], settings: {}, pinnedTabs: [], panelProviderIds: [], catalog: {}, i18n: text => text, providerIcon: () => "", ...state };
    const backend = { defaultTrayStyle: "numbers", publishTrayState: value => { published = JSON.parse(value); } };
    const I18n = { i18n: (catalog, text, ...args) => text.replace(/%(\d)/g, (all, i) => args[Number(i) - 1] ?? all) };
    vm.runInNewContext(windows + "\nroot.publishTray();", { root, backend, I18n });
    return published;
}

function renderSessionCost(source, entry, usesShell) {
    const replace = (text, ...values) => text.replace(/%(\d)/g, (all, index) => values[Number(index) - 1] ?? all);
    const context = usesShell
        ? { root: {}, shell: { i18n: replace }, FeatureTabs }
        : { root: {}, i18n: replace, FeatureTabs };
    vm.runInNewContext(source + "\nroot.sessionCostText = sessionCostText;", context);
    return context.root.sessionCostText(entry);
}

test("shared frontend behavior", async t => {
    const { stdout } = await promisify(execFile)(process.env.PYTHON || "python3",
        [path.join(rootDir, "scripts/frontend-fixtures.py")]);
    const cases = JSON.parse(stdout);
    for (const scenario of cases) {
        await t.test(scenario.name + ": tray publication and shared history", () => {
            const provider = scenario.envelope.providers[0];
            const published = publishWindowsTray({
                activeId: provider.id, activeIsFeature: false, providers: [provider]
            });
            assert.deepEqual(published.slots.map(s => s.text || Math.round(s.pct) + "%"), scenario.expected.panelText);
            assert.deepEqual(UsageHistory.collect([provider]), scenario.expected.history);
        });
    }
});


test("Windows tray keeps provider usage while a feature tab is active", () => {
    const providers = [
        { id: "claude", label: "Claude", slots: [{ pct: 25 }] },
        { id: "openai", label: "OpenAI", slots: [{ pct: 70 }] }
    ];
    for (const activeId of ["overview", "spend", "sessions"]) {
        const state = { activeId, activeIsFeature: true, providers, lastProviderId: "openai" };
        assert.deepEqual(publishWindowsTray(state).slots.map(s => s.pct), [70]);
        assert.deepEqual(publishWindowsTray({ ...state, lastProviderId: "removed" }).slots.map(s => s.pct), [25]);
        assert.deepEqual(publishWindowsTray({ ...state, providers: [] }).slots, []);
    }
});

test("local spend rows merge actual and estimated costs by source", () => {
    const rows = FeatureTabs.localSpendRows({
        actual: {
            totalUSD: 12.34,
            costStatus: "exact",
            providers: {
                OpenCode: { costUSD: 12.34, costStatus: "exact" },
            },
        },
        estimated: {
            totalUSD: 2.5,
            costStatus: "partial",
            providers: {
                " opencode ": { costUSD: 1.5, costStatus: "partial" },
                cline: { costUSD: 1, costStatus: "exact" },
            },
        },
    });

    assert.deepEqual(JSON.parse(JSON.stringify(rows.map(row => ({
        id: row.id,
        label: row.label,
        cost: row.cost,
        local: row.local,
        provenance: row.provenance,
        costStatus: row.costStatus,
        costBreakdown: row.costBreakdown,
        note: row.note,
    })))), [
        {
            id: "local-opencode",
            label: "OpenCode",
            cost: 13.84,
            local: true,
            provenance: "mixed",
            costStatus: "partial",
            costBreakdown: { actualUSD: 12.34, estimatedUSD: 1.5 },
            note: "local CLI logs · mixed · partial",
        },
        {
            id: "local-cline",
            label: "Cline",
            cost: 1,
            local: true,
            provenance: "estimated",
            costStatus: "exact",
            costBreakdown: { actualUSD: 0, estimatedUSD: 1 },
            note: "local CLI logs · estimated · exact",
        },
    ]);

    assert.equal(FeatureTabs.spendTotal([
        { id: "openai", cost: 4, currency: "USD" },
        ...rows,
    ], "USD"), 4);
});

test("OpenCode local rows retain separate upstream billing providers", () => {
    const rows = FeatureTabs.localSpendRows({
        actual: {
            totalUSD: 0.13,
            costStatus: "exact",
            providers: {
                "openai::opencode": { costUSD: 0.13, costStatus: "exact", source: "opencode" },
            },
        },
        estimated: {
            totalUSD: 0.42,
            costStatus: "exact",
            providers: {
                "anthropic::opencode": { costUSD: 0.42, costStatus: "exact", source: "opencode" },
            },
        },
    });

    assert.deepEqual(JSON.parse(JSON.stringify(rows.map(row => ({
        id: row.id,
        label: row.label,
        cost: row.cost,
        source: row.source,
        note: row.note,
    })))), [
        { id: "local-opencode-openai", label: "OpenAI", cost: 0.13, source: "opencode", note: "via OpenCode · actual · exact" },
        { id: "local-opencode-anthropic", label: "Anthropic", cost: 0.42, source: "opencode", note: "via OpenCode · estimated · exact" },
    ]);
});

test("OpenCode usage chart range selects matching daily data and period summary", () => {
    const file = "ui/OpenCodeUsageChart.qml";
    const source = qmlSource(file);
    const tab = require("../ui/js/OpenCodeUsage.js");

    const now = new Date(2026, 8, 22, 12).getTime();
    const series = [
        { date: "2026-08-22", total: 900 },
        { date: "2026-09-01", total: 50 },
        { date: "2026-09-16", total: 100 },
        { date: "2026-09-22", total: 200 },
    ];
    const dailyTotals = points => Array.from(points, point => [point.date, point.total]);
    const periods = [
        { key: "7d", label: "Last 7 days", tokens: 300, sessions: 2 },
        { key: "30d", label: "Last 30 days", tokens: 350, sessions: 3 },
        { key: "all", label: "All time", tokens: 1250, sessions: 4 },
    ];

    assert.deepEqual(dailyTotals(tab.seriesForRange("7d", series, now)), [
        ["2026-09-16", 100], ["2026-09-17", 0], ["2026-09-18", 0], ["2026-09-19", 0],
        ["2026-09-20", 0], ["2026-09-21", 0], ["2026-09-22", 200],
    ]);
    assert.equal(tab.periodForRange("7d", periods).tokens, 300);
    assert.equal(tab.periodForRange("7d", periods).label, "Last 7 days");
    const thirtyDays = Array.from(tab.seriesForRange("30d", series, now));
    assert.equal(thirtyDays.length, 30);
    assert.deepEqual(dailyTotals(thirtyDays.filter(point => ["2026-09-01", "2026-09-16", "2026-09-22"].includes(point.date))), [
        ["2026-09-01", 50], ["2026-09-16", 100], ["2026-09-22", 200],
    ]);
    assert.equal(thirtyDays.find(point => point.date === "2026-09-02").total, 0);
    assert.equal(tab.periodForRange("30d", periods).tokens, 350);
    assert.equal(tab.periodForRange("30d", periods).label, "Last 30 days");
    const allDays = Array.from(tab.seriesForRange("all", series, now));
    assert.equal(allDays.length, 32);
    assert.deepEqual(dailyTotals(allDays.filter(point => point.total > 0)), [
        ["2026-08-22", 900], ["2026-09-01", 50], ["2026-09-16", 100], ["2026-09-22", 200],
    ]);
    assert.equal(allDays.find(point => point.date === "2026-08-23").total, 0);
    const longHistory = [
        { date: "2000-01-01", total: 100 },
        { date: "2026-09-22", total: 200 },
    ];
    const boundedAll = Array.from(tab.seriesForRange("all", longHistory, now));
    assert.ok(boundedAll.length <= 366);
    assert.equal(boundedAll.reduce((sum, point) => sum + point.total, 0), 300);
    assert.ok(boundedAll.some(point => point.total === 0));
    assert.ok(boundedAll[0].endDate, "grouped all-time points expose their covered date interval");
    assert.ok(boundedAll.every((point, index) => index === 0 || boundedAll[index - 1].date < point.date));
    assert.equal(tab.periodForRange("all", periods).tokens, 1250);
    assert.equal(tab.periodForRange("all", periods).label, "All time");
    // The range pills come from the shared window list; the chart itself is
    // the shared UsageChart, so hover, navigation and axes are not redrawn here.
    assert.deepEqual(Array.from(tab.chartWindows(series, now), window => window.label), ["7D", "30D", "All"]);
    assert.match(source, /^UsageChart \{/m);
    assert.match(source, /OpenCodeUsage\.chartWindows\(sourceSeries/);
    assert.match(source, /stats\.dailySeries && stats\.dailySeries\.length \? stats\.dailySeries : stats\.dailyTokens/);
    assert.doesNotMatch(source, /Canvas \{/);
});

test("every host shows the OpenCode daily chart on the Usage tab", () => {
    const chart = qmlSource("ui/OpenCodeUsageChart.qml");
    const popup = qmlSource("ui/PopupContent.qml");
    for (const source of [chart]) {
        assert.match(source, /UsageChart \{/);
        assert.match(source, /OpenCodeUsage\.chartWindows\(sourceSeries/);
        assert.match(source, /stats\.dailySeries && stats\.dailySeries\.length \? stats\.dailySeries : stats\.dailyTokens/);
    }
    assert.match(popup, /OpenCodeUsageChart \{\s*visible: \(shell\.activeId === "opencode" \|\| shell\.activeId === "mimo" \|\| shell\.activeId === "junie" \|\| shell\.activeId === "pi"\)[^\n]*stats \|\| \{\}\)\.available === true/);
    // Inside the Usage column, not the Stats sub-tab.
    assert.ok(popup.indexOf("OpenCodeUsageChart") < popup.indexOf("StatsSection {"));
});

test("local spend rows keep legacy flat totals and reject unavailable groups", () => {
    assert.equal(FeatureTabs.localSpendRows({
        actual: { totalUSD: 1, costStatus: "unavailable" },
        estimated: { totalUSD: Infinity, costStatus: "exact" },
    }).length, 0);

    assert.equal(FeatureTabs.localSpendRows({
        actual: { totalUSD: 1, costStatus: "exact" },
        estimated: { totalUSD: 2, costStatus: "partial" },
    }).length, 0);

    const legacy = FeatureTabs.localSpendRows({ totalUSD: 3.5, costStatus: "partial" });
    assert.deepEqual({ ...legacy[0] }, {
        id: "local",
        label: "Local sessions",
        cost: 3.5,
        currency: "USD",
        note: "local CLI logs · partial",
        local: true,
        legacy: true,
        costStatus: "partial",
    });

    for (const localSpend of [
        { actual: { totalUSD: 0, costStatus: "exact" } },
        { actual: { totalUSD: -1, costStatus: "partial" } },
        { actual: { totalUSD: "3.5", costStatus: "exact" } },
        { totalUSD: NaN, costStatus: "exact" },
        null,
    ]) {
        assert.equal(FeatureTabs.localSpendRows(localSpend).length, 0);
    }
});

test("local source labels normalize known IDs and safe fallbacks", () => {
    assert.equal(FeatureTabs.localSourceLabel(" OPENAI "), "Codex");
    assert.equal(FeatureTabs.localSourceLabel("claude_code"), "Claude Code");
    assert.equal(FeatureTabs.localSourceLabel("custom_source"), "Custom Source");
    assert.equal(FeatureTabs.localSourceLabel("  "), "Local source");
});

test("upstream provider labels cover every OpenCode-routed provider", () => {
    assert.equal(FeatureTabs.upstreamProviderLabel("ollama-cloud"), "Ollama Cloud");
    assert.equal(FeatureTabs.upstreamProviderLabel("ollama"), "Ollama");
    assert.equal(FeatureTabs.upstreamProviderLabel("github-copilot"), "GitHub Copilot");
    assert.equal(FeatureTabs.upstreamProviderLabel("google"), "Google");
    assert.equal(FeatureTabs.upstreamProviderLabel("zenmux"), "ZenMux");
    assert.equal(FeatureTabs.upstreamProviderLabel("opencode"), "OpenCode Zen");
    assert.equal(FeatureTabs.upstreamProviderLabel("anthropic"), "Anthropic");
});

test("spend views identify provider totals and constrain local metadata", () => {
    // The grand total sits in each frontend's popup header, not in the Spend
    // view itself, so the figure stays visible while the view scrolls.
    for (const file of ["ui/PopupHeader.qml"]) {
        assert.match(fs.readFileSync(path.join(rootDir, file), "utf8"), /Provider\/API total/);
    }
    for (const file of ["ui/SpendPage.qml"]) {
        const source = fs.readFileSync(path.join(rootDir, file), "utf8");
        assert.match(source, /maximumLineCount: 1/);
        assert.match(source, /elide: Text\.ElideRight/);
        assert.match(source, /wrapMode: Text\.NoWrap/);
    }
});

test("provider spend rows and totals remain provider-only", () => {
    const rows = FeatureTabs.spendProviderRows([
        {
            id: "claude",
            label: "Claude",
            details: { organizationUsage: { totalCostUSD: 4.5 } },
            accent: "#cc785c",
        },
        {
            id: "openai",
            label: "OpenAI",
            details: { organizationUsage: { totalCostUSD: 2 } },
            accent: "#10a37f",
        },
        {
            id: "openrouter",
            label: "OpenRouter",
            details: { usageUSD: 3 },
            accent: "#9333ea",
        },
        {
            id: "muse",
            label: "Muse",
            details: { stats: { totalCostUSD: 0 } },
            accent: "#0064e0",
        },
    ]);

    assert.deepEqual(Array.from(rows, row => ({ ...row, dailyCost: [...row.dailyCost], dailyTokens: [...row.dailyTokens] })), [
        {
            id: "claude",
            label: "Claude",
            accent: "#cc785c",
            icon: "",
            cost: 4.5,
            currency: "USD",
            note: "30d API",
            dailyCost: [],
            dailyTokens: [],
        },
        {
            id: "openrouter",
            label: "OpenRouter",
            accent: "#9333ea",
            icon: "",
            cost: 3,
            currency: "USD",
            note: "all-time",
            dailyCost: [],
            dailyTokens: [],
        },
        {
            id: "openai",
            label: "OpenAI",
            accent: "#10a37f",
            icon: "",
            cost: 2,
            currency: "USD",
            note: "30d API",
            dailyCost: [],
            dailyTokens: [],
        },
    ]);
    assert.equal(FeatureTabs.spendTotal(rows, "USD"), 9.5);
});

test("provider spend rows take cost-by-day from sessions and tokens-by-day from stats", () => {
    // A provider blob only ever carries totals; per-day cost lives on the
    // session rollups, which is also the only place plan-covered work has a
    // non-zero figure.
    const rows = FeatureTabs.spendProviderRows(
        [
            {
                id: "claude",
                label: "Claude",
                details: {
                    organizationUsage: { totalCostUSD: 4.5 },
                    stats: { dailySeries: [{ date: "2026-01-01", total: 100 }] },
                },
            },
            {
                id: "openai",
                label: "OpenAI",
                details: { organizationUsage: { totalCostUSD: 2 } },
            },
        ],
        {
            subscription: {
                costStatus: "exact",
                providers: {
                    claude: {
                        costUSD: 4.5,
                        costStatus: "exact",
                        dailyUSD: [{ date: "2026-01-01", usd: 4.5 }],
                    },
                },
            },
        },
    );
    assert.deepEqual(Array.from(rows[0].dailyCost, x => ({ ...x })), [{ date: "2026-01-01", usd: 4.5 }]);
    assert.deepEqual(Array.from(rows[0].dailyTokens, x => ({ ...x })), [{ date: "2026-01-01", total: 100 }]);
    // No sessions for this provider, so no cost history — not a fake zero line.
    assert.deepEqual(Array.from(rows[1].dailyCost), []);
    assert.deepEqual(Array.from(rows[1].dailyTokens), []);
});

test("dailyCostByProvider merges every billing group and needs no per-provider code", () => {
    const daily = FeatureTabs.dailyCostByProvider({
        estimated: {
            providers: {
                grok: { dailyUSD: [{ date: "2026-01-01", usd: 1 }, { date: "2026-01-02", usd: 2 }] },
                "anthropic::opencode": { dailyUSD: [{ date: "2026-01-01", usd: 0.5 }] },
            },
        },
        subscription: {
            providers: { grok: { dailyUSD: [{ date: "2026-01-01", usd: 0.25 }] } },
        },
    });
    // Same provider in two groups sums per day; the "::source" rollup key
    // collapses onto the provider it belongs to.
    assert.deepEqual(Array.from(daily.grok, x => ({ ...x })), [
        { date: "2026-01-01", usd: 1.25 },
        { date: "2026-01-02", usd: 2 },
    ]);
    assert.deepEqual(Array.from(daily.anthropic, x => ({ ...x })), [{ date: "2026-01-01", usd: 0.5 }]);
});

test("spendTimeline zips cost and token series and can trim to a trailing window", () => {
    const cost = [
        { date: spendDateDaysAgo(2), usd: 1 },
        { date: spendDateDaysAgo(1), usd: 2 },
        { date: spendDateDaysAgo(0), usd: 3 },
    ];
    const tokens = [
        { date: spendDateDaysAgo(1), total: 20 },
        { date: spendDateDaysAgo(0), total: 30 },
    ];
    assert.deepEqual(Array.from(FeatureTabs.spendTimeline(cost, tokens, 0), x => ({ ...x })), [
        { date: spendDateDaysAgo(2), usd: 1, total: 0 },
        { date: spendDateDaysAgo(1), usd: 2, total: 20 },
        { date: spendDateDaysAgo(0), usd: 3, total: 30 },
    ]);
    assert.deepEqual(Array.from(FeatureTabs.spendTimeline(cost, tokens, 2), x => ({ ...x })), [
        { date: spendDateDaysAgo(1), usd: 2, total: 20 },
        { date: spendDateDaysAgo(0), usd: 3, total: 30 },
    ]);
    assert.deepEqual(Array.from(FeatureTabs.spendTimeline([], [], 30)), []);
});

test("spendTimeline clips tokens to the cost window so two histories never share one axis", () => {
    // Cost comes from session rows (recent), tokens from the provider's own
    // aggregate (older). Plotting the union drew a token line that stopped
    // exactly where the cost line started.
    const cost = [
        { date: "2026-08-22", usd: 5 },
        { date: "2026-08-23", usd: 7 },
    ];
    const tokens = [
        { date: "2026-04-07", total: 900 },
        { date: "2026-07-06", total: 800 },
        { date: "2026-08-23", total: 100 },
    ];
    const points = FeatureTabs.spendTimeline(cost, tokens, 0);

    assert.deepEqual(Array.from(points, x => ({ ...x })), [
        { date: "2026-08-22", usd: 5, total: 0 },
        { date: "2026-08-23", usd: 7, total: 100 },
    ]);
});

test("spendTimeline keeps the token range when a row has no cost history at all", () => {
    const points = FeatureTabs.spendTimeline([], [{ date: "2026-09-10", total: 5 }, { date: "2026-09-14", total: 9 }], 0);

    assert.deepEqual(Array.from(points, x => ({ ...x })), [
        { date: "2026-09-10", usd: 0, total: 5 },
        { date: "2026-09-14", usd: 0, total: 9 },
    ]);
});

test("provider spend rows reject non-finite and non-numeric reported values", () => {
    const malformed = [
        { id: "claude", details: { organizationUsage: { totalCostUSD: Infinity } } },
        { id: "openai", details: { organizationUsage: { totalCostUSD: NaN } } },
        { id: "openrouter", details: { usageUSD: "3.25" } },
        { id: "mistral", details: { vibe: { totalCost: true } } },
    ];
    assert.equal(FeatureTabs.spendProviderRows(malformed).length, 0);

    const validAndMalformed = [
        { id: "openai", label: "OpenAI", details: { totalCostUSD: 2 } },
        { id: "claude", label: "Claude", details: { totalCostUSD: 4.5 } },
        { id: "openrouter", details: { usageUSD: Infinity } },
        { id: "mistral", details: { totalCost: "8" } },
        { id: "muse", details: { stats: { totalCostUSD: false } } },
    ];
    const rows = FeatureTabs.spendProviderRows(validAndMalformed);
    assert.deepEqual(Array.from(rows, row => [row.id, row.cost]), [
        ["claude", 4.5],
        ["openai", 2],
    ]);
    assert.equal(FeatureTabs.spendTotal(rows, "USD"), 6.5);
});

test("spend totals ignore non-numeric and non-finite row costs", () => {
    const rows = [
        { currency: "USD", cost: 4 },
        { currency: "USD", cost: 2 },
        { currency: "USD", cost: Infinity },
        { currency: "USD", cost: NaN },
        { currency: "USD", cost: "8" },
        { currency: "USD", cost: true },
        { currency: "EUR", cost: 99 },
    ];
    assert.equal(FeatureTabs.spendTotal(rows, "USD"), 6);
    assert.equal(FeatureTabs.spendTotal(rows, "EUR"), 99);
});

test("spend timeframe filtering recomputes bounded rows and preserves ALL", () => {
    const rows = [{
        id: "claude",
        cost: 6,
        currency: "USD",
        dailyCost: [
            { date: spendDateDaysAgo(2), usd: 1 },
            { date: spendDateDaysAgo(1), usd: 2 },
            { date: spendDateDaysAgo(0), usd: 3 },
        ],
        dailyTokens: [
            { date: spendDateDaysAgo(2), total: 10 },
            { date: spendDateDaysAgo(1), total: 20 },
            { date: spendDateDaysAgo(0), total: 30 },
        ],
    }];

    const oneDay = FeatureTabs.spendRowsForWindow(rows, 1);
    const all = FeatureTabs.spendRowsForWindow(rows, 0);

    assert.equal(oneDay[0].cost, 3);
    assert.equal(JSON.stringify(oneDay[0].dailyCost), JSON.stringify([{ date: spendDateDaysAgo(0), usd: 3 }]));
    assert.equal(JSON.stringify(oneDay[0].dailyTokens), JSON.stringify([{ date: spendDateDaysAgo(0), total: 30 }]));
    assert.strictEqual(all, rows);
});

test("spend timeframe filtering zeroes out rows without bounded history instead of dropping them", () => {
    const rows = [{ id: "legacy", cost: 4, currency: "USD", dailyCost: [], dailyTokens: [] }];

    const sevenDays = FeatureTabs.spendRowsForWindow(rows, 7);
    assert.equal(sevenDays.length, 1);
    assert.equal(sevenDays[0].cost, 0);
    assert.equal(FeatureTabs.spendRowsForWindow(rows, 0)[0].cost, 4);
});

test("spend timeframe windows end today rather than at the last recorded day", () => {
    const today = new Date();
    const lastRecorded = new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()));
    lastRecorded.setUTCDate(lastRecorded.getUTCDate() - 45);
    const staleDate = lastRecorded.toISOString().slice(0, 10);
    const rows = [{
        id: "stale",
        cost: 4,
        currency: "USD",
        dailyCost: [{ date: staleDate, usd: 4 }],
        dailyTokens: [{ date: staleDate, total: 40 }],
    }];

    const thirtyDays = FeatureTabs.spendRowsForWindow(rows, 30);
    assert.equal(thirtyDays.length, 1);
    assert.equal(thirtyDays[0].cost, 0);
    assert.equal(FeatureTabs.spendRowsForWindow(rows, 0)[0].cost, 4);
});

test("spend prices align their decimal points with fixed-width digits", () => {
    // Two-decimal amounts plus fixed-width digits put "." in one column.
    for (const file of ["ui/SpendPage.qml"]) {
        const source = fs.readFileSync(path.join(rootDir, file), "utf8");
        assert.match(source, /font\.family: "monospace"/);
        assert.doesNotMatch(source, /Layout\.preferredWidth: 60/);
        assert.doesNotMatch(source, /Layout\.maximumWidth: 60/);
        assert.doesNotMatch(source, /horizontalAlignment: Text\.AlignLeft/);
    }
});

test("session rows render provenance, coverage, unavailable, and legacy costs", () => {
    const cases = [
        [{ costStatus: "exact", costProvenance: "actual", costUSD: 0.123456 }, "Actual provider cost: $0.1235 (exact)"],
        [{ costStatus: "partial", costProvenance: "actual", costUSD: 0.123456 }, "Actual provider cost: $0.1235 (partial)"],
        [{ costStatus: "exact", costProvenance: "estimated", costUSD: 0.123456 }, "Calculated estimate: $0.1235 (exact)"],
        [{ costStatus: "partial", costProvenance: "estimated", costUSD: 0.123456 }, "Calculated estimate: $0.1235 (partial)"],
        [{ costStatus: "exact", costProvenance: "mixed", costUSD: 0.123456, costBreakdown: { actualUSD: 0.1, estimatedUSD: 0.023456 } }, "Mixed cost: $0.1235 (exact)"],
        [{ costStatus: "partial", costProvenance: "mixed", costUSD: 0.123456, costBreakdown: { actualUSD: 0.1, estimatedUSD: 0.023456 } }, "Mixed cost: $0.1235 (partial)"],
        [{ costStatus: "exact", costUSD: 0.123456 }, "Cost: $0.1235 (exact)"],
        [{ costStatus: "unavailable" }, "Cost unavailable"],
        [{ costStatus: "exact", costProvenance: "unknown", costUSD: 0.123456 }, "Cost unavailable"],
        [{ costStatus: "exact", costProvenance: "actual", costUSD: "0.123456" }, "Cost unavailable"],
    ];
    for (const [entry, expected] of cases) {
        assert.equal(renderSessionCost(hyprlandCost, entry, true), expected);
    }
});

test("shared Hyprland/Windows page stages source selection until popup close", () => {
    const state = replaySourcePopupSelection("ui/SessionsPage.qml", "page");
    state.pendingSourceIds = state.selectedSourceIds.slice(0);
    state.stageSourceSelection(SessionSources.toggled(state.pendingSourceIds, "claude", true, state.sessionSources));
    assert.equal(state.queryCount, 0, "no query while the popup is open");
    assert.deepEqual(state.sessions, ["existing row"], "rows stay visible while the popup is open");
    state.commitSourceSelection();
    assert.equal(state.queryCount, 1, "one query after the popup closes");
    assert.deepEqual(state.selectedSourceIds, ["codex", "claude"]);
});

test("shared Hyprland/Windows page close without changes does not query", () => {
    const state = replaySourcePopupSelection("ui/SessionsPage.qml", "page");
    state.pendingSourceIds = state.selectedSourceIds.slice(0);
    state.commitSourceSelection();
    assert.equal(state.queryCount, 0);
});


test("plan-covered spend is its own row and never joins the metered total", () => {
    const rows = FeatureTabs.localSpendRows({
        estimated: {
            costStatus: "exact",
            totalUSD: 2,
            providers: { cline: { costUSD: 2, costStatus: "exact", costProvenance: "estimated" } },
        },
        subscription: {
            costStatus: "exact",
            totalUSD: 100,
            providers: { claude: { costUSD: 100, costStatus: "exact", costProvenance: "estimated" } },
        },
    });

    const plan = rows.find((row) => row.billing === "subscription");
    const metered = rows.find((row) => row.billing !== "subscription");
    assert.ok(plan, "expected a plan-covered row");
    assert.equal(plan.cost, 100);
    assert.match(plan.note, /would cost on API/);
    assert.equal(metered.cost, 2);
    // Local rows never count toward the provider/API total either way.
    assert.equal(FeatureTabs.spendTotal(rows, "USD"), 0);
});

test("spend summary computes metered, plan-covered and all-inclusive totals", () => {
    const rows = [
        { id: "cursor", cost: 2.10, currency: "USD" },
        { id: "local-grok", cost: 2.37, currency: "USD", local: true },
        { id: "plan-claude", cost: 3966.35, currency: "USD", local: true, billing: "subscription" },
        { id: "plan-codex", cost: 1457.64, currency: "USD", local: true, billing: "subscription" },
    ];

    assert.equal(Math.round(FeatureTabs.spendMeteredTotal(rows, "USD") * 100) / 100, 4.47);
    assert.equal(Math.round(FeatureTabs.spendPlanTotal(rows, "USD") * 100) / 100, 5423.99);
    assert.equal(Math.round(FeatureTabs.spendAllTotal(rows, "USD") * 100) / 100, 5428.46);
    assert.equal(FeatureTabs.spendSummaryText(rows, "USD"), "Metered: $4.47 · Incl. plan: $5428.46");

    // Only plan-covered
    assert.equal(FeatureTabs.spendSummaryText([rows[2]], "USD"), "Incl. plan: $3966.35");

    // Only metered
    assert.equal(FeatureTabs.spendSummaryText([rows[0], rows[1]], "USD"), "Metered: $4.47");

    // Empty
    assert.equal(FeatureTabs.spendSummaryText([], "USD"), "");

    // Tooltip breakdown
    const tooltip = FeatureTabs.spendSummaryTooltip(rows, "USD");
    assert.match(tooltip, /Metered \(out-of-pocket\): \$4\.47/);
    assert.match(tooltip, /Covered by plan \(subscription\): \$5423\.99/);
    assert.match(tooltip, /Total including plan: \$5428\.46/);
});

test("a plan-covered row's daily cost adds up to the total shown on it", () => {
    // The whole point of sourcing cost from sessions: a Pro/Max plan reports
    // $0 of real API cost, so a chart built from provider stats sat flat at
    // zero under a four-figure total.
    const rawProviders = [
        { id: "claude", details: { stats: { dailySeries: [{ date: "2026-01-01", total: 500 }] } } },
    ];
    const rows = FeatureTabs.localSpendRows(
        {
            subscription: {
                costStatus: "exact",
                totalUSD: 100,
                providers: {
                    claude: {
                        costUSD: 100,
                        costStatus: "exact",
                        costProvenance: "estimated",
                        dailyUSD: [{ date: "2026-01-01", usd: 60 }, { date: "2026-01-02", usd: 40 }],
                    },
                },
            },
        },
        [],
        rawProviders,
    );
    const plan = rows.find((row) => row.billing === "subscription");
    assert.ok(plan, "expected a plan-covered row");
    assert.deepEqual(Array.from(plan.dailyCost, x => ({ ...x })), [
        { date: "2026-01-01", usd: 60 },
        { date: "2026-01-02", usd: 40 },
    ]);
    assert.equal(plan.dailyCost.reduce((sum, point) => sum + point.usd, 0), plan.cost);
    assert.deepEqual(Array.from(plan.dailyTokens, x => ({ ...x })), [{ date: "2026-01-01", total: 500 }]);
});

test("local rows fall back to empty dailyCost/dailyTokens when rawProviders is omitted", () => {
    const rows = FeatureTabs.localSpendRows({
        estimated: {
            costStatus: "exact",
            totalUSD: 2,
            providers: { cline: { costUSD: 2, costStatus: "exact", costProvenance: "estimated" } },
        },
    });
    assert.deepEqual(Array.from(rows[0].dailyCost), []);
    assert.deepEqual(Array.from(rows[0].dailyTokens), []);
});

test("session cost info reports the billing mode it was priced under", () => {
    const plan = FeatureTabs.sessionCostInfo({
        costUSD: 1.5,
        costStatus: "exact",
        costProvenance: "estimated",
        costBilling: "subscription",
    });
    const metered = FeatureTabs.sessionCostInfo({
        costUSD: 1.5,
        costStatus: "exact",
        costProvenance: "estimated",
    });

    assert.equal(plan.billing, "subscription");
    assert.equal(metered.billing, "api");
    assert.equal(FeatureTabs.sessionCostInfo({ costStatus: "unavailable" }).billing, "api");
});

// ── Model rate table helpers (shared by the Plasma and Quickshell Spend views) ──

test("rateText formats rates, free tiers and missing values distinctly", () => {
    assert.equal(FeatureTabs.rateText(0), "free");
    assert.equal(FeatureTabs.rateText(0.25), "$0.250");
    assert.equal(FeatureTabs.rateText(15), "$15.00");
    assert.equal(FeatureTabs.rateText(undefined), "—");
    assert.equal(FeatureTabs.rateText(NaN), "—");
    assert.equal(FeatureTabs.rateText(Infinity), "—");
});

test("rateAge buckets the catalog age and stays empty when unknown", () => {
    const now = 1_700_000_000_000;
    const at = (secondsAgo) => FeatureTabs.rateAge(now / 1000 - secondsAgo, null, now);
    assert.equal(at(30), "updated just now");
    assert.equal(at(7200), "updated 2h ago");
    assert.equal(at(3 * 86400), "updated 3d ago");
    assert.equal(at(3 * 604800), "updated 3w ago");
    // A missing or unusable timestamp renders nothing rather than "1970".
    assert.equal(FeatureTabs.rateAge(0, null, now), "");
    assert.equal(FeatureTabs.rateAge(undefined, null, now), "");
    // A clock skewed behind the cache must not produce a negative age.
    assert.equal(FeatureTabs.rateAge(now / 1000 + 600, null, now), "updated just now");
});

test("rateAge routes through the frontend's i18n function", () => {
    const seen = [];
    const i18n = (text, arg) => {
        seen.push([text, arg]);
        return "T:" + text.replace("%1", arg);
    };
    const now = 1_700_000_000_000;
    assert.equal(FeatureTabs.rateAge(now / 1000 - 7200, i18n, now), "T:updated 2h ago");
    assert.deepEqual(seen, [["updated %1h ago", 2]]);
});

test("rate paging arithmetic is stable at the edges", () => {
    assert.equal(FeatureTabs.ratePageCount(0, 40), 1);
    assert.equal(FeatureTabs.ratePageCount(676, 40), 17);
    assert.equal(FeatureTabs.ratePageCount(80, 40), 2);
    assert.equal(FeatureTabs.ratePageNumber(0, 40, 676), 1);
    assert.equal(FeatureTabs.ratePageNumber(640, 40, 676), 17);
    // Never divide by zero, and never report a page past the last one.
    assert.equal(FeatureTabs.ratePageCount(100, 0), 100);
    assert.equal(FeatureTabs.ratePageNumber(9999, 40, 676), 17);
});

test("parseRateTable normalizes payloads and rejects unusable output", () => {
    const parsed = FeatureTabs.parseRateTable(
        '{"rows":[{"provider":"anthropic","model":"claude-opus-5","input":15,"output":75}],' +
        '"total":676,"unit":"USD per 1M tokens","fetchedAt":1789976769,"error":""}\n');
    assert.equal(parsed.total, 676);
    assert.equal(parsed.unit, "USD per 1M tokens");
    assert.equal(parsed.fetchedAt, 1789976769);
    assert.equal(parsed.rows[0].model, "claude-opus-5");
    // Unusable output is null so each frontend can pick its own error string.
    assert.equal(FeatureTabs.parseRateTable(""), null);
    assert.equal(FeatureTabs.parseRateTable("   "), null);
    assert.equal(FeatureTabs.parseRateTable("not json"), null);
    assert.equal(FeatureTabs.parseRateTable("[1,2]").rows.length, 0);
    // A payload missing fields degrades to empty rather than throwing.
    const bare = FeatureTabs.parseRateTable("{}");
    assert.equal(bare.rows.length, 0);
    assert.equal(bare.total, 0);
    assert.equal(bare.error, "");
});

test("MiMo spend is labeled separately and counted once when its provider card is present", () => {
    const ledger = { actual: { costStatus: "exact", totalUSD: 0.75, providers: {
        "mimo::mimo": { costUSD: 0.25, costStatus: "exact", source: "mimo" },
        "openai::opencode": { costUSD: 0.50, costStatus: "exact", source: "opencode" }
    } } };
    const rows = FeatureTabs.localSpendRows(ledger);
    const mimo = rows.find(row => row.label === "MiMo Code");
    assert.equal(mimo.cost, 0.25);
    assert.match(mimo.note, /via MiMo Code/);
    const remaining = FeatureTabs.localSpendRows(ledger, [{ id: "mimo" }]);
    assert.equal(remaining.length, 1);
    assert.equal(remaining[0].label, "OpenAI");
});
