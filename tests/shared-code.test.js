const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Format = require("../ui/js/Format.js");
const PanelRotation = require("../ui/js/PanelRotation.js");
const UsageHistory = require("../ui/js/UsageHistory.js");
const Shell = require("../ui/js/Shell.js");
const SessionSources = require("../ui/js/SessionSources.js");
const RefreshCoalescer = require("../ui/js/RefreshCoalescer.js");
const ProjectInfoRequests = require("../ui/js/ProjectInfoRequests.js");
const PanelColor = require("../ui/js/PanelColor.js");
const { execFileSync } = require("node:child_process");

test("panel rotation is opt-in and only runs with multiple pins", () => {
    const schema = fs.readFileSync(path.join(__dirname, "..", "hosts/kde/contents/config/main.xml"), "utf8");
    assert.match(schema, /entry name="panelRotationIntervalSec" type="Int">[\s\S]*?<default>0<\/default>/);
    assert.equal(PanelRotation.normalizeIntervalSec(1), 0);
    assert.equal(PanelRotation.normalizeIntervalSec(600), 600);
    assert.equal(PanelRotation.isEnabled(0, ["claude", "openai"]), false);
    assert.equal(PanelRotation.isEnabled(1, ["claude", "openai"]), false);
    assert.equal(PanelRotation.isEnabled(30, ["claude"]), false);
    assert.equal(PanelRotation.isEnabled(30, ["claude", "openai"]), true);
});

test("normalizes session source descriptors without exposing malformed entries", () => {
    assert.deepEqual(SessionSources.normalizeDescriptors([
        { id: " codex ", label: " Codex " },
        { id: "codex", label: "duplicate" },
        { id: "", label: "empty id" },
        { id: "muse", label: " " },
        { id: 7, label: "not a descriptor" },
        null
    ]), [{ id: "codex", label: "Codex" }]);
});

test("normalizes source selection with All as the empty canonical selection", () => {
    const sources = [{ id: "codex", label: "Codex" }, { id: "claude", label: "Claude" }];
    assert.deepEqual(SessionSources.normalizeIds([" claude ", "unknown"], sources), ["claude"]);
    assert.deepEqual(SessionSources.normalizeIds(["codex", "claude"], sources), []);
    assert.equal(SessionSources.signature(["codex", "claude"]), "codex\u001fclaude");
    assert.equal(SessionSources.hasStaleIds(["removed"], sources), true);
    assert.equal(SessionSources.hasStaleIds(["codex"], sources), false);
});

test("toggles sources while preserving All, single, and multiple semantics", () => {
    const sources = [{ id: "codex", label: "Codex" }, { id: "claude", label: "Claude" }, { id: "muse", label: "Muse" }];
    assert.deepEqual(SessionSources.toggled([], "codex", false, sources), ["claude", "muse"]);
    assert.deepEqual(SessionSources.toggled(["claude"], "codex", true, sources), ["codex", "claude"]);
    assert.deepEqual(SessionSources.toggled(["codex", "claude"], "claude", false, sources), ["codex"]);
    assert.deepEqual(SessionSources.toggled(["codex", "claude"], "muse", true, sources), []);
    assert.deepEqual(SessionSources.toggled(["codex"], "codex", false, [sources[0]]), []);
});

test("panel rotation preserves valid selection and wraps in pin order", () => {
    const pins = ["openai", "claude", "muse"];
    assert.equal(PanelRotation.normalizeSelection(pins, "claude"), "claude");
    assert.equal(PanelRotation.normalizeSelection(["claude", "muse"], "openai"), "claude");
    assert.equal(PanelRotation.nextSelection(["claude", "muse"], "removed"), "muse");
    assert.equal(PanelRotation.nextSelection(pins, "openai"), "claude");
    assert.equal(PanelRotation.nextSelection(pins, "muse"), "openai");
});

test("panel rotation stops changing selection for zero or one pin", () => {
    assert.equal(PanelRotation.normalizeSelection([], "claude"), "");
    assert.equal(PanelRotation.nextSelection([], "claude"), "");
    assert.equal(PanelRotation.nextSelection(["claude"], "claude"), "claude");
    assert.equal(PanelRotation.nextSelection(["claude"], "removed"), "claude");
});

test("concurrent history writers keep both provider keys at one timestamp", () => {
    const mirror = [{ t: 1785000000000, w: 40 }];
    const plasmaBatch = [{ t: 1785000000000, w: 41 }];
    const quickBatch = [{ t: 1785000000000, cp: 9 }];
    assert.deepEqual(UsageHistory.union(mirror, plasmaBatch, 500), [{ t: 1785000000000, w: 41 }]);
    assert.deepEqual(UsageHistory.union(mirror, quickBatch, 500), [{ t: 1785000000000, w: 40, cp: 9 }]);
    assert.deepEqual(UsageHistory.union(plasmaBatch, quickBatch, 500), [{ t: 1785000000000, w: 41, cp: 9 }]);
});

test("formats a countdown down to the minute", () => {
    const now = 1785000000000;
    assert.equal(Format.countdown(now + 90 * 60000, now), "1h 30m");
    assert.equal(Format.countdown(now + (2 * 1440 + 65) * 60000, now), "2d 1h 5m");
    assert.equal(Format.countdown(now + 45 * 1000, now), "0m");
});

test("reports a passed deadline and a missing one", () => {
    const now = 1785000000000;
    assert.equal(Format.countdown(now - 1000, now), "resetting...");
    assert.equal(Format.countdown(0, now), "");
    assert.equal(Format.countdown(null, now), "");
});

test("converts the contract's epoch seconds", () => {
    const now = 1785000000000;
    assert.equal(Format.countdownFromEpoch(1785003600, now), "1h 0m");
    assert.equal(Format.countdownFromEpoch(0, now), "");
});

test("collects every provider's history values into one patch", () => {
    assert.deepEqual(UsageHistory.collect([
        { id: "claude", historyValues: { s: 12, w: 34 } },
        { id: "kiro", historyValues: { kr: 56 } },
        { id: "grok", historyValues: {} },
        { id: "broken" }
    ]), { s: 12, w: 34, kr: 56 });
});

test("patches its own recent point instead of appending", () => {
    const now = 1785000000000;
    const store = UsageHistory.newStore(500);
    UsageHistory.record(store, { s: 10 }, now - 30000);
    UsageHistory.record(store, { s: 20, w: 5 }, now);
    assert.deepEqual(store.points, [{ t: now - 30000, s: 20, w: 5 }]);
    // ...while the chart also gets the reading at `now`, where it was taken.
    assert.deepEqual(store.history, [{ t: now - 30000, s: 20, w: 5 }, { t: now, s: 20, w: 5 }]);
});

test("leaves another writer's point alone however recent it is", () => {
    // adopt(), not record(): that point came off the shared file, so this
    // frontend is not its writer. Patching it would leave no way to tell which
    // of the two values is the newer reading.
    const now = 1785000000000;
    const store = UsageHistory.newStore(500);
    UsageHistory.adopt(store, [{ t: now - 30000, cp: 9 }]);
    UsageHistory.record(store, { s: 20 }, now);
    assert.deepEqual(store.history, [{ t: now - 30000, cp: 9 }, { t: now, s: 20 }]);
});

