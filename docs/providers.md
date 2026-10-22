# Providers

What each provider tab reads, where it looks for credentials, and what the
underlying API does or does not expose. For the JSON model these all produce,
see [`provider-contract.md`](provider-contract.md).

## Adding providers and choosing sources

Settings → Providers lists only the providers that are switched on, each with
how it is being read ("Using agy session", "Not set up", or its error). **Add
provider** opens a search over the rest; picking one switches it on and opens its
page. A provider's page shows every way it can be read — a CLI, an IDE, a local
server, an API key — with whether each works on this machine (Working, Ready,
Not working, Not found), then its API key and extras.

Where the ways are alternatives you choose one, or leave **Auto**, which uses the
first that works. An explicit choice is strict: only that source is tried, so
what the tab says it uses is what it uses. The choice is stored as
`sources: {"<provider>": "<source id>"}` in the shared settings file (or
`$WIDGET_SOURCE_<PROVIDER>`). Today Cursor (`cli`, `ide`), Kiro (`cli`, `ide`),
Antigravity (`aiu`, `server`, `agy`), Copilot (`key`, `editor`, `cli`), Kilo
(`key`, `cli`) and Ollama Cloud (`key`, `opencode`) have a choice; the others
are read one way.

**Network → Proxy** (Settings → Advanced) sends the backend's requests through an
HTTP proxy, ignores any proxy (Off), or leaves the environment alone (System, the
default). Requests to this machine never go through it, and a proxy already in the
environment (`HTTPS_PROXY`) takes precedence. SOCKS is not supported.

## Token cost estimates

