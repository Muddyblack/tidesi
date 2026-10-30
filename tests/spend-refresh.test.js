const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(process.env.TIDESI_TEST_APPSTATE || path.join(__dirname, "../ui/AppState.qml"), "utf8");

function functionBody(name) {
    const start = source.indexOf(`    function ${name}(`);
    const end = source.indexOf("\n    }", start);
    return source.slice(start, end + 6);
}

function fixture() {
    const calls = [];
    const root = {
        spendViewVisible: true, sessionsViewVisible: false, sessionsLoading: false,
        sessionsRequestId: 1, sessionsQuery: "existing search", sessionsOffset: 60,
        sessionsSourceIds: ["openai"], sessionsSourceSignature: "openai", sessionsActiveSourceSignature: "openai",
        sessionsRefreshStatus: "not-run", sessionsActiveRefresh: false,
        normalizeSessionSources: (values) => values || [], sessionSourceSelectionHasStaleIds: () => false,
        i18n: (text) => text, providerDefaultsReady: true, providerDefaultsInitializing: false,
        querySessions: (...args) => calls.push(["query", ...args]),
        reconcileSessions: (...args) => calls.push(["scan", ...args]),
    };
    const backend = { refresh: () => calls.push(["usage"]) };
    const context = {
        root, backend, sessionsReconcileTimer: { restart() {} },
        SessionRefreshPolicy: { cacheExpired: (age) => age == null || age >= 600 },
    };
    root.handleSessions = vm.runInNewContext(`(${functionBody("handleSessions")})`, context);
    root.refresh = vm.runInNewContext(`(${functionBody("refresh")})`, context);
    return { root, calls, context };
}

test("opening Usage & Spend queries local activity without changing the Sessions selection", () => {
    const { root, calls, context } = fixture();
    const start = source.indexOf("    onSpendViewVisibleChanged:");
    assert.notEqual(start, -1);
    const end = source.indexOf("\n    }", start);
    const code = source.slice(start, end + 6).replace(/^\s*onSpendViewVisibleChanged:\s*/, "");
    vm.runInNewContext(code, context);
    assert.deepEqual(calls, [["query", "existing search", 60, false, ["openai"]]]);
    assert.deepEqual(root.sessionsSourceIds, ["openai"]);
});

test("a missing local cost cache is reconciled even when the Sessions tab is hidden", () => {
    const { root, calls } = fixture();
    root.handleSessions(JSON.stringify({ cacheStatus: "no-cache", cacheAgeSeconds: null, sessions: [], sources: [] }), root.sessionsQuery, 1);
    assert.deepEqual(calls, [["scan", "existing search", ["openai"]]]);
});

test("a newly created but unpopulated index is scanned even if its file age is zero", () => {
    const { root, calls } = fixture();
    root.handleSessions(JSON.stringify({ cacheStatus: "no-cache", cacheAgeSeconds: 0, sessions: [], sources: [] }), root.sessionsQuery, 1);
    assert.deepEqual(calls, [["scan", "existing search", ["openai"]]]);
});

test("completed local activity scan refreshes the cost envelope without triggering another scan", () => {
    const { root, calls } = fixture();
    root.sessionsActiveRefresh = true;
    root.handleSessions(JSON.stringify({ cacheStatus: "ready", cacheAgeSeconds: 0, refreshStatus: "refreshed", sessions: [], sources: [] }), root.sessionsQuery, 1);
    assert.deepEqual(calls, [["usage"]]);
});

test("manual refresh in Usage & Spend refreshes both local activity and provider data", () => {
    const { root, calls } = fixture();
    root.refresh(true);
    assert.deepEqual(calls, [["scan", "existing search", ["openai"]], ["usage"]]);
});

test("ordinary provider polls do not rescan fresh or failed session indexes", () => {
    for (const status of ["refreshed", "failed"]) {
        const { root, calls } = fixture();
        root.sessionsRefreshStatus = status;
        root.sessionsCacheAgeSeconds = 0;
        root.refresh();
        assert.deepEqual(calls, [["usage"]]);
    }
});

test("failed activity scans do not repeatedly rescan or discard prior costs", () => {
    const { root, calls } = fixture();
    root.localSpend = { actual: { costStatus: "exact", totalUSD: 5 } };
    root.sessionsActiveRefresh = true;
    root.handleSessions(JSON.stringify({ error: "fixture scan failure" }), root.sessionsQuery, 1);
    assert.deepEqual(calls, []);
    assert.equal(root.localSpend.actual.totalUSD, 5);
    assert.equal(root.sessionsRefreshStatus, "failed");
});

test("the reconcile timer retries a failed scan later without overlapping an active request", () => {
    const { root, calls, context } = fixture();
    root.sessionsRefreshStatus = "failed";
    const timer = source.indexOf("        id: sessionsReconcileTimer");
    const start = source.indexOf("        onTriggered:", timer);
    const end = source.indexOf("\n        }", start);
    const code = source.slice(start, end + 10).replace(/^\s*onTriggered:\s*/, "");
    vm.runInNewContext(code, context);
    assert.deepEqual(calls, [["scan", "existing search", ["openai"]]]);
    calls.length = 0;
    root.sessionsLoading = true;
    vm.runInNewContext(code, context);
    assert.deepEqual(calls, []);
    assert.match(source.slice(timer, start), /running:\s*root\.sessionsViewVisible\s*\|\|\s*root\.spendViewVisible/);
});
