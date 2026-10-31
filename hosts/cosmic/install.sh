#!/bin/sh
# Build and install the COSMIC applet, then put it in the panel.
# The build runs at the lowest CPU/IO priority on 4 cores, so the desktop stays responsive.
set -eu
root=$(cd "$(dirname "$0")/../.." && pwd)
bin=$HOME/.local/bin
id=org.muddyblack.tidesi.CosmicApplet

nice -n 19 ionice -c3 nix develop "path:$root#cosmic" --command \
  cargo build --release -j4 --manifest-path "$root/hosts/cosmic/Cargo.toml"

install -Dm755 "$root/hosts/cosmic/target/release/tidesi-cosmic-applet" "$bin/tidesi-cosmic-applet"
install -Dm644 "$root/hosts/cosmic/data/$id.desktop" "$HOME/.local/share/applications/$id.desktop"
install -Dm644 "$root/assets/icons/org.muddyblack.tidesi.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/org.muddyblack.tidesi.svg"
printf '#!/bin/sh\nexec nix develop "path:%s#windows" --command python3 %s/hosts/desktop/app.py "$@"\n' "$root" "$root" > "$bin/tidesi"
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
