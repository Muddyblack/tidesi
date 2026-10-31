self:
{ config, lib, pkgs, ... }:
let
  cfg = config.programs.tidesi;
  system = pkgs.stdenv.hostPlatform.system;
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
      description = "The Plasma widget package. Plasma finds it under /run/current-system/sw/share/plasma/plasmoids.";
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
        description = "Install the tray app with the floating pill, for any desktop with a tray (GNOME, Sway, XFCE, ...). Start it with `tidesi`.";
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
        description = "Install the Quickshell panel and its tray helper for Hyprland. Start it with `tidesi-hyprland`.";
      };
      package = lib.mkOption {
        type = lib.types.package;
        default = self.packages.${system}.hyprland;
        defaultText = lib.literalExpression "inputs.tidesi.packages.\${system}.hyprland";
        description = "The Hyprland/Quickshell package.";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ cfg.package ]
      ++ lib.optional cfg.cli.enable cfg.cli.package
      ++ lib.optional (cfg.desktop.enable || cfg.cosmic.enable) cfg.desktop.package
      ++ lib.optional cfg.cosmic.enable cfg.cosmic.package
      ++ lib.optional cfg.hyprland.enable cfg.hyprland.package;
  };
}
