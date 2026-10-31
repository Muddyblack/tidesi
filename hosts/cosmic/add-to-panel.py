#!/usr/bin/env python3
"""Add the tideSI applet to the right side of COSMIC's top panel, once.

cosmic-panel reloads its config files as they change, so nothing restarts. The
dock is left alone. Run with --remove to take it out again."""

import os
import sys

APPLET = "org.muddyblack.tidesi.CosmicApplet"
PATH = os.path.expanduser("~/.config/cosmic/com.system76.CosmicPanel.Panel/v1/plugins_wings")

try:
    text = open(PATH, encoding="utf-8").read()
except OSError:
    sys.exit("No COSMIC panel config found: add tideSI under Settings → Desktop → Panel → Applets.")

line = f'        "{APPLET}",\n'
if "--remove" in sys.argv:
    new = text.replace(line, "")
elif APPLET in text:
    new = text
else:
    # Some(( [left...], [right...] )): first entry of the right list.
    head, sep, right = text.rpartition("[")
    new = head + "[\n" + line + right.lstrip("\n") if sep else text
if new != text:
    open(PATH, "w", encoding="utf-8").write(new)
    print("Updated", PATH)
