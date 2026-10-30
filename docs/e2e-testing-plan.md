# End-to-End Testing Plan

**Status:** In progress — hermetic harness, two provider paths, and an HTTP error matrix implemented; remaining roadmap pending
**Last implementation review:** 2026-10-28

This document tracks the work needed to add deterministic, useful end-to-end (E2E) coverage without discarding the focused tests that already protect provider and feature edge cases. Update the checkboxes and provider matrix as implementation lands; link each completed item to its test file or CI job.

## Status key

- `[x]` Implemented and validated in the current worktree.
- `[ ]` Missing, incomplete, or not yet audited.
- A provider marked **Audit** is not a claim that it has no tests. It means no full-pipeline E2E test has yet been mapped to it in this plan.

## Baseline: what exists today

| Area | Verified current coverage | Remaining gap |
|---|---|---|
| Test infrastructure | `[x]` Python `unittest`, Node tests, shell tests, fixture data, and discovery-based CI exist. The hermetic harness tests are in `tests/python/test_e2e_harness.py`; `make test-e2e` runs only E2E modules. | `[ ]` Core cross-feature workflows and populated UI coverage remain incomplete. |
| Provider tests | `[x]` Every one of the 25 providers has at least one hermetic CLI-to-envelope E2E across loopback HTTP, local files, SQLite, response fixtures, or a fake CLI. Every selectable source mode also has an explicit E2E. | `[ ]` Some non-selectable provider-specific modes and cross-feature workflows remain. |
| Desktop Qt/QML | `[x]` `tests/python/test_windows_app.py` loads the real app headlessly with providers disabled and checks for load warnings. | `[ ]` It does not exercise populated provider data and UI interactions through a real backend. |
| Process-based QML | `[x]` `tests/python/test_command_backend.py` runs real `CommandBackend.qml` and `AppState.qml` with backend tools. | `[ ]` Extend seeded-data coverage where valuable; do not rebuild this harness unnecessarily. |
| QML behavior | `[x]` `tests/python/test_theme_switch.py` exercises real QML behavior. | `[ ]` `tests/python/test_qml_backend_tokens.py` uses regex/source inspection for the session-text safety property; runtime delegate coverage remains a candidate improvement. |
| Source-scraping tests | `[x]` Several Node tests extract QML function text and execute it in a VM. | `[ ]` Replace only after equivalent real-QML coverage exists. These tests currently cover behavior and should not be deleted as a bulk cleanup. |
| Core workflows | `[x]` Detection, pricing, sessions, history, and CLI behavior have focused test modules. | `[ ]` A small number of multi-step workflows should verify that the pieces still compose. |
| CI | `[x]` Windows workflow installs PySide6 and requires Qt tests on Ubuntu and Windows. Portable test discovery also runs in Linux and macOS jobs; the new E2E test names are discovered automatically. | `[ ]` UI E2E is not implemented; keep it optional outside Qt-enabled jobs. |

### Scope and boundary

The initial target is **hermetic E2E**: run the real application/backend path while replacing external services at the network or executable boundary with controlled local substitutes. This validates our integration and data flow without depending on live vendors. It does not prove that a vendor’s current live API still matches its documentation; that is a separate, optional canary.

Keep the existing unit and component tests. E2E tests complement them; they are not a reason to delete useful edge-case coverage or make every test launch Qt.

## Roadmap and progress

### Phase 0 — Complete the coverage inventory

- [ ] Audit the current provider tests and fixtures; record which tests exercise credentials, collection, normalization, envelopes, subprocesses, SQLite, and file discovery.
- [ ] Record which tests actually reach `urllib` and which use response-file shortcuts or mocks.
- [ ] Map the relevant CLI commands and settings/XDG paths used by test subprocesses.
- [ ] Identify platform-specific paths and opt-in modes (for example, macOS-only integrations and optional quota collection).
- [ ] Update the provider matrix below with test references and any justified exclusions.

