"""tidesi-cli — terminal frontend for the shared provider backend.

A third consumer of the contract the Plasma widget and the Quickshell panel
already render, for people who do not have a panel to put a widget on: Plasma 5
desktops, status bars (waybar, tmux, i3blocks) via --compact, and headless
boxes over SSH.

Like the QML frontends this does presentation only. Fetching, parsing and quota
maths stay in the package; the rendering itself is in tidesi.render.
"""

import json
import os
import stat
import sys

from . import accounts, config, envelope, render
from .contract import finalize
from .render import Style

USAGE = """usage: tidesi-cli [--all | --provider <id>[,<id>...]] [options]

  --all                 every provider enabled in the shared settings file (default)
  --provider <ids>      the named providers, regardless of the settings toggles
  --compact             one line, headline value per provider — for status bars
  --json                print the envelope instead of rendering it
  --color <when>        auto (default) | always | never; auto honours NO_COLOR
  --ascii               ASCII bars and rules instead of box drawing
  --list                print the known provider ids, one per line
  -h, --help            show this help

A JSON envelope piped or redirected in is rendered instead of fetching, so a
recorded response can be replayed with no network access:

  get-ai-usage --all | tidesi-cli

providers: """ + " ".join(config.ALL_PROVIDERS)


def _stdin_envelope_waiting():
    """True when stdin is a pipe.

    Deliberately narrower than "not a tty": under cron, a systemd unit or a
    status bar stdin is typically /dev/null or closed, and treating that as
    "an envelope is coming" would turn a normal run into a parse error. A
    regular file is excluded for the same reason — stdin inherited from an
    unrelated redirect (`while read …; done < hosts.txt`, systemd's
    StandardInput=file:) is not an envelope, and eating it breaks the caller.
    """
    try:
        mode = os.fstat(sys.stdin.fileno()).st_mode
    except (OSError, ValueError, AttributeError):
        return False
    return stat.S_ISFIFO(mode)


def _want_color(when, stream):
    if when == "always":
        return True
    if when == "never":
        return False
    # https://no-color.org — any value, including empty, disables colour.
    if os.environ.get("NO_COLOR") is not None:
        return False
    if (os.environ.get("TERM") or "") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def _unicode_ok(stream):
    return "utf" in (getattr(stream, "encoding", "") or "").lower()


def main(argv):
    requested = ""
    compact = False
    as_json = False
    color = "auto"
    force_ascii = False

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--all":
            requested = ""
        elif arg == "--provider":
            i += 1
            if i >= len(argv) or not argv[i]:
                sys.stderr.write("tidesi-cli: --provider needs at least one id\n")
                return 2
            requested = argv[i]
        elif arg.startswith("--provider="):
            requested = arg[len("--provider=") :]
            if not requested:
                sys.stderr.write("tidesi-cli: --provider needs at least one id\n")
                return 2
        elif arg == "--compact":
            compact = True
        elif arg == "--json":
            as_json = True
        elif arg == "--color":
            i += 1
            color = argv[i] if i < len(argv) else ""
        elif arg.startswith("--color="):
            color = arg[len("--color=") :]
        elif arg == "--ascii":
            force_ascii = True
        elif arg == "--list":
            for p in config.ALL_PROVIDERS:
                print(p)
            return 0
        elif arg in ("-h", "--help"):
            print(USAGE)
            return 0
        else:
            sys.stderr.write(f"tidesi-cli: unknown argument: {arg}\n{USAGE}\n")
            return 2
        i += 1

    if color not in ("auto", "always", "never"):
        sys.stderr.write(f"tidesi-cli: --color takes auto, always or never (got: {color or 'nothing'})\n")
        return 2

    selected = []
    if requested:
        for id_ in requested.split(","):
            if id_ not in config.ALL_PROVIDERS and not accounts.find(config.load_settings(), id_):
                sys.stderr.write(f"tidesi-cli: unknown provider: {id_}\n")
                return 2
            selected.append(id_)

    if _stdin_envelope_waiting():
        try:
            env = json.load(sys.stdin)
        except ValueError as e:
            sys.stderr.write(f"tidesi-cli: invalid envelope on stdin: {e}\n")
            return 2
        if selected:
            kept = [p for p in (env.get("providers") or []) if p.get("id") in selected]
            # `active` may name a provider the filter just removed; --json would
            # otherwise emit an envelope that contradicts its own provider list.
            active = env.get("active") if env.get("active") in {p.get("id") for p in kept} else ""
            env = dict(env, providers=kept, active=active)
    else:
        cfg = config.load_settings()
        config.apply_widget_env(cfg)
        env = envelope.build(selected or envelope.enabled(cfg))

    if as_json:
        sys.stdout.write(json.dumps(finalize(env), separators=(",", ":"), ensure_ascii=False) + "\n")
        return 0

    style = Style(_want_color(color, sys.stdout))
    unicode_ok = _unicode_ok(sys.stdout) and not force_ascii
    text = render.render_compact(env, style) if compact else render.render_table(env, style, unicode_ok)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
