# Provider data contract (schema version 1)

Every frontend gets all of its provider data from a single backend: the one
shared UI (`ui/`) in every host — KDE Plasma (`hosts/kde`), Quickshell
(`hosts/quickshell`), and the Windows and macOS tray app (`hosts/desktop`) —
and the terminal frontend (`tidesi/render.py`). The process-based hosts run it
through `backend/sh/get-ai-usage` (`ui/CommandBackend.qml`); the tray app calls
it in-process. See `docs/architecture.md`.

```
shared provider backend (Python, stdlib only)   backend/tidesi
  - credential discovery, HTTP, local-data reads   tidesi/providers/, tidesi/http.py
  - normalization                                  tidesi/normalize/
                 │
                 ▼  thin bash launcher, execs into the package above
  backend/sh/get-ai-usage
                 │
          stable JSON model
           ┌─────────┼─────────┐
           ▼         ▼         ▼
      ui/ (every host)       terminal
                         tidesi/render.py
```

No frontend performs a provider network request, parses a provider response, or
computes a quota percentage or reset window. They map the fields below onto
their own widgets — or columns — and nothing else.

## Invoking the backend

```bash
get-ai-usage --provider claude            # one provider
get-ai-usage --provider claude,openai     # several (KDE: active tab + pins)
get-ai-usage --all                        # every enabled provider (Hyprland)
get-ai-usage --all --stream               # completed providers, then the full envelope
get-ai-usage --normalize < envelope.json  # replay a raw envelope, no network
get-ai-usage --list                       # known provider ids
get-ai-usage --sessions --query <text>    # search local sessions through the backend
get-ai-usage --sessions --source <ids>      # filter cached sessions by source
get-ai-usage --detect-providers             # report installed tools, no collection
get-ai-usage --initialize-provider-defaults # apply and return persisted settings
get-ai-usage --save-snapshot --provider claude  # fetch, and keep it as the last good envelope
get-ai-usage --last-snapshot                # the last good envelope, {} when none
WIDGET_SHARED_PATCH='{"claudeEnabled":true}' get-ai-usage --shared-settings  # Plasma: merge + print shared settings
```

`--stream` is opt-in newline-delimited JSON. Each completed provider produces a
flushed envelope with `partial: true` and one complete normalized provider in
`providers`. Arrival order is completion order; the final envelope has no
`partial` flag and retains the normal provider order and `localSpend` aggregate.
Only the final envelope is saved by `--save-snapshot`. Without `--stream`, stdout
remains one JSON envelope for existing CLI consumers.

Quickshell (including the DMS plugin) and the desktop tray app publish partial
results immediately. The shared UI merges them by provider ID, preserving cached
peers and ignoring providers disabled during the refresh. Partial updates do not
record history or mark the overall refresh complete. KDE's executable DataSource
delivers stdout only at process exit, so its current adapter still applies the
final snapshot rather than displaying intermediate results.

The terminal frontend renders that same model, either fetching it itself or
reading an envelope on stdin:

```bash
tidesi-cli                        # table of every enabled provider
tidesi-cli --compact              # one line, for status bars
get-ai-usage --all | tidesi-cli   # render a fetched envelope, no second fetch
```

`--all` respects the provider toggles in the shared settings file
(`$XDG_CONFIG_HOME/tidesi/hyprland-settings.json`, overridable with
`TIDESI_CONFIG`). `--provider` fetches exactly what was asked for, because the
Plasma widget keeps its own toggles in the plasmoid configuration.

### Provider defaults and one-shot detection

Provider defaults are zero based. A new shared settings file starts with every
entry in `config.ALL_PROVIDERS` set to `false`. The explicit initializer is the
only operation that changes those defaults automatically. It runs detection once,
enables only providers returned by `detect_providers()`, writes the resulting
settings, and sets `providerDefaultsApplied: true`. Once that latch is true, the
initializer returns the persisted settings without probing or rewriting them.

The initializer is not implicit. In particular, `--all` only reads the current
shared settings and never initializes them. This keeps a fetch from changing a
user's configuration as a side effect. A frontend that wants first-run defaults
must call `--initialize-provider-defaults` explicitly and use the returned
`data` object according to its own settings model.

The two CLI modes have stable, separate contracts:

```json
{"ok":true,"data":["claude","cursor"]}
```

`--detect-providers` returns a data list in canonical provider order. It reports
the providers whose program is installed right now (a CLI on the search path or
a desktop application); leftover logs, session folders and credential files are
not evidence, because they survive an uninstall. It does not read file
contents, open SQLite databases, invoke subprocesses, contact a network, or
return credentials. Its result is not proof that a provider account is
authenticated or that a quota request will succeed.

```json
{"ok":true,"data":{"providers":{"claude":true},"providerDefaultsApplied":true}}
```

`--initialize-provider-defaults` returns the persisted settings object, including
any existing fields. For an existing settings file, boolean provider choices
are explicit user choices and are preserved. Missing or non-boolean provider
entries are materialized using the legacy defaults, then detected providers are
enabled unless they had an explicit choice. This is the migration path for
older shared JSON files. A failed atomic write leaves the existing file intact.