test("queues just the keys the sample carried", () => {
    // That, and not the series, is what a save sends: the rest of the series is
    // this frontend's copies of the other one's points.
    const now = 1785000000000;
    const store = UsageHistory.newStore(500);
    UsageHistory.adopt(store, [{ t: now - 30000, s: 10, w: 40 }]);
    UsageHistory.record(store, { s: 20 }, now - 30000 + 1);
    assert.deepEqual(store.fresh, [{ t: now - 30000 + 1, s: 20 }]);
    assert.deepEqual(store.history, [{ t: now - 30000, s: 10, w: 40 }, { t: now - 30000 + 1, s: 20 }]);
});

test("appends once the merge window has passed", () => {
    const now = 1785000000000;
    const store = UsageHistory.newStore(500);
    UsageHistory.record(store, { s: 10 }, now - UsageHistory.MERGE_WINDOW_MS - 1);
    UsageHistory.record(store, { s: 20 }, now);
    assert.equal(store.history.length, 2);
    assert.deepEqual(store.history[1], { t: now, s: 20 });
});

test("records nothing when a provider reports nothing", () => {
    const store = UsageHistory.newStore(500);
    UsageHistory.record(store, { s: 10 }, 1000);
    const before = store.history;
    assert.equal(UsageHistory.record(store, {}, 2000), false);
    // Same array back, which is how a frontend knows there is nothing to repaint.
    assert.equal(store.history, before);
});

test("trims to the history limit", () => {
    const store = UsageHistory.newStore(5);
    for (let i = 0; i < 12; i++)
        UsageHistory.record(store, { s: i }, i * 1000000);
    assert.equal(store.history.length, 5);
    assert.equal(store.history[store.history.length - 1].s, 11);
});

test("unions the mirror file with points recorded since startup", () => {
    // Same timestamp, different series: the two frontends each saw one provider.
    const file = [{ t: 1000, w: 40 }, { t: 2000, w: 50 }];
    const live = [{ t: 2000, cp: 7 }, { t: 3000, w: 60 }];
    assert.deepEqual(UsageHistory.union(file, live, 500), [
        { t: 1000, w: 40 },
        { t: 2000, w: 50, cp: 7 },
        { t: 3000, w: 60 }
    ]);
});

test("lets the overlay win on a shared key and drops junk points", () => {
    const out = UsageHistory.union([{ t: 1, w: 1 }, null, { w: 9 }], [{ t: 1, w: 2 }], 500);
    assert.deepEqual(out, [{ t: 1, w: 2 }]);
});

test("trims the union to the history limit, keeping the newest", () => {
    const file = [{ t: 1, w: 1 }, { t: 2, w: 2 }, { t: 3, w: 3 }];
    assert.deepEqual(UsageHistory.union(file, [{ t: 4, w: 4 }], 2), [{ t: 3, w: 3 }, { t: 4, w: 4 }]);
});

test("migrates legacy weekly-only points and drops junk", () => {
    assert.deepEqual(UsageHistory.normalize([
        { t: 1, v: 40 },
        { t: 2, w: 50 },
        { v: 60 },
        null
    ], 500), [{ t: 1, w: 40 }, { t: 2, w: 50 }]);
});

test("keeps a failed provider's null out of the series", () => {
    // historyValues comes back null when a provider errored; recording it would
    // blank the last good sample instead of leaving a gap in the chart.
    const now = 1785000000000;
    const store = UsageHistory.newStore(500);
    UsageHistory.record(store, { s: 10, w: 40 }, now - 30000);
    assert.equal(UsageHistory.record(store, { s: null, w: undefined }, now), false);
    UsageHistory.record(store, { s: null, w: 41 }, now);
    assert.deepEqual(store.points, [{ t: now - 30000, s: 10, w: 41 }]);
    assert.deepEqual(store.fresh, [{ t: now - 30000, s: 10, w: 41 }]);
});

test("never patches a point in place", () => {
    // slice() is shallow — the array the frontend still holds must not change
    // under it, because QML never signals a change made that way and the file
    // has not seen it either.
    const now = 1785000000000;
    const store = UsageHistory.newStore(500);
    UsageHistory.record(store, { s: 10 }, now - 30000);
    const before = store.history;
    UsageHistory.record(store, { s: 20 }, now);
    assert.deepEqual(before, [{ t: now - 30000, s: 10 }]);
    assert.deepEqual(store.points, [{ t: now - 30000, s: 20 }]);
});

test("combines repeated timestamps inside one side of the union", () => {
    // Two half-filled points at the same instant — one frontend's mirror write
    // raced its own poll — must not cost each other their series.
    assert.deepEqual(UsageHistory.union([{ t: 1, w: 1 }, { t: 1, cp: 2 }], [], 500), [{ t: 1, w: 1, cp: 2 }]);
});

test("drops points the chart cannot place on a time axis", () => {
    // The mirror file is written by the other frontend and can be hand-edited;
    // a null or non-numeric t sorts as NaN and drags the whole range with it.
    assert.deepEqual(UsageHistory.normalize([{ t: null, w: 1 }, { t: "abc", w: 2 }, { t: NaN, w: 3 }, { t: 5, w: 4 }], 500), [{ t: 5, w: 4 }]);
    assert.deepEqual(UsageHistory.union([{ t: null, w: 1 }], [{ t: 5, w: 4 }], 500), [{ t: 5, w: 4 }]);
});

test("reads a numeric timestamp back out of a string", () => {
    assert.deepEqual(UsageHistory.normalize([{ t: "1700000000000", w: 7 }], 500), [{ t: 1700000000000, w: 7 }]);
    // ...and the string and number forms are then the same point, not two.
    assert.deepEqual(UsageHistory.union([{ t: "1700000000000", w: 7 }], [{ t: 1700000000000, cp: 8 }], 500), [{ t: 1700000000000, w: 7, cp: 8 }]);
});

test("normalize copies, so a restored file cannot alias the live series", () => {
    const file = [{ t: 1, w: 1 }];
    const norm = UsageHistory.normalize(file, 500);
    norm[0].w = 99;
    assert.deepEqual(file, [{ t: 1, w: 1 }]);
});

test("normalize drops the legacy value key once it has been migrated", () => {
    assert.deepEqual(UsageHistory.normalize([{ t: 1, v: 40 }], 500), [{ t: 1, w: 40 }]);
    // An existing w wins; v is not carried forward either way.
    assert.deepEqual(UsageHistory.normalize([{ t: 1, v: 40, w: 50 }], 500), [{ t: 1, w: 50 }]);
});

