# hermes-quota-status

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that displays real-time API quota usage for AI providers directly in the Hermes status bar.

## Features

- **Multi-provider support** — Claude, Codex, Gemini, GLM/Zhipu, and DeepSeek
- **Real-time quota** — reads from each provider's native API/CLI auth tokens
- **Multiple windows** — Claude shows 5-hour session + 7-day windows; Codex shows primary + secondary
- **Relative countdowns** — reset times shown as `3h42m` / `0h0m`
- **Configurable filtering** — choose which providers appear via `quota_status.providers`
- **Narrow-width trimming** — auto-trims to 60 characters on constrained terminals
- **Auth-failure suppression** — providers with 3 consecutive auth failures are hidden until a successful check recovers them
- **Background refresh** — non-blocking network fetches so the TUI never stalls

## Supported providers

| Provider | Short code | Auth method |
|----------|-----------|-------------|
| Claude | `C` | OAuth token from `~/.claude/.credentials.json` |
| Codex | `Cx` | OAuth token from `~/.codex/auth.json` |
| Gemini | `Ge` | Google Cloud Code OAuth or agy keyring token |
| GLM/Zhipu | `G` | `GLM_API_KEY` or `ZHIPU_API_KEY` env var |
| DeepSeek | `Ds` | `DEEPSEEK_API_KEY` env var |

## Installation

Clone into your Hermes plugins directory:

```bash
git clone https://github.com/chpomob/hermes-quota-status.git \
  ~/.hermes/plugins/hermes-quota-status
```

The plugin activates automatically on the next Hermes start. Add the
`on_status_bar_render` hook to your Hermes config if it is not already enabled.

### Prerequisites

The plugin reads OAuth tokens from the respective CLI tools. Make sure you
have authenticated with each provider you want to monitor:

- **Claude**: `claude` CLI must be logged in
- **Codex**: `codex` CLI must be logged in (`~/.codex/auth.json`)
- **Gemini**: requires `gemini-cli` Cloud Code OAuth or agy keyring setup
- **GLM**: set `GLM_API_KEY` (or `ZHIPU_API_KEY`) in your environment
- **DeepSeek**: set `DEEPSEEK_API_KEY` in your environment

## Configuration

In your Hermes `config.yaml`, optionally restrict which providers appear:

```yaml
quota_status:
  providers: [claude, codex, gemini]
```

Valid values (case-sensitive): `claude`, `codex`, `gemini`, `glm`, `deepseek`.
When unset, all available providers are shown.

## License

MIT
