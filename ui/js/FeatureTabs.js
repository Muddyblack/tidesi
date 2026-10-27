.pragma library

// Optional popup views that sit ahead of provider tabs when enabled —
// Overview, Usage & Spend, and Sessions. Shared by every frontend so the
// toggles, labels and defaults stay in lockstep.

var FEATURE_TAB_IDS = ["overview", "spend", "sessions"];

// Defaults: Sessions on; Overview and Spend off until the user opts in.
function defaultEnabled(id) {
    return id === "sessions";
}

function settingKey(id) {
    return id + "Enabled";
}

function label(id, i18nFn) {
    if (id === "overview")
        return i18nFn ? i18nFn("Overview") : "Overview";
    if (id === "spend")
        return i18nFn ? i18nFn("Usage & Spend") : "Usage & Spend";
    if (id === "sessions")
        return i18nFn ? i18nFn("Sessions") : "Sessions";
    return id;
}

function isFeatureTab(id) {
    return FEATURE_TAB_IDS.indexOf(id) !== -1;
}

// Whether a feature tab is on, given a settings/config object that stores
// top-level booleans named overviewEnabled / spendEnabled / sessionsEnabled.
function enabled(settings, id) {
    if (!isFeatureTab(id))
        return false;
    var key = settingKey(id);
    if (settings && settings[key] !== undefined && settings[key] !== null)
        return settings[key] === true;
    return defaultEnabled(id);
}

// Plasma configuration uses the same key names as the shared JSON file.
function plasmoidEnabled(configuration, id) {
    if (!isFeatureTab(id))
        return false;
    var key = settingKey(id);
    if (configuration && configuration[key] !== undefined)
        return configuration[key] === true;
    return defaultEnabled(id);
}

function enabledFeatureTabs(settings) {
    var out = [];
    for (var i = 0; i < FEATURE_TAB_IDS.length; i++) {
        var id = FEATURE_TAB_IDS[i];
        if (enabled(settings, id))
            out.push(id);
    }
    return out;
}

function enabledFeatureTabsPlasmoid(configuration) {
    var out = [];
    for (var i = 0; i < FEATURE_TAB_IDS.length; i++) {
        var id = FEATURE_TAB_IDS[i];
        if (plasmoidEnabled(configuration, id))
            out.push(id);
    }
    return out;
}

// Accent used when a feature tab is active (theme highlight falls through
// on the caller when this returns "").
function accent(id) {
    if (id === "overview")
        return "#38bdf8";
    if (id === "spend")
        return "#34d399";
    if (id === "sessions")
        return "#a78bfa";
    return "";
}

function localSourceKey(source) {
    if (typeof source !== "string")
        return "";
    return source.trim().toLowerCase().replace(/_/g, "-");
}

function localSourceLabel(source) {
    var key = localSourceKey(source);
    var labels = {
        opencode: "OpenCode",
        mimo: "MiMo Code",
        junie: "Junie",
        pi: "Pi",
        claude: "Claude Code",
        "claude-code": "Claude Code",
        openai: "Codex",
        codex: "Codex",
        cline: "Cline",
        grok: "Grok CLI",
        "grok-cli": "Grok CLI",
        muse: "Muse",
        antigravity: "Antigravity"
    };
    if (labels[key] !== undefined)
        return labels[key];
    var fallback = key.replace(/-+/g, " ").replace(/\s+/g, " ").trim();
    if (!fallback)
        return "Local source";
    return fallback.replace(/(^|\s)\S/g, function (letter) { return letter.toUpperCase(); });
}

function upstreamProviderLabel(provider) {
    var labels = {
        anthropic: "Anthropic",
        openai: "OpenAI",
        openrouter: "OpenRouter",
        "ollama-cloud": "Ollama Cloud",
        ollama: "Ollama",
        "github-copilot": "GitHub Copilot",
        google: "Google",
        zenmux: "ZenMux",
        opencode: "OpenCode Zen",
        "opencode-go": "OpenCode Go",
    };
    return labels[localSourceKey(provider)] || localSourceLabel(provider);
}

