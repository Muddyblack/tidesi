"""Raw input collection — one envelope-builder per provider.

Every provider's raw inputs are gathered here and handed to
tidesi.normalize.normalize(). This is the IO half: credentials, provider API
requests and on-disk stats/caches all live in this module and providers/.
"""

import datetime
import os
import time

from . import config
from .contract import STATUS_FEEDS, STATUS_PAGES
from .http import as_json, fetch_json, http_error_text
from .pricing import get_pricing
from .providers import claude_transcripts
from .providers.antigravity import get_antigravity_usage
from .providers.claude_credentials import claude_config_dir, get_claude_credentials
from .providers.claude_remote import remote_sessions
from .providers.cline import get_cline_sessions
from .providers.coderabbit import get_coderabbit_usage
from .providers.codex_rate_limits import get_codex_rate_limits
from .providers.codex_rate_limits import remember as remember_codex_rate_limits
from .providers.codex_stats import get_codex_stats
from .providers.copilot import get_copilot_usage
from .providers.copilot_stats import get_copilot_stats
from .providers.cursor import get_cursor_usage
from .providers.deepseek import get_deepseek_balance
from .providers.grok import get_grok_usage
from .providers.jetbrains import get_jetbrains_usage
from .providers.junie import usage_snapshot as get_junie_usage
from .providers.kilo import get_kilo_usage
from .providers.kimi_code import get_kimi_code_usage
from .providers.kiro import get_kiro_usage
from .providers.mimo import usage_snapshot as get_mimo_usage
from .providers.mistral import get_mistral_usage
from .providers.moonshot import get_moonshot_balance
from .providers.muse import get_muse_usage
from .providers.muse_quota import get_muse_quota
from .providers.ollama import get_ollama_usage
from .providers.openai_credentials import codex_home, get_openai_credentials
from .providers.opencode import usage_snapshot as get_opencode_usage
from .providers.opencode_account import account_mode, get_go_usage
from .providers.openrouter import get_openrouter_usage
from .providers.pi import usage_snapshot as get_pi_usage
from .providers.windsurf import get_windsurf_usage
from .providers.zai import get_zai_usage
from .providers.zed import get_zed_usage


