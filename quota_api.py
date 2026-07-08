"""Shared quota API client for Claude, Codex, and Gemini."""
from __future__ import annotations

import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MAX_RESPONSE_BYTES = 1_048_576
HTTP_TIMEOUT = 8
GEMINI_PROBE_TIMEOUT = 5
MAX_RESET_EPOCH = 32_503_680_000.0  # 3000-01-01T00:00:00Z

logger = logging.getLogger(__name__)


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _non_empty_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def get_claude_token() -> str | None:
    creds_path = Path.home() / ".claude" / ".credentials.json"
    if not creds_path.exists():
        return None

    creds = _read_json_file(creds_path)
    claude_oauth = json_object(creds.get("claudeAiOauth") if creds else None)
    return _non_empty_str(claude_oauth.get("accessToken"))


def get_codex_token() -> str | None:
    auth_path = Path.home() / ".codex" / "auth.json"
    if not auth_path.exists():
        return None

    auth = _read_json_file(auth_path)
    tokens = json_object(auth.get("tokens") if auth else None)
    return _non_empty_str(tokens.get("access_token"))


def get_gemini_key() -> str | None:
    key = _non_empty_str(os.environ.get("GOOGLE_API_KEY"))
    if key:
        return key

    env_path = Path.home() / ".hermes" / ".env"
    if not env_path.exists():
        return None

    try:
        with env_path.open(encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.startswith("GOOGLE_API_KEY="):
                    return stripped.split("=", 1)[1].strip().strip("\"'") or None
    except OSError:
        return None
    return None


def fetch_json(req: urllib.request.Request) -> dict[str, Any]:
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=ctx) as resp:
        data = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("API response too large")

    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise ValueError("API response is not a JSON object")
    return payload


def json_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def json_number(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _clamp_pct(value: Any) -> float:
    return max(0.0, min(json_number(value), 100.0))


def _epoch_to_iso(epoch_seconds: Any) -> str:
    reset_at = json_number(epoch_seconds)
    if 0.0 < reset_at <= MAX_RESET_EPOCH:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(reset_at))
    return ""


def fetch_claude_quota() -> dict[str, Any] | None:
    token = get_claude_token()
    if not token:
        return None

    req = urllib.request.Request(
        "https://api.anthropic.com/api/oauth/usage",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = fetch_json(req)
    five_hour = json_object(data.get("five_hour"))
    seven_day = json_object(data.get("seven_day"))
    return {
        "session_pct": _clamp_pct(five_hour.get("utilization")),
        "session_reset": five_hour.get("resets_at") if isinstance(five_hour.get("resets_at"), str) else "",
        "weekly_pct": _clamp_pct(seven_day.get("utilization")),
        "weekly_reset": seven_day.get("resets_at") if isinstance(seven_day.get("resets_at"), str) else "",
    }


def fetch_codex_quota() -> dict[str, Any] | None:
    token = get_codex_token()
    if not token:
        return None

    req = urllib.request.Request(
        "https://chatgpt.com/backend-api/wham/usage",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = fetch_json(req)
    rate_limit = json_object(data.get("rate_limit"))
    primary_window = json_object(rate_limit.get("primary_window"))
    secondary_window = json_object(rate_limit.get("secondary_window"))
    return {
        "session_pct": _clamp_pct(primary_window.get("used_percent")),
        "session_reset": _epoch_to_iso(primary_window.get("reset_at")),
        "weekly_pct": _clamp_pct(secondary_window.get("used_percent")),
        "weekly_reset": _epoch_to_iso(secondary_window.get("reset_at")),
    }


def fetch_gemini_quota() -> dict[str, Any] | None:
    """Validate a Gemini API key and probe quota status.

    Gemini does not expose a public quota endpoint. This client validates the
    key with ``models.list`` and then sends a minimal ``generateContent`` probe.
    """
    key = get_gemini_key()
    if not key:
        return None

    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": key},
    )
    try:
        data = fetch_json(req)
    except Exception as exc:
        return {"error": str(exc), "available_models": [], "key_valid": False}

    models = []
    raw_models = data.get("models")
    if isinstance(raw_models, list):
        for model in raw_models:
            model_obj = json_object(model)
            name = model_obj.get("name")
            methods = model_obj.get("supportedGenerationMethods")
            if not isinstance(name, str) or not isinstance(methods, list):
                continue
            if "gemini" in name.lower() and "generateContent" in methods:
                models.append(name.replace("models/", "", 1))

    ctx = ssl.create_default_context()
    probe: dict[str, Any]
    try:
        probe_body = json.dumps({"contents": [{"parts": [{"text": "ok"}]}]}).encode("utf-8")
        probe_req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            data=probe_body,
            headers={
                "x-goog-api-key": key,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(probe_req, timeout=GEMINI_PROBE_TIMEOUT, context=ctx) as resp:
            headers = dict(resp.getheaders())
            probe = {
                "status": resp.status,
                "rate_limit_remaining": headers.get("x-ratelimit-remaining"),
                "rate_limit_reset": headers.get("x-ratelimit-reset"),
            }
            resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            probe = {"status": 429, "rate_limited": True}
        elif exc.code == 403:
            probe = {"status": 403, "error": "quota_exhausted or model not available"}
        else:
            probe = {"status": exc.code, "error": str(exc)}
    except Exception as exc:
        probe = {"status": "error", "error": str(exc)}

    return {
        "key_valid": True,
        "available_models": models,
        "model_count": len(models),
        "probe": probe,
    }


def fetch_all_quotas() -> dict[str, dict[str, Any] | None]:
    """Fetch all providers with per-provider error isolation."""
    result: dict[str, dict[str, Any] | None] = {}
    for name, fetcher in (
        ("claude", fetch_claude_quota),
        ("codex", fetch_codex_quota),
        ("gemini", fetch_gemini_quota),
    ):
        try:
            result[name] = fetcher()
        except Exception as exc:
            logger.debug("quota fetch failed for %s", name, exc_info=True)
            result[name] = {"error": str(exc)}
    return result
