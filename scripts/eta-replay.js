#!/usr/bin/env node
// Scores the burn-rate ETA against a recorded usage history.
//
//   node scripts/eta-replay.js [usage-history.json] [hours ahead, default 1]
//
// Walks every percentage series in the file a quarter of an hour at a time. At
// each step it asks the rate the ETA is built on (UsageHistory.burnRate) what it
// would have said knowing only the readings up to then, and holds that against
// what the series really did over the next hour (or the hours given: a weekly
// window is predicted days ahead, so score it over 24). Steps where the frontend
// was not recording, or where the window reset within that time, are left out.
//
// Printed per half-life, next to the straight six-hour fit the ETA used before:
//   mae     mean error of the predicted hour, in points of quota
//   active  the same over the hours in which the quota really moved
//   quiet   the same over the hours in which it did not (what a stale ETA costs)
// The half-life the frontends use for a window is etaHalfLife() of its reset;
// this shows which values hold up on real usage.

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const UsageHistory = require("../ui/js/UsageHistory.js");

const MIN = 60000;
const HOUR = 60 * MIN;
const STEP = 15 * MIN;
// A step counts only when readings sit this close on both sides of it and of
// the hour after: the frontend was running and saw what happened.
const NEAR = 75 * MIN;
const RESET_DROP = 3;
const HALF_LIVES = [10, 20, 30, 45, 60, 90, 120, 180, 360, 720, 1440];

function defaultFile() {
    const base = process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share");
    return path.join(base, "tidesi", "usage-history-latest.json");
}

function load(file) {
    const body = JSON.parse(fs.readFileSync(file, "utf8"));
    const points = Array.isArray(body) ? body : body.points || body.history || [];
    return points.filter(p => p && typeof p.t === "number" && isFinite(p.t)).sort((a, b) => a.t - b.t);
}

// Readings of one series, and whether it is a percentage at all.
function seriesOf(points, key) {
    const out = points.filter(p => typeof p[key] === "number" && isFinite(p[key])).map(p => ({ t: p.t, v: p[key] }));
    const percent = out.length > 0 && out.every(p => p.v >= 0 && p.v <= 100);
    return percent ? out : null;
}

function valueAt(series, t) {
    let lo = 0;
    let hi = series.length - 1;
    if (t < series[0].t || t > series[hi].t)
        return null;
    while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (series[mid].t <= t)
            lo = mid;
        else
            hi = mid;
    }
    const a = series[lo];
    const b = series[hi];
    if (b.t - a.t > NEAR || b.v < a.v - RESET_DROP)
        return null;
    return b.t === a.t ? a.v : a.v + (b.v - a.v) * (t - a.t) / (b.t - a.t);
}

function resetBetween(series, from, to) {
    for (let i = 1; i < series.length; i++) {
        if (series[i].t <= from)
            continue;
        if (series[i - 1].t >= to)
            break;
        if (series[i].v < series[i - 1].v - RESET_DROP)
            return true;
    }
    return false;
}

function score(points, key, ahead) {
    const series = seriesOf(points, key);
    if (!series || series.length < 20)
        return null;

    const asPoints = series.map(p => ({ t: p.t, [key]: p.v }));
    const rows = {};
    const names = HALF_LIVES.map(m => `${m}m`).concat(["fit6h"]);
    for (const name of names)
        rows[name] = { n: 0, err: 0, activeN: 0, activeErr: 0, quietN: 0, quietErr: 0 };

    let end = 0;
    for (let t = series[0].t + 2 * HOUR; t + ahead <= series[series.length - 1].t; t += STEP) {
        const now = valueAt(series, t);
        const later = valueAt(series, t + ahead);
        if (now === null || later === null || resetBetween(series, t, t + ahead))
            continue;

        while (end < asPoints.length && asPoints[end].t <= t)
            end++;
        const known = asPoints.slice(Math.max(0, end - 2000), end);
        const actual = later - now;
        for (const name of names) {
            let rate;
            if (name === "fit6h")
                rate = UsageHistory.slopePerHour(known.concat([{ t, [key]: now }]), key, 6 * HOUR);
            else
                rate = UsageHistory.burnRate(known, key, now, t, parseInt(name, 10) * MIN);
            // Under half a point an hour the ETA shows nothing: a prediction of no climb.
            const predicted = rate === null || !isFinite(rate) || rate < 0.5 ? 0 : Math.min(rate * ahead / HOUR, 100 - now);
            const err = Math.abs(predicted - actual);
            const row = rows[name];
            row.n++;
            row.err += err;
            if (actual >= ahead / HOUR) {
                row.activeN++;
                row.activeErr += err;
            } else {
                row.quietN++;
                row.quietErr += err;
            }
        }
    }
    return rows;
}

function main() {
    const file = process.argv[2] || defaultFile();
    const hours = Number(process.argv[3]) > 0 ? Number(process.argv[3]) : 1;
    const points = load(file);
    if (points.length === 0) {
        console.error(`no history points in ${file}`);
        process.exit(1);
    }
    const keys = new Set();
    for (const p of points)
        for (const key of Object.keys(p))
            if (key !== "t")
                keys.add(key);

    const days = (points[points.length - 1].t - points[0].t) / (24 * HOUR);
    console.log(`${points.length} points over ${days.toFixed(1)} days, ${keys.size} series, predicting ${hours} h ahead`);

    const total = {};
    const fmt = (sum, n) => (n > 0 ? (sum / n).toFixed(2) : "  - ").padStart(6);
    const print = (title, rows) => {
        const any = Object.values(rows)[0];
        console.log(`\n${title}  (${any.n} steps scored, ${any.activeN} active)`);
        console.log("  half-life     mae  active   quiet");
        for (const [name, r] of Object.entries(rows))
            console.log(`  ${name.padEnd(9)} ${fmt(r.err, r.n)}  ${fmt(r.activeErr, r.activeN)}  ${fmt(r.quietErr, r.quietN)}`);
    };

    for (const key of [...keys].sort()) {
        const rows = score(points, key, hours * HOUR);
        if (!rows || Object.values(rows)[0].activeN < 5)
            continue;
        print(key, rows);
        for (const [name, r] of Object.entries(rows)) {
            total[name] = total[name] || { n: 0, err: 0, activeN: 0, activeErr: 0, quietN: 0, quietErr: 0 };
            for (const field of Object.keys(r))
                total[name][field] += r[field];
        }
    }
    if (Object.keys(total).length === 0) {
        console.log("\nno series with enough recorded activity to score");
        return;
    }
    print("all series", total);
}

main();
