"""
Hermes Quota Status Plugin - displays Claude, Codex, Gemini, GLM, and DeepSeek quota status.

Uses tokens stored by each CLI/API integration to query quota APIs directly.
Network refreshes run in a background thread so status bar rendering never
blocks the TUI redraw path.
"""
from __future__ import annotations

import json
import logging
import math
import re
import ssl
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final, Literal, NotRequired, TypeAlias, TypedDict, cast

try:
    from . import gemini_cloudcode
    from . import agy_quota
    from .quota_api import (
        GEMINI_AUTH_HTTP_STATUSES,
        MAX_RESPONSE_BYTES,
        fetch_claude_quota,
        fetch_codex_quota,
        fetch_deepseek_balance,
        fetch_gemini_quota,
        fetch_glm_quota,
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
        GEMINI_AUTH_HTTP_STATUSES,
        MAX_RESPONSE_BYTES,
        fetch_claude_quota,
        fetch_codex_quota,
        fetch_deepseek_balance,
        fetch_gemini_quota,
        fetch_glm_quota,
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
CONFIG_CONTAINER_KEYS: Final[tuple[str, ...]] = (
    "config",
    "config_snapshot",
    "snapshot",
    "context",
    "render_context",
)
STATUS_SEGMENT_SEPARATOR: Final[str] = " │ "
STALE_MARKER: Final[str] = " (stale)"
NARROW_TERMINAL_WIDTH_LIMIT: Final[int] = 60
TERMINAL_WIDTH_KEYS: Final[tuple[str, ...]] = ("terminal_width", "terminal_columns", "columns", "width")
TERMINAL_WIDTH_CONTAINER_KEYS: Final[tuple[str, ...]] = (
    "terminal",
    "terminal_size",
    "viewport",
    "screen",
    "dimensions",
    "context",
    "render_context",
)
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
AUTH_FAILURE_SUPPRESSION_THRESHOLD: Final[int] = 3
# Longer than the expected successful provider fetch path, but finite so a
# worker stuck in an OS, keyring, or network call cannot suppress that provider
# indefinitely. A reclaimed worker may still return; its generation token is
# checked before every state mutation below.
REFRESH_CLAIM_TTL: Final[int] = 60
AGY_AUTH_HTTP_STATUSES: Final[frozenset[int]] = frozenset({401, 403})
# Agy currently stores its keyring entry as service=gemini,
# username=antigravity. Older Linux installs exposed only this exact label,
# so retain it as a compatibility match without accepting substring lookalikes.
AGY_KEYRING_ATTRIBUTES: Final[dict[str, str]] = {
    "service": "gemini",
    "username": "antigravity",
}
AGY_KEYRING_LEGACY_LABELS: Final[frozenset[str]] = frozenset({"antigravity"})
AGY_KEYRING_DEADLINE_SECONDS: Final[float] = 1.0
AGY_TOKEN_CACHE_TTL: Final[float] = 300.0
CREDENTIAL_REQUIRED_OMIT_PROVIDERS: Final[tuple[ProviderName, ...]] = ("glm", "deepseek")

logger = logging.getLogger(__name__)

__all__ = [
    "ProviderName",
    "PROVIDERS",
    "PROVIDER_SHORT",
    "fetch_claude_quota",
    "fetch_codex_quota",
    "fetch_deepseek_balance",
    "fetch_gemini_quota",
    "fetch_glm_quota",
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
_refresh_thread_factory: Callable[..., threading.Thread] = threading.Thread


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
    available_limit_pct: float
    balance: float
    total_balance: float
    total_balance_display: str
    granted_balance: float
    granted_balance_display: str
    topped_up_balance: float
    topped_up_balance_display: str
    currency: str
    is_available: bool
    host: str
    prompt_credits: float
    monthly_prompt_credits: float
    plan_type: str
    key_valid: bool
    auth_error: bool
    http_status: int
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
    stale: set[ProviderName]
    missing_credentials: set[ProviderName]
    next_retry: dict[ProviderName, float]


class ProviderError(TypedDict):
    provider: ProviderName | Literal["render"]
    type: str
    timestamp: float
    http_status: NotRequired[int]


class RefreshClaim(TypedDict):
    generation: int
    claimed_at: float


_cache: CacheState = {
    "claude": None,
    "codex": None,
    "gemini": None,
    "glm": None,
    "deepseek": None,
    "ts": 0.0,
    "stale": set(),
    "missing_credentials": set(),
    "next_retry": {provider: 0.0 for provider in PROVIDERS},
}
# Authentication counters and cached provider data form one render snapshot.
# A re-entrant lock lets the counter helpers enforce synchronization even when
# their caller is already updating the cache, while keeping network I/O outside
# the critical section in the refresh loop.
_cache_lock = threading.RLock()
_last_errors: list[ProviderError] = []
_auth_failure_counts: dict[ProviderName, int] = {provider: 0 for provider in PROVIDERS}
_refresh_claims: dict[ProviderName, RefreshClaim] = {}
_refresh_generations: dict[ProviderName, int] = {provider: 0 for provider in PROVIDERS}
_agy_token_cache_lock = threading.Lock()
_agy_token_cache: str | None = None
_agy_token_cache_expires_at = 0.0
_agy_token_cache_generation = 0

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


def _fetch_glm() -> ProviderQuota | None:
    data = fetch_glm_quota()
    return cast(ProviderQuota | None, data)


def _fetch_deepseek() -> ProviderQuota | None:
    data = fetch_deepseek_balance()
    return cast(ProviderQuota | None, data)


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


_AGY_RESET_DELTA_RE: Final[re.Pattern[str]] = re.compile(r"^\+(?P<hours>\d+)h$")


def _agy_reset_iso(value: Any, scraped_at: datetime) -> str:
    """Convert Agy's relative whole-hour reset to an ISO timestamp.

    Agy's scraper normally supplies an integer hour count, while older callers
    may supply its ``+Nh`` display form. Fixing the timestamp at scrape time
    introduces at most the cache TTL's drift and keeps ``reset_iso`` strictly
    ISO-or-empty for every downstream parser and countdown formatter.
    """
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        delta = f"+{value}h"
    elif isinstance(value, str):
        delta = value
    else:
        return ""

    match = _AGY_RESET_DELTA_RE.fullmatch(delta)
    if match is None:
        return ""
    try:
        hours = int(match.group("hours"))
        if hours <= 0:
            return ""
        reset_at = scraped_at + timedelta(hours=hours)
    except (OverflowError, ValueError):
        return ""
    return reset_at.isoformat().replace("+00:00", "Z")


def _call_with_deadline(callback: Callable[[], Any], timeout: float) -> Any | None:
    """Run a blocking callback off the caller thread and bound the wait.

    Secret Service calls cannot be interrupted safely. A truly wedged call
    therefore leaves one blocked daemon thread behind, but never blocks the
    refresh worker or invokes libsecret on the GTK main loop.
    """
    completed = threading.Event()
    result: list[Any] = []
    error: list[Exception] = []

    def run() -> None:
        try:
            result.append(callback())
        except Exception as exc:
            error.append(exc)
        finally:
            completed.set()

    threading.Thread(target=run, name="hermes-agy-keyring", daemon=True).start()
    if not completed.wait(max(0.0, timeout)):
        logger.warning("agy keyring service lookup timed out")
        return None
    if error:
        raise error[0]
    return result[0] if result else None


def _agy_keyring_item_matches(item: Any) -> bool:
    """Match Agy credentials using the observed schema or legacy exact label."""
    try:
        attributes = item.get_attributes()
    except Exception:
        attributes = None
    if isinstance(attributes, Mapping) and all(
        attributes.get(key) == value for key, value in AGY_KEYRING_ATTRIBUTES.items()
    ):
        return True

    try:
        label = item.get_label()
    except Exception:
        return False
    return isinstance(label, str) and label in AGY_KEYRING_LEGACY_LABELS


def _agy_token_from_item(item: Any) -> str | None:
    """Retrieve and validate an Agy token payload without leaking shape errors."""
    try:
        secret = item.retrieve_secret_sync()
        if secret is None:
            return None
        text = secret.get_text()
        if not isinstance(text, str):
            return None
        payload = json.loads(text)
    except (AttributeError, TypeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None
    token = payload.get("token")
    if not isinstance(token, dict):
        return None
    access_token = token.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return None
    return access_token


def _read_agy_keyring_token() -> str | None:
    """Read Agy's token from Secret Service, bounding service acquisition."""
    try:
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret

        service = _call_with_deadline(
            lambda: Secret.Service.get_sync(
                Secret.ServiceFlags.OPEN_SESSION | Secret.ServiceFlags.LOAD_COLLECTIONS
            ),
            AGY_KEYRING_DEADLINE_SECONDS,
        )
        if service is None:
            return None

        for collection in service.get_collections():
            for item in collection.get_items():
                if not _agy_keyring_item_matches(item):
                    continue
                token = _agy_token_from_item(item)
                if token is not None:
                    return token
    except Exception:
        logger.debug("agy keyring lookup failed", exc_info=True)
    return None


def _get_agy_token() -> str | None:
    """Return Agy's bounded-TTL cached token with race-safe refill."""
    global _agy_token_cache, _agy_token_cache_expires_at

    now = time.monotonic()
    with _agy_token_cache_lock:
        if _agy_token_cache is not None and now < _agy_token_cache_expires_at:
            return _agy_token_cache
        refill_generation = _agy_token_cache_generation

    token = _read_agy_keyring_token()
    if token is None:
        return None

    with _agy_token_cache_lock:
        if refill_generation != _agy_token_cache_generation:
            return None
        _agy_token_cache = token
        _agy_token_cache_expires_at = time.monotonic() + AGY_TOKEN_CACHE_TTL
        return token


def _invalidate_agy_token_cache() -> None:
    """Invalidate cached and in-flight Agy tokens after authentication failure."""
    global _agy_token_cache, _agy_token_cache_expires_at, _agy_token_cache_generation

    with _agy_token_cache_lock:
        _agy_token_cache_generation += 1
        _agy_token_cache = None
        _agy_token_cache_expires_at = 0.0


def _agy_api_fetch_models() -> dict[str, Any] | None:
    """Fetch model data from daily-cloudcode-pa using agy's keyring token.

    Returns the structured API response with display names. Returns None when
    the keyring token is unavailable or the API returns a non-auth HTTP error.
    Authentication, transport, and malformed-body errors are propagated.
    """
    agy_token = _get_agy_token()
    if agy_token is None:
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
    try:
        with urllib.request.urlopen(req, timeout=8, context=ssl.create_default_context()) as resp:
            data = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        logger.debug("agy API fetch failed", exc_info=True)
        if exc.code in AGY_AUTH_HTTP_STATUSES:
            _invalidate_agy_token_cache()
            raise
        return None

    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("Agy API response too large")

    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise ValueError("Agy API response is not a JSON object")
    return payload


def _fetch_gemini_agy() -> ProviderQuota | None:
    """Fetch Gemini quota from Agy's best-effort TUI scrape.

    The viewport can contain only a subset of the available model pools, so
    this result must remain a fallback behind both structured quota sources.
    """
    try:
        models = agy_quota.fetch_agy_quota()
        if models:
            gemini_models = [model for model in models if model.get("provider") == "gemini"]
            # A relative reset is anchored once per scrape. This deliberately
            # fixes the clock until the cache refreshes rather than allowing
            # non-ISO display strings to leak into the reset_iso contract.
            scraped_at = _now_utc()
            # Group models by (used_pct, reset_iso) — same pool.
            pool_groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
            for m in gemini_models:
                reset_iso = _agy_reset_iso(m.get("reset_hours"), scraped_at)
                key = (m["used_pct"], reset_iso)
                if key not in pool_groups:
                    pool_groups[key] = []
                pool_groups[key].append(m)

            groups: list[GroupInfo] = []
            for (used_pct, reset_iso), model_list in pool_groups.items():
                remaining = 1.0 - (used_pct / 100.0)
                # Pick the best label from the group
                label = _agy_model_label(model_list[0]["name"])
                groups.append({
                    "label": label,
                    "remaining": remaining,
                    "used_pct": used_pct,
                    "reset": reset_iso,
                    "model_count": len(model_list),
                })

            groups.sort(key=lambda g: -g["used_pct"])
            if groups:
                result: ProviderQuota = {
                    "agy_scrape": True,
                    "remaining_fraction": groups[0]["remaining"],
                    "reset_iso": groups[0].get("reset", ""),
                    "model_count": len(gemini_models),
                    "groups": groups,
                }
                return result
    except Exception:
        logger.debug("agy scraper failed", exc_info=True)

    return None


def _fetch_gemini_agy_api() -> ProviderQuota | None:
    """Fetch Gemini quota from Agy's structured daily Cloud Code API.

    Returns None when no structured quota is available. Authentication,
    transport, and malformed-response errors are propagated so the refresh
    coordinator can classify them without discarding retained quota data.
    """

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
    except urllib.error.HTTPError as exc:
        logger.debug("agy API fetch failed", exc_info=True)
        if exc.code in AGY_AUTH_HTTP_STATUSES:
            raise
        return None
    except Exception:
        logger.debug("agy API fetch failed", exc_info=True)
        raise

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
        "auth_error": data.get("auth_error") is True,
        "available_models": [m for m in available_models if isinstance(m, str)] if isinstance(available_models, list) else [],
        "model_count": int(json_number(data.get("model_count"))),
        "probe": probe if isinstance(probe, dict) else None,
    }
    if isinstance(data.get("http_status"), int):
        result["http_status"] = data["http_status"]
    if isinstance(data.get("error"), str):
        result["error"] = data["error"]
    return result


def _fetch_gemini() -> ProviderQuota | None:
    # Never merge source results: the same quota pool may be present in both
    # structured responses and the partial TUI viewport. Strict precedence
    # avoids double-counting while retaining each source as a fallback.
    auth_failure: ProviderQuota | None = None
    fallback_error: ProviderQuota | None = None

    try:
        agy_api = _fetch_gemini_agy_api()
    except urllib.error.HTTPError:
        # Agy's cached keyring token may simply have expired between app
        # refreshes. Treat that as this source being temporarily unavailable,
        # not as evidence that the user's Gemini credentials were revoked.
        agy_api = None
    if agy_api is not None:
        return agy_api

    cloudcode = _fetch_gemini_cloudcode()
    if cloudcode is not None and "cloudcode_error" not in cloudcode:
        return cloudcode
    if cloudcode is not None:
        cloudcode_error = _provider_result_auth_error_type("gemini", cloudcode)
        if cloudcode_error == "auth":
            auth_failure = cloudcode
        elif cloudcode_error != "missing_token":
            fallback_error = cloudcode

    if auth_failure is None:
        agy_scrape = _fetch_gemini_agy()
        if agy_scrape is not None:
            return agy_scrape

    try:
        probe = _fetch_gemini_probe()
    except (OSError, TimeoutError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        if auth_failure is not None:
            return auth_failure
        raise
    if probe is not None:
        if _provider_result_is_authenticated_success("gemini", probe):
            return probe
        if _provider_result_auth_error_type("gemini", probe) == "auth":
            auth_failure = probe
        elif auth_failure is None:
            return probe

    if auth_failure is not None:
        return auth_failure
    if fallback_error is not None:
        return fallback_error
    return cloudcode


_PROVIDERS: Final[dict[ProviderName, Callable[[], ProviderQuota | None]]] = {
    "claude": _fetch_claude,
    "codex": _fetch_codex,
    "gemini": _fetch_gemini,
    "glm": _fetch_glm,
    "deepseek": _fetch_deepseek,
}


def _record_error(error: ProviderError) -> None:
    _last_errors.append(error)
    del _last_errors[:-10]


def _retry_ttl(error_type: str) -> int:
    return AUTH_RETRY_TTL if error_type in {"auth", "missing_token"} else ERROR_RETRY_TTL


def _record_auth_failure(provider: ProviderName) -> None:
    with _cache_lock:
        _auth_failure_counts[provider] = _auth_failure_counts.get(provider, 0) + 1


def _reset_auth_failures(provider: ProviderName) -> None:
    with _cache_lock:
        _auth_failure_counts[provider] = 0


def _auth_failure_counts_snapshot() -> dict[ProviderName, int]:
    with _cache_lock:
        return dict(_auth_failure_counts)


def _provider_auth_suppressed(provider: ProviderName, auth_failure_counts: Mapping[ProviderName, int]) -> bool:
    return auth_failure_counts.get(provider, 0) >= AUTH_FAILURE_SUPPRESSION_THRESHOLD


def _provider_missing_credentials(
    provider: ProviderName, missing_credentials: set[ProviderName] | frozenset[ProviderName]
) -> bool:
    return provider in CREDENTIAL_REQUIRED_OMIT_PROVIDERS and provider in missing_credentials


def _mark_provider_credentials_available(provider: ProviderName) -> None:
    _cache["missing_credentials"].discard(provider)


def _provider_result_auth_error_type(provider: ProviderName, result: ProviderQuota) -> str | None:
    if provider != "gemini":
        return None

    if result.get("cloudcode") is True:
        error = result.get("cloudcode_error")
        if error in {"auth", "forbidden"}:
            return "auth"
        if error == "missing_token":
            return "missing_token"
        return None

    if result.get("key_valid") is False and result.get("auth_error") is True:
        return "auth"
    return None


def _provider_result_is_authenticated_success(provider: ProviderName, result: ProviderQuota) -> bool:
    if provider == "gemini":
        if result.get("cloudcode_error") is not None:
            return False
        if result.get("cloudcode") is True:
            return True
        return result.get("key_valid") is True
    return True


def _due_providers(now: float, providers: tuple[ProviderName, ...] = PROVIDERS) -> tuple[ProviderName, ...]:
    return tuple(
        provider
        for provider in providers
        if provider in _PROVIDERS and now >= _cache["next_retry"].get(provider, 0.0)
    )


def _claim_refresh(
    now: float,
    providers: tuple[ProviderName, ...] = PROVIDERS,
    *,
    force: bool = False,
) -> dict[ProviderName, int]:
    """Claim each eligible provider independently and return generation tokens.

    ``force`` preserves the synchronous helper's historical behavior for tests
    and explicit callers. It bypasses retry backoff, but never an unexpired
    in-flight claim.
    """
    with _cache_lock:
        claimed: dict[ProviderName, int] = {}
        for provider in dict.fromkeys(providers):
            if provider not in _PROVIDERS:
                continue
            if not force and now < _cache["next_retry"].get(provider, 0.0):
                continue

            active_claim = _refresh_claims.get(provider)
            if active_claim is not None and now - active_claim["claimed_at"] < REFRESH_CLAIM_TTL:
                continue

            generation = _refresh_generations.get(provider, 0) + 1
            _refresh_generations[provider] = generation
            _refresh_claims[provider] = {"generation": generation, "claimed_at": now}
            claimed[provider] = generation
        return claimed


def _claim_is_current_locked(provider: ProviderName, generation: int) -> bool:
    claim = _refresh_claims.get(provider)
    return claim is not None and claim["generation"] == generation


def _release_refresh_claim(provider: ProviderName, generation: int) -> None:
    with _cache_lock:
        if _claim_is_current_locked(provider, generation):
            del _refresh_claims[provider]


def _mark_provider_error(
    provider: ProviderName,
    error_type: str,
    generation: int,
    *,
    http_status: int | None = None,
) -> bool:
    now = time.time()
    error: ProviderError = {"provider": provider, "type": error_type, "timestamp": now}
    if http_status is not None:
        error["http_status"] = http_status

    with _cache_lock:
        if not _claim_is_current_locked(provider, generation):
            return False
        _record_error(error)
        _cache["ts"] = now
        _cache["stale"].add(provider)
        if error_type == "auth":
            _record_auth_failure(provider)
            _cache[provider] = None
            _mark_provider_credentials_available(provider)
        elif error_type == "missing_token":
            _cache[provider] = None
            _cache["missing_credentials"].add(provider)
        else:
            _mark_provider_credentials_available(provider)
        _cache["next_retry"][provider] = now + _retry_ttl(error_type)

    logger.warning("quota refresh failed", extra={"quota_error": error})
    return True


def _store_provider_result(provider: ProviderName, result: ProviderQuota | None, generation: int) -> bool:
    now = time.time()
    with _cache_lock:
        if not _claim_is_current_locked(provider, generation):
            return False
        _cache["ts"] = now
        if result is None:
            _cache[provider] = None
            _cache["stale"].add(provider)
            _cache["missing_credentials"].add(provider)
            _cache["next_retry"][provider] = now + AUTH_RETRY_TTL
        else:
            _cache[provider] = result
            _cache["stale"].discard(provider)
            _mark_provider_credentials_available(provider)
            result_error_type = _provider_result_auth_error_type(provider, result)
            if result_error_type == "auth":
                _record_error({"provider": provider, "type": "auth", "timestamp": now})
                _record_auth_failure(provider)
                _mark_provider_credentials_available(provider)
                _cache["next_retry"][provider] = now + AUTH_RETRY_TTL
            elif result_error_type == "missing_token":
                _record_error({"provider": provider, "type": "missing_token", "timestamp": now})
                _cache[provider] = None
                _cache["stale"].add(provider)
                _cache["missing_credentials"].add(provider)
                _cache["next_retry"][provider] = now + AUTH_RETRY_TTL
            elif _provider_result_is_authenticated_success(provider, result):
                _reset_auth_failures(provider)
                _cache["next_retry"][provider] = now + CACHE_TTL
            else:
                _cache["next_retry"][provider] = now + CACHE_TTL
    return True


def _refresh_provider(provider: ProviderName, generation: int) -> None:
    """Fetch one provider without holding the cache lock."""
    try:
        fetcher = _PROVIDERS.get(provider)
        if fetcher is None:
            return
        try:
            result = fetcher()
        except urllib.error.HTTPError as exc:
            auth_statuses = GEMINI_AUTH_HTTP_STATUSES if provider == "gemini" else (401, 403)
            error_type = "auth" if exc.code in auth_statuses else "http"
            accepted = _mark_provider_error(provider, error_type, generation, http_status=exc.code)
            if not accepted:
                logger.debug(
                    "discarded stale quota refresh failure for %s generation %s",
                    provider,
                    generation,
                    exc_info=True,
                )
            return
        except (OSError, TimeoutError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            accepted = _mark_provider_error(provider, type(exc).__name__, generation)
            if not accepted:
                logger.debug(
                    "discarded stale quota refresh failure for %s generation %s",
                    provider,
                    generation,
                    exc_info=True,
                )
            return
        except Exception as exc:
            accepted = _mark_provider_error(provider, type(exc).__name__, generation)
            if accepted:
                logger.warning("unexpected quota refresh failure", exc_info=True)
            else:
                logger.debug(
                    "discarded stale quota refresh failure for %s generation %s",
                    provider,
                    generation,
                    exc_info=True,
                )
            return

        if result is None:
            _mark_provider_error(provider, "missing_token", generation)
        else:
            _store_provider_result(provider, result, generation)
    finally:
        _release_refresh_claim(provider, generation)


def _refresh_cache(due_providers: tuple[ProviderName, ...] | None = None) -> None:
    """Synchronously refresh providers; production rendering uses per-provider threads."""
    claims = _claim_refresh(
        time.time(),
        due_providers if due_providers is not None else PROVIDERS,
        force=due_providers is not None,
    )
    for provider, generation in claims.items():
        _refresh_provider(provider, generation)


def _start_refresh_if_needed(providers: tuple[ProviderName, ...]) -> None:
    claims = _claim_refresh(time.time(), providers)
    if not claims:
        return

    pending_claims = dict(claims)
    for provider, generation in claims.items():
        try:
            thread = _refresh_thread_factory(
                target=_refresh_provider,
                args=(provider, generation),
                name=f"hermes-quota-refresh-{provider}",
                daemon=True,
            )
            thread.start()
            pending_claims.pop(provider)
        except Exception:
            for pending_provider, pending_generation in pending_claims.items():
                _release_refresh_claim(pending_provider, pending_generation)
            raise


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_reset(iso_str: Any) -> str:
    """Format an API reset timestamp as a relative countdown; return '?' for missing or invalid values."""
    dt = gemini_cloudcode.parse_reset_time(iso_str)
    if dt is None:
        return "?"
    seconds = (dt - _now_utc()).total_seconds()
    if seconds <= 0:
        return "0h0m"
    minutes = int(math.ceil(seconds / 60.0))
    if minutes >= 24 * 60:
        days = minutes // (24 * 60)
        hours = (minutes % (24 * 60)) // 60
        return f"{days}d{hours}h"
    return f"{minutes // 60}h{minutes % 60}m"


def _fmt_hours_until(iso_str: Any) -> str:
    """Alias for the canonical relative countdown formatter."""
    return _fmt_reset(iso_str)


def _fmt_pct(pct: float) -> str:
    return f"{int(pct)}%"


def _first_balance_number(data: ProviderQuota) -> float | None:
    for key in ("balance", "total_balance", "granted_balance", "topped_up_balance"):
        number = json_number_or_none(data.get(key))
        if number is not None:
            return number
    return None


def _balance_display(data: ProviderQuota) -> str:
    amount = ""
    for display_key in ("total_balance_display", "granted_balance_display", "topped_up_balance_display"):
        display = data.get(display_key)
        if isinstance(display, str) and display:
            amount = display
            break
    if not amount:
        balance = _first_balance_number(data)
        if balance is None:
            return ""
        amount = f"{balance:.2f}"

    currency = data.get("currency")
    if currency == "USD":
        return amount if amount.startswith("$") else f"${amount}"
    if isinstance(currency, str) and currency:
        return f"{amount} {currency}"
    return amount


def _with_stale_marker(segment: str, is_stale: bool) -> str:
    """Identify retained quota values without replacing them with an error."""
    return f"{segment}{STALE_MARKER}" if is_stale else segment


def _render_deepseek_data(data: ProviderQuota | None) -> str:
    short = PROVIDER_SHORT["deepseek"]
    if data is None:
        return f"🔴 {short}:?"

    display = _balance_display(data)
    if not display:
        return f"🟡 {short}:?"

    balance = _first_balance_number(data)
    is_available = data.get("is_available")
    if is_available is False or (balance is not None and balance <= 0.0):
        return f"🔴 {short}:{display}"
    return f"🟢 {short}:{display}"


def _render_balance_provider(short: str, data: ProviderQuota, unknown_status: str = "🟡") -> str:
    display = _balance_display(data)
    if not display:
        return f"{unknown_status} {short}:?"

    balance = _first_balance_number(data)
    if balance is not None and balance <= 0.0:
        return f"🔴 {short}:{display}"
    return f"🟢 {short}:{display}"


def _render_gemini_data(data: ProviderQuota | None) -> str:
    short = PROVIDER_SHORT["gemini"]
    if data is None:
        if cloudcode_login_pending():
            return f"🟡 {short}:LOGIN..."
        return f"🔴 {short}:?"
    if data.get("cloudcode") or data.get("agy_scrape"):
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
    if session_window is None:
        available_limit_pct = _quota_pct_or_none(data.get("available_limit_pct"))
        if available_limit_pct is not None:
            session_window = _quota_window_from_values(
                _fallback_quota_window_label(provider, "session"),
                100.0 - available_limit_pct,
                data.get("reset_iso") or data.get("session_reset"),
            )
    if session_window is None:
        remaining_fraction = json_number_or_none(data.get("remaining_fraction"))
        if remaining_fraction is not None:
            session_window = _quota_window_from_values(
                _fallback_quota_window_label(provider, "session"),
                100.0 - (max(0.0, min(1.0, remaining_fraction)) * 100.0),
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
    return _with_stale_marker(_render_provider_data(provider, data), is_stale and data is not None)


def _render_provider_data(provider: ProviderName, data: ProviderQuota | None) -> str:
    if provider == "gemini":
        return _render_gemini_data(data)
    if provider == "deepseek":
        return _render_deepseek_data(data)

    short = PROVIDER_SHORT[provider]
    if data is None:
        return f"🔴 {short}:?"
    windows, has_explicit_windows = _quota_windows_for_render(provider, data)
    if not windows:
        if provider == "glm" and _first_balance_number(data) is not None:
            return _render_balance_provider(short, data)
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


def _source_value(source: Any, key: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def _terminal_width_value(raw_width: Any) -> int | None:
    if isinstance(raw_width, bool):
        return None
    if isinstance(raw_width, int):
        return raw_width if raw_width > 0 else None
    if isinstance(raw_width, float):
        if math.isfinite(raw_width) and raw_width > 0:
            return int(raw_width)
        return None
    if isinstance(raw_width, str):
        try:
            width = int(raw_width.strip())
        except ValueError:
            return None
        return width if width > 0 else None
    return None


def _terminal_width_from_source(source: Any, *, depth: int = 0, seen: set[int] | None = None) -> int | None:
    if source is None or depth > 4:
        return None
    if seen is None:
        seen = set()
    source_id = id(source)
    if source_id in seen:
        return None
    seen.add(source_id)

    for key in TERMINAL_WIDTH_KEYS:
        width = _terminal_width_value(_source_value(source, key))
        if width is not None:
            return width

    for key in TERMINAL_WIDTH_CONTAINER_KEYS:
        width = _terminal_width_from_source(_source_value(source, key), depth=depth + 1, seen=seen)
        if width is not None:
            return width
    return None


def _terminal_width_from_render_args(snapshot: Any, kwargs: Mapping[str, Any]) -> int | None:
    width = _terminal_width_from_source(snapshot)
    if width is not None:
        return width
    return _terminal_width_from_source(kwargs)


def _display_width(text: str) -> int:
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        category = unicodedata.category(char)
        if category in {"Mn", "Me", "Cf"} or category.startswith("C"):
            continue
        if unicodedata.east_asian_width(char) in {"F", "W"}:
            width += 2
            continue
        width += 1
    return width


def _trim_status_parts_for_width(parts: list[str], terminal_width: int | None) -> str | None:
    if terminal_width is None:
        return STATUS_SEGMENT_SEPARATOR.join(parts)

    selected: list[str] = []
    for part in parts:
        candidate = STATUS_SEGMENT_SEPARATOR.join([*selected, part])
        if _display_width(candidate) <= terminal_width:
            selected.append(part)
            continue
        break
    if not selected:
        return None
    return STATUS_SEGMENT_SEPARATOR.join(selected)


def _normalize_provider_allowlist(raw_providers: Any) -> tuple[ProviderName, ...] | None:
    if raw_providers is None or isinstance(raw_providers, str | bytes | bytearray) or isinstance(raw_providers, Mapping):
        return None
    if not isinstance(raw_providers, Iterable) or isinstance(raw_providers, Iterator):
        return None

    configured: list[ProviderName] = []
    invalid_names: list[str] = []
    seen: set[ProviderName] = set()
    for provider in raw_providers:
        if not isinstance(provider, str):
            continue
        if provider not in PROVIDERS:
            invalid_names.append(provider)
            continue
        provider_name = cast(ProviderName, provider)
        if provider_name in seen:
            continue
        configured.append(provider_name)
        seen.add(provider_name)

    if invalid_names:
        logger.warning(
            "ignored unknown quota_status.providers entries",
            extra={"quota_status_unknown_providers": invalid_names},
        )
    return tuple(configured)


def _provider_allowlist_from_quota_status(quota_status: Any) -> tuple[ProviderName, ...] | None:
    if quota_status is None:
        return None
    return _normalize_provider_allowlist(_source_value(quota_status, "providers"))


def _provider_allowlist_from_config_source(
    source: Any,
    *,
    depth: int = 0,
    seen: set[int] | None = None,
) -> tuple[ProviderName, ...] | None:
    if source is None or depth > 4:
        return None
    if seen is None:
        seen = set()
    source_id = id(source)
    if source_id in seen:
        return None
    seen.add(source_id)

    allowlist = _provider_allowlist_from_quota_status(_source_value(source, "quota_status"))
    if allowlist is not None:
        return allowlist

    for key in CONFIG_CONTAINER_KEYS:
        nested = _source_value(source, key)
        allowlist = _provider_allowlist_from_config_source(nested, depth=depth + 1, seen=seen)
        if allowlist is not None:
            return allowlist
    return None


def _provider_allowlist_from_render_args(snapshot: Any, kwargs: Mapping[str, Any]) -> tuple[ProviderName, ...] | None:
    allowlist = _provider_allowlist_from_config_source(snapshot)
    if allowlist is not None:
        return allowlist
    return _provider_allowlist_from_config_source(kwargs)


def on_status_bar_render(snapshot=None, **kwargs) -> str | None:
    """Hook: return a string for the status bar, or None to not contribute.
    
    Accepts **kwargs to stay forward-compatible (e.g. telemetry_schema_version).
    """
    try:
        provider_allowlist = _provider_allowlist_from_render_args(snapshot, kwargs)
        terminal_width = _terminal_width_from_render_args(snapshot, kwargs)
        render_providers = provider_allowlist if provider_allowlist is not None else PROVIDERS
        if not render_providers:
            return None

        _start_refresh_if_needed(render_providers)
        with _cache_lock:
            cache_snapshot = {
                **{provider: _cache[provider] for provider in render_providers},
                "stale": set(_cache["stale"]),
                "missing_credentials": set(_cache["missing_credentials"]),
                "auth_failure_counts": _auth_failure_counts_snapshot(),
            }

        parts = []
        auth_failure_counts = cast(Mapping[ProviderName, int], cache_snapshot["auth_failure_counts"])
        missing_credentials = cast(set[ProviderName], cache_snapshot["missing_credentials"])
        for provider in render_providers:
            if _provider_auth_suppressed(provider, auth_failure_counts):
                continue
            data = cache_snapshot[provider]
            is_stale = provider in cache_snapshot["stale"]
            if data is None and is_stale and _provider_missing_credentials(provider, missing_credentials):
                continue
            if provider not in _PROVIDERS and data is None and not is_stale:
                continue
            parts.append(_render_provider(provider, data, is_stale))
        if not parts:
            return None
        return _trim_status_parts_for_width(parts, terminal_width)
    except Exception as exc:
        logger.warning(
            "quota status render failed",
            extra={"quota_error": {"provider": "render", "type": type(exc).__name__, "timestamp": time.time()}},
        )
        return None


def register(ctx):
    """Called by Hermes plugin system on load."""
    ctx.register_hook("on_status_bar_render", on_status_bar_render)
