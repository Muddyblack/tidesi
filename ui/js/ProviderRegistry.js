.pragma library

// Every provider the settings page can switch on, in display order, with the
// settings key its API key is stored under. Shared by the two frontends that
// draw the settings page themselves — the Quickshell panel (TidesiShell.qml)
// and the Windows tray app (hosts/desktop/qml/Main.qml) — so adding a provider here
// is enough to make it configurable in both.

var providers = [
    {
        id: "claude",
        label: "Claude",
        accent: "#cc785c",
        keySetting: "claudeAdmin",
        keyPlaceholder: "sk-ant-api03-…"
    },
    {
        id: "antigravity",
        label: "Antigravity",
        accent: "#4285f4"
    },
    {
        id: "openai",
        label: "OpenAI",
        accent: "#10a37f",
        keySetting: "openai",
        keyPlaceholder: "sk-proj-…"
    },
    {
        id: "kiro",
        label: "Kiro",
        accent: "#8b5cf6"
    },
    {
        id: "mistral",
        label: "Mistral",
        accent: "#ff7000",
        keySetting: "mistral",
        keyPlaceholder: "or $MISTRAL_API_KEY"
    },
    {
        id: "openrouter",
        label: "OpenRouter",
        accent: "#9333ea",
        keySetting: "openrouter",
        keyPlaceholder: "or $OPENROUTER_API_KEY"
    },
    {
        id: "ollama",
        label: "Ollama Cloud",
        accent: "#f0f0f0",
        keySetting: "ollama",
        keyPlaceholder: "optional — OpenCode login or $OLLAMA_API_KEY"
    },
    {
        id: "selfhosted",
        label: "Local Models",
        accent: "#38bdf8",
        keySetting: "selfhosted",
        keyPlaceholder: "optional server token"
    },
    {
        id: "grok",
        label: "Grok",
        accent: "#e6e6e6",
        keySetting: "grok",
        keyPlaceholder: "optional; uses Grok CLI login"
    },
    {
        id: "zai",
        label: "Z.AI",
        accent: "#126ef4",
        keySetting: "zai",
        keyPlaceholder: "or $ZAI_TOKEN"
    },
    {
        id: "copilot",
        label: "Copilot",
        accent: "#8b5cf6",
        keySetting: "github",
        keyPlaceholder: "optional — gh/Copilot login is used"
    },
    {
        id: "deepseek",
        label: "DeepSeek",
        accent: "#4f8cff",
        keySetting: "deepseek",
        keyPlaceholder: "or $DEEPSEEK_API_KEY"
    },
    {
        id: "kimi",
        label: "Kimi",
        accent: "#1e3a8a",
        keySetting: "moonshot",
        keyPlaceholder: "optional — the kimi CLI login is used"
    },
    {
        id: "muse",
        label: "Muse",
        accent: "#0064e0",
        keySetting: "muse",
        keyPlaceholder: "optional — the CLI login is used"
    },
    {
        id: "cursor",
        label: "Cursor",
        accent: "#e6e6e6"
    },
    {
        id: "cline",
        label: "Cline",
        accent: "#e6e6e6"
    },
    {
        id: "mimo",
        label: "MiMo Code",
        accent: "#E8E8E8"
    },
    {
        id: "junie",
        label: "Junie",
        accent: "#48e054"
    },
    {
        id: "jetbrains",
        label: "JetBrains AI",
        accent: "#e6e6e6"
    },
    {
        id: "windsurf",
        label: "Windsurf",
        accent: "#34e8bb"
    },
    {
        id: "pi",
        label: "Pi",
        accent: "#d4d4d8"
    },
    {
        id: "kilo",
        label: "Kilo",
        accent: "#f4e04d",
        keySetting: "kilo",
        keyPlaceholder: "optional — the kilo CLI login is used"
    },
    {
        id: "coderabbit",
        label: "CodeRabbit",
        accent: "#ff7a3d"
    },
    {
        id: "zed",
        label: "Zed",
        accent: "#e6e6e6"
    },
    {
        id: "opencode",
        label: "OpenCode",
        accent: "#B7B1B1"
    }
];

