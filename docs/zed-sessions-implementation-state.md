# Zed local sessions implementation state

**Scope:** implement the `Local session source / tool idea` as a read-only Sessions source on Linux, macOS, and Windows, then extend the existing Zed quota provider in `backend/tidesi/providers/zed.py` to read Linux credentials. Keep account quota and local token activity separate. Zed local token usage is not priced because the account API has no billed-consumption field and Zed's internal pricing/billing adjustments are unknown to tideSI.

**Working rule:** provider/model identifiers are read as data from Zed's saved model metadata. Do not enumerate known providers or models; catalog pricing must use exact identities if reliable per-model usage ever becomes available.

## Verified Zed storage facts

- Database table: `threads`.
- Stable required columns: `id`, `summary`, `updated_at`, `data_type`, `data`.
- Optional metadata columns include `parent_id`, `folder_paths`, `folder_paths_order`, and `created_at`; inspect the schema before querying them.
- `data_type` is `json` for legacy data and `zstd` for current compressed JSON.
- The thread blob stores `cumulative_token_usage`, `request_token_usage`, and one `model` object with `provider` and `model` identifiers. The current serialized `model` is not a per-request model ledger. Request usage has no trustworthy timestamps or model identity, so any cost computed from the saved thread model is an API-rate estimate, not exact historical attribution; Usage & Spend labels this caveat.
- Root threads can be selected using `parent_id` when present; the blob's `subagent_context` is a fallback filter.
- Zed does not document an exact-thread CLI resume command, so rows must stay display-only (`openKey` empty).

## Existing Zed quota provider

- Zed quota is a separate account API (`GET /client/users/me`) that reports plan/edit-prediction quota and subscription-period end, not local model-token spend.
- macOS credentials come from the login Keychain via `/usr/bin/security`. Linux credentials come from Secret Service via `secret-tool search --all --unlock url <server>`; use the entry whose `attribute.url` exactly matches the configured server, with `attribute.username` as user ID and `secret` as token. Secret Service may require an unlock prompt. Nix packages pin `secret-tool` to the libsecret store path.
- Windows quota credential access remains unsupported. The provider is manual-only; credential presence does not automatically enable it. The API flow has not been verified against a live account.

## Platform paths and override

| Platform | Default database |
| --- | --- |
| Linux | `$XDG_DATA_HOME/zed/threads/threads.db`, default `~/.local/share/zed/threads/threads.db` |
| macOS | `~/Library/Application Support/Zed/threads/threads.db` |
| Windows | `%LOCALAPPDATA%/Zed/threads/threads.db` |

`TIDESI_ZED_THREADS_DB` is the explicit database-file override for custom Zed user-data directories. Fingerprint the database and `-wal`, not `-shm`.

## Implementation checklist

- [x] Add a read-only cross-platform Zed SQLite reader with schema introspection, JSON/zstd decoding, malformed-row handling, root-thread filtering, safe workspace basenames, token totals, dynamic model identity, and no resume target.
- [x] Register source `zed` in the session collector, metadata manifest, and source descriptor registry; preserve the existing collector/manifest ordering invariant.
- [x] Preserve the aggregate input/output/cache token breakdown and dynamic saved provider/model identity. Keep local Zed session costs unavailable; never substitute the shared public API-rate catalog for Zed billing.
- [x] Decode Zed's unknown-content-size zstd frames within the existing 64 MiB safety bound; version the Zed session manifest so existing cached zero-token rows are reparsed after reader fixes.
- [x] Reconcile local sessions when Usage & Spend opens, then reload the usage envelope after the scan so Zed and other local usage appear without first opening Sessions.
- [x] Add Zed thread token rows to Usage & Spend under normal provider names without presenting local rate calculations as actual Zed spend.
- [x] Extend the existing Zed quota provider for Linux Secret Service credentials, retain macOS Keychain support, and document Windows as unsupported; keep quota separate from local model-token usage.
- [x] Add zstandard to desktop runtime, Windows/macOS frozen-build requirements, PyInstaller imports, Linux Nix runtime environments, and CI test setup.
- [x] Add synthetic SQLite tests for platform paths/override, JSON and zstd (including unknown content size and larger decode windows), optional schemas, WAL/read-only access, subagent filtering, malformed data, dynamic model IDs, token counts, privacy, and no-resume behavior.
- [x] Update source lists, provider documentation, runtime dependency claims, and this state file.
- [x] Run focused tests, full Python tests/lint, and available Linux package checks for the original local Sessions implementation; report platform builds that cannot be run locally.
- [x] Run focused validation for the quota-provider and token-only Usage & Spend follow-up edits below.
- [x] Live-smoke-test Zed quota credential lookup and account response on Linux; the account returned the Student plan and unlimited edit predictions.
- [ ] Live-smoke-test macOS quota credential lookup. Verify the Windows-only unsupported message in platform CI.

## Progress log

