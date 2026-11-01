{
  description = "Tidesi: AI usage limits for KDE Plasma 6, Hyprland/Quickshell, DankMaterialShell, COSMIC, the desktop tray and the CLI";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      forAllSystems = f: nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ] (system: f system);
      metadata = builtins.fromJSON (builtins.readFile ./hosts/kde/metadata.json);
      quickshellDesktopFor = pkgs: pkgs.makeDesktopItem {
        name = "org.quickshell";
        desktopName = "Quickshell";
        comment = "QtQuick desktop shell runtime";
        # xdg-desktop-portal resolves this entry in the portal daemon's
        # environment, where a flake-only Quickshell is not on PATH.
        exec = "${pkgs.quickshell}/bin/qs";
        icon = "org.muddyblack.tidesi";
        terminal = false;
        noDisplay = true;
        categories = [ "Utility" ];
      };
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          backendPython = pkgs.python3.withPackages (ps: [ ps.zstandard ]);
        in
        {
          default = pkgs.stdenvNoCC.mkDerivation {
            pname = "tidesi";
            version = metadata.KPlugin.Version;
            # The package is assembled from the shared sources by
            # scripts/build-kde-package.sh, which also compiles the .mo catalogs.
            src = pkgs.lib.fileset.toSource {
              root = ./.;
              fileset = pkgs.lib.fileset.unions [ ./hosts/kde ./ui ./backend ./assets ./translate ./scripts/build-kde-package.sh ];
            };
            nativeBuildInputs = [ pkgs.gettext ];

            dontConfigure = true;

            buildPhase = ''
              runHook preBuild
              bash scripts/build-kde-package.sh build/kde
              runHook postBuild
            '';

            installPhase = ''
              runHook preInstall

              # Install plasmoid package
              root=$out/share/plasma/plasmoids/org.muddyblack.tidesi
              mkdir -p "$root"
              cp -r build/kde/. "$root/"

              # The shell tools resolve Python from PATH, but plasmashell inherits the
              # systemd user session's PATH, which on NixOS has no Python at all — the
              # widget then renders "python3 missing" for every provider. Pin the
              # interpreter so the installed plasmoid is self-contained; this also puts
              # python3 in the closure so it cannot be garbage-collected away.
              # $PYTHON3 still wins at runtime, the PATH candidates still act as
              # fallbacks, and --replace-fail turns a drifted line into a build error
              # rather than a silently unpatched script.
              substituteInPlace "$root/contents/backend/sh/python-interp.sh" \
                --replace-fail 'PY_DEFAULT="python3"' \
                               'PY_DEFAULT="${backendPython}/bin/python3"'
              # Remote devices reuse Antigravity's Secret Service credential.
              substituteInPlace "$root/contents/backend/tidesi/providers/antigravity_remote.py" \
                --replace-fail 'SECRET_TOOL = "secret-tool"' \
                               'SECRET_TOOL = "${pkgs.libsecret}/bin/secret-tool"'
              substituteInPlace "$root/contents/backend/tidesi/providers/zed.py" \
                --replace-fail 'SECRET_TOOL = "secret-tool"' \
                               'SECRET_TOOL = "${pkgs.libsecret}/bin/secret-tool"'

              # Register icon in hicolor theme so Plasma Widget Explorer picks it up
              mkdir -p "$out/share/icons/hicolor/scalable/apps"
              cp assets/icons/org.muddyblack.tidesi.svg "$out/share/icons/hicolor/scalable/apps/org.muddyblack.tidesi.svg"

              runHook postInstall
            '';

            meta = with pkgs.lib; {
              description = "Tidesi: multi-provider AI usage limits for KDE Plasma 6";
              license = licenses.mit;
              platforms = platforms.linux;
              homepage = "https://github.com/Muddyblack/tidesi";
            };
          };

          tray-helper = pkgs.stdenv.mkDerivation {
            pname = "tidesi-tray";
            version = metadata.KPlugin.Version;
            src = ./hosts/quickshell/tray;
            nativeBuildInputs = with pkgs; [ cmake ninja qt6.wrapQtAppsHook ];
            buildInputs = with pkgs; [ qt6.qtbase ];
          };

          # The terminal frontend alone: the Python backend and its launchers, no
          # Qt and no plasmoid, so a server or a terminal-only setup pulls in
          # nothing graphical.
          cli = pkgs.stdenvNoCC.mkDerivation {
            pname = "tidesi-cli";
            version = metadata.KPlugin.Version;
            src = pkgs.lib.fileset.toSource {
              root = ./.;
              fileset = ./backend;
            };
            dontConfigure = true;
            dontBuild = true;
            installPhase = ''
              runHook preInstall
              dest=$out/share/tidesi
              mkdir -p "$dest" "$out/bin"
              cp -r backend "$dest/backend"
              find "$dest" \( -name '__pycache__' -o -name '*.pyc' \) -prune -exec rm -rf {} +
              substituteInPlace "$dest/backend/sh/python-interp.sh" \
                --replace-fail 'PY_DEFAULT="python3"' \
                               'PY_DEFAULT="${backendPython}/bin/python3"'
              substituteInPlace "$dest/backend/tidesi/providers/antigravity_remote.py" \
                --replace-fail 'SECRET_TOOL = "secret-tool"' \
                               'SECRET_TOOL = "${pkgs.libsecret}/bin/secret-tool"'
              substituteInPlace "$dest/backend/tidesi/providers/zed.py" \
                --replace-fail 'SECRET_TOOL = "secret-tool"' \
                               'SECRET_TOOL = "${pkgs.libsecret}/bin/secret-tool"'
              # The launchers resolve symlinks to find the tidesi package beside them.
              ln -s "$dest/backend/sh/tidesi-cli" "$out/bin/tidesi-cli"
              ln -s "$dest/backend/sh/get-ai-usage" "$out/bin/get-ai-usage"
              ln -s tidesi-cli "$out/bin/ai-usage-cli"  # pre-rename name
              runHook postInstall
            '';
            doInstallCheck = true;
            installCheckPhase = ''
              $out/bin/tidesi-cli --help > /dev/null
            '';
            meta = {
              description = "Terminal frontend of Tidesi";
              license = pkgs.lib.licenses.mit;
              platforms = pkgs.lib.platforms.linux;
              mainProgram = "tidesi-cli";
            };
          };

          # The DankMaterialShell (DMS) plugin: the shared UI, assets, catalogs
          # and backend assembled into the one directory the DMS plugin loader
          # expects. The PluginComponent host itself lives in
          # hosts/quickshell/plugin/ and is copied in unchanged; this package is
          # what `programs.dank-material-shell.plugins.tidesi.src` points at.
          dms = pkgs.stdenvNoCC.mkDerivation {
            pname = "tidesi-dms";
            version = metadata.KPlugin.Version;
            src = pkgs.lib.fileset.toSource {
              root = ./.;
              fileset = pkgs.lib.fileset.unions [
                ./hosts/quickshell/plugin
                ./hosts/quickshell/ProcessRunner.qml
                ./ui
                ./backend
                ./assets
                ./translate
                ./LICENSE
              ];
            };
            buildInputs = [ pkgs.bash ];
            strictDeps = true;
            dontConfigure = true;
            dontBuild = true;

            installPhase = ''
              runHook preInstall
              root=$out
              mkdir -p "$root"
              cp hosts/quickshell/plugin/TidesiPlugin.qml \
                 hosts/quickshell/plugin/PluginAppState.qml \
                 hosts/quickshell/plugin/plugin.json \
                 hosts/quickshell/ProcessRunner.qml \
                 LICENSE "$root/"
              cp -r ui assets translate backend "$root/"
              find "$root" \( -name '__pycache__' -o -name '*.pyc' \) -prune -exec rm -rf {} +

              # DMS starts the plugin from its own service PATH, which on NixOS has
              # no Python and no secret-tool. Pin both so the plugin is
              # self-contained, the same way the Plasma, CLI and Hyprland packages
              # do. $PYTHON3 still wins at runtime, and --replace-fail turns a
              # drifted line into a build error instead of a silent regression.
              substituteInPlace "$root/backend/sh/python-interp.sh" \
                --replace-fail 'PY_DEFAULT="python3"' \
                               'PY_DEFAULT="${backendPython}/bin/python3"'
              substituteInPlace "$root/backend/tidesi/providers/antigravity_remote.py" \
                --replace-fail 'SECRET_TOOL = "secret-tool"' \
                               'SECRET_TOOL = "${pkgs.libsecret}/bin/secret-tool"'
              substituteInPlace "$root/backend/tidesi/providers/zed.py" \
                --replace-fail 'SECRET_TOOL = "secret-tool"' \
                               'SECRET_TOOL = "${pkgs.libsecret}/bin/secret-tool"'
              runHook postInstall
            '';

            doInstallCheck = true;
            installCheckPhase = ''
              runHook preInstallCheck
              test -f "$out/plugin.json"
              test -f "$out/TidesiPlugin.qml"
              test -f "$out/ui/AppState.qml"
              test -x "$out/backend/sh/tidesi-cli"
              "$out/backend/sh/tidesi-cli" --help > /dev/null
              runHook postInstallCheck
            '';

            meta = {
              description = "DankMaterialShell plugin of Tidesi: the shared bar pill and popup";
              license = pkgs.lib.licenses.mit;
              platforms = pkgs.lib.platforms.linux;
            };
          };

          # The Quickshell panel for Hyprland, with its tray helper: what
          # `nix run .#hyprland` starts, installable.
          hyprland = pkgs.stdenv.mkDerivation {
            pname = "tidesi-hyprland";
            version = metadata.KPlugin.Version;
            src = pkgs.lib.fileset.toSource {
              root = ./.;
              fileset = pkgs.lib.fileset.unions [ ./hosts/quickshell ./ui ./backend ./assets ./translate ./shell.qml ];
            };
            nativeBuildInputs = with pkgs; [ cmake ninja qt6.wrapQtAppsHook ];
            buildInputs = with pkgs; [ qt6.qtbase qt6.qtwayland ];
            cmakeDir = "../hosts/quickshell/tray";

            postInstall = ''
              root="$out/share/tidesi"
              mkdir -p "$root"
              # shell.qml must sit at the repo root beside hosts/, ui/ and backend/:
              # Quickshell roots its QML sandbox at the entry point's directory.
              # assets/ and translate/ are read at runtime; without them every icon
              # and the watermark fail to load.
              cp -r ../hosts ../ui ../backend ../assets ../translate ../shell.qml "$root/"
              substituteInPlace "$root/backend/sh/python-interp.sh" \
                --replace-fail 'PY_DEFAULT="python3"' \
                               'PY_DEFAULT="${backendPython}/bin/python3"'
              substituteInPlace "$root/backend/tidesi/providers/antigravity_remote.py" \
                --replace-fail 'SECRET_TOOL = "secret-tool"' \
                               'SECRET_TOOL = "${pkgs.libsecret}/bin/secret-tool"'
              substituteInPlace "$root/backend/tidesi/providers/zed.py" \
                --replace-fail 'SECRET_TOOL = "secret-tool"' \
                               'SECRET_TOOL = "${pkgs.libsecret}/bin/secret-tool"'
              # xdg-desktop-portal resolves this entry in the portal daemon's
              # environment, where Quickshell is not on PATH.
              install -Dm644 ${quickshellDesktopFor pkgs}/share/applications/org.quickshell.desktop \
                "$out/share/applications/org.quickshell.desktop"
              install -Dm644 ../assets/icons/org.muddyblack.tidesi.svg \
                "$out/share/icons/hicolor/scalable/apps/org.muddyblack.tidesi.svg"

              cat > "$out/bin/tidesi-hyprland" <<EOF
              #!${pkgs.bash}/bin/bash
              set -eu
              root=$out/share/tidesi
              $out/bin/tidesi-tray ${pkgs.quickshell}/bin/qs "\$root/shell.qml" "\$root/backend/sh/get-ai-usage" &
              tray_pid=\$!
              trap 'kill "\$tray_pid" 2>/dev/null || true' EXIT INT TERM
              ${pkgs.quickshell}/bin/qs -p "\$root/shell.qml"
              EOF
              chmod +x "$out/bin/tidesi-hyprland"
            '';

            meta = {
              description = "Quickshell panel of Tidesi for Hyprland";
              license = pkgs.lib.licenses.mit;
              platforms = pkgs.lib.platforms.linux;
              mainProgram = "tidesi-hyprland";
            };
          };

          # The tray app with the floating pill (hosts/desktop): the same popup as
          # the panel, for any Linux desktop with a tray, with no Plasma and no
          # Quickshell. It runs the backend in-process.
          desktop =
            let
              python = pkgs.python3.withPackages (ps: [ ps.pyside6 ps.psutil ps.zstandard ]);
            in
            pkgs.stdenv.mkDerivation {
              pname = "tidesi-app";
              version = metadata.KPlugin.Version;
              src = pkgs.lib.fileset.toSource {
                root = ./.;
                fileset = pkgs.lib.fileset.unions [ ./hosts/desktop ./ui ./backend ./assets ./translate ];
              };
              nativeBuildInputs = [ pkgs.makeWrapper pkgs.qt6.wrapQtAppsHook ];
              buildInputs = [ pkgs.qt6.qtbase pkgs.qt6.qtdeclarative pkgs.qt6.qtsvg pkgs.qt6.qtwayland ];
              dontWrapQtApps = true;
              dontConfigure = true;
              dontBuild = true;

              installPhase = ''
                runHook preInstall
                root=$out/share/tidesi
                mkdir -p "$root/hosts" "$out/bin"
                cp -r hosts/desktop "$root/hosts/desktop"
                cp -r ui backend assets translate "$root/"
                find "$root" \( -name '__pycache__' -o -name '*.pyc' \) -prune -exec rm -rf {} +
                substituteInPlace "$root/backend/tidesi/providers/antigravity_remote.py" \
                  --replace-fail 'SECRET_TOOL = "secret-tool"' \
                                 'SECRET_TOOL = "${pkgs.libsecret}/bin/secret-tool"'
                substituteInPlace "$root/backend/tidesi/providers/zed.py" \
                  --replace-fail 'SECRET_TOOL = "secret-tool"' \
                                 'SECRET_TOOL = "${pkgs.libsecret}/bin/secret-tool"'
                install -Dm644 assets/icons/org.muddyblack.tidesi.svg \
                  "$out/share/icons/hicolor/scalable/apps/org.muddyblack.tidesi.svg"
                # Single instance: a menu launch while it runs hands over to the
                # running one, so this coexists with the autostart entry.
                install -Dm644 ${pkgs.makeDesktopItem {
                  name = "org.muddyblack.tidesi";
                  desktopName = "Tidesi";
                  genericName = "AI usage monitor";
                  comment = "Provider limits and usage in the tray";
                  exec = "tidesi";
                  icon = "org.muddyblack.tidesi";
                  categories = [ "Utility" ];
                  keywords = [ "AI" "quota" "usage" "Claude" "Codex" ];
                }}/share/applications/org.muddyblack.tidesi.desktop \
                  "$out/share/applications/org.muddyblack.tidesi.desktop"

                # nixpkgs' PySide6 ships no QML modules (QtQuick.Controls ...):
                # they come from qtdeclarative, the same Qt it runs on.
                makeWrapper ${python}/bin/python "$out/bin/tidesi" \
                  --add-flags "-B $root/hosts/desktop/app.py" \
                  --prefix QML_IMPORT_PATH : ${pkgs.qt6.qtdeclarative}/lib/qt-6/qml \
                  --prefix QT_PLUGIN_PATH : ${pkgs.qt6.qtsvg}/lib/qt-6/plugins
                wrapQtApp "$out/bin/tidesi"
                ln -s tidesi "$out/bin/ai-usage"  # pre-rename name
                runHook postInstall
              '';

              doInstallCheck = true;
              installCheckPhase = ''
                export HOME=$TMPDIR XDG_RUNTIME_DIR=$TMPDIR QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software
                $out/bin/tidesi --selftest
              '';

              meta = {
                description = "Tray app with a floating pill for Tidesi, for any Linux desktop";
                license = pkgs.lib.licenses.mit;
                platforms = pkgs.lib.platforms.linux;
                mainProgram = "tidesi";
              };
            };

          # The COSMIC panel applet (hosts/cosmic): the native pill. It needs the
          # tray app (`desktop`) for the popup, which the modules install with it.
          # libcosmicAppHook wraps the binary with the Wayland/GL libraries it loads.
          cosmic = pkgs.rustPlatform.buildRustPackage {
            pname = "tidesi-cosmic";
            version = metadata.KPlugin.Version;
            src = pkgs.lib.fileset.toSource {
              root = ./hosts/cosmic;
              fileset = pkgs.lib.fileset.unions [
                ./hosts/cosmic/Cargo.toml
                ./hosts/cosmic/Cargo.lock
                ./hosts/cosmic/src
                ./hosts/cosmic/data
              ];
            };
            cargoLock = {
              lockFile = ./hosts/cosmic/Cargo.lock;
              # libcosmic and its forks are git dependencies, pinned by rev in the lock.
              allowBuiltinFetchGit = true;
            };
            nativeBuildInputs = [ pkgs.libcosmicAppHook ];
            postInstall = ''
              install -Dm644 data/org.muddyblack.tidesi.CosmicApplet.desktop \
                "$out/share/applications/org.muddyblack.tidesi.CosmicApplet.desktop"
              install -Dm644 ${./assets/icons/org.muddyblack.tidesi.svg} \
                "$out/share/icons/hicolor/scalable/apps/org.muddyblack.tidesi.svg"
            '';
            meta = {
              description = "COSMIC panel applet for Tidesi: the usage pill, drawn natively";
              license = pkgs.lib.licenses.mit;
              platforms = pkgs.lib.platforms.linux;
              mainProgram = "tidesi-cosmic-applet";
            };
          };
        });

      overlays.default = final: prev: {
        tidesi-app = self.packages.${final.stdenv.hostPlatform.system}.desktop;
        tidesi = self.packages.${final.stdenv.hostPlatform.system}.default;
        tidesi-cli = self.packages.${final.stdenv.hostPlatform.system}.cli;
        tidesi-hyprland = self.packages.${final.stdenv.hostPlatform.system}.hyprland;
        tidesi-dms = self.packages.${final.stdenv.hostPlatform.system}.dms;
      };
      nixosModules.default = import ./nix/nixos-module.nix self;
      homeManagerModules.default = import ./nix/home-manager-module.nix self;
      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixpkgs-fmt);

      apps = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          backendPython = pkgs.python3.withPackages (ps: [ ps.zstandard ]);
          quickshellDesktop = quickshellDesktopFor pkgs;
        in
        rec {
          view = {
            type = "app";
            program = toString (pkgs.writeShellScript "view" ''
              if [ ! -f "$PWD/hosts/kde/metadata.json" ]; then
                echo "error: no plasmoid host at $PWD/hosts/kde" >&2
                echo "  'nix run .#view' previews your working copy, so run it from the repo root." >&2
                exit 1
              fi
              export PATH=${pkgs.lib.makeBinPath [ pkgs.kdePackages.plasma-sdk pkgs.kdePackages.plasma-desktop pkgs.gettext ]}:"$PATH"
              "$PWD/scripts/build-kde-package.sh" "$PWD/build/kde"
              exec plasmoidviewer \
                -a "$PWD/build/kde" -f "''${1:-planar}"
            '');
          };
          pack = {
            type = "app";
            program = toString (pkgs.writeShellScript "pack" ''
              set -euo pipefail
              here="$PWD"
              ver="$(grep -oE '"Version":[[:space:]]*"[^"]+"' "$here/hosts/kde/metadata.json" | head -1 | sed -E 's/.*"([^"]+)"$/\1/')"
              name=tidesi
              out="$here/$name-$ver.plasmoid"
              rm -f "$out"
              PATH=${pkgs.gettext}/bin:"$PATH" "$here/scripts/build-kde-package.sh" "$here/build/kde"
              (cd "$here/build/kde" && ${pkgs.zip}/bin/zip -r "$out" . -x '*.swp' '*~')
              echo "wrote $out"
            '');
          };
          # `nix run .#ruff -- format .` / `-- check . --fix`: the pinned linter,
          # without entering the dev shell.
          ruff = {
            type = "app";
            program = "${pkgs.ruff}/bin/ruff";
          };
          cli = {
            type = "app";
            program = toString (pkgs.writeShellScript "tidesi-cli" ''
              set -eu
              export PATH=${pkgs.lib.makeBinPath [ backendPython pkgs.libsecret ]}:"$PATH"
              exec ${self}/backend/sh/tidesi-cli "$@"
            '');
          };
          hyprland = {
            type = "app";
            program = toString (pkgs.writeShellScript "tidesi-hyprland" ''
              set -eu
              export PATH=${pkgs.lib.makeBinPath [
                pkgs.bash
                pkgs.coreutils
                backendPython
                pkgs.libsecret
              ]}:"$PATH"
              # The repo root, not hosts/quickshell/ — Quickshell roots its QML sandbox at
              # the entry point's directory, and hosts/quickshell/ cannot reach the shared
              # ui/ and backend/. See shell.qml.
              config=${self}/shell.qml
              desktop_dir="''${XDG_DATA_HOME:-$HOME/.local/share}/applications"
              ${pkgs.coreutils}/bin/mkdir -p "$desktop_dir"
              ${pkgs.coreutils}/bin/install -m 0644 \
                ${quickshellDesktop}/share/applications/org.quickshell.desktop \
                "$desktop_dir/org.quickshell.desktop"
              ${self.packages.${system}.tray-helper}/bin/tidesi-tray \
                ${pkgs.quickshell}/bin/qs "$config" \
                ${self}/backend/sh/get-ai-usage &
              tray_pid=$!
              trap 'kill "$tray_pid" 2>/dev/null || true' EXIT INT TERM
              ${pkgs.quickshell}/bin/qs -p "$config"
            '');
          };
          view-hyprland = hyprland;
        });

      devShells = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          backendPython = pkgs.python3.withPackages (ps: [ ps.zstandard ]);
        in
        rec {
          default = pkgs.mkShell {
            name = "tidesi-dev";
            packages = with pkgs; [
              qt6.qtdeclarative
              kdePackages.kirigami
              kdePackages.kpackage
              kdePackages.libplasma
              kdePackages.plasma5support
              kdePackages.ksvg
              kdePackages.plasma-sdk
              # gettext provides gettext, msgfmt, xgettext, msgmerge, msgcat, and msgattrib.
              gettext
              pre-commit
              zip
              backendPython
              libsecret
              ruff
              jq
              nodejs
            ];
            shellHook = ''
              # KPackage discovers Plasma package structures through Qt plugins.
              # Prefer the plugin built with this shell's pinned KDE stack over
              # incompatible system plugins inherited through QT_PLUGIN_PATH.
              # plasma5support also supplies the executable data engine that
              # the QML command tests run real shell commands through.
              export QT_PLUGIN_PATH="${pkgs.kdePackages.libplasma}/lib/qt-6/plugins:${pkgs.kdePackages.plasma5support}/lib/qt-6/plugins''${QT_PLUGIN_PATH:+:$QT_PLUGIN_PATH}"
              # qmllint does not discover KDE's QML modules from the Qt import
              # path automatically. Keep this in the development shell so the
              # same imports work for CI, pre-commit, and local editor checks.
              export QML_IMPORT_PATH="${pkgs.kdePackages.kirigami.unwrapped}/lib/qt-6/qml:${pkgs.kdePackages.libplasma}/lib/qt-6/qml:${pkgs.kdePackages.plasma5support}/lib/qt-6/qml:${pkgs.kdePackages.ksvg}/lib/qt-6/qml:${pkgs.qt6.qtdeclarative}/lib/qt-6/qml''${QML_IMPORT_PATH:+:$QML_IMPORT_PATH}"
              pre-commit install -f --install-hooks
              echo "tidesi dev shell ready"
              echo "  make help        — list targets (view, install, pack, tag)"
            '';
          };

          # Profilers and debuggers for performance work (`nix develop .#profiling`).
          # Kept out of the default shell, which CI enters on every lint run.
          profiling = pkgs.mkShell {
            name = "tidesi-profiling";
            inputsFrom = [ default ];
            packages = with pkgs; [
              (python3.withPackages (ps: [ ps.pyinstrument ps.memory-profiler ps.zstandard ]))
              perf
              hotspot
              heaptrack
              valgrind
              gdb
              strace
            ];
            inherit (default) shellHook;
          };

          # The desktop tray app (hosts/desktop/, the Windows and macOS host) runs on Linux too, which is how it is
          # developed: `nix develop .#windows`, then `python hosts/desktop/app.py`.
          # Separate because only people working on that app want PySide6.
          windows = pkgs.mkShell {
            name = "tidesi-windows";
            packages = [
              (pkgs.python3.withPackages (ps: [ ps.pyside6 ps.psutil ps.zstandard ]))
              pkgs.ruff
              pkgs.qt6.qtdeclarative
              pkgs.qt6.qtsvg
            ];
            shellHook = ''
              # nixpkgs' PySide6 ships no QML modules (QtQuick.Controls …): they
              # come from qtdeclarative, which must be the same Qt it runs on.
              # A desktop session exports its own Qt/Plasma paths (a different
              # Qt build) that would be loaded in their place and crash on a
              # symbol mismatch, so those are dropped first.
              unset QML2_IMPORT_PATH QT_PLUGIN_PATH QT_ADDITIONAL_PACKAGES_PREFIX_PATH \
                NIXPKGS_QT6_QML_IMPORT_PATH NIXPKGS_QML_SEARCH_PATHS QT_QPA_PLATFORMTHEME QT_STYLE_OVERRIDE
              export QML_IMPORT_PATH="${pkgs.qt6.qtdeclarative}/lib/qt-6/qml"
              # The provider logos are SVGs: Qt's SVG image-format plugin.
              export QT_PLUGIN_PATH="${pkgs.qt6.qtsvg}/lib/qt-6/plugins"
            '';
          };

          # The COSMIC panel applet (hosts/cosmic, Rust + libcosmic): `nix develop .#cosmic`,
          # then `make cosmic` (or `cargo build --release` in hosts/cosmic).
          cosmic = pkgs.mkShell {
            name = "tidesi-cosmic";
            nativeBuildInputs = with pkgs; [ cargo rustc rustfmt clippy pkg-config ];
            buildInputs = with pkgs; [
              libxkbcommon
              wayland
              libglvnd
              vulkan-loader
              fontconfig
              freetype
              expat
              libinput
              udev
              openssl
            ];
            shellHook = ''
              export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath (with pkgs; [ libxkbcommon wayland libglvnd vulkan-loader ])}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
            '';
          };
        });
    };
}
