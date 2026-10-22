const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../ui/AppState.qml"), "utf8");

function state() {
    const root = {
        providers: [], settingsLoaded: false, pendingLastSnapshot: "", liveSnapshotSeen: false,
        settings: { providers: { cursor: false, ollama: true }, providerDefaultsApplied: true },
        activeId: "", lastProviderId: "", backend: null,
        normalizeSettings: (value) => value, initializeProviderDefaults() {},
        publishTray() {}, recordHistory() {}, language: "",
    };
    const backend = { firstRun: false, requestCatalog() {} };
    const FeatureTabs = { enabledFeatureTabs: () => [] };
    for (const name of ["applyLoadedSettings", "applySnapshot", "applyLastSnapshot"]) {
        const start = source.indexOf(`    function ${name}(`);
        const end = source.indexOf("\n    }", start);
        root[name] = vm.runInNewContext(`(${source.slice(start, end + 6)})`, { root, backend, FeatureTabs });
    }
    root.providerEnabled = (id) => root.settings.providers[id] === true;
    root.providerById = (id) => root.providers.find((provider) => provider.id === id) || null;
    root.i18n = (text) => text;
    return root;
}

const snapshot = JSON.stringify({ active: "cursor", providers: [{ id: "cursor" }, { id: "ollama" }], updatedAt: 1 });

test("cached replay waits for settings and never restores disabled providers", () => {
    const root = state();
    root.applyLastSnapshot(snapshot);
    assert.equal(root.providers.length, 0);
    root.applyLoadedSettings(JSON.stringify(root.settings));
    assert.deepEqual(Array.from(root.providers, (provider) => provider.id), ["ollama"]);
    assert.equal(root.activeId, "ollama");
    assert.equal(root.lastProviderId, "ollama");
});

test("a stale live response cannot reintroduce a provider disabled during the request", () => {
    const root = state();
    root.settingsLoaded = true;
    root.applySnapshot(snapshot);
    assert.deepEqual(Array.from(root.providers, (provider) => provider.id), ["ollama"]);
    assert.equal(root.activeId, "ollama");
});

test("settings changes remove disabled cards before a delayed refresh finishes", () => {
    const root = state();
    root.settingsLoaded = true;
    root.providers = [{ id: "cursor" }, { id: "ollama" }];
    const start = source.indexOf("    onSettingsChanged:");
    const next = source.indexOf("\n    readonly property", start);
    const body = source.slice(start, next).replace(/^\s*onSettingsChanged:\s*/, "");
    vm.runInNewContext(body, { root });
    assert.deepEqual(Array.from(root.providers, (provider) => provider.id), ["ollama"]);
});
