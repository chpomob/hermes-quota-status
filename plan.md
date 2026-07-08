---
spec: "hermes-quota-status-v2"
version: "2.0"
author: "adversarial-plan"
based-on: "adversarial-spec"
findings-input: false
---

## Steps

### P1: Gemini rename + short codes
- **Files:** ~/.hermes/plugins/hermes-quota-status/__init__.py, test_quota_status.py
- **Description:** Change `PROVIDER_SHORT["gemini"]` from `"G"` to `"Ge"`. Add `"glm": "G"` and `"deepseek": "D"` to `PROVIDER_SHORT`. Add `"glm"` and `"deepseek"` to the `PROVIDERS` tuple. Update `_cache` init keys. Update all tests that check for `G:` prefix in render output to expect `Ge:`.
- **Dependencies:** []
- **Tests:** All existing tests updated to expect new short codes.

### P2: GLM API client — quota/limit endpoint
- **Files:** ~/.hermes/plugins/hermes-quota-status/quota_api.py
- **Description:** Add `get_glm_key()` (reads `GLM_API_KEY` then `ZHIPU_API_KEY` from env vars, with `~/.hermes/.env` fallback), `_glm_base_url()` (returns `https://api.z.ai` for Global, `https://open.bigmodel.cn` for CN), and `fetch_glm_quota()` calling `POST /api/monitor/usage/quota/limit` with no-Bearer auth header. Returns dict with `{plan_type, groups: [{label, used_pct, remaining, reset}], remaining_fraction}` or None. Also add `fetch_glm_model_usage()` as fallback.
- **Dependencies:** [P1]
- **Tests:** Mock HTTP test for GLM quota response. Test key discovery (GLM_API_KEY wins, ZHIPU_API_KEY fallback, .env fallback). Test both platforms.

### P3: DeepSeek API client — balance endpoint
- **Files:** ~/.hermes/plugins/hermes-quota-status/quota_api.py
- **Description:** Add `get_deepseek_key()` (reads `DEEPSEEK_API_KEY` from env var or `~/.hermes/.env`). Add `fetch_deepseek_balance()` calling `GET https://api.deepseek.com/user/balance` with standard `Authorization: Bearer <key>` header. Returns dict with `{is_available, currency, total_balance}` or None.
- **Dependencies:** [P1]
- **Tests:** Mock HTTP test for balance response. Test key discovery.

### P4: GLM fetch integration in __init__.py
- **Files:** ~/.hermes/plugins/hermes-quota-status/__init__.py
- **Description:** Create `_fetch_glm()` that calls `fetch_glm_quota()` then falls back to `fetch_glm_model_usage()`. Add GLM render logic in `_render_provider` dispatch. Wire into `_PROVIDERS` dict. Add to `_PROVIDERS` dict referencing `_fetch_glm`.
- **Dependencies:** [P2]
- **Tests:** Test `_fetch_glm()` with mock quotas. Test render states (0%, 50%, 80%, 100%, auth error, missing key).

### P5: DeepSeek fetch integration in __init__.py
- **Files:** ~/.hermes/plugins/hermes-quota-status/__init__.py
- **Description:** Create `_fetch_deepseek()` calling `fetch_deepseek_balance()`. Add DeepSeek render logic: show balance as `$X.XX` with color by amount. Wire into `_PROVIDERS` dict. Sort DeepSeek last in output (not a usage %).
- **Dependencies:** [P3]
- **Tests:** Test `_fetch_deepseek()` with mock balance. Test render with $0.50, $3.00, $10.00, $100.00. Test missing key.

### P6: Enhanced Claude (weekly) and Codex (secondary) rendering
- **Files:** ~/.hermes/plugins/hermes-quota-status/__init__.py, quota_api.py
- **Description:** Modify `_fetch_claude()` to pass through `weekly_pct` and `weekly_reset` from the API. Same for `_fetch_codex()` with `secondary_window`. Update `_render_provider` for claude/codex to show worst % plus detail when both windows are used.
- **Dependencies:** [P1]
- **Tests:** Test both-window render format for Claude and Codex. Test with None windows.

### P7: Config-driven provider filtering
- **Files:** ~/.hermes/plugins/hermes-quota-status/__init__.py
- **Description:** On first `on_status_bar_render()`, read `quota_status.providers` from `snapshot.get("config", {}).get("quota_status", {}).get("providers")`. Fall back to reading `~/.hermes/config.yaml` directly if snapshot doesn't have config. Filter `PROVIDERS` list at render and fetch time. Document in plugin.yaml.
- **Dependencies:** [P4, P5]
- **Tests:** Mock config with subset. Test only selected providers fetched and rendered.

### P8: Better reset countdowns + output length management
- **Files:** ~/.hermes/plugins/hermes-quota-status/__init__.py
- **Description:** Replace `_fmt_reset()` with `_fmt_countdown()` (relative `3h42m`). Add `_trim_providers(parts, max_chars=60)` that sorts by usage % (DeepSeek last) and keeps worst N fitting in 60 chars, minimum 1. Apply in `on_status_bar_render()`.
- **Dependencies:** [P4, P5, P6]
- **Tests:** Countdown format. 60-char trim with 5 providers at various levels.

### P9: Error suppression after 3 consecutive auth failures
- **Files:** ~/.hermes/plugins/hermes-quota-status/__init__.py
- **Description:** Add `_consecutive_auth_errors: dict[ProviderName, int]` and `_suppressed: set[ProviderName]`. Increment on auth error, clear on success. At >= 3, suppress render output (return `""`).
- **Dependencies:** [P4, P5]
- **Tests:** 3 auth errors → suppressed. Successful fetch → restored.

### P10: Update tests + run
- **Files:** ~/.hermes/plugins/hermes-quota-status/test_quota_status.py
- **Description:** Add test classes for all new features. Ensure all existing tests still pass.
- **Dependencies:** [P1 through P9]
- **Tests:** `python3 -m pytest test_quota_status.py -v` must pass.

### P11: Version bump
- **Files:** ~/.hermes/plugins/hermes-quota-status/plugin.yaml
- **Description:** Bump to `"2.0.0"`. Update description. Add config reference for `quota_status.providers` and `quota_status.max_chars`.
- **Dependencies:** [P10]
- **Tests:** N/A.
