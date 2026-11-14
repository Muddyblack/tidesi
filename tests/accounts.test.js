const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// ui/js/*.js are QML "pragma library" files: plain JavaScript once the pragma
// is dropped, loaded here the way the other shared-code tests do.
const load = (name, exports) => {
    const source = fs.readFileSync(path.join(__dirname, "..", "ui", "js", name), "utf8").replace(/^\.pragma library/m, "");
    return vm.runInNewContext(`${source}; ({ ${exports} })`);
};
const Accounts = load("Accounts.js", "HOMES, baseId, isAccount, supports, list, slug, defaultHome, loginCommand, add, remove, update");
const Registry = load("ProviderRegistry.js", "enabled");
const Order = load("ProviderOrder.js", "sort");
const plain = (value) => JSON.parse(JSON.stringify(value));

test("an account id belongs to its provider", () => {
    assert.equal(Accounts.baseId("claude@work"), "claude");
    assert.equal(Accounts.baseId("claude"), "claude");
    assert.equal(Accounts.isAccount("claude@work"), true);
    assert.equal(Accounts.isAccount("claude"), false);
});

test("only providers with a login folder can have accounts", () => {
    assert.equal(Accounts.supports("claude"), true);
    assert.equal(Accounts.supports("openrouter"), false);
    assert.equal(Accounts.supports("hasOwnProperty"), false);
});

test("the backend and the settings page agree on which variable each provider uses", () => {
    const py = fs.readFileSync(path.join(__dirname, "..", "backend", "tidesi", "accounts.py"), "utf8");
    const backend = Object.fromEntries([...py.matchAll(/^\s+"(\w+)": "([A-Z_]+)",$/gm)].map((m) => [m[1], m[2]]));
    const page = Object.fromEntries(Object.entries(plain(Accounts.HOMES)).map(([id, spec]) => [id, spec.env]));
    assert.deepEqual(page, backend);
});

test("adding an account names it, picks a folder and a unique key", () => {
    let result = Accounts.add({ providers: { claude: true } }, "claude", "  Work  ", "");
    assert.equal(result.id, "claude@work");
    assert.deepEqual(plain(result.settings.accounts.claude), [{ id: "work", label: "Work", home: "~/.claude-work" }]);
    assert.deepEqual(plain(result.settings.providers), { claude: true });

    result = Accounts.add(result.settings, "claude", "Work", "/srv/claude");
    assert.equal(result.id, "claude@work-2");
    assert.equal(plain(result.settings.accounts.claude)[1].home, "/srv/claude");
});

test("an account the backend would drop is refused here", () => {
    assert.equal(Accounts.add({}, "claude", "   ", "").error, "name");
    assert.equal(Accounts.add({}, "claude", "!!!", "").error, "name");
    assert.equal(Accounts.add({}, "openrouter", "Work", "").error, "unsupported");
    let settings = {};
    for (let i = 0; i < 8; i++) settings = Accounts.add(settings, "claude", `a${i}`, "").settings;
    assert.equal(Accounts.add(settings, "claude", "one more", "").error, "limit");
});

test("keys are what the backend accepts", () => {
    assert.equal(Accounts.slug("My Work Acc."), "my-work-acc");
    assert.equal(Accounts.slug("Büro"), "b-ro");
    assert.ok(/^[a-z0-9][a-z0-9_-]{0,31}$/.test(Accounts.slug("x".repeat(60))));
});

test("accounts are listed, edited and removed without touching other settings", () => {
    let settings = Accounts.add({ keys: { a: "b" } }, "claude", "Work", "").settings;
    settings = Accounts.add(settings, "openai", "Team", "").settings;
    assert.deepEqual(plain(Accounts.list(settings, "claude")), [{ key: "work", id: "claude@work", label: "Work", home: "~/.claude-work" }]);

    settings = Accounts.update(settings, "claude", "work", { label: "Job", home: "/x" });
    assert.deepEqual(plain(Accounts.list(settings, "claude"))[0], { key: "work", id: "claude@work", label: "Job", home: "/x" });
    // A blank edit keeps what was there.
    settings = Accounts.update(settings, "claude", "work", { label: "  ", home: "" });
    assert.equal(Accounts.list(settings, "claude")[0].label, "Job");

    settings = Accounts.remove(settings, "claude", "work");
    assert.deepEqual(plain(Accounts.list(settings, "claude")), []);
    assert.equal(Accounts.list(settings, "openai").length, 1);
    assert.deepEqual(plain(settings.keys), { a: "b" });
    settings = Accounts.remove(settings, "openai", "team");
    assert.equal(settings.accounts, undefined);
});

test("the sign-in command points the tool at the folder", () => {
    assert.equal(Accounts.loginCommand("claude", "~/.claude-work"), "CLAUDE_CONFIG_DIR=~/.claude-work claude");
    assert.equal(Accounts.loginCommand("openai", "/srv/my codex"), 'CODEX_HOME="/srv/my codex" codex login');
    assert.equal(Accounts.loginCommand("kiro", "/k"), "KIRO_HOME=/k <kiro>");
    assert.equal(Accounts.loginCommand("zed", "/k"), "");
});

test("an account tab is on exactly when its provider is", () => {
    const on = { providerDefaultsApplied: true, providers: { claude: true } };
    assert.equal(Registry.enabled(on, "claude@work"), true);
    assert.equal(Registry.enabled(on, "openai@team"), false);
    assert.equal(Registry.enabled({ providerDefaultsApplied: true, providers: { claude: false } }, "claude@work"), false);
});

test("an account follows its provider in the user's order", () => {
    const canonical = ["claude", "openai", "kiro"].map((id) => ({ id }));
    const items = ["kiro", "openai@team", "claude@work", "claude", "openai"].map((id) => ({ id }));
    const ids = (order) => plain(Order.sort(items, order, canonical)).map((i) => i.id);
    assert.deepEqual(ids(undefined), ["claude", "claude@work", "openai", "openai@team", "kiro"]);
    assert.deepEqual(ids(["kiro", "claude"]), ["kiro", "claude", "claude@work", "openai", "openai@team"]);
});