- 2026-10-28: Repository confirmed clean before implementation. Reviewed the session collector/index patterns, OpenCode's dynamic pricing path, Zed's current official `db.rs` and serialized thread types, and the cross-platform default storage locations.
- 2026-10-28: Implemented the read-only Zed source and cross-platform dependency/build wiring. Provider/model identities are taken from stored metadata rather than an enumerated model list. Sessions remain display-only, and pricing remains unavailable because Zed stores cumulative usage alongside only the currently selected model, not a per-request model ledger.
- Validation: `ruff check backend/tidesi hosts scripts tests/python` and `ruff format --check backend/tidesi hosts scripts tests/python` passed. The Nix Windows development-shell session test selection passed (155 tests). `nix flake check --no-build` passed with existing warnings. `nix build --builders "" .#default .#cli .#hyprland .#desktop --no-link` passed after temporarily marking the new reader intent-to-add so Nix's Git source filtering included it; the temporary index entry was removed afterward. Native macOS and Windows package builds were not run here.
- Full Python suite: 822 tests ran; one unrelated failure remains in `test_theme_switch.ThemeSwitchTests.test_live_theme_switch` (Fontconfig reported invalid configuration and the expected success text was absent). The Zed/session tests passed.
- Repository checks: `git diff --check` passed. All implementation changes remain unstaged; the reader, tests, and this state file remain untracked.
- 2026-10-28 follow-up: Extended the existing `zed` quota provider to use Linux Secret Service (`secret-tool`) while retaining macOS Keychain access; Windows quota credential access remains unsupported. Added token-only Zed local usage to Usage & Spend without estimating dollars from the saved thread model. Updated provider/setup/contract documentation and this state record.
- 2026-10-28 debugging fix: The local Zed DB was present, but all inspected thread rows used zstd frames with unknown content size and a 2 MiB window; the reader limited the decoder to 64 KiB, silently returning zero tokens. Raised the decoder's bounded window to 64 MiB, added a matching regression fixture, bumped the Zed manifest parser version to invalidate old cache rows, and made Usage & Spend reconcile sessions on entry. A read-only check then found 5 threads with positive token usage.
- Follow-up validation before provider rollups: Nix-shell Zed tests passed (13), including the zstd regression; session-manifest tests passed (13), session-index tests (18), and envelope tests (26). Spend-refresh and token-only JavaScript tests passed (17). `ruff check backend/tidesi hosts scripts tests/python`, `ruff format --check backend/tidesi hosts scripts tests/python`, and `git diff --check` passed. `nix flake check --no-build` and Linux Nix builds for `default`, `cli`, `hyprland`, and `desktop` passed with existing evaluation warnings. No live quota API or macOS/Windows runtime test was performed; those remain outstanding.
- 2026-10-28 provider/model breakdown correction: Usage & Spend first emitted separate token-only rows keyed by each Zed thread's saved model. Follow-up: resolve Zed-hosted model IDs through exact shared-catalog matches, preserve token classes, and price known models as API-equivalent estimates under the normal provider name; Zed-hosted estimates are plan-covered. Costs remain explicitly qualified because Zed does not persist per-request model/token pairs. The session-index schema fingerprint invalidates old cached rows after parser or pricing logic changes.
- Initial validation for the token-only split: 10 local-spend tests, 18 session-index tests, 27 Zed session tests, and 75 frontend tests passed; Ruff check/format and `git diff --check` passed. A sanitized read of the current Zed DB yielded saved-model identities `zed.dev/gpt-6-luna` and `zed.dev/gpt-5.6-terra`.
- Follow-up implementation now maps those IDs to the catalog provider via exact rate identity, calculates an API-equivalent estimate from the saved thread token breakdown, and groups the result under that provider. Zed-hosted models are categorized as subscription-covered; direct API models remain metered estimates. Unknown providers/models are not guessed and keep cost unavailable.
- 2026-10-28 final provider/model correction: cost rollups retain both the resolved provider and dynamic model ID, producing separate rows such as `OpenAI · gpt-6-luna` and `OpenAI · gpt-5.6-terra`. A read-only runtime check against the current local DB resolved its five saved threads under those two OpenAI model rollups. Zed-hosted costs remain API-rate estimates, marked as saved-thread-model attribution rather than exact per-request spend.
- Final focused validation: Zed reader (11), pricing (19), session-index (40), and token-only/frontend behavior (11) tests passed; Ruff check/format and `git diff --check` passed. Quickshell UI was not relaunched in this turn.
- 2026-10-28 pricing-source correction: verified live `/client/users/me` exposes model-request/edit-prediction counts but no billed USD usage. Removed public-catalog estimates from Zed local sessions and Usage; preserve token-only rows. The monetary graph remains unavailable except for account-reported values, with plan allowance derived separately from the account plan and Zed's published entitlement table. Zed's private hosted pricing and billing adjustments are not known to tideSI.
