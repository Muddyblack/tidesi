const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

test("panel pill renders at its intrinsic dimensions", () => {
    const source = fs.readFileSync(path.join(__dirname, "..", "ui/PanelPill.qml"), "utf8");
    assert.match(source, /implicitWidth:\s*Math\.max\(96,\s*row\.implicitWidth\s*\+\s*20\)/);
    assert.match(source, /width:\s*implicitWidth/);
    assert.match(source, /height:\s*implicitHeight/);
});
