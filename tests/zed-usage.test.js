const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ZedUsage = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../ui/js/ZedUsage.js"), "utf8").replace(/^\.pragma library\s*/, ""), ZedUsage);

test("cumulative series starts at zero and clips daily usage at now", () => {
    const usage = { available: true, usedUSD: 99, includedUSD: 4, periodStart: 0, periodEnd: 345600, dailyUSD: [
        { date: "1970-01-01", usd: 1 }, { date: "1970-01-02", usd: 2 }, { date: "1970-01-03", usd: 8 },
    ] };
    const result = ZedUsage.series(usage, 150000);
    assert.deepEqual(Array.from(result.points, point => point.usd), [0, 1, 3, 3]);
    assert.equal(result.threshold, 4);
    assert.equal(result.available, true);
});

test("crossing segments split at the exact allowance point", () => {
    const segments = ZedUsage.split([{ x: 0, usd: 0 }, { x: 10, usd: 10 }], 4);
    assert.deepEqual(JSON.parse(JSON.stringify(segments)), {
        included: [{ x: 0, usd: 0 }, { x: 4, usd: 4 }],
        overage: [{ x: 4, usd: 4 }, { x: 10, usd: 10 }],
    });
});

test("zero allowance is a known threshold and positive use is wholly above it", () => {
    const result = ZedUsage.series({ available: true, periodStart: 0, periodEnd: 86400, includedUSD: 0, dailyUSD: [{ date: "1970-01-01", usd: 2 }] }, 43200);
    assert.equal(result.threshold, 0);
    assert.deepEqual(Array.from(result.points, point => point.usd), [0, 2, 2]);
    assert.deepEqual(JSON.parse(JSON.stringify(ZedUsage.split(result.points, result.threshold))), {
        included: [{ x: 0, usd: 0 }], overage: [{ x: 0, usd: 0 }, { x: 0, usd: 2 }, { x: 0.5, usd: 2 }],
    });
});

test("unknown allowance stays absent and unavailable usage has no series", () => {
    const unknown = ZedUsage.series({ available: true, periodStart: 0, periodEnd: 86400, includedUSD: null, dailyUSD: [{ date: "1970-01-01", usd: 2 }] }, 43200);
    assert.equal(unknown.threshold, null);
    const unavailable = ZedUsage.series({ available: false, periodStart: 0, periodEnd: 86400, dailyUSD: [{ date: "1970-01-01", usd: 2 }] }, 43200);
    assert.equal(unavailable.points, null);
});

test("an available zero estimate draws a flat line through the observation time", () => {
    const result = ZedUsage.series({ available: true, periodStart: 100, periodEnd: 300, dailyUSD: [] }, 200);
    assert.deepEqual(JSON.parse(JSON.stringify(result.points)), [{ x: 0, usd: 0 }, { x: 0.5, usd: 0 }]);
});

test("unavailable usage retains a known allowance without inventing a curve", () => {
    const result = ZedUsage.series({ available: false, includedUSD: 10 }, 200);
    assert.equal(result.points, null);
    assert.equal(result.threshold, 10);
});

test("first-day usage uses its observation timestamp instead of being dropped before a midday reset", () => {
    const result = ZedUsage.series({ available: true, periodStart: 43200, periodEnd: 172800,
        dailyUSD: [{ date: "1970-01-01", at: 50000, usd: 2 }] }, 86400);
    assert.equal(result.points[result.points.length - 1].usd, 2);
    assert(result.points[1].x > 0);
});