function providerAccent(provider) {
    var colors = {
        anthropic: "#cc785c",
        claude: "#cc785c",
        antigravity: "#4285f4",
        openai: "#10a37f",
        kiro: "#8b5cf6",
        mistral: "#ff7000",
        openrouter: "#9333ea",
        ollama: "#f0f0f0",
        "ollama-cloud": "#f0f0f0",
        selfhosted: "#38bdf8",
        grok: "#e6e6e6",
        zai: "#126ef4",
        copilot: "#8b5cf6",
        deepseek: "#4f8cff",
        kimi: "#1e3a8a",
        muse: "#0064e0",
        cursor: "#e6e6e6",
        cline: "#e6e6e6",
        opencode: "#B7B1B1",
        mimo: "#E8E8E8",
        junie: "#48e054",
        jetbrains: "#e6e6e6",
        windsurf: "#34e8bb",
        pi: "#d4d4d8",
        kilo: "#f4e04d",
        coderabbit: "#ff7a3d",
        zed: "#e6e6e6"
    };
    var key = localSourceKey(provider);
    return colors[key] || "#34d399";
}

function localSpendNote(provenance, costStatus, source) {
    var prefix = source === "mimo" ? "via MiMo Code" : source === "opencode" ? "via OpenCode" : "local CLI logs";
    var notes = {
        "legacy:exact": prefix + " · local value · complete",
        "legacy:partial": prefix + " · partial",
        "actual:exact": prefix + " · recorded · complete",
        "actual:partial": prefix + " · recorded · partial",
        "estimated:exact": prefix + " · estimate · complete",
        "estimated:partial": prefix + " · estimate · partial",
        "mixed:exact": prefix + " · recorded + estimated · complete",
        "mixed:partial": prefix + " · recorded + estimated · partial"
    };
    return notes[provenance + ":" + costStatus] || prefix;
}

// Plan-covered work: the figure is what the API would have charged, not money
// owed, so its note has to say so rather than read like a bill.
function planSpendNote(costStatus, source) {
    var prefix = source === "mimo" ? "via MiMo Code" : source === "opencode" ? "via OpenCode" : "covered by plan";
    return prefix + " · would cost on API" + (costStatus === "partial" ? " · partial" : "");
}

function localProviderCost(group, source) {
    if (!group || (group.costStatus !== "exact" && group.costStatus !== "partial"))
        return null;
    var providers = group.providers || {};
    var entry = null;
    var keys = Object.keys(providers);
    for (var i = 0; i < keys.length; i++) {
        if (localSourceKey(keys[i]) === source) {
            entry = providers[keys[i]];
            break;
        }
    }
    if (!entry || (entry.costStatus !== "exact" && entry.costStatus !== "partial") ||
            typeof entry.costUSD !== "number" || !isFinite(entry.costUSD) || !(entry.costUSD > 0))
        return null;
    return {
        cost: entry.costUSD,
        status: entry.costStatus,
        source: localSourceKey(entry.source),
        daily: entry.dailyUSD || [],
        dailyTokens: entry.dailyTokens || []
    };
}

function localProviderKey(identity) {
    var key = localSourceKey(identity);
    var separator = key.lastIndexOf("::");
    return separator > 0 ? key.slice(0, separator) : key;
}

// Per-day cost for every provider, from the session rows the spend totals
// are summed from (envelope.py's _local_spend_group emits dailyUSD per
// provider). Provider-agnostic by construction: a provider appears here
// because it produced session rows, not because it was special-cased. This
// is also the only daily cost that matches a plan-covered row — a Pro/Max
// plan reports $0 of real API cost, so the "would cost on API" estimate
// lives on the sessions, not in the provider's own usage stats.
// Sum any number of [{date, usd}] series into one sorted series.
function mergeDailySeries() {
    var totals = {};
    for (var a = 0; a < arguments.length; a++) {
        var series = arguments[a];
        if (!series || !series.length)
            continue;
        for (var i = 0; i < series.length; i++) {
            var point = series[i] || {};
            if (!point.date)
                continue;
            var usd = typeof point.usd === "number" && isFinite(point.usd) ? point.usd : 0;
            totals[point.date] = (totals[point.date] || 0) + usd;
        }
    }
    return Object.keys(totals).sort().map(function (date) {
        return { date: date, usd: this[date] };
    }, totals);
}

