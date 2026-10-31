#!/bin/sh
# Installs the prebuilt applet from a release tarball (no Rust needed) into ~/.local.
# The popup is the tray app: install it too (`pip install -r hosts/desktop/requirements.txt`,
# or your distro's package) so that a `tidesi` command exists.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
bin=$HOME/.local/bin
id=org.muddyblack.tidesi.CosmicApplet
install -Dm755 "$here/tidesi-cosmic-applet" "$bin/tidesi-cosmic-applet"
install -Dm644 "$here/$id.desktop" "$HOME/.local/share/applications/$id.desktop"
# The panel does not have ~/.local/bin on its PATH.
sed -i "s|^Exec=.*|Exec=$bin/tidesi-cosmic-applet|" "$HOME/.local/share/applications/$id.desktop"
install -Dm644 "$here/org.muddyblack.tidesi.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/org.muddyblack.tidesi.svg"
python3 "$here/add-to-panel.py"
echo "Installed. If the panel does not show it, run: pkill cosmic-panel"