// The union runs in two places: in QML when a frontend restores at startup, and
// in Python when history-io merges a save into the file on disk. They have to
// agree exactly, or the series would change shape depending on which one last
// touched it. Replay the same cases through both and compare the JSON.
test("the QML and Python unions produce identical output", () => {
    const cases = [
        [[{ t: 1000, w: 40 }, { t: 2000, w: 50 }], [{ t: 2000, cp: 7 }, { t: 3000, w: 60 }]],
        [[{ t: 1, w: 1 }, { t: 1, cp: 2 }], [{ t: 1, w: 3 }]],
        [[{ t: 1, w: 1 }, null, { w: 9 }], [{ t: 1, w: 2 }]],
        [[{ t: null, w: 1 }, { t: "abc", w: 2 }, { t: "1700000000000", w: 3 }], [{ t: 1700000000000, cp: 4 }]],
        [[{ t: 5, w: null, s: 0 }], [{ t: 5, w: 7, cp: null }]],
        [[], []],
        [[{ t: 3, w: 3 }, { t: 1, w: 1 }, { t: 2, w: 2 }], [{ t: 4, w: 4 }]],
        [[{ t: 1.5, w: 1 }, { t: -2, w: 2 }], [{ t: 0, w: 0 }]],
        // Number() reads "0x10" and "Infinity"; float() reads "1_0" and "nan".
        // Neither set is the other's, so both sides take plain decimals only.
        [[{ t: "0x10", w: 1 }, { t: "1_0", w: 2 }, { t: "Infinity", w: 3 }, { t: "nan", w: 4 }, { t: "", w: 5 }], [{ t: " 12 ", w: 6 }, { t: "1e3", w: 7 }]]
    ];
    const limits = [500, 2];
    const script = "import json,sys; sys.path.insert(0, sys.argv[1]); from tidesi import history;" +
        " c = json.load(sys.stdin);" +
        " print(json.dumps([history.union(b, o, l) for b, o, l in c], separators=(',', ':')))";
    const payload = [];
    for (const limit of limits)
        for (const [base, overlay] of cases)
            payload.push([base, overlay, limit]);

    const py = execFileSync("python3", ["-B", "-c", script, "backend"], {
        input: JSON.stringify(payload),
        encoding: "utf8",
        cwd: __dirname + "/.."
    });
    const fromJs = payload.map(([base, overlay, limit]) => UsageHistory.union(base, overlay, limit));
    assert.equal(py.trim(), JSON.stringify(fromJs));
});

test("the QML and Python normalize produce identical output", () => {
    const cases = [
        [{ t: 1, v: 40 }, { t: 2, w: 50 }, { v: 60 }, null],
        [{ t: "1700000000000", w: 7 }, { t: null }, { t: NaN, w: 1 }],
        [{ t: 1, v: 40, w: 50 }, { t: 2, w: null, s: 3 }],
        [{ t: "0x10", w: 1 }, { t: "1_0", w: 2 }, { t: "Infinity", w: 3 }, { t: " 12 ", w: 4 }, { t: "+1e3", w: 5 }],
        []
    ];
    const script = "import json,sys; sys.path.insert(0, sys.argv[1]); from tidesi import history;" +
        " c = json.load(sys.stdin);" +
        " print(json.dumps([history.normalize(p, 500) for p in c], separators=(',', ':')))";
    // JSON has no NaN, and neither store can hold one; drop it the way a file
    // round trip would before handing the cases to Python.
    const py = execFileSync("python3", ["-B", "-c", script, "backend"], {
        input: JSON.stringify(cases).replace(/NaN/g, "null"),
        encoding: "utf8",
        cwd: __dirname + "/.."
    });
    const fromJs = cases.map(points => UsageHistory.normalize(points, 500));
    assert.equal(py.trim(), JSON.stringify(fromJs));
});

// ── The save protocol ───────────────────────────────────────────────────────
// Both frontends run this state machine — neither has a copy of the rules — so
// these cover the shipped code rather than a model of it.

test("a reading is asserted, a restored series is offered", () => {
    const store = UsageHistory.newStore(500);
    UsageHistory.opened(store);
    UsageHistory.restore(store, [{ t: 1, w: 9 }]);
    UsageHistory.record(store, { w: 20 }, 5000);

    // The seed goes first and goes as a seed — a fresh install has to get its
    // restored series on disk — and the reading follows behind it.
    const first = UsageHistory.take(store);
    assert.equal(first.op, "seed");
    assert.deepEqual(first.points, [{ t: 1, w: 9 }]);
    assert.equal(UsageHistory.take(store), null, "two batches must never be out at once");

    UsageHistory.done(store, [{ t: 1, w: 9 }]);
    const second = UsageHistory.take(store);
    assert.equal(second.op, "autosave");
    assert.deepEqual(second.points, [{ t: 5000, w: 20 }]);
});

test("a reading that changed nothing is not worth a save", () => {
    // At a poll faster than the merge window the same value lands again and
    // again; each repeat would otherwise spawn history-io to write a point the
    // file already has.
    const store = UsageHistory.newStore(500);
    UsageHistory.opened(store);
    UsageHistory.record(store, { w: 40 }, 1000);
    assert.deepEqual(UsageHistory.take(store).points, [{ t: 1000, w: 40 }]);
    UsageHistory.done(store, [{ t: 1000, w: 40 }]);

    UsageHistory.record(store, { w: 40 }, 31000);
    assert.equal(UsageHistory.take(store), null, "a repeat must not queue a save");

    // A change to the same point still does.
    UsageHistory.record(store, { w: 41 }, 61000);
    assert.deepEqual(UsageHistory.take(store).points, [{ t: 1000, w: 41 }]);
});

test("a failed save is not retried until the next poll", () => {
    // The Quickshell panel asks for the next batch on every process exit. Handing
    // this one straight back would be a loop of failing saves as fast as the
    // shell can fork.
    const store = UsageHistory.newStore(500);
    UsageHistory.opened(store);
    UsageHistory.record(store, { w: 10 }, 1000);
    assert.deepEqual(UsageHistory.take(store).points, [{ t: 1000, w: 10 }]);

    UsageHistory.failed(store);
    assert.deepEqual(store.fresh, [{ t: 1000, w: 10 }], "the batch is kept, not dropped");
    assert.equal(UsageHistory.take(store), null);
    assert.equal(UsageHistory.take(store), null, "however often it is asked");

    UsageHistory.record(store, {}, 200000);
    assert.deepEqual(UsageHistory.take(store).points, [{ t: 1000, w: 10 }]);
});

test("a failed batch goes back to the lane it came from", () => {
    const store = UsageHistory.newStore(500);
    UsageHistory.opened(store);
    UsageHistory.restore(store, [{ t: 1, w: 9 }]);
    UsageHistory.take(store);
    UsageHistory.failed(store);
    assert.deepEqual(store.seed, [{ t: 1, w: 9 }], "a seed must not come back as a reading");
    assert.deepEqual(store.fresh, []);

    // The Quickshell watchdog kills the process and unwinds, and the collector
    // may then report the kill as an empty answer and unwind again. The second
    // one has to be a no-op, not a second copy of the batch.
    UsageHistory.failed(store);
    assert.deepEqual(store.seed, [{ t: 1, w: 9 }]);
});

test("nothing is written before the startup read has answered", () => {
    const store = UsageHistory.newStore(500);
    UsageHistory.record(store, { w: 10 }, 1000);
    assert.equal(UsageHistory.take(store), null);
    UsageHistory.opened(store);
    assert.deepEqual(UsageHistory.take(store).points, [{ t: 1000, w: 10 }]);
});

test("adopting keeps what only exists here and lets fresh readings win", () => {
    const store = UsageHistory.newStore(500);
    UsageHistory.opened(store);
    UsageHistory.restore(store, [{ t: 1, w: 1 }]);
    UsageHistory.record(store, { w: 50 }, 3000);
    UsageHistory.adopt(store, [{ t: 2, cp: 7 }, { t: 3000, w: 12 }]);
    // t=1 survives though the file has never had it, the file's t=2 arrives, and
    // the reading at t=3000 is this frontend's own and is not rolled back.
    assert.deepEqual(store.history, [{ t: 1, w: 1 }, { t: 2, cp: 7 }, { t: 3000, w: 50 }]);
});