**Exit criteria:** Every provider has a documented I/O path, existing focused-test references, and a proposed E2E case or an explicit reason it is unsuitable for the hermetic suite.

### Phase 1 — Build and verify a hermetic harness

Implementation: `tests/python/_e2e_harness.py`, with the child audit policy in `_e2e_guard.py` and bounded process capture/cleanup in `_e2e_process.py`.

- [x] Provide isolated temporary `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and `XDG_CACHE_HOME` directories in child processes; keep the parent test environment unchanged.
- [x] Provide a `ThreadingHTTPServer` bound to `127.0.0.1` on an ephemeral port. It records method, target, headers, and body, and supports status responses, malformed bodies, and a dropped connection.
- [x] Add a narrow test transport seam: `fetch_json()` and Muse's remote direct-`urlopen()` request route through `route_e2e_url()` when explicit `TIDESI_E2E_HTTP_BASE` is set. Existing loopback URLs remain unchanged. Antigravity's loopback-only language-server path has a seeded local-service E2E in `tests/python/test_e2e_source_modes_a.py`.
- [x] Guard Python child processes against accidental non-loopback DNS/socket access (including UDP `sendmsg`) and untrusted native executable/credential-store launches. Bootstrap failure is fatal; persisted violations fail the parent even if application code catches the denial. Tests also reject unsafe Python startup options and critical environment mutations.
- [x] Provide isolated child environments and a fake-CLI helper with POSIX executable and Windows wrapper support. Invocation has been verified on POSIX; the wrapper still needs Windows CI validation.
- [x] Clean up accepted sockets, server workers, and temporary directories. Bound subprocess execution, output draining, and direct-child reaping; terminate ordinary descendants on success and timeout with POSIX process groups or a Windows Job Object.
- [x] Add harness tests for loopback HTTP, exact request capture, connection drop, fake executable invocation, UTF-8 decoding, stalled server workers, process cleanup, fail-closed startup, and swallowed isolation violations.

**Acceptance criteria:** `[x]` The harness sends real `urllib` requests over a local TCP socket, asserts captured requests, and verifies guarded Python I/O cannot silently contact non-loopback services. Backend E2E tests use only standard-library dependencies.

**Isolation limits:** This is a Python-level accidental-I/O guard, **not an OS security sandbox**. Trusted native fake executables, native extensions, inherited sockets, deliberate guard tampering, and deliberate process-group escapes need separate OS isolation. AF_UNIX and loopback services are allowed, so the harness does not isolate every host-local service. Windows Job Object assignment occurs after process creation, leaving a small early-descendant race; assignment failures are fatal rather than falling back to an uncontained run. Linux runtime behavior is validated locally; Windows/macOS runtime behavior remains unverified until those CI jobs run. In particular, fake CLI wrappers and Windows Job Object handling need native Windows validation.

### Phase 2 — Provider pipeline E2E

Implemented across `tests/python/test_e2e_providers.py` and focused `tests/python/test_e2e_*providers.py` modules, split by I/O strategy and source mode.

Exercise the real backend/CLI pipeline where applicable:

`isolated credentials or local data → provider discovery/collector → HTTP, subprocess, SQLite, or files → normalizer → envelope/CLI output`

- [x] Add representative end-to-end cases for subprocess adapters, SQLite/file-ledger readers, and local-model discovery. Provider/source references are in the coverage matrices below.
- [x] Verify request method, target path, relevant headers, and body for the two initial loopback HTTP cases; broaden method/query coverage as providers are added.
- [x] Verify provider subprocess behavior through fake CLI boundaries. CodeRabbit asserts the exact `usage` argument; Copilot CLI verifies its seeded token reaches the loopback quota request.
- [x] Verify the final envelope contract for provider/source E2Es, not only intermediate provider dictionaries.
- [x] Add provider error-matrix cases for `401`, `403`, `429`, `500`, malformed JSON, and offline/connection errors. `DeepSeekProviderE2ETest` verifies each reaches the final envelope; provider-specific wording is asserted by contract suffix.
- [ ] Verify one provider failure does not abort a multi-provider run.
- [ ] Verify `--all --stream` emits partial records followed by a complete envelope.
- [x] Check sentinel secrets in DeepSeek and Muse CLI output and generated cache/output directories while excluding credential inputs; the Muse case also requires its quota cache to be written. Expand coverage as providers/workflows are added.
- [x] Check local-model tests never issue generation/completion requests; Selfhosted asserts only the loopback version and model-list probes.
- [x] Keep optional and platform-specific provider paths explicitly marked; Antigravity server process discovery and Zed Keychain limitations are documented in the coverage matrix.

**Provider coverage matrix — full-pipeline E2E status**

For each row, fill in **I/O path**, **test reference**, **CI/platform**, and status as it is implemented. Existing unit tests do not count as full-pipeline E2E evidence by themselves. `Initial E2E` means one real pipeline case exists; provider modes and resilience are not necessarily complete.

| Provider | Status | E2E test / notes |
|---|---|---|
| `claude` | Initial E2E | `tests/python/test_e2e_http_providers.py::HttpProviderE2ETest.test_claude_oauth_token_reaches_usage_api_and_envelope` — isolated OAuth credential → loopback GET → envelope. |
| `antigravity` | Initial E2E | `tests/python/test_e2e_uncovered_providers.py::RemainingProviderE2ETest.test_antigravity_fake_agy_report_reaches_envelope` — fake `agy --usage` response → normalized quota; all selectable source modes are listed below. |
| `openai` | Initial E2E | Organization usage: `tests/python/test_e2e_http_providers.py::HttpProviderE2ETest.test_openai_organization_key_reaches_usage_api_and_envelope`. Codex plan: `tests/python/test_e2e_source_modes_c.py::OpenAICodexModeE2ETest.test_codex_login_and_rate_limits_reach_openai_envelope`. |
| `kiro` | Initial E2E | CLI login → regional HTTP: `tests/python/test_e2e_http_providers.py::HttpProviderE2ETest.test_kiro_cli_login_reaches_usage_limits_and_envelope`; IDE snapshot mode is listed below. |
| `mistral` | Initial E2E | `tests/python/test_e2e_http_providers.py::HttpProviderE2ETest.test_mistral_api_key_reaches_models_api_and_envelope` — API key → loopback models request → envelope. |
| `openrouter` | Initial E2E | `tests/python/test_e2e_http_providers.py::HttpProviderE2ETest.test_openrouter_key_and_balance_requests_reach_envelope` — validates both key and credits requests. |
| `ollama` | Initial E2E | API-key account balance: `tests/python/test_e2e_http_providers.py::HttpProviderE2ETest.test_ollama_cloud_key_reaches_balance_api_and_envelope`; OpenCode-stored login mode is listed below. |
| `selfhosted` | Initial E2E | `tests/python/test_e2e_uncovered_providers.py::RemainingProviderE2ETest.test_selfhosted_ollama_probe_uses_loopback_and_no_generation` — loopback Ollama version/model probes; no generation request. |
| `grok` | Initial E2E | `tests/python/test_e2e_http_providers.py::HttpProviderE2ETest.test_grok_oauth_token_reaches_billing_api_and_envelope` — isolated OAuth data and loopback billing flow. |
| `zai` | Initial E2E | `tests/python/test_e2e_http_providers.py::HttpProviderE2ETest.test_zai_token_reaches_quota_api_and_envelope` — token → loopback quota request → envelope. |
| `copilot` | Initial E2E | API token: `tests/python/test_e2e_http_providers.py::HttpProviderE2ETest.test_copilot_token_reaches_internal_quota_api_and_envelope`; editor and GitHub CLI login modes are listed below. |
| `deepseek` | Initial E2E | `tests/python/test_e2e_providers.py::DeepSeekProviderE2ETest` — credential file → real GET → envelope; covers HTTP errors, malformed JSON, and dropped connections. |
| `kimi` | Initial E2E | `tests/python/test_e2e_http_providers.py::HttpProviderE2ETest.test_kimi_api_key_reaches_moonshot_balance_and_envelope` — key → loopback balance request → envelope. |
| `muse` | Initial E2E | `tests/python/test_e2e_providers.py::MuseQuotaProviderE2ETest` — opt-in direct POST → SSE quota → envelope; default local-stats mode pending. |
| `cursor` | Initial E2E | cursor-agent login → dashboard RPC: `tests/python/test_e2e_http_providers.py::HttpProviderE2ETest.test_cursor_agent_login_reaches_dashboard_and_envelope`; IDE database mode is listed below. |
| `cline` | Initial E2E | `tests/python/test_e2e_local_providers.py::LocalProviderE2ETest.test_cline_session_file_reaches_cli_envelope` — isolated session record → envelope; status feed is seeded in cache. |
| `opencode` | Initial E2E | `tests/python/test_e2e_uncovered_providers.py::RemainingProviderE2ETest.test_opencode_sqlite_session_reaches_cli_envelope` — isolated SQLite ledger → local usage envelope; Go account mode pending. |
| `mimo` | Initial E2E | `tests/python/test_e2e_storage_providers.py::StorageProviderE2ETest.test_mimo_database_reaches_cli_envelope` — isolated SQLite ledger → envelope. |
| `junie` | Initial E2E | `tests/python/test_e2e_local_providers.py::LocalProviderE2ETest.test_junie_event_log_reaches_cli_envelope` — isolated event ledger → envelope. |
| `jetbrains` | Initial E2E | `tests/python/test_e2e_local_providers.py::LocalProviderE2ETest.test_jetbrains_quota_file_reaches_cli_envelope` — isolated quota XML → envelope. |
| `windsurf` | Initial E2E | `tests/python/test_e2e_storage_providers.py::StorageProviderE2ETest.test_windsurf_state_database_reaches_cli_envelope` — isolated editor state DB → envelope. |
| `pi` | Initial E2E | `tests/python/test_e2e_local_providers.py::LocalProviderE2ETest.test_pi_session_file_reaches_cli_envelope` — isolated session file → envelope. |
| `kilo` | Initial E2E | API key → loopback tRPC: `tests/python/test_e2e_http_providers.py::HttpProviderE2ETest.test_kilo_api_key_reaches_trpc_and_envelope`; CLI login mode is listed below. |
| `coderabbit` | Initial E2E | `tests/python/test_e2e_local_providers.py::LocalProviderE2ETest.test_coderabbit_report_cli_is_invoked_and_normalized` — fake executable argv/output → envelope. |
| `zed` | Initial E2E | `tests/python/test_e2e_storage_providers.py::StorageProviderE2ETest.test_zed_response_fixture_reaches_cli_envelope` — explicit response-file boundary → envelope; native macOS Keychain path pending. |

**Exit criteria:** All 25 providers have either a passing E2E case for their supported I/O path(s), or a documented platform/opt-in exception with existing focused tests retained. Avoid one enormous test that makes failures hard to localize.

### Selectable provider source-mode E2E coverage

Every source ID declared in `tidesi.sources._registry()` has an E2E that selects that source explicitly and verifies it became active. This covers all 14 selectable modes across the six multi-source providers. Antigravity's `server` mode scans the process table; it skips on platforms without `/proc` when optional `psutil` is not installed. Its mode runs through the real loopback API when process discovery is available.

| Provider | Source | E2E reference |
|---|---|---|
| `antigravity` | `aiu` | `test_e2e_source_modes_a.py::ProviderSourceModeE2ETest.test_antigravity_aiu_cli_reaches_envelope` |
| `antigravity` | `server` | `test_e2e_source_modes_a.py::ProviderSourceModeE2ETest.test_antigravity_server_reaches_envelope_over_loopback` |
| `antigravity` | `agy` | `test_e2e_uncovered_providers.py::RemainingProviderE2ETest.test_antigravity_fake_agy_report_reaches_envelope` |
| `cursor` | `cli` | `test_e2e_http_providers.py::HttpProviderE2ETest.test_cursor_agent_login_reaches_dashboard_and_envelope` |
| `cursor` | `ide` | `test_e2e_source_modes_b.py::SourceModesE2ETest.test_cursor_ide_database_credential_reaches_request_and_envelope` |
| `kiro` | `cli` | `test_e2e_http_providers.py::HttpProviderE2ETest.test_kiro_cli_login_reaches_usage_limits_and_envelope` |
| `kiro` | `ide` | `test_e2e_source_modes_b.py::SourceModesE2ETest.test_kiro_ide_snapshot_reaches_envelope` |
| `copilot` | `key` | `test_e2e_http_providers.py::HttpProviderE2ETest.test_copilot_token_reaches_internal_quota_api_and_envelope` |
| `copilot` | `editor` | `test_e2e_source_modes_b.py::SourceModesE2ETest.test_copilot_editor_login_reaches_request_and_envelope` |
| `copilot` | `cli` | `test_e2e_source_modes_b.py::SourceModesE2ETest.test_copilot_cli_gh_token_reaches_request_and_envelope` |
| `kilo` | `key` | `test_e2e_http_providers.py::HttpProviderE2ETest.test_kilo_api_key_reaches_trpc_and_envelope` |
| `kilo` | `cli` | `test_e2e_source_modes_a.py::ProviderSourceModeE2ETest.test_kilo_cli_login_reaches_envelope` |
| `ollama` | `key` | `test_e2e_http_providers.py::HttpProviderE2ETest.test_ollama_cloud_key_reaches_balance_api_and_envelope` |
| `ollama` | `opencode` | `test_e2e_source_modes_a.py::ProviderSourceModeE2ETest.test_ollama_opencode_login_reaches_envelope` |

The OpenAI Codex plan path is not a selectable `sources._registry()` entry, but has its own CLI E2E in `test_e2e_source_modes_c.py::OpenAICodexModeE2ETest.test_codex_login_and_rate_limits_reach_openai_envelope`; it is tested separately from organization API usage.

### Phase 3 — Core feature workflow E2E

Planned location: `tests/python/test_e2e_features.py`.

Prioritize workflows that connect already-tested features; do not duplicate every unit assertion.

- [ ] **Detection and defaults:** fake executables on `PATH` → detection CLI → defaults initialization → persisted settings/latch behavior.
- [ ] **Pricing and sessions:** loopback pricing response → refresh/cache → seeded session scan → query/source filter/pagination → local spend result → open-session command sent to a fake terminal.
- [ ] **History:** save a snapshot and load it back; verify persistence through the actual CLI/file boundary where stable.
- [ ] **CLI rendering:** pipe a real backend envelope into `tidesi-cli`; verify exit status and meaningful table/error output.
- [ ] Reuse current feature-focused tests for detailed edge cases; use E2E assertions for cross-feature contracts and data flow.

**Exit criteria:** At least one deterministic end-to-end test passes for each workflow selected for the initial release, on supported CI platforms, without contacting public services.

### Phase 4 — Populated Qt/QML UI E2E

Planned location: `tests/python/test_e2e_ui.py` or focused additions to the existing Qt test modules.

- [ ] Run Qt tests in a subprocess with an offscreen/software backend and collect QML warnings as test failures.
- [ ] Use the real desktop `Backend` and shared QML with seeded provider/session data; do not use screenshot demo data as a substitute for backend integration.
- [ ] Exercise a small representative set of visible states: Overview, Spend, Sessions, one healthy provider, and one error provider.
- [ ] Verify a real interaction (such as session search/source filter or provider navigation) changes the displayed state.
- [ ] Verify a settings change is persisted and reflected in the UI.
- [ ] Where the relevant delegate is instantiated, assert untrusted session text is rendered as plain text.
- [ ] Extend `test_command_backend.py` only for process-host behavior that its current real-QML harness does not already cover.
- [ ] Keep Qt tests skippable when PySide6 is absent; require them in the existing Qt-enabled Windows workflow rather than making portable backend jobs depend on Qt.

**Exit criteria:** The test reaches the real UI and backend, renders seeded data, performs at least one user-like interaction, checks the resulting state, and fails on QML warnings.

### Phase 5 — CI, developer workflow, and test consolidation

- [x] Add `make test-e2e` to run only the E2E modules locally.
- [x] Keep test names compatible with existing `unittest discover -s tests/python` so CI includes portable E2E tests automatically.
- [ ] Run Qt UI E2E only where PySide6 is installed; preserve `REQUIRE_FRONTEND_QT=1` enforcement in the Qt job.
- [x] Bound execution, descendant cleanup, and server waits for the initial backend E2E slice. Preserve these constraints as providers/workflows are added; cross-platform runtime validation is still pending.
- [ ] After replacements are green, review source-scraping tests one by one. Remove or simplify only tests whose behavior is now covered at least as well elsewhere.
- [ ] Do not consolidate provider/session edge-case tests solely to reduce the test-file count.

**Exit criteria:** `make test-e2e`, `make test`, `make lint-py`, and the applicable CI workflows pass. Failures identify the provider/workflow and assertion without exposing secrets.

### Deferred — Live provider canary

- [ ] Consider a separate manually triggered/scheduled workflow only after the hermetic suite is stable and there is a clear need to detect upstream schema drift.
- [ ] Keep it separate from PR tests, explicitly document possible cost/rate limits, use read-only requests, and never expose repository secrets to untrusted pull requests.
- [ ] Treat public status/pricing feeds as potentially flaky; avoid making their availability a merge requirement.

## Latest review validation (Linux, 2026-10-28)

- `make test-e2e`: 26 tests passed, including guard/bootstrap regressions, exact provider request counts, error envelopes, and generated-cache secret checks.
- Cleanup stress check: 30 tests passed (10 repetitions each of normal-exit descendants, timeout descendants, and incomplete HTTP requests).
- `make test-py`: 813 tests run, 41 skipped, passing on the final run. An earlier full run hit the unchanged `test_codex_stats.py::CodexStatsCacheTest::test_malformed_cache_recovers` timestamp flake (recomputed timestamps differed by one second); the module and full suite both passed on rerun.
- `make lint-py` and `git diff --check`: passed.
- `node --test tests/*.test.js`: all 214 tests passed. Python-interpreter, history-I/O, and translation-provider-parity shell tests also passed.
- **Full `make test` is not green:** apart from the earlier timestamp flake, separately running `tests/test_install_script.sh` reproduces an unrelated fixture failure in this Nix environment: the fake package omits `contents/backend/sh/python-interp.sh`, which the installer tries to patch. No installer or Codex changes were made as part of this review.
- Windows/macOS runtime validation and the remaining roadmap phases are still pending; a passing Linux backend slice does not imply complete application E2E coverage.

## Completion checklist

- [ ] Harness's Python-level loopback/launch guard and cleanup pass on all supported CI platforms; stronger native/OS isolation is explicitly out of scope.
- [ ] Provider matrix is complete, with justified exceptions documented.
- [ ] Core feature workflows cover selected cross-component paths without replacing useful unit coverage.
- [ ] UI E2E exercises populated real UI/backend state and at least one interaction.
- [ ] Secret scanning excludes test inputs and checks generated outputs safely.
- [ ] Local command and CI coverage are documented and passing.
- [ ] Any tests removed during consolidation have explicit replacement evidence.