// Per-day tokens by provider, from the same session rollups the cost comes
// from (envelope.py emits dailyTokens beside dailyUSD). Preferred over a
// provider's own dailySeries because that aggregate often covers a different,
// much older span than the sessions do — plotting the two together drew a
// token line that stopped exactly where the cost line started.
function sessionDailyTokensByProvider(localSpend) {
    var byProvider = {};
    var groups = ["actual", "estimated", "subscription", "subscriptionActual"];
    for (var g = 0; g < groups.length; g++) {
        var group = localSpend && localSpend[groups[g]];
        var providers = group && group.providers;
        if (!providers)
            continue;
        var keys = Object.keys(providers);
        for (var i = 0; i < keys.length; i++) {
            var series = (providers[keys[i]] || {}).dailyTokens;
            if (!series || !series.length)
                continue;
            var key = localProviderKey(keys[i]);
            var totals = byProvider[key] || (byProvider[key] = {});
            for (var j = 0; j < series.length; j++) {
                var point = series[j] || {};
                if (!point.date)
                    continue;
                var total = typeof point.total === "number" && isFinite(point.total) ? point.total : 0;
                totals[point.date] = (totals[point.date] || 0) + total;
            }
        }
    }
    var out = {};
    var providerKeys = Object.keys(byProvider);
    for (var k = 0; k < providerKeys.length; k++) {
        var dates = Object.keys(byProvider[providerKeys[k]]).sort();
        out[providerKeys[k]] = dates.map(function (date) {
            return { date: date, total: this[date] };
        }, byProvider[providerKeys[k]]);
    }
    return out;
}

function dailyCostByProvider(localSpend) {
    var byProvider = {};
    var groups = ["actual", "estimated", "subscription", "subscriptionActual"];
    for (var g = 0; g < groups.length; g++) {
        var group = localSpend && localSpend[groups[g]];
        var providers = group && group.providers;
        if (!providers)
            continue;
        var keys = Object.keys(providers);
        for (var i = 0; i < keys.length; i++) {
            var entry = providers[keys[i]] || {};
            var series = entry.dailyUSD;
            if (!series || !series.length)
                continue;
            var key = localProviderKey(keys[i]);
            var totals = byProvider[key] || (byProvider[key] = {});
            for (var j = 0; j < series.length; j++) {
                var point = series[j] || {};
                if (!point.date)
                    continue;
                var usd = typeof point.usd === "number" && isFinite(point.usd) ? point.usd : 0;
                totals[point.date] = (totals[point.date] || 0) + usd;
            }
        }
    }
    var out = {};
    var providerKeys = Object.keys(byProvider);
    for (var k = 0; k < providerKeys.length; k++) {
        var dates = Object.keys(byProvider[providerKeys[k]]).sort();
        out[providerKeys[k]] = dates.map(function (date) {
            return { date: date, usd: this[date] };
        }, byProvider[providerKeys[k]]);
    }
    return out;
}

// Per-day token counts by provider id, from each provider's own stats blob.
// Session rows carry cost but no token counts, so the two halves of a row's
// chart come from different places: cost from the sessions (which is what
// the totals are summed from), tokens from the provider's dailySeries.
function providerTokenSeries(stats) {
    if (!stats)
        return [];
    var series = stats.dailyTokens && stats.dailyTokens.length ? stats.dailyTokens
        : (!stats.dailyUnit || stats.dailyUnit === "tokens") ? (stats.dailySeries || []) : [];
    return series.filter(function (point) {
        return point && point.date && typeof point.total === "number" && isFinite(point.total) && point.total >= 0;
    });
}

function dailyTokensByProvider(rawProviders) {
    var out = {};
    var list = rawProviders || [];
    for (var i = 0; i < list.length; i++) {
        var p = list[i];
        if (!p || !p.id)
            continue;
        var stats = (p.details && p.details.stats) || {};
        var series = providerTokenSeries(stats);
        if (series && series.length)
            out[p.id] = series;
    }
    return out;
}

