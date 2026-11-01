# Packaging for Nix and nixpkgs

## The flake and its modules

The flake builds from the working tree and exports:

| Output | What |
| --- | --- |
| `packages.default` | the Plasma widget (`share/plasma/plasmoids/org.muddyblack.tidesi`) |
| `packages.cli` | `tidesi-cli` and `get-ai-usage`, no Qt, no plasmoid |
| `packages.desktop` | `tidesi`: the tray app with the floating pill, for any desktop with a tray (GNOME, Sway, XFCE, ...) |
| `packages.hyprland` | the Quickshell panel and tray helper, started with `tidesi-hyprland` |
| `packages.dms` | the DankMaterialShell plugin: shared `ui/`, backend, assets and translations in one plugin directory |
| `overlays.default` | `tidesi`, `tidesi-cli`, `tidesi-hyprland`, `tidesi-dms` |
| `nixosModules.default` | `programs.tidesi` for NixOS |
| `homeManagerModules.default` | `programs.tidesi` for Home Manager |

All of them pin Python and `secret-tool` to store paths, so the widget works under plasmashell, whose `PATH` has no Python on NixOS.

```nix
# Home Manager
imports = [ tidesi.homeManagerModules.default ];
programs.tidesi = {
  enable = true;        # the Plasma widget, linked into ~/.local/share/plasma/plasmoids
  cli.enable = true;    # tidesi-cli, for the terminal and status bars
  desktop.enable = true;  # `tidesi`, the tray app with the pill, for non-Plasma desktops (autostarts)
  hyprland.enable = true; # tidesi-hyprland, the Quickshell panel
};
```

The NixOS module takes the same options, minus `plasmoid.enable` (Plasma finds the widget under `/run/current-system/sw/share/plasma/plasmoids`) and `dms` (DankMaterialShell is configured from Home Manager).

## DankMaterialShell

`programs.tidesi.dms.enable` registers `packages.dms` with
`programs.dank-material-shell.plugins.tidesi`. Import DMS's Home Manager module as
well; without it the option has no effect:

```nix
imports = [
  inputs.dank-material-shell.homeModules.dank-material-shell
  tidesi.homeManagerModules.default
];

programs.tidesi = {
  enable = true;
  plasmoid.enable = false; # DMS only, no Plasma widget
  dms.enable = true;
};
```

The package copies upstream's `hosts/quickshell/plugin/` host, `ProcessRunner.qml`,
`ui/`, `backend/`, `assets/` and `translate/` into one directory, with Python and
`secret-tool` pinned to store paths. No local assembly or patching is required.
It is also available directly as `packages.dms` for hosts that would rather set
`programs.dank-material-shell.plugins.tidesi.src` themselves.

## nixpkgs-style packages

`nix/nixpkgs/` holds three packages written the way nixpkgs wants them (they fetch a tagged release instead of building the working tree), laid out like `pkgs/by-name`:

| File | Attribute | What |
| --- | --- | --- |
| `tidesi/package.nix` | `tidesi` | the Plasma 6 widget |
| `tidesi-app/package.nix` | `tidesi-app` | the tray app with the floating pill, for any desktop with a tray (binary `tidesi`) |
| `tidesi-cli/package.nix` | `tidesi-cli` | the command line frontend |

`nix-build nix/nixpkgs -A tidesi-cli` builds one from the release its `version` names. CI bumps `version` and `hash` on every tag but does not commit them back (the hash only exists once the tag does), so the copies here can trail the latest release.

## What the release does for you

1. `make tag` bumps `hosts/kde/metadata.json` on a `release/vX.Y.Z` branch and opens a PR; merging it tags `vX.Y.Z`.
2. The **Release** workflow builds the `.plasmoid` and creates the GitHub release.
3. The **nixpkgs** workflow bumps `version` and `hash` in `nix/nixpkgs` with `nix-update`, builds all three packages against the flake's pinned nixpkgs, and uploads the result as an artifact. A failing build means the release is not packageable; fix it before the next tag.

## Once, by hand (the first submission)

1. Fork `NixOS/nixpkgs`. Add yourself to `maintainers/maintainer-list.nix` (alphabetical, in its own commit `maintainers: add muddyblack`) unless it is already there, then set `maintainers = [ lib.maintainers.muddyblack ];` in all package files. Only in the fork: here the files keep `maintainers = [ ];`.
2. Copy the three `package.nix` files (from the workflow's `nixpkgs-packages` artifact, which already has the new version and hash) to `pkgs/by-name/ti/tidesi/`, `pkgs/by-name/ti/tidesi-app/` and `pkgs/by-name/ti/tidesi-cli/`.
3. Check in the fork: `nix-build -A tidesi -A tidesi-app -A tidesi-cli`, `nixfmt` the files, and run `nixpkgs-review wip`. Add the widget to a Plasma session once.
4. Open one pull request per package, titled `tidesi: init at X.Y.Z`, `tidesi-app: init at X.Y.Z` and `tidesi-cli: init at X.Y.Z`, using the nixpkgs PR template.

Nixpkgs rules that matter here: sources come from a tag, builds must work in the sandbox without network, and the install check must be quick.

## After it is merged: nothing

The nixpkgs bot (r-ryantm) notices a new tag, runs the packages' `passthru.updateScript` (`nix-update`), builds, and opens the version-bump pull request. You only approve.

## Notes

- None of the nixpkgs packages contain the Hyprland panel, the DMS plugin, the Windows app or the macOS app; the flake builds the panel and the DMS plugin.
- The flake and the modules build from the working tree; `nix/nixpkgs/` is only for nixpkgs.
