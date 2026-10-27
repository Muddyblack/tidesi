"""Tidesi for Windows — a tray icon with the same popup as the Hyprland panel.

The popup is the shared QML under ui/ (PopupContent.qml, SettingsPage.qml
and the components they use); qml/Main.qml wraps it in a frameless window and
implements the `shell` interface they read. The data comes from the same
tidesi package every other frontend runs, called in-process: there is no
shell on Windows to run tools/sh/* through, and nothing here needs one.

It runs on Linux too, which is how it is developed: `nix develop .#windows`
brings PySide6 (or `make run-windows`), else `pip install -r hosts/desktop/requirements.txt`.

  python hosts/desktop/app.py                  start (a second start toggles the popup)
  python hosts/desktop/app.py --selftest       load the QML headless, open every settings
                                         section once, exit 1 on any QML warning
  python hosts/desktop/app.py --screenshot F [--settings]
                                         render the popup (or the settings page)
                                         into F (PNG) and exit
  python hosts/desktop/app.py --screenshot DIR --demo
                                         render every popup tab and settings section into
                                         DIR using demo fixture data (no network,
                                         no credentials) — used by CI
  python hosts/desktop/app.py --tour DIR --demo
                                         run the real app on the real desktop and save
                                         full-screen captures of a few views into DIR
                                         (popup tabs, settings) — CI's only screenshots
"""

import contextlib
import getpass
import io
import json
import os
import re
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
# Frozen, everything the app reads sits under the bundle root in the same
# layout as the repository, so the QML's relative imports resolve unchanged.
ROOT = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from tidesi import config, detect, envelope, history, historyio, paths, snapshot_export, widget_state  # noqa: E402
from tidesi.__main__ import _refresh_pricing, snapshot  # noqa: E402
from tidesi.sessions import collect_sessions, open_session, refresh_sessions  # noqa: E402

APP_NAME = "Tidesi"

# macOS draws its menu bar item natively (hosts/macos/statusitem.py, PyObjC)
# instead of through Qt's tray icons; anywhere else this stays None.
statusitem = None
if paths.IS_MACOS:
    sys.path.insert(0, str(ROOT / "hosts" / "macos"))
    try:
        import statusitem  # noqa: E402
    except ImportError as exc:  # PyObjC missing: fall back to Qt's tray icon
        print(f"Tidesi: native menu bar item unavailable ({exc}); using Qt's", file=sys.stderr)
        statusitem = None

# The popup's system blur (hosts/macos/vibrancy.py) and the login item
# (hosts/macos/loginitem.py, SMAppService); None where unavailable, and the
# QML glass or the LaunchAgent stand in.
vibrancy = None
backdrop = None
taskbar = None
startuptask = None
loginitem = None
if paths.IS_WINDOWS:
    sys.path.insert(0, str(ROOT / "hosts" / "windows"))
    import backdrop  # noqa: E402

    # The Store build (MSIX) starts at sign-in through a startup task, not the Run key.
    import startuptask  # noqa: E402
    import taskbar  # noqa: E402

    if not startuptask.is_packaged():
        startuptask = None

if paths.IS_MACOS:
    try:
        import vibrancy  # noqa: E402
    except ImportError as exc:
        print(f"Tidesi: system blur unavailable ({exc})", file=sys.stderr)
    # SMAppService registers an app bundle; run from a checkout, there is none.
    if getattr(sys, "frozen", False) and ".app/Contents/MacOS/" in sys.executable:
        try:
            import loginitem  # noqa: E402
        except ImportError as exc:
            print(f"Tidesi: login item API unavailable ({exc}); using a LaunchAgent", file=sys.stderr)


def _server_name():
    """The single-instance socket's name, one per user. On Windows it is a
    named pipe, which every session on the machine shares: with one name for
    everybody, a second signed-in user's start was handed to the first user's
    copy instead of starting their own."""
    try:
        user = getpass.getuser()
    except (ImportError, KeyError, OSError):
        user = ""
    user = re.sub(r"[^A-Za-z0-9_.-]", "_", user)
    return f"tidesi-tray-{user}" if user else "tidesi-tray"


SERVER_NAME = _server_name()
ICON_PATH = ROOT / "assets" / "icons" / "org.muddyblack.tidesi.svg"
MAIN_QML = ROOT / "hosts" / "desktop" / "qml" / "Main.qml"
# The settings studio's sections, in sidebar order (ui/SettingsPage.qml).
SETTINGS_SECTIONS = ("providers", "local", "panel", "appearance", "data", "advanced", "info")

# os.environ is process-wide and snapshot() writes the settings' keys into it,
# so one collection runs at a time and puts the environment back afterwards.
_env_lock = threading.Lock()

# Non-None when --demo is active: a pre-built envelope JSON string that stands
# in for a real network call so screenshots can be produced in CI without any
# credentials.
_DEMO_ENVELOPE: str | None = None


def collect_sessions_json(query: str = "", limit: int | None = 60, offset: int = 0, source_ids=None) -> str:
    with _env_lock:
        saved = dict(os.environ)
        try:
            if source_ids is None:
                result = collect_sessions(query, limit=limit, offset=offset)
            else:
                result = collect_sessions(query, limit=limit, offset=offset, source_ids=source_ids)
            return json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        finally:
            _restore_environ(saved)


def refresh_sessions_json(query: str = "", limit: int | None = 60, offset: int = 0, source_ids=None) -> str:
    with _env_lock:
        saved = dict(os.environ)
        try:
            if source_ids is None:
                result = refresh_sessions(query, limit=limit, offset=offset)
            else:
                result = refresh_sessions(query, limit=limit, offset=offset, source_ids=source_ids)
            return json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        finally:
            _restore_environ(saved)


def open_session_json(key):
    with _env_lock:
        saved = dict(os.environ)
        try:
            ok, message = open_session(key)
            return json.dumps({"ok": ok, "message": message}, ensure_ascii=False)
        finally:
            _restore_environ(saved)


def collect_snapshot(*, on_provider=None):
    if _DEMO_ENVELOPE is not None:
        return _DEMO_ENVELOPE
    with _env_lock:
        saved = dict(os.environ)
        try:
            result = snapshot() if on_provider is None else snapshot(on_provider=on_provider)
            # The last good answer, replayed (marked stale) on the next start.
            with contextlib.suppress(OSError, ValueError):
                widget_state.save_snapshot(result)
            return json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        finally:
            _restore_environ(saved)


def refresh_pricing_json():
    # pricing.load_catalog() reads its own cache and does not touch os.environ,
    # so it needs neither the process-wide environment lock nor a save/restore
    # round trip. Holding the lock here needlessly serialized pricing behind
    # every provider collection.
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        _refresh_pricing()
    return output.getvalue().strip()


def _restore_environ(saved):
    """Put os.environ back as `saved` had it, touching only what differs.
    Clearing it and filling it again would leave the process without PATH or
    SYSTEMROOT for a moment, while other threads — a history save, Qt — may be
    reading them."""
    for key in [key for key in os.environ if key not in saved]:
        del os.environ[key]
    for key, value in saved.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


# ── Start at login ───────────────────────────────────────────────────────────
# Windows: the HKCU Run key. macOS: a per-user LaunchAgent. Linux desktops
# start things through their own autostart settings.

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "org.muddyblack.tidesi.plist"
# What the macOS agent and the Windows Run value were called before the rename.
_LEGACY_LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "org.muddyblack.aiUsageWidget.plist"
_LEGACY_RUN_NAME = "AI Usage"
AUTOSTART_AVAILABLE = paths.IS_WINDOWS or paths.IS_MACOS


def _launch_arguments():
    if FROZEN:
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve())]


def _launch_agent_plist():
    import plistlib

    return plistlib.dumps(
        {
            "Label": "org.muddyblack.tidesi",
            "ProgramArguments": _launch_arguments(),
            "RunAtLoad": True,
            "ProcessType": "Interactive",
        }
    )


def migrate_autostart():
    """Carry "start at login" over from earlier builds.

    macOS: first a LaunchAgent plist (under this id, then the pre-rename one),
    now a login item. Windows: the Run value was named "AI Usage" and pointed
    at an executable the installer has since replaced."""
    if paths.IS_WINDOWS:
        _migrate_windows_autostart()
        return
    agents = [agent for agent in (_LAUNCH_AGENT, _LEGACY_LAUNCH_AGENT) if agent.is_file()]
    if loginitem is None or not agents:
        return
    try:
        loginitem.set_enabled(True)
    except OSError as exc:
        print(f"Tidesi: could not move start-at-login to a login item: {exc}", file=sys.stderr)
        return
    for agent in agents:
        with contextlib.suppress(OSError):
            agent.unlink()


def _migrate_windows_autostart():
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _LEGACY_RUN_NAME)
    except OSError:
        return  # no old value: nothing to carry over
    set_autostart(True)


def _launch_command():
    if FROZEN:
        return f'"{sys.executable}"'
    # pythonw has no console, so a login start does not leave a terminal open.
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{interpreter}" "{Path(__file__).resolve()}"'


def autostart_enabled():
    if startuptask is not None:
        try:
            return startuptask.enabled()
        except Exception:  # WinRT unavailable: report it as off
            return False
    if loginitem is not None:
        return loginitem.enabled()
    if paths.IS_MACOS:
        return _LAUNCH_AGENT.is_file()
    if not paths.IS_WINDOWS:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return False