function localSpendRows(localSpend, providerRows, rawProviders) {
    var statsTokens = dailyTokensByProvider(rawProviders);
    var sessionTokens = sessionDailyTokensByProvider(localSpend);
    function tokensFor(providerKey) {
        var fromSessions = sessionTokens[providerKey];
        return (fromSessions && fromSessions.length) ? fromSessions : (statsTokens[providerKey] || []);
    }
    var rows = [];
    var hasOpenCodeProvider = false;
    var hasMimoProvider = (providerRows || []).some(function (row) { return row.id === "mimo" && row.costAvailable !== false; });
    for (var providerIndex = 0; providerIndex < (providerRows || []).length; providerIndex++) {
        if ((providerRows[providerIndex] || {}).id === "opencode" && providerRows[providerIndex].costAvailable !== false) {
            hasOpenCodeProvider = true;
            break;
        }
    }
    var sources = {};
    var groups = ["actual", "estimated"];
    for (var i = 0; i < groups.length; i++) {
        var group = localSpend && localSpend[groups[i]];
        var providers = group && group.providers;
        if (!providers)
            continue;
        var sourceIds = Object.keys(providers);
        for (var j = 0; j < sourceIds.length; j++) {
            var sourceKey = localSourceKey(sourceIds[j]);
            if (sourceKey)
                sources[sourceKey] = true;
        }
    }

    var sourceIds = Object.keys(sources);
    for (var k = 0; k < sourceIds.length; k++) {
        var sourceKey = sourceIds[k];
        var actual = localProviderCost(localSpend && localSpend.actual, sourceKey);
        var estimated = localProviderCost(localSpend && localSpend.estimated, sourceKey);
        if (!actual && !estimated)
            continue;

        var actualUSD = actual ? actual.cost : 0;
        var estimatedUSD = estimated ? estimated.cost : 0;
        var source = actual && actual.source || estimated && estimated.source || "";
        var providerKey = localProviderKey(sourceKey);
        // Mistral's provider card and its Vibe session totals read the same
        // ~/.vibe/meta.json costs. Keep the session rows available for the
        // Sessions tab, but show that total once in Usage & Spend.
        if (providerKey === "mistral" && source !== "opencode" && source !== "mimo")
            continue;
        // OpenCode's provider card covers Zen/Go, not the other routed providers.
        if ((hasOpenCodeProvider && source === "opencode" && (providerKey === "opencode" || providerKey === "opencode-go")) || (hasMimoProvider && source === "mimo"))
            continue;
        var hasActual = actualUSD > 0;
        var hasEstimated = estimatedUSD > 0;
        var provenance = hasActual && hasEstimated ? "mixed" : hasActual ? "actual" : "estimated";
        var costStatus = (actual && actual.status === "partial") ||
                (estimated && estimated.status === "partial") ? "partial" : "exact";
        rows.push({
            id: "local-" + (source ? source + "-" : "") + providerKey,
            label: (source === "opencode" || source === "mimo") ? upstreamProviderLabel(providerKey) : localSourceLabel(providerKey),
            cost: actualUSD + estimatedUSD,
            currency: "USD",
            note: localSpendNote(provenance, costStatus, source),
            local: true,
            source: source || providerKey,
            provenance: provenance,
            costStatus: costStatus,
            costBreakdown: { actualUSD: actualUSD, estimatedUSD: estimatedUSD },
            accent: providerAccent(providerKey),
            // Both halves of this row's own total, by day.
            dailyCost: mergeDailySeries(actual && actual.daily, estimated && estimated.daily),
            dailyTokens: tokensFor(providerKey)
        });
    }

    // Subscription usage, kept as its own rows so it is never summed with, or
    // mistaken for, the metered totals above.
    var planSources = {};
    var planGroups = ["subscription", "subscriptionActual"];
    for (var g = 0; g < planGroups.length; g++) {
        var planGroup = localSpend && localSpend[planGroups[g]];
        var planProviders = planGroup && planGroup.providers;
        if (!planProviders)
            continue;
        var planKeys = Object.keys(planProviders);
        for (var n = 0; n < planKeys.length; n++) {
            var planKey = localSourceKey(planKeys[n]);
            if (planKey)
                planSources[planKey] = true;
        }
    }
    var planIds = Object.keys(planSources);
    for (var q = 0; q < planIds.length; q++) {
        var planSourceKey = planIds[q];
        var planEstimated = localProviderCost(localSpend && localSpend.subscription, planSourceKey);
        var planActual = localProviderCost(localSpend && localSpend.subscriptionActual, planSourceKey);
        var planCost = (planEstimated ? planEstimated.cost : 0) + (planActual ? planActual.cost : 0);
        if (!(planCost > 0))
            continue;
        var planSource = (planEstimated && planEstimated.source) || (planActual && planActual.source) || "";
        var planProviderKey = localProviderKey(planSourceKey);
        var planStatus = (planEstimated && planEstimated.status === "partial") ||
                (planActual && planActual.status === "partial") ? "partial" : "exact";
        rows.push({
            id: "plan-" + (planSource ? planSource + "-" : "") + planProviderKey,
            label: (planSource === "opencode" || planSource === "mimo") ? upstreamProviderLabel(planProviderKey) : localSourceLabel(planProviderKey),
            cost: planCost,
            currency: "USD",
            note: planSpendNote(planStatus, planSource),
            local: true,
            billing: "subscription",
            source: planSource || planProviderKey,
            provenance: "estimated",
            costStatus: planStatus,
            accent: providerAccent(planProviderKey),
            dailyCost: mergeDailySeries(planActual && planActual.daily, planEstimated && planEstimated.daily),
            dailyTokens: tokensFor(planProviderKey)
        });
    }

    if (rows.length === 0 && localSpend && !localSpend.actual && !localSpend.estimated &&
            (localSpend.costStatus === "exact" || localSpend.costStatus === "partial") &&
            typeof localSpend.totalUSD === "number" && isFinite(localSpend.totalUSD) &&
            localSpend.totalUSD > 0) {
        rows.push({
            id: "local",
            label: "Local sessions",
            cost: localSpend.totalUSD,
            currency: "USD",
            note: localSpendNote("legacy", localSpend.costStatus, ""),
            local: true,
            legacy: true,
            costStatus: localSpend.costStatus
        });
    }
    return rows;
}

