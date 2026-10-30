# PyInstaller spec for the macOS menu bar app. From the repository root, on a Mac:
#
#   pip install -r hosts/macos/build-requirements.txt
#   pyinstaller --noconfirm hosts/macos/tidesi.spec
#
# Produces dist/tideSI.app: the shared tray app (hosts/desktop/app.py) with
# the shared UI, the backend and the native NSStatusItem (statusitem.py). The
# data keeps the repository's layout inside the bundle, so the QML's relative
# imports resolve exactly as in a checkout. hosts/macos/build-app.sh wraps this
# and adds the Finder icon.

import glob
import os
import re
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))  # noqa: F821 — set by PyInstaller
TOOLS = os.path.join(ROOT, "backend")
MACOS = os.path.join(ROOT, "hosts", "macos")
sys.path.insert(0, TOOLS)

with open(os.path.join(ROOT, "hosts", "kde", "metadata.json"), encoding="utf-8") as fh:
    VERSION = re.search(r'"Version":\s*"([^"]+)"', fh.read()).group(1)

datas = [
    (os.path.join(ROOT, "hosts", "desktop", "qml"), os.path.join("hosts", "desktop", "qml")),
    (os.path.join(ROOT, "ui"), "ui"),
    (os.path.join(ROOT, "hosts", "kde", "metadata.json"), os.path.join("hosts", "kde")),
    (os.path.join(ROOT, "assets", "icons"), os.path.join("assets", "icons")),
    (os.path.join(ROOT, "assets", "icon.png"), "assets"),
]
datas += [(path, "translate") for path in glob.glob(os.path.join(ROOT, "translate", "*.po"))]

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "hosts", "desktop", "app.py")],
    pathex=[TOOLS, MACOS],
    hiddenimports=collect_submodules("tidesi") + ["psutil", "zstandard", "statusitem", "vibrancy", "loginitem", "objc", "AppKit", "Foundation", "ServiceManagement"],
    datas=datas,
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="tideSI",
    console=False,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="tideSI")  # noqa: F821
app = BUNDLE(  # noqa: F821
    coll,
    name="tideSI.app",
    icon=os.environ.get("TIDESI_ICNS") or None,
    bundle_identifier="org.muddyblack.tidesi",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "tideSI",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "13.0",
        # No Dock tile and no app menu: this is a menu bar item.
        "LSUIElement": True,
        "NSHumanReadableCopyright": "MIT-licensed. https://github.com/Muddyblack/tidesi",
        "NSSupportsAutomaticTermination": False,
        "NSSupportsSuddenTermination": False,
    },
)
