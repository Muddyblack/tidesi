// The shared usage-history series.
//
// Both frontends append to the same rolling array — and to the same mirror file
// on disk — so the merge rules have to agree exactly. Only persistence differs
// (Plasma writes its widget config, Quickshell writes the file), so that stays
// with the callers.
//
// A point is {t: epochMs, <seriesKey>: value, ...}. Which keys a provider
// contributes is decided by the backend (historyValues in the contract), so
// nothing here knows about individual providers.

// Samples closer together than this patch the previous point instead of adding
// one, which keeps a fast poll interval from flooding the series.
var MERGE_WINDOW_MS = 120000;
// About 34 days of unbroken 5-minute polling, and most of a year at the density a
// machine that sleeps actually produces. 500 was ~13 days, which left the 30-day
// deltas ("last month") permanently without data. The file is ~40 bytes a sample,
// so this is a 480 KB ceiling, and the merge cost is process startup either way —
// 10k points measure the same as 500.
var DEFAULT_LIMIT = 10000;
// A flat run is stored as its first sighting, its last one before the value moves,
// and one sighting an hour in between. The hourly one bounds how far back the
// chart has to reach when a run ends while no frontend is running to see it end.
var HEARTBEAT_MS = 3600000;
// The grid slopePerHour() resamples on — the default poll interval.
var SLOPE_STEP_MS = 300000;

// A sample only counts when it carries a number. A provider that failed reports
// its keys as null; writing those into the series would erase the last good
// reading instead of leaving a gap.
function isValue(v) {
    return v !== undefined && v !== null;
}

// A timestamp written as a string is only read back when it is a plain decimal.
// Number() would also take "0x10", "1_0" and "Infinity", none of which Python's
// float() accepts — and the two implementations have to agree on every byte of
// the shared file.
var DECIMAL_RE = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;

// A point's timestamp as a finite number, or null when the point is unusable.
// The mirror file is written by whichever frontend ran last and can be edited by
// hand, so everything read back through it goes past this guard first.
function pointTime(p) {
    if (!p || typeof p !== "object")
        return null;

    var t = p.t;
    if (typeof t === "string") {
        if (!DECIMAL_RE.test(t.trim()))
            return null;

        t = Number(t);
    }
    if (typeof t !== "number" || !isFinite(t))
        return null;

    return t;
}

// Copy every series value of `src` onto `dst`, keeping `t` and dropping the
// blanks. Used everywhere two points have to be combined.
function assignValues(dst, src) {
    for (var key in src) {
        if (key === "t")
            continue;
        if (isValue(src[key]))
            dst[key] = src[key];
    }
    return dst;
}

// Collect every provider's historyValues from one backend response into a
// single patch.
function collect(providers) {
    var patch = {};
    for (var i = 0; i < (providers || []).length; i++) {
        var values = (providers[i] || {}).historyValues || {};
        for (var key in values)
            patch[key] = values[key];
    }
    return patch;
}

// Combine two series that were recorded independently — the mirror file on disk
// against whatever the running frontend already holds. Plasma keeps a second
// copy of the history in its widget config, so on startup the file may carry
// points the config never saw (recorded under Quickshell) and the config may
// carry points the file never saw (a mirror write that failed). Points sharing a
// timestamp are combined key by key, with `overlay` winning, so a point holding
// only `w` and one holding only `cp` end up as one complete point. Points the
// other side never recorded are kept as they are.
//
// Returns a new array, ascending by t and trimmed to the cap.
function union(base, overlay, limit) {
    var byTime = {};
    var t;

    // Both sides fold the same way: a repeated timestamp contributes its keys to
    // the point already there rather than replacing it, so two half-filled points
    // recorded a millisecond apart never cost each other their series.
    function absorb(points) {
        for (var j = 0; j < (points || []).length; j++) {
            var point = points[j];
            var pt = pointTime(point);
            if (pt === null)
                continue;

            if (!byTime[pt])
                byTime[pt] = {
                    t: pt
                };

            assignValues(byTime[pt], point);
        }
    }

    absorb(base);
    absorb(overlay);

    var out = [];
    for (t in byTime)
        out.push(byTime[t]);

    out.sort(function (p1, p2) {
        return p1.t - p2.t;
    });

    var cap = limit || DEFAULT_LIMIT;
    if (out.length > cap)
        out = out.slice(out.length - cap);

    return out;
}