function sessionCostInfo(entry) {
    var status = entry && entry.costStatus;
    var cost = entry && entry.costUSD;
    if ((status !== "exact" && status !== "partial") ||
            typeof cost !== "number" || !isFinite(cost) || cost < 0)
        return { available: false, provenance: "", status: "unavailable", cost: 0, billing: "api" };

    var provenance = entry.costProvenance;
    if (provenance === undefined || provenance === null || provenance === "")
        provenance = "legacy";
    if (provenance !== "actual" && provenance !== "estimated" &&
            provenance !== "mixed" && provenance !== "legacy")
        return { available: false, provenance: "", status: "unavailable", cost: 0 };
    var billing = entry.costBilling === "subscription" ? "subscription" : "api";
    return { available: true, provenance: provenance, status: status, cost: cost, billing: billing };
}

// Build the per-provider cost rows the Spend tab shows from a provider list
// that already carries details. Only numbers the backend already exposed.
// `localSpend` is optional and supplies each row's per-day cost history from
// the session rows (see dailyCostByProvider) — the provider blobs themselves
// carry only totals, never a daily cost.
function spendProviderRows(providers, localSpend) {
    var rows = [];
    var list = providers || [];
    var dailyByProvider = dailyCostByProvider(localSpend);
    var sessionTokens = sessionDailyTokensByProvider(localSpend);
    for (var i = 0; i < list.length; i++) {
        var p = list[i] || {};
        var d = p.details || {};
        var cost = 0;
        var currency = "USD";
        var note = "";
        var id = p.id || "";

        if (id === "claude") {
            cost = (d.organizationUsage && d.organizationUsage.totalCostUSD)
                || (d.org && d.org.totalCostUSD) || d.totalCostUSD
                || (d.stats && d.stats.totalCostUSD) || 0;
            note = "30d API";
        } else if (id === "openai") {
            cost = (d.organizationUsage && d.organizationUsage.totalCostUSD)
                || (d.org && d.org.totalCostUSD) || d.totalCostUSD
                || (d.stats && d.stats.totalCostUSD) || 0;
            note = "30d API";
        } else if (id === "openrouter") {
            cost = d.usageUSD || d.usage || 0;
            note = "all-time";
        } else if (id === "mistral") {
            cost = (d.vibe && d.vibe.totalCost) || d.vibeTotalCost || d.totalCost || 0;
            note = "vibe CLI";
        } else if (id === "muse") {
            cost = (d.stats && d.stats.totalCostUSD) || d.totalCostUSD || 0;
            currency = (d.stats && d.stats.currency) || d.currency || "USD";
            note = "local est.";
        } else if (id === "cline") {
            cost = (d.stats && d.stats.totalCostUSD) || d.totalCostUSD || 0;
            note = "local";
        } else if (id === "cursor") {
            cost = (d.stats && d.stats.totalCostUSD) || d.onDemandUsed || d.onDemandSpendUSD || d.onDemand || 0;
            note = (d.stats && d.stats.totalCostUSD) ? "billing cycle" : "on-demand";
        } else if (id === "opencode" || id === "mimo") {
            cost = (d.stats && d.stats.totalCostUSD) || d.totalCostUSD || 0;
            note = "local sessions";
        }

        if (typeof cost !== "number" || !isFinite(cost) || !(cost > 0)) {
            var stats = d.stats || {};
            var tokenSeries = providerTokenSeries(stats);
            var tokens = typeof stats.totalTokens === "number" && isFinite(stats.totalTokens) && stats.totalTokens > 0
                ? stats.totalTokens : tokenSeries.reduce(function (sum, point) { return sum + point.total; }, 0);
            var hasLocalCost = ["actual", "estimated", "subscription", "subscriptionActual"].some(function (group) {
                return !!localProviderCost(localSpend && localSpend[group], id);
            });
            if (tokens > 0 && !hasLocalCost) {
                rows.push({
                    id: id, label: p.label || id, accent: p.accent || providerAccent(id), icon: p.icon || "",
                    cost: null, costAvailable: false, usageOnly: true, currency: "USD",
                    note: "Cost unavailable", tokens: tokens, dailyCost: [], dailyTokens: tokenSeries
                });
            }
            continue;
        }
        rows.push({
            id: id,
            label: id === "opencode" ? "OpenCode Zen/Go" : p.label || id,
            accent: p.accent || providerAccent(id),
            icon: p.icon || "",
            cost: cost,
            currency: currency,
            note: note,
            // For the per-row expandable timeline chart: cost by day from the
            // session rows, tokens by day from the provider's own stats.
            dailyCost: id === "opencode" ? mergeDailySeries(dailyByProvider.opencode, dailyByProvider["opencode-go"]) : (dailyByProvider[id] || []),
            dailyTokens: id === "opencode" ? providerTokenSeries(d.stats) : (sessionTokens[id] && sessionTokens[id].length)
                ? sessionTokens[id]
                : providerTokenSeries(d.stats)
        });
    }
    rows.sort(function (a, b) {
        return b.cost - a.cost;
    });
    return rows;
}

