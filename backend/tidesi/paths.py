"""Where things live on this platform.

Almost everything a provider reads was written by another program, and that
program follows its platform's conventions: XDG directories on Linux,
%APPDATA% / %LOCALAPPDATA% on Windows, ~/Library on macOS. Every provider asks
here instead of spelling out ``~/.config`` itself.

The XDG variables win whenever they are set, on every platform. That keeps the
tests — which plant each file under a throwaway HOME and point XDG_* at it —
independent of the platform they run on.

macOS is the awkward one, because both conventions are genuinely in use there:
a tool written against XDG keeps reading ~/.config and ~/.local/share on a Mac
(Muse, glm-acp-agent), while one built on a cross-platform directories library
lands in ~/Library/Application Support (the Electron IDEs, kiro-cli). Neither
answer is right for all of them, so the *_dirs() functions hand back both, best
first, and a caller picks the one that exists — see first_file().

Dotfile homes such as ~/.claude, ~/.codex and ~/.gemini need nothing from this
module: those CLIs use ``%USERPROFILE%\\.<name>`` on Windows as well, which is
exactly what ``os.path.expanduser("~/.<name>")`` resolves to there.
"""

import contextlib
import os
import sys

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

# Pinned to libsecret's path by the Nix packages. Kept out of the providers: they
# are part of the session cache key, and a store path that moves with nixpkgs
# must not read as a parser change.
SECRET_TOOL = "secret-tool"

# The folder this tool keeps its own settings, cache and history under, below
# each of config_home(), data_home() and cache_home().
APP_DIR = "tidesi"

# Where those lived before the rename. Two names, because the caches and
# Claude's stats sat in a separate "kde-ai-usage" folder.
_LEGACY_APP_DIRS = ("ai-usage-widget", "kde-ai-usage")
# The Plasma widget's id, which names its shared settings file.
_LEGACY_WIDGET_ID = "org.muddyblack.aiUsageWidget"
_WIDGET_ID = "org.muddyblack.tidesi"


def _windows_known(var, fallback):
    """A Windows known folder from its environment variable, with the default
    location under the profile for the rare session that lacks the variable."""
    return os.environ.get(var) or os.path.join(os.path.expanduser("~"), "AppData", fallback)


def config_home():
    """$XDG_CONFIG_HOME, else ~/.config — %APPDATA% on Windows."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return xdg
    if IS_WINDOWS:
        return _windows_known("APPDATA", "Roaming")
    return os.path.expanduser("~/.config")


def data_home():
    """$XDG_DATA_HOME, else ~/.local/share — %LOCALAPPDATA% on Windows."""
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return xdg
    if IS_WINDOWS:
        return _windows_known("LOCALAPPDATA", "Local")
    return os.path.expanduser("~/.local/share")


def cache_home():
    """$XDG_CACHE_HOME, else ~/.cache — %LOCALAPPDATA%\\cache on Windows.

    Windows has no cache folder of its own; the subfolder keeps caches apart
    from data_home(), which is %LOCALAPPDATA% itself there."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return xdg
    if IS_WINDOWS:
        return os.path.join(_windows_known("LOCALAPPDATA", "Local"), "cache")
    return os.path.expanduser("~/.cache")


def electron_app_data():
    """Electron's appData: the parent of a VS Code-family IDE's own folder
    (Cursor, Kiro, …), which holds User/globalStorage/state.vscdb.

    Electron follows XDG_CONFIG_HOME on Linux and uses %APPDATA% on Windows,
    which is config_home() on both; macOS is the one that differs."""
    if IS_MACOS and not os.environ.get("XDG_CONFIG_HOME"):
        return os.path.expanduser("~/Library/Application Support")
    return config_home()


# The one directory on macOS that neither an XDG variable nor a platform
# default can be relied on to name: programs written against a cross-platform
# directories library put both their config and their data here.
APPLE_APP_SUPPORT = "~/Library/Application Support"


