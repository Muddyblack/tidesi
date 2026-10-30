# tideSI on macOS

A menu bar app: the active provider's mark and its percentages in the menu
bar, and the same popup every other host shows under it — quota windows, reset
countdowns, usage history, sessions, spend and settings.

## Screenshots

See the [macOS and Windows screenshot gallery](../README.md#macos-and-windows)
for the popup, sessions, provider settings and appearance settings captured
on each platform with demo data.

## How it is built

There is one UI for every platform: the QML under `ui/`, with its state in
`ui/AppState.qml`. On macOS it runs inside the shared PySide6 tray app
(`hosts/desktop/app.py`, the same one Windows uses); the only macOS-specific
part is the menu bar item, `hosts/macos/statusitem.py`, a real `NSStatusItem`
created through PyObjC:

- the pill's values ("23% · 61%") are the item's **text**, in the menu bar
  font, so they adapt to a light or dark bar like any other item; a value at
  70% or more takes its warning colour;
- the active provider's logo is a **template image**, tinted by macOS;
- a click opens the popup under the item, a right- or control-click opens the
  menu (refresh, settings, tray style, start at login, quit).

The app has no Dock icon (`LSUIElement`). "Start at login" registers the app
as a login item through `SMAppService` (`hosts/macos/loginitem.py`), so it
shows by name under **System Settings → General → Login Items**; an older
build's LaunchAgent plist is moved over on the first start. Run from a
checkout, which has no app bundle to register, it still writes a per-user
LaunchAgent (`~/Library/LaunchAgents/org.muddyblack.tidesi.plist`).

The popup is the shared glass panel rather than an `NSPopover`: that is the
trade for one UI that cannot drift between platforms. It does get the
system's own blur: `hosts/macos/vibrancy.py` puts an `NSVisualEffectView`
(the popover material) behind it, set to the popup's light or dark, and the
QML glass thins out over it. Settings → Appearance → Theme picks light, dark,
or Auto, which follows the system appearance.

## Python on macOS

macOS ships no Python anyone can rely on, so `tideSI.app` carries its own:
PyInstaller freezes the app, Qt, PyObjC, the backend and its zstandard runtime
dependency into one bundle (`hosts/macos/tidesi.spec`). The terminal frontend
(`tidesi-cli`) is a separate matter: it is a shell script that needs a
Python of its own (`brew install python`).

## Where things are on macOS

Both directory conventions are genuinely in use on a Mac — a tool written
against XDG keeps reading `~/.config` there, and one built on a cross-platform
directories library lands in `~/Library/Application Support` — so `paths.py`
offers both, best first, and the provider picks whichever exists.

| | on macOS | fixed in |
| --- | --- | --- |
| Claude Code login | login Keychain, service `Claude Code-credentials` | `claude_credentials.py` |
| cursor-agent login | login Keychain, service `cursor-access-token` | `cursor.py` |
| Muse key | login Keychain, service `ai.meta.dev.credentials` | `muse_quota.py` |
| Codex login | `$CODEX_HOME/auth.json`, or the Keychain when configured for it | `openai_credentials.py` |
| kiro-cli | `~/Library/Application Support/kiro-cli/data.sqlite3` | `kiro.py` |
| Cursor / Kiro IDE | `~/Library/Application Support/<IDE>/User/globalStorage/state.vscdb` | `paths.py` |
| `gh` / Copilot | the Keychain, read by `gh auth token` itself | nothing to do |
| everything else | XDG, as on Linux | nothing to do |

### Provider defaults and detection

The app shares the backend settings file and format with Hyprland and
Windows. New shared JSON settings are zero based. The explicit
`--initialize-provider-defaults` operation enables only approved providers with
local evidence and sets `providerDefaultsApplied`; it is not implicit in
refresh or `--all`; the shared UI runs it on first start, as on every host.

Detection looks for installed tools only — a CLI in `PATH`, Homebrew,
`~/.local/bin`, npm/nvm and similar folders (a Finder-launched app gets a short
`PATH`, so these are searched explicitly), or an app bundle such as
`Cursor.app` in `/Applications` or `~/Applications`. Leftover logs and
credential files do not count. Settings → Providers → **Detect installed
providers** re-syncs later: newly installed tools are switched on, uninstalled
ones off unless they have an API key. It is stat-only: no credentials are read,
no SQLite opened, no command started, no network used. See
[`provider-detection.md`](provider-detection.md) for the allowlist, migration
rules, and provider addition checklist.

Every Keychain read goes through `/usr/bin/security`, never the Security
framework, and that is not a shortcut — it is the only way that does not
prompt. These items are created by the vendor's own tool running `security`,
which leaves the item's ACL trusting `/usr/bin/security` and its partition list
holding `apple-tool:` — a list no third-party signature can join, and one that
"Always Allow" does not edit. Read through the framework, a widget polling
every five minutes would raise the "wants to access your keychain" dialog on
every poll, forever. Asking the same Apple-signed tool raises none, whatever
this app is signed with. See `tidesi/keychain.py`.

Nothing here ever refreshes a token. Anthropic allows one live refresh token
per client, so a refresh from this widget would sign the user out of Claude
Code itself.

### The widget's own files

`~/.config/tidesi/` and `~/.local/share/tidesi/`, the same as
on Linux — deliberately, not by omission. The settings file and the usage
history are shared with `tidesi-cli` and with any Linux machine the user syncs
dotfiles from; moving them to `~/Library/Application Support` on macOS alone
would mean pasting every API key twice and keeping two histories.

## Building

On a Mac:

```bash
pip install -r hosts/macos/build-requirements.txt
hosts/macos/build-app.sh       # dist/tideSI.app
hosts/macos/package-dmg.sh     # tidesi-macos-<version>-<apple-silicon|intel>.dmg
```

`rsvg-convert` (`brew install librsvg`) is needed for the Finder icon; without
it the build still succeeds with the generic icon.

### Seeing it without a Mac

The popup is the shared QML, so `python3 hosts/desktop/app.py --screenshot DIR
--demo` on Linux renders exactly what the Mac shows inside the popup. Only the
menu bar item itself (`statusitem.py`) needs a Mac to try.

Every macOS CI run starts the real app on a Mac's real desktop and keeps a few
full-screen captures of it (`--tour`: popup tabs, settings) as its `screenshots`
artifact — never offscreen renders. On a pull request, a
maintainer can comment `/macos` to have them posted into a comment on the PR;
that labels the PR `macos-screenshots`, and the comment then updates with
every new commit (`.github/workflows/macos-screenshots.yml`).

## Translations

The popup and the menu read `translate/<lang>.po` at runtime, like every other
host (`ui/js/I18n.js`); there is no separate macOS catalog.

## Signing and distribution

Each release has two builds: `tidesi-macos-<version>-apple-silicon.dmg`
for M1 and later, and `tidesi-macos-<version>-intel.dmg` for Intel Macs
(Apple menu → **About This Mac** says which). psutil ships no universal
wheel, so CI builds each on its own runner (`macos-14`, `macos-15-intel`).
Download the right DMG, open it, and drag **tideSI.app** onto
**Applications**. Eject the disk image and open the app from Applications.

Until the repository has an Apple Developer ID, the app is ad-hoc signed and
not notarized. If macOS blocks the first launch and you trust the download:

1. Try opening the app from Applications.
2. Open **System Settings → Privacy & Security** and scroll to **Security**.
3. Choose **Open Anyway** for tideSI and confirm the prompt.

See [Apple's instructions for opening an app from an unknown developer](https://support.apple.com/guide/mac-help/mh40616/mac).
Users do **not** need an Apple developer account to install or use the app.

### Homebrew

The repository doubles as a [Homebrew](https://brew.sh/) tap, which picks the
right DMG for the Mac by itself:

```sh
brew tap muddyblack/tidesi https://github.com/Muddyblack/tidesi
brew install --cask muddyblack/tidesi/tidesi
```

Update with `brew upgrade --cask tidesi`. The first launch needs the same
**Open Anyway** step as above until releases are notarized. `brew uninstall
--zap --cask tidesi` also removes settings, history and caches.

`Casks/tidesi.rb` is written by the `homebrew-tap.yml` workflow (daily, or run
it by hand after a release) from the latest release, using the SHA-256 GitHub
reports for each DMG (`hosts/macos/homebrew-cask.py`). Being in the official
`homebrew/cask` needs a notarized app and more GitHub stars than the project
has yet; this tap needs neither.

### Notarizing releases

With a paid Apple Developer account, releases are signed with a Developer
ID and notarized by Apple, so they open with no "Open Anyway" step. The
release workflow does it by itself (`hosts/macos/notarize.sh`, the
hardened-runtime exceptions in `hosts/macos/entitlements.plist`) once these
repository secrets exist; without them it builds ad-hoc signed as before.

| Secret | What it holds |
| --- | --- |
| `MACOS_CERT_P12` | the "Developer ID Application" certificate and key, exported as .p12, base64-encoded |
| `MACOS_CERT_PASSWORD` | that .p12's password |
| `MACOS_SIGN_IDENTITY` | the identity name, e.g. `Developer ID Application: Name (TEAMID)` |
| `APPLE_ID` | the Apple Account email notarytool signs in with |
| `APPLE_TEAM_ID` | the 10-character team ID |
| `APPLE_APP_PASSWORD` | an app-specific password for that account (account.apple.com) |
