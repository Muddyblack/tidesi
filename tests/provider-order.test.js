const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// ui/js/*.js are QML "pragma library" files: plain JavaScript once the pragma
// is dropped, loaded here the way the other shared-code tests do.
const source = fs.readFileSync(path.join(__dirname, "..", "ui", "js", "ProviderOrder.js"), "utf8").replace(/^\.pragma library/m, "");
const Order = vm.runInNewContext(`${source}; ({ sort, merge, move })`);

// Results come from another realm, so compare them as plain JSON.
const plain = (value) => JSON.parse(JSON.stringify(value));
const ids = (items) => plain(items).map((item) => item.id);
const items = (...names) => names.map((id) => ({ id }));
const canonical = items("claude", "openai", "kiro", "zed");

test("with no saved order providers keep the canonical order", () => {
    assert.deepEqual(ids(Order.sort(items("zed", "claude", "kiro"), undefined, canonical)), ["claude", "kiro", "zed"]);
    assert.deepEqual(ids(Order.sort(items("zed", "claude"), [], canonical)), ["claude", "zed"]);
});

test("a saved order wins, and providers it never placed follow in canonical order", () => {
    assert.deepEqual(ids(Order.sort(items("claude", "openai", "kiro", "zed"), ["zed", "kiro"], canonical)), ["zed", "kiro", "claude", "openai"]);
});

test("ids missing from the data or the canonical list are tolerated", () => {
    assert.deepEqual(ids(Order.sort(items("claude", "kiro"), ["gone", "kiro"], canonical)), ["kiro", "claude"]);
    assert.deepEqual(ids(Order.sort(items("mystery", "claude"), null, canonical)), ["claude", "mystery"]);
    assert.deepEqual(ids(Order.sort(items("b", "a"), null, [])), ["b", "a"]);
});

test("sorting returns a new array and leaves its input alone", () => {
    const input = items("kiro", "claude");
    const sorted = Order.sort(input, ["claude", "kiro"], canonical);
    assert.deepEqual(ids(input), ["kiro", "claude"]);
    assert.notEqual(sorted, input);
});

test("merging keeps the place of providers that are not on screen", () => {
    assert.deepEqual(plain(Order.merge(["claude", "zed", "kiro"], ["kiro", "claude"])), ["kiro", "claude", "zed"]);
    assert.deepEqual(plain(Order.merge(undefined, ["openai", "claude"])), ["openai", "claude"]);
});

test("moving an entry shifts the ones between and ignores impossible moves", () => {
    assert.deepEqual(plain(Order.move(["a", "b", "c", "d"], 0, 2)), ["b", "c", "a", "d"]);
    assert.deepEqual(plain(Order.move(["a", "b", "c", "d"], 3, 1)), ["a", "d", "b", "c"]);
    assert.deepEqual(plain(Order.move(["a", "b"], 0, 5)), ["a", "b"]);
    assert.deepEqual(plain(Order.move(["a", "b"], 1, 1)), ["a", "b"]);
});