Organization and local token estimates use the [models.dev JSON pricing
catalog](https://models.dev/) first, keyed by OpenCode's exact provider and
model IDs. The community-maintained [LiteLLM JSON pricing
catalog](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json)
is an exact-match fallback for Anthropic and OpenAI, and OpenRouter remains a
last-resort exact fallback for requested misses. Catalog prices are USD per
million tokens (`input`, `output`, and optional cached-input rates); zero is a
valid rate. No aliases, prefix stripping, regional inference, or near matches
are used. The widget downloads data only, with no catalog package dependency.
Prices refresh automatically once every seven days, after the fixed
604800-second TTL expires. Failed refreshes retry after 900 seconds. A manual
`--refresh-pricing` bypasses that TTL once, and concurrent forced refreshes
share one in-flight refresh. No account credentials or usage are sent to the
catalog hosts.

A failed or malformed download retains the last good rates and their original
`fetchedAt`. The refresh result is `refreshed` on success, `stale-good` when
saved rates remain usable after a failed refresh, or `no-cache` when no usable
rates exist. The command exits nonzero only for `no-cache`. Before the first
successful download, or for a model absent from the catalog, tokens remain
visible with `priced: false`; unknown models are not estimated and cost totals
include only priced models. These are standard-rate estimates, not invoices:
contract discounts, batch rates, long-context tiers and other per-request
charges cannot be reconstructed from the aggregate usage available here.
Claude Code's local recorded costs and subscription limits continue to come
from their existing sources.

Session rows can expose optional structured `costUSD` and `costStatus` values.
Statuses are `exact`, `partial`, and `unavailable`. Multi-model session costs
are summed from the structured model buckets that can be priced. Overview and
provider totals remain provider-level figures, not session totals. Session
activity is locally observed only, so the widget does not infer costs or promise
parsing for unsupported local formats.

Session cost provenance is separate from coverage status. `actual` means a
finite provider-reported USD amount; `estimated` means a calculated amount from
exact local token fields and an exact cached model rate; and `mixed` means the
row contains both, with `actualUSD` and `estimatedUSD` subtotals in
`costBreakdown`. Estimates are labels for calculated rates, not bills or
invoices. `partial` indicates that at least one structured bucket was priced
and another was not; `unavailable` is used when there is no trustworthy numeric
result.

The Usage & Spend tab shows one non-navigable row per local source, such as
**OpenCode**, **Claude Code**, **Codex**, or **Cline**. Actual and estimated
rollups for the same source are merged into that source row, whose secondary
text identifies the provenance (`actual`, `estimated`, or `mixed`) and coverage
(`exact` or `partial`). Local rows are not added to provider/API spend and must
not be interpreted as one bill. Mixed rows feed each source breakdown only when
both origin-specific values are finite and non-negative and their sum matches
the session total. Unknown or missing model, rate, or token data remains
unavailable. OpenCode reads every structured assistant message it routed,
regardless of upstream provider, and aggregates them per provider and model,
so no session is truncated at a fixed row count. Token-only estimates are
strict: the local provider and model strings must exactly match a provider and
model key in the dynamic catalog. Aliases, normalized names, near matches, and
metadata-only records do not produce estimates. Multi-provider sessions roll up per upstream
provider (for example `ollama-cloud::opencode`).

## Defaults and setup per provider

There are 25 provider IDs in the backend. New shared settings are zero based.
The one-shot initializer can enable only the 16 providers in the backend's
`AUTO_DETECT_PROVIDERS` allowlist when local evidence exists. The other 9 are
manual-only and need an explicit choice, key, or endpoint. Detection does not
prove authentication or usability. Each service has its own setup requirement:

| Service | What you need |
|---|---|
| Claude | Claude Code, signed in locally |
| Antigravity | Node.js 18+, the `antigravity-usage` CLI, and a Google account with access |
| OpenAI | An OpenAI API key for organization API usage; a Codex CLI login provides Codex/ChatGPT plan limits and account status |
| Grok | Grok CLI authenticated with `grok --oauth`; an xAI API key is optional |
| Kiro | kiro-cli signed in (`kiro-cli login`), or the Kiro IDE signed in at least once |
| Junie | Junie CLI sessions; authenticate inside Junie using a JetBrains account, Junie token, or BYOK. No key is needed by the widget. Integration is untested. |
| Mistral AI | A Mistral API key; vibe CLI is optional and adds local session statistics |
| OpenRouter | An OpenRouter API key entered in widget settings |
| Ollama Cloud | Enable the provider, then use an Ollama API key in widget settings or `$OLLAMA_API_KEY`; when enabled, the `ollama-cloud` API-key login from OpenCode can also be read automatically |
| Local Models | Enable the separate Local Models provider. It checks Ollama (`127.0.0.1:11434`), vLLM (`127.0.0.1:8000`), and llama.cpp (`127.0.0.1:8080`). Set one or more comma-separated URLs in provider settings, or use `WIDGET_SELFHOSTED_ENDPOINT`, `WIDGET_SELFHOSTED_ENGINE`, and optional `WIDGET_SELFHOSTED_KEY`. Empty URLs auto-discover all three defaults. Token counters are since server start, where exposed. |

The local provider only reads health, model, slot, and metrics endpoints. It never sends a generation request. On a local URL, NVIDIA VRAM and GPU activity are read with a 300 ms `nvidia-smi` timeout. This is host-level telemetry and may appear under multiple servers on the same machine. Remote servers use only telemetry exposed by their runtime. Ollama's `/api/ps` reports loaded model size but not total GPU memory or token counters; those values are shown without a percentage or invented token total when no hardware telemetry is available. Runtime counters reset when the server restarts. The panel gauge uses the highest available utilization across configured servers.
| Z.AI | A Z.AI token from widget settings, `$ZAI_TOKEN`, `$Z_AI_API_KEY`, `~/.config/zai/token`, `~/.zai/token`, or the one `glm-acp-agent --setup` already stored |
| GitHub Copilot | Usually nothing to configure: the Copilot editor login (`~/.config/github-copilot/apps.json`), the Copilot CLI login, or `gh auth token` is picked up automatically. Widget settings, `$GITHUB_TOKEN` and `$GH_TOKEN` still win when set; a token with fine-grained **Plan: read** permission additionally unlocks the documented billing endpoint. Personal billing only |
| DeepSeek | A DeepSeek API key from widget settings, `$DEEPSEEK_API_KEY`, or `~/.config/deepseek/api-key` |
| Kimi / Moonshot AI | A Kimi Code login (`kimi`, then `/login`) for the plan quota, and/or a Moonshot API key from widget settings, `$MOONSHOT_API_KEY`, `$KIMI_API_KEY`, or `~/.config/moonshot/api-key` for the API balance |
| Muse | Nothing to configure for the local stats — `muse login` is enough. The optional plan quota additionally uses `$META_API_KEY` or the key `muse login` stored |
| Cursor | cursor-agent signed in (`cursor-agent login`), or the Cursor IDE signed in. No API key |
| Cline | The Cline CLI, run at least once. Nothing to configure |
| JetBrains AI | A JetBrains IDE signed in to JetBrains AI, opened at least once. Nothing to configure; manual-only |
| Windsurf | The Windsurf editor, signed in and opened at least once. Nothing to configure |
| Pi / OMP | Pi or OMP, run at least once. Nothing to configure |
| Kilo | The `kilo` CLI login (`kilo auth login`), or a Kilo API key from widget settings or `$KILO_API_KEY` |
| CodeRabbit | The `coderabbit` CLI, signed in |
| Zed | macOS only: the Zed editor signed in. Untested; manual-only |

All configuration is done in the widget's settings panel (right-click the widget
→ *Configure*).

For the exact detection signals, platform paths, shared JSON versus Plasma
KConfig behavior, and future-provider registration checklist, see
[`provider-detection.md`](provider-detection.md).

Provider APIs do not all expose the same information. In particular,
Codex/ChatGPT plan limits are separate from OpenAI API organization usage,
DeepSeek reports a balance rather than a usage window, and Grok's free tier does
not expose progressive usage before its limit is exhausted.

