#!/usr/bin/env bash
# Runs a development tool for the pre-commit hooks: from the pinned dev shell
# when Nix is available, otherwise from PATH.
set -euo pipefail

# IN_NIX_SHELL alone does not mean this project's shell: any Nix shell sets it.
if { [ -z "${IN_NIX_SHELL:-}" ] || ! command -v "$1" >/dev/null 2>&1; } &&
    command -v nix >/dev/null 2>&1; then
    exec nix develop --command "$@"
fi

tool="$1"
shift
if ! command -v "$tool" >/dev/null 2>&1; then
    # Distributions keep the Qt tools under other names.
    for candidate in "${tool}6" "/usr/lib/qt6/bin/$tool" "/usr/lib64/qt6/bin/$tool"; do
        if command -v "$candidate" >/dev/null 2>&1; then
            tool="$candidate"
            break
        fi
    done
fi
if ! command -v "$tool" >/dev/null 2>&1; then
    echo "error: $tool not found — install it (or install Nix to use the dev shell)" >&2
    exit 1
fi
exec "$tool" "$@"