def set_autostart(enabled):
    if startuptask is not None:
        startuptask.set_enabled(enabled)
        return
    if loginitem is not None:
        loginitem.set_enabled(enabled)
        return
    if paths.IS_MACOS:
        if enabled:
            _LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
            _LAUNCH_AGENT.write_bytes(_launch_agent_plist())
        else:
            with contextlib.suppress(FileNotFoundError):
                _LAUNCH_AGENT.unlink()
        return
    if not paths.IS_WINDOWS:
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


# ── Tray icons ───────────────────────────────────────────────────────────────

_PERCENT_RE = re.compile(r"^\s*(\d{1,3})(?:\.\d+)?\s*%\s*$")


# (setting value, tray menu label), in menu order.
TRAY_STYLES = [("icons", "Logo and percent"), ("numbers", "Numbers"), ("ring", "Ring")]
# The ring on Windows: Windows 11 hides every new tray icon behind the ^
# overflow until it is dragged onto the taskbar, and the ring is one icon to
# pin where the other styles are two or more — whose places, all sharing one
# Qt icon id, Windows is not guaranteed to remember. Elsewhere, the look of the
# panel pill.
DEFAULT_TRAY_STYLE = "ring" if paths.IS_WINDOWS else "icons"


def tray_style(state):
    """The style a published state asks for. Settings from before there were
    three styles only had trayNumbers, whose "off" was the ring."""
    style = state.get("style")
    if style in dict(TRAY_STYLES):
        return style
    return "ring" if state.get("numbers") is False else DEFAULT_TRAY_STYLE


def tray_entries(state):
    """What the tray shows for a state Main.qml published: one entry per icon.

    "icons" reads like the panel pill: for every value, the
    provider's logo tinted in the value's colour, then the value as "NN%":
      {"kind": "tinted", …} {"kind": "percent", "value": 0..100, …}
    "numbers": the logo once — its tooltip lists every provider — then each
    value as plain coloured digits:
      {"kind": "logo", …} {"kind": "number", "value": 0..100, …} …
    "ring", or no data yet: one icon, the logo inside a usage ring.

    Windows gives every tray icon the same square, so a logo and its number
    cannot share one. A value that is not a percentage (a balance, a dash) is a
    ring in the first two styles too. `icon` is the logo's file URL, "" for the
    app icon."""
    slots = [s for s in state.get("slots") or [] if isinstance(s, dict)]
    summary = state.get("tooltip") or APP_NAME
    logo = state.get("icon") or ""
    style = tray_style(state)
    if style == "ring" or not slots:
        first = slots[0] if slots else {}
        return [{"kind": "ring", "value": _number(first.get("pct"), -1), "color": first.get("color") or "", "icon": logo, "tooltip": summary}]

    entries = [] if style == "icons" else [{"kind": "logo", "icon": logo, "tooltip": summary}]
    for slot in slots:
        text = slot.get("text") or ""
        match = _PERCENT_RE.match(text) if text else None
        base = {"color": slot.get("color") or "", "icon": logo, "tooltip": slot.get("tooltip") or summary}
        if text and not match:
            entries.append(dict(base, kind="ring", value=_number(slot.get("pct"), 0)))
            continue
        value = int(match.group(1)) if match else round(_number(slot.get("pct"), 0))
        value = max(0, min(value, 100))
        if style == "icons":
            entries.append(dict(base, kind="tinted"))
            entries.append(dict(base, kind="percent", value=value, textColor=_text_colour(value)))
        else:
            entries.append(dict(base, kind="number", value=value, textColor=_text_colour(value)))
    return entries


_DANGER, _WARNING = "#ff4d4d", "#ffa64d"


def _text_colour(value):
    """The panel pill's rule (ui/PanelSlot.qml): the logo carries the
    provider's colour, the number stays neutral until it is worth a look —
    amber from 70 %, red from 90 %. "" is the neutral colour, which depends on
    the taskbar and is decided when drawing."""
    return _DANGER if value >= 90 else _WARNING if value >= 70 else ""


def _number(value, default):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


# ── Translations ─────────────────────────────────────────────────────────────


def catalog_text(language=""):
    """translate/<lang>.po for `language`, the settings page's choice; with ""
    for $LANGUAGE, read the way gettext reads it, and then the system's UI
    languages. "" when none has a catalog, which leaves English ("en" included).
    Picking the file is all this does — Main.qml parses it (I18n.js)."""
    if language:
        languages = [language]
    else:
        languages = [lang for lang in os.environ.get("LANGUAGE", "").split(":") if lang]
        languages += QLocale.system().uiLanguages()
    for lang in languages:
        tag = lang.split(".")[0].split("@")[0].replace("-", "_")
        for name in (tag, tag.split("_")[0]):
            path = ROOT / "translate" / f"{name}.po"
            if re.fullmatch(r"[A-Za-z]{2,3}(_[A-Za-z0-9]+)?", name) and path.is_file():
                return path.read_text(encoding="utf-8")
    return ""


def catalog_languages():
    """The language codes translate/ has a catalog for, for the language picker."""
    return sorted(path.stem for path in (ROOT / "translate").glob("*.po"))


# Qt is the one dependency beyond the backend's standard library; say how to
# get it rather than failing with a bare traceback.
try:
    import PySide6  # noqa: E402, F401
except ImportError:
    sys.exit(
        "Tidesi needs PySide6, which is not installed for this Python.\n"
        "  pip install -r hosts/desktop/requirements.txt\n"
        "or, from a Nix checkout: nix develop .#windows   (or: make run-windows)"
    )