def read_json_file(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return as_json(f.read())
    except OSError:
        return None


def status_json(name, url):
    """Fetch a Statuspage summary, memoised on disk so a fast poll interval
    does not hammer the status hosts. Never fatal."""
    cache_path = os.path.join(config.cache_dir(), f"status-{name}.json")
    if os.path.isfile(cache_path):
        try:
            age = time.time() - os.path.getmtime(cache_path)
        except OSError:
            age = None
        if age is not None and 0 <= age < config.status_ttl():
            cached = read_json_file(cache_path)
            if cached is not None:
                return cached

    result = fetch_json(url, timeout=12)
    if result.status == 200:
        body = as_json(result.body)
        if body is not None:
            try:
                os.makedirs(config.cache_dir(), exist_ok=True)
                with open(cache_path, "w", encoding="utf-8") as f:
                    f.write(result.body)
            except OSError:
                pass
            return body
    cached = read_json_file(cache_path)
    return cached


def provider_status(id_):
    """The raw status feed for a provider, or None when its page has none."""
    page = STATUS_PAGES.get(id_) or {}
    feed = STATUS_FEEDS.get(page.get("feed"))
    return status_json(id_, page["url"] + feed) if feed else None


def collect_claude(now):
    creds = get_claude_credentials()
    # ~/.claude/.credentials.json is written by another program and read
    # verbatim, so nothing guarantees these are the shapes we expect.
    oauth = creds.get("claudeAiOauth")
    oauth = oauth if isinstance(oauth, dict) else {}
    token = str(oauth.get("accessToken") or "")
    admin = str(creds.get("claudeAdminApiKey") or "")

    usage = None
    usage_error = ""
    if token:
        result = fetch_json(
            "https://api.anthropic.com/api/oauth/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "claude-code/2.1.0",
            },
            timeout=12,
            fixture_path=os.environ.get("CLAUDE_USAGE_RESPONSE_FILE"),
        )
        if result.status == 200:
            usage = as_json(result.body)
            if usage is None:
                usage_error = "parse error"
        else:
            usage_error = http_error_text(result.status)

    org = None
    if admin:
        today = datetime.datetime.now(datetime.timezone.utc)
        end = today.strftime("%Y-%m-%d")
        start = (today - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        result = fetch_json(
            f"https://api.anthropic.com/v1/organization/usage?start_date={start}&end_date={end}",
            headers={"x-api-key": admin, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            timeout=12,
            fixture_path=os.environ.get("CLAUDE_ORG_USAGE_RESPONSE_FILE"),
        )
        if result.status == 200:
            org = as_json(result.body)

    settings = read_json_file(os.path.join(claude_config_dir(), "settings.json"))
    # Claude Code no longer refreshes stats-cache.json: the widget keeps its own copy and adds the days from the transcripts.
    stats = claude_transcripts.load(read_json_file(os.path.join(claude_config_dir(), "stats-cache.json")), now)
    status = provider_status("claude")

    return {
        "id": "claude",
        "now": now,
        "inputs": {
            "credentials": creds or {},
            "usage": usage,
            "usageError": usage_error,
            "orgUsage": org,
            "pricing": get_pricing("anthropic") if isinstance(org, dict) and org.get("data") else {},
            "settings": settings or {},
            "stats": stats or {},
            "status": status,
            "remote": remote_sessions(),
        },
    }


def collect_openai(now):
    creds = get_openai_credentials()
    # As in collect_claude: ~/.codex/auth.json is not ours, so coerce rather
    # than trust. These all flow into HTTP headers, which must be str.
    token = str(creds.get("codexAccessToken") or "")
    account = str(creds.get("accountId") or "")
    api_key = str(creds.get("openaiApiKey") or "")

    codex = None
    codex_error = ""
    fixture = os.environ.get("CODEX_USAGE_RESPONSE_FILE")
    if fixture:
        codex = read_json_file(fixture)
    else:
        codex = get_codex_rate_limits(token, codex_home())
        codex.pop("_codexSource", None)
        codex.pop("_codexAge", None)
        # The app-server can answer "no limits on this plan" explicitly; that
        # is an answer, not a failure, so it must not trigger the HTTP fallback.
        no_limits = codex.pop("_codexNoLimits", False)
        if not (codex.get("rateLimits") or codex.get("rate_limit") or no_limits) and token:
            headers = {"Authorization": f"Bearer {token}", "User-Agent": "codex-cli"}
            if account:
                headers["chatgpt-account-id"] = account
            result = fetch_json("https://chatgpt.com/backend-api/codex/usage", headers=headers, timeout=12)
            if result.status == 200:
                codex = as_json(result.body) or {}
                remember_codex_rate_limits(token, codex_home(), codex if codex else {"_codexNoLimits": True})
            else:
                codex_error = http_error_text(result.status)

    org = None
    if api_key:
        end = int(now)
        start = int(now) - 30 * 86400
        result = fetch_json(
            f"https://api.openai.com/v1/organization/usage/completions?start_time={start}&end_time={end}&group_by=model&limit=100",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=12,
            fixture_path=os.environ.get("OPENAI_ORG_USAGE_RESPONSE_FILE"),
        )
        if result.status == 200:
            org = as_json(result.body)

    stats = get_codex_stats()
    status = provider_status("openai")

    return {
        "id": "openai",
        "now": now,
        "inputs": {
            "credentials": creds or {},
            "codex": codex or {},
            "codexError": codex_error,
            "orgUsage": org,
            "pricing": get_pricing("openai") if isinstance(org, dict) and org.get("data") else {},
            "stats": stats or {},
            "status": status,
        },
    }


def collect_muse(now):
    """Local statistics always; the plan quota only when the user switched the
    billed call on (see providers/muse_quota.py)."""
    usage = get_muse_usage() or {}
    quota, quota_error = get_muse_quota()
    usage["quota"] = quota
    usage["quotaError"] = quota_error
    return {"id": "muse", "now": now, "inputs": {"usage": usage}}


def collect_copilot(now):
    """Like Claude and Codex, Copilot pairs a remote quota with a local CLI
    history — so it does not fit the _SIMPLE shape."""
    return {
        "id": "copilot",
        "now": now,
        "inputs": {"usage": get_copilot_usage() or {}, "stats": get_copilot_stats() or {}, "status": provider_status("copilot")},
    }


def collect_kimi(now):
    """Two unrelated Kimi sources that either stand alone: the Moonshot API
    balance (an API key) and the Kimi Code plan quota (the CLI login)."""
    return {
        "id": "kimi",
        "now": now,
        "inputs": {"usage": get_moonshot_balance() or {}, "codePlan": get_kimi_code_usage() or {}, "status": provider_status("kimi")},
    }


def collect_opencode(now):
    mode = account_mode()
    go_usage = None
    go_error = ""
    if mode == "go":
        go_usage, go_error = get_go_usage()
    return {
        "id": "opencode",
        "now": now,
        "inputs": {
            "usage": get_opencode_usage(),
            "account": {"mode": mode, "goUsage": go_usage, "goError": go_error},
        },
    }


_SIMPLE = {
    "antigravity": get_antigravity_usage,
    "kiro": get_kiro_usage,
    "mistral": get_mistral_usage,
    "openrouter": get_openrouter_usage,
    "ollama": get_ollama_usage,
    "grok": get_grok_usage,
    "zai": get_zai_usage,
    "deepseek": get_deepseek_balance,
    "cursor": get_cursor_usage,
    "cline": get_cline_sessions,
    "mimo": get_mimo_usage,
    "junie": get_junie_usage,
    "jetbrains": get_jetbrains_usage,
    "windsurf": get_windsurf_usage,
    "pi": get_pi_usage,
    "kilo": get_kilo_usage,
    "coderabbit": get_coderabbit_usage,
    "zed": get_zed_usage,
}


def collect(id_, now):
    if id_ == "selfhosted":
        from .providers.selfhosted import get_selfhosted_usage

        return {"id": id_, "now": now, "inputs": {"usage": get_selfhosted_usage()}}
    if id_ == "claude":
        return collect_claude(now)
    if id_ == "openai":
        return collect_openai(now)
    if id_ == "copilot":
        return collect_copilot(now)
    if id_ == "muse":
        return collect_muse(now)
    if id_ == "kimi":
        return collect_kimi(now)
    if id_ == "opencode":
        return collect_opencode(now)
    if id_ in _SIMPLE:
        usage = _SIMPLE[id_]() or {}
        inputs = {"usage": usage, "status": provider_status(id_)}
        if id_ == "antigravity":
            # Local session history, for the Stats view; read whether or not
            # the IDE is running.
            from .providers.antigravity_stats import get_antigravity_stats

            inputs["stats"] = get_antigravity_stats()
        elif id_ == "kiro":
            from .providers.kiro_stats import get_kiro_stats

            inputs["stats"] = get_kiro_stats()
        return {"id": id_, "now": now, "inputs": inputs}
    return {"id": id_, "now": now, "inputs": {}}