The shared JSON policy applies to Hyprland, Windows, and macOS. Plasma has a
separate KConfig store in `Plasmoid.configuration.<id>Enabled`, with its own
per-provider defaults and user choices. Plasma must not be described as reading
the shared JSON toggles for its provider tabs. Plasma instances do share their
settings with each other through `--shared-settings`
(`$XDG_CONFIG_HOME/tidesi/plasma-shared-settings-<widget id>.json`,
mode 0600, one file per installed widget id): a
patch names only the keys it changes, so two widgets editing different settings
never undo each other. Pins, panel rotation, the open tab, chart view state and
the per-widget history fallback are not shared. `--last-snapshot` reads
`last-snapshot.json` in the cache directory, merged per provider from every
`--save-snapshot` fetch and replayed (marked stale) by a widget that has just
started. The backend's `--all` mode still
uses shared JSON, regardless of which frontend is displaying it. See
[`provider-detection.md`](provider-detection.md) for the complete policy and
the maintainer checklist.

## Pricing catalog refresh

The shared model pricing catalog uses [models.dev](https://models.dev/) as its
primary source, keyed by OpenCode's exact provider ID and model ID. Prices are
USD per million tokens: `input`, `output`, and optional `cache_read` become the
internal `input`, `output`, and `cached` rates. LiteLLM is an exact-match
fallback for Anthropic and OpenAI; OpenRouter remains a last-resort fallback for
requested misses. No aliases, prefix stripping, or near matches are accepted,
and a genuinely free zero rate is valid.

The catalog has a fixed TTL of **604800 seconds**, exactly seven days. A normal
catalog read reuses usable rates until that TTL expires; failed refreshes retry
after **900 seconds**. The `--refresh-pricing` operation is the manual force
refresh: it bypasses the TTL once. Concurrent forced refreshes are single-flight,
so they share one download and one result. A versioned cache rejects malformed
or incompatible snapshots.

The refresh result is a compact object with only `ok`, `status`, `fetchedAt`,
and `error`.

If a refresh fails after usable rates have been saved, those last-good rates and
their original `fetchedAt` remain available. The operation returns `ok: true`,
`status: "stale-good"`, and a non-empty `error` describing the failed refresh.
A successful refresh returns `ok: true` and `status: "refreshed"`. When no
usable rates exist, it returns `ok: false`, `status: "no-cache"`,
`fetchedAt: 0` when no prior fetch exists, and an `error`. The command exits
nonzero only for this no-usable-rates case. Frontends should use `status` and
`error` together rather than treating a refresh error as proof that saved rates
are unusable.

Unknown or unpriced models remain visible in usage data but are not estimated.
Session rows may carry optional structured `costUSD` and `costStatus` values.
`costStatus` is `exact`, `partial`, or `unavailable`; a row with an unknown
model normally has no `costUSD` and uses `unavailable`. For a multi-model row,
the cost is the sum of the structured per-model buckets that can be priced,
not an estimate based on one selected model. Overview and provider totals are
provider-level figures and are not session totals.

### Local session cost provenance

`costProvenance` is optional and independent of `costStatus`:

| value | meaning |
| --- | --- |
| `actual` | finite, positive USD reported by the local provider/session record; this is the only local value that represents a provider-reported amount |
| `estimated` | calculated from exact local input/output/cache token counts and an exact cached model rate; it is not an invoice or subscription bill |
| `mixed` | the session contains both actual and calculated amounts; `costBreakdown` contains finite non-negative `actualUSD` and `estimatedUSD` subtotals whose sum matches the session `costUSD` |

An estimate is allowed only when the local usage fields and model identity are
complete and the cached rate is exact. OpenCode uses the upstream provider ID as
the catalog namespace, and the model string must exactly equal that provider's
catalog key; no alias, prefix stripping, or near-match is accepted. Unknown or
missing model/rate data, incomplete or invalid token data, and metadata-only
records remain `unavailable`. `partial` coverage means that some structured
buckets were priced while others were not; it does not change an estimate into
actual cost.

OpenCode sessions aggregate every structured assistant message they routed,
per provider and model, with no fixed row limit. A session whose messages
span several upstream providers carries a `providerCosts` object with one
entry per upstream provider (for example `ollama-cloud`), each with the same
`costUSD`/`costStatus`/`costProvenance` shape as the session row; the session
row itself keeps the aggregate across all providers. Single-provider sessions
carry `billingProvider` instead.

The envelope's `localSpend` object has two independent aggregate objects,
`actual` and `estimated`. Each may contain `totalUSD`, `costStatus`,
`costProvenance`, and provider rollups keyed by local provider identity. A
provider with explicit source metadata uses `provider::source` (for example,
`anthropic::opencode`); a native or legacy row without source metadata keeps
the plain provider/source key. Spend views merge actual and estimated values
only when this full identity matches, so native Anthropic cannot inherit
OpenCode's `via OpenCode` note. OpenCode rows retain the upstream provider
label with `via OpenCode` secondary metadata. These local rows are never
included in provider/API spend and are not invoice totals. A mixed session
contributes its two `costBreakdown` subtotals to the corresponding identity
rollup. When no qualifying numeric rows exist, the aggregate contains only
`costStatus: "unavailable"`.

Session activity is locally observed data. The contract does not promise parsing
formats that do not produce supported local records, and frontends must not
infer missing costs or session details.

Antigravity also reads `antigravity-cli/conversations/*.db`,
`antigravity-ide/conversations/*.db` and `antigravity/conversations/*.db` below
its configured Gemini home, using read-only SQLite connections. A locked or
interrupted database marks the scan incomplete so it is retried; a corrupt
database, or a generation record over 4 MiB, is skipped on its own. Generation metadata supplies machine model IDs and separate
new-input, cached-input, output, and reasoning token counts. Response IDs dedupe
repeated generation records within a database. Missing model IDs are recovered
only from unambiguous model/label associations in that same database; unknown
IDs are never assigned a default model or price. Exact cached model rates yield
`costProvenance: "estimated"` and `costBilling: "subscription"`; mixed coverage
is partial. The protobuf field layout follows the independently documented
[tokscale Antigravity reader](https://github.com/junhoyeo/tokscale/blob/39ecb83ab045a300c6f502f9c9319acf5c80e764/crates/tokscale-core/src/sessions/antigravity_cli.rs).
Private numeric caches are invalidated by parser and database/WAL changes and
contain no prompts, paths, session IDs, or response IDs. Transcript/artifact
metadata is joined by session identity without counting its tokens a second time.

Cline's aggregate `totalCost` is provider-owned aggregate reporting. It is not a
per-session local actual and is intentionally excluded from the local session
actual aggregate. Cline contributes to a local calculated estimate only when
trustworthy per-session model and token fields are present and the exact cached
model rate is available; otherwise its local session cost is `unavailable`.

### Session search

`get-ai-usage --sessions --query <text>` performs session search in the shared
backend. Empty, whitespace-only, and non-empty queries all return 60-session
pages by default; `--limit <n>` and `--offset <n>` select a page. The response
includes the exact `total`, `totalExact`, `offset`, `limit`, and `hasMore`
metadata, so frontends can append another page with **Load more** rather than
loading every match in one response or filtering client-side. A non-empty query
searches the underlying session records rather than filtering only that
60-session result, using only the safe display fields
`provider`, `title`, `sessionName`, `state`, and `detail`. It does not search
`fullTitle`, opaque resume keys, IDs, paths, or transcripts. Claude's `title`
is a clipped opening-prompt preview; raw prompt text never leaves the backend.
Provider readers retain their existing safety limits while supplying records
for the search.

#### Session sources

Session responses retain the existing fields and add the following field:

```json
{
  "sources": [
    { "id": "openai", "label": "Codex" },
    { "id": "opencode", "label": "OpenCode" }
  ]
}
```

`sources` describes only providers that have cached, parsed session rows. It is
returned independently of the active source filter, search text, page, and
page size. Descriptors use this canonical order and these verified IDs and
labels:

| ID | Label |
| --- | --- |
| `cline` | Cline |
| `muse` | Muse |
| `openai` | Codex |
| `grok` | Grok |
| `claude` | Claude Code |
| `opencode` | OpenCode |
| `mimo` | MiMo Code |
| `antigravity` | Antigravity |
| `mistral` | Mistral |
| `cursor` | Cursor |
| `junie` | Junie |

The command line accepts `--source <id[,id...]>` and
`--source=<id[,id...]>`, only with `--sessions`. IDs are trimmed, validated
against the registry, deduplicated, and sent in canonical order. Unknown IDs,
empty tokens, and a source option without `--sessions` are errors. Omitting
`--source`, or passing an empty source selection from a frontend, means All.

Multiple source IDs are an OR filter. Text search is then applied to the
matching rows, and `total`, `totalExact`, `offset`, `limit`, and `hasMore` are
computed for that filtered result. Changing sources starts at offset zero.
Refresh and Load more preserve the current source selection and search text;
Load more appends the next page. If a refresh reports that a selected source
is no longer available, frontends clear the stale selection and issue one
query-only All request.

The source list is view-local and is not persisted. In KDE Plasma,
Hyprland, Windows, and macOS, the selector sits beside the Sessions search
field and leaves the title row unchanged. It is hidden when there are no
sources. With one source, it shows that source without an All option. With
multiple sources, All appears first, selecting multiple entries is supported,
and selecting every source normalizes back to All. These frontends share the
same cache, canonical IDs, labels, OR semantics, pagination reset, refresh,
Load more, stale-response, and stale-selection behavior.

`--query-only` reads the already reconciled cache and does not scan provider
stores, build a manifest, or collect new rows. A refresh may be incomplete if
one or more local stores cannot be read. When a valid persisted cache is
available, the backend preserves that cache; if all collectors fail, the
direct fallback may instead contain no rows and no source descriptors. A cache
read failure returns an empty `sources` list and an empty schema-complete
result.

The source metadata and searchable fields remain public, redacted data. They
expose no filesystem paths, raw session IDs, transcripts, full titles, opaque
resume keys, source fingerprints, or credentials. The existing row resume flow
still passes only its opaque `openKey` back to `--open-session`; that key is not
part of source metadata and remains subject to the existing row redaction and
open-session boundary. Existing callers that do not send a source selection
remain compatible; `sources` is additive to the session response.

The session views in KDE, Hyprland, Windows, and macOS send search requests to
this backend instead of filtering locally. Their search inputs are debounced
before a request is started, including the macOS input. Session rows remain
redacted, and existing opaque resume keys are still the only values passed back
to `--open-session`.

API keys come from `WIDGET_*` environment variables (what Plasma passes) or from
the `keys` object of the settings file (what the Hyprland settings page writes).
The environment always wins.

Beyond that, each provider searches the places its vendor's own tooling uses, in a
fixed order: the `WIDGET_*` variable, then one or more conventional environment
variables, then the first readable config file. Where a vendor documents a different
variable name than the one this package grew up with, both are accepted —
`Z_AI_API_KEY` alongside `ZAI_TOKEN`, `KIMI_API_KEY` alongside `MOONSHOT_API_KEY` —
because a provider that knows only one spelling reports "no token configured" at
somebody who did set the key. Several providers additionally borrow a credential another tool already stores:
`~/.config/glm-acp-agent/credentials.json` for Z.AI, `~/.vibe/config.toml` for
Mistral, the Copilot editor/CLI logins and `gh auth token` for Copilot, and —
only on its opt-in quota path — the Muse login store for Muse. A borrowed
credential always ranks last, so an explicit one wins.
`tests/python/test_credentials.py` pins the order with an isolated home.

## Envelope

```json
{
  "schemaVersion": 1,
  "updatedAt": 1785000000,
  "active": "claude",
  "providers": [ { "…provider result…" } ]
}
```

`active` is the first healthy provider, used by the Hyprland shell to pick a tab
when its remembered one disappears.

## Provider result

```json
{
  "id": "claude",
  "label": "Claude",
  "accent": "#cc785c",
  "icon": "claude-color.svg",
  "ok": true,
  "stale": false,
  "error": "",
  "updatedAt": 1785000000,
  "summary":       { "pct": 23, "text": "23%", "detail": "max", "hasChart": true },
  "quotaWindows":  [ { "…window…" } ],
  "slots":         [ { "…panel pill…" } ],
  "historyValues": { "s": 23, "w": 61 },
  "details":       { "…provider specific…" }
}
```

| field | meaning |
| --- | --- |
| `icon` | brand logo filename under `contents/icons/`, `""` when the provider has no artwork yet (frontends fall back to an `accent` dot). Bare filename, not a path — the two frontends sit at different depths and each resolves the directory itself |
| `ok` | the provider produced usable data |
| `stale` | data is missing or older than this refresh |
| `error` | human-readable failure, `""` when healthy. Well-known values: `offline`, `rate limited`, `token expired`, `access denied`, `err <http status>`, `python3 missing` (unlike the others, not retried — see the bash launcher in `tools/sh/get-ai-usage`; the string is fixed vocabulary and means "no Python 3 interpreter could be resolved by `tools/sh/python-interp.sh`", not that the literal `python3` binary is absent) |
| `updatedAt` | epoch seconds when the data was collected |
| `summary.pct` | headline percentage (0–100) |
| `summary.text` | headline string, already formatted (`"23%"`, `"$12.5"`, `"CLI"`) |
| `summary.detail` | plan / account line |
| `summary.hasChart` | false when the provider has no series worth charting |
| `quotaWindows[]` | ordered rows: `key`, `label`, `pct`, `available`, `resetAt`, `resetText`, `detail`, `showMeter`, and optionally `note` |
| `note` | aside shown beside the value. Only meaningful with `showMeter: false`, where `detail` becomes the value itself and would otherwise leave the row no room for context. Optional — a frontend that ignores it loses the aside, nothing else |
| `chartWindows[]` | chart ranges — see below. Empty when the provider has no chartable series |
| `slots[]` | compact panel pills: `pct`, `color`, `text` (null → show the meter), `tooltip` |
| `historyValues` | series keys this provider contributes to the shared usage history |
| `details` | everything the detailed KDE tabs render |

All timestamps are **epoch seconds**, `0` meaning "no reset known".
`resetText` is a preformatted local-time string for frontends that do not want
to format it themselves; the Plasma widget formats from `resetAt` instead so it
keeps following the desktop locale.

### Chart windows

Which history series a provider contributes, and what each chart range means.
Both frontends render the list they are handed rather than keeping their own
copy of the table, so a provider's ranges are defined in exactly one place.

| field | meaning |
| --- | --- |
| `id` | range identifier, persisted as the user's selection (`session`, `codex_weekly`, `kiro`, …) |
| `key` | which `historyValues` series it plots |
| `label` | button text (`5H`, `24H`, `7D`, `30D`) |
| `size` | how much time the chart shows, in ms |
| `granularity` | `5h` / `24h` / `7d`, or `""` for a provider with a single fixed range. Frontends carry it across tabs so switching services keeps the range |
| `raw` | the series holds absolute money rather than percentages, so the chart auto-scales it to its own maximum |
| `resets` | the underlying quota empties periodically |
| `periodMs` | how often it empties (0 when `resets` is false) |
| `resetAt` | epoch seconds of the *next* reset, which anchors every earlier one |

`size` and `periodMs` differ for the 24H range: it plots the five-hour series
over a wider span. `periodMs` and `resetAt` are what let a frontend redraw a
reset that happened while the machine was asleep at the moment it really
happened, instead of sloping from the last pre-sleep sample to the first sample
after wake-up (`UsageHistory.withResets`).

### History series keys

| key | series |
| --- | --- |
| `s` / `w` | Claude 5-hour / 7-day |
| `cp` / `cw` | Codex 5-hour / weekly |
| `ag` | Antigravity average |
| `agg` | Antigravity Gemini |
| `age` | Antigravity External / rest |
| `kr` | Kiro credits |
| `or` | OpenRouter credit usage |
| `jb` | JetBrains AI monthly credits pct |
| `ws` | Windsurf highest of daily / weekly quota pct |
| `kl` | Kilo highest of credits / Kilo Pass pct |
| `zd` | Zed edit predictions pct (limited plans only) |
| `mv` | Mistral vibe spend (absolute USD, auto-scaled by the chart) |
| `gr` | Grok credits |
| `za` | Z.AI tokens |
| `gh` | Copilot premium requests |
| `ds` | DeepSeek balance (absolute) |
| `km` | Kimi / Moonshot balance (absolute) |
| `kc` / `kcw` | Kimi Code 5-hour / weekly plan pct |
| `cu` | Cursor included usage |
| `mu` | Muse tokens (absolute) |
| `mc` / `mw` | Muse Current / Weekly plan pct — only present when the opt-in quota is switched on |

### Credentials

Credentials and access tokens are **never** part of a result. The backend
exposes presence only: `details.hasKey` and `details.keyValid` for
single-credential providers, the specific `details.hasOAuth` /
`details.hasAdminKey` / `details.hasApiKey` / `details.codexLoggedIn` for the
two that accept more than one, and `details.hasLogin` for Muse, whose default
path needs no credential at all. A contract test asserts that no fixture secret
can appear anywhere in a result.

Most helpers now report presence rather than echoing the credential back, so a
token never crosses a process boundary at all. The exceptions are
`get-openai-usage` and `get-grok-usage`, whose credentials the backend itself
needs in order to call the plan endpoints.

## `details` per provider

**claude** — `hasOAuth`, `hasAdminKey`, `subscriptionType`, `rateLimitTier`,
`organizationUuid`, `effortLevel`, `autoDream`, `session`/`weekly`
(`available`, `pct`, `resetAt`, `tokensUsed`, `tokenLimit`), `scopedWeekly`
(one entry per `weekly_scoped` limit — a narrower week that runs alongside the
all-models one, such as Fable; `key`, `label`, `model`, `available`, `pct`,
`resetAt`, and a matching `quotaWindows` row), `extraTokens`,
`extraUsage` (`enabled`, `limit`, `used`, `pct`, `currency`),
`organizationUsage` (`models` keyed by model with `input_tokens`,
`output_tokens`, `cost_usd`, `priced`, plus `totalInputTokens`,
`totalOutputTokens`, `totalCostUSD`), `stats`, `status`.

**openai** — `hasApiKey`, `codexLoggedIn`, `email`, `planType`, `orgId`,
`accountId`, `authMode`, `codex` (`available`, `limitReached`, `session`,
`weekly`, `additional[]` with `name`, `limitReached`, `session`, `weekly`),
`organizationUsage`, `stats` (plus `model` and `effortLevel` from the newest
rollout), `status`.

**antigravity** — `email`, `planType`, `promptCreditsMonthly`,
`promptCreditsAvailable`, `pct`, `googlePct`, `externalPct`, `resetAt`,
`models` keyed by model id (`displayName`, `usedPct`, `resetTime`, `resetAt`,
`isExhausted`, `hasQuota`), `groups[]` (`key`, `label`, `usedPct`, `resetAt`,
`isExhausted`, `models`).

**kiro** — `available`, `planType`, `displayName`, `displayNamePlural`,
`currentUsage`, `usageLimit`, `pct`, `remaining`, `currentOverages`,
`overageCap`, `overageCharges`, `overageRate`, `currencyCode`,
`currencySymbol`, `resetAt`, `source` (`cli` = live from the kiro-cli login,
`ide` = the Kiro IDE's cached snapshot).

**mistral** — `hasKey`, `keyValid`, `availableModels`, `vibe` (`sessionCount`,
`totalCost`, `totalTokens`, `promptTokens`, `completionTokens`, `totalSteps`,
`toolOk`, `toolFail`, `activeModel`, `recent`), `status`.

**openrouter** — `hasKey`, `keyValid`, `label`, `usageUSD`, `limitUSD`
(null = unlimited), `limitRemainingUSD`, `isFreeTier`, `rateLimit`, `status`.

**grok** — `hasKey`, `loggedIn`, `pct`, `used`, `monthlyLimit`, `email`,
`teamName`, `tierId`, `billingPeriodEnd`, `sessionCount`, `totalTokens`,
`totalToolCalls`, `hasBilling`, `quotaKind`, `quotaWindow`, `quotaExhausted`,
`billingError`.

**zai** — `hasKey`, `keyValid`, `level`, `token` (`pct`, `used`, `limit`,
`resetAt`), `tokenLong` (`pct`, `resetAt`), `tools` (`pct`, `remaining`,
`resetAt`), `models`, `today` (`available`, `date`, `rollsOverAt`, `tokens`,
`calls`, `models[]` with `name`/`tokens`, `tools` with `search`/`reader`/
`zread`).

`today` comes from two further monitor endpoints, `model-usage` and
`tool-usage`, both taking `startTime`/`endTime` in `yyyy-MM-dd HH:mm:ss` —
they reject ISO-8601 with a `T`. The bounds are the plain **local calendar
date**, sent without timezone conversion, because that is what the vendor's
dashboard does and matching it is the whole point of the figure (verified
digit-for-digit against a live account). The service applies those bounds on
its own clock, which runs ahead of Europe, so the day being summed is shifted
and the total stops growing before local midnight; `rollsOverAt` carries the
date change so a total that has stopped moving does not read as stuck. Neither
call can fail the provider: the quota windows are the point, and a missing
statistic must not cost them.

**copilot** — `hasKey`, `keyValid`, `username`, `used`, `quota`, `pct`,
`unlimited`, `plan`, `resetAt`, `stats`. The quota and the reset day come from
the plan itself when `/copilot_internal/user` answers (the endpoint the editors
use, which any Copilot login can read); the documented billing endpoint is the
fallback for a token that carries billing scope, and only there does the
configured quota and a guessed first-of-next-month reset apply.

**deepseek** — `hasKey`, `keyValid`, `isAvailable`, `balances`,
`primaryCurrency`, `primaryTotal`, `primaryGranted`, `primaryToppedUp`,
`currency`, `symbol`.

**kimi** — `hasKey`, `keyValid`, `balanceError`, `availableBalance`,
`voucherBalance`, `cashBalance`, `currency`, `codePlan` (`loggedIn`,
`available`, `exhausted`, `message`, `error`, `windows[]` with `label`, `name`,
`seconds`, `pct`, `used`, `limit`, `resetAt`, and `booster` — `balance`, `total`,
`monthlyLimit`, `monthlyUsed`, `currency` — or null). `label` is the English
wording; `name` (the vendor's own, when it sends one) and `seconds` let a
translated frontend word the label itself. The Moonshot balance
and the Kimi Code plan are independent; the provider is healthy when either
answers, and a plan Kimi reports as used up (HTTP 429 `resource_exhausted`)
is a full window, not an error.

**cursor** — `loggedIn`, `source` (`cli` / `ide`), `planName`, `price`,
`totalPct`, `autoPct`, `apiPct`, `hasSplit`, `includedSpend`, `limit`,
`bonusSpend`, `remaining` (USD), `resetAt`, `cycleStartAt`, `onDemandUsed`,
`onDemandLimit`, `displayMessage`, `nextUpgrade` (`name`, `price`), `stats`
(the shared stats shape, for the current billing cycle: `totalTokens`,
`totalCostUSD`, `totalRequests`, `totalSessions` = distinct conversations,
`models` keyed by model with `input`/`output`/`cached`/`cacheWrite`/`total`/
`cost`/`requests`, `dailySeries` with `dailyUnit` `tokens` or `requests`, and
`partial` when the paged request list stopped short). It comes from the
dashboard's `GetAggregatedUsageEvents` and `GetFilteredUsageEvents`; the
backend reduces each request to model, tokens, cost, time and a conversation
ordinal, so no account or conversation identifier reaches the envelope.

**cline** — `stats` (the shared stats shape, all time, from the CLI's local
session records: `totalTokens`, `totalCostUSD`, `totalSessions`, `models`
keyed by model with `input`/`output`/`cached`/`cacheWrite`/`total`/`cost`/
`sessions`, `topWorkspaces`, `dailySeries`) and `periods[]` (`key` —
`cline_today` / `cline_7d` / `cline_30d` — `label`, `sessions`, `tokens`,
`cost`). "Today" starts at local midnight; the other two are rolling. No
network request is made.

**muse** — `hasLogin`, `email`, `fullName` (display identity from the CLI
login store), `current` / `weekly` (`available`, `pct`, `resetAt`),
`quotaError`, `stats`. Deliberately thin: every total, the model and the
currency live in `stats` and are read from there, so no number appears twice in
one envelope.

Muse is the only provider whose quota cannot be read for free, and the only one
that can price itself. Both follow from the same fact: Meta publishes the
Current/Weekly plan windows solely as a `response.subscription_usage` frame on a
live model call — they are in neither the MSP wire schema, the view fold,
`session-index.db` nor the feature-config cache, and the CLI has no
`usage`/`status`/`quota` subcommand.

The provider is therefore split in two, and the rule above decides which half
runs by default:

- `providers/muse.py` is the default path and imports no networking module at
  all — a test asserts that against its import graph, not its text. It reads
  what Muse writes to disk anyway: session logs, the folded counted-once totals
  under `.msp-view-v1/`, the login store (presence and display name only), the
  selected model in `settings.json`, and the model catalog cache. That
  catalog's own `cost` rows are what make the offline spend estimate possible,
  and they are why no model id, context window or price is hardcoded anywhere.
- `providers/muse_quota.py` is the opt-in half: one minimal streaming call per
  `MUSE_QUOTA_TTL_SECONDS` (default 1800), gated on `WIDGET_MUSE_QUOTA`, which
  `config.muse_quota_enabled()` defaults to off. Separate module so the free
  path cannot reach a credential or a socket; its `refresh_cost()` prices one
  refresh from the user's own catalog so both frontends can state the cost
  beside the switch.

`quotaError` distinguishes `disabled`, `no-credential`, `no-model`, `rejected`
and `unreachable` — a flaky network must never be reported as a bad key — and
any quota failure degrades to the free local numbers rather than failing the
provider. A quota that did come back renders even when the local logs are
empty: paying for a window and then discarding it would be the worst of both.

### Shared sub-objects

`stats` (Claude Code, Codex CLI, Copilot CLI and Muse Code): `available`,
`totalMessages`, `totalSessions`, `totalTokens`, `totalToolCalls`,
`favoriteModel`, `firstDate`, `computedDate`, `activeDays`, `spanDays`,
`currentStreak`, `longestStreak`, `longestSessionMs`,
`longestSessionMessages`, `peakHour`, `hourCounts[24]` (activity per hour of
day, hour 0 first, for the heatmap), `models`, `dailyTokens[]` (`date`,
`total`), plus `dailySeries[]` and `dailyUnit` — the per-day series the
frontends draw, named separately because not every CLI counts tokens. Claude
adds `version`, `totalCostUSD` and `totalWebSearches`; Codex adds `model` and
`effortLevel`; Copilot (which records no tokens, models or cost) adds
`totalFiles`, `totalRepositories` and `topRepositories[]` (`name`, `sessions`)
and reports `dailyUnit: "messages"`; Muse adds `subagentSessions`,
`totalInputTokens`, `totalOutputTokens`, `totalCachedTokens`,
`totalReasoningTokens`, `totalCostUSD` (priced offline from the local
catalog), `totalModelCalls`, `contextWindow`, `workspaceCount`,
`topWorkspaces[]` (`name`, `sessions`), `model` and `currency`.

`status` — present on every provider, attached by `normalize()` itself:
`indicator` (`none` | `minor` | `major` | `critical`, or `""` when there is
no live reading), `description`, `components[]`, `incidents[]`,
`latestUpdate` and `url` (the human status page, `""` when the provider has
none). Which page belongs to which provider, and which feed it publishes,
is the `STATUS_PAGES` table in `contract.py`: Atlassian Statuspage
(`/api/v2/summary.json` — Claude, OpenAI, Cursor, Kimi, and Copilot scoped
to GitHub's `Copilot*` components) or Gatus (`/api/v1/endpoints/statuses` —
Cline). Pages with no feed (Antigravity, Grok, DeepSeek, Mistral,
OpenRouter) come through as `indicator: ""` plus a `url`, which the
frontends render as a plain link. Feeds are cached on disk for
`TIDESI_STATUS_TTL` seconds (default 300) so a fast poll interval does not
hammer them.

## Shared frontend code

Three things are identical in both QML frontends and live in
`ui/js/` so they cannot drift (the terminal frontend keeps no
history and formats its own countdowns from `resetText`):

- `Format.js` — countdown formatting (`countdown`, `countdownFromEpoch`).
- `UsageHistory.js` — collecting `historyValues` from a response, the rolling
  series and the state machine that saves it, migrating legacy points, and
  replaying quota resets.

`~/.local/share/tidesi/usage-history-latest.json` is the store, shared
by both frontends, and `tools/sh/history-io` owns every access to it. It holds the
newest `DEFAULT_LIMIT` samples — 10,000, about 40 bytes each, which is a month of
unbroken 5-minute polling and most of a year on a machine that sleeps. A save
carries only new samples, so the size costs nothing per poll; `export` copies the
file rather than taking the series from a frontend, because a single command-line
argument is capped at 128 KB and a full series passes that well before the point
cap does. The Plasma widget's config keeps a 500-sample tail of the same series,
which is also what bounds the startup seed. A save
(`history-io autosave`) takes an `flock`, unions the payload into whatever is
already on disk, replaces the file by rename, and prints the merged series back
to the caller. A lock it cannot take is an error, not something to go ahead
without: the merge is a read-modify-write, so racing the holder would drop one
side's points. The frontend keeps its batch and retries on the next poll. That makes a save a merge rather than an overwrite, so two
frontends — or two Plasma widget instances, which are two writers too — converge
instead of clobbering each other, and each one picks up the other's points on
its next poll without a separate read. The rename matters because a reader that
catches a partial write treats the file as corrupt and *deletes* it.

The union has no way to tell which of two values for one key is the newer one, so
it gives the payload precedence — which makes what a payload may hold the whole
contract. There are two kinds, and `history-io` has an entry point for each:

- `autosave` — **readings the frontend has just taken**, and only those keys.
  Asserted, which is right because nothing anywhere is newer for them. A whole
  series would also carry that frontend's copies of the *other* one's points,
  with the same precedence, and roll them back.
- `seed` — **a series the frontend restored** rather than measured: the widget
  config it starts from, a snapshot the user imported. Offered, not asserted:
  the file keeps its own values and gains only the keys it lacks. It can predate
  what is on disk, and a frontend cannot tell — its copy of the file is from
  whenever it last saved, and the other frontend may have written since. So the
  comparison happens inside the lock, where both sides are visible at once,
  rather than out in the frontend against a copy that may already be behind.

A third rule keeps the two apart: **a point is only ever patched by the writer
that created it.** `UsageHistory.record` patches the caller's own last point and
appends otherwise. The timestamp *is* that ownership, so two frontends polling in
the same millisecond both claim the point and its value flips between their
readings until the merge window passes — left as it is, since closing it means a
writer id in every point for a one-point wobble at roughly 0.1% a day.

**Only changes are stored.** Most readings repeat the one before, so `record`
keeps a flat run as its first sighting, its last sighting before the value moves,
and one sighting an hour. The chart joins samples with curves, and a run's two
ends are what keep the line flat until the change instead of ramping across the
whole run; the hourly one bounds that for a run that ends while nothing is
watching. The latest reading is shown but not saved until its run ends (the
store's `tail`), so the line still reaches the present. The burn rate and the
chart's pulse fit the series resampled every five minutes (`slopePerHour`), not
the stored points, so they do not move with how a run happens to be stored.

The state machine over all this — the two lanes, one batch in flight, what an
answer means — is `UsageHistory.js` too, shared rather than written out twice in
QML, where only a running Plasma session or Quickshell could exercise it. Each
frontend is left with the transport and the timers. One batch is in flight at a
time, because the answer replaces the series with what is on disk and two could
land out of order; anything recorded meanwhile waits its turn. A batch that fails
goes back to its lane and waits for the *next poll* — history-io leaves the file
untouched when it cannot merge, so nothing is lost, and resending immediately
would only fail the same way as fast as the shell can fork.

Neither frontend writes the file before it has read it: the startup read is
asynchronous while the poll timer fires immediately, so a write that got in
first would drop everything recorded under the other frontend. The file wins over
the restored copy for everything it knows; what it does not know follows as a
seed.

The Plasma widget also keeps a copy in its widget config, but only as a backup
to seed a fresh install — it is read at startup and flushed on a slow timer,
because Plasma rewrites `plasma-org.kde.plasma.desktop-appletsrc` (every
widget's config) whole on each change and the series runs to 30-100 KiB.

The union therefore exists twice: in QML (`UsageHistory.union`) for the startup
restore, and in Python (`tidesi/history.py`) for the on-disk merge.
`tests/shared-code.test.js` replays the same cases through both and compares the
JSON byte for byte, so they cannot drift.

A save costs about 60 ms of CPU, nearly all of it process startup — at the
default 300 s poll that is 0.02% of one core, or ~17 s of CPU per day.

## Testing

`tests/python/test_fixtures.py` replays `tests/fixtures/*.json` — raw envelopes for
success, missing credentials, malformed responses, offline and rate-limited
states — through the in-process normalizers, so the whole provider matrix is
covered without network access and runs on Windows.

`test_provider_values.py` preserves the provider-specific expectations.
`test_collect.py` and `test_muse.py` run the real collectors against the response
hooks, local log files and SQLite stores. Recorded bodies formerly embedded in
the shell suite live in `tests/fixtures/*-response.json`. Synthetic settings and
session logs are created by the tests. The raw `muse-quota.json`
response goes through the quota reader instead of the envelope normalizer.

`tests/python/test_cli.py` renders each of those fixtures through the terminal
frontend, asserting among other things that a provider which cannot report still
produces a row — a state the graphical frontends show as a tab or a pill, and
which a table could silently drop instead.

`tests/shared-code.test.js` covers the shared frontend modules. Run them all
with `make test`.

## Changing the contract

Adding a field is backwards compatible. Removing or repurposing one is not:
bump `SCHEMA_VERSION` in `backend/tidesi/contract.py`, update
this document, and update every frontend in the same change — except that
the macOS app needs no change for a new provider, only for a new *field*.

### MiMo Code local activity

The `mimo` provider reports local SQLite activity through `details.stats` and
non-metered period rows. `details.accountMode` is `local`; `historyValues` and
`chartWindows` are empty because no account quota endpoint is used. Session
rows carry `source: "mimo"` and an opaque resume key for `mimo --session <id>`.
Missing or unreadable databases produce an unavailable provider envelope.


### Junie local activity (untested)

`junie` uses the same `details.stats`, activity periods and non-metered windows
as the other local ledgers. `details.untested: true` and the compact summary
label its verification status. It emits no quota
history or reset times. Missing token buckets are flagged by
`details.tokensPartial`; only explicitly recorded non-negative integer counts
contribute. Sessions without usage remain searchable, with no `tokens` field.

Input, output, cache read and cache creation counts come from
`SessionA2uxEvent.event.agentEvent` records whose `kind` is
`LlmResponseMetadataEvent`, using each entry in `modelUsage` as a delta.
Event timestamps determine daily and period totals; summary/index files never
add a second copy of tokens. Standalone subagent transcripts are not counted.
The stored `cost` lacks a currency and historical billing route, so
`costStatus` stays `unavailable`, with no USD session cost or spend contribution.
Session reopening uses `junie --session-id <id>` through an opaque resume key.
See [Junie provider details](providers.md#junie-cli-untested) for paths and sources.

### Local-file and CLI providers

`jetbrains`, `windsurf` and `pi` read files the other program already wrote and
open no socket. `jetbrains` and `windsurf` produce ordinary metered quota rows
(`details.recordedAt` says when the file was last written, since both only
update while the program runs). `pi` uses the shared local-activity shape
(`details.stats`, non-metered period rows, no `historyValues` or
`chartWindows`) and leaves `costStatus` `unavailable`: Pi records an API-rate
price even on subscription logins, so it is never presented as spend.

`kilo` and `zed` make the vendor's own request with the vendor's own login (or
an API key for `kilo`); `coderabbit` runs `coderabbit usage` and exposes only a
review count in non-metered rows. `zed` carries `details.untested: true`.
`openrouter` adds `details.balanceUSD`, `creditsTotalUSD`, `creditsUsedUSD`,
`usageDailyUSD`, `usageWeeklyUSD`, `usageMonthlyUSD` and `keyUnavailable`; any
of them is `null` when OpenRouter did not report it.

### Sources

A provider that can be read more than one way carries a `sources` block on its
envelope — error envelopes included — built by `tidesi/sources.py`:

```json
"sources": {
  "choice": true,
  "selected": "auto",
  "active": "cli",
  "options": [
    {"id": "cli", "kind": "cli", "label": "cursor-agent", "detail": "…", "state": "working"},
    {"id": "ide", "kind": "ide", "label": "Cursor app", "detail": "…", "state": "ready"}
  ]
}
```

`kind` is one of `cli`, `ide`, `server`, `api`, `file`, `browser`. `state` is
`working` (it answered on the last refresh), `failing` (it was in use and did
not), `ready` (it looks set up but is not the one in use) or `missing`.
`active` comes from `usage["source"]`, which the collector records; a provider
that records none is taken to use the first source that looks set up. `choice:
false` means the options are not alternatives and none is chosen between.
`selected` is the user's choice (`"auto"` or a source id), stored in the settings
file as `sources: {provider: id}`.

A provider is made choosable by declaring `SOURCES` (a tuple of `sources.Source`,
each with a stat-only `probe`) in its module, listing it in
`sources._registry()`, and asking `sources.candidates(provider, default_order)`
which sources to try. A new kind of source, such as a browser session, is one more
`Source` and a fetch function; the settings page needs no change.

Settings also carry `proxy: {mode: "system" | "off" | "http", host, port}`,
applied by `config.apply_proxy` (see providers.md).