from PySide6.QtCore import (  # noqa: E402
    Property,
    QLocale,
    QObject,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
    qInstallMessageHandler,
)
from PySide6.QtGui import (  # noqa: E402
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

# Imported for its side effect as much as its name: with QtQuick loaded, the
# engine's root comes back as a QQuickWindow (grabWindow() and all) instead of
# the plain QWindow PySide falls back to for a type it does not know.
from PySide6.QtQuick import QQuickWindow  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon  # noqa: E402


class Backend(QObject):
    """What Main.qml calls into: the data, the settings file and the history.

    Anything that can block — a provider over the network, a history save
    waiting on the other frontend's lock — runs on a worker thread and answers
    with a signal, which Qt delivers on the GUI thread."""

    snapshotReady = Signal(str)
    providerReady = Signal(str)
    sessionsReady = Signal(str, str, int)
    openSessionFinished = Signal(str)
    refreshFailed = Signal(str)
    historyFinished = Signal(str, str)
    busyChanged = Signal()
    autostartChanged = Signal()
    trayStateChanged = Signal(str)
    # A setting changed from the tray menu: key, JSON-encoded value.
    settingRequested = Signal(str, str)
    popupToggleRequested = Signal()
    popupLightChanged = Signal(bool)
    trayLabelsChanged = Signal()
    pricingRefreshFinished = Signal(str)
    pricingRefreshFailed = Signal(str)
    pricingBusyChanged = Signal()
    # A completed rate-table query, published on the GUI thread. The query text
    # is carried by the QML side's own request generation.
    ratesReady = Signal(str)
    # Answers to the asynchronous requests ui/AppState.qml makes of every host
    # (ui/CommandBackend.qml is the process-based twin of this class).
    settingsLoaded = Signal(str)
    lastSnapshotLoaded = Signal(str)
    snapshotExported = Signal(str)
    # Part of the interface ui/CommandBackend.qml documents; this app has no
    # terminal frontend to open, so it never emits it.
    cliOpened = Signal(str)
    catalogLoaded = Signal(str)
    languagesLoaded = Signal(str)
    providerDefaultsLoaded = Signal(str)
    providersDetected = Signal(str)
    # Workers only emit these private signals. Public QML signals and property
    # notifications are published by slots on this object's GUI thread.
    _refreshCompleted = Signal(str, str)
    _providerCompleted = Signal(str)
    _sessionsCompleted = Signal(str, str, str, int)
    _openSessionCompleted = Signal(str)
    _historyCompleted = Signal(str, str)
    _pricingCompleted = Signal(str, str)
    _ratesCompleted = Signal(str, int)
    _snapshotCompleted = Signal(str)

    def __init__(self, first_run=False):
        super().__init__()
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tidesi")
        self._busy = False
        self._pricing_busy = False
        self._sessions_request_id = 0
        self._sessions_future = None
        self._rates_request_id = 0
        self._autostart = autostart_enabled()
        self._first_run = first_run
        self._tray_labels = {}
        self._refreshCompleted.connect(self._finish_refresh, Qt.QueuedConnection)
        self._providerCompleted.connect(self.providerReady, Qt.QueuedConnection)
        self._sessionsCompleted.connect(self._finish_sessions, Qt.QueuedConnection)
        self._openSessionCompleted.connect(self.openSessionFinished, Qt.QueuedConnection)
        self._historyCompleted.connect(self._finish_history, Qt.QueuedConnection)
        self._pricingCompleted.connect(self._finish_pricing, Qt.QueuedConnection)
        self._ratesCompleted.connect(self._finish_rates, Qt.QueuedConnection)
        self._snapshotCompleted.connect(self.snapshotExported, Qt.QueuedConnection)

    # ── Data ──
    def _get_busy(self):
        return self._busy

    busy = Property(bool, _get_busy, notify=busyChanged)

    def _get_pricing_busy(self):
        return self._pricing_busy

    pricingBusy = Property(bool, _get_pricing_busy, notify=pricingBusyChanged)

    @Slot()
    def refresh(self):
        if self._busy:
            return
        self._busy = True
        self.busyChanged.emit()
        self._pool.submit(self._refresh)

    def _refresh(self):
        try:
            result = collect_snapshot(
                on_provider=lambda provider: self._providerCompleted.emit(
                    json.dumps(envelope.provider_update(provider), separators=(",", ":"), ensure_ascii=False)
                )
            )
        except Exception as exc:  # the popup shows it; nothing else would
            self._refreshCompleted.emit("", f"usage backend failed: {exc}")
        else:
            self._refreshCompleted.emit(result, "")

    @Slot(str, str)
    def _finish_refresh(self, result, error):
        try:
            if error:
                self.refreshFailed.emit(error)
            else:
                self.snapshotReady.emit(result)
        finally:
            self._busy = False
            self.busyChanged.emit()

    @Slot()
    def refreshPricing(self):
        if self._pricing_busy:
            return
        self._pricing_busy = True
        self.pricingBusyChanged.emit()
        self._pool.submit(self._refresh_pricing)

    def _refresh_pricing(self):
        try:
            result = refresh_pricing_json()
        except Exception as exc:
            self._pricingCompleted.emit("", f"pricing backend failed: {exc}")
        else:
            self._pricingCompleted.emit(result, "")

    @Slot(str, str)
    def _finish_pricing(self, result, error):
        try:
            if error:
                self.pricingRefreshFailed.emit(error)
            else:
                self.pricingRefreshFinished.emit(result)
        finally:
            self._pricing_busy = False
            self.pricingBusyChanged.emit()

    @Slot(str, int, int, result=str)
    def queryRatesJson(self, query="", limit=40, offset=0):
        # Synchronous fallback used by tests and non-GUI callers; QML uses the
        # queued path below so the catalog parse never runs on the GUI thread.
        try:
            from tidesi import pricing

            rows = pricing.catalog_rows(query, limit=limit, offset=offset)
            return json.dumps(rows, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            return ""

    @Slot(str, int, int, int)
    def requestRates(self, query="", limit=40, offset=0, request_id=0):
        if request_id <= self._rates_request_id:
            return
        self._rates_request_id = request_id
        self._pool.submit(self._query_rates, query, limit, offset, request_id)

    def _query_rates(self, query, limit, offset, request_id):
        try:
            from tidesi import pricing

            rows = pricing.catalog_rows(query, limit=limit, offset=offset)
            payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            payload = ""
        self._ratesCompleted.emit(payload, request_id)

    @Slot(str, int)
    def _finish_rates(self, payload, request_id):
        if request_id != self._rates_request_id:
            return
        self.ratesReady.emit(payload)

    @Slot()
    @Slot(str)
    @Slot(str, int)
    @Slot(str, int, int)
    @Slot(str, int, int, list)
    def refreshSessions(self, query="", request_id=0, offset=0, source_ids=None):
        if request_id == 0:
            self._sessions_request_id += 1
            request_id = self._sessions_request_id
        elif request_id <= self._sessions_request_id:
            return
        else:
            self._sessions_request_id = request_id
        normalized_query = query.strip()
        previous_session_future = self._sessions_future
        if previous_session_future is not None:
            previous_session_future.cancel()
        self._sessions_future = self._pool.submit(self._refresh_sessions, normalized_query, request_id, offset, False, source_ids)

    @Slot()
    @Slot(str)
    @Slot(str, int)
    @Slot(str, int, int)
    @Slot(str, int, int, list)
    def refreshSessionsAndQuery(self, query="", request_id=0, offset=0, source_ids=None):
        if request_id == 0:
            self._sessions_request_id += 1
            request_id = self._sessions_request_id
        elif request_id <= self._sessions_request_id:
            return
        else:
            self._sessions_request_id = request_id
        normalized_query = query.strip()
        previous_session_future = self._sessions_future
        if previous_session_future is not None:
            previous_session_future.cancel()
        self._sessions_future = self._pool.submit(self._refresh_sessions, normalized_query, request_id, offset, True, source_ids)

    def _refresh_sessions(self, query, request_id, offset, refresh=False, source_ids=None):
        try:
            # The QML side shows the cached page first and asks for the scan
            # separately, so a refresh is one blocking call on this worker; it
            # must never wait on another task of the same (bounded) pool.
            helper = refresh_sessions_json if refresh else collect_sessions_json
            if source_ids is None:
                result = helper(query, limit=60, offset=offset)
            else:
                result = helper(query, source_ids=source_ids, limit=60, offset=offset)
        except Exception as exc:
            self._sessionsCompleted.emit("", str(exc), query, request_id)
        else:
            self._sessionsCompleted.emit(result, "", query, request_id)

    @Slot(str, str, str, int)
    def _finish_sessions(self, result, error, query, request_id):
        if request_id != self._sessions_request_id:
            return
        if error:
            self.sessionsReady.emit(json.dumps({"error": error, "sessions": []}), query, request_id)
        else:
            self.sessionsReady.emit(result, query, request_id)

    @Slot(str)
    def openSession(self, key):
        self._pool.submit(self._open_session, key)

    def _open_session(self, key):
        try:
            result = open_session_json(key)
        except Exception as exc:
            result = json.dumps({"ok": False, "message": str(exc)})
        self._openSessionCompleted.emit(result)

    # ── History ──
    @Slot(str, str)
    def history(self, op, payload):
        """Run one history-io command; the answer arrives as historyFinished."""
        self._pool.submit(lambda: self._historyCompleted.emit(op, historyio.run(op, payload)))

    @Slot(str, str, result=str)
    def flushHistory(self, op, payload):
        """Run one history-io command synchronously, for a controlled exit."""
        return historyio.run(op, payload)

    @Slot(str, str)
    def _finish_history(self, op, result):
        self.historyFinished.emit(op, result)

    # ── Popup picture ──
    @Slot(str, str, str, int, int)
    def exportSnapshot(self, fmt, src, dest, width, height):
        """Finish a grab the QML saved to `src` as a PNG or SVG at `dest`; the
        answer arrives as snapshotExported ({"ok":true,"path":…} or {"error":…})."""
        self._pool.submit(lambda: self._snapshotCompleted.emit(json.dumps(snapshot_export.export(fmt, src, dest, width, height), ensure_ascii=False)))

    # ── Settings ──
    @Property(str, constant=True)
    def configPath(self):
        return config.config_path()

    @Slot(result=str)
    def loadSettings(self):
        if _DEMO_ENVELOPE is not None:
            return "{}"  # the demo never depends on, or touches, your own setup
        try:
            with open(config.config_path(), encoding="utf-8") as fh:
                settings = json.load(fh)
            if not isinstance(settings, dict):
                settings = {}
        except (OSError, ValueError):
            settings = {}
        if paths.IS_WINDOWS:
            settings.setdefault("popupBgOpacity", 1)
        return json.dumps(settings)

    @Slot()
    def requestLastSnapshot(self):
        if _DEMO_ENVELOPE is not None:
            self.lastSnapshotLoaded.emit("{}")
            return
        try:
            text = json.dumps(widget_state.last_snapshot(), separators=(",", ":"), ensure_ascii=False)
        except (OSError, ValueError):
            text = "{}"
        self.lastSnapshotLoaded.emit(text)

    @Slot()
    def requestSettings(self):
        self.settingsLoaded.emit(self.loadSettings())

    @Slot()
    def initializeProviderDefaults(self):
        """The first start's detection: turns on what is installed, once
        (config latches providerDefaultsApplied). Stat-only, so inline."""
        try:
            result = json.dumps({"ok": True, "data": config.initialize_provider_defaults()})
        except Exception as exc:
            result = json.dumps({"ok": False, "error": str(exc)})
        self.providerDefaultsLoaded.emit(result)

    @Slot()
    def detectProviders(self):
        self.providersDetected.emit(self.detectProvidersJson())

    @Slot(result=str)
    def detectProvidersJson(self):
        """Locally evidenced providers, for the settings page's re-detect
        button. Stat-only (no network, no file contents), so it runs inline."""
        try:
            return json.dumps({"ok": True, "data": detect.detect_providers()})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    @Property(bool, constant=True)
    def firstRun(self):
        """No settings file when the app started. Main.qml writes one straight
        away, and main() opens the popup by itself — both once."""
        return self._first_run

    @Property(str, constant=True)
    def defaultTrayStyle(self):
        return DEFAULT_TRAY_STYLE

    # ── Translations ──
    @Slot(str, result=str)
    def catalogFor(self, language):
        """The .po for `language` ("" follows the system), or "" for English;
        Main.qml parses it with I18n.js — the catalog the Plasma widget compiles."""
        return catalog_text(language)

    @Slot(str)
    def requestCatalog(self, language):
        self.catalogLoaded.emit(catalog_text(language))

    @Slot()
    def requestLanguages(self):
        self.languagesLoaded.emit(json.dumps(catalog_languages()))

    @Property(str, constant=True)
    def languagesJson(self):
        """The catalogs present, as a JSON list, for the language picker."""
        return json.dumps(catalog_languages())

    @Slot(str)
    def setTrayLabels(self, text):
        """The tray menu's words, translated on the QML side: before the menu is
        built, and again whenever the language setting changes."""
        self._tray_labels = json.loads(text)
        self.trayLabelsChanged.emit()

    def tray_label(self, key, english):
        return self._tray_labels.get(key) or english

    @Slot(str)
    def saveSettings(self, text):
        if _DEMO_ENVELOPE is not None:
            return
        path = config.config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        history.replace(tmp, path)

    # ── Assets ──
    @Property(str, constant=True)
    def iconDir(self):
        return QUrl.fromLocalFile(str(ICON_PATH.parent) + os.sep).toString()

    @Property(str, constant=True)
    def appIcon(self):
        return QUrl.fromLocalFile(str(ICON_PATH)).toString()

    @Property(str, constant=True)
    def appVersion(self):
        # hosts/kde/metadata.json is the one place the version lives.
        try:
            with open(ROOT / "hosts" / "kde" / "metadata.json", encoding="utf-8") as fh:
                return str(json.load(fh)["KPlugin"]["Version"])
        except (OSError, ValueError, KeyError):
            return ""

    @Property(str, constant=True)
    def historyDir(self):
        return paths.history_dir()

    # ── Start with Windows ──
    @Property(bool, constant=True)
    def autostartAvailable(self):
        return AUTOSTART_AVAILABLE

    def _get_autostart(self):
        return self._autostart

    autostart = Property(bool, _get_autostart, notify=autostartChanged)

    @Slot(bool)
    def setAutostart(self, enabled):
        try:
            set_autostart(enabled)
        except OSError:
            pass
        self._autostart = autostart_enabled()
        self.autostartChanged.emit()

    # ── Tray ──
    @Slot(str)
    def publishTrayState(self, state):
        """The QML side knows which tab is active; it tells the tray what to show,
        as JSON: {"style": TRAY_STYLES key, "floatingPill": bool, "icon": logo
        URL, "tooltip": str, "slots": [{pct, color, text, tooltip}]}."""
        self.trayStateChanged.emit(state)

    @Slot()
    def togglePopupFromPill(self):
        """The floating pill was clicked; the popup opens next to it."""
        self.popupToggleRequested.emit()

    @Slot(bool)
    def setPopupLight(self, light):
        """The popup turned light or dark (Settings → Appearance → Theme)."""
        self.popupLightChanged.emit(light)


_ICON_SIZE = 64


def _canvas():
    pixmap = QPixmap(_ICON_SIZE, _ICON_SIZE)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    return pixmap, painter


def logo_icon(icon):
    """The provider's own logo — the SVG the popup's tabs show — or the app
    icon for a provider that ships none. Handed to Qt as is, so the tray
    renders the SVG at whatever size it needs."""
    path = QUrl(icon).toLocalFile() if icon.startswith("file:") else icon
    return QIcon(path if path and os.path.isfile(path) else str(ICON_PATH))


def ring_icon(pct, color, icon=""):
    """The provider's logo inside a thin usage ring in the slot's colour.

    A negative pct means no data yet: the bare logo."""
    pixmap, painter = _canvas()
    size, logo = _ICON_SIZE, logo_icon(icon)
    if pct < 0:
        logo.paint(painter, 4, 4, size - 8, size - 8)
    else:
        logo.paint(painter, 17, 17, size - 34, size - 34)
        ring = QRectF(5, 5, size - 10, size - 10)
        # Neutral grey, so the empty part of the ring shows on a light taskbar too.
        painter.setPen(QPen(QColor(128, 128, 128, 90), 5, Qt.SolidLine, Qt.FlatCap))
        painter.drawEllipse(ring)
        painter.setPen(QPen(QColor(color or "#cc785c"), 5, Qt.SolidLine, Qt.RoundCap))
        # Clockwise from twelve o'clock; Qt counts in 1/16 degree, anticlockwise.
        painter.drawArc(ring, 90 * 16, -int(max(0.0, min(pct, 100.0)) / 100.0 * 360 * 16))
    painter.end()
    return QIcon(pixmap)


def number_icon(value, color):
    """The percentage as plain digits in the desktop's own UI font, in the
    colour it is given — no badge, no outline, so it sits among the other tray
    icons like text on the panel."""
    pixmap, painter = _canvas()
    size, text = _ICON_SIZE, str(int(value))
    font = QFont(QGuiApplication.font())
    font.setWeight(QFont.Weight.DemiBold)
    # Digits about two thirds of the icon tall, narrowed to fit: "100" comes out
    # smaller than "93", which is the one case three digits have to squeeze.
    font.setPixelSize(100)
    cap_ratio = QFontMetricsF(font).capHeight() / 100 or 0.7
    font.setPixelSize(max(8, int(size * 0.68 / cap_ratio)))
    width = QFontMetricsF(font).horizontalAdvance(text)
    if width > size - 2:
        font.setPixelSize(max(8, int(font.pixelSize() * (size - 2) / width)))
    metrics = QFontMetricsF(font)
    path = QPainterPath()
    path.addText((size - metrics.horizontalAdvance(text)) / 2, (size + metrics.capHeight()) / 2, font, text)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color or "#cc785c"))
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


