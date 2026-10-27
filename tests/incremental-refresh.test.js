const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function functions(file, root, extra = {}) {
    const source = fs.readFileSync(path.join(__dirname, "..", file), "utf8");
    for (const match of source.matchAll(/^    function (\w+)\(/gm)) {
        const end = source.indexOf("\n    }", match.index);
        root[match[1]] = vm.runInNewContext(`(${source.slice(match.index, end + 6)})`, { root, ...extra });
    }
    return root;
}

function state() {
    let history = 0;
    const root = functions("ui/AppState.qml", {
        providers: [{ id: "openai", value: "cached" }, { id: "cursor", value: "old" }],
        allProviders: [{ id: "openai" }, { id: "cursor" }, { id: "opencode" }],
        settingsLoaded: true, settings: { lastTab: "openai" },
        activeId: "openai", lastProviderId: "openai", localSpend: { retained: true },
        updatedAt: 90, lastFetched: 91, publishTray() {},
    }, { FeatureTabs: { enabledFeatureTabs: () => [] } });
    root.providerEnabled = (id) => id !== "opencode";
    root.providerById = (id) => root.providers.find((row) => row.id === id);
    root.publishTray = () => {};
    root.i18n = (text) => text;
    root.recordHistory = () => history++;
    return { root, history: () => history };
}

test("a completed provider updates immediately without clearing pending peers or recording stale history", () => {
    const { root, history } = state();
    root.applySnapshot(JSON.stringify({ partial: true, providers: [{ id: "cursor", value: "fresh" }], updatedAt: 100 }));
    assert.deepEqual(Array.from(root.providers, (row) => [row.id, row.value]), [["openai", "cached"], ["cursor", "fresh"]]);
    assert.equal(root.activeId, "openai");
    assert.deepEqual(root.localSpend, { retained: true });
    assert.equal(root.updatedAt, 90);
    assert.equal(root.lastFetched, 91);
    assert.equal(history(), 0);
    assert.equal(root.liveSnapshotSeen, true);
});

test("disabled provider updates cannot reappear and final completion records history once", () => {
    const { root, history } = state();
    root.applySnapshot(JSON.stringify({ partial: true, providers: [{ id: "opencode" }] }));
    assert.equal(root.providers.some((row) => row.id === "opencode"), false);
    root.applySnapshot(JSON.stringify({ providers: [{ id: "cursor", value: "fresh" }], updatedAt: 100, active: "cursor", localSpend: { final: true } }));
    assert.deepEqual(Array.from(root.providers, (row) => row.id), ["cursor"]);
    assert.equal(root.updatedAt, 100);
    assert.equal(history(), 1);
});

test("a late startup cache fills pending peers without overwriting an already fresh provider", () => {
    const { root, history } = state();
    root.providers = [];
    root.applySnapshot(JSON.stringify({ partial: true, providers: [{ id: "cursor", value: "fresh" }] }));
    root.applyLastSnapshot(JSON.stringify({ providers: [{ id: "openai", value: "cached" }, { id: "cursor", value: "stale" }], updatedAt: 80 }));
    assert.deepEqual(Array.from(root.providers, (row) => [row.id, row.value]), [["openai", "cached"], ["cursor", "fresh"]]);
    assert.equal(history(), 0);
    root.applySnapshot(JSON.stringify({ providers: [{ id: "cursor", value: "final" }], updatedAt: 100 }));
    root.applyLastSnapshot(JSON.stringify({ providers: [{ id: "openai", value: "obsolete" }] }));
    assert.deepEqual(Array.from(root.providers, (row) => [row.id, row.value]), [["cursor", "final"]]);
});

function commandBackend() {
    const jobs = [];
    const partials = [];
    const snapshots = [];
    const failures = [];
    const root = functions("ui/CommandBackend.qml", {
        busy: false, refreshPending: false, settingsSaveBlocked: false,
        settingsQueue: { busy: false }, configPath: "/fixture/settings.json", toolsDir: "/fixture/backend/sh", pythonPath: "",
        runner: { run: (argv, callback, onLine) => jobs.push({ argv, callback, onLine }) },
        providerReady: (text) => partials.push(JSON.parse(text)),
        snapshotReady: (text) => snapshots.push(JSON.parse(text)),
        refreshFailed: (text) => failures.push(text),
    });
    return { root, jobs, partials, snapshots, failures };
}

test("streamed providers are published before process completion and the final snapshot is applied once", () => {
    const { root, jobs, partials, snapshots } = commandBackend();
    root.refresh();
    const job = jobs[0];
    assert.ok(job.argv.includes("--stream"));
    job.onLine('{"partial":true,"providers":[{"id":"cursor"}]}');
    assert.equal(partials.length, 1);
    assert.equal(root.busy, true);
    assert.equal(snapshots.length, 0);
    const final = '{"providers":[{"id":"cursor"},{"id":"openai"}]}';
    job.onLine(final);
    assert.equal(snapshots.length, 0);
    job.callback('{"partial":true,"providers":[{"id":"cursor"}]}\n' + final + '\n', "", 0);
    assert.equal(partials.length, 1);
    assert.equal(snapshots.length, 1);
    assert.equal(root.busy, false);
});

test("EOF-only runners consume a streamed final snapshot without corrupting the JSON contract", () => {
    const { root, jobs, snapshots } = commandBackend();
    root.refresh();
    jobs[0].callback('{"partial":true,"providers":[{"id":"cursor"}]}\n{"providers":[{"id":"cursor"}]}\n', "", 0);
    assert.equal(snapshots.length, 1);
});

test("partial-only and failed processes do not claim a complete successful refresh", () => {
    for (const code of [0, 1]) {
        const { root, jobs, snapshots, failures } = commandBackend();
        root.refresh();
        jobs[0].callback('{"partial":true,"providers":[{"id":"cursor"}]}\n', "fixture failure", code);
        assert.equal(snapshots.length, 0);
        assert.equal(failures.length, 1);
        assert.equal(root.busy, false);
    }
});
