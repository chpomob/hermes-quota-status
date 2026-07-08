---
name: "hermes-quota-status-v2"
version: "2.0"
author: "adversarial-spec"
status: "draft"
targets:
  - file: ~/.hermes/plugins/hermes-quota-status/__init__.py
    description: "Add GLM + DeepSeek providers, rename Gemini G→Ge, improve Claude/Codex with weekly data, countdowns, config filtering, output trim, error suppression"
  - file: ~/.hermes/plugins/hermes-quota-status/quota_api.py
    description: "Add GLM quota fetch (quota/limit, model-usage, tool-usage) + DeepSeek balance fetch, refactor shared helpers"
  - file: ~/.hermes/plugins/hermes-quota-status/test_quota_status.py
    description: "Add tests for GLM, DeepSeek, weekly Claude/Codex, config filtering, countdowns, trimming, error suppression"
  - file: ~/.hermes/plugins/hermes-quota-status/plugin.yaml
    description: "Bump version to 2.0.0"
---

# Hermes Quota Status Bar — v2

## Problem

The existing Hermes quota-status plugin shows usage for Claude, Codex, and Gemini
in the Hermes TUI status bar, but has several gaps:

1. **GLM/智谱 missing** — user has a GLM Coding Plan (Z.AI) and uses GLM-5.2 daily
   via `GLM_API_KEY`. No GLM usage in the status bar.
2. **DeepSeek missing** — user pays per token for DeepSeek API. No balance or
   spend info displayed. DeepSeek has a balance endpoint `GET /user/balance`.
3. **Claude shows only 5h session %** — API returns both `five_hour` and
   `seven_day`, only `session_pct` rendered.
4. **Codex shows only primary window** — same issue, `secondary_window` ignored.
5. **Gemini short code `G` conflicts with GLM** — need `Ge` for Gemini, `G` for GLM.
6. **No per-provider enable/disable** — all providers always rendered even when
   tokens are expired.
7. **Reset countdown is absolute time** — `17h00` instead of relative `3h42m`.
8. **No output length management** — 4+ providers overflow the status bar.

## Requirements

### R1: GLM (Z.AI) provider
- Fetch quota from Z.AI monitoring API: `POST /api/monitor/usage/quota/limit`.
- Platforms: **Global** (`https://api.z.ai`) and **CN** (`https://open.bigmodel.cn`).
- Auth: `Authorization: <api-key>` (no "Bearer"). Key from `GLM_API_KEY` (Global)
  or `ZHIPU_API_KEY` (CN) env var / `~/.hermes/.env`.
- Fallback: model-usage endpoint if quota/limit fails.
- Render: `🟢/🟡/🔴 G:<used_pct>%` with group labels when multiple pools.
- Short code: `G` (Gemini moves to `Ge`).

### R2: DeepSeek (pay-per-token) provider
- Fetch balance from `GET https://api.deepseek.com/user/balance`.
- Auth: `Authorization: Bearer <DEEPSEEK_API_KEY>` (standard Bearer).
- Response: `{is_available, balance_infos: [{currency, total_balance, granted_balance, topped_up_balance}]}`.
- Render: `🟢 D:$10.50` showing total balance. No quota %, just $ remaining.
- Color thresholds: Green > $5.00, Yellow $1.00-$5.00, Red < $1.00.
- Show both USD and CNY balance if both present.
- Short code: `D`.

### R3: Rename Gemini short code
- Change `PROVIDER_SHORT["gemini"]` from `"G"` to `"Ge"`.
- All Gemini render paths updated.
- All tests updated.

### R4: Enhanced Claude rendering
- Parse and display both `five_hour` (session) and `seven_day` (weekly).
- Primary shows worst (highest %) of the two.
- When both used significantly: `🟡 C:80% (5h) / 55% (7d)`.

### R5: Enhanced Codex rendering
- Same pattern — both `primary_window` and `secondary_window`.
- Worst % primary, detail both on divergence.

### R6: Config-driven provider filtering
- Read `quota_status.providers` from Hermes `config.yaml`.
- Default: `["claude", "codex", "gemini", "glm", "deepseek"]`.
- Filter both render and fetch.

### R7: Better reset countdowns
- Relative countdowns: `3h42m` instead of absolute `17h00`.
- Absolute time as secondary hint: `3h42m (17:00)`.

### R8: Error resilience
- HTTP 401/403 → auth status.
- 3+ consecutive auth errors → suppress output entirely.
- Auto-retry after 300s.

### R9: Output length management
- When plugin output > 60 chars, show only worst N providers (sorted by usage %
  descending). Always show at least 1.
- DeepSeek balance sorts last (not a usage %).

## Acceptance criteria

1. **AC1 (GLM fetch):** `_fetch_glm()` returns `ProviderQuota` with groups on valid key. None when missing. Tested.
2. **AC2 (GLM render):** `_render_provider("glm", ...)` outputs `🟢 G:30%`, `🔴 G:AUTH`. Tested.
3. **AC3 (DeepSeek fetch):** `_fetch_deepseek()` returns balance info. None when key missing. Tested.
4. **AC4 (DeepSeek render):** `_render_provider("deepseek", ...)` outputs `🟢 D:$10.50`, `🔴 D:$0.50`. Tested.
5. **AC5 (Gemini rename):** `PROVIDER_SHORT["gemini"]` is `"Ge"`. All renders use `Ge`. Tested.
6. **AC6 (Claude weekly):** `_fetch_claude()` returns both windows. Render shows both. Tested.
7. **AC7 (Codex weekly):** Same. Tested.
8. **AC8 (Config filtering):** `quota_status.providers: ["glm","codex"]` only shows those. Tested.
9. **AC9 (Countdown):** Relative format `2h30m` instead of absolute. Tested.
10. **AC10 (60-char trim):** 5 providers trimmed to fit. Tested.
11. **AC11 (Auth suppression):** 3 auth failures suppress output. Tested.
12. **AC12 (Backward compat):** Existing Claude/Codex/Gemini fetch paths unchanged. All existing tests pass.