def tinted_logo_icon(icon, color):
    """The provider's logo in the value's colour — the panel pill's slot icon,
    which reads severity from its colour.

    A logo that is one tone — a shape, like Claude's or OpenAI's — is filled
    with the colour outright. One with a picture in it, like Kiro's ghost on a
    tile, keeps the picture: its darkest part takes the colour and its lightest
    goes towards white. A flat fill would turn that tile into a solid block, and
    keeping the logo's own lightness would leave OpenAI's black logo black."""
    # These brand logos need purpose-made silhouettes: Kiro has a background
    # tile, and Antigravity uses SVG masks/filters unsupported by some Qt paths.
    path = QUrl(icon).toLocalFile() if icon.startswith("file:") else icon
    symbolic = {"antigravity-color.svg": "antigravity-symbolic.svg", "kiro.svg": "kiro-symbolic.svg"}.get(os.path.basename(path))
    if symbolic:
        icon = os.path.join(os.path.dirname(path), symbolic)
    size = _ICON_SIZE
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    logo_icon(icon).paint(painter, 6, 6, size - 12, size - 12)
    painter.end()

    pixels = [(x, y, image.pixelColor(x, y)) for y in range(size) for x in range(size)]
    pixels = [(x, y, c) for x, y, c in pixels if c.alpha()]
    # The logo's own range of lightness, from its darkest fifth — so that a few
    # small dark details (the ghost's eyes) do not count as its base tone — to
    # its lightest pixel. Edges are left out: antialiasing greys them.
    solid = sorted(c.lightnessF() for _x, _y, c in pixels if c.alpha() > 96)
    lo, hi = (solid[len(solid) // 5], solid[-1]) if solid else (0.0, 0.0)
    hue, saturation, lightness, _alpha = QColor(color or "#cc785c").getHslF()
    hue = max(hue, 0.0)  # -1 for a grey, which has no hue
    pictured = hi - lo >= 0.25
    for x, y, pixel in pixels:
        shade, own = lightness, pixel.lightnessF()
        if pictured and own >= lo:
            shade += (own - lo) / (hi - lo) * (0.97 - lightness)
        elif pictured:
            # Darker than the base tone: the details, kept dark.
            shade *= own / lo
        image.setPixelColor(x, y, QColor.fromHslF(hue, saturation, shade, pixel.alphaF()))
    return QIcon(QPixmap.fromImage(image))


def percent_icon(value, color):
    """The value the way the pill prints it — "83%", the % a size smaller.

    One text size for every value, the largest at which "88%" fits, so "5%"
    and "83%" match side by side; only "100%" has to come out narrower."""
    pixmap, painter = _canvas()
    size, digits = _ICON_SIZE, str(int(value))
    big = QFont(QGuiApplication.font())
    big.setWeight(QFont.Weight.DemiBold)
    small = QFont(big)
    sample = digits if len(digits) > 2 else "88"
    for px in range(60, 8, -1):
        big.setPixelSize(px)
        small.setPixelSize(max(6, int(px * 0.62)))
        width = QFontMetricsF(big).horizontalAdvance(sample) + QFontMetricsF(small).horizontalAdvance("%")
        if width <= size - 2 and QFontMetricsF(big).capHeight() <= size * 0.6:
            break
    big_metrics = QFontMetricsF(big)
    width = big_metrics.horizontalAdvance(digits) + QFontMetricsF(small).horizontalAdvance("%")
    x, baseline = (size - width) / 2, (size + big_metrics.capHeight()) / 2
    path = QPainterPath()
    path.addText(x, baseline, big, digits)
    path.addText(x + big_metrics.horizontalAdvance(digits), baseline, small, "%")
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color or "#cc785c"))
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


def neutral_text_colour():
    """The colour of a number below 70 %: the pill's white on a dark taskbar,
    near-black on a light one. Windows says which its taskbar is (the
    "system" half of the light/dark setting); elsewhere this is the pill's own
    white, which is what a Plasma or Hyprland panel shows."""
    if paths.IS_WINDOWS:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                if winreg.QueryValueEx(key, "SystemUsesLightTheme")[0] == 1:
                    return "#1f1f1f"
        except OSError:
            pass
    return "#f8fafc"


