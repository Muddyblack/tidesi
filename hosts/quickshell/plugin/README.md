# Quickshell PluginComponent host

This host integrates tideSI's shared UI and backend with DankMaterialShell
(DMS 1.5 or later). It uses `PluginComponent` for the bar slot and its own
transparent Quickshell popup, not DMS's popup frame. DMS owns bar position and
monitor selection; tideSI owns provider configuration, charts and appearance.

## Assemble a plugin directory

Run from the repository root. The destination below must be a **new** directory;
the commands refuse to overwrite an installed plugin or settings.

```sh
plugin="${XDG_CONFIG_HOME:-$HOME/.config}/DankMaterialShell/plugins/aiUsagebar"
test ! -e "$plugin" && test ! -L "$plugin" || exit 1
mkdir -p "$plugin"
cp hosts/quickshell/plugin/AiUsagePlugin.qml \
   hosts/quickshell/plugin/PluginAppState.qml \
   hosts/quickshell/plugin/plugin.json "$plugin/"
cp -R ui assets translate backend "$plugin/"
cp LICENSE "$plugin/"
```

Enable **tideSI** in DMS's plugin settings and add `aiUsagebar` to a bar.
The plugin ID is retained for existing bar configurations. Bash, Python 3 and
coreutils must be on DMS's process PATH. Provider CLI tools are optional and
needed only for the providers or session-resume actions that use them.

The relative layout is required: Quickshell sandboxes imports to the plugin
root, so `ui/`, `assets/`, `translate/` and `backend/` must be inside that root.
`backend/sh` must stay beside `backend/tidesi`; copying only the launchers breaks
Python module discovery. Do not copy credentials into the plugin directory.

The host reads and writes
`$XDG_CONFIG_HOME/tidesi/quickshell-settings.json` (normally
`~/.config/tidesi/quickshell-settings.json`). The command backend passes that
same file to every provider tool. Its existing legacy-directory migration also
recognizes `ai-usage-widget/quickshell-settings.json`.

## Behavior and verification

- Left-click the bar pill to open the popup; Escape or an outside click closes it.
- Settings use the same provider, language, theme, history and export components
  as the other hosts. The CLI command field points to `backend/sh/tidesi-cli`.
- The overlay surface stays screen-sized while the visible body changes size.
  This avoids a stale-buffer resize observed with grabbing popups on Labwc.
- After replacing shared QML files, a plugin-only reload can retain cached types.
  Restart DMS from an unlocked session if the update still shows old behavior.
  Do not restart the shell while it is responsible for an active lock screen.

The original integration was exercised on Labwc with a horizontal bottom bar;
multi-monitor and vertical-bar interaction are not claimed verified. It depends
on DMS's `qs.Modules.Plugins` imports and is not a standalone Quickshell config.

Regression checks:

```sh
node --test tests/dms-host.test.js tests/panel-pill-sizing.test.js \
  tests/command-backend-config.test.js tests/command-backend-ordering.test.js
```
