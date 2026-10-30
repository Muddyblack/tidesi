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

test("local Zed thread tokens appear in Usage & Spend without invented costs", () => {
    const localSpend = { usageOnly: { providers: { zed: {
        tokens: 1234, dailyTokens: [{ date: dateDaysAgo(0), total: 734 }, { date: dateDaysAgo(3), total: 500 }],
    } } } };
    const rows = FeatureTabs.localSpendRows(localSpend, [], []);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].id, "session-usage-zed");
    assert.equal(rows[0].label, "Zed Editor");
    assert.equal(rows[0].cost, null);
    assert.equal(rows[0].costAvailable, false);
    assert.equal(rows[0].usageOnly, true);
    assert.equal(rows[0].tokens, 1234);
    assert.equal(FeatureTabs.spendAllTotal(rows, "USD"), 0);
    assert.equal(FeatureTabs.spendMeteredTotal(rows, "USD"), 0);
    assert.equal(FeatureTabs.spendPlanTotal(rows, "USD"), 0);

    const filtered = FeatureTabs.spendRowsForWindow(rows, 1);
    assert.equal(filtered[0].tokens, 734);
    assert.equal(filtered[0].cost, null);
});

test("Zed token-only rows use upstream providers without hiding against provider cards", () => {
    const localSpend = { usageOnly: { providers: {
        "zed.dev::zed": {
            provider: "zed.dev", source: "zed", tokens: 1200,
            dailyTokens: [{ date: dateDaysAgo(0), total: 1200 }],
        },
        "another-provider::zed": {
            provider: "another-provider", source: "zed", tokens: 800,
            dailyTokens: [{ date: dateDaysAgo(1), total: 800 }],
        },
        "future-provider::zed": {
            provider: "future-provider", model: "future-model-v1", source: "zed", tokens: 450,
            dailyTokens: [{ date: dateDaysAgo(2), total: 450 }],
        },
        "openai::zed": {
            provider: "openai", model: "gpt-4.1", source: "zed", tokens: 300,
            dailyTokens: [{ date: dateDaysAgo(3), total: 300 }],
        },
        "anthropic::zed": {
            provider: "anthropic", model: "claude-sonnet-4-5", source: "zed", tokens: 200,
            dailyTokens: [{ date: dateDaysAgo(4), total: 200 }],
        },
        "google::zed": {
            provider: "google", model: "gemini-2.5-pro", source: "zed", tokens: 100,
            dailyTokens: [{ date: dateDaysAgo(5), total: 100 }],
        },
    } } };
    const providers = [provider("openai", {
        totalTokens: 800, dailySeries: [{ date: dateDaysAgo(1), total: 800 }],
    })];
    const providerRows = FeatureTabs.spendProviderRows(providers, localSpend);
    assert.deepEqual(Array.from(providerRows[0].dailyTokens, point => point.total), [800]);
    const rows = FeatureTabs.localSpendRows(localSpend, providerRows, providers);

    assert.deepEqual(Array.from(rows, row => [row.label, row.source, row.tokens, row.cost]), [
        ["Zed", "zed", 1200, null],
        ["Another Provider", "zed", 800, null],
        ["Future Provider", "zed", 450, null],
        ["OpenAI", "zed", 300, null],
        ["Anthropic", "zed", 200, null],
        ["Google", "zed", 100, null],
    ]);
    assert.equal(FeatureTabs.spendAllTotal(rows, "USD"), 0);
    assert.equal(FeatureTabs.spendRowsForWindow(rows, 1)[0].tokens, 1200);
});

test("Zed session usage rows show tokens without presenting local values as money", () => {
    const localSpend = {
        usageOnly: { providers: {
            "openai::zed": { provider: "openai", source: "zed", tokens: 800, dailyTokens: [{ date: dateDaysAgo(1), total: 800 }] },
            "google::zed": { provider: "google", source: "zed", tokens: 550, dailyTokens: [{ date: dateDaysAgo(2), total: 550 }] },
        } },
    };
    const rows = FeatureTabs.localSpendRows(localSpend, [], []);

    assert.deepEqual(Array.from(rows, row => [row.label, row.cost, row.usageOnly]), [
        ["OpenAI", null, true],
        ["Google", null, true],
    ]);
    assert.deepEqual(Array.from(rows, row => row.tokens), [800, 550]);
    assert.equal(FeatureTabs.spendAllTotal(rows, "USD"), 0);
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

test("zero-cost local OpenCode rollups stay priced without a duplicate token-only row", () => {
    const localSpend = { estimated: { costStatus: "exact", providers: {
        "opencode::opencode": { source: "opencode", costStatus: "exact", costUSD: 0,
            dailyUSD: [{ date: dateDaysAgo(0), usd: 0 }], dailyTokens: [{ date: dateDaysAgo(0), total: 1000 }] },
    } } };
    const providers = [provider("opencode", { totalTokens: 1000, dailyTokens: [{ date: dateDaysAgo(0), total: 1000 }] })];
    const providerRows = FeatureTabs.spendProviderRows(providers, localSpend);
    assert.equal(providerRows.length, 0);
    const localRows = FeatureTabs.localSpendRows(localSpend, providerRows, providers);
    assert.equal(localRows.length, 1);
    assert.equal(localRows[0].id, "local-opencode-opencode");
    assert.equal(localRows[0].cost, 0);
    assert.equal(localRows[0].costAvailable, true);
    assert.deepEqual(Array.from(localRows[0].dailyCost, (point) => point.usd), [0]);
    assert.deepEqual(Array.from(localRows[0].dailyTokens, (point) => point.total), [1000]);
    const filtered = FeatureTabs.spendRowsForWindow(localRows, 1);
    assert.equal(filtered.length, 1);
    assert.equal(filtered[0].cost, 0);
    assert.equal(filtered[0].costAvailable, true);
    assert.deepEqual(Array.from(filtered[0].dailyTokens, (point) => point.total), [1000]);
});

test("zero-cost partial subscription rollups remain priced plan rows", () => {
    const localSpend = { subscription: { costStatus: "partial", providers: {
        "opencode::opencode": { source: "opencode", costStatus: "partial", costUSD: 0,
            dailyUSD: [{ date: dateDaysAgo(0), usd: 0 }], dailyTokens: [{ date: dateDaysAgo(0), total: 250 }] },
    } } };
    const rows = FeatureTabs.localSpendRows(localSpend, [], []);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].billing, "subscription");
    assert.equal(rows[0].cost, 0);
    assert.equal(rows[0].costStatus, "partial");
    assert.deepEqual(Array.from(rows[0].dailyTokens, (point) => point.total), [250]);
});

test("the OpenCode provider price explicitly identifies its Zen/Go-only scope", () => {
    const rows = FeatureTabs.spendProviderRows([{ id: "opencode", label: "OpenCode", details: { stats: { totalCostUSD: 0.1 } } }], {});
    assert.equal(rows[0].label, "OpenCode Zen/Go");
    assert.equal(rows[0].cost, 0.1);
});

test("a tokens-only row with no tokens in the selected timeframe is hidden", () => {
    const row = { id: "cline", label: "Cline", cost: null, costAvailable: false, usageOnly: true,
        tokens: 500, dailyCost: [], dailyTokens: [{ date: dateDaysAgo(90), total: 500 }] };
    assert.equal(FeatureTabs.spendRowsForWindow([row], 30).length, 0);
    assert.equal(FeatureTabs.spendRowsForWindow([row], "all").length, 1);
    row.dailyTokens.push({ date: dateDaysAgo(2), total: 40 });
    assert.equal(FeatureTabs.spendRowsForWindow([row], 30)[0].tokens, 40);
});