## Ollama Cloud *(experimental)*

The Ollama Cloud tab reads the documented
[`GET https://ollama.com/api/balance`](https://docs.ollama.com/api/balance)
with an Ollama Cloud API key. Legacy plans report session and weekly
`remaining_percent` values, which the widget converts to used percentages,
with the reported UTC reset times. Current included-credit plans report a USD
balance and allowance; the labeled included-credit meter uses that ratio.
Purchased credits are shown as a balance without a quota meter.

[`/api/usage`](https://docs.ollama.com/api/cloud-usage) now reports activity
totals and buckets, not quota limits. The widget does not invent a quota from
request counts or activity costs. Older saved response shapes remain readable,
but fresh quota reads use `/api/balance`. Reading balances does not run a model
or change the account's billing plan.

Key order: widget settings → `$OLLAMA_API_KEY` → OpenCode's
`ollama-cloud` API-key entry in `~/.local/share/opencode/auth.json` (or
`$XDG_DATA_HOME/opencode/auth.json` when set, on every OS). OpenCode's `/connect` flow saves that key. Other
OpenCode provider logins remain separate; OpenCode does not aggregate their
account quotas for this widget.

## Claude

On each refresh cycle the widget reads `~/.claude/.credentials.json` to get the OAuth access token, then calls Anthropic's subscription usage endpoint. It prefers the current semantic `limits[]` entries and falls back to the legacy `five_hour` and `seven_day` objects. Only windows with usable data are displayed; legacy five-hour support remains available if Anthropic returns it.

## Antigravity

Quota sources are the `antigravity-usage` CLI, a running IDE's local language
server, and the `agy` CLI's `/usage` report, in that order unless a source is
selected explicitly.

The Remote tab reads the account's named desktop and CLI devices using the
existing Antigravity sign-in. Linux needs `secret-tool` (libsecret, included by
the Nix package); macOS uses Keychain. Both read service `gemini`, account
`antigravity`. The CLI's `antigravity-oauth-token` file in
`~/.gemini/antigravity/` or `~/.gemini/antigravity-cli/` is a fallback; Windows
currently requires that file. Refreshes use the OAuth client configuration
shipped in agy 1.3.1 and keep credentials in memory, without rewriting its login.

No separate `agy remote-control` daemon is needed: its status describes only
the CLI daemon, not the Antigravity desktop app. Device rows show names,
connection status, and the server's status-update time. Each row opens that
device directly at `https://antigravity.google.com/r/<instance-id>`, using the
same link format as agy. The bottom button opens the complete device list.
If credentials or Google's private device-list API are
unavailable, the tab falls back to the desktop app's local log status.

The `GET /v1internal:listInstances` protocol and credential layout were inspected
in agy 1.3.1. Mocked tests cover requests, parsing, refresh, account mismatch,
and fallback. The account-wide device list has also been confirmed in the
running widget; direct links follow agy's inspected link builder.

## OpenAI

The OpenAI tab has two independent sections. API usage is fetched from the official OpenAI organization usage endpoint with an API key and summarized over the last 30 days. Codex subscription limits are read through the local Codex app-server, with the authenticated web usage endpoint retained as a compatibility fallback. Windows are classified by their actual duration instead of assuming that `primary` means five hours. Codex plan limits are separate from API billing usage.

Organization-usage HTTP failures and malformed responses are shown separately;
the presence of an API key does not imply access to organization usage. A failed
organization request does not hide healthy Codex limits or charts.

The Codex app-server has a 15-second startup deadline and a five-second
rate-limit read deadline. Timeout messages identify the failed phase; a
successful web fallback can recover a non-authentication timeout. An explicit
expired-login response is not obscured by another fallback failure: sign in
again with `codex login`. The widget never rewrites Codex credentials itself.

## OpenCode

OpenCode remains one provider. The widget selects its mode from the top-level
`opencode` and `opencode-go` entries in OpenCode's `auth.json`:
`$XDG_DATA_HOME/opencode/auth.json`, or `~/.local/share/opencode/auth.json`
when that variable is unset. Only API entries with a non-empty key are valid.
If a valid Go entry exists, it takes precedence even when a valid Zen entry is
also present. Otherwise, the provider uses Zen mode.

In **Go mode**, account usage comes from
`GET https://opencode.ai/zen/go/v1/usage`, authenticated with the Go key. The
server-reported rolling (5-hour), weekly, and monthly windows are authoritative;
displayed percentages and reset times are used only when reported and valid.
Missing values and authentication, entitlement, network, or response errors
remain unavailable. A failed Go request does not fall back to Zen activity or
turn missing values into zero usage.

In **Zen mode**, the widget shows informational activity read from OpenCode's
device-local SQLite session ledger, such as local session and token counts and
daily activity when available. These counts describe activity recorded on this
device, not account-wide usage. OpenCode has no supported Zen account-quota
endpoint, so the widget does not show or infer a Zen account cap, remaining
allowance, balance, percentage, or reset time. It does not scrape billing pages
or infer an allowance from local activity.

The auth key is used only for the Go request and is not exposed in provider
output or logs. The Zen local ledger remains separate from Go account quota and
its history.

## Grok *(free tier tested; paid plans untested)*

The Grok tab reads the Grok CLI login from `~/.grok/auth.json`, fetches the same credit/billing data used by the CLI, and summarizes local CLI sessions from `~/.grok/sessions`. For the tested free tier, the CLI only records the exact token allowance after it returns `free-usage-exhausted`, so the widget can show the confirmed exhausted amount and rolling 24-hour window but cannot infer progressive usage before that event. Paid-plan billing parsing is implemented but remains unverified. An xAI API key is optional; CLI OAuth is the primary source for quota data.

## Kiro

The Kiro tab needs no API key and works with either Kiro tool:

- **kiro-cli** — kiro-cli stores no usage snapshot, but it keeps its login in `~/.local/share/kiro-cli/data.sqlite3`. The widget opens that store read-only and asks the same `getUsageLimits` endpoint the CLI's own `/usage` screen shows. This live figure wins whenever it answers. The CLI's access token lasts about an hour after kiro-cli last ran and the widget deliberately does not renew it (a second refresher could sign the CLI out), so an expired login reports as such until kiro-cli runs again.
- **Kiro IDE** — the IDE caches its last usage payload in `~/.config/Kiro/User/globalStorage/state.vscdb`, read with no network request. It is the fallback, and the only source on a machine without kiro-cli.

Either way the tab shows the credit breakdown, usage percentage, reset date, overage information and plan tier, and feeds the percentage into the 30-day chart history.

## Mistral AI

The widget validates the configured API key against the Mistral API and lists available models, highlighting the one currently active in vibe CLI. Since Mistral exposes no public billing REST API, cost data is sourced locally from vibe CLI session logs (`~/.vibe/logs/session/*/meta.json`): cumulative spend, session count, total tokens, and the last session title are shown in a stats card. The spend bar is scaled against a $50 soft cap and feeds into a 30-day chart. The key is resolved from widget settings → `$MISTRAL_API_KEY` → `~/.vibe/.env` → `~/.config/mistral/api-key`.

## OpenRouter *(untested)*

The widget makes two independent requests with the configured key, so one failing never hides the other:

- `GET /api/v1/key` — the key's spend, its spending cap (if any), the account label and, when OpenRouter reports them, today's / this week's / this month's key spend. The usage bar reflects spend as a percentage of the cap; with no cap the bar stays empty. A cap is a limit on the key, not a balance.
- `GET /api/v1/credits` — purchased credits and total usage, shown as the account **Balance** (credits minus usage). If the key lookup is refused but this answers, the balance is shown on its own. If it is refused, the key figures are shown without a balance. Only when both fail is the tab an error.

The deprecated `rate_limit` field is kept in `details` but not shown. The 30-day activity endpoint needs a Management key and is not used.

## Z.AI

The Z.AI tab calls the Z.AI usage quota endpoint with the configured token. It shows the 5-hour token quota, monthly tools quota, reset countdowns, and model details when the API response includes them. The token is resolved from widget settings → `$ZAI_TOKEN` → `$Z_AI_API_KEY` → `~/.config/zai/token` → `~/.zai/token`.

## GitHub Copilot

The GitHub Copilot tab has a **Usage** and a **Stats** sub-tab.

Usage reads premium request consumption from `GET /copilot_internal/user` — the endpoint the Copilot editor plugins themselves call. It answers any Copilot login, and reports the plan's own entitlement, how much of it is left, the plan name, and the date the allowance actually resets, so neither the quota nor the reset day has to be guessed. If that endpoint does not answer (an account without a Copilot quota), the documented `GET /users/{user}/settings/billing/premium_request/usage` billing endpoint is used instead, scaled against the configured quota (default 300); a fine-grained token needs **Plan: read** permission for it. Personal billing only — usage billed through an organization or enterprise is not shown yet.

The credential is resolved from widget settings → `$GITHUB_TOKEN` / `$GH_TOKEN` → `~/.config/github-copilot/token` → the Copilot plugin login in `apps.json` / `hosts.json` (under `$XDG_CONFIG_HOME/github-copilot`, `~/.config/github-copilot` or `~/.copilot`) → `gh auth token`. So a machine that is already signed in to the Copilot CLI, a JetBrains/Neovim Copilot plugin, or the GitHub CLI needs no token pasted at all. A VS Code Copilot login is still not imported: VS Code keeps its session token in encrypted secret storage rather than a reusable file.

Stats aggregates the Copilot CLI's own history from `~/.copilot/session-store.db` — sessions, messages, tool calls, files touched, repositories, active days, streaks, peak hour, longest session and a messages-per-day sparkline. The CLI records no token counts, models or cost, so those tiles are absent rather than shown as zero.

## DeepSeek

The DeepSeek tab calls `GET https://api.deepseek.com/user/balance` with the configured API key. It shows whether the account has sufficient balance for API calls, the primary total balance, and the granted / topped-up split. The key is resolved from widget settings → `$DEEPSEEK_API_KEY` → `~/.config/deepseek/api-key`.

## Kimi / Moonshot AI

The Kimi tab combines two independent sources; either one is enough.

- **Kimi Code plan** — read with the login the `kimi` CLI stores in `~/.kimi-code/credentials/kimi-code.json` (`$KIMI_CODE_HOME` is honoured), from the same `GET https://api.kimi.com/coding/v1/usages` endpoint its `/usage` panel shows (`$KIMI_CODE_BASE_URL` is honoured). It shows each plan window with its reset countdown — the 5-hour and weekly windows are charted — plus the extra-usage wallet when there is one. A plan that is used up is shown as full rather than as an error. The access token is short-lived and the widget does not refresh it (the CLI rotates the pair itself and a second refresher could sign it out), so run `kimi` once when the tab reports an expired login.
- **Moonshot API balance** — `GET https://api.moonshot.ai/v1/users/me/balance` with the available, voucher and cash balances. The key is resolved from widget settings → `$MOONSHOT_API_KEY` / `$KIMI_API_KEY` → `~/.config/moonshot/api-key`.

## Cursor *(free login/stats tested; paid quotas untested)*

The Cursor tab needs no API key and no Cursor IDE: it uses the login `cursor-agent login` stores in `~/.config/cursor/auth.json`, falling back to the Cursor IDE's own login in its `state.vscdb`. With it, the widget calls the two dashboard RPCs the CLI's `/usage` screen uses (`aiserver.v1.DashboardService/GetCurrentPeriodUsage` and `GetPlanInfo` on `api2.cursor.sh`) and shows the included usage for the billing cycle, how much of it went to Auto/Composer versus hand-picked API models, on-demand spend against its limit, the plan name and the cycle's end. Enterprise seats get no plan block from Cursor — the CLI says the same — and the tab reports that instead of a number. A **Stats** sub-tab mirrors the Usage page of cursor.com/dashboard for the current billing cycle: tokens, the usage value Cursor prices it at, requests, conversations, active days, peak hour, a per-day sparkline and a per-model breakdown, from the same `GetAggregatedUsageEvents` / `GetFilteredUsageEvents` calls the dashboard makes (the request list is capped at 500 per refresh; beyond that the per-day figures cover the newest requests). Free/Hobby accounts without an included allowance report **usage unavailable**: the billing endpoint can return all-zero percentages even when agent usage is blocked. These are not an agent-session quota or reset timer, so the widget does not draw meters or record quota history for that response. Dashboard stats remain available. Cursor is opt-in, so enable it under **Settings → Providers**.

## Cline

The Cline tab is **offline**: it reads the session records the Cline CLI writes to `~/.cline/data/sessions/<id>/<id>.json` and keeps only the provider, model, workspace folder name, start and end time, and the token and cost totals (sub-agents included). Prompts, titles, git remotes and transcripts are never read into the result. **Usage** shows today, the last 7 days and the last 30 days — tokens, sessions, and spend when Cline recorded one; a lifetime token count says little on its own, since a token costs very different amounts from model to model. **Stats** keeps the all-time picture: tokens, spend, sessions, active days, streaks, longest session, peak hour, a per-day sparkline, top workspaces and a per-model breakdown. Cline is opt-in, so enable it under **Settings → Providers**.

Cline's aggregate `totalCost` is provider-owned and belongs to Cline's provider
reporting. It is intentionally not surfaced as a per-session or local actual
value. A local Cline cost is an estimate only when trustworthy per-session
model and token fields exist and the exact cached model rate is available;
otherwise the local session cost remains unavailable.

## Muse

The Muse tab is **fully offline** — it opens no socket, and a test enforces that against the provider's import graph. It reads what Muse Code writes to disk anyway:

| File | What the tab takes from it |
| --- | --- |
| `~/.local/share/muse/sessions/**/session.jsonl` | sessions, subagents, turns, messages, tool calls, per-call token counters, workspace folder name |
| `~/.local/share/muse/sessions/.msp-view-v1/<id>/snapshot-*.json` | the folded counted-once token totals — the same numbers the TUI's `/usage` prints |
| `~/.local/share/muse/model-catalog/*.json` | every model id, its real context limit, and its price list |
| `~/.config/muse/settings.json` | the model the CLI will use next |
| `~/.config/muse/auth.json` | login presence and display name only; the stored tokens are never read |

Because Meta ships the price list to disk, this is the one tab that can price its own usage offline: tokens × the catalog's rates gives the spend estimate, per model and in total, in the catalog's own currency. Nothing is hardcoded — a new Muse model needs no widget update.

**The plan quota is opt-in, because it is the one number here that costs money.** The Current/Weekly windows in the TUI's `/usage` screen are stored nowhere: they arrive only as a `response.subscription_usage` frame riding a live model call, and are absent from the MSP wire schema, the view fold, `session-index.db` and the feature-config cache. The CLI has no `usage`/`status`/`quota` subcommand either, and the frame is the *last* event on the stream — after generation has been paid for — so the call cannot be cut short. (Running `/usage` in the CLI itself stays free: it re-displays what a call you already made told it.)

So reading it from a widget means one minimal model call per refresh — about 12 input and 120 output tokens, `store: false`, cached 30 minutes. At the contributor tier that is a few cents a year; at standard rates closer to ten dollars. [The provider contract](provider-contract.md) says a statistic must not cost the user, so:

- **Off by default.** Out of the box the tab makes no network call at all — `providers/muse.py` imports no networking module, and a test enforces that against its import graph.
- Turn it on in settings → *Muse Quota* (Hyprland has the same toggle, or `WIDGET_MUSE_QUOTA=1` / `museQuota: true`). Both panels state the cost next to the switch, priced from your own model's catalog rates.
- The billed path lives in a separate module (`providers/muse_quota.py`) so it cannot be reached by accident, and every failure falls back to the free local numbers — saying whether the credential was refused or the endpoint was unreachable, rather than calling a working key invalid.
- `MUSE_QUOTA_TTL_SECONDS` (default 1800) bounds how often it can fire, so a 5-minute poll interval cannot become a per-poll model call.

The tab itself is off by default too: enable *Muse* in settings if you use Muse Code.

## Local sessions

The optional Sessions tab (**Settings → Views**) lists recent local activity merged
from Claude Code, Codex, Grok CLI, Cline, OpenCode, Antigravity and Muse: a redacted title
preview, the workspace/session name, active/idle state and recency only — source paths,
raw session IDs, and transcript contents never leave the backend
(`get-ai-usage --sessions`). Claude's title is a clipped opening-prompt preview; raw prompt
text never leaves the backend. Titles can contain prompt text or artifact headings; they are
shortened, not scrubbed of secrets, and may expose sensitive text in the local UI.

Rows for Claude Code, Codex, Grok CLI, Cline, OpenCode and Antigravity carry a ⧉ button that resumes that
exact session in your terminal (`get-ai-usage --open-session <key>`, using each
CLI's own resume flag: `claude --resume`, `codex resume`, `grok --resume`,
`cline --id`, `opencode --session <id>`, `agy --conversation <id>`). The button spawns your `$TERMINAL`, falling back through
ghostty/alacritty/kitty/wezterm/konsole/gnome-terminal/xfce4-terminal/xterm, and
resumes headless in the background if none is found. **Muse has no button** — it
ships no CLI binary here and documents no resume/continue flag, so its rows are
display-only.

Antigravity CLI transcripts are read from
`~/.gemini/antigravity-cli/brain/<id>/.system_generated/logs/transcript.jsonl`;
editor artifact sessions are read from `~/.gemini/antigravity/brain/<id>/*.md`.
The reader examines at most 20,000 lines and 4 MiB per CLI transcript, stopping
at a record larger than 256 KiB. Editor title reads are limited to 2,000 characters.
The Sessions tab displays up to 60 characters of the first user prompt or artifact
heading as plain text. Listing these sessions makes no network requests; choosing
Resume launches `agy`, which can use its own network connections.

Search is backend-powered, not a client-side-only filter. The command
`get-ai-usage --sessions --query <text>` searches all underlying session records
for a non-empty query, using only `provider`, `title`, `sessionName`, `state`,
and `detail`. Empty, whitespace-only, and non-empty queries all return 60-row
pages with exact totals and show **Load more** while another page exists.
`fullTitle`, opaque resume keys, IDs, paths, and transcripts are not searchable or
exposed. The KDE, Hyprland, and Windows frontends request
the backend after their search input debounce, and macOS does the same through
its debounced search task. Existing redaction and opaque resume-key handling
remain unchanged.

## Usage history

Each refresh records the usage values that a provider actually reports into a rolling history of up to 10,000 samples — only when a value moves, plus one sighting an hour, so a flat stretch costs two points rather than one per refresh — used by the chart, spark-lines, burn-rate ETA, and period comparison. Rolling plan windows (Claude, Codex) empty at a known instant, so when the machine was asleep across one the chart replays the drop where it actually happened instead of sloping from the last pre-sleep sample to the first one after wake-up. Most series are percentages; Mistral stores its raw vibe CLI spend and DeepSeek stores its raw balance so their charts retain meaningful units. Existing session and weekly history fields are retained even while a window is unavailable, so five-hour charts can return without migration if providers restore that limit.

History lives in `~/.local/share/tidesi/usage-history-latest.json`, shared by both frontends, so it survives a full uninstall/reinstall; the widget's Plasma config keeps only a recent tail of it as a first-run fallback, because Plasma rewrites every widget's config whole on each change. You can also manually **Export** (copies the shared file to a timestamped snapshot) and **Import** from the settings panel. If a saved file is unreadable or in an unrecognized format, it's discarded and history starts fresh rather than erroring out.

## Local caches

Everything below is local, never holds a credential or a transcript, and can be
deleted at any time — it is rebuilt on the next refresh. `~/.cache/tidesi/`
(`TIDESI_CACHE_DIR` overrides) holds the pricing catalog (7 days), the Codex
rate-limit reply (120 s, keyed by a hash of the token and `CODEX_HOME`, so
several widgets do not each launch `codex app-server`), the Antigravity usage
(90 s), the session index `sessions.sqlite3`, and `last-snapshot.json`, the last
good envelope a freshly started Plasma widget shows (marked stale) until its
first fetch. `~/.cache/tidesi/` holds the Codex and Mistral Vibe local
statistics, recomputed only when a log is added, removed or changed. Grok's
session discovery is cached in memory for one collection only.

Plasma widget instances share their settings through
`~/.config/tidesi/plasma-shared-settings-<widget id>.json` (mode 0600); pins, panel
rotation and view state stay per widget.

## MiMo Code

The `mimo` provider reads MiMo Code's OpenCode-compatible SQLite database at
`$XDG_DATA_HOME/mimocode/mimocode.db`, normally under `~/.local/share`.
Platform data roots come from `paths.py`; `MIMO_DB` can override the database
with an absolute path or a filename under a data root's `mimocode` directory.
The reader uses read-only SQLite connections and never launches the CLI to
collect usage. It shares OpenCode's assistant-message and step-finish parsing,
including duplicate prevention, but keeps MiMo sessions and costs under their
own source. Messages recorded in MiMo’s `external_import` or legacy
`claude_import` tables are excluded. Imported-only conversations are hidden;
native continuations count only newly generated messages. Legacy imports
without message IDs are excluded entirely because their origin is ambiguous.
The UI reports tokens, models, activity periods, and cost where
recorded or priced by an exact catalog match. Subscription limits and remaining
quota are unavailable. Session reopening uses `mimo --session <id>`.

Validated against the installed MiMo Code 0.1.15 schema and synthetic fixtures;
live paid requests and subscription quota were not tested.


## Junie CLI (untested)

The `junie` provider reads `$JUNIE_HOME/sessions`, defaulting to
`~/.junie/sessions` on Linux/macOS and `%USERPROFILE%\.junie\sessions` on
Windows. It detects the `junie` executable, including the official installer's
`~/.local/bin` location. Existing settings keep Junie disabled until selected
or until **Detect installed providers** is run.

The collector reads `index.jsonl`, per-session `summary.json` metadata, and
`events.jsonl`. It never reads authentication settings, launches Junie, sends a
model request, or contacts a billing endpoint. Changed files invalidate its
bounded in-process parser cache and the shared session search index.

Available data: recorded input/output/cache-read/cache-creation tokens,
per-model breakdowns, daily activity, Today/7-day/30-day/all-time periods,
workspace names, session titles, recency and resume actions. Failed sessions
without token events stay in Sessions but do not report a fabricated zero
usage reading. All frontends use the existing stats and session components.

**Verification:** format checked against the installed JetBrains Junie
3419.22 `SessionStore`, `UsageAggregator`, `LlmResponseMetadataEvent` and
`ModelUsage` serializers, plus local failed-session metadata and three recorded
Gemini usage entries. Tests use synthetic usage events. Live authorization,
JetBrains subscriptions and billing units have not been tested; the UI remains
explicitly marked untested.

The event `cost` has no stored unit or billing route. Junie's own `/usage`
resolves the balance unit from the current authorization, so the widget cannot
safely call historical values USD or infer which account paid. Dollar spend,
remaining credits, subscription limits, reset dates and account identity remain
unavailable. There is no verified free standalone quota endpoint in the
published CLI repository/documentation. IDE-only history is outside this reader.

### BYOK without JetBrains account authorization

Junie's [quickstart](https://junie.jetbrains.com/docs/junie-cli.html) supports
BYOK on its own. Use `/account` → **Use your own API key**, choose Google, then
`/model`; or, in a Linux/macOS shell with an existing Google key:

```sh
export JUNIE_GOOGLE_API_KEY="$GOOGLE_API_KEY"
junie --provider google
```

If the existing variable is `GEMINI_API_KEY`, substitute that variable. In
Windows PowerShell:

```powershell
$env:JUNIE_GOOGLE_API_KEY = $env:GOOGLE_API_KEY
junie --provider google
```

Google BYOK needs `JUNIE_GOOGLE_API_KEY` and provider `google`; a generic Google
variable alone does not select Junie's BYOK route. Other providers have
`JUNIE_ANTHROPIC_API_KEY`, `JUNIE_OPENAI_API_KEY`, `JUNIE_GROK_API_KEY`, and
`JUNIE_OPENROUTER_API_KEY`. Provider usage is billed directly by that provider.
Keep these keys in Junie, not the widget.

Sources: [JetBrains repository](https://github.com/JetBrains/junie),
[BYOK](https://junie.jetbrains.com/docs/byok.html),
[environment variables](https://junie.jetbrains.com/docs/environment-variables.html),
[CLI reference](https://junie.jetbrains.com/docs/parameters.html).
The bundled green SVG is the Junie brand mark from
[the official site](https://junie.jetbrains.com/), fetched on 2026-10-01;
it remains JetBrains artwork, separate from the LobeHub icon license.

## JetBrains AI

The widget reads the AI Assistant quota that every JetBrains IDE keeps in
`<config>/JetBrains/<IDE><version>/options/AIAssistantQuotaManager2.xml`
(`~/.config` on Linux, `~/Library/Application Support` on macOS, `%APPDATA%` on
Windows; Android Studio is under `Google` instead of `JetBrains`). The file holds
two JSON documents in XML attributes: `quotaInfo` (monthly credits used and
maximum, plus top-up credits) and `nextRefill` (the refill date, which is the
reset). When several IDEs are installed the file written last wins. No socket is
opened and no credential is read; the figures are as fresh as the last time an
IDE was running. This provider is manual-only: IDE installs are too varied for
the stat-only detection rules. `JETBRAINS_QUOTA_FILE` points at one file
explicitly.

## Windsurf

Windsurf caches its plan in the editor's `state.vscdb` (SQLite) under
`User/globalStorage`, key `windsurf.settings.cachedPlanInfo`. The widget opens
it read-only. Newer caches carry daily and weekly **remaining** percentages with
reset stamps (shown here as used percentages); older ones carry message and
flow-action counters, which are used when the percentages are missing. The cache
is only rewritten while Windsurf runs, so the tab says when it was last written.
`WINDSURF_STATE_DB` overrides the path.

## Pi / OMP

Both coding agents write one JSONL transcript per session under
`~/.pi/agent/sessions` and `~/.omp/agent/sessions`. `PI_CODING_AGENT_DIR` (its
`sessions` folder) and `PI_CODING_AGENT_SESSION_DIR` override the locations.
Token counters come from assistant `message` entries and stand-alone `usage`
entries; repeated entry ids (forked or resumed sessions) count once. Only
counters, model ids and the workspace folder name are kept.

Cost is deliberately not shown: Pi records an API-rate price on every turn even
when the model was reached through a subscription login, so presenting it as
spend would overstate the bill. The OMP location is the one these agents are
documented to use; it has not been checked against an OMP install.

## Kilo

Kilo's web app talks to its backend over tRPC. The widget sends one GET batch
to `https://app.kilo.ai/api/trpc` for `user.getCreditBlocks` (prepaid credit,
summed from `creditBlocks`) and `kiloPass.getState` (the subscription period:
usage against base + bonus credits, and the renewal date). The credential is the
widget setting / `$KILO_API_KEY`, then the `kilo.access` token the `kilo` CLI
stores in `~/.local/share/kilo/auth.json`; a refused key falls back to the CLI
login. An empty prepaid balance is shown as exhausted. The request shape follows
Kilo's own web API and has not been verified against a live account.

## CodeRabbit

The widget runs `coderabbit usage` (20-second timeout) and parses its labelled
text report: organization, plan, user, usage billing, the review count and the
period reset date. CodeRabbit publishes a count, not a quota, so the tab shows
meterless rows. The widget never handles a CodeRabbit key; the CLI makes its own
request with its own login.

## Zed *(untested)*

On macOS the Zed editor keeps its sign-in as an internet-password item in the
login Keychain (server `https://zed.dev`, or the `credentials_url` /
`server_url` in `~/.config/zed/settings.json`; the account is the Zed user id).
The widget reads it through `/usr/bin/security` and sends it to Zed's own cloud
API (`GET /client/users/me`) the way the editor does, then shows the plan and the
edit-prediction usage and the billing-period end. A custom server must use HTTPS.
On Linux and Windows the login is in the system secret store, which this package
has no safe way to read, so the tab explains that. Response field names are read
defensively and the whole path is untested against a live account.

Artwork: `jetbrains.svg` and `zed.svg` come from the
[simple-icons](https://github.com/simple-icons/simple-icons) set (CC0), fetched
on 2026-10-10 — the marks themselves remain their owners' trademarks. The
`windsurf`, `pi`, `kilo` and `coderabbit` icons are simple monograms drawn for
this widget, not official artwork.
