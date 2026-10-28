const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..");
const host = path.join(root, "hosts", "quickshell", "plugin");
const read = (file) => fs.readFileSync(path.join(host, file), "utf8");

test("host dependencies do not bind shared component properties to themselves", () => {
    const widget = read("AiUsagePlugin.qml");
    assert.doesNotMatch(widget, /\bshell:\s*shell\b/);
    assert.doesNotMatch(widget, /\brunner:\s*runner\b/);
    assert.doesNotMatch(widget, /root\.popoutHeight/);
});

test("the manifest points at the plugin component, and the README copies every file it needs", () => {
    const manifest = JSON.parse(read("plugin.json"));
    const readme = read("README.md");
    assert.equal(manifest.id, "tidesi");
    assert.ok(fs.existsSync(path.join(host, manifest.component)));
    // The components the plugin uses beside the shared ui/ must be in the cp list.
    assert.match(read("AiUsagePlugin.qml"), /ProcessRunner\s*\{/);
    for (const file of ["AiUsagePlugin.qml", "PluginAppState.qml", "plugin.json", "ProcessRunner.qml"])
        assert.ok(readme.includes(file), `${file} missing from the README copy list`);
    assert.ok(fs.existsSync(path.join(root, "hosts", "quickshell", "ProcessRunner.qml")));
});

test("the bar opens the plugin popup without a DMS popout frame", () => {
    const widget = read("AiUsagePlugin.qml");
    assert.match(widget, /pillClickAction:/);
    assert.match(widget, /Shortcut\s*\{\s*sequence:\s*"Escape"/);
    assert.doesNotMatch(widget, /\bPopoutComponent\s*\{|\bpopoutContent\s*:/);
});