// ── Only changes are stored ────────────────────────────────────────────────
// Most readings repeat the one before — 83% of a real history did — so a flat run
// is stored as its first and last sighting plus one an hour. The chart joins
// samples with curves, so dropping repeats is only safe if it draws the same line.

const MIN = 60000;

// A day of five-minute readings for one series: flat, a climb, flat, a reset to
// zero, flat, another climb, flat.
function aDayOfReadings() {
    const out = [];
    let t = 1785000000000;
    const run = (v, n) => { for (let i = 0; i < n; i++, t += 5 * MIN) out.push({ t, v }); };
    run(40, 30);
    for (let v = 41; v <= 48; v++) run(v, 1);
    run(48, 40);
    run(0, 20);
    for (let v = 3; v <= 30; v += 3) run(v, 1);
    run(30, 60);
    return out;
}

function recordAll(readings) {
    const store = UsageHistory.newStore(10000);
    for (const r of readings)
        UsageHistory.record(store, { w: r.v }, r.t);
    return store;
}

test("the chart draws the same line from the changes as from every reading", () => {
    const readings = aDayOfReadings();
    const store = recordAll(readings);
    const shown = store.history.map(p => ({ t: p.t, v: p.w }));
    assert.ok(store.points.length < readings.length / 3, `${store.points.length} of ${readings.length} stored`);

    // Every point the chart gets is a reading that was really taken...
    const taken = new Map(readings.map(r => [r.t, r.v]));
    for (const p of shown)
        assert.equal(taken.get(p.t), p.v, `t=${p.t}`);
    // ...and every reading it does not get sits between two it does that hold the
    // same value, so the curve through them is flat there and passes through it.
    const kept = new Set(shown.map(p => p.t));
    for (const r of readings) {
        if (kept.has(r.t))
            continue;
        const before = shown.filter(p => p.t < r.t).pop();
        const after = shown.find(p => p.t > r.t);
        assert.equal(before.v, r.v, `before t=${r.t}`);
        assert.equal(after.v, r.v, `after t=${r.t}`);
    }
    // And the line reaches the latest reading, not the start of the run it is in.
    assert.equal(shown[shown.length - 1].t, readings[readings.length - 1].t);
});

test("a run that ends is closed by its last sighting", () => {
    // Without that point the curve would ramp from where the run began to the new
    // value, drawing a slow climb that never happened.
    const t0 = 1785000000000;
    const store = UsageHistory.newStore(500);
    for (let i = 0; i <= 6; i++)
        UsageHistory.record(store, { w: 40 }, t0 + i * 5 * MIN);
    UsageHistory.record(store, { w: 45 }, t0 + 35 * MIN);
    assert.deepEqual(store.points, [{ t: t0, w: 40 }, { t: t0 + 30 * MIN, w: 40 }, { t: t0 + 35 * MIN, w: 45 }]);
});

test("an unchanged reading is still stored once an hour", () => {
    // So a run that ends while no frontend is running to see it end is drawn from
    // at most an hour before, not from wherever it began.
    const t0 = 1785000000000;
    const store = UsageHistory.newStore(500);
    for (let i = 0; i <= 36; i++)
        UsageHistory.record(store, { w: 40 }, t0 + i * 5 * MIN);
    assert.deepEqual(store.points.map(p => (p.t - t0) / MIN), [0, 60, 120, 180]);
    assert.equal(store.fresh.length, 4, "and nothing in between is queued");
});

test("a series that stops reporting closes its run at its last sighting", () => {
    // A provider erroring for a while must not leave its flat run open, or the
    // chart would ramp across the outage from wherever the run began.
    const t0 = 1785000000000;
    const store = UsageHistory.newStore(500);
    for (let i = 0; i <= 6; i++)
        UsageHistory.record(store, { w: 40, s: 1 }, t0 + i * 5 * MIN);
    for (let i = 7; i <= 12; i++)
        UsageHistory.record(store, { s: 1 }, t0 + i * 5 * MIN);
    UsageHistory.record(store, { w: 50, s: 1 }, t0 + 65 * MIN);
    const w = store.points.filter(p => p.w !== undefined).map(p => [(p.t - t0) / MIN, p.w]);
    assert.deepEqual(w, [[0, 40], [30, 40], [65, 50]]);
});

test("the chart gets the latest reading even when nothing was stored for it", () => {
    const t0 = 1785000000000;
    const store = UsageHistory.newStore(500);
    UsageHistory.record(store, { w: 40 }, t0);
    UsageHistory.record(store, { w: 40 }, t0 + 5 * MIN);
    assert.deepEqual(store.points, [{ t: t0, w: 40 }]);
    assert.deepEqual(store.history, [{ t: t0, w: 40 }, { t: t0 + 5 * MIN, w: 40 }]);
});

test("the burn rate reads the same from the changes as from every reading", () => {
    // Fitting the stored points directly would weigh a flat run by how it is
    // stored rather than how long it lasted, and move the ETA and the pulse.
    const readings = aDayOfReadings();
    const store = recordAll(readings);
    const dense = readings.map(r => ({ t: r.t, w: r.v }));
    for (const hours of [2, 6, 12]) {
        const fromDense = UsageHistory.slopePerHour(dense, "w", hours * 3600000);
        const fromStored = UsageHistory.slopePerHour(store.history, "w", hours * 3600000);
        assert.ok(Math.abs(fromDense - fromStored) < 1e-9, `${hours}h: ${fromDense} vs ${fromStored}`);
    }
});

test("the burn rate can read one series through another", () => {
    // The Antigravity chart reads "ag" wherever a point has no "agg".
    const slope = UsageHistory.slopePerHour([{ t: 0, ag: 10 }, { t: 3600000, agg: 20 }], "agg", 3600000, "ag");
    assert.ok(Math.abs(slope - 10) < 1e-9, String(slope));
    assert.equal(UsageHistory.slopePerHour([{ t: 0, w: 1 }], "w", 3600000), null, "one sample has no slope");
});

// ── The two frontends against the real history-io ───────────────────────────
// The Plasma widget and the Quickshell panel share one file and never
// coordinate, so what each one *sends* decides what the other can lose. Both
// compose the same three calls — record(), missing() and union() — around
// history-io, and that composition is replayed here rather than only its parts.

const os = require("node:os");

const HISTORY_IO = path.join(__dirname, "..", "backend", "sh", "history-io");

function newHome() {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "tidesi-history-"));
    test.after(() => fs.rmSync(home, { recursive: true, force: true }));
    return home;
}

function fileOf(home) {
    return JSON.parse(fs.readFileSync(path.join(home, "tidesi", "usage-history-latest.json"), "utf8"));
}

// What is left of a frontend once the protocol moved into the store: run the
// tool, hand the answer back. This is the whole of main.qml's and
// TidesiShell.qml's part, so the decisions under test here are the shipped
// ones. `send` runs the tool — the file changes there and then — while `settle`
// delivers the answer, which is the gap an asynchronous response opens and where
// samples land.
class Frontend {
    constructor(home, limit = 500, debounceMs = 0) {
        this.home = home;
        this.store = UsageHistory.newStore(limit);
        this.answer = null;
        this.env = {};
        this.debounceMs = debounceMs;
        this.now = 0;
    }

