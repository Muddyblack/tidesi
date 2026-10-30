"""get-ai-usage — the shared provider backend for every frontend of this widget.

This tool owns credential discovery, provider API requests, response parsing,
quota/percentage maths, reset timestamps and error/stale state. The KDE Plasma
widget and the Hyprland/Quickshell shell both consume its output and do
presentation only; see docs/provider-contract.md for the schema.

  get-ai-usage --provider claude          one provider (KDE's active tab)
  get-ai-usage --provider claude,openai   several providers (active + pinned)
  get-ai-usage --all                      every enabled provider (Hyprland)
  get-ai-usage --normalize < envelope     replay a raw envelope offline (tests)
"""

import json
import os
import sys

from . import config, detect, envelope, pricing, widget_state
from .contract import finalize
from .normalize import normalize
from .session_index import SOURCE_REGISTRY

USAGE = """usage: get-ai-usage [--all | --provider <id>[,<id>...] | --normalize | --sessions | --refresh-pricing | --pricing-table |
--detect-providers | --initialize-provider-defaults | --last-snapshot | --shared-settings]

  --all                 refresh enabled providers; Zed account data uses its last
                        on-demand result without another request
  --provider <ids>      refresh named providers, including on-demand Zed account data
  --normalize           read one raw envelope on stdin, print the provider object
  --sessions            list recent local agent sessions (no paths or transcripts)
  --refresh-pricing     force one shared pricing catalog refresh
  --pricing-table       cached model rates as a searchable page
                        (--query, --limit, --offset)
  --detect-providers    report locally evidenced providers without fetching data
  --initialize-provider-defaults
                        apply shared provider defaults once and print settings
  --save-snapshot       with --all/--provider: keep the envelope as the last good one
  --stream              emit each completed provider as a partial JSON line,
                        followed by the complete envelope (with --all/--provider)
  --last-snapshot       print the last good envelope ({} when there is none)
  --shared-settings     merge the JSON object in $WIDGET_SHARED_PATCH into the
                        settings shared by all Plasma widget instances, print them
  --query-only          query the shared session index without refreshing
  --refresh             refresh all session providers before querying
  --query <text>        search all local session records by safe display fields
  --source <ids>        restrict sessions to verified source ids
  --limit <n>           limit session results to a positive number of rows
  --offset <n>          skip a non-negative number of session rows
  --open-session <key>  resume one listed session (by its openKey) in a terminal
  --open-cli            open the terminal frontend (tidesi-cli) in a terminal
  --list                print the known provider ids, one per line
  -h, --help            show this help

providers: """ + " ".join(config.ALL_PROVIDERS)


