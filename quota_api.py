"""Shared quota API client for Claude, Codex, Gemini, GLM, and DeepSeek."""
from __future__ import annotations

import json
import logging
import math
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
GEMINI_AUTH_HTTP_STATUSES = frozenset({400, 401, 403})
MAX_RESET_EPOCH = 32_503_680_000.0  # 3000-01-01T00:00:00Z
GLM_QUOTA_PATH = "/api/monitor/usage/quota/limit"
GLM_QUOTA_HOSTS = ("https://api.z.ai", "https://open.bigmodel.cn")
DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"
GLM_AVAILABLE_PCT_FIELDS = (
    "availableLimitPercentage",
    "available_limit_percentage",
    "availableLimitPercent",
    "available_limit_percent",
    "availablePercentage",
    "available_percentage",
    "availablePercent",
    "remainingPercentage",
    "remaining_percent",
)
GLM_USED_PCT_FIELDS = (
    "percentage",
    "usedPercentage",
    "used_percentage",
    "usedPercent",
    "used_percent",
    "usagePercentage",
    "usage_percentage",
    "usagePercent",
    "usage_percent",
    "utilization",
)
GLM_REMAINING_FRACTION_FIELDS = (
    "remainingFraction",
    "remaining_fraction",
    "availableFraction",
    "available_fraction",
)
GLM_BALANCE_FIELDS = (
    "balance",
    "remainingBalance",
    "remaining_balance",
    "availableBalance",
    "available_balance",
    "credits",
    "credit",
)
GLM_RESET_FIELDS = (
    "nextResetTime",
    "next_reset_time",
    "resetTime",
    "reset_time",
    "resetAt",
    "reset_at",
    "resetsAt",
    "resets_at",
    "periodEnd",
    "period_end",
    "endTime",
    "end_time",
)
GLM_QUOTA_VALUE_FIELDS = (
    *GLM_AVAILABLE_PCT_FIELDS,
    *GLM_USED_PCT_FIELDS,
    *GLM_REMAINING_FRACTION_FIELDS,
    *GLM_BALANCE_FIELDS,
)

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


def get_glm_key() -> str | None:
    key = _non_empty_str(os.environ.get("GLM_API_KEY"))
    if key:
        return key
    return _non_empty_str(os.environ.get("ZHIPU_API_KEY"))


def get_deepseek_key() -> str | None:
    return _non_empty_str(os.environ.get("DEEPSEEK_API_KEY"))


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
    number = json_number_or_none(value)
    return 0.0 if number is None else number


def json_number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, int | float | str):
            number = float(value)
        else:
            return None
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _format_money_amount(value: float) -> str:
    return f"{value:.2f}"


def _json_nested_number_and_display(value: Any) -> tuple[float, str] | None:
    number = json_number_or_none(value)
    if number is not None:
        display = value if isinstance(value, str) and value else _format_money_amount(number)
        return number, display
    if isinstance(value, dict):
        for key in ("value", "amount", "balance", "total"):
            parsed = _json_nested_number_and_display(value.get(key))
            if parsed is not None:
                return parsed
    return None


def _json_nested_number_or_none(value: Any) -> float | None:
    parsed = _json_nested_number_and_display(value)
    return parsed[0] if parsed is not None else None


def _clamp_pct(value: Any) -> float:
    return max(0.0, min(json_number(value), 100.0))


def _clamp_fraction(value: Any) -> float | None:
    number = json_number_or_none(value)
    if number is None:
        return None
    return max(0.0, min(number, 1.0))


def _percent_or_none(value: Any) -> float | None:
    number = json_number_or_none(value)
    if number is None:
        return None
    return max(0.0, min(number, 100.0))


def _epoch_to_iso(epoch_seconds: Any) -> str:
    reset_at = json_number_or_none(epoch_seconds)
    if reset_at is None:
        return ""
    if reset_at > MAX_RESET_EPOCH and 0.0 < reset_at / 1000.0 <= MAX_RESET_EPOCH:
        reset_at = reset_at / 1000.0
    if 0.0 < reset_at <= MAX_RESET_EPOCH:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(reset_at))
    return ""


def _normalize_key(key: str) -> str:
    return "".join(ch for ch in key.lower() if ch.isalnum())


def _walk_json(value: Any) -> list[dict[str, Any]]:
    pending = [value]
    objects: list[dict[str, Any]] = []
    while pending:
        current = pending.pop(0)
        if isinstance(current, dict):
            objects.append(current)
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return objects


def _find_direct_value(data: dict[str, Any], names: tuple[str, ...]) -> Any:
    wanted = {_normalize_key(name) for name in names}
    for key, value in data.items():
        if isinstance(key, str) and _normalize_key(key) in wanted:
            return value
    return None