    // What it has stored. The chart's view adds the latest reading on top, which
    // the file only gets once that reading's run ends.
    get history() {
        return this.store.points;
    }

    run(op, json) {
        const env = Object.assign({}, process.env, { XDG_DATA_HOME: this.home }, this.env);
        if (json !== null)
            env.WIDGET_HISTORY_JSON = json;
        return JSON.parse(execFileSync(HISTORY_IO, [op], { env, encoding: "utf8" }).trim());
    }

    // loadUsageHistory(): the widget config, or a snapshot the user imported.
    restore(points) {
        UsageHistory.restore(this.store, points);
    }

    // the startup autoload, and the release that follows it
    load() {
        UsageHistory.adopt(this.store, this.run("autoload", null).data);
        UsageHistory.opened(this.store);
        this.save();
        this.settle();
    }

    // recordHistoryValues() / recordHistory()
    record(values, nowMs) {
        UsageHistory.record(this.store, values, nowMs);
        this.now = nowMs;
        this.save();
    }

    // saveHistory(): arm the debounce window, or send straight away without one.
    save() {
        if (this.debounceMs) {
            if (UsageHistory.arm(this.store, this.now, this.debounceMs))
                this.armedAt = this.now;
        } else {
            this.send();
        }
    }

    // The debounce timer fires: send once the window has elapsed.
    advance(nowMs) {
        this.now = nowMs;
        if (this.debounceMs && UsageHistory.due(this.store, nowMs))
            this.send();
    }

    // A controlled hide/exit.
    flush() {
        const batch = UsageHistory.takeForExit(this.store);
        if (batch)
            this.run(batch.op, JSON.stringify(batch.points));
    }

    // sendHistory()
    send() {
        const batch = UsageHistory.take(this.store);
        if (batch)
            this.answer = this.run(batch.op, JSON.stringify(batch.points));
    }

    // finishHistorySave() / failHistorySave()
    settle() {
        if (!this.store.sending)
            return;

        const res = this.answer;
        this.answer = null;
        if (!res || res.error) {
            UsageHistory.failed(this.store);
            return;
        }
        UsageHistory.done(this.store, res.data);
        this.save();
    }
}

test("a save never rolls back a reading the other frontend recorded", () => {
    const home = newHome();
    const plasma = new Frontend(home);
    const quick = new Frontend(home);
    plasma.load();
    quick.load();

    plasma.record({ w: 10 }, 1000);
    plasma.settle();
    quick.record({ cp: 5 }, 2000);
    quick.settle();
    // The widget patches its own point; the panel is still holding the w: 10 it
    // adopted a moment ago.
    plasma.record({ w: 11 }, 3000);
    plasma.settle();
    assert.deepEqual(quick.history, [{ t: 1000, w: 10 }, { t: 2000, cp: 5 }]);

    // The panel's next save must carry its own sample and not that stale copy.
    quick.record({ cp: 6 }, 4000);
    quick.settle();
    assert.deepEqual(fileOf(home), [{ t: 1000, w: 11 }, { t: 2000, cp: 6 }]);

    // A save is also the read: each frontend catches up with whatever the other
    // recorded on its own next one, without polling the file separately.
    plasma.record({ w: 12 }, 5000);
    plasma.settle();
    assert.deepEqual(plasma.history, fileOf(home));
    quick.record({ cp: 7 }, 6000);
    quick.settle();
    assert.deepEqual(quick.history, fileOf(home));
});

test("a save in flight cannot replace a sample recorded while it ran", () => {
    const home = newHome();
    const f = new Frontend(home);
    f.load();

    f.record({ w: 10 }, 1000);
    // Still waiting on that answer, and the merge window has passed, so this is
    // a point of its own that the file has not heard about yet.
    f.record({ w: 20 }, 1000 + UsageHistory.MERGE_WINDOW_MS + 1);
    f.settle();
    assert.deepEqual(f.history, [{ t: 1000, w: 10 }, { t: 121001, w: 20 }]);

    // The batch held back while the first save ran goes out with the next one.
    f.settle();
    assert.deepEqual(fileOf(home), [{ t: 1000, w: 10 }, { t: 121001, w: 20 }]);
});

test("exit flush persists active and queued batches before a late success", () => {
    const home = newHome();
    const f = new Frontend(home);
    f.load();
    UsageHistory.record(f.store, { w: 10 }, 1000);
    const first = UsageHistory.take(f.store);
    UsageHistory.record(f.store, { w: 20 }, 1000 + UsageHistory.MERGE_WINDOW_MS + 1);

    f.flush();
    f.run(first.op, JSON.stringify(first.points));
    UsageHistory.done(f.store, fileOf(home));

    assert.deepEqual(fileOf(home), [{ t: 1000, w: 10 }, { t: 121001, w: 20 }]);
    assert.equal(fileOf(home).length, 2);
});

test("exit flush persists active and queued batches before a late failure", () => {
    const home = newHome();
    const f = new Frontend(home);
    f.load();
    UsageHistory.record(f.store, { w: 10 }, 1000);
    UsageHistory.take(f.store);
    UsageHistory.record(f.store, { w: 20 }, 1000 + UsageHistory.MERGE_WINDOW_MS + 1);

    f.flush();
    UsageHistory.failed(f.store);

    assert.deepEqual(fileOf(home), [{ t: 1000, w: 10 }, { t: 121001, w: 20 }]);
    assert.equal(fileOf(home).length, 2);
});

test("a startup restore adds what the file lacks and adopts the rest", () => {
    const home = newHome();
    const quick = new Frontend(home);
    quick.load();
    quick.record({ cp: 5 }, 2000);
    quick.settle();

    // The widget comes back with a config written before the panel ever ran, and
    // with its own stale copy of a point the panel has since patched.
    const plasma = new Frontend(home);
    plasma.restore([{ t: 500, w: 1 }, { t: 2000, cp: 4 }]);
    plasma.load();
    plasma.settle();
    assert.deepEqual(plasma.history, [{ t: 500, w: 1 }, { t: 2000, cp: 5 }]);
    assert.deepEqual(fileOf(home), [{ t: 500, w: 1 }, { t: 2000, cp: 5 }]);
});

test("a seed cannot override the file, not even one written since it was read", () => {
    // The window a frontend that worked the difference out for itself would be
    // deciding in: its copy of the file is from before the other frontend wrote.
    // Only history-io sees both sides at once, and only under the lock.
    const home = newHome();
    const f = new Frontend(home);
    f.restore([{ t: 500, s: 1 }, { t: 1000, w: 10 }]);
    UsageHistory.adopt(f.store, f.run("autoload", null).data);

    const peer = new Frontend(home);
    peer.load();
    peer.record({ w: 20 }, 1000);
    peer.settle();

    UsageHistory.opened(f.store);
    f.send();
    f.settle();
    assert.deepEqual(fileOf(home), [{ t: 500, s: 1 }, { t: 1000, w: 20 }]);
    assert.deepEqual(f.history, [{ t: 500, s: 1 }, { t: 1000, w: 20 }]);
});

