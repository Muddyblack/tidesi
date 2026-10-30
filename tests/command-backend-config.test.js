const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { execFileSync } = require("node:child_process");

const repo = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(repo, "ui/CommandBackend.qml"), "utf8");

function commandBackend(configPath) {
    const root = { configPath, pythonPath: "", toolsDir: path.join(repo, "backend/sh") };
    for (const name of ["tool", "backendArgs"]) {
        const start = source.indexOf(`    function ${name}(`);
        assert.notEqual(start, -1);
        const end = source.indexOf("\n    }", start);
        root[name] = vm.runInNewContext(`(${source.slice(start, end + 6)})`, { root });
    }
    return root;
}

test("provider edits and reopening use the host config instead of an ambient config", () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "ai-usage-config-"));
    try {
        // Given: a host file and a conflicting inherited backend config.
        const config = path.join(home, "plugin's settings.json");
        const decoy = path.join(home, "old-settings.json");
        const initial = { providerDefaultsApplied: true, providers: { cursor: true, ollama: false } };
        fs.writeFileSync(config, JSON.stringify(initial));
        fs.writeFileSync(decoy, JSON.stringify(initial));
        const env = {
            PATH: process.env.PATH,
            HOME: home,
            XDG_CONFIG_HOME: path.join(home, "config"),
            XDG_DATA_HOME: path.join(home, "data"),
            XDG_CACHE_HOME: path.join(home, "cache"),
            AI_USAGE_CONFIG: decoy,
        };
        const providers = (backend) => {
            const [program, ...args] = backend.backendArgs(["--all"]);
            const output = execFileSync(program, args, { env, encoding: "utf8", timeout: 30000 });
            return JSON.parse(output).providers.map((provider) => provider.id);
        };
        const backend = commandBackend(config);
        assert.deepEqual(providers(backend), ["cursor"]);

        // When: the UI removes Cursor and adds Ollama in its own settings file.
        fs.writeFileSync(config, JSON.stringify({ ...initial, providers: { cursor: false, ollama: true } }));

        // Then: both refresh and a newly opened host fetch only Ollama.
        assert.deepEqual(providers(backend), ["ollama"]);
        assert.deepEqual(providers(commandBackend(config)), ["ollama"]);
        assert.deepEqual(JSON.parse(fs.readFileSync(decoy, "utf8")), initial);
    } finally {
        fs.rmSync(home, { recursive: true, force: true });
    }
});
