const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const host = path.join(__dirname, "..", "hosts", "quickshell", "plugin");

test("host dependencies do not bind shared component properties to themselves", () => {
    const widget = fs.readFileSync(path.join(host, "AiUsagePlugin.qml"), "utf8");
    assert.doesNotMatch(widget, /\bshell:\s*shell\b/);
    assert.doesNotMatch(widget, /\brunner:\s*runner\b/);
    assert.doesNotMatch(widget, /root\.popoutHeight/);
});

test("Quickshell PluginComponent host uses the shared UI and exposes its supported features", () => {
    const manifest = JSON.parse(fs.readFileSync(path.join(host, "plugin.json"), "utf8"));
    const widget = fs.readFileSync(path.join(host, "AiUsagePlugin.qml"), "utf8");
    const appState = fs.readFileSync(path.join(host, "PluginAppState.qml"), "utf8");

    assert.equal(manifest.id, "tidesi");
    assert.equal(manifest.component, "./AiUsagePlugin.qml");
    assert.match(widget, /import "\.\/ui" as Upstream/);
    assert.match(widget, /Upstream\.CommandBackend\s*\{/);
    assert.match(widget, /toolsDir:\s*root\.pluginDir\s*\+\s*"\/backend\/sh"/);
    assert.match(widget, /Upstream\.PopupHeader\s*\{/);
    assert.match(widget, /width:\s*Math\.min\(appState\.popupWidth/);
    assert.match(widget, /Upstream\.PopupBackground\s*\{/);
    assert.match(widget, /Upstream\.PopupContent\s*\{/);
    assert.match(widget, /Upstream\.PopupScrollBar\s*\{/);
    assert.match(widget, /Upstream\.ScrollFade\s*\{/);
    assert.match(widget, /interpreterControls:\s*true/);
    assert.match(appState, /Upstream\.AppState\s*\{/);
    assert.match(appState, /groups:\s*root\.panelGroups/);
});

test("the bar opens the plugin popup without a DMS popout frame", () => {
    const widget = fs.readFileSync(path.join(host, "AiUsagePlugin.qml"), "utf8");
    assert.match(widget, /PanelWindow\s*\{/);
    assert.match(widget, /WlrLayershell\.keyboardFocus:.*WlrKeyboardFocus\.Exclusive/);
    assert.match(widget, /anchors\s*\{\s*top:\s*true;?\s*bottom:\s*true;?\s*left:\s*true;?\s*right:\s*true\s*\}/);
    assert.match(widget, /Shortcut\s*\{\s*sequence:\s*"Escape"/);
    assert.match(widget, /visible:\s*appState\.popupOpen/);
    assert.match(widget, /pillClickAction:/);
    assert.doesNotMatch(widget, /\bPopoutComponent\s*\{|\bpopoutContent\s*:/);
});
