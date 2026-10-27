const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const FeatureTabs = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../ui/js/FeatureTabs.js"), "utf8").replace(/^\.pragma library\s*/, ""), FeatureTabs);

function dateDaysAgo(days) {
    const now = new Date();
    const date = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate() - days));
    return date.toISOString().slice(0, 10);
}

function provider(id, stats) {
    return { id, label: id, details: { stats: { available: true, ...stats } } };
}

test("token-only providers stay visible without inventing dollar costs", () => {
    for (const id of ["antigravity", "kiro", "pi", "junie", "openai", "claude", "opencode"]) {
        const rows = FeatureTabs.spendProviderRows([provider(id, {
            totalTokens: 1234, dailySeries: [{ date: dateDaysAgo(0), total: 1234 }],
        })], {});
        assert.equal(rows.length, 1, id);
        assert.equal(rows[0].id, id);
        assert.equal(rows[0].cost, null);
        assert.equal(rows[0].costAvailable, false);
        assert.equal(rows[0].usageOnly, true);
        assert.equal(rows[0].tokens, 1234);
        assert.equal(FeatureTabs.spendAllTotal(rows, "USD"), 0);
        assert.equal(FeatureTabs.spendMeteredTotal(rows, "USD"), 0);
        assert.equal(FeatureTabs.spendPlanTotal(rows, "USD"), 0);
        assert.equal(FeatureTabs.spendTotal(rows, "USD"), 0);
    }
});

test("token-only day filters trim activity without converting unknown cost to zero", () => {
    const rows = FeatureTabs.spendProviderRows([provider("antigravity", {
        totalTokens: 1600,
        dailySeries: [{ date: dateDaysAgo(0), total: 100 }, { date: dateDaysAgo(3), total: 500 }, { date: dateDaysAgo(40), total: 1000 }],
    })], {});
    for (const [days, total] of [[1, 100], [7, 600], [30, 600], [0, 1600]]) {
        const filtered = FeatureTabs.spendRowsForWindow(rows, days);
        assert.equal(filtered.length, 1);
        assert.equal(filtered[0].tokens, total);
        assert.equal(filtered[0].cost, null);
        assert.equal(filtered[0].costAvailable, false);
        assert.equal(FeatureTabs.spendAllTotal(filtered), 0);
    }
    assert.equal(rows[0].dailyTokens.length, 3);
});

test("daily token history works without a lifetime total and malformed activity creates no row", () => {
    const rows = FeatureTabs.spendProviderRows([provider("antigravity", {
        dailySeries: [], dailyTokens: [{ date: dateDaysAgo(0), total: 500 }],
    })], {});
    assert.equal(rows.length, 1);
    assert.equal(rows[0].tokens, 500);
    for (const totalTokens of [0, -1, NaN, Infinity, "500", null, true]) {
        assert.equal(FeatureTabs.spendProviderRows([provider("antigravity", { totalTokens })], {}).length, 0);
    }
});

test("token-only rows do not duplicate a priced local source or hide valid paid providers", () => {
    const localSpend = { estimated: { costStatus: "exact", providers: {
        claude: { costStatus: "exact", costUSD: 2, dailyTokens: [{ date: dateDaysAgo(0), total: 700 }] },
    } } };
    const providers = [provider("claude", { totalTokens: 700 }), { id: "openrouter", details: { usageUSD: 4 } }];
    const rows = FeatureTabs.spendProviderRows(providers, localSpend);
    assert.deepEqual(Array.from(rows, (row) => row.id), ["openrouter"]);
    const combined = rows.concat(FeatureTabs.localSpendRows(localSpend, rows, providers));
    assert.equal(combined.length, 2);
    assert.equal(FeatureTabs.spendAllTotal(combined), 6);
});

test("a paid row lacking daily costs retains a filtered token timeline", () => {
    const rows = [{ id: "paid", cost: 3, dailyCost: [], dailyTokens: [
        { date: dateDaysAgo(0), total: 20 }, { date: dateDaysAgo(40), total: 80 },
    ] }];
    const filtered = FeatureTabs.spendRowsForWindow(rows, 7)[0];
    assert.equal(filtered.costAvailable, false);
    assert.deepEqual(Array.from(filtered.dailyTokens, (point) => point.total), [20]);
});

test("message and request series are never presented as token history", () => {
    const antigravity = provider("antigravity", {
        totalTokens: 500, dailyUnit: "messages",
        dailySeries: [{ date: dateDaysAgo(0), total: 12 }],
        dailyTokens: [{ date: dateDaysAgo(0), total: 500 }],
    });
    const rows = FeatureTabs.spendProviderRows([antigravity], {});
    assert.deepEqual(Array.from(rows[0].dailyTokens, (point) => point.total), [500]);
    const byProvider = FeatureTabs.dailyTokensByProvider([antigravity]);
    assert.deepEqual(Array.from(byProvider.antigravity, (point) => point.total), [500]);
    const kiro = provider("kiro", { totalTokens: 0, dailyUnit: "requests", dailySeries: [{ date: dateDaysAgo(0), total: 20 }] });
    assert.equal(FeatureTabs.spendProviderRows([kiro], {}).length, 0);
});

test("an unpriced OpenCode card cannot suppress its separately priced local ledger", () => {
    const localSpend = { estimated: { costStatus: "exact", providers: {
        "opencode::opencode": { source: "opencode", costStatus: "exact", costUSD: 3 },
    } } };
    const providers = [provider("opencode", { totalTokens: 1000 })];
    const rows = FeatureTabs.spendProviderRows(providers, localSpend);
    const localRows = FeatureTabs.localSpendRows(localSpend, rows, providers);
    assert.equal(FeatureTabs.spendAllTotal(rows.concat(localRows)), 3);
    assert.equal(localRows.length, 1);
});

test("the OpenCode provider price explicitly identifies its Zen/Go-only scope", () => {
    const rows = FeatureTabs.spendProviderRows([{ id: "opencode", label: "OpenCode", details: { stats: { totalCostUSD: 0.1 } } }], {});
    assert.equal(rows[0].label, "OpenCode Zen/Go");
    assert.equal(rows[0].cost, 0.1);
});