// Accept both the current {t, <key>: v} points and the legacy weekly-only
// {t, v} shape, and trim to the cap. Used when importing or restoring a file.
function normalize(points, limit) {
    var out = [];
    for (var i = 0; i < (points || []).length; i++) {
        var p = points[i];
        var t = pointTime(p);
        // A point with no usable timestamp (null, a stray string, a torn write)
        // would sort as NaN and drag the whole chart range with it, and both
        // frontends would then persist it back into the shared file.
        if (t === null)
            continue;

        var q = {
            t: t
        };
        assignValues(q, p);
        // Legacy weekly-only points carried the value as `v`.
        if (q.w === undefined && isValue(p.v))
            q.w = p.v;
        delete q.v;
        out.push(q);
    }
    var cap = limit || DEFAULT_LIMIT;
    if (out.length > cap)
        out = out.slice(out.length - cap);

    return out;
}

// A rolling quota window empties at a known instant, whether or not anything
// was recorded then. With the machine asleep — or simply between two polls —
// the samples on either side of a reset would be joined by one straight line,
// which reads as hours of gradual usage that never happened and puts the drop
// at wake-up time instead of at the reset.
//
// So replay the resets we can derive: `resetAt` is the next one and `periodMs`
// how often it repeats, which pins every earlier reset in the visible range.
// For each one falling inside a gap, hold the previous value right up to the
// instant and drop to zero there; the curve then climbs again from the next
// real sample.
//
// `series` is the windowed [{t, v}] view, ascending. Returns a new array.
function withResets(series, resetAtMs, periodMs, minT, maxT) {
    if (!resetAtMs || !periodMs || periodMs <= 0 || !series || series.length === 0)
        return series;

    var instants = [];
    var guard = 0;
    for (var r = resetAtMs; r > minT && guard < 1000; r -= periodMs, guard++) {
        if (r <= maxT && r > series[0].t)
            instants.push(r);
    }
    if (instants.length === 0)
        return series;

    instants.reverse();

    // The held value is whatever the curve is at right now — which after an
    // earlier replayed reset is zero, not the last real sample. A long gap can
    // span several resets, so read it back off the output.
    var out = [];
    var next = 0;
    var i;

    function dropAt(instant) {
        out.push({
            t: instant - 1,
            v: out[out.length - 1].v
        });
        out.push({
            t: instant,
            v: 0
        });
    }

    for (i = 0; i < series.length; i++) {
        while (i > 0 && next < instants.length && instants[next] > series[i - 1].t && instants[next] < series[i].t) {
            dropAt(instants[next]);
            next++;
        }
        out.push(series[i]);
    }

    // A reset newer than the last sample: the window has already emptied even
    // though nothing has been recorded since.
    var lastT = series[series.length - 1].t;
    while (next < instants.length) {
        if (instants[next] > lastT)
            dropAt(instants[next]);

        next++;
    }
    return out;
}

