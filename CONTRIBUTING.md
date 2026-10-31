# Contributing

## Dependencies

Installing a release `.plasmoid` (GitHub release or KDE Store) needs only the
runtime [requirements in the README](README.md#requirements): its translations
are already compiled in. Working from a clone of the source is different — the
compiled translations are not in git, and the dev tooling below is on you.

The easy route is `nix develop`, which provides all of it (and installs the git
hooks); `nix develop .#windows` adds PySide6 for the Windows tray app. Without
Nix, install:

| Tool | Needed for |
|---|---|
| Plasma SDK (`kpackagetool6`, `plasmoidviewer`) | `make test-install`, `make view` |
| gettext (`msgfmt`, `xgettext`, `msgmerge`, `msgattrib`) | compiling translations in `make test-install`, `make view`, `make pack`; `make translations` |
| `zip` | `make pack` |
| Python 3.8+ | the backend and every test suite |
| `jq`, `flock`, `timeout` | `make test` (the shell contract tests) |
| Node.js | `tests/shared-code.test.js` (skipped when `node` is missing) |
| Qt 6 `qmllint`, `qmlformat` | QML lint and formatting |
| `ruff` | `make lint-py` |
| `pre-commit` | the git hooks — they run `qmlformat`/`qmllint` and the catalog checks through `nix develop` if you have Nix, otherwise from your `PATH` (Qt tools and gettext) |

Only if you work on that part:

- **Windows tray app** — PySide6 and psutil (`hosts/desktop/requirements.txt`); building
  the `.exe` also needs `hosts/windows/build-requirements.txt` (PyInstaller) and Inno
  Setup on Windows. See [`docs/windows.md`](docs/windows.md).
- **Hyprland / Quickshell** — Quickshell, plus CMake and Qt 6 for the tray helper;
  `nix run .#hyprland` brings them. See [`docs/hyprland.md`](docs/hyprland.md).
- **README screenshots** — `make readme-screenshots` captures the real QML UI with
  demo data into `docs/readme/screenshots` (popup tabs, Claude stats, panel pills,
  settings). On Nix it uses the `windows` shell, which includes PySide6; elsewhere
  install `hosts/desktop/requirements.txt` first. Nothing is drawn by hand, so a QML
  change needs only a rerun.

## Development shell

`nix develop` (or `direnv allow` once, through the tracked `.envrc`) gives the
full toolchain; run `direnv reload` after changing `flake.nix`. Profilers and
debuggers (`perf`, Hotspot, Heaptrack, Valgrind, GDB, strace, pyinstrument,
memory-profiler) are in a separate shell, `nix develop .#profiling`, so the
default one — which CI enters — stays small. For SQLite timings, use a tmpfs
`TMPDIR` such as `/dev/shm`: on a real disk fsync noise outweighs most changes.

## Development install

```bash
make test-install     # or ./scripts/test_install.sh
```

Installs as `Tidesi (Test)` alongside the real widget so you can iterate
without touching your live install.

`make test-install`, `make view` and `make pack` compile the translations first,
so they need gettext (`msgfmt`); `nix develop` has it. The compiled `.mo`
files under `build/kde/contents/locale/` are git-ignored build output: edit
`translate/*.po` (regenerate with `make translations`), never commit a `.mo`.

## Translations

One catalog per language, `translate/<lang>.po`, serves every platform: the
shared UI parses it at runtime (`ui/js/I18n.js`) through `shell.i18n()` /
`shell.i18nc()` / `shell.i18np()`, and text the backend words itself is
written as `tr("…")` (`backend/tidesi/messages.py`) and translated by the UI
from the same catalog. `translate/Messages.sh` extracts both. Write new text
as one full phrase with `%1` placeholders rather than pieces joined with `+`,
and see [`translate/README.md`](translate/README.md) for Weblate.

Adding a language:

```bash
make new-language CODE=de                           # starts translate/de.po
# translate de.po, then:
make translations && make check-translations
```

Nothing else needs registering: the scripts, CI, packaging and every
host pick up every `translate/*.po`. `hosts/kde/metadata.json` can carry a
`"Name[de]"` / `"Description[de]"` for the widget list.

To remove the test copy:

```bash
kpackagetool6 -t Plasma/Applet -r org.muddyblack.tidesiTest
```

Preview the widget without installing it at all:

```bash
make view      # planar
make view-h    # horizontal
```

## Tests

```bash
make test
```

`make test` includes the portable Python suite, which also discovers the hermetic E2E
tests. To run only those:

```bash
make test-e2e
```

The E2Es in `tests/python/test_e2e_*.py` run the real CLI in a throwaway `HOME`,
with every upstream HTTP(S) call routed to a loopback server
(`TIDESI_E2E_HTTP_BASE`) and fake executables on `PATH`. No live vendor service is
contacted. `_e2e.py` holds the shared helpers; the suites are split by topic:
`test_e2e_http.py` (API-backed providers), `test_e2e_local.py` (session files,
SQLite databases, local executables) and `test_e2e_sources.py` (each selectable
source of the multi-source providers). Every provider has at least one test.
Native macOS Keychain and Windows Credential Manager access are not exercised.

`tests/python/test_fixtures.py` replays the provider envelopes in `tests/fixtures/` through
the in-process normalizers — success, missing credentials, malformed responses,
offline and rate-limited states for every provider. `test_cli.py` renders those
same fixtures through the terminal frontend. No network access is needed, and
the fixture tests run on Windows CI.
`test_provider_values.py` pins provider-specific calculations; `test_collect.py`
and `test_muse.py` exercise real file/SQLite readers and response-file hooks.
Recorded API bodies use `*-response.json` in `tests/fixtures/`; tests create
synthetic configs, credentials and session logs in temporary directories.
`tests/shared-code.test.js` covers the JavaScript both QML frontends share.

`nix develop --command make test-qml` runs the Qt tests in `tests/qml/`
without opening desktop windows or contacting providers. They check that
repeated Plasma commands release their DataSources and that popup animations
and the Info timer stop when their window is hidden.

`tests/python/` holds the portable suites — plain `unittest`, no shell — that
CI also runs on Windows: platform paths, the shared history file and its lock,
the Codex app-server client against a fake `codex.cmd`, finding Antigravity
through `psutil`, credential discovery, and the tray app loading its QML headless
with every settings section opened once (`make test-py`, or
`python hosts/desktop/app.py --selftest`).

Linting the Python backend and tray app needs `ruff`:

```bash
make lint-py
```

## Adding or changing a provider

The backend owns all provider logic and hands the frontends a versioned JSON
model. Read [`docs/provider-contract.md`](docs/provider-contract.md) first — it
documents the schema, the invariants the contract tests enforce, and the rule
that a statistic must not cost the user money to read.

Per-provider specifics (credential resolution, endpoints, API quirks) are
described in [`docs/providers.md`](docs/providers.md).

For provider defaulting and detection, follow the complete checklist in
[`docs/provider-detection.md`](docs/provider-detection.md). It covers
`config.py`, `collect.py`, `envelope.py`, `ProviderRegistry.js`, fixtures,
tests, and every affected document. A new
provider must not be called automatically detected unless it is in the backend
`AUTO_DETECT_PROVIDERS` allowlist and has stat-only, no-network, no-credential-read
tests.

For behavior shared by frontends, add a case to
[`tests/behavior/scenarios.json`](tests/behavior/scenarios.json). The
[behavioral test guide](tests/behavior/README.md) explains how Python, Node
and Qt consume the same scenarios and how to run the tests.

## Packaging

```bash
make pack
# compiles the translations, then writes tidesi-<version>.plasmoid
```

## Releasing

```bash
make tag     # or ./scripts/tag.sh
```

Prompts for a version bump (patch / minor / major), updates
`hosts/kde/metadata.json` (the one place the version lives) on a `release/vX.Y.Z`
branch, and opens a PR. Nothing is tagged or pushed to `main` by hand:
merging the PR makes CI create the tag, build the `.plasmoid` and the
Windows / macOS assets, and publish the GitHub release.
