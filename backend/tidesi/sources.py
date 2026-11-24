"""Where a provider's numbers can come from, and which of those places work here.

Several providers can be read more than one way: Cursor through cursor-agent or
through the IDE, Antigravity through a CLI, the IDE's language server or ``agy``,
Copilot through a key, the editor login or the ``gh`` CLI. This module is the one
place that knows that, so a new way of reading a provider — a browser session,
say — is one more :class:`Source` in that provider's module and nothing else:

* the provider's module declares ``SOURCES`` (what exists, and a cheap stat-only
  probe for each);
* its fetch code asks :func:`candidates` which sources to try, in order;
* the normalizer chain attaches :func:`describe` to the envelope, which the
  settings UI draws without knowing any provider by name.

A user's choice lives in the shared settings file as ``sources: {provider: id}``
(``"auto"`` or absent means "try them in the default order, first that works").
An explicit choice is strict: only that source is tried, so what the tab says it
is using is what it is using.
"""

import os
from typing import Callable, NamedTuple

from . import config

AUTO = "auto"

# What kind of place a source is. The UI maps each to a short label and glyph, so
# a new kind needs adding here and in ui/js/ProviderSources.js, nowhere else.
KINDS = ("cli", "ide", "server", "api", "file", "browser")

# How a source stands, best first.
WORKING = "working"  # it answered on the last refresh
FAILING = "failing"  # it was in use and did not answer
READY = "ready"  # it looks set up here, but is not the one in use
MISSING = "missing"  # nothing on this machine suggests it is set up


class Source(NamedTuple):
    id: str
    kind: str
    label: str
    detail: str
    # Must be cheap and side-effect free: stat a file, look a program up on
    # PATH. It never reads a credential, runs a program or touches the network.
    probe: Callable[[], bool]


class Spec(NamedTuple):
    sources: tuple
    # False for sources that are not alternatives to each other (both halves are
    # shown, none is chosen between), e.g. an API balance next to a CLI plan.
    choice: bool = True


def _registry():
    """Provider id -> Spec, imported lazily: providers pull in a lot, and the
    paths that never describe sources (--normalize of one fixture, an
    unconfigured provider) should not pay for them."""
    from .providers import antigravity, commandcode, copilot, cursor, kilo, kiro, ollama

    return {
        "cursor": Spec(cursor.SOURCES),
        "kiro": Spec(kiro.SOURCES),
        "antigravity": Spec(antigravity.SOURCES),
        "copilot": Spec(copilot.SOURCES),
        "kilo": Spec(kilo.SOURCES),
        "commandcode": Spec(commandcode.SOURCES),
        "ollama": Spec(ollama.SOURCES),
    }


def registered(provider_id):
    return provider_id in _registry()


def preferred(provider_id):
    """The source id the user chose, or AUTO."""
    env = os.environ.get(f"WIDGET_SOURCE_{provider_id.upper()}")
    if env:
        return env
    chosen = (config.load_settings().get("sources") or {}).get(provider_id)
    return chosen if isinstance(chosen, str) and chosen else AUTO


def candidates(provider_id, default_order):
    """The source ids a fetch should try, in order.

    `default_order` is the provider's own best-first order. An explicit choice of
    one of them narrows it to that one; an unknown choice (a stale setting, a
    source removed in an update) is ignored rather than leaving the tab empty.
    """
    choice = preferred(provider_id)
    return [choice] if choice in default_order else list(default_order)


def _state(source, active, failing, ready):
    if source.id == active:
        return FAILING if failing else WORKING
    return READY if ready else MISSING


def describe(provider_id, inputs, envelope):
    """The ``sources`` block of an envelope, or None for a provider that reads
    only one way.

    `inputs` are the raw collector inputs and `envelope` the normalized result:
    the source the data came from is whatever the collector recorded in
    ``usage["source"]``; failing means the envelope is an error. A provider that
    records none is taken to be using the first source that looks set up, which is
    the one :func:`candidates` would try first.
    """
    spec = _registry().get(provider_id)
    if spec is None:
        return None
    usage = (inputs or {}).get("usage")
    ids = [source.id for source in spec.sources]
    probes = {source.id: _safe_probe(source) for source in spec.sources}
    selected = preferred(provider_id) if spec.choice else AUTO
    if selected not in ids:
        selected = AUTO
    recorded = usage.get("source") if isinstance(usage, dict) else None
    active = recorded if recorded in ids else (selected if selected != AUTO else next((i for i in ids if probes[i]), ""))
    failing = envelope.get("ok") is not True
    return {
        "choice": spec.choice,
        "selected": selected,
        "active": active,
        "options": [
            {
                "id": source.id,
                "kind": source.kind if source.kind in KINDS else "file",
                "label": source.label,
                "detail": source.detail,
                "state": _state(source, active, failing, probes[source.id]),
            }
            for source in spec.sources
        ],
    }


def _safe_probe(source):
    try:
        return bool(source.probe())
    except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        # A probe is advice for the UI; it must never cost a provider its tab.
        return False


def attach(provider_id, inputs, envelope):
    """Add ``sources`` to a normalized envelope when the provider has any."""
    block = describe(provider_id, inputs, envelope)
    if block is not None and isinstance(envelope, dict):
        envelope["sources"] = block
    return envelope


# Shared probes, so a provider's SOURCES stays a table rather than a pile of
# lambdas. Each answers only "does this look set up here".
def has_file(*candidates_):
    return lambda: any(os.path.isfile(os.path.expanduser(path)) for path in candidates_)


def has_program(*names):
    import shutil

    return lambda: any(shutil.which(name) for name in names)


def has_any(*probes):
    return lambda: any(probe() for probe in probes)