def _with_app_support(dirs):
    """`dirs` plus ~/Library/Application Support on macOS, if not already there.

    Second, not first, for two reasons: a path this module derived from an XDG
    variable is something the user asked for explicitly, and the suites here
    plant their files under a throwaway XDG home that has to keep winning."""
    native = os.path.expanduser(APPLE_APP_SUPPORT)
    if IS_MACOS and native not in dirs:
        return dirs + [native]
    return dirs


def electron_app_data_dirs():
    """Every directory a VS Code-family IDE's folder may be under, best first.

    One entry everywhere but macOS. There, Electron ignores XDG_CONFIG_HOME —
    but this module does not, so a Mac user whose dotfiles export that variable
    would otherwise have Cursor and Kiro looked for somewhere the IDEs never
    write."""
    return _with_app_support([electron_app_data()])


def data_home_dirs():
    """Every directory a program may keep its private data under, best first.

    Both conventions are in use on a Mac. A tool written against XDG keeps
    using XDG there (Muse, glm-acp-agent); one built on a cross-platform
    directories library follows the platform and lands in ~/Library/Application
    Support (kiro-cli, through Rust's `dirs`). data_home() knows only the
    first, so on macOS this offers the second after it."""
    return _with_app_support([data_home()])


def first_file(candidates):
    """The first of `candidates` that is a file, else the first one — so a
    caller that reports "not installed" still names a place it looked."""
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[0] if candidates else ""


def _merge_dir(old, new):
    """Move everything in `old` into `new`, keeping whatever `new` already has.

    A whole-folder rename when `new` does not exist yet, which is the normal
    case. Both existing happens when a frontend that already looked for the new
    path wrote there first; its files win and only the missing ones move over.
    Best effort: a folder that cannot be moved is simply left where it is."""
    if not os.path.isdir(old) or os.path.realpath(old) == os.path.realpath(new):
        return
    try:
        if not os.path.exists(new):
            os.rename(old, new)
            return
        for name in os.listdir(old):
            target = os.path.join(new, name)
            if not os.path.exists(target):
                os.rename(os.path.join(old, name), target)
        os.rmdir(old)
    except OSError:
        pass


def _rename_widget_settings(config_dir):
    """plasma-shared-settings-<widget id>.json follows the widget's id, which
    changed with the rename. The "...Test" copy `make install` adds follows too."""
    try:
        names = os.listdir(config_dir)
    except OSError:
        return
    for name in names:
        if _LEGACY_WIDGET_ID in name:
            target = os.path.join(config_dir, name.replace(_LEGACY_WIDGET_ID, _WIDGET_ID))
            if not os.path.exists(target):
                with contextlib.suppress(OSError):
                    os.rename(os.path.join(config_dir, name), target)


_MIGRATED = set()


def _migrated(root):
    """`root`/tidesi, after pulling in what an earlier version left under the
    old folder names. Once per root and process; the answer is the same path
    either way."""
    new = os.path.join(root, APP_DIR)
    if new not in _MIGRATED:
        _MIGRATED.add(new)
        for legacy in _LEGACY_APP_DIRS:
            _merge_dir(os.path.join(root, legacy), new)
    return new


def config_dir():
    """This tool's settings folder (see APP_DIR)."""
    path = _migrated(config_home())
    key = path + "#widget"
    if key not in _MIGRATED:
        _MIGRATED.add(key)
        _rename_widget_settings(path)
    return path


def data_dir():
    """This tool's history and stats folder (see APP_DIR)."""
    return _migrated(data_home())


def cache_dir():
    """This tool's cache folder (see APP_DIR)."""
    return _migrated(cache_home())


def history_dir():
    """Where the shared usage-history file and its snapshots live."""
    return data_dir()


def no_window():
    """Keyword arguments for subprocess calls that keep Windows from opening a
    console for a console program (codex, gh, …).

    The Windows frontend is a GUI program with no console of its own, so each
    child would otherwise flash a terminal window up on every poll. Empty on
    every other platform."""
    if IS_WINDOWS:
        # Imported here: history saves import this module and are otherwise
        # kept free of heavy imports (see history.save).
        import subprocess

        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}
