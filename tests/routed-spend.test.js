const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const FeatureTabs = {};
vm.runInNewContext(fs.readFileSync(require("node:path").join(__dirname, "../ui/js/FeatureTabs.js"), "utf8").replace(/^\.pragma library\s*/, ""), FeatureTabs);

test("routed provider rows keep Zed and OpenCode token histories separate", () => {
    // Given: the same upstream provider is used through two independent tools.
    const entry = (source, tokens, costUSD) => ({
        source, costUSD, costStatus: "exact",
        dailyUSD: [{ date: "2026-10-28", usd: costUSD }],
        dailyTokens: [{ date: "2026-10-28", total: tokens }],
    });
    const localSpend = {
        estimated: { costStatus: "exact", providers: { "openai::opencode": entry("opencode", 100, 1) } },
        usageOnly: { providers: { "openai::zed": { provider: "openai", source: "zed", tokens: 50, dailyTokens: [{ date: "2026-10-28", total: 50 }] } } },
    };
    // When: Usage & Spend builds its provider rows.
    const rows = FeatureTabs.localSpendRows(localSpend, [], []);
    // Then: unknown Zed usage is retained without inflating OpenCode activity.
    assert.equal(rows.length, 2);
    assert.equal(rows.find(row => row.source === "opencode").dailyTokens[0].total, 100);
    assert.equal(rows.find(row => row.source === "zed").dailyTokens[0].total, 50);
    assert.equal(rows.find(row => row.source === "zed").label, "OpenAI");
});

test("old token-only activity is hidden outside the selected timeframe but retained in All", () => {
    const rows = FeatureTabs.localSpendRows({ usageOnly: { providers: {
        "ollama::opencode": { provider: "ollama", source: "opencode", tokens: 123,
            dailyTokens: [{ date: "2020-01-01", total: 123 }] },
    } } }, [], []);
    assert.equal(FeatureTabs.spendRowsForWindow(rows, 30).length, 0);
    assert.equal(FeatureTabs.spendRowsForWindow(rows, 0)[0].tokens, 123);
});

test("an unattributed OpenCode session is not labeled as Zen", () => {
    const rows = FeatureTabs.localSpendRows({ usageOnly: { providers: {
        opencode: { tokens: 123, dailyTokens: [{ date: "2020-01-01", total: 123 }] },
    } } }, [], []);
    assert.equal(rows[0].label, "OpenCode");
    assert.equal(rows[0].cost, null);
});
