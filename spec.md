---
name: "quota-status-bar-v2"
version: "1.0"
author: "adversarial-spec"
status: "draft"
tags: [adversarial, spec]
targets:
  - file: __init__.py
    description: "Create or update status bar rendering so configured provider segments use the v2 labels, countdown text, width limit, and suppression eligibility."
  - file: quota_api.py
    description: "Create or update provider quota checks for GLM, DeepSeek, and the Claude/Codex window data consumed by the renderer."
  - file: test_quota_status.py
    description: "Create or update automated tests for each provider, filtering, countdowns, width limiting, and authentication-failure suppression."
  - file: plugin.yaml
    description: "Update plugin metadata/configuration to document and support the v2 provider set and config-driven filtering."
---

# Quota Status Bar v2

## Problem
The Hermes quota status plugin is expected to report Claude, Codex, and Gemini quota in the Hermes TUI status bar, but the v2 workflow also needs quota visibility for GLM/Zhipu and DeepSeek and a fuller view of providers that expose multiple quota windows. The status bar also needs deterministic behavior in constrained terminal widths and when provider credentials are invalid, so that unavailable providers do not repeatedly consume status bar space or produce noisy failures.

This enhancement expands provider coverage to GLM/Zhipu and DeepSeek, disambiguates provider short codes by renaming Gemini from `G` to `Ge` and reserving `G` for GLM, exposes all relevant quota windows for Claude and Codex, allows users to select which providers appear, shows reset times as relative countdowns, trims output for explicitly narrow terminal contexts, and suppresses providers after repeated authentication failures while still allowing them to recover.

## Requirements
- R1: The plugin must include GLM/智谱 as a quota provider for the Z.AI Coding Plan.
- R2: The GLM provider must authenticate with the raw value from `GLM_API_KEY` or, when `GLM_API_KEY` is unset or empty, `ZHIPU_API_KEY` in the `Authorization` request header.
- R3: The GLM provider must retrieve quota limit data from the Z.AI/BigModel quota limit endpoint, trying `api.z.ai` first and falling back to `open.bigmodel.cn` only when the first host cannot produce a successful quota response.
- R4: The plugin must include DeepSeek as a pay-per-token balance provider.
- R5: The DeepSeek provider must authenticate with `DEEPSEEK_API_KEY` using bearer-token authentication.
- R6: The DeepSeek provider must retrieve balance data from `https://api.deepseek.com/user/balance`.
- R7: Gemini's status bar short code must be renamed from `G` to `Ge`.
- R8: GLM's status bar short code must be `G`.
- R9: Claude status output must show every available Claude quota window from the 5-hour session window and 7-day weekly window set, showing both in the same segment when both are available and showing the single available window when only one is available.
- R10: Codex status output must show every available Codex quota window from the primary and secondary window set, showing both in the same segment when both are available and showing the single available window when only one is available.
- R11: The plugin must support config-driven provider filtering through `quota_status.providers` in the Hermes `config.yaml`, using the case-sensitive provider names `claude`, `codex`, `gemini`, `glm`, and `deepseek`.
- R12: When provider filtering is configured, only the listed providers may appear in the status bar output.
- R13: Reset times shown in the status bar must use relative countdowns rather than absolute clock times.
- R14: Status bar output must be trimmed to a maximum of 60 characters when the renderer is told the terminal width is 60 columns or fewer.
- R15: A provider must be suppressed from status bar output after 3 consecutive authentication failures within the current plugin process; successful authenticated checks reset that provider's counter to zero, and non-authentication failures do not increment or reset the counter.
- R16: Authentication-failure suppression must be tracked independently per provider.
- R17: A provider suppressed because of authentication failures must continue to run background quota checks when credentials are configured, and must become eligible for display again after a subsequent successful authenticated quota check for that provider.
- R18: The v2 behavior must preserve Claude, Codex, and Gemini availability semantics: when each provider has credentials configured, is allowed by filtering, and has quota data available, it must produce one provider segment using its v2 short code and available quota values.
- R19: The plugin metadata must reflect the v2 provider set and configuration surface.
- R20: Automated tests must cover all new and changed observable behavior introduced by this specification with at least one passing automated test for every requirement id.

