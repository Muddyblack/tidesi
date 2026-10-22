const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { spawnSync } = require("node:child_process");
const UsageHistory = require("../ui/js/UsageHistory.js");
const repo = path.resolve(__dirname, "..");

function functions(file, root, extra = {}) {
    const source = fs.readFileSync(path.join(repo, file), "utf8");
    for (const match of source.matchAll(/^    function (\w+)\(/gm)) {
        const end = source.indexOf("\n    }", match.index);
        root[match[1]] = vm.runInNewContext(`(${source.slice(match.index, end + 6)})`, { root, ...extra });
    }
    return root;
}

function fixture(t) {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "ai-usage-order-"));
    t.after(() => fs.rmSync(home, { recursive: true, force: true }));
    const jobs = [];
    const root = functions("ui/CommandBackend.qml", {
        configPath: path.join(home, "settings.json"),
        toolsDir: path.join(repo, "backend/sh"),
        pythonPath: "",
        busy: false,
        refreshPending: false,
        settingsSaveBlocked: false,
        runner: { run: (argv, callback) => jobs.push({ argv, callback }) },
        historyFinished: () => {},
        snapshotReady: () => {},
        refreshFailed: (error) => assert.fail(error),
        settingsSaved: () => {},
        settingsSaveFailed: () => {},
    });
    if (fs.existsSync(path.join(repo, "ui/ProcessQueue.qml"))) {
        const queue = (idle = () => {}) => {
            const state = functions("ui/ProcessQueue.qml", {
                pending: [], current: null, runner: root.runner, idle,
            });
            Object.defineProperty(state, "busy", { get: () => state.current !== null || state.pending.length > 0 });
            return state;
        };
        root.settingsQueue = queue(() => { if (root.refreshPending) root.refresh(); });
        root.historyQueue = queue();
    }
    const env = {
        PATH: process.env.PATH, HOME: home,
        XDG_CONFIG_HOME: path.join(home, "config"),
        XDG_DATA_HOME: path.join(home, "data"),
        XDG_CACHE_HOME: path.join(home, "cache"),
    };
    const drain = () => {
        while (jobs.length) {
            const job = jobs.pop();
            const result = spawnSync("sh", ["-c", 'umask 022; exec "$@"', "test", ...job.argv], { env, encoding: "utf8", timeout: 30000 });
            if (result.error) throw result.error;
            job.callback(result.stdout || "", result.stderr || "", result.status ?? -1);
        }
    };
    return { root, jobs, home, drain, env };
}

test("later settings edits survive an adversarial process completion order", (t) => {
    const { root, drain } = fixture(t);
    root.saveSettings('{"providers":{"cursor":true}}');
    root.saveSettings('{"providers":{"cursor":false,"ollama":true}}');
    drain();
    assert.deepEqual(JSON.parse(fs.readFileSync(root.configPath, "utf8")), { providers: { cursor: false, ollama: true } });
});

test("settings containing keys are owner-only even with a permissive inherited umask", (t) => {
    const { root, drain } = fixture(t);
    fs.writeFileSync(root.configPath, "{}", { mode: 0o644 });
    root.saveSettings('{"keys":{"test":"fixture-not-a-real-key"}}');
    drain();
    assert.equal(fs.statSync(root.configPath).mode & 0o777, 0o600);
});

test("failed saves notify without leaking stderr, hold refresh, and recover on a later save", (t) => {
    const { root, jobs, home, drain, env } = fixture(t);
    const bin = path.join(home, "bin");
    fs.mkdirSync(bin);
    const mv = path.join(bin, "mv");
    fs.writeFileSync(mv, "#!/bin/sh\nprintf 'fixture-secret' >&2\nexit 1\n", { mode: 0o700 });
    env.PATH = bin + path.delimiter + env.PATH;
    fs.writeFileSync(root.configPath, "{}");
    const failures = [];
    let saved = 0;
    let refreshed = 0;
    root.settingsSaveFailed = (...args) => failures.push(args);
    root.settingsSaved = () => saved++;
    root.snapshotReady = () => refreshed++;
    const payload = '{"providerDefaultsApplied":true,"providers":{},"keys":{"fixture":"fixture-secret"}}';
    root.saveSettings(payload);
    root.refresh();
    drain();
    assert.equal(failures.length, 1);
    assert.equal(JSON.stringify(failures).includes("fixture-secret"), false);
    assert.equal(root.settingsSaveBlocked, true);
    assert.equal(refreshed, 0);
    assert.equal(fs.readFileSync(root.configPath, "utf8"), "{}");
    assert.equal(fs.readdirSync(home).some((name) => name.includes(".tmp.")), false);
    fs.unlinkSync(mv);
    root.saveSettings(payload);
    drain();
    assert.equal(saved, 1);
    assert.equal(root.settingsSaveBlocked, false);
    assert.equal(refreshed, 1);
    assert.equal(jobs.length, 0);
});

