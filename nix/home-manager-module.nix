self:
{ config, lib, options, pkgs, ... }:
let
  cfg = config.programs.tidesi;
  system = pkgs.stdenv.hostPlatform.system;
  pluginId = "org.muddyblack.tidesi";
in
{
  imports = [
    (lib.mkRenamedOptionModule [ "programs" "ai-usage-widget" ] [ "programs" "tidesi" ])
  ];

  options.programs.tidesi = {
    enable = lib.mkEnableOption "Tidesi, provider limits for KDE Plasma 6, Hyprland/Quickshell and the terminal";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${system}.default;
      defaultText = lib.literalExpression "inputs.tidesi.packages.\${system}.default";
      description = "The Plasma widget package.";
    };

    plasmoid.enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Link the Plasma widget into ~/.local/share/plasma/plasmoids, so Widget Explorer
        finds it and a new build replaces the old one on the next login. Turn off on a
        desktop without Plasma.
      '';
    };

    cli = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Install `tidesi-cli` and `get-ai-usage`: the terminal frontend, for SSH, prompts and status bars. No Qt involved.";
      };
      package = lib.mkOption {
        type = lib.types.package;
        default = self.packages.${system}.cli;
        defaultText = lib.literalExpression "inputs.tidesi.packages.\${system}.cli";
        description = "The command line package.";
      };
    };

    desktop = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Install the tray app with the floating pill, for any desktop with a tray (GNOME, Sway, XFCE, ...). No Plasma or Quickshell needed.";
      };
      autostart = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Start the tray app when you log in.";
      };
      package = lib.mkOption {
        type = lib.types.package;
        default = self.packages.${system}.desktop;
        defaultText = lib.literalExpression "inputs.tidesi.packages.\${system}.desktop";
        description = "The tray app package.";
      };
    };

    cosmic = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Install the COSMIC panel applet (and the tray app it opens as its popup). Then add tideSI under Settings → Desktop → Panel → Applets.";
      };
      package = lib.mkOption {
        type = lib.types.package;
        default = self.packages.${system}.cosmic;
        defaultText = lib.literalExpression "inputs.tidesi.packages.\${system}.cosmic";
        description = "The COSMIC applet package.";
      };
    };

    hyprland = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Install the Quickshell panel and its tray helper for Hyprland. Start it with `tidesi-hyprland`, e.g. from exec-once.";
      };
      package = lib.mkOption {
        type = lib.types.package;
        default = self.packages.${system}.hyprland;
        defaultText = lib.literalExpression "inputs.tidesi.packages.\${system}.hyprland";
        description = "The Hyprland/Quickshell package.";
      };
    };

    dms = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = ''
          Register the DankMaterialShell plugin with
          `programs.dank-material-shell.plugins.tidesi`. Import DMS's Home Manager
          module as well; without it this option has no effect.
        '';
      };
      package = lib.mkOption {
        type = lib.types.package;
        default = self.packages.${system}.dms;
        defaultText = lib.literalExpression "inputs.tidesi.packages.\${system}.dms";
        description = "The DankMaterialShell plugin package.";
      };
    };
  };

  config = lib.mkIf cfg.enable (lib.mkMerge [
    {
      warnings = lib.optional (cfg.dms.enable && !(options ? programs.dank-material-shell.plugins))
        "programs.tidesi.dms.enable has no effect: DankMaterialShell's Home Manager module is not imported.";

      home.packages = lib.optional cfg.plasmoid.enable cfg.package
        ++ lib.optional cfg.cli.enable cfg.cli.package
        ++ lib.optional (cfg.desktop.enable || cfg.cosmic.enable) cfg.desktop.package
        ++ lib.optional cfg.cosmic.enable cfg.cosmic.package
        ++ lib.optional cfg.hyprland.enable cfg.hyprland.package;

      xdg.configFile."autostart/tidesi.desktop" = lib.mkIf (cfg.desktop.enable && cfg.desktop.autostart) {
        text = ''
          [Desktop Entry]
          Type=Application
          Name=Tidesi
          Exec=${cfg.desktop.package}/bin/tidesi
          Icon=org.muddyblack.tidesi
          X-GNOME-Autostart-enabled=true
        '';
      };

      xdg.dataFile = lib.mkIf cfg.plasmoid.enable {
        "plasma/plasmoids/${pluginId}" = {
          source = "${cfg.package}/share/plasma/plasmoids/${pluginId}";
          force = true;
        };
      };
    }
    # Only define the DMS plugin when DMS's module is actually imported; a
    # definition for an undeclared option is an evaluation error even when
    # behind mkIf. The branch must not read `cfg`, or it recurses.
    (lib.optionalAttrs (options ? programs.dank-material-shell.plugins) {
      programs.dank-material-shell.plugins.tidesi = lib.mkIf cfg.dms.enable {
        enable = true;
        src = cfg.dms.package;
      };
    })
  ]);
}
