import QtQuick

// The `backend` AppState.qml talks to, for hosts whose only way to reach the
// Python backend is to start a process: Quickshell and the KDE Plasma widget.
// The Windows and macOS apps hand AppState their in-process Python `Backend`
// (hosts/windows/app.py), which implements this same interface.
//
// A host gives it a `runner`: any object with
//
//     run(argv, callback)   start argv (argv[0] is the program) and call
//                           callback(stdout, stderr, exitCode) once it is done
//
// Everything else — which tool, which arguments, how answers are parsed into
// the signals below — lives here once.
//
// Interface (shared with app.py's Backend):
//   properties  busy, pricingBusy, appIcon, iconDir, configPath, firstRun,
//               defaultTrayStyle, autostartAvailable, autostart
//   signals     settingsLoaded(text), catalogLoaded(text), languagesLoaded(json),
//               providerDefaultsLoaded(text), providersDetected(text),
//               snapshotReady(text), providerReady(text), refreshFailed(message),
//               sessionsReady(text, query, requestId), openSessionFinished(text),
//               pricingRefreshFinished(text), pricingRefreshFailed(message),
//               ratesReady(text), historyFinished(op, result),
//               settingRequested(key, jsonValue), snapshotExported(json)
//               lastSnapshotLoaded(text)
//   methods     requestSettings(), saveSettings(text), requestCatalog(language),
//               requestLastSnapshot(),
//               requestLanguages(), initializeProviderDefaults(),
//               detectProviders(), refresh(), refreshPricing(),
//               requestRates(query, limit, offset, requestId), refreshProvider(id),
//               refreshSessions(query, requestId, offset[, sourceIds]),
//               refreshSessionsAndQuery(query, requestId, offset[, sourceIds]),
//               openSession(key), history(op, payload),
//               flushHistory(op, payload), exportSnapshot(format, src, dest, w, h),
//               openCli(), setAutostart(enabled),
//               publishTrayState(json), setTrayLabels(json)
QtObject {
    id: root

    required property var runner
    // Where the backend's shell tools (backend/sh), the translation catalogs
    // and the icons are. A checkout has them at backend/sh, translate/ and
    // assets/icons; an installed package wherever its host put them.
    required property string toolsDir
    required property string translateDir
    required property string assetsDir
    // Where the backend reads and this writes the settings JSON.
    required property string configPath
    // $PYTHON3 for tools/sh/python-interp.sh; "" auto-detects.
    property string pythonPath: ""
    // Optional function(text) -> text over the settings as read, before
    // AppState sees them; a changed result is written back. The KDE host uses
    // it to carry the old KConfig settings over once.
    property var migrateSettings: null
    // Languages a host can name before the settings say otherwise ($LANGUAGE,
    // the locale), most preferred first.
    property var systemLanguages: []

    readonly property string appIcon: "file://" + assetsDir + "/icons/org.muddyblack.tidesi.svg"
    readonly property string iconDir: "file://" + assetsDir + "/icons/"
    readonly property bool firstRun: false
    readonly property string defaultTrayStyle: "icons"
    // Starting at login is the desktop's business on these hosts.
    readonly property bool autostartAvailable: false
    readonly property bool autostart: false

    property bool busy: false
    property bool pricingBusy: false
    property bool refreshPending: false
    property bool zedRefreshBusy: false
    property bool zedRefreshPending: false
    property bool settingsSaveBlocked: false
    property QtObject settingsQueue: ProcessQueue {
        runner: root.runner
        onIdle: {
            if (root.refreshPending)
                root.refresh();
            root.runPendingZedRefresh();
        }
    }
    property QtObject historyQueue: ProcessQueue {
        runner: root.runner
    }

    signal settingsLoaded(string text)
    signal settingsSaved
    signal settingsSaveFailed(string reason)
    signal lastSnapshotLoaded(string text)
    signal catalogLoaded(string text)
    signal languagesLoaded(string text)
    signal providerDefaultsLoaded(string text)
    signal providersDetected(string text)
    signal snapshotReady(string text)
    signal providerReady(string text)
    signal refreshFailed(string message)
    signal sessionsReady(string text, string query, int requestId)
    signal openSessionFinished(string text)
    signal pricingRefreshFinished(string text)
    signal pricingRefreshFailed(string message)
    signal ratesReady(string text)
    signal historyFinished(string op, string result)
    signal snapshotExported(string text)
    signal cliOpened(string text)
    signal settingRequested(string key, string value)

    // ── Command lines ────────────────────────────────────────────────────────
    // Every value goes in as a positional argument rather than being spliced
    // into the script, so paths with spaces and keys with shell metacharacters
    // stay intact. An empty $PYTHON3 reads as unset in python-interp.sh.
    // Always braced: in sh, "$10" is "$1" followed by "0", not argument ten.
    function tool(name, args, env) {
        var script = "PYTHON3=\"$1\" AI_USAGE_CONFIG=\"$2\"";
        var argv = ["sh", "-c", "", "tidesi", root.pythonPath || "", root.configPath];
        var keys = Object.keys(env || {});
        for (var i = 0; i < keys.length; i++) {
            argv.push(String(env[keys[i]]));
            script += " " + keys[i] + "=\"${" + (argv.length - 4) + "}\"";
        }
        argv.push(root.toolsDir + "/" + name);
        script += " exec \"${" + (argv.length - 4) + "}\"";
        for (var j = 0; j < (args || []).length; j++) {
            argv.push(String(args[j]));
            script += " \"${" + (argv.length - 4) + "}\"";
        }
        argv[2] = script;
        return argv;
    }

    function backendArgs(args) {
        return root.tool("get-ai-usage", args);
    }

    function firstLine(text) {
        return (text || "").trim().split("\n")[0];
    }

    // ── Settings and translations ────────────────────────────────────────────
    function requestSettings() {
        root.runner.run(["sh", "-c", "cat \"$1\" 2>/dev/null || cat \"$(dirname \"$(dirname \"$1\")\")/ai-usage-widget/$(basename \"$1\")\" 2>/dev/null || printf '{}'", "tidesi", root.configPath], function (out) {
            var text = out || "{}";
            if (typeof root.migrateSettings === "function") {
                var migrated = root.migrateSettings(text);
                if (migrated !== text) {
                    root.saveSettings(migrated);
                    text = migrated;
                }
            }
            root.settingsLoaded(text);
        });
    }

    function saveSettings(text) {
        // Written beside the target and renamed over it, so a reader never sees
        // a half-written file.
        root.settingsQueue.run(["sh", "-c", "umask 077; tmp=\"$2.tmp.$$\"; trap 'rm -f \"$tmp\"' EXIT; mkdir -p \"$(dirname \"$2\")\" && printf '%s' \"$1\" > \"$tmp\" && mv -f \"$tmp\" \"$2\"", "tidesi", text, root.configPath], function (out, err, code) {
            root.settingsSaveBlocked = code !== 0;
            // Only a known OS reason is passed on: stderr itself is never shown,
            // since a failing command could echo the settings text and its keys.
            if (root.settingsSaveBlocked)
                root.settingsSaveFailed(["Permission denied", "No space left on device", "Read-only file system", "Disk quota exceeded"].find(function (reason) {
                    return (err || "").indexOf(reason) !== -1;
                }) || "");
            else
                root.settingsSaved();
        });
    }

    function requestCatalog(language) {
        var langs = language !== "" ? [language] : root.systemLanguages;
        root.runner.run(["sh", "-c", "d=\"$1\"; shift; for l in \"$@\"; do [ -f \"$d/$l.po\" ] && exec cat \"$d/$l.po\"; done; true", "tidesi", root.translateDir].concat(langs), function (out) {
            root.catalogLoaded(out || "");
        });
    }

    function requestLanguages() {
        root.runner.run(["sh", "-c", "for f in \"$1\"/*.po; do [ -f \"$f\" ] && basename \"$f\" .po; done; true", "tidesi", root.translateDir], function (out) {
            var list = (out || "").split("\n").filter(function (l) {
                return l !== "";
            });
            root.languagesLoaded(JSON.stringify(list));
        });
    }

    function initializeProviderDefaults() {
        root.runner.run(root.backendArgs(["--initialize-provider-defaults"]), function (out) {
            root.providerDefaultsLoaded(out || "");
        });
    }

    function detectProviders() {
        root.runner.run(root.backendArgs(["--detect-providers"]), function (out) {
            root.providersDetected(out || "");
        });
    }

    // ── Usage ────────────────────────────────────────────────────────────────
    function refresh() {
        if (root.busy || root.settingsQueue.busy || root.settingsSaveBlocked) {
            root.refreshPending = true;
            return;
        }
        root.refreshPending = false;
        root.busy = true;
        var finalText = "";
        function consume(line, progressive) {
            var data;
            try {
                data = JSON.parse(line);
            } catch (e) {
                return;
            }
            if (!data || !Array.isArray(data.providers))
                return;
            if (data.partial === true) {
                if (progressive)
                    root.providerReady(line);
            } else {
                finalText = line;
            }
        }
        // --save-snapshot keeps the last good answer for the next start.
        root.runner.run(root.backendArgs(["--all", "--stream", "--save-snapshot"]), function (out, err, code) {
            (out || "").split("\n").forEach(function (line) {
                consume(line, false);
            });
            if (code === 0 && finalText !== "")
                root.snapshotReady(finalText);
            else
                root.refreshFailed(root.firstLine(err) || "usage backend failed");
            root.busy = false;
            if (root.refreshPending)
                root.refresh();
            root.runPendingZedRefresh();
        }, function (line) {
            consume(line, true);
        });
    }

    function refreshProvider(providerId) {
        if (providerId !== "zed")
            return;
        if (root.zedRefreshBusy || root.busy || root.settingsQueue.busy || root.settingsSaveBlocked) {
            root.zedRefreshPending = true;
            return;
        }
        root.zedRefreshBusy = true;
        root.runner.run(root.backendArgs(["--provider", providerId, "--save-snapshot"]), function (out, err, code) {
            if (code === 0) {
                try {
                    var data = JSON.parse((out || "").trim());
                    var provider = (data.providers || []).filter(function (item) {
                        return item.id === providerId;
                    })[0];
                    if (provider) {
                        root.providerReady(JSON.stringify({
                            schemaVersion: data.schemaVersion,
                            partial: true,
                            recordHistory: provider.ok === true,
                            updatedAt: data.updatedAt || provider.updatedAt || 0,
                            active: providerId,
                            providers: [provider]
                        }));
                    } else {
                        root.refreshFailed("Zed usage backend returned no provider data");
                    }
                } catch (e) {
                    root.refreshFailed("Zed usage backend returned invalid data");
                }
            } else {
                root.refreshFailed(root.firstLine(err) || "Zed usage backend failed");
            }
            root.zedRefreshBusy = false;
            root.runPendingZedRefresh();
        });
    }

    function runPendingZedRefresh() {
        if (!root.zedRefreshPending || root.zedRefreshBusy || root.busy || root.settingsQueue.busy || root.settingsSaveBlocked)
            return;
        root.zedRefreshPending = false;
        Qt.callLater(function () {
            root.refreshProvider("zed");
        });
    }

    function requestLastSnapshot() {
        root.runner.run(root.backendArgs(["--last-snapshot"]), function (out) {
            root.lastSnapshotLoaded(out || "");
        });
    }

    function refreshPricing() {
        if (root.pricingBusy)
            return;
        root.pricingBusy = true;
        root.runner.run(root.backendArgs(["--refresh-pricing"]), function (out, err, code) {
            root.pricingBusy = false;
            if ((out || "").trim() !== "")
                root.pricingRefreshFinished(out);
            else
                root.pricingRefreshFailed(root.firstLine(err));
        });
    }

    property int ratesRequestId: 0

    function requestRates(query, limit, offset, requestId) {
        if (requestId <= root.ratesRequestId)
            return;
        root.ratesRequestId = requestId;
        root.runner.run(root.backendArgs(["--pricing-table", "--query", query, "--limit", String(limit), "--offset", String(offset)]), function (out, err, code) {
            if (requestId === root.ratesRequestId)
                root.ratesReady(code === 0 ? (out || "") : "");
        });
    }

    // ── Sessions ─────────────────────────────────────────────────────────────
    property int sessionsRequestId: 0

    function sessions(refresh, query, requestId, offset, sourceIds) {
        if (requestId <= root.sessionsRequestId)
            return;
        root.sessionsRequestId = requestId;
        var args = ["--sessions", refresh ? "--refresh" : "--query-only", "--query", query, "--limit", "60", "--offset", String(offset || 0)];
        if (sourceIds && sourceIds.length > 0)
            args.push("--source", sourceIds.join(","));
        root.runner.run(root.backendArgs(args), function (out, err, code) {
            // A superseded answer is dropped here; AppState checks again.
            if (requestId !== root.sessionsRequestId)
                return;
            if (code !== 0 && (out || "").trim() === "")
                root.sessionsReady(JSON.stringify({
                    error: root.firstLine(err) || "sessions backend failed",
                    sessions: []
                }), query, requestId);
            else
                root.sessionsReady(out || "", query, requestId);
        });
    }

    function refreshSessions(query, requestId, offset, sourceIds) {
        root.sessions(false, query, requestId, offset, sourceIds);
    }

    function refreshSessionsAndQuery(query, requestId, offset, sourceIds) {
        root.sessions(true, query, requestId, offset, sourceIds);
    }

    function openSession(key) {
        root.runner.run(root.backendArgs(["--open-session", key]), function (out) {
            root.openSessionFinished(out || "");
        });
    }

    // ── History ──────────────────────────────────────────────────────────────
    function history(op, payload) {
        var env = payload !== "" ? {
            WIDGET_HISTORY_JSON: payload
        } : {};
        root.historyQueue.run(root.tool("history-io", [op], env), function (out) {
            root.historyFinished(op, out || "");
        });
    }

    // No synchronous process here: the write is started and left to finish,
    // which it does — the tool does not depend on this process staying alive.
    function flushHistory(op, payload) {
        var env = payload !== "" ? {
            WIDGET_HISTORY_JSON: payload
        } : {};
        root.runner.run(root.tool("history-io", [op], env), function () {});
        return "";
    }

    // Open the terminal frontend in a terminal window.
    function openCli() {
        root.runner.run(root.backendArgs(["--open-cli"]), function (out) {
            root.cliOpened(out || "");
        });
    }

    // ── Popup picture ────────────────────────────────────────────────────────
    // The UI saved its grab to `src`; this finishes it as a PNG or SVG at `dest`.
    function exportSnapshot(format, src, dest, width, height) {
        root.runner.run(root.tool("export-snapshot", [format, src, dest, String(width), String(height)]), function (out) {
            root.snapshotExported(out || "");
        });
    }

    // ── Tray (none on these hosts) ───────────────────────────────────────────
    function setAutostart(enabled) {
    }
    function publishTrayState(json) {
    }
    function setTrayLabels(json) {
    }
}
