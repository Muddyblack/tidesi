const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const policy = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../ui/js/SessionRefreshPolicy.js"), "utf8").replace(/^\.pragma library\s*/, ""), policy);

test("resumable incomplete scans retry after the cache cooldown while failed scans retain backoff", () => {
    assert.equal(policy.refreshDelayMs(0, "incomplete"), 60000);
    assert.equal(policy.refreshDelayMs(0, "failed"), 600000);
    assert.equal(policy.incompleteRetryDue(1000, 60999), false);
    assert.equal(policy.incompleteRetryDue(1000, 61000), true);
    assert.equal(policy.incompleteRetryDue(0, 1000), true);
});

function state() {
    const calls = [];
    const root = {
        sessionsRequestId: 1, sessionsQuery: "", sessionsActiveSourceSignature: "", sessionsSourceSignature: "",
        sessionsActiveRefresh: false, sessionsLoading: false, sessionsSourceIds: [],
        spendViewVisible: true, sessionsViewVisible: false,
        normalizeSessionSources: (rows) => rows || [], sessionSourceSelectionHasStaleIds: () => false,
        reconcileSessions() { calls.push("scan"); this.sessionsLoading = true; }, i18n: (text) => text,
        refresh: () => calls.push("usage"),
        sessionsLastReconcile: 0,
    };
    const source = fs.readFileSync(path.join(__dirname, "../ui/AppState.qml"), "utf8");
    const start = source.indexOf("    function handleSessions(");
    const end = source.indexOf("\n    }", start);
    root.handleSessions = vm.runInNewContext(`(${source.slice(start, end + 6)})`, {
        root, SessionRefreshPolicy: policy, sessionsReconcileTimer: { restart() {} },
        backend: { refresh: () => calls.push("usage") },
    });
    return { root, calls };
}

function answer(root, status) {
    root.handleSessions(JSON.stringify({ sessions: [], cacheStatus: "ready", cacheAgeSeconds: 0, refreshStatus: status }), "", 1);
}

test("returning to Usage & Spend resumes an overdue incomplete scan even when the cache file is fresh", () => {
    const { root, calls } = state();
    root.sessionsLastReconcile = Date.now() - 61000;
    answer(root, "incomplete");
    assert.deepEqual(calls, ["scan"]);
});

test("a just-finished incomplete scan does not loop and a cached query before cooldown does not rescan", () => {
    for (const active of [true, false]) {
        const { root, calls } = state();
        root.sessionsActiveRefresh = active;
        root.sessionsLastReconcile = Date.now();
        answer(root, "incomplete");
        assert.deepEqual(calls, ["usage"]);
    }
});

test("a real failure is not converted into repeated immediate scans", () => {
    const { root, calls } = state();
    answer(root, "failed");
    assert.deepEqual(calls, ["usage"]);
});
