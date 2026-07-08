---
spec: "quota-status-bar-v2"
version: "1.0"
author: "adversarial-plan"
based-on: "adversarial-spec"
findings-input: true
---

# Implementation Plan

## Steps

### P1: Expand provider identity and cache state
- **Files:** [__init__.py, test_quota_status.py]
- **Description:** Extend `ProviderName`, `PROVIDERS`, `_PROVIDERS`, `PROVIDER_SHORT`, `CacheState`, `_cache`, retry bookkeeping, and public imports for the v2 provider set. Rename Gemini's short code from `G` to `Ge`, reserve `G` for GLM, add a DeepSeek short code, and keep the rendered provider order deterministic as `claude`, `codex`, `gemini`, `glm`, `deepseek`. Update existing Gemini render expectations so the old `G:` code is no longer accepted. Covers R7, R8, and the provider identity portions of R18.
- **Dependencies:** []
- **Tests:** Update existing render and status-bar tests to expect `Ge:` for Gemini, add an assertion that GLM's short code is `G`, and add a provider order/cache initialization test that includes all five v2 providers.
- **Risks:** Type aliases, cache keys, and mocked provider dictionaries must be updated together or refresh/render tests will fail with missing keys.

### P2: Add GLM quota API client
- **Files:** [quota_api.py, test_quota_status.py]
- **Description:** Add `get_glm_key()` that returns the raw `GLM_API_KEY` value when set and otherwise the raw `ZHIPU_API_KEY` value, treating unset or empty strings as unavailable. Add `fetch_glm_quota()` to request `/api/monitor/usage/quota/limit` with `Authorization` equal to the raw credential, trying `https://api.z.ai` first and falling back to `https://open.bigmodel.cn` only after the first host cannot return a successful quota response. Normalize the successful response into the shared quota shape consumed by rendering, including available limit percentage, remaining fraction or balance fields when present, and reset time data when present. Covers R1, R2, R3, and the API-client portion of R15/R31 for authentication errors.
- **Dependencies:** [P1]
- **Tests:** Add API-level tests for GLM success, missing credentials, `GLM_API_KEY` priority over `ZHIPU_API_KEY`, fallback to `ZHIPU_API_KEY` when `GLM_API_KEY` is empty or unset, raw non-bearer `Authorization`, first request to `api.z.ai`, fallback to `open.bigmodel.cn`, and rendering data sourced from the fallback host.
- **Risks:** The external quota response shape may vary; normalization should defensively parse dictionaries and numbers without masking HTTP 401/403 errors needed by auth suppression.

### P3: Add DeepSeek balance API client
- **Files:** [quota_api.py, test_quota_status.py]
- **Description:** Add `get_deepseek_key()` that reads `DEEPSEEK_API_KEY` and returns `None` when unset or empty. Add `fetch_deepseek_balance()` to request `https://api.deepseek.com/user/balance` with `Authorization: Bearer <DEEPSEEK_API_KEY>`, parse successful pay-per-token balance data into a stable shared shape, and return `None` when credentials are unavailable. Covers R4, R5, R6, and the API-client portion of R15/R31 for authentication errors.
- **Dependencies:** [P1]
- **Tests:** Add API-level tests for DeepSeek success, missing credentials, exact bearer authorization header, exact balance endpoint URL, and defensive parsing of balance amounts and currency fields.
- **Risks:** Balance responses can contain nested or string numeric values; parsing must preserve useful display data while allowing malformed authenticated responses to be handled as non-auth failures.

### P4: Preserve all Claude and Codex quota windows
- **Files:** [quota_api.py, __init__.py, test_quota_status.py]
- **Description:** In `quota_api.py`, keep `fetch_claude_quota()` and `fetch_codex_quota()` returning every available window from the upstream data. In `__init__.py`, update the status refresh wrappers `_fetch_claude()` and `_fetch_codex()` so they pass through 5-hour/session plus 7-day/weekly windows for Claude and primary plus secondary windows for Codex. Represent missing windows by omission or empty values rather than placeholders, so rendering can show one or two windows based only on available data. Covers R9, R10, and the Claude/Codex portions of R18.
- **Dependencies:** [P1]
- **Tests:** Add tests for Claude dual windows, Claude 5-hour-only rendering data, Claude 7-day-only rendering data, Codex dual windows, Codex primary-only rendering data, and Codex secondary-only rendering data.
- **Risks:** Existing tests currently assume a single `session_pct` and `reset_iso`; the compatibility layer must not break old single-window data while adding multi-window support.

### P5: Render v2 provider segments and countdowns
- **Files:** [__init__.py, test_quota_status.py]
- **Description:** Update `_render_provider()` and supporting helpers to render exactly one segment per available provider, using v2 short codes and provider-specific values. Render Claude and Codex with all available quota windows in the same segment, GLM as a quota segment, DeepSeek as a balance segment, Gemini as `Ge`, and reset times as relative countdowns like `3h42m` with current or expired resets rendered as `0h0m`. Covers R1, R4, R7, R8, R9, R10, R13, and R18.
- **Dependencies:** [P2, P3, P4]
- **Tests:** Add render tests for GLM success, DeepSeek success, Gemini `Ge` without standalone `G`, GLM `G`, Claude dual and single windows, Codex dual and single windows, a future reset exactly 3 hours 42 minutes ahead, and current or expired reset values producing `0h0m` without absolute timestamps.
- **Risks:** Segment text must remain concise enough for later width trimming while still exposing both windows; tests should assert stable substrings instead of depending on wall-clock seconds.

