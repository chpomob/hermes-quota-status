"""
Hermes Quota Status Plugin - displays Claude, Codex, Gemini, GLM, and DeepSeek quota status.

Uses tokens stored by each CLI/API integration to query quota APIs directly.
Network refreshes run in a background thread so status bar rendering never
blocks the TUI redraw path.
"""
from __future__ import annotations

import json
import logging
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Literal, NotRequired, TypeAlias, TypedDict, cast

try:
    from . import gemini_cloudcode
    from . import agy_quota
    from .quota_api import (
        fetch_claude_quota,
        fetch_codex_quota,
        fetch_gemini_quota,
        get_claude_token,
        get_codex_token,
        get_gemini_key,
        json_number,
        json_number_or_none,
        json_object,
    )
except ImportError:
    # Unit tests and direct file execution load this module outside the Hermes
    # package namespace; fall back to importing the sibling module by path.
    plugin_dir = Path(__file__).resolve().parent
    if str(plugin_dir) not in sys.path:
        sys.path.insert(0, str(plugin_dir))
    import gemini_cloudcode  # type: ignore[no-redef]
    import agy_quota  # type: ignore[no-redef]
    from quota_api import (  # type: ignore[no-redef]
        fetch_claude_quota,
        fetch_codex_quota,
        fetch_gemini_quota,
        get_claude_token,
        get_codex_token,
        get_gemini_key,
        json_number,
        json_number_or_none,
        json_object,
    )

ProviderName: TypeAlias = Literal["claude", "codex", "gemini", "glm", "deepseek"]
QuotaWindowKey: TypeAlias = Literal["session", "weekly"]

PROVIDERS: Final[tuple[ProviderName, ...]] = ("claude", "codex", "gemini", "glm", "deepseek")
PROVIDER_SHORT: Final[dict[ProviderName, str]] = {
    "claude": "C",
    "codex": "Cx",
    "gemini": "Ge",
    "glm": "G",
    "deepseek": "D",
}
THRESHOLD_WARN: Final[float] = 50.0
THRESHOLD_CRITICAL: Final[float] = 80.0
THRESHOLD_FULL: Final[float] = 100.0
CACHE_TTL: Final[int] = 120
ERROR_RETRY_TTL: Final[int] = 30
AUTH_RETRY_TTL: Final[int] = 300

logger = logging.getLogger(__name__)

__all__ = [
    "ProviderName",
    "PROVIDERS",
    "PROVIDER_SHORT",
    "fetch_claude_quota",
    "fetch_codex_quota",
    "fetch_gemini_quota",
    "cloudcode_login",
    "cloudcode_login_pending",
    "get_claude_token",
    "get_codex_token",
    "get_gemini_key",
    "on_status_bar_render",
    "register",
]

_cloudcode_login_thread: threading.Thread | None = None
_cloudcode_login_lock = threading.Lock()


def _run_cloudcode_login() -> None:
    global _cloudcode_login_thread
    try:
        gemini_cloudcode.cloudcode_login()
    finally:
        with _cloudcode_login_lock:
            _cloudcode_login_thread = None


def cloudcode_login() -> bool:
    """Start Cloud Code OAuth login without blocking the Hermes TUI thread."""
    global _cloudcode_login_thread
    with _cloudcode_login_lock:
        if _cloudcode_login_thread is not None and _cloudcode_login_thread.is_alive():
            return True
        thread = threading.Thread(target=_run_cloudcode_login, name="hermes-cloudcode-login", daemon=True)
        _cloudcode_login_thread = thread
        thread.start()
        return True


def cloudcode_login_pending() -> bool:
    """Return True if a Cloud Code OAuth login is in progress."""
    global _cloudcode_login_thread
    with _cloudcode_login_lock:
        return _cloudcode_login_thread is not None and _cloudcode_login_thread.is_alive()


class GroupInfo(TypedDict, total=False):
    label: str
    remaining: float
    used_pct: int
    reset: str
    model_count: int


class QuotaWindowInfo(TypedDict, total=False):
    name: str
    label: str
    pct: float
    reset_iso: str


class QuotaWindowSpec(TypedDict):
    key: QuotaWindowKey
    pct_key: str
    reset_key: str
    name: str
    label: str