test("an import cannot override a reading the file has since taken", () => {
    const home = newHome();
    const f = new Frontend(home);
    f.load();

    // The other frontend records while this one is not looking, so this one's
    // copy of the file has never held w: 20.
    const peer = new Frontend(home);
    peer.load();
    peer.record({ w: 20 }, 1000);
    peer.settle();

    // A snapshot from when the series was younger: it holds an older value for
    // that very point, and a point the running series has lost.
    f.restore([{ t: 500, s: 3 }, { t: 1000, w: 10 }]);
    f.send();
    f.settle();

    assert.deepEqual(fileOf(home), [{ t: 500, s: 3 }, { t: 1000, w: 20 }]);
    assert.deepEqual(f.history, [{ t: 500, s: 3 }, { t: 1000, w: 20 }]);
});

test("a save that could not merge keeps its batch for the next poll", () => {
    const home = newHome();
    const f = new Frontend(home);
    f.load();
    f.record({ w: 10 }, 1000);
    f.settle();

    // An interpreter that cannot run the merge: history-io reports it rather than
    // writing over the file, and the batch comes back to the outbox.
    f.env = { PYTHON3: "/nonexistent/python3" };
    f.record({ w: 20 }, 200000);
    f.settle();
    assert.deepEqual(fileOf(home), [{ t: 1000, w: 10 }]);
    assert.deepEqual(f.store.fresh, [{ t: 200000, w: 20 }]);
    // The sample is still on screen either way — only the mirror is behind.
    assert.deepEqual(f.history, [{ t: 1000, w: 10 }, { t: 200000, w: 20 }]);

    // ...and it is not thrown straight back at a tool that just failed.
    f.send();
    assert.equal(f.store.sending, null);

    // With the interpreter back, the next poll lands it.
    f.env = {};
    f.record({}, 200001);
    f.settle();
    assert.deepEqual(fileOf(home), [{ t: 1000, w: 10 }, { t: 200000, w: 20 }]);
});

// ── The persistence debounce ────────────────────────────────────────────────
// Persistence is coalesced into one history-io run per window while the chart
// stays current from the in-memory store. The window is fixed from the first
// changed reading, so a fast poll batches up instead of sliding the save away.

test("repeated snapshots within the window coalesce into one persistence", () => {
    const WINDOW = 300000;
    const now = 1785000000000;
    const store = UsageHistory.newStore(500);
    UsageHistory.opened(store);

    // The first changed reading arms the window; later ones do not re-arm it.
    UsageHistory.record(store, { w: 10 }, now);
    assert.equal(UsageHistory.arm(store, now, WINDOW), true);
    assert.equal(UsageHistory.arm(store, now + 1000, WINDOW), false, "already armed");
    UsageHistory.record(store, { w: 11 }, now + 60000);
    assert.equal(UsageHistory.arm(store, now + 60000, WINDOW), false);

    // Nothing is due before the window ends; one batch carries everything after.
    assert.equal(UsageHistory.due(store, now + WINDOW - 1), false);
    assert.equal(UsageHistory.due(store, now + WINDOW), true);
    const batch = UsageHistory.take(store);
    assert.equal(batch.op, "autosave");
    assert.deepEqual(batch.points, [{ t: now, w: 11 }], "the second reading patched the first");
    assert.equal(UsageHistory.due(store, now + WINDOW), false, "the window is spent");
});

test("a flush sends the pending batch immediately", () => {
    const WINDOW = 300000;
    const now = 1785000000000;
    const store = UsageHistory.newStore(500);
    UsageHistory.opened(store);

    UsageHistory.record(store, { w: 10 }, now);
    assert.equal(UsageHistory.arm(store, now, WINDOW), true);
    assert.equal(UsageHistory.due(store, now + 1000), false);

    assert.equal(UsageHistory.flush(store), true);
    assert.deepEqual(UsageHistory.take(store).points, [{ t: now, w: 10 }]);
    assert.equal(UsageHistory.due(store, now + 1000), false, "flush cancels the window");
});

test("a failed write is retried after the next window", () => {
    const WINDOW = 300000;
    const now = 1785000000000;
    const store = UsageHistory.newStore(500);
    UsageHistory.opened(store);

    UsageHistory.record(store, { w: 10 }, now);
    UsageHistory.arm(store, now, WINDOW);
    assert.equal(UsageHistory.due(store, now + WINDOW), true);
    assert.deepEqual(UsageHistory.take(store).points, [{ t: now, w: 10 }]);

    UsageHistory.failed(store);
    assert.deepEqual(store.fresh, [{ t: now, w: 10 }], "the batch is kept, not dropped");
    assert.equal(UsageHistory.take(store), null, "not retried until the next poll");

    // The next poll releases the retry and arms a fresh window.
    UsageHistory.record(store, {}, now + 200000);
    assert.equal(UsageHistory.arm(store, now + 200000, WINDOW), true);
    assert.equal(UsageHistory.due(store, now + 200000 + WINDOW), true);
    assert.deepEqual(UsageHistory.take(store).points, [{ t: now, w: 10 }]);
});

test("exit during a pending batch flushes it", () => {
    const WINDOW = 300000;
    const now = 1785000000000;
    const store = UsageHistory.newStore(500);
    UsageHistory.opened(store);

    UsageHistory.record(store, { w: 10 }, now);
    UsageHistory.arm(store, now, WINDOW);
    assert.equal(UsageHistory.due(store, now + 1000), false, "the window has not elapsed");

    assert.equal(UsageHistory.flush(store), true);
    assert.deepEqual(UsageHistory.take(store).points, [{ t: now, w: 10 }]);
});

test("a flush also releases a batch held back by a failure", () => {
    const WINDOW = 300000;
    const now = 1785000000000;
    const store = UsageHistory.newStore(500);
    UsageHistory.opened(store);

    UsageHistory.record(store, { w: 10 }, now);
    UsageHistory.arm(store, now, WINDOW);
    UsageHistory.take(store);
    UsageHistory.failed(store);
    assert.equal(UsageHistory.take(store), null, "waiting for the next poll");

    // A controlled exit does not wait for the next poll.
    assert.equal(UsageHistory.flush(store), true);
    assert.deepEqual(UsageHistory.take(store).points, [{ t: now, w: 10 }]);
});

test("an unchanged reading never arms the debounce", () => {
    const WINDOW = 300000;
    const now = 1785000000000;
    const store = UsageHistory.newStore(500);
    UsageHistory.opened(store);

    UsageHistory.record(store, { w: 40 }, now);
    assert.deepEqual(UsageHistory.take(store).points, [{ t: now, w: 40 }]);
    UsageHistory.done(store, [{ t: now, w: 40 }]);

    // A repeat reading queues nothing, so nothing arms.
    UsageHistory.record(store, { w: 40 }, now + 31000);
    assert.equal(UsageHistory.arm(store, now + 31000, WINDOW), false);
    assert.equal(UsageHistory.due(store, now + 31000 + WINDOW), false);
});

test("nothing arms the debounce before the startup read has answered", () => {
    const WINDOW = 300000;
    const now = 1785000000000;
    const store = UsageHistory.newStore(500);

    UsageHistory.record(store, { w: 10 }, now);
    assert.equal(UsageHistory.arm(store, now, WINDOW), false);
    UsageHistory.opened(store);
    assert.equal(UsageHistory.arm(store, now, WINDOW), true);
});