def _emit(obj):
    sys.stdout.write(json.dumps(finalize(obj), separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.stdout.flush()


def snapshot(*, on_provider=None, provider_ids=None):
    """The envelope `--all` prints, for an in-process caller (the Windows tray
    app, which has no shell to run this module through).

    Like main(), this exports the settings file's keys into os.environ first. A
    long-lived caller has to put its environment back afterwards, or a key the
    user later clears would linger there."""
    cfg = config.load_settings()
    config.apply_widget_env(cfg)
    selected = envelope.enabled(cfg) if provider_ids is None else provider_ids
    deferred = {"zed"} if provider_ids is None else set()
    if not deferred and on_provider is None:
        result = envelope.build(selected)
    elif not deferred:
        result = envelope.build(selected, on_provider=on_provider)
    elif on_provider is None:
        result = envelope.build(selected, deferred_provider_ids=deferred)
    else:
        result = envelope.build(selected, on_provider=on_provider, deferred_provider_ids=deferred)
    return finalize(result)


def _refresh_pricing():
    snapshot = pricing.load_catalog(force=True)
    providers = snapshot.get("providers") or {}
    usable = bool(providers.get("anthropic") or providers.get("openai"))
    error = str(snapshot.get("error") or "")
    if not usable:
        error = (
            f"{error}; no usable pricing rates; retry when pricing sources are available."
            if error
            else "No usable pricing rates; retry when pricing sources are available."
        )
    result = {
        "ok": usable,
        "status": "stale-good" if usable and error else "refreshed" if usable else "no-cache",
        "fetchedAt": snapshot.get("fetchedAt", 0),
        "error": error,
    }
    sys.stdout.write(json.dumps(result, separators=(",", ":"), ensure_ascii=False) + "\n")
    return 0 if usable else 1


def main(argv):
    mode = ""
    requested = ""
    open_key = ""
    query = ""
    limit = None
    offset = None
    query_only = False
    refresh = False
    save_snapshot = False
    stream = False
    source_ids = None

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--all":
            mode = "all"
        elif arg == "--provider":
            mode = "provider"
            i += 1
            requested = argv[i] if i < len(argv) else ""
        elif arg.startswith("--provider="):
            mode = "provider"
            requested = arg[len("--provider=") :]
        elif arg == "--normalize":
            mode = "normalize"
        elif arg == "--sessions":
            mode = "sessions"
        elif arg == "--refresh-pricing":
            mode = "refresh-pricing"
        elif arg == "--pricing-table":
            mode = "pricing-table"
        elif arg == "--detect-providers":
            mode = "detect-providers"
        elif arg == "--initialize-provider-defaults":
            mode = "initialize-provider-defaults"
        elif arg == "--last-snapshot":
            mode = "last-snapshot"
        elif arg == "--shared-settings":
            mode = "shared-settings"
        elif arg == "--save-snapshot":
            save_snapshot = True
        elif arg == "--stream":
            stream = True
        elif arg == "--query-only":
            query_only = True
        elif arg == "--refresh":
            refresh = True
        elif arg == "--query":
            i += 1
            query = argv[i] if i < len(argv) else ""
        elif arg.startswith("--query="):
            query = arg[len("--query=") :]
        elif arg == "--source" or arg.startswith("--source="):
            if arg == "--source":
                i += 1
                raw_sources = argv[i] if i < len(argv) else ""
            else:
                raw_sources = arg[len("--source=") :]
            source_ids = []
            valid_source_ids = {source_id for source_id, _label in SOURCE_REGISTRY}
            for source_id in raw_sources.split(","):
                normalized_source_id = source_id.strip()
                if not normalized_source_id or normalized_source_id not in valid_source_ids:
                    sys.stderr.write(f"get-ai-usage: invalid session source id: {normalized_source_id or raw_sources}\n")
                    sys.stderr.write(USAGE + "\n")
                    return 2
                if normalized_source_id not in source_ids:
                    source_ids.append(normalized_source_id)
            selected_source_ids = set(source_ids)
            source_ids = [source_id for source_id, _label in SOURCE_REGISTRY if source_id in selected_source_ids]
        elif arg == "--limit" or arg.startswith("--limit="):
            if arg == "--limit":
                i += 1
                raw_limit = argv[i] if i < len(argv) else ""
            else:
                raw_limit = arg[len("--limit=") :]
            try:
                limit = int(raw_limit)
            except ValueError:
                sys.stderr.write("get-ai-usage: --limit needs a positive integer\n")
                sys.stderr.write(USAGE + "\n")
                return 2
            if limit <= 0:
                sys.stderr.write("get-ai-usage: --limit needs a positive integer\n")
                sys.stderr.write(USAGE + "\n")
                return 2
        elif arg == "--offset" or arg.startswith("--offset="):
            if arg == "--offset":
                i += 1
                raw_offset = argv[i] if i < len(argv) else ""
            else:
                raw_offset = arg[len("--offset=") :]
            try:
                offset = int(raw_offset)
            except ValueError:
                sys.stderr.write("get-ai-usage: --offset needs a non-negative integer\n")
                sys.stderr.write(USAGE + "\n")
                return 2
            if offset < 0:
                sys.stderr.write("get-ai-usage: --offset needs a non-negative integer\n")
                sys.stderr.write(USAGE + "\n")
                return 2
        elif arg == "--open-cli":
            mode = "open-cli"
        elif arg == "--open-session":
            mode = "open-session"
            i += 1
            open_key = argv[i] if i < len(argv) else ""
        elif arg.startswith("--open-session="):
            mode = "open-session"
            open_key = arg[len("--open-session=") :]
        elif arg == "--list":
            for p in config.ALL_PROVIDERS:
                print(p)
            return 0
        elif arg in ("-h", "--help"):
            print(USAGE)
            return 0
        else:
            sys.stderr.write(f"get-ai-usage: unknown argument: {arg}\n")
            sys.stderr.write(USAGE + "\n")
            return 2
        i += 1

    if stream and mode not in ("", "all", "provider"):
        sys.stderr.write("get-ai-usage: --stream requires --all or --provider\n")
        return 2

    if query_only and refresh:
        sys.stderr.write("get-ai-usage: --query-only and --refresh cannot be used together\n")
        sys.stderr.write(USAGE + "\n")
        return 2

    if (query_only or refresh) and mode != "sessions":
        sys.stderr.write("get-ai-usage: session modes require --sessions\n")
        sys.stderr.write(USAGE + "\n")
        return 2

    if source_ids is not None and mode != "sessions":
        sys.stderr.write("get-ai-usage: --source requires --sessions\n")
        sys.stderr.write(USAGE + "\n")
        return 2

    if mode == "sessions":
        from .sessions import collect_sessions, refresh_sessions

        collect = collect_sessions if query_only else refresh_sessions
        if limit is None and offset is None and source_ids is None:
            result = collect(query)
        elif limit is None and offset is None:
            result = collect(query, source_ids=source_ids)
        elif source_ids is None:
            result = collect(query, limit=limit if limit is not None else 60, offset=offset if offset is not None else 0)
        else:
            result = collect(
                query,
                source_ids=source_ids,
                limit=limit if limit is not None else 60,
                offset=offset if offset is not None else 0,
            )
        _emit(result)
        return 0

    if mode == "pricing-table":
        _emit(
            pricing.catalog_rows(
                query,
                limit=limit if limit is not None else 60,
                offset=offset if offset is not None else 0,
            )
        )
        return 0

    if mode == "refresh-pricing":
        return _refresh_pricing()

    if mode == "detect-providers":
        _emit({"ok": True, "data": detect.detect_providers()})
        return 0

    if mode == "last-snapshot":
        sys.stdout.write(json.dumps(widget_state.last_snapshot(), separators=(",", ":"), ensure_ascii=False) + "\n")
        return 0

    if mode == "shared-settings":
        try:
            patch = json.loads(os.environ.get("WIDGET_SHARED_PATCH") or "{}")
        except ValueError:
            sys.stderr.write("get-ai-usage: $WIDGET_SHARED_PATCH is not a JSON object\n")
            return 2
        _emit({"ok": True, "data": widget_state.merge_shared_settings(patch)})
        return 0

    if mode == "initialize-provider-defaults":
        _emit({"ok": True, "data": config.initialize_provider_defaults()})
        return 0

    if mode == "open-cli":
        from .sessions import open_cli

        ok, message = open_cli()
        print(json.dumps({"ok": ok, "message": message}))
        return 0 if ok else 1

    if mode == "open-session":
        from .sessions import open_session

        ok, message = open_session(open_key)
        print(json.dumps({"ok": ok, "message": message}))
        return 0 if ok else 1

    cfg = config.load_settings()
    config.apply_widget_env(cfg)

    if mode == "normalize":
        try:
            raw = json.load(sys.stdin)
        except ValueError as e:
            sys.stderr.write(f"get-ai-usage: invalid envelope on stdin: {e}\n")
            return 2
        _emit(normalize(raw))
        return 0

    if not mode:
        mode = "all"

    if mode == "provider":
        if not requested:
            sys.stderr.write("get-ai-usage: --provider needs at least one id\n")
            return 2
        selected = []
        for id_ in requested.split(","):
            if id_ not in config.ALL_PROVIDERS:
                sys.stderr.write(f"get-ai-usage: unknown provider: {id_}\n")
                return 2
            selected.append(id_)
    else:
        selected = envelope.enabled(cfg)

    deferred = {"zed"} if mode != "provider" else set()
    if not deferred and stream:
        result = envelope.build(selected, on_provider=lambda provider: _emit(envelope.provider_update(provider)))
    elif not deferred:
        result = envelope.build(selected)
    elif stream:
        result = envelope.build(
            selected,
            on_provider=lambda provider: _emit(envelope.provider_update(provider)),
            deferred_provider_ids=deferred,
        )
    else:
        result = envelope.build(selected, deferred_provider_ids=deferred)
    _emit(result)
    if save_snapshot:
        widget_state.save_snapshot(finalize(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
