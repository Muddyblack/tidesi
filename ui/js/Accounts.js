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
// variable that points a provider's tool at a login folder, the main login's
// folder, a default folder to suggest, and the command that signs in there when
// there is a short one. Kiro and Antigravity are not here: their quota is read
// from the IDE, never from a login folder, so a second account of either would
// show the main one's numbers.
var HOMES = {
    claude: { env: "CLAUDE_CONFIG_DIR", main: "~/.claude", dir: "~/.claude-work", login: "claude" },
    openai: { env: "CODEX_HOME", main: "~/.codex", dir: "~/.codex-work", login: "codex login" },
    grok: { env: "GROK_HOME", main: "~/.grok", dir: "~/.grok-work", login: "grok" },
    kimi: { env: "KIMI_CODE_HOME", main: "~/.kimi-code", dir: "~/.kimi-code-work", login: "" },
    mistral: { env: "VIBE_HOME", main: "~/.vibe", dir: "~/.vibe-work", login: "vibe" }
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

// The command that signs a login folder in, for the page to show. A leading "~"
// is written as $HOME: a shell expands "~" only at the start of a word, so
// VAR=~/dir would hand the tool a folder literally named "~" in fish and in any
// quoted form, while $HOME is expanded everywhere.
//
// `os` is Qt.platform.os. Windows has no VAR=value prefix: PowerShell sets the
// variable, runs the tool and clears it again, so the window it was typed in
// does not go on signing that folder in.
function loginCommand(providerId, home, os) {
    var spec = HOMES[providerId];
    if (!spec)
        return "";
    var tool = spec.login || "<" + providerId + ">";
    if (os === "windows") {
        var win = String(home).replace(/^~(?=[\/\\]|$)/, "$HOME").replace(/\//g, "\\");
        return "$env:" + spec.env + " = \"" + win + "\"; " + tool + "; Remove-Item Env:" + spec.env;
    }
    var folder = String(home).replace(/^~(?=\/|$)/, "$HOME");
    if (/\s/.test(folder))
        folder = "\"" + folder + "\"";
    return spec.env + "=" + folder + " " + tool;
}

// A folder as something to compare: "~", "$HOME" and "%USERPROFILE%" alike,
// one kind of slash, no trailing one, and case folded (on macOS and Windows
// the case of a folder name does not make it another folder).
function folderKey(home) {
    return String(home || "").trim().replace(/^(\$HOME|%USERPROFILE%|\$env:USERPROFILE)(?=[\/\\]|$)/i, "~")
        .replace(/\\/g, "/").replace(/\/{2,}/g, "/").replace(/(.)\/+$/, "$1").toLowerCase();
}

// Whether `home` is a folder the main login or another account of `providerId`
// already reads (`exceptKey`: the account being edited). Read twice, its
// sessions and spend would count twice.
function folderTaken(settings, providerId, home, exceptKey) {
    var spec = HOMES[providerId];
    var key = folderKey(home);
    if (!spec || key === "")
        return false;
    if (key === folderKey(spec.main))
        return true;
    return list(settings, providerId).some(function (account) {
        return account.key !== exceptKey && folderKey(account.home) === key;
    });
}

// `settings` with a new account added, or the reason it cannot be:
//   { settings, id }  or  { error: "name" | "limit" | "folder" | "unsupported" }
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
    if (folderTaken(settings, providerId, folder, ""))
        return { error: "folder" };
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

// `settings` with one account's name or folder changed. A folder another login
// already reads is not taken (see folderTaken; the page says why).
function update(settings, providerId, key, changes) {
    if (changes.home !== undefined && folderTaken(settings, providerId, changes.home, key))
        changes = { label: changes.label };
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