// Zip one provider's own dailyCost/dailyTokens series into one chart-ready
// timeline, optionally trimmed to the trailing `windowDays` (0/undefined =
// everything). Deliberately per-provider, not merged across providers:
// providers report on wildly different ranges (a 30-day API window vs.
// all-time local logs), so summing them produced a chart that was mostly
// flat with one misleading spike.
function spendTimeline(costSeries, tokenSeries, windowDays) {
    var byDate = {};
    var costs = costSeries || [];
    var tokens = tokenSeries || [];
    var costFrom = "";
    var costTo = "";
    for (var i = 0; i < costs.length; i++) {
        var c = costs[i] || {};
        if (!c.date)
            continue;
        if (costFrom === "" || c.date < costFrom)
            costFrom = c.date;
        if (costTo === "" || c.date > costTo)
            costTo = c.date;
        byDate[c.date] = byDate[c.date] || { date: c.date, usd: 0, total: 0 };
        byDate[c.date].usd = typeof c.usd === "number" && isFinite(c.usd) ? c.usd : 0;
    }
    for (var j = 0; j < tokens.length; j++) {
        var t = tokens[j] || {};
        if (!t.date)
            continue;
        // The two halves come from different stores with different retention:
        // cost from the session rows (recent), tokens from the provider's own
        // aggregate (often much older). Plotting the union drew a token line
        // that "stopped" exactly where the cost line began — two disjoint
        // histories glued to one axis. This is the Spend view, so the cost
        // history defines the window and tokens are context inside it.
        if (costFrom !== "" && (t.date < costFrom || t.date > costTo))
            continue;
        byDate[t.date] = byDate[t.date] || { date: t.date, usd: 0, total: 0 };
        byDate[t.date].total = typeof t.total === "number" && isFinite(t.total) ? t.total : 0;
    }
    var points = Object.keys(byDate).sort().map(function (date) {
        return byDate[date];
    });
    if (!windowDays || windowDays <= 0 || points.length === 0)
        return points;
    var now = new Date();
    var today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
    var cutoff = today - (windowDays - 1) * 86400000;
    return points.filter(function (p) {
        var time = new Date(p.date + "T00:00:00Z").getTime();
        return time >= cutoff && time <= today;
    });
}