// Least-squares slope in %/hour over the trailing `windowMs` of one series, fitted
// to the series resampled on a five-minute grid rather than to the stored points.
// record() stores a flat run as a pair of points, so fitting those directly would
// weigh the run by how it is stored instead of how long it lasted, and the ETA and
// the chart's pulse would move with the storage. `fallbackKey` stands in for a
// point that lacks `key`, the way the Antigravity chart reads "ag" for "agg".
function slopePerHour(points, key, windowMs, fallbackKey) {
    var xs = [];
    var ys = [];
    var i;
    for (i = 0; i < (points || []).length; i++) {
        var v = points[i][key];
        if (!isValue(v) && fallbackKey)
            v = points[i][fallbackKey];
        if (!isValue(v))
            continue;

        xs.push(points[i].t);
        ys.push(v);
    }
    if (xs.length < 2)
        return null;

    var end = xs[xs.length - 1];
    var steps = Math.floor((end - Math.max(end - windowMs, xs[0])) / SLOPE_STEP_MS);
    if (steps < 1)
        return null;

    // Hours before `end` rather than epoch milliseconds, whose squares lose the
    // precision the fit needs.
    var n = 0, sx = 0, sy = 0, sxx = 0, sxy = 0;
    var j = 0;
    for (var s = steps; s >= 0; s--) {
        var t = end - s * SLOPE_STEP_MS;
        while (j < xs.length - 2 && xs[j + 1] < t)
            j++;
        var y = ys[j] + (ys[j + 1] - ys[j]) * (t - xs[j]) / (xs[j + 1] - xs[j]);
        var x = -s * SLOPE_STEP_MS / 3.6e+06;
        n++;
        sx += x;
        sy += y;
        sxx += x * x;
        sxy += x * y;
    }
    var denom = n * sxx - sx * sx;
    return denom === 0 ? null : (n * sxy - sx * sy) / denom;
}

// ── The save protocol ────────────────────────────────────────────────────
//
// Both frontends run this over tools/sh/history-io, so it lives here rather than
// twice in QML, where only a live Plasma session or Quickshell could exercise it.
// The frontend is left with the transport and the timers.
//
// history-io gives the payload precedence, so what a payload holds is the whole
// contract: `fresh` is readings this frontend just took and asserts them, `seed`
// is a series it restored and only offers them. "Shared frontend code" in
// docs/provider-contract.md has the why, and the rest of the rules.

