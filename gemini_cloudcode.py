"""Gemini Cloud Code quota client.

This module talks to Google's internal Cloud Code API used by Gemini Code
Assist clients. It intentionally uses only the Python standard library so it
can run inside the Hermes plugin process without extra installation steps.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Final, Literal

# Google's published installed-app credentials for the Cloud Code / gemini-cli
# OAuth flow. Installed-app client secrets are distributed with the client and
# therefore are not confidential by design. These remain a third party's
# credentials, so reuse may be subject to Google's terms of service.
CLIENT_ID: Final[str] = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CLIENT_SECRET: Final[str] = "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"
AUTH_URL: Final[str] = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL: Final[str] = "https://oauth2.googleapis.com/token"
LOAD_CODE_ASSIST_URL: Final[str] = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
FETCH_MODELS_URL: Final[str] = "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels"
SCOPE: Final[str] = "https://www.googleapis.com/auth/cloud-platform https://www.googleapis.com/auth/userinfo.email"
TOKEN_PATH: Final[Path] = Path.home() / ".hermes" / "gemini_cloudcode_token.json"
HTTP_TIMEOUT: Final[int] = 10
OAUTH_TIMEOUT: Final[int] = 300
MAX_RESPONSE_BYTES: Final[int] = 1_048_576

CloudCodeError = Literal["none", "missing_token", "auth", "rate_limited", "forbidden", "network", "json", "http", "unknown"]

logger = logging.getLogger(__name__)
_ERROR_STATE = threading.local()


def _set_error(error: CloudCodeError) -> None:
    _ERROR_STATE.last_error = error


def cloudcode_last_error() -> CloudCodeError:
    """Return the last failure category observed by this module."""
    return getattr(_ERROR_STATE, "last_error", "none")


def cloudcode_token_exists() -> bool:
    """Return True if the Cloud Code OAuth token file is present."""
    return TOKEN_PATH.exists()


def _json_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_number(value: Any) -> float:
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


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        _set_error("missing_token")
        return None
    except (OSError, json.JSONDecodeError) as exc:
        _set_error("auth" if isinstance(exc, json.JSONDecodeError) else "network")
        if isinstance(exc, json.JSONDecodeError):
            _delete_token(path)
        logger.warning("failed to read Gemini Cloud Code token", exc_info=True)
        return None
    if not isinstance(data, dict):
        _set_error("auth")
        _delete_token(path)
        return None
    return data


def _delete_token(path: Path | None = None) -> None:
    target = path or TOKEN_PATH
    try:
        target.unlink(missing_ok=True)
    except OSError:
        logger.warning("failed to delete unusable Gemini Cloud Code token", exc_info=True)


def _save_token(data: dict[str, Any]) -> bool:
    try:
        TOKEN_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp_path = TOKEN_PATH.with_suffix(".json.tmp")
        encoded = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.write(b"\n")
        os.replace(tmp_path, TOKEN_PATH)
        os.chmod(TOKEN_PATH, 0o600)
        return True
    except OSError:
        _set_error("network")
        logger.warning("failed to save Gemini Cloud Code token", exc_info=True)
        return False


def _with_expiry(token_response: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(previous or {})
    merged.update(token_response)
    if "refresh_token" not in merged and previous and isinstance(previous.get("refresh_token"), str):
        merged["refresh_token"] = previous["refresh_token"]
    expires_in = int(_json_number(merged.get("expires_in"))) or 3600
    # Store expiry as epoch seconds; callers only need monotonic comparison with wall time.
    merged["expiry"] = time.time() + max(0, expires_in)
    return merged


def _read_response_json(response: Any) -> dict[str, Any] | None:
    data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        _set_error("http")
        return None
    parsed = json.loads(data)
    if not isinstance(parsed, dict):
        _set_error("json")
        return None
    return parsed


def _post_form(url: str, fields: dict[str, str]) -> dict[str, Any] | None:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT, context=ssl.create_default_context()) as response:
            return _read_response_json(response)
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403):
            _set_error("auth")
        elif exc.code == 429:
            _set_error("rate_limited")
        else:
            _set_error("http")
        logger.warning("Gemini Cloud Code token request failed with HTTP %s", exc.code)
        return None
    except (OSError, TimeoutError):
        _set_error("network")
        logger.warning("Gemini Cloud Code token request failed", exc_info=True)
        return None
    except json.JSONDecodeError:
        _set_error("json")
        logger.warning("Gemini Cloud Code token response was not valid JSON", exc_info=True)
        return None


def _refresh_token(token_data: dict[str, Any]) -> str | None:
    refresh_token = token_data.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        _set_error("auth")
        _delete_token()
        return None

    response = _post_form(
        TOKEN_URL,
        {
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
    )
    if response is None:
        if cloudcode_last_error() == "auth":
            _delete_token()
        return None

    updated = _with_expiry(response, token_data)
    access_token = updated.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        _set_error("auth")
        _delete_token()
        return None
    if not _save_token(updated):
        return None
    _set_error("none")
    return access_token


def cloudcode_get_token() -> str | None:
    """Return a valid access token, refreshing it when it is near expiry."""
    token_data = _read_json_file(TOKEN_PATH)
    if token_data is None:
        return None

    access_token = token_data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        _set_error("auth")
        _delete_token()
        return None

    expiry = _json_number(token_data.get("expiry"))
    if expiry < time.time() + 60:
        return _refresh_token(token_data)

    _set_error("none")
    return access_token


def _post_cloudcode(url: str, payload: dict[str, Any], *, retry_auth: bool = True) -> dict[str, Any] | None:
    access_token = cloudcode_get_token()
    if access_token is None:
        return None

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "antigravity",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT, context=ssl.create_default_context()) as response:
            parsed = _read_response_json(response)
            if parsed is not None:
                _set_error("none")
            return parsed
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            _set_error("auth")
            if retry_auth:
                token_data = _read_json_file(TOKEN_PATH)
                if token_data is not None and _refresh_token(token_data):
                    return _post_cloudcode(url, payload, retry_auth=False)
        elif exc.code == 429:
            _set_error("rate_limited")
        elif exc.code == 403:
            _set_error("forbidden")
        else:
            _set_error("http")
        logger.warning("Gemini Cloud Code API request failed with HTTP %s", exc.code)
        return None
    except (OSError, TimeoutError):
        _set_error("network")
        logger.warning("Gemini Cloud Code API request failed", exc_info=True)
        return None
    except json.JSONDecodeError:
        _set_error("json")
        logger.warning("Gemini Cloud Code API response was not valid JSON", exc_info=True)
        return None


def cloudcode_fetch_plan() -> dict[str, Any] | None:
    """Fetch plan and prompt-credit information from loadCodeAssist."""
    return _post_cloudcode(LOAD_CODE_ASSIST_URL, {})


def cloudcode_fetch_models() -> dict[str, Any] | None:
    """Fetch available model quota information from fetchAvailableModels."""
    return _post_cloudcode(FETCH_MODELS_URL, {})


def _iter_models(data: dict[str, Any] | None, gemini_only: bool) -> list[dict[str, Any]]:
    models = _json_object((data or {}).get("models"))
    parsed: list[dict[str, Any]] = []
    for model_id, raw_model in models.items():
        model = _json_object(raw_model)
        provider = model.get("modelProvider")
        if gemini_only and str(provider).lower() not in {"google", "gemini", "model_provider_google"}:
            continue
        quota = _json_object(model.get("quotaInfo"))
        parsed.append(
            {
                "id": str(model_id),
                "displayName": model.get("displayName") if isinstance(model.get("displayName"), str) else "",
                "label": model.get("label") if isinstance(model.get("label"), str) else "",
                "modelProvider": provider if isinstance(provider, str) else "",
                "remainingFraction": quota.get("remainingFraction"),
                "resetTime": quota.get("resetTime") if isinstance(quota.get("resetTime"), str) else "",
                "isExhausted": bool(quota.get("isExhausted")),
                "raw": model,
            }
        )
    return parsed


def parse_reset_time(iso_str: Any) -> datetime | None:
    """Parse an API reset timestamp as a timezone-aware UTC datetime."""
    if not isinstance(iso_str, str) or not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
    return dt.astimezone(timezone.utc)


def _hours_until(iso_str: Any) -> int | None:
    dt = parse_reset_time(iso_str)
    if dt is None:
        return None
    seconds = (dt - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(round(seconds / 3600)))


def _model_code(model: dict[str, Any]) -> str:
    label = str(model.get("label") or model.get("displayName") or model.get("id") or "G")
    words = [part for part in label.replace("-", " ").replace("_", " ").split() if part]
    if not words:
        return "G"
    if len(words) == 1:
        return words[0][:4]
    return "".join(word[0] for word in words[:4])[:4]


def _best_remaining(models: list[dict[str, Any]]) -> tuple[float | None, str]:
    best_fraction: float | None = None
    best_reset = ""
    for model in models:
        remaining = model.get("remainingFraction")
        if isinstance(remaining, bool) or not isinstance(remaining, int | float):
            continue
        fraction = max(0.0, min(float(remaining), 1.0))
        if best_fraction is None or fraction > best_fraction:
            best_fraction = fraction
            best_reset = str(model.get("resetTime") or "")
    return best_fraction, best_reset


def cloudcode_format_status(gemini_only: bool = True) -> str:
    """Return a short Hermes status string, never raising to callers."""
    try:
        if not cloudcode_token_exists():
            return "G:LOGIN"

        plan = cloudcode_fetch_plan()
        models_data = cloudcode_fetch_models()
        if plan is None and models_data is None:
            last_error = cloudcode_last_error()
            if last_error == "missing_token":
                return "G:LOGIN"
            if last_error in {"auth", "forbidden"}:
                return "G:AUTH"
            return "G:?"

        models = _iter_models(models_data, gemini_only)
        best_fraction, best_reset = _best_remaining(models)
        plan_info = _json_object((plan or {}).get("planInfo"))
        available_credits = (plan or {}).get("availablePromptCredits") if plan else None
        monthly_credits = plan_info.get("monthlyPromptCredits")

        if 0 < len(models) <= 3:
            details = []
            for model in models:
                remaining = model.get("remainingFraction")
                pct = f"{int(max(0.0, min(float(remaining), 1.0)) * 100)}%" if isinstance(remaining, int | float) else "N/A"
                details.append(f"{_model_code(model)}({pct})")
            if details:
                return "G:" + " ".join(details)

        if best_fraction is not None:
            pct = int(best_fraction * 100)
            hours = _hours_until(best_reset)
            suffix = f" ({hours}h)" if hours is not None else ""
            credit_suffix = f" {int(_json_number(available_credits))}cr" if available_credits is not None else ""
            return f"G:{pct}%{suffix}{credit_suffix}"

        if available_credits is not None:
            credits = int(_json_number(available_credits))
            monthly = int(_json_number(monthly_credits))
            return f"G:CREDITS {credits}/{monthly}" if monthly > 0 else f"G:CREDITS {credits}"
        return "G:?"
    except Exception:
        logger.warning("Gemini Cloud Code status formatting failed", exc_info=True)
        return "G:?"


def cloudcode_format_json() -> dict[str, Any]:
    """Return normalized plan/model data for JSON output and debugging."""
    try:
        plan = cloudcode_fetch_plan()
        models_data = cloudcode_fetch_models()
        plan_info = _json_object((plan or {}).get("planInfo"))
        return {
            "ok": plan is not None or models_data is not None,
            "error": cloudcode_last_error() if plan is None and models_data is None else None,
            "plan_type": plan_info.get("planType"),
            "prompt_credits": (plan or {}).get("availablePromptCredits") if plan else None,
            "monthly_prompt_credits": plan_info.get("monthlyPromptCredits"),
            "current_tier": _json_object((plan or {}).get("currentTier")).get("name") if plan else None,
            "models": _iter_models(models_data, gemini_only=False),
            "raw": {"plan": plan, "models": models_data},
        }
    except Exception as exc:
        logger.warning("Gemini Cloud Code JSON formatting failed", exc_info=True)
        return {"ok": False, "error": type(exc).__name__, "models": []}


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    server: "_OAuthServer"

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("OAuth callback: " + format, *args)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if parsed.path != "/callback":
            self.send_error(404)
            return

        state = params.get("state", [""])[0]
        code = params.get("code", [""])[0]
        error = params.get("error", [""])[0]
        if state != self.server.oauth_state:
            self.server.oauth_error = "state_mismatch"
            message = "Gemini Cloud Code login failed."
            body = message.encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.server.oauth_code = code or None
        self.server.oauth_error = error or None
        message = "Gemini Cloud Code login complete. You can close this tab." if code else "Gemini Cloud Code login failed."
        body = message.encode("utf-8")
        self.send_response(200 if code else 400)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _OAuthServer(HTTPServer):
    oauth_code: str | None = None
    oauth_error: str | None = None
    oauth_state: str = ""


def cloudcode_login() -> bool:
    """Run a one-time browser OAuth flow and store Cloud Code tokens."""
    try:
        server = _OAuthServer(("127.0.0.1", 0), _OAuthCallbackHandler)
    except OSError:
        _set_error("network")
        logger.warning("failed to start Gemini Cloud Code OAuth callback server", exc_info=True)
        return False

    try:
        port = int(server.server_address[1])
        redirect_uri = f"http://127.0.0.1:{port}/callback"
        server.oauth_state = secrets.token_urlsafe(32)
        auth_query = urllib.parse.urlencode(
            {
                "client_id": CLIENT_ID,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "state": server.oauth_state,
            }
        )
        webbrowser.open(f"{AUTH_URL}?{auth_query}", new=1, autoraise=True)

        deadline = time.monotonic() + OAUTH_TIMEOUT
        while server.oauth_code is None and server.oauth_error is None and time.monotonic() < deadline:
            server.timeout = min(1.0, max(0.0, deadline - time.monotonic()))
            server.handle_request()

        if not server.oauth_code:
            _set_error("auth")
            return False

        token_response = _post_form(
            TOKEN_URL,
            {
                "code": server.oauth_code,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_response is None:
            return False
        token_data = _with_expiry(token_response)
        if not isinstance(token_data.get("access_token"), str) or not isinstance(token_data.get("refresh_token"), str):
            _set_error("auth")
            return False
        if not _save_token(token_data):
            return False
        _set_error("none")
        return True
    except Exception:
        _set_error("unknown")
        logger.warning("Gemini Cloud Code OAuth login failed", exc_info=True)
        return False
    finally:
        server.server_close()


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gemini Cloud Code quota helper")
    parser.add_argument("--login", action="store_true", help="run browser OAuth login")
    parser.add_argument("--json", action="store_true", help="print normalized Cloud Code quota JSON")
    args = parser.parse_args(argv)

    if args.login:
        return 0 if cloudcode_login() else 1
    if args.json:
        print(json.dumps(cloudcode_format_json(), indent=2, sort_keys=True))
    else:
        print(cloudcode_format_status())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