test("save failure status is separate from refresh status and clears only after save success", () => {
    const source = fs.readFileSync(path.join(repo, "ui/AppState.qml"), "utf8");
    const root = { settingsSaveError: "", errorText: "unrelated refresh error", i18n: (text) => text };
    const handler = (name) => {
        const start = source.indexOf(`        function ${name}(`);
        assert.notEqual(start, -1);
        const end = source.indexOf("\n        }", start);
        return vm.runInNewContext(`(${source.slice(start, end + 10)})`, { root });
    };
    handler("onSettingsSaveFailed")();
    assert.ok(root.settingsSaveError.length > 0);
    assert.equal(root.errorText, "unrelated refresh error");
    handler("onSettingsSaved")();
    assert.equal(root.settingsSaveError, "");
});

test("a throwing completion callback cannot strand later queued work", () => {
    const jobs = [];
    const queue = functions("ui/ProcessQueue.qml", {
        current: null, pending: [], idle() {},
        runner: { run: (_, callback) => jobs.push(callback) },
    });
    let completed = false;
    queue.run(["first"], () => { throw new Error("callback failed"); });
    queue.run(["second"], () => { completed = true; });
    assert.throws(() => jobs.shift()("", "", 0), /callback failed/);
    assert.equal(jobs.length, 1);
    jobs.shift()("", "", 0);
    assert.equal(completed, true);
});

test("refresh waits for pending settings writes before reading provider selection", (t) => {
    const { root, jobs, drain } = fixture(t);
    root.saveSettings('{"providerDefaultsApplied":true,"providers":{"ollama":true}}');
    root.refresh();
    assert.equal(jobs.length, 1);
    let providers;
    root.snapshotReady = (text) => { providers = JSON.parse(text).providers.map((provider) => provider.id); };
    drain();
    assert.deepEqual(providers, ["ollama"]);
});

for (const op of ["clear", "prune"]) {
    test(`pending autosave cannot undo a later history ${op}`, (t) => {
        const { root, drain } = fixture(t);
        root.history("autosave", JSON.stringify([{ t: 1, w: 40 }, { t: Date.now(), w: 60 }]));
        root.history(op, op === "prune" ? "1" : "");
        drain();
        let data;
        root.historyFinished = (_, text) => { data = JSON.parse(text).data || []; };
        root.history("autoload", "");
        drain();
        assert.equal(data.some((point) => point.t === 1), false);
        assert.equal(data.length, op === "clear" ? 0 : 1);
    });
}

test("exit flush starts its write rather than leaving it in a dying QML queue", (t) => {
    const { root, jobs, drain } = fixture(t);
    root.history("autosave", '[{"t":1,"w":40}]');
    root.flushHistory("autosave", '[{"t":2,"w":60}]');
    assert.equal(jobs.length, 2);
    drain();
});

test("history mutation holds new autosaves until the replacement store is installed", () => {
    const calls = [];
    const timer = { stop() {}, restart() {} };
    const root = functions("ui/AppState.qml", {
        historySaving: false, historyChangesPending: 0,
        historyStore: UsageHistory.newStore(100),
        historyDebounceTimer: { stop() {} },
        historySaveTimeout: { restart() {} },
    }, { UsageHistory, historySaveTimeout: timer, historyDebounceTimer: timer, backend: { history: (...args) => calls.push(args) } });
    UsageHistory.opened(root.historyStore);
    UsageHistory.record(root.historyStore, { w: 40 }, Date.now());
    root.clearHistory();
    root.sendHistory();
    assert.deepEqual(calls.map(([op]) => op), ["clear"]);
});

for (const op of ["clear", "prune"]) {
    for (const failed of [false, true]) {
        test(`${op} completion releases the hold and ${failed ? "preserves failed" : "replaces successful"} history`, () => {
            const timer = { stop() {}, restart() {} };
            const root = functions("ui/AppState.qml", {
                historyChangesPending: 1, historyLimit: 100,
                historyStore: UsageHistory.newStore(100),
            }, { UsageHistory });
            root.i18n = (text) => text;
            root.i18np = (one) => one;
            UsageHistory.opened(root.historyStore);
            UsageHistory.record(root.historyStore, { w: 40 }, 1);
            const original = root.historyStore;
            const source = fs.readFileSync(path.join(repo, "ui/AppState.qml"), "utf8");
            const start = source.indexOf("        function onHistoryFinished(");
            const end = source.indexOf("\n        }", start);
            const finish = vm.runInNewContext(`(${source.slice(start, end + 10)})`, { root, UsageHistory, historyMsgTimer: timer });
            finish(op, JSON.stringify(failed ? { error: "fixture failure" } : { data: [], removed: 1 }));
            assert.equal(root.historyChangesPending, 0);
            if (failed) {
                assert.equal(root.historyStore, original);
                assert.equal(root.historyMsg, "fixture failure");
            } else {
                assert.notEqual(root.historyStore, original);
                assert.equal(root.historyStore.history.length, 0);
            }
        });
    }
}