test("a debounced frontend persists once per window and flushes on exit", () => {
    const home = newHome();
    const f = new Frontend(home, 500, 300000);
    f.load();

    // Three polls inside one window coalesce into one save.
    f.record({ w: 10 }, 1000);
    f.record({ w: 11 }, 61000);
    f.record({ w: 12 }, 121000);
    const latest = path.join(home, "tidesi", "usage-history-latest.json");
    assert.equal(fs.existsSync(latest), false, "nothing is written before the window ends");

    f.advance(301000);
    f.settle();
    // The run that ended at 61000 is closed by its last sighting, so the one
    // save carries the whole window: the patched first point, the hold, and
    // the reading that ended the run.
    assert.deepEqual(fileOf(home), [{ t: 1000, w: 11 }, { t: 61000, w: 11 }, { t: 121000, w: 12 }]);

    // A controlled exit flushes whatever is still queued.
    f.record({ w: 13 }, 400000);
    f.flush();
    f.settle();
    assert.deepEqual(fileOf(home), [{ t: 1000, w: 11 }, { t: 61000, w: 11 }, { t: 121000, w: 12 }, { t: 400000, w: 13 }]);
});

test("replays a reset that happened while nothing was recorded", () => {
    const H = 3600000;
    const resetAt = 100 * H;          // next reset
    const period = 5 * H;             // five-hour window
    // Asleep from 88h to 97h — the 90h and 95h resets fall inside that gap.
    const series = [{ t: 88 * H, v: 80 }, { t: 97 * H, v: 12 }];
    const out = UsageHistory.withResets(series, resetAt, period, 80 * H, 99 * H);

    assert.deepEqual(out.map(p => [p.t / H, p.v]), [
        [88, 80],
        [90 - 1 / H, 80], [90, 0],
        [95 - 1 / H, 0], [95, 0],
        [97, 12]
    ]);
});

test("drops the curve for a reset newer than the last sample", () => {
    const H = 3600000;
    const series = [{ t: 10 * H, v: 40 }];
    const out = UsageHistory.withResets(series, 12 * H, 5 * H, 5 * H, 14 * H);
    assert.deepEqual(out.map(p => p.v), [40, 40, 0]);
    assert.equal(out[out.length - 1].t, 12 * H);
});

test("leaves a series alone when the window never resets", () => {
    const series = [{ t: 1, v: 1 }, { t: 2, v: 2 }];
    assert.equal(UsageHistory.withResets(series, 0, 0, 0, 10), series);
    assert.equal(UsageHistory.withResets(series, 5, 0, 0, 10), series);
});

// ── Shell.js ────────────────────────────────────────────────────────────────
// The Plasma widget hands the backend its configured API keys as environment
// assignments in a /bin/sh command line, base64-encoded so metacharacters in a
// key cannot break out of it. Encoding this wrong does not fail loudly — the
// backend receives a well-formed but wrong credential and the provider answers
// with an auth error, which is issue #16: keys pasted into the widget's own
// settings fields were encoded as the *stringified* byte array ("119,105,...")
// and every provider rejected them, while the same key worked from $ENV.

test("encodes ASCII bytes the way `base64 -d` reads them back", () => {
    assert.equal(Shell.base64(""), "");
    assert.equal(Shell.base64("a"), "YQ==");
    assert.equal(Shell.base64("ab"), "YWI=");
    assert.equal(Shell.base64("abc"), "YWJj");
    const key = "5c8f2e1b4d3a4b7e9c2f1a2b3c4d5e6f.AbCdEfGhIjKlMnOp";
    assert.equal(Shell.base64(key), Buffer.from(key, "utf8").toString("base64"));
});

test("encodes UTF-8, including characters outside the BMP", () => {
    for (const text of ["clé-ünïcode-ñ", "密钥", "key 🔑 end", "\u{1F600}\u{1F680}"])
        assert.equal(Shell.base64(text), Buffer.from(text, "utf8").toString("base64"));
});

test("never emits invalid UTF-8 for an unpaired surrogate", () => {
    for (const text of ["\uD800", "x\uDC00y"])
        assert.equal(Shell.base64(text), Buffer.from(text, "utf8").toString("base64"));
});

test("builds no assignment for an empty value", () => {
    assert.equal(Shell.envAssign("WIDGET_ZAI_TOKEN", ""), "");
    assert.equal(Shell.envAssign("WIDGET_ZAI_TOKEN", null), "");
    assert.equal(Shell.envAssign("WIDGET_ZAI_TOKEN", undefined), "");
});

test("delivers the value byte-for-byte through a real shell", () => {
    // The end-to-end claim of the round-trip: whatever the user pasted is what
    // the backend's environment holds. Run through /bin/sh, the interpreter
    // Plasma's executable DataEngine uses, rather than trusting the encoder.
    const values = [
        "5c8f2e1b4d3a4b7e9c2f1a2b3c4d5e6f.AbCdEfGhIjKlMnOp",
        "sk-proj_a-b_c.d~e",
        "spaces and $HOME and `backticks` and \"quotes\" and 'single'",
        "semi;colon | pipe && amp > redirect",
        "clé-🔑-密钥"
    ];
    for (const value of values) {
        // Read it back from a *child* process: an assignment prefix lands in
        // the launched command's environment, which is exactly where the
        // Python backend reads it from — os.environ, not the parent shell.
        const cmd = Shell.envAssign("WIDGET_TEST_KEY", value) + '/bin/sh -c \'printf %s "$WIDGET_TEST_KEY"\'';
        assert.equal(execFileSync("/bin/sh", ["-c", cmd], { encoding: "utf8" }), value);
    }
});

test("quotes a path for the shell without losing a quote", () => {
    const cmd = "printf %s " + Shell.quote("/home/u/it's a dir/get-ai-usage");
    assert.equal(execFileSync("/bin/sh", ["-c", cmd], { encoding: "utf8" }), "/home/u/it's a dir/get-ai-usage");
});

test("a failed pricing refresh triggers no usage work", () => {
    assert.equal(RefreshCoalescer.nextAction(false, false, false), "none");
    assert.equal(RefreshCoalescer.nextAction(false, true, false), "none");
    assert.equal(RefreshCoalescer.nextAction(undefined, false, false), "none");
});

test("pricing refreshes usage immediately only when usage is idle", () => {
    assert.equal(RefreshCoalescer.nextAction(true, false, false), "refresh-now");
    assert.equal(RefreshCoalescer.nextAction(true, true, false), "mark-pending");
});

test("a pricing refresh during an in-flight usage request coalesces to one", () => {
    // First pricing completion during a running usage request marks one pending.
    assert.equal(RefreshCoalescer.nextAction(true, true, false), "mark-pending");
    // Further completions while still in flight do not stack another.
    assert.equal(RefreshCoalescer.nextAction(true, true, true), "none");
    // When the request settles, the pending refresh fires once.
    assert.equal(RefreshCoalescer.nextAction(true, false, true), "refresh-now");
});

test("Project Info network work is deferred until the visible pane ticks its client", () => {
    const source = fs.readFileSync(path.join(__dirname, "..", "ui/ProjectInfoPane.qml"), "utf8");
    assert.match(source, /if \(foregroundVisible && onlineEnabled\)\s*client\.tick\(\)/);
    assert.match(source, /if \(client\)\s*client\.dispose\(\)/);

    const requests = [];
    const client = ProjectInfoRequests.create({
        latestReleaseUrl: "/release",
        statistics: [],
        contributorsUrl: "/contributors"
    }, () => {
        const request = {
            abort() { this.aborted = true; },
            open(_method, url) { this.url = url; },
            send() { requests.push(this); }
        };
        return request;
    }, () => 1000, () => {});

    assert.equal(requests.length, 0);
    client.tick();
    assert.equal(requests.length, 2);
    client.pause();
    assert.ok(requests.every(request => request.aborted));
    client.dispose();
    client.tick();
    assert.equal(requests.length, 2);
});

