.pragma library

// Extra logins of one provider ("work" and "personal" Claude). The backend
// (tidesi/accounts.py) turns each entry of the `accounts` setting into a
// provider of its own with the id "<provider>@<key>"; this file is the settings
// page's side of that: what can have accounts, and how the list is edited.
//
//     settings.accounts = { claude: [{ id: "work", label: "Work", home: "~/.claude-work" }] }
//
// Only the folder is stored. Tokens stay where the tool wrote them.

var SEPARATOR = "@";
var MAX_PER_PROVIDER = 8;

// Mirrors HOME_ENV in tidesi/accounts.py (a test keeps them equal): the
// variable that points a provider's tool at a login folder, a default folder to
// suggest, and the command that signs in there when there is a short one.
var HOMES = {
    claude: { env: "CLAUDE_CONFIG_DIR", dir: "~/.claude-work", login: "claude" },
    openai: { env: "CODEX_HOME", dir: "~/.codex-work", login: "codex login" },
    grok: { env: "GROK_HOME", dir: "~/.grok-work", login: "grok" },
    kimi: { env: "KIMI_CODE_HOME", dir: "~/.kimi-code-work", login: "" },
    mistral: { env: "VIBE_HOME", dir: "~/.vibe-work", login: "vibe" },
    kiro: { env: "KIRO_HOME", dir: "~/.kiro-work", login: "" },
    antigravity: { env: "ANTIGRAVITY_HOME", dir: "~/.gemini-work", login: "" }
};

// "claude" for both "claude" and "claude@work".
function baseId(id) {
    return String(id || "").split(SEPARATOR)[0];
}

function isAccount(id) {
    return String(id || "").indexOf(SEPARATOR) !== -1;
}

function supports(providerId) {
    return Object.prototype.hasOwnProperty.call(HOMES, providerId);
}

// The accounts saved for `providerId`, as the backend will read them.
function list(settings, providerId) {
    var all = (settings && settings.accounts) || {};
    var entries = Array.isArray(all[providerId]) ? all[providerId] : [];
    return entries.filter(function (entry) {
        return entry && typeof entry === "object" && entry.id && entry.home;
    }).map(function (entry) {
        return {
            key: String(entry.id),
            id: providerId + SEPARATOR + entry.id,
            label: String(entry.label || entry.id),
            home: String(entry.home)
        };
    });
}

// A key the backend accepts, made from what the user typed: lower case letters,
// digits, "-" and "_", starting with a letter or digit.
function slug(text) {
    var key = String(text || "").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^[-_]+|[-_]+$/g, "");
    return key.slice(0, 32).replace(/[-_]+$/, "");
}

// The folder an account gets when the user does not pick one: the provider's
// usual folder with the account's key on the end (~/.claude -> ~/.claude-work).
function defaultHome(providerId, name) {
    var spec = HOMES[providerId];
    if (!spec)
        return "";
    return spec.dir.replace(/-work$/, "") + "-" + slug(name);
}

// The command that signs a login folder in, for the page to show.
function loginCommand(providerId, home) {
    var spec = HOMES[providerId];
    if (!spec)
        return "";
    var folder = /\s/.test(home) ? "\"" + home + "\"" : home;
    return spec.env + "=" + folder + " " + (spec.login || "<" + providerId + ">");
}

// `settings` with a new account added, or the reason it cannot be:
//   { settings, id }  or  { error: "name" | "limit" | "unsupported" }
// The key comes from the name; a name that is already taken gets "-2", "-3"….
// A blank folder becomes defaultHome().
function add(settings, providerId, label, home) {
    if (!supports(providerId))
        return { error: "unsupported" };
    var name = String(label || "").trim();
    if (name === "" || slug(name) === "")
        return { error: "name" };
    var next = JSON.parse(JSON.stringify(settings || {}));
    next.accounts = next.accounts && typeof next.accounts === "object" ? next.accounts : {};
    var entries = Array.isArray(next.accounts[providerId]) ? next.accounts[providerId] : [];
    if (entries.length >= MAX_PER_PROVIDER)
        return { error: "limit" };
    var base = slug(name);
    var key = base;
    var taken = entries.map(function (entry) {
        return entry && entry.id;
    });
    for (var n = 2; taken.indexOf(key) !== -1; n++)
        key = base.slice(0, 32 - String(n).length - 1) + "-" + n;
    var folder = String(home || "").trim() || defaultHome(providerId, key);
    entries.push({ id: key, label: name, home: folder });
    next.accounts[providerId] = entries;
    return { settings: next, id: providerId + SEPARATOR + key };
}

// `settings` without the account `key` of `providerId`.
function remove(settings, providerId, key) {
    var next = JSON.parse(JSON.stringify(settings || {}));
    var entries = next.accounts && Array.isArray(next.accounts[providerId]) ? next.accounts[providerId] : [];
    var kept = entries.filter(function (entry) {
        return !entry || entry.id !== key;
    });
    if (kept.length > 0) {
        next.accounts[providerId] = kept;
    } else if (next.accounts) {
        delete next.accounts[providerId];
        if (Object.keys(next.accounts).length === 0)
            delete next.accounts;
    }
    return next;
}

// `settings` with one account's name or folder changed.
function update(settings, providerId, key, changes) {
    var next = JSON.parse(JSON.stringify(settings || {}));
    var entries = next.accounts && Array.isArray(next.accounts[providerId]) ? next.accounts[providerId] : [];
    entries.forEach(function (entry) {
        if (!entry || entry.id !== key)
            return;
        if (changes.label !== undefined && String(changes.label).trim() !== "")
            entry.label = String(changes.label).trim();
        if (changes.home !== undefined && String(changes.home).trim() !== "")
            entry.home = String(changes.home).trim();
    });
    return next;
}