## Acceptance criteria
- AC1 (R1): With GLM credentials present and a successful GLM quota response, the status bar contains a GLM quota segment.
- AC2 (R2): When only one GLM credential variable is set, a GLM quota request is sent with an `Authorization` header equal to that raw credential value, without adding a bearer prefix.
- AC3 (R2): If both `GLM_API_KEY` and `ZHIPU_API_KEY` are absent, GLM is treated as unavailable and does not appear in successful status output.
- AC4 (R3): A GLM quota check targets `/api/monitor/usage/quota/limit` on `api.z.ai` before any request to `open.bigmodel.cn`.
- AC5 (R4): With DeepSeek credentials present and a successful DeepSeek balance response, the status bar contains a DeepSeek balance segment.
- AC6 (R5): A DeepSeek balance request is sent with an `Authorization` header using `Bearer ` followed by the configured `DEEPSEEK_API_KEY` value.
- AC7 (R5): If `DEEPSEEK_API_KEY` is absent, DeepSeek is treated as unavailable and does not appear in successful status output.
- AC8 (R6): A DeepSeek balance check targets `https://api.deepseek.com/user/balance`.
- AC9 (R7): Gemini status output uses the short code `Ge` and does not use the short code `G`.
- AC10 (R8): GLM status output uses the short code `G`.
- AC11 (R9): Given Claude quota data containing both 5-hour and 7-day windows, the rendered Claude segment includes both windows in the same status output.
- AC12 (R10): Given Codex quota data containing both primary and secondary windows, the rendered Codex segment includes both windows in the same status output.
- AC13 (R11): When Hermes config contains `quota_status.providers`, the plugin reads that list as the provider allowlist for status rendering.
- AC14 (R12): When `quota_status.providers` contains only `claude` and `deepseek`, status output includes Claude and DeepSeek when available and excludes Codex, Gemini, and GLM even when their credentials and quota data are available.
- AC15 (R13): A reset time 3 hours and 42 minutes in the future is rendered as a relative countdown containing `3h42m`, not as an absolute timestamp.
- AC16 (R13): Expired or current reset times are rendered as the relative countdown `0h0m` and without an absolute timestamp.
- AC17 (R14): In a narrow terminal context, the complete status bar string is no longer than 60 characters.
- AC18 (R14): When trimming is required, the output remains a single valid status string and does not contain partial control sequences or malformed separators.
- AC19 (R15): After the third consecutive authentication failure for a provider, that provider is omitted from status bar output.
- AC20 (R16): Three consecutive authentication failures for one provider do not suppress any other provider.
- AC21 (R17): After a suppressed provider later completes a successful authenticated quota check, the provider appears again in status output when otherwise allowed and available.
- AC22 (R18): Claude, Codex, and Gemini each render exactly one provider segment when their credentials are configured, they are allowed by filtering, and quota data is available; the Gemini segment uses `Ge`, and Claude and Codex include every available quota window required by R9 and R10.
- AC23 (R19): `plugin.yaml` documents Claude, Codex, Gemini, GLM, and DeepSeek as supported providers and exposes the `quota_status.providers` configuration key.
- AC24 (R20): The test suite includes at least one passing automated test that exercises each of these behaviors: GLM success, GLM credential priority, GLM host fallback, DeepSeek success, Gemini `Ge`, GLM `G`, Claude dual windows, Claude single-window rendering, Codex dual windows, Codex single-window rendering, provider filtering, relative future countdowns, expired/current `0h0m` countdowns, 60-character trimming at terminal widths of 60 columns or fewer, 3-failure auth suppression, non-auth failure handling, and suppression recovery through a background quota check.
- AC25 (R2): When both `GLM_API_KEY` and `ZHIPU_API_KEY` are set, a GLM quota request uses the raw `GLM_API_KEY` value in the `Authorization` header.
- AC26 (R3): If `api.z.ai` cannot produce a successful GLM quota response and `open.bigmodel.cn` can, GLM quota data from `open.bigmodel.cn` is used for rendering.
- AC27 (R9): Given Claude quota data containing only the 5-hour session window or only the 7-day weekly window, the rendered Claude segment includes the available window and does not include a placeholder for the missing window.
- AC28 (R10): Given Codex quota data containing only the primary window or only the secondary window, the rendered Codex segment includes the available window and does not include a placeholder for the missing window.
- AC29 (R11): Provider names in `quota_status.providers` are matched case-sensitively; an entry such as `GLM` does not enable the `glm` provider.
- AC30 (R14): When the renderer is told the terminal width is greater than 60 columns or no terminal width is available, the 60-character limit is not applied.
- AC31 (R15): A provider's authentication-failure counter starts at zero when the plugin process starts, increments only on authentication failures, is not changed by network errors, timeouts, or malformed responses, and resets to zero after a successful authenticated quota check.
- AC32 (R17): While a provider is suppressed and its credentials remain configured, the plugin still attempts that provider's quota check on subsequent refreshes even though the provider is omitted from status output until a successful authenticated check occurs.