### P6: Implement config-driven provider filtering
- **Files:** [__init__.py, test_quota_status.py]
- **Description:** Read `quota_status.providers` from the Hermes render context or config snapshot when available, interpret it as a case-sensitive allowlist containing only `claude`, `codex`, `gemini`, `glm`, and `deepseek`, and apply the allowlist to status bar rendering. Leave background refresh capable of checking configured credentialed providers so auth suppression recovery can occur even when a provider is currently omitted for display. Covers R11 and R12.
- **Dependencies:** [P5]
- **Tests:** Add tests that `quota_status.providers` is read, `["claude", "deepseek"]` renders only Claude and DeepSeek when all five providers have data, an entry such as `GLM` does not enable `glm`, and no configured allowlist preserves default rendering for available providers.
- **Risks:** Hermes may pass config through different hook argument shapes; the parser should be tolerant of missing context while keeping provider matching case-sensitive.

### P7: Enforce narrow-width trimming
- **Files:** [__init__.py, test_quota_status.py]
- **Description:** Add terminal-width handling to the renderer so the complete status string is trimmed to at most 60 characters only when the renderer is explicitly told the terminal width is 60 columns or fewer. Trim at whole provider-segment boundaries, preserving valid separators and avoiding partial control sequences; do not apply the 60-character limit when terminal width is greater than 60 or unavailable. Covers R14.
- **Dependencies:** [P5, P6]
- **Tests:** Add tests for width 60 producing a string of length 60 or less, trimming that leaves a single well-formed status string with no malformed separators, width 61 not applying the 60-character cap, and missing width not applying the cap.
- **Risks:** Existing callers may call `on_status_bar_render()` with no arguments; the signature must remain backward compatible while accepting width data from Hermes when present.

### P8: Track and recover authentication-failure suppression
- **Files:** [__init__.py, test_quota_status.py]
- **Description:** Add per-provider consecutive authentication-failure counters initialized to zero at process load. Increment only on 401/403-style authentication failures, leave counters unchanged for network errors, timeouts, malformed responses, and missing credentials, reset a provider's counter after a successful authenticated quota check, suppress a provider from status output after the third consecutive auth failure, and continue scheduling background checks for suppressed providers with configured credentials so a later success makes the provider eligible for display again. Covers R15, R16, R17, R31, and R32.
- **Dependencies:** [P5]
- **Tests:** Add tests for suppression after the third consecutive auth failure, one provider's failures not suppressing another provider, non-auth failures leaving counters unchanged, counters starting at zero on module load, success resetting the counter, suppressed providers still being fetched on later refreshes, and suppression recovery causing the provider to render again.
- **Risks:** Suppression must affect rendering only; if it prevents refresh attempts, recovery cannot happen and R17/R32 will fail.

### P9: Update plugin metadata for v2 configuration
- **Files:** [plugin.yaml, test_quota_status.py]
- **Description:** Update plugin metadata to describe Claude, Codex, Gemini, GLM/Zhipu, and DeepSeek support, document the `quota_status.providers` configuration key and valid case-sensitive provider names, and keep the status bar hook declaration intact. Covers R19.
- **Dependencies:** [P6]
- **Tests:** Add a metadata test that reads `plugin.yaml` and asserts the supported provider names and `quota_status.providers` configuration surface are documented.
- **Risks:** `plugin.yaml` schema should stay compatible with Hermes plugin loading; avoid introducing unsupported top-level fields unless existing metadata patterns support them.

### P10: Complete acceptance coverage and regression run
- **Files:** [test_quota_status.py]
- **Description:** Audit the test suite against AC1 through AC32 and add any missing focused tests so every requirement id has at least one passing automated test. Keep tests deterministic by mocking time, credentials, HTTP responses, and background refresh state. Covers R20 and validates the combined behavior from P1 through P9.
- **Dependencies:** [P1, P2, P3, P4, P5, P6, P7, P8, P9]
- **Tests:** Run `python -m unittest test_quota_status.py` from the plugin directory and ensure the full suite passes.
- **Risks:** Broad acceptance tests can become brittle if they duplicate implementation details; keep each assertion tied to observable behavior from the specification.

## Ordering rationale

P1 establishes the shared provider names, short codes, and cache shape needed by all later work. P2 and P3 then add the new provider API clients independently, while P4 expands the existing Claude and Codex data passed to rendering. P5 can render the complete v2 segment set only after those data shapes exist. P6 filters the rendered provider set after segment rendering is stable, and P7 trims the final composed status string after filtering decides which segments are present. P8 depends on the provider refresh and render paths from P5 so suppression can affect display without relying on config-driven allowlists or blocking background recovery. P9 documents the completed provider/config surface, and P10 performs the final requirement-to-test audit after all behavior-specific steps have landed.