// The index of the first point with t >= value, or length when none is. The
// series is ascending (union() sorts), so callers can slice a visible window
// without scanning every point.
function lowerBound(points, value) {
    var lo = 0;
    var hi = (points || []).length;
    while (lo < hi) {
        var mid = (lo + hi) >> 1;
        if (points[mid].t < value)
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo;
}

// The index of the first point with t > value, or length when none is.
function upperBound(points, value) {
    var lo = 0;
    var hi = (points || []).length;
    while (lo < hi) {
        var mid = (lo + hi) >> 1;
        if (points[mid].t <= value)
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo;
}

function newStore(limit) {
    return {
        limit: limit || DEFAULT_LIMIT,
        // What the file holds, or will once the queue drains.
        points: [],
        // The latest reading. It stands in for the end of a flat run that has not
        // ended yet, so it is shown but not saved until the run does end.
        tail: null,
        // `points` and `tail` together, for the chart. Replaced, never patched in
        // place: an unchanged reference is all a frontend needs to know there is
        // nothing to repaint.
        history: [],
        json: "[]",
        // The point this frontend last wrote. record() patches that one and no
        // other, which holds a timestamp to one writer — bar the case in _store().
        ownT: null,
        // Queued to go out: measured, and restored.
        fresh: [],
        seed: [],
        sending: null,
        ready: false,
        // A save failed. The next poll is what retries it.
        waiting: false,
        // When the armed persistence debounce window ends (epoch ms), or null
        // when none is armed. The frontend's timer fires then and only then.
        dueAt: null
    };
}

// Rebuild what the chart sees. Returns false when it came out the same.
function _refresh(store) {
    var shown = store.tail ? union(store.points, [store.tail], store.limit) : store.points;
    var json = JSON.stringify(shown);
    if (json === store.json)
        return false;

    store.history = shown;
    store.json = json;
    return true;
}

// The newest stored point holding `key`, or null.
function _lastSaved(store, key) {
    for (var i = store.points.length - 1; i >= 0; i--)
        if (isValue(store.points[i][key]))
            return store.points[i];
    return null;
}

// Whether the tail holds a sighting of `key` newer than the last one stored.
function _unstored(tail, key, saved) {
    return !!tail && isValue(tail[key]) && (!saved || tail.t > saved.t);
}

// Store this poll's hold and point, and queue them. `saveT` is when the point
// goes: now, or the time of the point being patched.
function _store(store, save, hold, holdT, saveT) {
    var queued = [];
    if (hold)
        queued.push(assignValues({
            t: holdT
        }, hold));
    if (save) {
        queued.push(assignValues({
            t: saveT
        }, save));
        store.ownT = saveT;
    }
    // union() builds every point afresh, so nothing the frontend still holds is
    // rewritten under it — a mutation QML would never signal.
    store.points = union(store.points, queued, store.limit);
    store.fresh = union(store.fresh, queued, store.limit);
}

// A series this frontend restored rather than measured. It goes under whatever
// is already on screen, and into the seed lane to be offered to the file.
function restore(store, points) {
    var series = normalize(points, store.limit);
    if (series.length === 0)
        return false;

    store.seed = union(store.seed, series, store.limit);
    // An import is as good a trigger as a poll for a save that is waiting.
    store.waiting = false;
    store.points = union(series, store.points, store.limit);
    return _refresh(store);
}

// One poll's provider values, whether or not it carried any. Calling this every
// poll is also what releases a save that failed, so a retry waits for the next
// one instead of going straight back out at a tool that just refused it.
//
// Only what the chart does not already show is stored — most readings repeat the
// one before. The chart joins samples with curves, so a flat run keeps both of its
// ends: its first sighting, and its last one, written when the run ends (the
// hold). Without the last one the curve would ramp from where the run began to
// the next value, drawing a slow climb that never happened.
function record(store, values, nowMs) {
    store.waiting = false;

    var obs = {
        t: nowMs
    };
    var keys = [];
    var k;
    for (k in (values || {}))
        if (isValue(values[k])) {
            obs[k] = values[k];
            keys.push(k);
        }
    if (keys.length === 0)
        return false;

    var tail = store.tail;
    var last = store.points.length > 0 ? store.points[store.points.length - 1] : null;
    // Changes inside the merge window patch this frontend's own previous point
    // rather than adding one, so a fast poll interval cannot flood the series.
    // Someone else's point is left alone however recent: two writers patching one
    // point leaves no way to tell which value is the newer reading. `nowMs` is the
    // whole of that ownership, so two frontends polling in the same millisecond do
    // both claim the point, and its value flips between their readings until the
    // window passes. Closing it needs a writer id in every point — ~30% more file
    // for a one-point wobble at ~0.1% a day on the default poll.
    var patching = !!last && last.t === store.ownT && nowMs - last.t < MERGE_WINDOW_MS;
    var save = null;
    var hold = null;
    var saved;

    for (var i = 0; i < keys.length; i++) {
        k = keys[i];
        saved = _lastSaved(store, k);
        var shown = _unstored(tail, k, saved) ? tail[k] : (saved ? saved[k] : undefined);
        if (shown !== obs[k]) {
            if (!save)
                save = {};
            save[k] = obs[k];
            // What a patch overwrites is under two minutes old, too short a run
            // to need closing.
            if (!patching && _unstored(tail, k, saved)) {
                if (!hold)
                    hold = {};
                hold[k] = tail[k];
            }
        } else if (!saved || nowMs - saved.t >= HEARTBEAT_MS) {
            if (!save)
                save = {};
            save[k] = obs[k];
        }
    }
    // A series that stopped reporting — its provider erroring, or switched off —
    // has ended its run too, at its last sighting. The next poll replaces the tail,
    // so this is the last chance to keep it.
    for (k in (tail || {})) {
        if (k === "t" || isValue(obs[k]))
            continue;
        if (_unstored(tail, k, _lastSaved(store, k))) {
            if (!hold)
                hold = {};
            hold[k] = tail[k];
        }
    }

    store.tail = obs;
    if (save || hold)
        _store(store, save, hold, tail ? tail.t : null, patching ? last.t : nowMs);
    return _refresh(store);
}

// The file is the truth for everything it knows; what only exists here — not
// mirrored yet — is kept, and the fresh lane wins over both.
function adopt(store, series) {
    store.points = union(union(store.points, normalize(series, store.limit), store.limit), store.fresh, store.limit);
    return _refresh(store);
}

// The startup read has answered (or given up): the file is ours to write.
function opened(store) {
    store.ready = true;
}

// The next batch as {op, points}, or null when there is nothing to send yet.
// Seeds go first: a fresh install has to get its restored series on disk.
function take(store) {
    if (!store.ready || store.waiting || store.sending)
        return null;

    if (store.seed.length > 0) {
        store.sending = {
            op: "seed",
            points: store.seed
        };
        store.seed = [];
    } else if (store.fresh.length > 0) {
        store.sending = {
            op: "autosave",
            points: store.fresh
        };
        store.fresh = [];
    } else {
        return null;
    }

    // The window is spent once a batch goes out; the next one is armed afresh.
    store.dueAt = null;
    return store.sending;
}

// `merged` is the file with that batch already in it, so the batch is done.
// Whatever queued up while it was out has not been saved, and stays queued.
function done(store, merged) {
    store.sending = null;
    return adopt(store, merged);
}

// history-io leaves the file untouched when it cannot merge, so nothing was
// written. Put the batch back in its lane, under anything queued since, and wait
// for the next poll. Idempotent: the watchdog and a late answer may both call it.
function failed(store) {
    var batch = store.sending;
    store.sending = null;
    store.waiting = true;
    if (!batch)
        return;

    if (batch.op === "seed")
        store.seed = union(batch.points, store.seed, store.limit);
    else
        store.fresh = union(batch.points, store.fresh, store.limit);
}

// Whether take() would hand out a batch right now.
function _hasBatch(store) {
    return store.ready && !store.waiting && !store.sending && (store.seed.length > 0 || store.fresh.length > 0);
}

// Arm the persistence debounce. Returns true when the caller should start its
// timer: there is a batch to send and none is armed yet. The window is fixed
// from the first changed reading, so a fast poll coalesces into one save per
// window instead of sliding it forever.
function arm(store, nowMs, windowMs) {
    if (!_hasBatch(store) || store.dueAt !== null)
        return false;
    store.dueAt = nowMs + windowMs;
    return true;
}

// Whether the armed window has elapsed and a batch is waiting to go out.
function due(store, nowMs) {
    return store.dueAt !== null && nowMs >= store.dueAt;
}

// A controlled hide/exit: the window is over and whatever is queued goes out
// now. A batch held back by a failed save is released too — there is no next
// poll to wait for. Returns true when the caller should send.
function flush(store) {
    store.dueAt = null;
    store.waiting = false;
    return store.ready && (!!store.sending || store.seed.length > 0 || store.fresh.length > 0);
}

// Snapshot every unsaved point for a controlled exit. Keep `sending` owned by
// its asynchronous request: a late answer must not settle the shutdown batch.
function takeForExit(store) {
    if (!flush(store))
        return null;

    var active = store.sending;
    var op = active && active.op === "seed" || store.seed.length > 0 ? "seed" : "autosave";
    var points = active ? active.points : [];
    points = union(points, store.seed, store.limit);
    points = union(points, store.fresh, store.limit);
    return { op: op, points: points };
}


if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        MERGE_WINDOW_MS: MERGE_WINDOW_MS,
        pointTime: pointTime,
        collect: collect,
        union: union,
        lowerBound: lowerBound,
        upperBound: upperBound,
        normalize: normalize,
        withResets: withResets,
        slopePerHour: slopePerHour,
        HEARTBEAT_MS: HEARTBEAT_MS,
        newStore: newStore,
        restore: restore,
        record: record,
        adopt: adopt,
        opened: opened,
        take: take,
        done: done,
        failed: failed,
        takeForExit: takeForExit,
        arm: arm,
        due: due,
        flush: flush
    };
}
