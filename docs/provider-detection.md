# Provider detection policy

This is the maintainer source of truth for provider defaulting. It describes
what the backend may detect, what detection means, where settings live, and all
registration points required when a provider is added.

## Policy

Provider defaults are zero based. A new shared JSON settings file has every
provider disabled. The explicit `--initialize-provider-defaults` operation runs
once, enables providers with approved local evidence, writes the settings, and
sets `providerDefaultsApplied` to `true`. A later call returns the saved object
without running detection again. `--all` never performs implicit initialization.

Detection is a one-shot convenience for first setup, not a background scan. It
does not rerun at boot, on refresh, or when a new tool is installed. Users can
enable or disable providers manually at any time. Detection does not prove that
an account is authenticated, that a token is valid, that a service is usable, or
that a quota endpoint will answer.

Detection means *installed right now*. Logs, session folders, databases and
credential files are not evidence: they outlive an uninstall, and treating them
as evidence kept a removed tool "detected" forever (issue #60).

Settings → Providers → **Detect installed providers** re-runs detection on
demand and syncs both ways: a detected provider that is off is switched on, and
an auto-detectable provider whose program is gone is switched off, unless an API
key is configured for it (the key keeps it usable without the local tool).
Manual-only providers are never touched.

## Approved automatic detection

The backend constant `AUTO_DETECT_PROVIDERS` is the complete allowlist:

| ID | Installed program the probe looks for |
|---|---|
| `claude` | `claude` executable |
| `antigravity` | `agy` or `antigravity` executable, or the Antigravity desktop app |
| `openai` | `codex` executable, or the Codex desktop app |
| `kiro` | `kiro-cli` or `kiro` executable, or the Kiro desktop app |
| `mistral` | `vibe` executable |
| `grok` | `grok` executable |
| `muse` | `muse` executable |
| `cursor` | `cursor-agent` or `cursor` executable, or the Cursor desktop app |
| `cline` | `cline` executable |
| `opencode` | `opencode` executable |
| `mimo` | `mimo` executable |
| `junie` | `junie` executable |
| `windsurf` | `windsurf` executable, or the Windsurf desktop app |
| `pi` | `pi` or `omp` executable |
| `kilo` | `kilo` executable |
| `coderabbit` | `coderabbit` executable |

Executables are looked up on `PATH` and in the directories user-level
installers use but a desktop session's `PATH` often lacks (`~/.local/bin`,
`~/.npm-global/bin`, `~/.bun/bin`, `~/.nix-profile/bin`,
`/etc/profiles/per-user/$USER/bin`, `~/.claude/local`, `~/.opencode/bin`, …).
Desktop apps are a `.desktop` entry in the XDG application directories
(including Flatpak exports) on Linux, a `.app` bundle in `/Applications` or
`~/Applications` on macOS, and `%LOCALAPPDATA%\Programs\<App>\<App>.exe` on
Windows.

The returned list uses the order in `config.ALL_PROVIDERS`, not filesystem or
probe completion order. Do not add an ID to this table unless it is also added
to `AUTO_DETECT_PROVIDERS` and its stat-only probe is implemented and tested.

## Manual-only providers

These providers are in `ALL_PROVIDERS` but are intentionally not detected:

`openrouter`, `ollama`, `selfhosted`, `zai`, `copilot`, `deepseek`, `kimi`, `jetbrains` (IDE installs are too varied for stat-only probes), and `zed` (a stored editor credential is not a reliable signal that the user wants quota collection).

They require a user decision, an API key, an endpoint choice, or a credential
source whose presence is not a sufficient local-use signal. Their tabs are
enabled manually in the relevant frontend settings. Do not claim automatic
detection for them unless this policy and the backend allowlist change together.

## Detection constraints

Detection is metadata-only and stat-only. It may test whether a named program
or application entry exists and is executable. It must not read file contents,
parse credentials, open SQLite, invoke a command, inspect a process, make a
socket or HTTP request, or copy a token into any result.

The result is provider IDs only. It contains no paths, account names, token
values, credentials, API responses, or usability claims. A credential file
is not evidence at all, since it survives an uninstall; an installed
program is the evidence, and it still says nothing about authentication.

## Settings and platform behavior

| System | Provider toggle store | Detection/default behavior |
|---|---|---|
| KDE Plasma | The same shared JSON as Hyprland. The old KConfig values (`hosts/kde/contents/config/main.xml`) are carried into it once, on the first start after updating (`hosts/kde/contents/ui/KdeMigration.js`). | Same as every host: the shared UI runs the latched first-run initialization. |
| Hyprland | Shared JSON at `$XDG_CONFIG_HOME/tidesi/hyprland-settings.json`, or `TIDESI_CONFIG` | Backend `--all` reads this file. First-run initialization must be explicit and latched. |
| Windows | The same shared JSON under `%APPDATA%\\tidesi\\hyprland-settings.json` | The Windows app uses the shared backend and settings format. It must not silently rerun detection during refresh. |
| macOS | The same shared JSON under `~/.config/tidesi/hyprland-settings.json`, unless overridden | Same as every host. |

The backend path is named `hyprland-settings.json` for compatibility. Windows
and macOS share the file format even though neither is Hyprland. Platform data
directories used by probes come from `paths.py`: XDG locations on Linux, `%APPDATA%`
and `%LOCALAPPDATA%` on Windows, and both XDG and `~/Library/Application Support`
locations on macOS where vendor tools use different conventions.

## Future-provider checklist

Complete every applicable item in one change. A provider is not complete when
only its collector works.

1. Add the canonical ID and intended default semantics to `config.py`, including
   `ALL_PROVIDERS` and `OPT_IN_PROVIDERS` when the provider is manual-only.
2. If automatic detection is approved, add the ID to `AUTO_DETECT_PROVIDERS` and
   add a probe in `detect.py` that obeys the stat-only, no-network,
   no-credential-read rules. Otherwise document it as manual-only here.
3. Implement or update the provider collector in `collect.py`. Keep local
   evidence, credential resolution, network access, and normalized output as
   separate concerns.
4. Register the provider's normalized envelope fields, labels, and invariants in
   `envelope.py` and update the provider contract if the JSON shape changes.
5. Add the provider to `ui/js/ProviderRegistry.js`, including display
   metadata and key setting. Keep its opt-in classification aligned with
   `config.py`. Wire provider-specific presentation in `ui/` only when the
   contract requires it — it then appears on every platform.
5a. If the provider can be read more than one way, declare its `SOURCES` and
   register them in `sources._registry()` (see the provider contract, "Sources").
6. If the provider has an API key, add its old KConfig entry (if any) to
   `hosts/kde/contents/ui/KdeMigration.js`; new providers need nothing there.
7. (no per-platform step: there is one UI.)
8. Add success, missing-credential, malformed, offline, and rate-limited fixtures
   under `tests/fixtures/` as applicable. Fixtures must not contain real secrets.
9. Add collector, normalization, CLI, detection, migration, and frontend contract
   tests as applicable. Detection tests must prove no file reads, SQLite access,
   subprocesses, or network calls. Test both a fresh settings file and a latched
   existing file.
10. Update `docs/provider-contract.md`, `docs/providers.md`, this document, and
    the user-facing provider tables in `README.md`. Update `docs/cli.md`,
    `docs/windows.md`, or `docs/macos.md` when paths, settings, or platform
    behavior change. Update `CONTRIBUTING.md` if a new registration or test step
    is introduced.

Finally, run the full test suite and review the provider count in every document.
The current backend has 25 IDs in `ALL_PROVIDERS` and 16 IDs in
`AUTO_DETECT_PROVIDERS`.