def _render_entry(entry):
    kind, icon, color = entry["kind"], entry.get("icon") or "", entry.get("color") or ""
    if kind == "logo":
        return logo_icon(icon)
    if kind == "tinted":
        return tinted_logo_icon(icon, color)
    if kind == "ring":
        return ring_icon(entry["value"], color, icon)
    text_colour = entry.get("textColor") or neutral_text_colour()
    if kind == "percent":
        return percent_icon(entry["value"], text_colour)
    return number_icon(entry["value"], text_colour)


_RENDERED = {}


def render_entry(entry):
    """The icon for an entry, drawn once per look. The tinted logo is coloured
    pixel by pixel, and the tray is refreshed on every tab switch and setting
    change; the same few icons come back again and again."""
    key = tuple(sorted((k, v) for k, v in entry.items() if k != "tooltip"))
    icon = _RENDERED.get(key)
    if icon is None:
        if len(_RENDERED) > 256:
            _RENDERED.clear()
        icon = _RENDERED[key] = _render_entry(entry)
    return icon


class TrayApp:
    """The tray icon, its menu, the popup window, and single-instance handling."""

    # A click on the tray icon first takes focus from the open popup, which then
    # hides itself; the click itself must not reopen it straight away.
    REOPEN_GRACE = 0.3

    def __init__(self, app, engine, backend):
        self.app = app
        self.backend = backend
        self.window = engine.rootObjects()[0]
        self._hidden_at = 0.0
        # The popup and the floating pill move as one: dragging either brings
        # the other along. _pill_offset is where the pill sits relative to the
        # popup; _syncing marks the moves made here, so they are not taken for
        # the user's.
        self._pill_offset = None
        self._syncing = False
        # macOS: the system blur follows the popup's Theme (light or dark).
        backend.popupLightChanged.connect(lambda _light: self.window.isVisible() and self._apply_vibrancy())

        # The logo and one icon per value in numbers mode, one ring icon
        # otherwise; every one of them opens the popup and has the menu.
        self.icons = []
        self.menus = []
        self._shown = None
        self._anchor = None
        self.style_menus = []
        backend.trayStateChanged.connect(self._on_tray_state)
        backend.trayLabelsChanged.connect(self._retranslate)

        # The actions are made once and shared. Each icon gets a menu of its own
        # around them: one QMenu under several icons leaves KDE's DBus menu
        # exporter without ids for its entries. Their words come from Main.qml
        # and follow its language setting; _worded keeps (action, key, English).
        self._worded = []

        def worded(key, english):
            item = QAction(backend.tray_label(key, english))
            self._worded.append((item, key, english))
            return item

        def action(key, english, slot):
            item = worded(key, english)
            item.triggered.connect(lambda _checked=False: slot())
            return item

        def request(key, value):
            backend.settingRequested.emit(key, json.dumps(value))

        self.actions = [
            action("open", "Open tideSI", self.toggle),
            action("refresh", "Refresh", backend.refresh),
            action("settings", "Settings", self.open_settings),
        ]
        # Tray style: exclusive choices, in a "Tray style" submenu of each menu.
        self.style_group = QActionGroup(app)
        self.style_actions = {}
        for key, english in TRAY_STYLES:
            item = worded(key, english)
            item.setCheckable(True)
            item.setActionGroup(self.style_group)
            item.triggered.connect(lambda _checked=False, key=key: request("trayStyle", key))
            self.style_actions[key] = item
        self.style_actions[DEFAULT_TRAY_STYLE].setChecked(True)
        self.pill_action = worded("floatingPill", "Floating pill")
        self.pill_action.setCheckable(True)
        self.pill_action.toggled.connect(lambda on: request("floatingPill", on))
        self.tail_actions = [self.pill_action]
        self.taskbar_action = None
        if taskbar is not None:
            self.taskbar_action = worded("dockToTaskbar", "Dock pill to taskbar")
            self.taskbar_action.setCheckable(True)
            self.taskbar_action.setChecked(json.loads(backend.loadSettings()).get("windowsTaskbar") is True)
            self.taskbar_action.toggled.connect(lambda on: request("windowsTaskbar", on))
            self.tail_actions.append(self.taskbar_action)
        if backend.autostartAvailable:
            autostart = worded("startWithWindows", "Start with Windows") if paths.IS_WINDOWS else worded("startAtLogin", "Start at login")
            autostart.setCheckable(True)
            autostart.setChecked(backend.autostart)
            autostart.toggled.connect(backend.setAutostart)
            backend.autostartChanged.connect(lambda: autostart.setChecked(backend.autostart))
            self.tail_actions.append(autostart)
        separator = QAction()
        separator.setSeparator(True)
        self.tail_actions += [separator, action("quit", "Quit", app.quit)]

        # macOS: one native menu bar item, with this menu on a right-click.
        self.status = None
        if statusitem is not None:
            self.status_menu = QMenu()
            self.status_menu.addActions(self.actions)
            mac_styles = self.status_menu.addMenu(backend.tray_label("trayStyle", "Tray style"))
            mac_styles.addActions(list(self.style_actions.values()))
            self.status_menu.addActions(self.tail_actions)
            self.style_menus.append(mac_styles)
            self.status = statusitem.StatusItem(on_click=self._on_status_click, on_menu=lambda: self.status_menu.popup(self.status.menu_position()))

        # The floating pill is a window of Main.qml's; placing it is done here,
        # where the screens' taskbar-free areas are known.
        self.pill = self.window.findChild(QQuickWindow, "floatingPill")
        self._pill_placed = False
        self._taskbar_timer = QTimer(app)
        self._taskbar_timer.timeout.connect(self._dock_pill)
        if taskbar is not None:
            self._taskbar_timer.start(1000)
        if self.pill is not None:
            self.pill.visibleChanged.connect(self._on_pill_visible)
            self.pill.placementModeChanged.connect(self._on_pill_placement_changed)
            self.pill.widthChanged.connect(self._keep_pill_on_screen)
            # An open popup travels with the pill while it is dragged.
            self.pill.xChanged.connect(self._follow_pill)
            self.pill.yChanged.connect(self._follow_pill)
            # Grabbing the pill makes it the active window; that must not count
            # as leaving the popup.
            self.pill.activeChanged.connect(self._on_active_changed)
            backend.popupToggleRequested.connect(self.toggle_from_pill)
            self._on_pill_visible()

        self.window.activeChanged.connect(self._on_active_changed)
        # The popup grows and shrinks with its content; keep it on the taskbar.
        self.window.heightChanged.connect(self._reposition)
        # And wider while the settings studio (with its sidebar) is open.
        self.window.widthChanged.connect(self._reposition)
        # The user dragging the popup brings the pill along.
        self.window.xChanged.connect(self._on_panel_moved)
        self.window.yChanged.connect(self._on_panel_moved)
        self._show_entries(tray_entries({}))

    # ── Icons ──
    def _add_icon(self, picture):
        icon = QSystemTrayIcon()
        # The picture goes on before show(): a tray icon shown bare is refused.
        icon.setIcon(picture)
        menu = QMenu()
        menu.addActions(self.actions)
        styles = menu.addMenu(self.backend.tray_label("trayStyle", "Tray style"))
        styles.addActions(list(self.style_actions.values()))
        menu.addActions(self.tail_actions)
        icon.setContextMenu(menu)
        icon.activated.connect(lambda reason, icon=icon: self._on_activated(icon, reason))
        icon.show()
        self.icons.append(icon)
        self.menus.append(menu)
        self.style_menus.append(styles)

    def _retranslate(self):
        for item, key, english in self._worded:
            item.setText(self.backend.tray_label(key, english))
        for styles in self.style_menus:
            styles.setTitle(self.backend.tray_label("trayStyle", "Tray style"))

    def _on_status_click(self):
        self._anchor = self.status
        self.toggle()

    def _show_entries(self, entries):
        """Show `entries` in the tray.

        The tray lays icons out newest first (Plasma does, and Windows adds new
        ones on the left too), so they are made in reverse: that is what puts
        each logo to the left of its number. That only holds for icons made
        together, though — one added next to older ones lands wherever the
        tray puts it, which scrambled the pairs after a tab switch. So the same
        number of icons is updated in place, and a different number means all
        of them are made anew, in one go."""
        if self.status is not None or entries == self._shown:
            return
        self._shown = entries
        entries = list(reversed(entries))
        if len(entries) != len(self.icons):
            while self.icons:
                icon, menu = self.icons.pop(), self.menus.pop()
                self.style_menus.pop()
                if self._anchor is icon:
                    self._anchor = None
                icon.hide()
                icon.deleteLater()
                menu.deleteLater()
            for entry in entries:
                self._add_icon(render_entry(entry))
        else:
            for icon, entry in zip(self.icons, entries):
                icon.setIcon(render_entry(entry))
        for icon, entry in zip(self.icons, entries):
            icon.setToolTip(entry["tooltip"])

    def _on_tray_state(self, text):
        try:
            state = json.loads(text)
        except ValueError:
            return
        if self.status is not None:
            self.status.update(state)
        self._show_entries(tray_entries(state))
        # setChecked() does not emit triggered, so the style items need no
        # blocking; the pill switch reports through toggled, which it does.
        style = tray_style(state)
        for key, item in self.style_actions.items():
            item.setChecked(key == style)
        self.pill_action.blockSignals(True)
        self.pill_action.setChecked(state.get("floatingPill") is True)
        self.pill_action.blockSignals(False)

    # ── Floating pill ──
    def _dock_pill(self):
        """Optional taskbar overlay, using physical Win32 coordinates at the
        primary screen's scale. Explorer owns layout; this reserves no space."""
        if taskbar is None or self.pill is None:
            return False
        enabled = json.loads(self.backend.loadSettings()).get("windowsTaskbar") is True
        self.taskbar_action.blockSignals(True)
        self.taskbar_action.setChecked(enabled)
        self.taskbar_action.blockSignals(False)
        was_docked = self.pill.property("taskbarDocked")
        position = taskbar.place() if enabled and self.pill.isVisible() else None
        screen = QGuiApplication.primaryScreen()
        scale = screen.devicePixelRatio()
        width, height = self.pill.width(), self.pill.height()
        if position is not None:
            (left, top, _right, bottom), tray_left = position
            if (bottom - top) / scale < height or tray_left - left < (width + 8) * scale:
                position = None
        if position is None:
            self.pill.setProperty("taskbarDocked", False)
            if was_docked:
                self._pill_placed = False
                self._place_floating_pill()
            return False
        (left, top, _right, bottom), tray_left = position
        full = screen.geometry()
        # Windows uses physical screen origins, Qt uses logical origins.
        self.pill.setProperty("taskbarDocked", True)
        self.pill.setProperty("taskbarLight", neutral_text_colour() != "#f8fafc")
        self.pill.setPosition(round(full.x() + tray_left / scale - width - 6), round(full.y() + top / scale + ((bottom - top) / scale - height) / 2))
        taskbar.keep_on_top(self.pill)
        return True

    def _saved_pill_position(self):
        try:
            saved = json.loads(self.backend.loadSettings()).get("pillPosition")
            return int(saved["x"]), int(saved["y"])
        except (ValueError, TypeError, KeyError, AttributeError):
            return None

    def _on_pill_placement_changed(self):
        if self.pill.property("placementMode") == "free":
            return
        self._pill_placed = False
        self._on_pill_visible()

    def _on_pill_visible(self):
        if not self._dock_pill():
            self._place_floating_pill()

    def _place_floating_pill(self):
        """Put the pill back where it was left, the first time it shows — or,
        when that spot is on no screen any more, just above the taskbar at the
        bottom right of the main screen."""
        if self._pill_placed or not self.pill.isVisible():
            return
        self._pill_placed = True
        w, h = self.pill.width(), self.pill.height()
        mode = self.pill.property("placementMode") or "free"
        if mode != "free":
            screen = self.pill.screen() or QGuiApplication.primaryScreen()
            area = screen.availableGeometry()
            x = (
                area.x() + 12
                if mode.endswith("-left")
                else area.x() + (area.width() - w) // 2
                if mode.endswith("-center")
                else area.x() + area.width() - w - 12
            )
            y = area.y() + 8 if mode.startswith("top-") else area.y() + area.height() - h - 8
            self.pill.setPosition(x, y)
            return
        saved = self._saved_pill_position()
        if saved and any(s.availableGeometry().intersects(QRect(saved[0], saved[1], w, h)) for s in QGuiApplication.screens()):
            self.pill.setPosition(*saved)
            return
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.pill.setPosition(area.right() - w - 16, area.bottom() - h - 12)

    def _keep_pill_on_screen(self):
        """The pill widens with the number of values; it must not grow off the
        edge it was dragged against."""
        if self._dock_pill() or not self.pill.isVisible():
            return
        if self.pill.property("placementMode") not in (None, "free"):
            self._pill_placed = False
            self._place_floating_pill()
            return
        screen = QGuiApplication.screenAt(self.pill.geometry().center()) or QGuiApplication.primaryScreen()
        right = screen.availableGeometry().right()
        if self.pill.x() + self.pill.width() > right:
            self.pill.setX(right - self.pill.width())

    def toggle_from_pill(self):
        self._anchor = self.pill
        self.toggle()

    def _follow_pill(self):
        if not self._syncing and self.window.isVisible() and self._anchor is self.pill:
            self._place()

    # ── Popup ──
    def toggle(self):
        if self.window.isVisible():
            self.hide()
        elif time.monotonic() - self._hidden_at > self.REOPEN_GRACE:
            self.show()

    def show(self):
        if self.status is not None:
            self.status.activate()
        self._place()
        self.window.show()
        self._apply_vibrancy()
        self.window.raise_()
        self.window.requestActivate()

    def _apply_vibrancy(self):
        """The system blur behind the popup, once its window exists,
        in the popup's light or dark."""
        if backdrop is not None:
            try:
                applied = backdrop.apply(self.window, bool(self.window.property("light")))
                self.window.setProperty("nativeBlur", applied)
            except (OSError, AttributeError) as exc:
                self.window.setProperty("nativeBlur", False)
                print(f"Tidesi: system backdrop failed: {exc}", file=sys.stderr)
            return
        if vibrancy is None:
            return
        try:
            if not self.window.property("nativeBlur") and vibrancy.apply(self.window):
                self.window.setProperty("nativeBlur", True)
            if self.window.property("nativeBlur"):
                vibrancy.set_light(self.window, bool(self.window.property("light")))
        except Exception as exc:  # PyObjC raises its own error types
            print(f"Tidesi: system blur failed: {exc}", file=sys.stderr)

    def hide(self):
        self._hidden_at = time.monotonic()
        self.window.hide()

    def open_settings(self):
        self.window.setProperty("showSettings", True)
        if not self.window.isVisible():
            self.show()

    def _on_active_changed(self):
        # Decided a moment later, once focus has settled: on the way from the
        # popup to the pill neither is active for an instant.
        QTimer.singleShot(150, self._hide_if_left)

    def _hide_if_left(self):
        """Close the popup once focus is on neither it nor the pill."""
        pill_active = self.pill is not None and self.pill.isActive()
        if self.window.isVisible() and not self.window.isActive() and not pill_active:
            self.hide()

    def _on_activated(self, icon, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            # The popup opens next to whichever of the icons was clicked.
            self._anchor = icon
            self.toggle()

    def _reposition(self):
        if self.window.isVisible():
            self._place()

    def _on_panel_moved(self):
        """The user dragged the popup: the pill keeps its place beside it."""
        if self.pill is not None and self.pill.property("taskbarDocked"):
            return
        if self._syncing or self._pill_offset is None or not self.window.isVisible():
            return
        if self._anchor is not self.pill or not self.pill.isVisible():
            return
        self._syncing = True
        try:
            self.pill.setPosition(self.window.x() + self._pill_offset[0], self.window.y() + self._pill_offset[1])
        finally:
            self._syncing = False

    def _place(self):
        self._syncing = True
        try:
            self._place_window()
        finally:
            self._syncing = False
        if self._anchor is self.pill and self.pill is not None and self.pill.isVisible():
            self._pill_offset = (self.pill.x() - self.window.x(), self.pill.y() - self.window.y())

    def _place_window(self):
        """Put the popup against the taskbar, next to the tray icon.

        The taskbar edge is wherever the screen's available area stops short of
        the screen itself; with no taskbar found, bottom-right."""
        anchor = self._anchor or self.status or (self.icons[0] if self.icons else None)
        icon = anchor.geometry() if anchor is not None else QRect()
        screen = QGuiApplication.screenAt(icon.center()) if icon.isValid() else None
        screen = screen or QGuiApplication.primaryScreen()
        area, full = screen.availableGeometry(), screen.geometry()
        w, h, gap = self.window.width(), self.window.height(), 8

        def clamp(value, low, high):
            return max(low, min(value, high))

        if anchor is not None and anchor is self.pill:
            # Opened from the floating pill: under it when there is room, over
            # it otherwise (a pill left just above the taskbar), centred on it.
            x = icon.center().x() - w // 2
            y = icon.bottom() + gap if icon.bottom() + gap + h <= area.bottom() else icon.top() - h - gap
            self.window.setX(clamp(x, area.left() + gap, area.right() - w - gap))
            self.window.setY(clamp(y, area.top() + gap, area.bottom() - h - gap))
            return

        if area.left() > full.left() or area.right() < full.right():
            # Vertical taskbar: beside it, level with the icon.
            x = area.left() + gap if area.left() > full.left() else area.right() - w - gap
            y = icon.center().y() - h // 2 if icon.isValid() else area.bottom() - h - gap
        else:
            y = area.top() + gap if area.top() > full.top() else area.bottom() - h - gap
            x = icon.center().x() - w // 2 if icon.isValid() else area.right() - w - gap
        self.window.setX(clamp(x, area.left() + gap, area.right() - w - gap))
        self.window.setY(clamp(y, area.top() + gap, area.bottom() - h - gap))


# ── KDE Plasma under Wayland ────────────────────────────────────────────────
# Wayland lets no ordinary window keep itself above others, stay out of the
# taskbar or choose where it goes: on a Linux desktop the floating pill sank
# behind every window clicked and showed up in the taskbar, and the popup
# opened in the middle of the screen. KWin, Plasma's compositor, runs scripts
# it is handed over DBus, and this one does all three. Windows needs none of it:
# there the pill is a topmost tool window and TrayApp._place positions the popup.

KWIN_PLUGIN = "tidesi-pill"
PILL_TITLE = "Tidesi pill"  # hosts/desktop/qml/Main.qml, pillWindow.title
POPUP_TITLE = APP_NAME  # hosts/desktop/qml/Main.qml, the root Window's title
_KWIN_SCRIPT = """\
// Loaded by hosts/desktop/app.py for as long as it runs; see _kwin_keep_pill_above().
// The pill and the popup keep above other windows and out of the taskbar, and
// the popup sits under the pill — over it where there is no room below — and
// the two move as one: dragging the pill brings the popup, dragging the popup
// brings the pill — as TrayApp does on Windows. `syncing` marks the moves made
// here, so that one does not set off the other again.
var PILL = "__PILL__", POPUP = "__POPUP__", GAP = 8;
var syncing = false;

function find(caption) {
    var all = workspace.windowList();
    for (var i = 0; i < all.length; i++)
        if (all[i].caption === caption)
            return all[i];
    return null;
}

function placePopup() {
    var pill = find(PILL), popup = find(POPUP);
    if (syncing || !pill || !popup)
        return;
    var p = pill.frameGeometry, g = popup.frameGeometry;
    var area = workspace.clientArea(KWin.MaximizeArea, pill);
    var x = p.x + (p.width - g.width) / 2;
    var y = p.y + p.height + GAP;
    if (y + g.height > area.y + area.height)
        y = p.y - g.height - GAP;
    x = Math.round(Math.max(area.x + GAP, Math.min(x, area.x + area.width - g.width - GAP)));
    y = Math.round(Math.max(area.y + GAP, Math.min(y, area.y + area.height - g.height - GAP)));
    if (x === Math.round(g.x) && y === Math.round(g.y))
        return;
    syncing = true;
    popup.frameGeometry = {x: x, y: y, width: g.width, height: g.height};
    syncing = false;
}

// The popup changed: a new size (its content) puts it back under the pill; a
// move of the user's takes the pill along by as much.
function popupChanged(popup, old) {
    if (syncing)
        return;
    var g = popup.frameGeometry;
    if (g.width !== old.width || g.height !== old.height) {
        placePopup();
        return;
    }
    var pill = find(PILL);
    if (!pill)
        return;
    var p = pill.frameGeometry;
    syncing = true;
    pill.frameGeometry = {x: p.x + g.x - old.x, y: p.y + g.y - old.y, width: p.width, height: p.height};
    syncing = false;
}

function apply(w) {
    if (w.caption !== PILL && w.caption !== POPUP)
        return;
    w.keepAbove = true;
    // Along to whichever virtual desktop is switched to, as a panel would be.
    w.onAllDesktops = true;
    w.skipTaskbar = true;
    w.skipPager = true;
    w.skipSwitcher = true;
    if (w.caption === PILL)
        w.frameGeometryChanged.connect(placePopup);
    else
        w.frameGeometryChanged.connect(function (old) {
            popupChanged(w, old);
        });
    placePopup();
}
workspace.windowList().forEach(apply);
workspace.windowAdded.connect(apply);
""".replace("__PILL__", PILL_TITLE).replace("__POPUP__", POPUP_TITLE)


def _kwin_keep_pill_above():
    """Load the KWin script on a Plasma Wayland session. Returns the function
    that unloads it again, or None where it does not apply or KWin said no."""
    if not (sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY") and "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "")):
        return None
    from PySide6.QtDBus import QDBusConnection, QDBusInterface

    bus = QDBusConnection.sessionBus()
    scripting = QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", bus)
    if not scripting.isValid():
        return None
    path = os.path.join(paths.cache_dir(), "kwin-pill.js")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_KWIN_SCRIPT)
    except OSError:
        return None
    # A copy left loaded by a crashed run would refuse the new one.
    scripting.call("unloadScript", KWIN_PLUGIN)
    reply = scripting.call("loadScript", path, KWIN_PLUGIN)
    script_id = reply.arguments()[0] if reply.arguments() else -1
    if not isinstance(script_id, int) or script_id < 0:
        return None
    QDBusInterface("org.kde.KWin", f"/Scripting/Script{script_id}", "org.kde.kwin.Script", bus).call("run")
    return lambda: scripting.call("unloadScript", KWIN_PLUGIN)


