# COSMIC

The panel pill is a native applet (`hosts/cosmic`, Rust + libcosmic). Clicking it
opens the same popup as every other host: the tray app (`hosts/desktop`), which
under COSMIC draws no tray icon and writes what the pill shows to
`$XDG_RUNTIME_DIR/tidesi/cosmic-pill.json`. The applet starts the tray app itself.

## Install

**NixOS / home-manager** (needs nothing built by hand):

```nix
programs.tidesi.cosmic.enable = true;   # applet + the tray app it opens
```

or `nix profile install github:Muddyblack/tidesi#cosmic github:Muddyblack/tidesi#desktop`.
Then add **tideSI** under Settings → Desktop → Panel → Applets (a restart of
`cosmic-panel` may be needed the first time).

**Prebuilt, any distro** — download `tideSI-cosmic-<version>-x86_64.tar.gz` from the
release, unpack it and run `./install.sh`. It puts the applet in `~/.local` and adds it
to the top panel. The popup needs the tray app on `PATH` as `tidesi`:
`pip install -r hosts/desktop/requirements.txt` in a checkout, or your distro's package.

**From source** (a checkout, on NixOS): `make cosmic` builds at low priority — the
first build compiles libcosmic and takes several minutes, later ones seconds — then
installs and adds it to the panel.

`hosts/cosmic/add-to-panel.py --remove` takes it out of the panel again.

## Notes

- The popup opens under the panel at the top right of the primary screen and
  remembers where you drag it. Wayland lets no window place itself, so under COSMIC
  the tray app runs through XWayland (`QT_QPA_PLATFORM=xcb`, unless you set it).
- Only the top panel is touched, not the dock.
