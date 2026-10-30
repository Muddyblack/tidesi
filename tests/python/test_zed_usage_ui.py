import importlib.util
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path


class ZedUsageUiTests(unittest.TestCase):
    def test_real_qml_chart_handles_allowances_and_unavailable_data(self):
        if importlib.util.find_spec("PySide6") is None:
            self.skipTest("PySide6 unavailable; run in the desktop development shell")
        result = subprocess.run(
            [sys.executable, __file__, "--qml"],
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"},
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


def check_qml():
    from PySide6.QtCore import QLibraryInfo, QUrl, qInstallMessageHandler
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtTest import QSignalSpy

    qInstallMessageHandler(lambda _kind, _context, message: print(message, file=sys.stderr, flush=True))
    app = QGuiApplication([])
    engine = QQmlApplicationEngine()
    engine.setImportPathList(
        [path for path in os.environ.get("QML_IMPORT_PATH", "").split(os.pathsep) if path]
        + [QLibraryInfo.path(QLibraryInfo.LibraryPath.QmlImportsPath)]
    )
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "behavior" / "ZedUsageHarness.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    assert isinstance(window, QQuickWindow)
    light = "--light" in sys.argv
    window.setProperty("light", light)
    base = {
        "available": True,
        "source": "account-api",
        "costStatus": "exact",
        "includedUSD": 10,
        "periodStart": 1_791_763_200,
        "periodEnd": 1_794_441_600,
        "dailyUSD": [{"date": "2026-10-14", "usd": 3}, {"date": "2026-10-17", "usd": 9}],
        "usedUSD": 12,
    }
    cases = {
        "overage": base,
        "below": {**base, "includedUSD": 20},
        "free": {**base, "includedUSD": 0},
        "business": {**base, "includedUSD": None},
        "zero": {**base, "dailyUSD": [], "usedUSD": 0},
        "unavailable": {**base, "available": False, "usedUSD": None},
        "partial": {**base, "costStatus": "partial"},
    }
    if "--live" in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
        from tidesi.providers.zed import get_zed_usage

        cases["live"] = get_zed_usage()["agentUsage"]
    capture = Path(sys.argv[sys.argv.index("--capture") + 1]) if "--capture" in sys.argv else None
    montage = QImage(1240, 300 * len(cases), QImage.Format.Format_ARGB32)
    montage.fill(0xFFF4F6FA if light else 0xFF141820)
    captures = []
    for row, (name, usage) in enumerate(cases.items()):
        offset = 0
        for width in (320, 400, 520):
            if capture:
                print(f"Rendering {name} at {width}px", flush=True)
            frame = QSignalSpy(window.frameSwapped)
            window.setProperty("referenceTime", time.time() if name == "live" else 1_792_281_600)
            window.setWidth(width)
            window.setProperty("usage", engine.evaluate("(" + json.dumps(usage) + ")"))
            window.requestUpdate()
            assert frame.wait(5000), f"No rendered frame: {name}/{width}"
            app.processEvents()
            image = window.grabWindow()
            assert not image.isNull(), f"Empty capture: {name}/{width}"
            data = window.property("rendered").toVariant()
            assert (data["points"] is not None) == usage["available"], name
            assert data["threshold"] == usage["includedUSD"], name
            if capture:
                assert image.save(str(capture / f"{'light-' if light else ''}{name}-{width}.png"))
                captures.append((offset, row * 300, image))
            offset += width
    if capture:
        painter = QPainter(montage)
        try:
            for x, y, image in captures:
                painter.drawImage(x, y, image)
        finally:
            painter.end()
        assert montage.save(str(capture / ("zed-chart-states-light.png" if light else "zed-chart-states.png")))
    assert not warnings, warnings
    print(f"Zed chart: all {len(cases) * 3} rendered states passed")


if __name__ == "__main__":
    if "--qml" in sys.argv:
        check_qml()
    else:
        unittest.main()
