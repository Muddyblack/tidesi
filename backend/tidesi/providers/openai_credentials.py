import base64
import os

from .. import accounts, keychain, paths
from ..http import as_json, clean_credential, resolve_key

# Where Codex puts auth.json's contents when it is configured to use the OS
# keyring (`cli_auth_credentials_store`) instead of a file.
CODEX_KEYCHAIN_SERVICE = "Codex Auth"


def codex_home():
    """``$CODEX_HOME`` when set, else ``~/.codex`` — where the Codex CLI keeps
    its login, config and session logs, on every platform. Codex can be told
    to keep the login in the OS keyring instead of auth.json
    (``cli_auth_credentials_store``); that is opt-in and not read here, so a
    user who turned it on has to set OPENAI_API_KEY."""
    return os.path.expanduser(os.environ.get("CODEX_HOME") or "~/.codex")


def _decode_jwt_payload(token):
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    segment = parts[1].replace("-", "+").replace("_", "/")
    segment += "=" * (-len(segment) % 4)
    try:
        return as_json(base64.b64decode(segment).decode("utf-8", "replace")) or {}
    except Exception:
        return {}


def _codex_auth():
    """Codex's login document, from wherever this install keeps it.

    auth.json under $CODEX_HOME is the default on every platform. Codex can be
    told to keep the same document in the OS keyring instead
    (`cli_auth_credentials_store`), which on macOS is the login Keychain — so
    when the file yields nothing, the Keychain is asked before giving up. The
    item is keyed to the codex home in a way this package cannot reconstruct,
    so it is looked up by service alone.
    """
    path = os.path.join(codex_home(), "auth.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                parsed = as_json(f.read())
        except OSError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    parsed = as_json(keychain.password(CODEX_KEYCHAIN_SERVICE))
    return parsed if isinstance(parsed, dict) else None


def get_openai_credentials():
    # An extra account (another CODEX_HOME) keeps only the key in its own
    # auth.json: the environment's would put the main org's bill on it too.
    api_key = "" if accounts.extra_account() else _main_api_key()
    return _credentials(api_key)


def _main_api_key():
    return resolve_key(
        "WIDGET_OPENAI_API_KEY",
        "OPENAI_API_KEY",
        # config_home() first so $XDG_CONFIG_HOME is honoured, as it is
        # everywhere else in this package; the plain ~/.config after it, for a
        # key file written before that was true.
        os.path.join(paths.config_home(), "openai-api-key"),
        os.path.expanduser("~/.config/openai-api-key"),
        os.path.expanduser("~/.openai/api-key"),
    )


def _credentials(api_key):
    access_token = email = plan_type = org_id = account_id = auth_mode = ""

    codex_auth = _codex_auth()
    if isinstance(codex_auth, dict):
        tokens = codex_auth.get("tokens") or {}
        access_token = clean_credential(tokens.get("access_token") or codex_auth.get("access_token"))
        account_id = tokens.get("account_id") or codex_auth.get("account_id") or ""
        auth_mode = codex_auth.get("auth_mode") or ""

        if not api_key:
            codex_api_key = clean_credential(codex_auth.get("OPENAI_API_KEY"))
            if codex_api_key:
                api_key = codex_api_key

        if access_token:
            payload = _decode_jwt_payload(access_token)
            email = (payload.get("https://api.openai.com/profile") or {}).get("email") or ""
            auth = payload.get("https://api.openai.com/auth") or {}
            plan_type = auth.get("chatgpt_plan_type") or ""
            orgs = auth.get("organizations") or []
            org_id = (orgs[0].get("id") if orgs else "") or ""

    if not api_key and not access_token:
        return {}

    return {
        "openaiApiKey": api_key,
        "codexAccessToken": access_token,
        "email": email,
        "planType": plan_type,
        "orgId": org_id,
        "accountId": account_id,
        "authMode": auth_mode,
        "codexLoggedIn": access_token != "",
    }