function spendRowsForWindow(rows, windowDays) {
    var days = Number(windowDays);
    if (!isFinite(days) || days <= 0)
        return rows || [];
    if (days !== 1 && days !== 7 && days !== 30)
        return rows || [];

    var source = rows || [];
    var out = [];
    for (var i = 0; i < source.length; i++) {
        var row = source[i];
        if (!row)
            continue;
        var hasDailyCost = (row.dailyCost || []).some(function (point) {
            return point && point.date && typeof point.usd === "number" && isFinite(point.usd);
        });
        if (!hasDailyCost) {
            var unavailable = Object.assign({}, row);
            unavailable.cost = null;
            unavailable.costAvailable = false;
            unavailable.note = row.usageOnly ? "Cost unavailable" : "Cost unavailable for this timeframe";
            unavailable.dailyTokens = spendTimeline([], row.dailyTokens, days).map(function (point) {
                return { date: point.date, total: point.total };
            });
            if (row.usageOnly)
                unavailable.tokens = unavailable.dailyTokens.length || (row.dailyTokens || []).length
                    ? unavailable.dailyTokens.reduce(function (sum, point) { return sum + point.total; }, 0) : null;
            out.push(unavailable);
            continue;
        }
        var points = spendTimeline(row.dailyCost, row.dailyTokens, days);

        var dailyCost = [];
        var dailyTokens = [];
        var cost = 0;
        for (var j = 0; j < points.length; j++) {
            var point = points[j];
            var usd = typeof point.usd === "number" && isFinite(point.usd) ? point.usd : 0;
            var total = typeof point.total === "number" && isFinite(point.total) ? point.total : 0;
            dailyCost.push({ date: point.date, usd: usd });
            dailyTokens.push({ date: point.date, total: total });
            cost += usd;
        }

        var filtered = Object.assign({}, row);
        filtered.cost = cost;
        filtered.dailyCost = dailyCost;
        filtered.dailyTokens = dailyTokens;
        out.push(filtered);
    }
    return out;
}

function formatMoney(value, currency) {
    var cur = currency || "USD";
    var num = typeof value === "number" && isFinite(value) ? value : 0;
    var amount = num.toFixed(2);
    if (cur === "USD")
        return "$" + amount;
    if (cur === "CNY")
        return "¥" + amount;
    return amount + (cur ? " " + cur : "");
}

function spendTotal(rows, currency) {
    var sum = 0;
    var cur = currency || "USD";
    for (var i = 0; i < (rows || []).length; i++) {
        if (rows[i].local !== true && (rows[i].currency || "USD") === cur &&
                typeof rows[i].cost === "number" && isFinite(rows[i].cost))
            sum += rows[i].cost;
    }
    return sum;
}

function spendMeteredTotal(rows, currency) {
    var sum = 0;
    var cur = currency || "USD";
    for (var i = 0; i < (rows || []).length; i++) {
        var r = rows[i];
        if (r && r.billing !== "subscription" && (r.currency || "USD") === cur &&
                typeof r.cost === "number" && isFinite(r.cost) && r.cost > 0)
            sum += r.cost;
    }
    return sum;
}

function spendPlanTotal(rows, currency) {
    var sum = 0;
    var cur = currency || "USD";
    for (var i = 0; i < (rows || []).length; i++) {
        var r = rows[i];
        if (r && r.billing === "subscription" && (r.currency || "USD") === cur &&
                typeof r.cost === "number" && isFinite(r.cost) && r.cost > 0)
            sum += r.cost;
    }
    return sum;
}

function spendAllTotal(rows, currency) {
    var sum = 0;
    var cur = currency || "USD";
    for (var i = 0; i < (rows || []).length; i++) {
        var r = rows[i];
        if (r && (r.currency || "USD") === cur &&
                typeof r.cost === "number" && isFinite(r.cost) && r.cost > 0)
            sum += r.cost;
    }
    return sum;
}

