"""Metadata-only detection of installed provider tools.

A provider counts as detected when its program is installed right now: a CLI
executable, or a desktop application for the IDE-only providers. Logs, session
folders, databases and credential files are deliberately *not* evidence: they
survive an uninstall, so a tool removed months ago would otherwise keep being
"found" forever (issue #60).

Everything here is a stat: no file is read, no program is run, no network.
"""

import glob
import os
import shutil

from . import config, paths

AUTO_DETECT_PROVIDERS = (
    "claude",
    "antigravity",
    "openai",
    "kiro",
    "mistral",
    "grok",
    "muse",
    "cursor",
    "cline",
    "opencode",
    "mimo",
    "junie",
    "windsurf",
    "pi",
    "kilo",
    "coderabbit",
    "commandcode",
)

# Command names each provider's tools install. A desktop application (see
# _APPS) counts as well for the providers that ship one.
_EXECUTABLES = {
    "claude": ("claude",),
    "antigravity": ("agy", "antigravity"),
    "openai": ("codex",),
    "kiro": ("kiro-cli", "kiro"),
    "mistral": ("vibe",),
    "grok": ("grok",),
    "muse": ("muse",),
    "cursor": ("cursor-agent", "cursor"),
    "cline": ("cline",),
    "opencode": ("opencode",),
    "mimo": ("mimo",),
    "junie": ("junie",),
    "windsurf": ("windsurf",),
    "pi": ("pi", "omp"),
    "kilo": ("kilo",),
    "coderabbit": ("coderabbit",),
    # Not `cmd`: that alias is the Windows shell, and command-code is always installed beside it.
    "commandcode": ("command-code", "commandcode"),
}

# Desktop application names: the .desktop id on Linux, the bundle name on
# macOS, the per-user install folder on Windows.
_APPS = {
    "antigravity": ("antigravity", "Antigravity"),
    "kiro": ("kiro", "Kiro"),
    "cursor": ("cursor", "Cursor"),
    "openai": ("codex", "Codex"),
    "windsurf": ("windsurf", "Windsurf"),
}

# The desktop session that starts plasmashell (or a tray app) usually has a far
# shorter PATH than a terminal: user-level installers (npm, bun, the official
# install scripts, Nix per-user profiles) are commonly missing from it. These
# are the places those installers put their commands.
_USER_BIN_DIRS = (
    "~/.local/bin",
    "~/bin",
    "~/.npm-global/bin",
    "~/.bun/bin",
    "~/.cargo/bin",
    "~/.volta/bin",
    "~/.local/share/pnpm",
    "~/.asdf/shims",
    "~/.local/share/mise/shims",
    "~/scoop/shims",
    "~/.nix-profile/bin",
    "~/.claude/local",
    "~/.opencode/bin",
    "~/.grok/bin",
)
# Node version managers keep one bin folder per installed version, and npm
# installs global CLIs (codex, claude, cline, opencode…) into the active one.
_VERSIONED_BIN_GLOBS = (
    "~/.nvm/versions/node/*/bin",
    "~/.local/share/fnm/node-versions/*/installation/bin",
    "~/.fnm/node-versions/*/installation/bin",
)
_SYSTEM_BIN_DIRS = (
    "/run/current-system/sw/bin",
    "/nix/var/nix/profiles/default/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/opt/homebrew/bin",
    "/snap/bin",
)
_SYSTEM_DATA_DIRS = (
    "/usr/local/share",
    "/usr/share",
    "/var/lib/flatpak/exports/share",
    "/run/current-system/sw/share",
    "/var/lib/snapd/desktop",
)
_SYSTEM_APP_DIRS = ("/Applications",)


def _search_path():
    dirs = [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    dirs.extend(os.path.expanduser(entry) for entry in _USER_BIN_DIRS)
    for pattern in _VERSIONED_BIN_GLOBS:
        dirs.extend(sorted(glob.glob(os.path.expanduser(pattern)), reverse=True))
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if user and not paths.IS_WINDOWS:
        dirs.append(f"/etc/profiles/per-user/{user}/bin")
    if paths.IS_WINDOWS:
        roaming = os.environ.get("APPDATA")
        if roaming:
            dirs.append(os.path.join(roaming, "npm"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(os.path.join(local, "Microsoft", "WinGet", "Links"))
    else:
        dirs.extend(_SYSTEM_BIN_DIRS)
    return os.pathsep.join(dict.fromkeys(dirs))


def _has_executable(names, search_path):
    return any(shutil.which(name, path=search_path) for name in names)


def _application_dirs():
    """Where installed desktop applications are registered on this platform."""
    if paths.IS_MACOS:
        return [os.path.expanduser("~/Applications"), *_SYSTEM_APP_DIRS]
    if paths.IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return [os.path.join(local, "Programs")]
    data_dirs = [paths.data_home(), os.path.join(paths.data_home(), "flatpak", "exports", "share")]
    data_dirs.extend(entry for entry in os.environ.get("XDG_DATA_DIRS", "").split(os.pathsep) if entry)
    data_dirs.extend(_SYSTEM_DATA_DIRS)
    return [os.path.join(entry, "applications") for entry in dict.fromkeys(data_dirs)]


def _has_application(names, app_dirs):
    for directory in app_dirs:
        for name in names:
            if paths.IS_MACOS:
                candidate = os.path.join(directory, f"{name}.app")
                if os.path.isdir(candidate):
                    return True
            elif paths.IS_WINDOWS:
                if os.path.isfile(os.path.join(directory, name, f"{name}.exe")):
                    return True
            elif os.path.isfile(os.path.join(directory, f"{name}.desktop")):
                return True
    return False


def detect_providers():
    """Return the providers whose tools are installed, in canonical order."""
    search_path = _search_path()
    app_dirs = _application_dirs()
    found = []
    for provider in config.ALL_PROVIDERS:
        if provider not in AUTO_DETECT_PROVIDERS:
            continue
        if _has_executable(_EXECUTABLES.get(provider, ()), search_path) or _has_application(_APPS.get(provider, ()), app_dirs):
            found.append(provider)
    return found