def _has_direct_field(data: dict[str, Any], names: tuple[str, ...]) -> bool:
    wanted = {_normalize_key(name) for name in names}
    return any(isinstance(key, str) and _normalize_key(key) in wanted for key in data)


def _glm_quota_object_rank(data: dict[str, Any]) -> tuple[int, int] | None:
    if not _has_direct_field(data, GLM_QUOTA_VALUE_FIELDS):
        return None

    limit_type = data.get("type")
    is_token_limit = isinstance(limit_type, str) and limit_type.upper() == "TOKENS_LIMIT"
    is_non_token_limit = isinstance(limit_type, str) and limit_type.upper() != "TOKENS_LIMIT"
    unit = json_number_or_none(data.get("unit"))

    if is_token_limit and unit == 3:
        return (0, 0)
    if is_token_limit and unit == 6:
        return (1, 0)
    if is_token_limit:
        return (2, 0)
    if is_non_token_limit:
        return (4, 0)
    return (3, 0)


def _select_glm_quota_object(data: dict[str, Any]) -> dict[str, Any]:
    ranked: list[tuple[tuple[int, int], dict[str, Any]]] = []
    for index, obj in enumerate(_walk_json(data)):
        rank = _glm_quota_object_rank(obj)
        if rank is not None:
            ranked.append(((rank[0], index), obj))
    if not ranked:
        raise ValueError("GLM quota response did not include quota fields")
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def _string_or_epoch_iso(value: Any) -> str:
    if isinstance(value, str):
        return value
    return _epoch_to_iso(value)


def _normalize_glm_quota(data: dict[str, Any], host: str) -> dict[str, Any]:
    quota = _select_glm_quota_object(data)
    available_pct = _percent_or_none(_find_direct_value(quota, GLM_AVAILABLE_PCT_FIELDS))
    used_pct = _percent_or_none(_find_direct_value(quota, GLM_USED_PCT_FIELDS))
    remaining_fraction = _clamp_fraction(_find_direct_value(quota, GLM_REMAINING_FRACTION_FIELDS))
    balance = json_number_or_none(_find_direct_value(quota, GLM_BALANCE_FIELDS))
    reset = _string_or_epoch_iso(_find_direct_value(quota, GLM_RESET_FIELDS))

    if available_pct is None and used_pct is None and remaining_fraction is None and balance is None:
        raise ValueError("GLM quota response did not include quota fields")

    if used_pct is None:
        if available_pct is not None:
            used_pct = 100.0 - available_pct
        elif remaining_fraction is not None:
            used_pct = 100.0 - (remaining_fraction * 100.0)

    result: dict[str, Any] = {
        "host": host,
        "session_reset": reset,
        "reset_iso": reset,
    }
    if used_pct is not None:
        result["session_pct"] = _clamp_pct(used_pct)
    if available_pct is not None:
        result["available_limit_pct"] = available_pct
    if remaining_fraction is not None:
        result["remaining_fraction"] = remaining_fraction
    if balance is not None:
        result["balance"] = balance
    return result