function spendSummaryText(rows, currency, i18nFn) {
    var cur = currency || "USD";
    var metered = spendMeteredTotal(rows, cur);
    var plan = spendPlanTotal(rows, cur);
    if (metered <= 0 && plan <= 0)
        return "";

    var t = function (fmt, a, b) {
        if (!i18nFn) {
            var s = fmt;
            if (a !== undefined) s = s.replace("%1", a);
            if (b !== undefined) s = s.replace("%2", b);
            return s;
        }
        return b !== undefined ? i18nFn(fmt, a, b) : (a !== undefined ? i18nFn(fmt, a) : i18nFn(fmt));
    };

    var meteredStr = formatMoney(metered, cur);
    var planStr = formatMoney(plan, cur);

    if (plan > 0 && metered > 0)
        return t("API/local: %1 · Plan equiv.: %2", meteredStr, planStr);
    if (plan > 0)
        return t("Plan equiv.: %1", planStr);
    return t("API/local: %1", meteredStr);
}

function spendSummaryTooltip(rows, currency, i18nFn) {
    var cur = currency || "USD";
    var metered = spendMeteredTotal(rows, cur);
    var plan = spendPlanTotal(rows, cur);
    if (metered <= 0 && plan <= 0)
        return "";

    var t = function (fmt, a) {
        if (!i18nFn) return a !== undefined ? fmt.replace("%1", a) : fmt;
        return a !== undefined ? i18nFn(fmt, a) : i18nFn(fmt);
    };

    var lines = [];
    if (metered > 0)
        lines.push(t("API/local recorded and estimated value (not a bill): %1", formatMoney(metered, cur)));
    if (plan > 0)
        lines.push(t("Plan-covered API-equivalent estimate: %1", formatMoney(plan, cur)));
    if (metered > 0 && plan > 0)
        lines.push(t("Combined work value (not a bill): %1", formatMoney(metered + plan, cur)));
    return lines.join("\n");
}

// ── Model rate table ───────────────────────────────────────────────────────
// Shared by every QML frontend's Spend view. The widgets differ (Plasma
// components vs. plain QtQuick) and so does the process plumbing (Plasma5Support
// DataSource vs. Quickshell Process), but the formatting, paging arithmetic and
// payload parsing are identical — so they live here rather than in each copy.

function rateText(value) {
    if (typeof value !== "number" || !isFinite(value))
        return "\u2014";
    if (value === 0)
        return "free";
    return "$" + (value < 1 ? value.toFixed(3) : value.toFixed(2));
}

// The catalog is cached for a week, so "is this current?" is the first question
// the table has to answer. nowMs is injectable so tests need no clock stubbing.
function rateAge(seconds, i18nFn, nowMs) {
    function t(text, arg) {
        if (!i18nFn)
            return text.replace("%1", arg);
        return arg === undefined ? i18nFn(text) : i18nFn(text, arg);
    }
    if (typeof seconds !== "number" || !isFinite(seconds) || !(seconds > 0))
        return "";
    var now = (typeof nowMs === "number" && isFinite(nowMs)) ? nowMs : new Date().getTime();
    var s = Math.max(0, (now / 1000) - seconds);
    if (s < 3600)
        return t("updated just now");
    if (s < 86400)
        return t("updated %1h ago", Math.floor(s / 3600));
    if (s < 604800)
        return t("updated %1d ago", Math.floor(s / 86400));
    return t("updated %1w ago", Math.floor(s / 604800));
}

function ratePageCount(total, limit) {
    var size = Math.max(1, Number(limit) || 1);
    return Math.max(1, Math.ceil((Number(total) || 0) / size));
}

function ratePageNumber(offset, limit, total) {
    var size = Math.max(1, Number(limit) || 1);
    var at = Math.max(0, Number(offset) || 0);
    return Math.min(ratePageCount(total, size), Math.floor(at / size) + 1);
}

// Parses --pricing-table stdout. Returns null when the output is unusable, so
// each frontend can show its own "could not read" string.
function parseRateTable(text) {
    var out = (text || "").trim();
    if (out === "")
        return null;
    var payload;
    try {
        payload = JSON.parse(out);
    } catch (e) {
        return null;
    }
    if (!payload || typeof payload !== "object")
        return null;
    return {
        rows: Array.isArray(payload.rows) ? payload.rows : [],
        total: Number(payload.total) || 0,
        unit: payload.unit || "",
        fetchedAt: Number(payload.fetchedAt) || 0,
        error: payload.error || ""
    };
}