class ProviderQuota(TypedDict, total=False):
    session_pct: float
    reset_iso: str
    session_reset: str
    weekly_pct: float
    weekly_reset: str
    windows: list[QuotaWindowInfo]
    cloudcode: bool
    cloudcode_error: str
    agy_scrape: bool
    remaining_fraction: float
    prompt_credits: float
    monthly_prompt_credits: float
    plan_type: str
    key_valid: bool
    available_models: list[str]
    model_count: int
    probe: dict[str, Any] | None
    error: str
    groups: list[GroupInfo]


class CacheState(TypedDict):
    claude: ProviderQuota | None
    codex: ProviderQuota | None
    gemini: ProviderQuota | None
    glm: ProviderQuota | None
    deepseek: ProviderQuota | None
    ts: float
    refreshing: bool
    stale: set[ProviderName]
    next_retry: dict[ProviderName, float]


class ProviderError(TypedDict):
    provider: ProviderName | Literal["render"]
    type: str
    timestamp: float
    http_status: NotRequired[int]


_cache: CacheState = {
    "claude": None,
    "codex": None,
    "gemini": None,
    "glm": None,
    "deepseek": None,
    "ts": 0.0,
    "refreshing": False,
    "stale": set(),
    "next_retry": {provider: 0.0 for provider in PROVIDERS},
}
_cache_lock = threading.Lock()
_last_errors: list[ProviderError] = []

QUOTA_WINDOW_SPECS: Final[dict[ProviderName, tuple[QuotaWindowSpec, ...]]] = {
    "claude": (
        {"key": "session", "pct_key": "session_pct", "reset_key": "session_reset", "name": "five_hour", "label": "5h"},
        {"key": "weekly", "pct_key": "weekly_pct", "reset_key": "weekly_reset", "name": "seven_day", "label": "7d"},
    ),
    "codex": (
        {"key": "session", "pct_key": "session_pct", "reset_key": "session_reset", "name": "primary", "label": "P"},
        {"key": "weekly", "pct_key": "weekly_pct", "reset_key": "weekly_reset", "name": "secondary", "label": "S"},
    ),
}


def _quota_pct(value: Any) -> float:
    return max(0.0, min(json_number(value), 100.0))


def _quota_pct_or_none(value: Any) -> float | None:
    number = json_number_or_none(value)
    if number is None:
        return None
    return max(0.0, min(number, 100.0))