test("panel-critical state stays resident while popup views are conditional", () => {
    // The panel slot and provider icons are always constructed; only the
    // popup-only views depend on showSettings/active tab. This guards the
    // rule that lazy-loading must not unload panel state.
    const main = fs.readFileSync(path.join(__dirname, "..", "hosts/kde/contents/ui/main.qml"), "utf8");
    assert.match(main, /compactRepresentation:/);
    assert.match(main, /fullRepresentation:/);
    // The compact panel does not reference the popup-only Info pane.
    const compact = main.slice(main.indexOf("compactRepresentation:"), main.indexOf("fullRepresentation:"));
    assert.doesNotMatch(compact, /ProjectInfoPane|SettingsPage|PopupContent/);
});

test("binary window bounds match a linear filter exactly", () => {
    const points = Array.from({ length: 2000 }, (_, i) => ({ t: 1000 + i * 7, v: (i * 13) % 101 }));
    const linear = (minT, maxT) => points.filter(p => p.t >= minT && p.t <= maxT);
    for (const [minT, maxT] of [[1000, 1000 + 1999 * 7], [1000, 1000], [-5000, 999], [999999, 1000000], [3000, 9000], [0, 0]]) {
        const from = UsageHistory.lowerBound(points, minT);
        const to = UsageHistory.upperBound(points, maxT);
        assert.deepEqual(points.slice(from, to), linear(minT, maxT), `window ${minT}..${maxT}`);
    }
});

test("binary bounds are inclusive on both ends", () => {
    const points = [{ t: 10 }, { t: 20 }, { t: 30 }];
    assert.equal(UsageHistory.lowerBound(points, 20), 1);
    assert.equal(UsageHistory.upperBound(points, 20), 2);
    assert.deepEqual(points.slice(1, 2), [{ t: 20 }]);
    assert.equal(UsageHistory.lowerBound(points, 5), 0);
    assert.equal(UsageHistory.upperBound(points, 99), 3);
    assert.deepEqual(points.slice(0, 0), []);
});

test("binary bounds tolerate empty and single-element series", () => {
    assert.equal(UsageHistory.lowerBound([], 5), 0);
    assert.equal(UsageHistory.upperBound([], 5), 0);
    assert.equal(UsageHistory.lowerBound([{ t: 7 }], 7), 0);
    assert.equal(UsageHistory.upperBound([{ t: 7 }], 7), 1);
    assert.equal(UsageHistory.lowerBound([{ t: 7 }], 8), 1);
    assert.equal(UsageHistory.upperBound([{ t: 7 }], 6), 0);
});

test("panel thresholds stay exact at their boundaries after the popup changes", () => {
    // The panel is always visible, so its thresholds are a contract. Amber at
    // 70, red at 90, both inclusive.
    const normal = "#000000";
    assert.equal(PanelColor.level(0), "normal");
    assert.equal(PanelColor.level(69), "normal");
    assert.equal(PanelColor.level(70), "warning");
    assert.equal(PanelColor.level(89), "warning");
    assert.equal(PanelColor.level(90), "danger");
    assert.equal(PanelColor.level(100), "danger");
    // The exact colours the panel paints.
    assert.equal(PanelColor.colorFor(0, normal), normal);
    assert.equal(PanelColor.colorFor(69, normal), normal);
    assert.equal(PanelColor.colorFor(70, normal), "#ffa64d");
    assert.equal(PanelColor.colorFor(89, normal), "#ffa64d");
    assert.equal(PanelColor.colorFor(90, normal), "#ff4d4d");
    assert.equal(PanelColor.colorFor(100, normal), "#ff4d4d");
});

test("panel colour rule tolerates invalid readings without leaving the normal state", () => {
    const normal = "#123456";
    for (const bad of [NaN, undefined, null, -1, "x"]) {
        assert.equal(PanelColor.colorFor(bad, normal), normal, String(bad));
    }
});

test("PanelSlot delegates its threshold colour to PanelColor", () => {
    const source = fs.readFileSync(path.join(__dirname, "..", "ui/PanelSlot.qml"), "utf8");
    assert.match(source, /PanelColor\.colorFor\(slot\.pct/);
    assert.doesNotMatch(source, /slot\.pct >= 90 \? slot\.dangerColor/);
});

test("panel works with zero, one, and multiple pins after popup changes", () => {
    // Rotation is disabled without multiple pins; the panel still chooses a
    // provider. These are the panel's provider-selection invariants.
    assert.equal(PanelRotation.isEnabled(600, []), false);
    assert.equal(PanelRotation.isEnabled(600, ["claude"]), false);
    assert.equal(PanelRotation.isEnabled(600, ["claude", "openai"]), true);
    assert.equal(PanelRotation.normalizeSelection(["claude", "openai"], ""), "claude");
    assert.equal(PanelRotation.normalizeSelection(["claude", "openai"], "openai"), "openai");
    assert.equal(PanelRotation.normalizeSelection(["claude", "openai"], "removed"), "claude");
    assert.equal(PanelRotation.nextSelection(["claude"], "claude"), "claude");
});

test("stale opacity is a panel contract, not a popup effect", () => {
    const source = fs.readFileSync(path.join(__dirname, "..", "ui/PanelSlot.qml"), "utf8");
    assert.match(source, /opacity: stale \? 0\.55 : 1/);
});

// Every host shares ui/AppState.qml, so these rules are pinned in one place.
test("the shared state delegates its refresh/session policy to the shared modules", () => {
    const state = fs.readFileSync(path.join(__dirname, "..", "ui/AppState.qml"), "utf8");
    assert.match(state, /SessionRefreshPolicy\.refreshDelayMs/);
    assert.match(state, /SessionRefreshPolicy\.cacheExpired/);
    assert.match(state, /RefreshCoalescer\.nextAction/);
    // The interval is never reimplemented as a literal.
    assert.doesNotMatch(state, /600000/);
});

test("the shared state retries a failed history save on the next poll", () => {
    const state = fs.readFileSync(path.join(__dirname, "..", "ui/AppState.qml"), "utf8");
    // failed() is what releases the batch for retry.
    assert.match(state, /UsageHistory\.failed\(root\.historyStore\)/);
    // The in-flight save is bounded with a watchdog.
    assert.match(state, /historySaveTimeout/);
});

test("the shared state rejects a superseded session page", () => {
    const state = fs.readFileSync(path.join(__dirname, "..", "ui/AppState.qml"), "utf8");
    // A response only applies when its request id/query/source signature is current.
    assert.match(state, /requestId !== root\.sessionsRequestId/);
    assert.match(state, /root\.sessionsActiveSourceSignature !== root\.sessionsSourceSignature/);
});

test("the installed version comes from metadata.json, not a generated copy", () => {
    const root = path.join(__dirname, "..");
    assert.ok(!fs.existsSync(path.join(root, "ui/js/ProjectVersion.js")));
    const pane = fs.readFileSync(path.join(root, "ui/ProjectInfoPane.qml"), "utf8");
    assert.match(pane, /Plasmoid\.metaData\.version/);
    assert.match(pane, /backend\.appVersion/);
});