// Mirrors PROVIDER_ICONS in tidesi/contract.py, so the settings page can show
// every provider's logo — including ones switched off, which the backend has
// sent no snapshot (and so no icon) for.
var ICONS = {
    antigravity: "antigravity-color.svg",
    claude: "claude-color.svg",
    cline: "cline.svg",
    copilot: "githubcopilot.svg",
    cursor: "cursor.svg",
    deepseek: "deepseek-color.svg",
    grok: "grok.svg",
    kimi: "kimi.svg",
    kiro: "kiro.svg",
    mistral: "mistral-color.svg",
    muse: "muse-color.svg",
    openai: "openai.svg",
    ollama: "ollama.svg",
    opencode: "opencode-color.svg",
    mimo: "mimo.svg",
    junie: "junie.svg",
    jetbrains: "jetbrains.svg",
    windsurf: "windsurf.svg",
    pi: "pi.svg",
    kilo: "kilo.svg",
    coderabbit: "coderabbit.svg",
    zed: "zed.svg",
    selfhosted: "local-models.svg",
    openrouter: "openrouter.svg",
    zai: "zai.svg"
};
for (var _i = 0; _i < providers.length; _i++)
    providers[_i].icon = providers[_i].icon || ICONS[providers[_i].id] || "";

// Mirrors OPT_IN_PROVIDERS in tidesi/config.py: what a missing toggle means
// until providerDefaultsApplied is set (or when applying the defaults failed).
var OPT_IN = ["zai", "copilot", "deepseek", "kimi", "muse", "cursor", "cline", "opencode", "mimo", "junie", "jetbrains", "windsurf", "pi", "kilo", "coderabbit", "zed", "ollama", "selfhosted"];

function enabled(settings, id) {
    // An extra account ("claude@work") is on exactly when its provider is.
    id = String(id).split("@")[0];
    var toggles = (settings && settings.providers) || {};
    if (!settings || settings.providerDefaultsApplied !== true) {
        if (OPT_IN.indexOf(id) !== -1)
            return toggles[id] === true;
        return toggles[id] !== false;
    }
    return toggles[id] === true;
}

// Mirrors AUTO_DETECT_PROVIDERS in tidesi/detect.py: the providers detection
// can speak for. Every other provider is only ever switched by hand.
var AUTO_DETECT = ["claude", "antigravity", "openai", "kiro", "mistral", "grok", "muse", "cursor", "cline", "opencode", "mimo", "junie", "windsurf", "pi", "kilo", "coderabbit"];

// Re-run of detection from the settings page. Detection reports what is
// installed right now, so it syncs both ways: a detected provider that is off
// is switched on, and an auto-detectable provider whose program is gone is
// switched off (issue #60), unless an API key is configured for it, because
// that key keeps it usable without the local tool. Returns the new settings
// and the ids that were switched on and off.
function applyDetected(settings, detected) {
    var next = JSON.parse(JSON.stringify(settings || {}));
    next.providers = next.providers || {};
    var keys = next.keys || {};
    var found = detected || [];
    var added = [];
    var removed = [];
    found.forEach(function (id) {
        if (!enabled(next, id)) {
            next.providers[id] = true;
            added.push(id);
        }
    });
    providers.forEach(function (provider) {
        var id = provider.id;
        if (AUTO_DETECT.indexOf(id) === -1 || found.indexOf(id) !== -1 || !enabled(next, id))
            return;
        if (provider.keySetting && String(keys[provider.keySetting] || "") !== "")
            return;
        next.providers[id] = false;
        removed.push(id);
    });
    return {
        settings: next,
        added: added,
        removed: removed
    };
}

// Display names for a list of provider ids, for the detection status line.
function labels(ids) {
    return (ids || []).map(function (id) {
        for (var i = 0; i < providers.length; i++)
            if (providers[i].id === id)
                return providers[i].label || id;
        return id;
    }).join(", ");
}