def _quota_reset(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _make_quota_window(
    data: dict[str, Any],
    pct_key: str,
    reset_key: str,
    name: str,
    label: str,
) -> QuotaWindowInfo | None:
    pct = _quota_pct_or_none(data.get(pct_key))
    if pct is None:
        return None
    return {
        "name": name,
        "label": label,
        "pct": pct,
        "reset_iso": _quota_reset(data.get(reset_key)),
    }


def _shared_window_quota(data: dict[str, Any], window_specs: tuple[QuotaWindowSpec, ...]) -> ProviderQuota:
    result: dict[str, Any] = {}
    windows: list[QuotaWindowInfo] = []

    for spec in window_specs:
        window = _make_quota_window(data, spec["pct_key"], spec["reset_key"], spec["name"], spec["label"])
        if window is None:
            continue
        windows.append(window)
        result[spec["pct_key"]] = window["pct"]
        result[spec["reset_key"]] = window.get("reset_iso", "")
        if spec["key"] == "session":
            result["reset_iso"] = window.get("reset_iso", "")

    if windows:
        result["windows"] = windows
        result.setdefault("reset_iso", "")
    return cast(ProviderQuota, result)


def _fetch_claude() -> ProviderQuota | None:
    data = fetch_claude_quota()
    if data is None:
        return None
    return _shared_window_quota(data, QUOTA_WINDOW_SPECS["claude"])


def _fetch_codex() -> ProviderQuota | None:
    data = fetch_codex_quota()
    if data is None:
        return None
    return _shared_window_quota(data, QUOTA_WINDOW_SPECS["codex"])


def _short_model_name(model_id: str) -> str:
    """Short label from Cloud Code model ID.

    Produces clean short names like '2.5F' (gemini-2.5-flash),
    '3F' (gemini-3-flash), '2.5FT' (gemini-2.5-flash-thinking).
    """
    name = model_id.replace("gemini-", "").replace("-preview", "")
    if name.startswith("chat_") or name.startswith("tab_"):
        return name.split("_")[0]
    parts = name.split("-")
    if not parts:
        return name[:6]
    version = parts[0].lstrip("g")
    suffix = "".join(p[0].upper() for p in parts[1:] if p)[:3]
    return f"{version}{suffix}"[:6]


def _fmt_reset_hours(iso_str: str) -> str:
    """Format reset time as '+Nh' or '' if missing/empty."""
    if not iso_str:
        return ""
    hours = gemini_cloudcode._hours_until(iso_str)
    if hours is None or hours <= 0:
        return ""
    return f"+{hours}h"


def _agy_api_fetch_models() -> dict[str, Any] | None:
    """Fetch model data from daily-cloudcode-pa using agy's keyring token.

    Returns the structured API response with displayNames, or None if
    the keyring token is unavailable or the API call fails.
    """
    try:
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret

        service = Secret.Service.get_sync(
            Secret.ServiceFlags.OPEN_SESSION | Secret.ServiceFlags.LOAD_COLLECTIONS
        )
        import time
        time.sleep(0.2)

        agy_token = None
        for col in service.get_collections():
            for item in col.get_items():
                if "antigravity" in item.get_label():
                    secret = item.retrieve_secret_sync()
                    if secret:
                        agy_token = json.loads(secret.get_text())["token"]["access_token"]
                        break
            if agy_token:
                break

        if not agy_token:
            return None

        url = "https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels"
        req = urllib.request.Request(
            url, data=b"{}",
            headers={
                "Authorization": f"Bearer {agy_token}",
                "Content-Type": "application/json",
                "User-Agent": "antigravity",
            },
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=8, context=ssl.create_default_context())
        return json.loads(resp.read())
    except Exception:
        logger.debug("agy API fetch failed", exc_info=True)
        return None


def _fetch_gemini_agy() -> ProviderQuota | None:
    """Fetch Gemini quota via agy (/usage command via tmux scraper).

    Falls back to the daily Cloud Code API (with keyring token) if
    the scraper is unavailable, then to the existing OAuth path.
    """
    # First try: agy scraper via tmux (most accurate, shows /usage data)
    try:
        models = agy_quota.fetch_agy_quota()
        if models:
            # Group models by (used_pct, reset_hours) — same pool
            pool_groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
            for m in models:
                key = (m["used_pct"], m["reset_hours"])
                if key not in pool_groups:
                    pool_groups[key] = []
                pool_groups[key].append(m)

            groups: list[GroupInfo] = []
            for (used_pct, reset_hours), model_list in pool_groups.items():
                remaining = 1.0 - (used_pct / 100.0)
                # Pick the best label from the group
                label = _agy_model_label(model_list[0]["name"])
                groups.append({
                    "label": label,
                    "remaining": remaining,
                    "used_pct": used_pct,
                    "reset": f"+{reset_hours}h" if reset_hours else "",
                    "model_count": len(model_list),
                })

            groups.sort(key=lambda g: -g["used_pct"])
            if groups:
                result: ProviderQuota = {
                    "cloudcode": True,
                    "agy_scrape": True,
                    "remaining_fraction": groups[0]["remaining"],
                    "reset_iso": groups[0].get("reset", ""),
                    "model_count": len(models),
                    "groups": groups,
                }
                return result
    except Exception:
        logger.debug("agy scraper failed", exc_info=True)

    # Second try: daily API via agy's keyring token (structured data)
    try:
        api_data = _agy_api_fetch_models()
        if api_data:
            result: ProviderQuota = {"cloudcode": True}
            raw_models = api_data.get("models", {})
            groups = []
            model_count = 0
            # Group Google + Anthropic + OpenAI models by remainingFraction
            raw_groups: dict[tuple[float | None, str], list[tuple[str, str]]] = {}
            for mid, m_obj in raw_models.items():
                if not isinstance(m_obj, dict):
                    continue
                q = m_obj.get("quotaInfo", {}) if isinstance(m_obj.get("quotaInfo"), dict) else {}
                rf = q.get("remainingFraction")
                if isinstance(rf, bool) or not isinstance(rf, int | float):
                    continue
                rt = str(q.get("resetTime", ""))[:16] if q.get("resetTime") else ""
                provider = str(m_obj.get("modelProvider", "")).lower()
                if provider not in {"google", "gemini", "model_provider_google"}:
                    continue
                model_count += 1
                display_name = m_obj.get("displayName", mid) if isinstance(m_obj.get("displayName"), str) else mid
                key = (rf, rt)
                if key not in raw_groups:
                    raw_groups[key] = []
                raw_groups[key].append((display_name, mid))

            for (rf, rt), model_list in raw_groups.items():
                fraction = max(0.0, min(float(rf), 1.0))
                used_pct = round((1.0 - fraction) * 100)
                label = _short_model_name(str(model_list[0][1]))
                groups.append({
                    "label": label,
                    "remaining": fraction,
                    "used_pct": used_pct,
                    "reset": rt,
                    "model_count": len(model_list),
                })

            groups.sort(key=lambda g: -g["used_pct"])
            if groups:
                result["groups"] = groups
                result["model_count"] = model_count
                result["remaining_fraction"] = groups[0]["remaining"]
                result["reset_iso"] = groups[0].get("reset", "")
                return result
    except Exception:
        logger.debug("agy API fetch failed", exc_info=True)

    return None


def _agy_model_label(name: str) -> str:
    """Short label from agy model display name.

    Examples:
      'Gemini 3.5 Flash (Medium)' → '3.5F'
      'Gemini 3.1 Pro (Low)' → '3.1P'
      'Claude Sonnet 4.6 (Thinking)' → 'C4.6'
      'GPT-OSS 120B (Medium)' → 'O120B'
    """
    name = re.sub(r"\s*\(.*?\)", "", name)
    if name.startswith("Gemini "):
        name = name[7:]
    elif name.startswith("Claude "):
        # Grab number from end (e.g. 'Sonnet 4.6' → '4.6')
        rest = name[7:]
        nums = re.findall(r"[\d.]+", rest)
        num = nums[-1] if nums else ""
        return f"C{num}"[:6]
    elif name.startswith("GPT-OSS "):
        # Grab first token (e.g. '120B')
        rest = name[8:]
        return f"O{rest.split()[0]}"[:6]
    parts = name.replace("-", " ").split()
    if not parts:
        return name[:6]
    version = parts[0].lstrip("g")
    suffix = "".join(p[0].upper() for p in parts[1:] if p)[:3]
    return f"{version}{suffix}"[:6]


def _fetch_gemini_cloudcode() -> ProviderQuota | None:
    if not gemini_cloudcode.cloudcode_token_exists():
        return None

    data = gemini_cloudcode.cloudcode_format_json()
    if not data.get("ok"):
        error = data.get("error")
        if error in {"auth", "missing_token"}:
            return None
        return {"cloudcode": True, "cloudcode_error": error if isinstance(error, str) else "unknown"}

    result: ProviderQuota = {"cloudcode": True}
    if isinstance(data.get("plan_type"), str):
        result["plan_type"] = data["plan_type"]
    if data.get("prompt_credits") is not None:
        result["prompt_credits"] = json_number(data.get("prompt_credits"))
    if data.get("monthly_prompt_credits") is not None:
        result["monthly_prompt_credits"] = json_number(data.get("monthly_prompt_credits"))

    models = data.get("models")
    worst_remaining: float = 1.0  # worst = lowest remaining = most consumed
    worst_reset = ""
    groups: list[GroupInfo] = []
    model_count = 0
    if isinstance(models, list):
        # Group Google models by (remainingFraction, resetTime) — same key = same pool
        raw_groups: dict[tuple[float | None, str], list[dict[str, Any]]] = {}
        for model in models:
            model_obj = json_object(model) if isinstance(model, dict) else model
            provider = str(model_obj.get("modelProvider", "")).lower()
            if provider not in {"google", "gemini", "model_provider_google"}:
                continue
            model_count += 1
            remaining = model_obj.get("remainingFraction")
            if isinstance(remaining, bool) or not isinstance(remaining, int | float):
                continue
            reset = model_obj.get("resetTime") if isinstance(model_obj.get("resetTime"), str) else ""
            key = (remaining, reset)
            if key not in raw_groups:
                raw_groups[key] = []
            raw_groups[key].append(model_obj)

        for (remaining, reset), _model_list in raw_groups.items():
            faction = max(0.0, min(float(remaining), 1.0))
            used_pct = round((1.0 - faction) * 100)
            if faction < worst_remaining:
                worst_remaining = faction
                worst_reset = reset
            groups.append({
                "label": _short_model_name(str(_model_list[0].get("id", ""))),
                "remaining": faction,
                "used_pct": used_pct,
                "reset": reset,
                "model_count": len(_model_list),
            })

    # Sort groups by used_pct descending (worst first)
    groups.sort(key=lambda g: -g["used_pct"])
    result["groups"] = groups
    result["model_count"] = model_count
    result["remaining_fraction"] = worst_remaining
    result["reset_iso"] = worst_reset
    return result


def _fetch_gemini_probe() -> ProviderQuota | None:
    data = fetch_gemini_quota()
    if data is None:
        return None

    available_models = data.get("available_models")
    probe = data.get("probe")
    result: ProviderQuota = {
        "key_valid": bool(data.get("key_valid")),
        "available_models": [m for m in available_models if isinstance(m, str)] if isinstance(available_models, list) else [],
        "model_count": int(json_number(data.get("model_count"))),
        "probe": probe if isinstance(probe, dict) else None,
    }
    if isinstance(data.get("error"), str):
        result["error"] = data["error"]
    return result


def _fetch_gemini() -> ProviderQuota | None:
    agy = _fetch_gemini_agy()
    if agy is not None:
        return agy
    cloudcode = _fetch_gemini_cloudcode()
    if cloudcode is not None:
        return cloudcode
    return _fetch_gemini_probe()


_PROVIDERS: Final[dict[ProviderName, Callable[[], ProviderQuota | None]]] = {
    "claude": _fetch_claude,
    "codex": _fetch_codex,
    "gemini": _fetch_gemini,
}


def _record_error(error: ProviderError) -> None:
    _last_errors.append(error)
    del _last_errors[:-10]


def _retry_ttl(error_type: str) -> int:
    return AUTH_RETRY_TTL if error_type in {"auth", "missing_token"} else ERROR_RETRY_TTL


def _due_providers(now: float) -> tuple[ProviderName, ...]:
    return tuple(provider for provider in _PROVIDERS if now >= _cache["next_retry"].get(provider, 0.0))


def _claim_refresh(now: float) -> tuple[ProviderName, ...]:
    with _cache_lock:
        if _cache["refreshing"]:
            return ()
        due = _due_providers(now)
        if due:
            _cache["refreshing"] = True
        return due


def _mark_provider_error(provider: ProviderName, error_type: str, *, http_status: int | None = None) -> None:
    now = time.time()
    error: ProviderError = {"provider": provider, "type": error_type, "timestamp": now}
    if http_status is not None:
        error["http_status"] = http_status

    with _cache_lock:
        _record_error(error)
        _cache["ts"] = now
        _cache["stale"].add(provider)
        if error_type in {"auth", "missing_token"}:
            _cache[provider] = None
        _cache["next_retry"][provider] = now + _retry_ttl(error_type)

    logger.warning("quota refresh failed", extra={"quota_error": error})


def _store_provider_result(provider: ProviderName, result: ProviderQuota | None) -> None:
    now = time.time()
    with _cache_lock:
        _cache["ts"] = now
        if result is None:
            _cache[provider] = None
            _cache["stale"].add(provider)
            _cache["next_retry"][provider] = now + AUTH_RETRY_TTL
        else:
            _cache[provider] = result
            _cache["stale"].discard(provider)
            _cache["next_retry"][provider] = now + CACHE_TTL


def _refresh_cache(due_providers: tuple[ProviderName, ...] | None = None) -> None:
    if due_providers is None:
        due_providers = _claim_refresh(time.time())
    if not due_providers:
        return

    try:
        for provider in due_providers:
            fetcher = _PROVIDERS.get(provider)
            if fetcher is None:
                continue
            try:
                result = fetcher()
            except urllib.error.HTTPError as exc:
                error_type = "auth" if exc.code in (401, 403) else "http"
                _mark_provider_error(provider, error_type, http_status=exc.code)
                continue
            except (OSError, TimeoutError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                _mark_provider_error(provider, type(exc).__name__)
                continue
            except Exception as exc:
                _mark_provider_error(provider, type(exc).__name__)
                logger.warning("unexpected quota refresh failure", exc_info=True)
                continue

            if result is None:
                _mark_provider_error(provider, "missing_token")
            else:
                _store_provider_result(provider, result)
    finally:
        with _cache_lock:
            _cache["refreshing"] = False


def _start_refresh_if_needed() -> None:
    due_providers = _claim_refresh(time.time())
    if not due_providers:
        return

    try:
        thread = threading.Thread(
            target=_refresh_cache,
            args=(due_providers,),
            name="hermes-quota-refresh",
            daemon=True,
        )
        thread.start()
    except Exception:
        with _cache_lock:
            _cache["refreshing"] = False
        raise


def _fmt_reset(iso_str: str) -> str:
    """Format an API reset timestamp as local HHhMM; return '?' for missing or invalid values."""
    dt = gemini_cloudcode.parse_reset_time(iso_str)
    if dt is None:
        return "?"
    return dt.astimezone().strftime("%Hh%M")


def _fmt_hours_until(iso_str: str) -> str:
    """Format a reset timestamp as whole hours from now; return '?' for invalid values."""
    dt = gemini_cloudcode.parse_reset_time(iso_str)
    if dt is None:
        return "?"
    seconds = (dt - datetime.now(timezone.utc)).total_seconds()
    return f"{max(0, int(round(seconds / 3600)))}h"


def _fmt_pct(pct: float) -> str:
    return f"{int(pct)}%"


def _render_gemini(data: ProviderQuota | None, is_stale: bool) -> str:
    short = PROVIDER_SHORT["gemini"]
    if data is None or is_stale:
        if data is None and cloudcode_login_pending():
            return f"🟡 {short}:LOGIN..."
        return f"🔴 {short}:?"
    if data.get("cloudcode"):
        error = data.get("cloudcode_error")
        if error in {"auth", "forbidden"}:
            return f"🔴 {short}:AUTH"
        groups = data.get("groups")
        if isinstance(groups, list) and len(groups) > 0:
            worst = groups[0]
            used = worst["used_pct"]

            if used == 0:
                return f"🟢 {short}:0%"

            if used >= 80:
                emoji = "🔴"
            elif used >= 50:
                emoji = "🟡"
            else:
                emoji = "🟢"

            # If all groups share the same %, just show % without labels
            if all(g["used_pct"] == used for g in groups):
                return f"{emoji} {short}:{used}%"

            # Multiple groups at different levels
            detail_parts = []
            for g in groups:
                g_used = g["used_pct"]
                if g_used == 0:
                    continue
                g_label = g.get("label", "")[:6]
                detail_parts.append(f"{g_label}:{g_used}%")

            if detail_parts:
                return f"{emoji} {short}:{used}% ({', '.join(detail_parts)})"
            return f"{emoji} {short}:{used}%"
        if data.get("prompt_credits") is not None:
            credits = int(json_number(data.get("prompt_credits")))
            monthly = int(json_number(data.get("monthly_prompt_credits")))
            suffix = f"{credits}/{monthly}" if monthly > 0 else str(credits)
            return f"🟡 {short}:CREDITS {suffix}"
        return f"🔴 {short}:?"

    if not data.get("key_valid"):
        return f"🔴 {short}:KEY"

    probe = json_object(data.get("probe"))
    status = probe.get("status")
    if status == 429 or probe.get("rate_limited") is True:
        return f"🔴 {short}:LIMIT"
    if status == 403:
        return f"🟡 {short}:403"
    if status == "error":
        return f"🟡 {short}:ERR"
    if status is None:
        return f"🟢 {short}:KEY"
    if int(json_number(status)) == 200:
        return f"🟢 {short}:OK"
    return f"🟡 {short}:{int(json_number(status))}"


def _fallback_quota_window_label(provider: ProviderName, key: QuotaWindowKey) -> str:
    for spec in QUOTA_WINDOW_SPECS.get(provider, ()):
        if spec["key"] == key:
            return spec["label"]
    return ""


def _quota_window_from_values(label: str, pct_value: Any, reset_value: Any) -> QuotaWindowInfo | None:
    pct = _quota_pct_or_none(pct_value)
    if pct is None:
        return None
    return {"label": label, "pct": pct, "reset_iso": _quota_reset(reset_value)}


def _quota_windows_for_render(provider: ProviderName, data: ProviderQuota) -> tuple[list[QuotaWindowInfo], bool]:
    raw_windows = data.get("windows")
    if isinstance(raw_windows, list):
        windows: list[QuotaWindowInfo] = []
        for raw_window in raw_windows:
            if not isinstance(raw_window, dict):
                continue
            label = raw_window.get("label")
            if not isinstance(label, str) or not label:
                continue
            window = _quota_window_from_values(
                label,
                raw_window.get("pct"),
                raw_window.get("reset_iso"),
            )
            if window is None:
                continue
            name = raw_window.get("name")
            if isinstance(name, str):
                window["name"] = name
            windows.append(window)
        return windows, True

    windows = []
    session_window = _quota_window_from_values(
        _fallback_quota_window_label(provider, "session"),
        data.get("session_pct"),
        data.get("reset_iso") or data.get("session_reset"),
    )
    if session_window is not None:
        windows.append(session_window)

    weekly_window = _quota_window_from_values(
        _fallback_quota_window_label(provider, "weekly"),
        data.get("weekly_pct"),
        data.get("weekly_reset"),
    )
    if weekly_window is not None:
        windows.append(weekly_window)

    return windows, False


def _render_quota_window(window: QuotaWindowInfo, include_label: bool) -> str:
    pct = _quota_pct(window.get("pct"))
    value = "FULL" if pct >= THRESHOLD_FULL else _fmt_pct(pct)
    if pct >= THRESHOLD_CRITICAL:
        reset_iso = window.get("reset_iso", "")
        reset = _fmt_reset(reset_iso) if isinstance(reset_iso, str) and reset_iso else ""
        if reset and reset != "?":
            value = f"{value} {reset}"

    label = window.get("label") if isinstance(window.get("label"), str) else ""
    if include_label and label:
        return f"{label}:{value}"
    return value


def _render_provider(provider: ProviderName, data: ProviderQuota | None, is_stale: bool) -> str:
    if provider == "gemini":
        return _render_gemini(data, is_stale)

    short = PROVIDER_SHORT[provider]
    if data is None or is_stale:
        return f"🔴 {short}:?"
    windows, has_explicit_windows = _quota_windows_for_render(provider, data)
    if not windows:
        return f"🟡 {short}:?"

    worst_pct = max(_quota_pct(window.get("pct")) for window in windows)
    include_labels = has_explicit_windows or len(windows) > 1 or (
        len(windows) == 1 and data.get("session_pct") is None and data.get("weekly_pct") is not None
    )
    rendered_windows = " ".join(_render_quota_window(window, include_labels) for window in windows)

    if worst_pct >= THRESHOLD_FULL:
        return f"🔴 {short}:{rendered_windows}"
    if worst_pct >= THRESHOLD_WARN:
        return f"🟡 {short}:{rendered_windows}"
    return f"🟢 {short}:{rendered_windows}"


def on_status_bar_render(snapshot=None, **kwargs) -> str | None:
    """Hook: return a string for the status bar, or None to not contribute.
    
    Accepts **kwargs to stay forward-compatible (e.g. telemetry_schema_version).
    """
    try:
        _start_refresh_if_needed()
        with _cache_lock:
            cache_snapshot = {
                **{provider: _cache[provider] for provider in PROVIDERS},
                "stale": set(_cache["stale"]),
            }

        parts = []
        for provider in PROVIDERS:
            data = cache_snapshot[provider]
            is_stale = provider in cache_snapshot["stale"]
            if provider not in _PROVIDERS and data is None and not is_stale:
                continue
            parts.append(_render_provider(provider, data, is_stale))
        return " │ ".join(parts)
    except Exception as exc:
        logger.warning(
            "quota status render failed",
            extra={"quota_error": {"provider": "render", "type": type(exc).__name__, "timestamp": time.time()}},
        )
        return None


def register(ctx):
    """Called by Hermes plugin system on load."""
    ctx.register_hook("on_status_bar_render", on_status_bar_render)