def _normalize_deepseek_balance(data: dict[str, Any]) -> dict[str, Any]:
    raw_balance_infos = data.get("balance_infos")
    if not isinstance(raw_balance_infos, list):
        raise ValueError("DeepSeek balance response did not include balance_infos")

    balances: list[dict[str, Any]] = []
    for raw_entry in raw_balance_infos:
        entry = json_object(raw_entry)
        if not entry:
            continue

        total_balance = _json_nested_number_and_display(entry.get("total_balance"))
        granted_balance = _json_nested_number_and_display(entry.get("granted_balance"))
        topped_up_balance = _json_nested_number_and_display(entry.get("topped_up_balance"))
        if total_balance is None and granted_balance is None and topped_up_balance is None:
            continue

        currency = entry.get("currency")
        balance: dict[str, Any] = {
            "currency": currency if isinstance(currency, str) else "",
        }
        for result_key, display_key, amount in (
            ("total_balance", "total_balance_display", total_balance),
            ("granted_balance", "granted_balance_display", granted_balance),
            ("topped_up_balance", "topped_up_balance_display", topped_up_balance),
        ):
            if amount is not None:
                value, display = amount
                balance[result_key] = value
                balance[display_key] = display if display else _format_money_amount(value)
        balances.append(balance)

    if not balances:
        raise ValueError("DeepSeek balance response did not include parseable balances")

    preferred = next(
        (
            balance
            for balance in balances
            if balance.get("currency") == "USD" and isinstance(balance.get("total_balance"), int | float)
        ),
        None,
    )
    if preferred is None:
        preferred = next(
            (balance for balance in balances if isinstance(balance.get("total_balance"), int | float)),
            None,
        )
    if preferred is None:
        preferred = next((balance for balance in balances if balance.get("currency") == "USD"), balances[0])
    is_available = data.get("is_available")
    result: dict[str, Any] = {
        "is_available": is_available if isinstance(is_available, bool) else False,
        "currency": preferred.get("currency", ""),
        "balances": balances,
    }
    if isinstance(preferred.get("total_balance"), int | float):
        result["balance"] = preferred["total_balance"]
        result["total_balance"] = preferred["total_balance"]
    if isinstance(preferred.get("granted_balance"), int | float):
        result["granted_balance"] = preferred["granted_balance"]
    if isinstance(preferred.get("topped_up_balance"), int | float):
        result["topped_up_balance"] = preferred["topped_up_balance"]
    for display_key in ("total_balance_display", "granted_balance_display", "topped_up_balance_display"):
        if isinstance(preferred.get(display_key), str):
            result[display_key] = preferred[display_key]
    return result


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
    result: dict[str, Any] = {}

    session_pct = _percent_or_none(five_hour.get("utilization"))
    if session_pct is not None:
        result["session_pct"] = session_pct
        result["session_reset"] = five_hour.get("resets_at") if isinstance(five_hour.get("resets_at"), str) else ""

    weekly_pct = _percent_or_none(seven_day.get("utilization"))
    if weekly_pct is not None:
        result["weekly_pct"] = weekly_pct
        result["weekly_reset"] = seven_day.get("resets_at") if isinstance(seven_day.get("resets_at"), str) else ""

    return result


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
    result: dict[str, Any] = {}

    primary_pct = _percent_or_none(primary_window.get("used_percent"))
    if primary_pct is not None:
        result["session_pct"] = primary_pct
        result["session_reset"] = _epoch_to_iso(primary_window.get("reset_at"))

    secondary_pct = _percent_or_none(secondary_window.get("used_percent"))
    if secondary_pct is not None:
        result["weekly_pct"] = secondary_pct
        result["weekly_reset"] = _epoch_to_iso(secondary_window.get("reset_at"))

    return result


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
    except urllib.error.HTTPError as exc:
        # Only responses that reject the request credentials establish that the
        # key is invalid. Transport errors and upstream 5xx responses must
        # propagate so callers can retry them without recording an auth failure.
        if exc.code not in GEMINI_AUTH_HTTP_STATUSES:
            raise
        return {
            "error": str(exc),
            "auth_error": True,
            "http_status": exc.code,
            "available_models": [],
            "key_valid": False,
        }

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


def fetch_glm_quota() -> dict[str, Any] | None:
    key = get_glm_key()
    if not key:
        return None

    last_error: Exception | None = None
    for host in GLM_QUOTA_HOSTS:
        req = urllib.request.Request(
            f"{host}{GLM_QUOTA_PATH}",
            headers={"Authorization": key},
        )
        try:
            return _normalize_glm_quota(fetch_json(req), host)
        except (urllib.error.HTTPError, OSError, TimeoutError, ValueError) as exc:
            # R3 requires the secondary host whenever the primary cannot
            # produce a successful quota response, including authentication
            # failures and malformed responses. Unexpected programming errors
            # are allowed to surface immediately instead of being masked by a
            # successful fallback request.
            last_error = exc

    # Authentication accounting happens once for the overall provider check,
    # not once per host. The final host is authoritative because a credential
    # can be valid for open.bigmodel.cn even when api.z.ai rejects it. Raising
    # its failure also avoids classifying a secondary availability incident as
    # an authentication failure based on an earlier 401 or ambiguous 403.
    if last_error is not None:
        raise last_error
    return None


def fetch_deepseek_balance() -> dict[str, Any] | None:
    key = get_deepseek_key()
    if not key:
        return None

    req = urllib.request.Request(
        DEEPSEEK_BALANCE_URL,
        headers={"Authorization": f"Bearer {key}"},
    )
    return _normalize_deepseek_balance(fetch_json(req))


def fetch_all_quotas() -> dict[str, dict[str, Any] | None]:
    """Fetch all providers with per-provider error isolation."""
    result: dict[str, dict[str, Any] | None] = {}
    for name, fetcher in (
        ("claude", fetch_claude_quota),
        ("codex", fetch_codex_quota),
        ("gemini", fetch_gemini_quota),
        ("glm", fetch_glm_quota),
        ("deepseek", fetch_deepseek_balance),
    ):
        try:
            result[name] = fetcher()
        except Exception as exc:
            logger.debug("quota fetch failed for %s", name, exc_info=True)
            result[name] = {"error": str(exc)}
    return result