_LOG_LIMIT = 1024 * 1024


def _install_log():
    """Frozen, the app has no console: sys.stdout and sys.stderr are None and
    Qt's warnings go nowhere. All three go to a file instead, so a traceback
    from a slot — or --selftest's warnings, in CI — can still be read."""
    log_dir = paths.history_dir()
    path = os.path.join(log_dir, "tray.log")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError:
        return
    # One older log is kept: a start that finds this one over the limit moves
    # it to tray.log.1, over the one before.
    try:
        if os.path.getsize(path) > _LOG_LIMIT:
            os.replace(path, path + ".1")
    except OSError:
        pass
    try:
        log = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return

    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log

    def handler(_mode, _context, message):
        log.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
        log.flush()

    qInstallMessageHandler(handler)


def _flush_stdio():
    """Flush what print() wrote, before os._exit() skips the interpreter's own
    flush. A windowed build (the .exe) has no console, so either stream can be
    None there — calling .flush() on it raised, and the bootloader turned that
    into an "unhandled exception" dialog on every quit."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.flush()


CAPTURE_HEIGHT = 780


def _crop_to_pill(image):
    """The pill window is grabbed at the size of the popup: cut it down to the
    pill itself, the only part with anything drawn."""
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    xs, ys = [], []
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixel(x, y) >> 24 > 8:
                xs.append(x)
                ys.append(y)
    if not xs:
        return image
    return image.copy(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


def _run_headless(app, engine, backend, warnings, screenshot, settings):
    """--selftest and --screenshot. Exits the process itself: a provider may
    still be waiting on the network in a worker thread, and a normal shutdown
    would sit out its timeout."""
    window = engine.rootObjects()[0]
    window.show()
    page = window.findChild(QObject, "settingsPage")
    if page is None:
        warnings.append("settingsPage not found in the popup")
    if window.findChild(QQuickWindow, "floatingPill") is None:
        warnings.append("floatingPill window not found")

    if screenshot:
        # screenshot can be:
        #   - a file path (legacy single-shot form): one PNG, then exit.
        #   - a directory path (CI / --demo form): popup-NN-<tab>.png per tab, then settings-NN-<section>.png.
        screenshot_path = Path(screenshot)
        is_dir = screenshot_path.suffix == "" or screenshot_path.is_dir()

        if is_dir:
            screenshot_path.mkdir(parents=True, exist_ok=True)
            # One size for every capture, whatever each tab's content needs.
            window.setProperty("fixedHeight", CAPTURE_HEIGHT)

            # Once the first snapshot is in: every popup tab (feature tabs and
            # each provider, popup-NN-<id>.png), then every settings section
            # (settings-NN-<section>.png). Names stay [a-z0-9-] for the PR
            # comment job in macos.yml.
            shots_done = []
            pending = []
            started = []

            def slug(text):
                return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "tab"

            def show_tab(tab_id):
                window.setProperty("showSettings", False)
                window.setProperty("activeId", tab_id)
                if tab_id == "spend":
                    spend = window.findChild(QObject, "spendPage")
                    if spend is not None:
                        spend.setProperty("expandedProviders", {"plan-claude": True})

            def show_section(section):
                window.setProperty("showSettings", True)
                window.setProperty("settingsSection", section)

            def show_sub(tab_id, sub):
                show_tab(tab_id)
                window.setProperty("activeSubTab", sub)

            def show_pill(tab_id):
                # Staged in memory only: assigning settings never saves them.
                show_tab(tab_id)
                current = window.property("settings")
                staged = dict(current.toVariant() if hasattr(current, "toVariant") else current or {})
                staged.update({"floatingPill": True, "pinnedTabs": [tab_id]})
                window.setProperty("settings", staged)

            def plan():
                tab_ids = window.property("popupTabIds") or []
                if hasattr(tab_ids, "toVariant"):
                    tab_ids = tab_ids.toVariant()
                for i, tab_id in enumerate(tab_ids):
                    pending.append((f"popup-{i:02d}-{slug(tab_id)}.png", lambda t=tab_id: show_tab(t)))
                for i, section in enumerate(SETTINGS_SECTIONS):
                    pending.append((f"settings-{i:02d}-{section}.png", lambda s=section: show_section(s)))
                pending.append(("popup-claude-stats.png", lambda: show_sub("claude", "stats")))
                pending.append(("popup-claude-remote.png", lambda: show_sub("claude", "remote")))
                pending.append(("popup-antigravity-remote.png", lambda: show_sub("antigravity", "remote")))
                for tab_id in ("claude", "antigravity"):
                    pending.append((f"pill-{tab_id}.png", lambda t=tab_id: show_pill(t)))

            def next_shot():
                if not pending:
                    app.quit()
                    return
                name, select = pending.pop(0)
                select()
                keep_size()
                QTimer.singleShot(800, lambda: settle(name))

            def settle(name):
                keep_size()
                QTimer.singleShot(300, lambda: grab(name))

            def keep_size():
                # The platform now and then shrinks the window back to its
                # content; put it back so every capture is the same height.
                if window.height() != CAPTURE_HEIGHT:
                    window.setHeight(CAPTURE_HEIGHT)

            def grab(name):
                out = str(screenshot_path / name)
                target = window
                if name.startswith("pill-"):
                    target = window.findChild(QQuickWindow, "floatingPill") or window
                image = target.grabWindow()
                if target is not window:
                    image = _crop_to_pill(image)
                image.save(out)
                print(f"  {name}  ({Path(out).stat().st_size} bytes)")
                shots_done.append(out)
                next_shot()

            def start(_text):
                if started:
                    return
                started.append(True)
                plan()
                QTimer.singleShot(800, next_shot)

            backend.snapshotReady.connect(start)
            # Safety net: if the backend never answers, give up after 60 s.
            QTimer.singleShot(60000, app.quit)
            app.exec()
            for warning in warnings:
                print(warning, file=sys.stderr)
            _flush_stdio()
            os._exit(0 if shots_done and not pending else 1)

        else:
            # Single-file form: legacy behaviour unchanged.
            # The usage page is only worth a picture once the first snapshot is in;
            # the settings page needs nothing from the network.
            if settings:
                window.setProperty("showSettings", True)

            def grab():
                window.grabWindow().save(screenshot)
                app.quit()

            backend.snapshotReady.connect(lambda _text: QTimer.singleShot(800, grab))
            QTimer.singleShot(1500 if settings else 30000, grab)
            app.exec()
            for warning in warnings:
                print(warning, file=sys.stderr)
            _flush_stdio()
            os._exit(0)

    steps = []
    if page is not None:
        # Every section once, so a binding that only breaks on a hidden page
        # still shows up as a warning.
        steps.append(lambda: window.setProperty("showSettings", True))
        for section in SETTINGS_SECTIONS:
            steps.append(lambda s=section: page.setProperty("section", s))
        steps.append(lambda: window.setProperty("showSettings", False))
    # The first step waits for the first refresh and history load to answer.
    for i, step in enumerate(steps):
        QTimer.singleShot(2500 + 400 * i, step)
    QTimer.singleShot(2500 + 400 * len(steps) + 500, app.quit)
    app.exec()

    for warning in warnings:
        print(warning, file=sys.stderr)
    _flush_stdio()
    os._exit(1 if warnings and not screenshot else 0)


def _hand_off_to_running_instance():
    """True when another instance is already up; it is asked to toggle its popup."""
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(300):
        return False
    if paths.IS_WINDOWS:
        # The user just started this process, so Windows lets it bring a
        # window to the front; hand that on to the running copy. Without it
        # the popup can open without focus, and a popup that never had focus
        # never closes on a click elsewhere.
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    socket.write(b"toggle\n")
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return True


# The views the desktop tour captures: the first popup tab, the next two
# popup tabs, and two settings sections. Few on purpose.
TOUR_POPUP_TABS = 3
TOUR_SETTINGS = ("providers", "appearance")


def _run_tour(app, backend, tray, out_dir):
    """--tour: the app as it really runs (tray, popup, pill, native window
    chrome), photographed from the screen — not rendered offscreen — once per
    view. Exits the process itself."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    window = tray.window
    steps = []
    done = []
    started = []

    def view(name, **props):
        def go():
            for key, value in props.items():
                window.setProperty(key, value)
            # The popup hides itself when focus leaves it, which a CI desktop
            # may do at any time.
            if not window.isVisible():
                tray.show()
            QTimer.singleShot(1500, lambda: grab(name))

        steps.append(go)

    def grab(name):
        file = out / name
        # Window id 0: the whole screen, every window on it.
        pixmap = QGuiApplication.primaryScreen().grabWindow(0)
        if pixmap.save(str(file)):
            print(f"  {name}  ({file.stat().st_size} bytes)")
            done.append(name)
        else:
            print(f"could not save {file}", file=sys.stderr)
        advance()

    def advance():
        if not steps:
            app.quit()
            return
        steps.pop(0)()

    def start(_text):
        if started:
            return
        started.append(True)
        tab_ids = window.property("popupTabIds") or []
        if hasattr(tab_ids, "toVariant"):
            tab_ids = tab_ids.toVariant()
        for i, tab_id in enumerate(list(tab_ids)[:TOUR_POPUP_TABS]):
            slug = re.sub(r"[^a-z0-9]+", "-", str(tab_id).lower()).strip("-") or "tab"
            view(f"desktop-{i:02d}-{slug}.png", showSettings=False, activeId=tab_id)
        for i, section in enumerate(TOUR_SETTINGS):
            view(f"desktop-{TOUR_POPUP_TABS + i:02d}-settings-{section}.png", showSettings=True, settingsSection=section)
        QTimer.singleShot(1000, advance)

    backend.snapshotReady.connect(start)
    QTimer.singleShot(90000, app.quit)  # the backend never answered
    app.exec()
    _flush_stdio()
    os._exit(0 if done and not steps else 1)


