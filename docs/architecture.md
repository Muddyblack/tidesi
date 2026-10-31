# Architecture

One backend, one UI, one thin host per platform.

```
backend/                 Python (stdlib plus zstandard for Zed sessions) + sh launchers — every provider,
  tidesi/               credential, HTTP call, normalisation, history, sessions
  sh/get-ai-usage        the CLI the process-based hosts run

ui/                      THE user interface, shown unchanged on every platform
  AppState.qml           all application state and logic (tabs, settings,
                         sessions, pricing, history, translations)
  CommandBackend.qml     `backend` for hosts that can only start processes
  PopupContent.qml …     the popup, its pages, rows, charts, settings
  PanelPill.qml          the compact panel / pill readout
  js/                    pure JavaScript shared by the QML (and the node tests)

hosts/                   only what differs per platform: the panel, tray or
  kde/                   menu bar item around the UI, and how it is packaged
  quickshell/            Hyprland / Caelestia / any wlroots panel (+ C++ tray helper)
  desktop/               PySide6 tray app (Windows, macOS; any Linux tray too)
  cosmic/                Rust/libcosmic panel applet for COSMIC (popup = the desktop app)
  windows/               installer, PyInstaller spec, Scoop/WinGet manifests
  macos/                 native NSStatusItem (PyObjC), .app spec, .dmg

assets/                  icons
translate/               gettext catalogs, read at runtime by ui/js/I18n.js
```

## The host contract

`ui/AppState.qml` needs two things from a host:

- a **`backend`** object. `hosts/desktop/app.py`'s `Backend` runs the backend
  in-process; `ui/CommandBackend.qml` runs `backend/sh/*` through a `runner`
  the host provides (`hosts/quickshell/ProcessRunner.qml`,
  `hosts/kde/contents/ui/PlasmaRunner.qml`). Both implement the interface
  listed at the top of `CommandBackend.qml`.
- **`popupVisible`**, and a few capability flags the settings page reads
  (`pillControls`, `interpreterControls`, `trayOptions`, `screenNames`).

The host then places `PanelPill { … app.pillSlots … }` in its panel and
`PopupContent { shell: app }` in its popup. That is all a new platform
(Noctalia, GNOME, COSMIC, …) has to write — a few hundred lines — provided its
shell can host QtQuick.

## Packaging

| Platform | Built by | Ships |
|---|---|---|
| KDE Plasma | `make pack` → `scripts/build-kde-package.sh` | `.plasmoid` (KDE Store): `hosts/kde` + `ui/` + `backend/` + assets |
| Quickshell | `nix run .#hyprland` | runs from the checkout (`shell.qml` at the root) |
| COSMIC | `nix build .#cosmic`, `cosmic.yml` | applet binary; release tarball `tideSI-cosmic-*.tar.gz` ([docs/cosmic.md](cosmic.md)) |
| Windows | `hosts/windows/tidesi.spec` + Inno Setup | installer, portable zip, Scoop/WinGet |
| macOS | `hosts/macos/build-app.sh` | `Tidesi.app`, `.dmg` |
