#!/bin/sh
# Build and install the COSMIC applet, then put it in the panel.
# The build runs at the lowest CPU/IO priority on 4 cores, so the desktop stays responsive.
set -eu
root=$(cd "$(dirname "$0")/../.." && pwd)
bin=$HOME/.local/bin
id=org.muddyblack.tidesi.CosmicApplet

nice -n 19 ionice -c3 nix develop "git+file://$root#cosmic" --command \
  cargo build --release -j4 --manifest-path "$root/hosts/cosmic/Cargo.toml"

install -Dm755 "$root/hosts/cosmic/target/release/tidesi-cosmic-applet" "$bin/tidesi-cosmic-applet.bin"
# NixOS has no global library path: the applet loads libwayland, libxkbcommon
# and the GL/Vulkan libraries at run time, so it starts through a wrapper that
# hands it the ones the dev shell used to build it.
libs=$(nix develop "git+file://$root#cosmic" --command sh -c 'printf %s "$LD_LIBRARY_PATH"')
printf '#!/bin/sh\nLD_LIBRARY_PATH="%s${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" exec "%s/tidesi-cosmic-applet.bin" "$@"\n' "$libs" "$bin" > "$bin/tidesi-cosmic-applet"
chmod +x "$bin/tidesi-cosmic-applet"
install -Dm644 "$root/hosts/cosmic/data/$id.desktop" "$HOME/.local/share/applications/$id.desktop"
# The panel does not have ~/.local/bin on its PATH.
sed -i "s|^Exec=.*|Exec=$bin/tidesi-cosmic-applet|" "$HOME/.local/share/applications/$id.desktop"
install -Dm644 "$root/assets/icons/org.muddyblack.tidesi.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/org.muddyblack.tidesi.svg"
# The launcher bakes in the desktop host's Python/Qt environment, resolved once
# here: running nix on every start (and for every applet instance) is slow.
env=$(nix develop "git+file://$root#windows" --command sh -c \
  'printf "PATH=%s\nQML_IMPORT_PATH=%s\nQT_PLUGIN_PATH=%s\n" "$PATH" "$QML_IMPORT_PATH" "$QT_PLUGIN_PATH"')
{
  echo '#!/bin/sh'
  echo "$env" | sed "s/^\([A-Z_]*\)=\(.*\)\$/export \1='\2'/"
  echo "unset QML2_IMPORT_PATH QT_ADDITIONAL_PACKAGES_PREFIX_PATH NIXPKGS_QT6_QML_IMPORT_PATH NIXPKGS_QML_SEARCH_PATHS QT_QPA_PLATFORMTHEME QT_STYLE_OVERRIDE"
  echo "exec python3 \"$root/hosts/cosmic/../desktop/app.py\" \"\$@\""
} > "$bin/tidesi"
chmod +x "$bin/tidesi"

# Add the applet to the right side of every panel, once. cosmic-panel reloads
# its config files as they change, so nothing needs restarting.
python3 - "$id" <<'PY'
import glob, os, sys
applet = sys.argv[1]
for path in glob.glob(os.path.expanduser("~/.config/cosmic/com.system76.CosmicPanel.*/v1/plugins_wings")):
    text = open(path).read()
    if applet in text:
        continue
    # Some(( [left...], [right...] )): first entry of the right list.
    head, sep, right = text.rpartition("[")
    if not sep:
        continue
    open(path, "w").write(head + "[\n        \"" + applet + "\",\n" + right.lstrip("\n"))
    print("Added to", path)
PY
echo "Done: tideSI is in the panel."