def main(argv):
    selftest = "--selftest" in argv
    screenshot = argv[argv.index("--screenshot") + 1] if "--screenshot" in argv else ""
    if (selftest or screenshot) and not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    # The shared QML styles every control itself on top of Basic; the native
    # Windows style would fight it.
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

    if "--demo" in argv:
        # Use the same fixture-based envelope the macOS CI screenshots use.
        # No credentials are read and nothing reaches the network.
        global _DEMO_ENVELOPE
        demo_script = ROOT / "scripts" / "demo-envelope.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("demo_envelope", demo_script)
        _demo_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_demo_mod)
        _DEMO_ENVELOPE = json.dumps(_demo_mod.envelope(), separators=(",", ":"), ensure_ascii=False)

    if paths.IS_WINDOWS and not os.environ.get("QT_QPA_FONTDIR"):
        windir = os.environ.get("WINDIR", r"C:\Windows")
        fonts_dir = Path(windir) / "Fonts"
        if fonts_dir.is_dir():
            os.environ["QT_QPA_FONTDIR"] = str(fonts_dir)

    if paths.IS_WINDOWS:
        # Another process holding the clipboard (a clipboard manager, RDP or VM
        # clipboard sync) makes Qt log "Retrying to obtain clipboard" over and
        # over. It retries on its own and nothing here needs the clipboard.
        rules = os.environ.get("QT_LOGGING_RULES", "")
        if "qt.qpa.mime" not in rules:
            os.environ["QT_LOGGING_RULES"] = (rules + ";" if rules else "") + "qt.qpa.mime.warning=false"

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    if paths.IS_WINDOWS:
        windir = os.environ.get("WINDIR", r"C:\Windows")
        fonts_dir = Path(windir) / "Fonts"
        if fonts_dir.is_dir():
            for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "seguisym.ttf", "seguiemj.ttf", "arial.ttf"):
                font_file = fonts_dir / name
                if font_file.exists():
                    QFontDatabase.addApplicationFont(str(font_file))
        app.setFont(QFont("Segoe UI", 10))
    # Every window's icon, wherever the desktop shows one — on Linux, the pill
    # can get a taskbar entry (see docs/windows.md), which otherwise shows a
    # generic one.
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    app.setQuitOnLastWindowClosed(False)
    if statusitem is not None:
        statusitem.accessory_app()

    headless = selftest or screenshot
    if not headless and _hand_off_to_running_instance():
        return 0
    if FROZEN:
        # Headless as well: CI runs the built .exe with --selftest and prints
        # this log when it fails, the .exe having no console to write to.
        _install_log()

    # No settings file yet: the first start after installing. Main.qml writes
    # one as it loads (Backend.firstRun), so this holds only once.
    first_run = not headless and not os.path.isfile(config.config_path())
    if not headless:
        # Best effort: a settings file that cannot be written leaves the legacy
        # provider defaults in force (config.provider_enabled) and retries next start.
        try:
            config.initialize_provider_defaults()
        except OSError as e:
            print(f"Tidesi: could not initialize provider defaults: {e}", file=sys.stderr)
        migrate_autostart()
    backend = Backend(first_run)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
    if not engine.rootObjects():
        print("\n".join(warnings) or "Main.qml did not load", file=sys.stderr)
        return 1

    if headless:
        return _run_headless(app, engine, backend, warnings, screenshot, "--settings" in argv)

    tray = TrayApp(app, engine, backend)
    tour = argv[argv.index("--tour") + 1] if "--tour" in argv else ""
    if first_run or "--demo" in argv or tour:
        # Windows 11 puts a new tray icon out of sight, behind the ^ overflow:
        # the popup opening by itself shows the app is there, the first time.
        QTimer.singleShot(1500, tray.show)
    if tour:
        return _run_tour(app, backend, tray, tour)
    server = QLocalServer()
    QLocalServer.removeServer(SERVER_NAME)
    server.listen(SERVER_NAME)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), tray.toggle()))

    # Ctrl+C in the terminal quits. Python only runs a signal handler between
    # bytecodes, and Qt's event loop gives it none while idle — so a timer wakes
    # the interpreter a few times a second.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    wake = QTimer()
    wake.timeout.connect(lambda: None)
    wake.start(250)

    unload_kwin_script = _kwin_keep_pill_above()
    code = app.exec()
    if unload_kwin_script is not None:
        unload_kwin_script()
    # Leave without waiting for the worker threads: a provider may be waiting on
    # the network, and a normal exit would sit out its timeout. Nothing there
    # needs finishing — a history save lands by atomic rename or not at all.
    _flush_stdio()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
