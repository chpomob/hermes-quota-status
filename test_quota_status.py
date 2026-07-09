from __future__ import annotations

import importlib.util
import json
import tempfile
import time
import urllib.error
import urllib.request
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch


PLUGIN_PATH = Path(__file__).with_name("__init__.py")
CLOUDCODE_PATH = Path(__file__).with_name("gemini_cloudcode.py")


def load_plugin() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hermes_quota_status_test", PLUGIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load plugin module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_cloudcode() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gemini_cloudcode_test", CLOUDCODE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load Cloud Code module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HermesQuotaStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = load_plugin()

    def test_fetch_claude_missing_token_returns_none(self) -> None:
        with patch.object(self.plugin, "fetch_claude_quota", return_value=None):
            self.assertIsNone(self.plugin._fetch_claude())

    def test_fetch_codex_missing_token_returns_none(self) -> None:
        with patch.object(self.plugin, "fetch_codex_quota", return_value=None):
            self.assertIsNone(self.plugin._fetch_codex())

    def test_fetch_gemini_missing_key_returns_none(self) -> None:
        with (
            patch.object(self.plugin, "_fetch_gemini_agy", return_value=None),
            patch.object(self.plugin, "fetch_gemini_quota", return_value=None),
            patch("gemini_cloudcode.cloudcode_token_exists", return_value=False),
        ):
            self.assertIsNone(self.plugin._fetch_gemini())

    def test_fetch_codex_maps_shared_quota_shape(self) -> None:
        with patch.object(
            self.plugin,
            "fetch_codex_quota",
            return_value={"session_pct": None, "session_reset": "", "weekly_pct": 0.0, "weekly_reset": ""},
        ):
            result = self.plugin._fetch_codex()

        self.assertIsNotNone(result)
        self.assertEqual(result["session_pct"], 0.0)
        self.assertEqual(result["reset_iso"], "")

    def test_fetch_claude_does_not_inflate_percent_utilization(self) -> None:
        with patch.object(
            self.plugin,
            "fetch_claude_quota",
            return_value={"session_pct": 1.0, "session_reset": "2026-06-02T18:10:00Z"},
        ):
            result = self.plugin._fetch_claude()

        self.assertIsNotNone(result)
        self.assertEqual(result["session_pct"], 1.0)
        self.assertEqual(result["reset_iso"], "2026-06-02T18:10:00Z")

    def test_fetch_gemini_maps_probe_shape(self) -> None:
        with patch.object(self.plugin, "_fetch_gemini_agy", return_value=None), patch.object(
            self.plugin.gemini_cloudcode, "cloudcode_token_exists", return_value=False
        ), patch.object(
            self.plugin,
            "fetch_gemini_quota",
            return_value={
                "key_valid": True,
                "available_models": ["gemini-2.5-flash", 42],
                "model_count": 1,
                "probe": {"status": 200},
            },
        ):
            result = self.plugin._fetch_gemini()

        self.assertIsNotNone(result)
        self.assertTrue(result["key_valid"])
        self.assertEqual(result["available_models"], ["gemini-2.5-flash"])
        self.assertEqual(result["probe"], {"status": 200})

    def test_fetch_gemini_cloudcode_maps_best_remaining_fraction(self) -> None:
        with patch.object(self.plugin.gemini_cloudcode, "cloudcode_token_exists", return_value=True), patch.object(
            self.plugin.gemini_cloudcode,
            "cloudcode_format_json",
            return_value={
                "ok": True,
                "plan_type": "PRO",
                "prompt_credits": 600,
                "monthly_prompt_credits": 1500,
                "models": [
                    {
                        "id": "gemini-2.5-flash",
                        "modelProvider": "google",
                        "remainingFraction": 0.4,
                        "resetTime": "2026-06-20T00:00:00Z",
                    },
                    {
                        "id": "claude-sonnet",
                        "modelProvider": "anthropic",
                        "remainingFraction": 0.9,
                        "resetTime": "2026-06-21T00:00:00Z",
                    },
                ],
            },
        ):
            result = self.plugin._fetch_gemini_cloudcode()

        self.assertIsNotNone(result)
        self.assertTrue(result["cloudcode"])
        self.assertEqual(result["plan_type"], "PRO")
        self.assertEqual(result["prompt_credits"], 600.0)
        self.assertEqual(result["monthly_prompt_credits"], 1500.0)
        self.assertEqual(result["model_count"], 1)
        self.assertEqual(result["remaining_fraction"], 0.4)
        self.assertEqual(result["reset_iso"], "2026-06-20T00:00:00Z")

    def test_fetch_gemini_falls_back_to_probe_on_cloudcode_auth_error(self) -> None:
        with patch.object(self.plugin, "_fetch_gemini_agy", return_value=None), patch.object(
            self.plugin.gemini_cloudcode, "cloudcode_token_exists", return_value=True
        ), patch.object(
            self.plugin.gemini_cloudcode,
            "cloudcode_format_json",
            return_value={"ok": False, "error": "auth", "models": []},
        ), patch.object(
            self.plugin,
            "fetch_gemini_quota",
            return_value={"key_valid": True, "available_models": ["gemini-2.5-flash"], "model_count": 1, "probe": {"status": 200}},
        ):
            result = self.plugin._fetch_gemini()

        self.assertIsNotNone(result)
        self.assertNotIn("cloudcode", result)
        self.assertTrue(result["key_valid"])
        self.assertEqual(result["probe"], {"status": 200})

    def test_http_error_keeps_cache_marks_stale_and_sets_retry_backoff(self) -> None:
        cached = {"session_pct": 42.0, "reset_iso": ""}
        self.plugin._cache["claude"] = cached
        self.plugin._cache["codex"] = cached
        self.plugin._cache["ts"] = 10.0

        http_error = urllib.error.HTTPError("https://example.test", 500, "error", {}, None)

        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"claude": MagicMock(side_effect=http_error), "codex": MagicMock(side_effect=http_error)},
            clear=True,
        ):
            self.plugin._refresh_cache()

        self.assertEqual(self.plugin._cache["claude"], cached)
        self.assertEqual(self.plugin._cache["codex"], cached)
        self.assertEqual(self.plugin._cache["ts"], 200.0)
        self.assertEqual(self.plugin._cache["next_retry"]["claude"], 200.0 + self.plugin.ERROR_RETRY_TTL)
        self.assertEqual(self.plugin._cache["next_retry"]["codex"], 200.0 + self.plugin.ERROR_RETRY_TTL)
        self.assertIn("claude", self.plugin._cache["stale"])
        self.assertIn("codex", self.plugin._cache["stale"])

    def test_auth_error_clears_provider_without_refetching_successful_provider(self) -> None:
        self.plugin._cache["claude"] = {"session_pct": 99.0, "reset_iso": ""}
        self.plugin._cache["ts"] = 10.0
        auth_error = urllib.error.HTTPError("https://example.test", 401, "unauthorized", {}, None)
        codex_fetcher = MagicMock(return_value={"session_pct": 12.0, "reset_iso": ""})

        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"claude": MagicMock(side_effect=auth_error), "codex": codex_fetcher},
            clear=True,
        ):
            self.plugin._refresh_cache()
            self.plugin._refresh_cache()

        self.assertIsNone(self.plugin._cache["claude"])
        self.assertEqual(self.plugin._cache["codex"], {"session_pct": 12.0, "reset_iso": ""})
        self.assertIn("claude", self.plugin._cache["stale"])
        self.assertEqual(codex_fetcher.call_count, 1)
        self.assertEqual(self.plugin._cache["next_retry"]["claude"], 200.0 + self.plugin.AUTH_RETRY_TTL)
        self.assertEqual(self.plugin._cache["next_retry"]["codex"], 200.0 + self.plugin.CACHE_TTL)

    def test_refreshing_is_reset_after_unexpected_provider_exception(self) -> None:
        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"claude": MagicMock(side_effect=OverflowError("bad epoch"))},
            clear=True,
        ):
            self.plugin._refresh_cache()

        self.assertFalse(self.plugin._cache["refreshing"])
        self.assertIn("claude", self.plugin._cache["stale"])
        self.assertEqual(self.plugin._cache["next_retry"]["claude"], 200.0 + self.plugin.ERROR_RETRY_TTL)

    def test_thread_start_failure_clears_refresh_lock(self) -> None:
        fake_thread = MagicMock()
        fake_thread.start.side_effect = RuntimeError("thread failed")

        with patch.object(self.plugin.threading, "Thread", return_value=fake_thread):
            with self.assertRaises(RuntimeError):
                self.plugin._start_refresh_if_needed()

        self.assertFalse(self.plugin._cache["refreshing"])

    def test_fmt_reset_edge_cases(self) -> None:
        self.assertEqual(self.plugin._fmt_reset(""), "?")
        self.assertEqual(self.plugin._fmt_reset(None), "?")
        self.assertEqual(self.plugin._fmt_hours_until(["bad"]), "?")
        self.assertEqual(self.plugin._fmt_reset("not-a-date"), "?")
        self.assertRegex(self.plugin._fmt_reset("2026-06-02T18:10:00Z"), r"^\d{2}h\d{2}$")

    def test_render_provider_thresholds_and_stale_states(self) -> None:
        cases = [
            (None, False, "🔴 C:?"),
            ({"session_pct": 42.0, "reset_iso": ""}, True, "🔴 C:?"),
            ({"session_pct": 49.9, "reset_iso": ""}, False, "🟢 C:49%"),
            ({"session_pct": 50.0, "reset_iso": ""}, False, "🟡 C:50%"),
            ({"session_pct": 79.9, "reset_iso": ""}, False, "🟡 C:79%"),
            ({"session_pct": 80.0, "reset_iso": ""}, False, "🟡 C:80% ?"),
            ({"session_pct": 99.9, "reset_iso": ""}, False, "🟡 C:99% ?"),
            ({"session_pct": 100.0, "reset_iso": ""}, False, "🔴 C:FULL ?"),
        ]

        for data, stale, expected in cases:
            with self.subTest(data=data, stale=stale):
                self.assertEqual(self.plugin._render_provider("claude", data, stale), expected)

    def test_render_gemini_states(self) -> None:
        cases = [
            (None, False, "🔴 Ge:?"),
            ({"key_valid": False}, False, "🔴 Ge:KEY"),
            ({"key_valid": True, "probe": {"status": 200}}, False, "🟢 Ge:OK"),
            ({"key_valid": True, "probe": {"status": 429}}, False, "🔴 Ge:LIMIT"),
            ({"key_valid": True, "probe": {"status": 403}}, False, "🟡 Ge:403"),
            ({"key_valid": True, "probe": {"status": "error"}}, False, "🟡 Ge:ERR"),
            ({"key_valid": True}, False, "🟢 Ge:KEY"),
        ]

        for data, stale, expected in cases:
            with self.subTest(data=data, stale=stale):
                rendered = self.plugin._render_provider("gemini", data, stale)
                self.assertEqual(rendered, expected)

    def test_render_gemini_cloudcode_percent_and_credits(self) -> None:
        reset = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")

        self.assertEqual(
            self.plugin._render_provider(
                "gemini",
                {
                    "cloudcode": True,
                    "remaining_fraction": 0.6,
                    "reset_iso": reset,
                    "groups": [{"label": "3.5F", "used_pct": 40, "remaining": 0.6, "reset": reset, "model_count": 5}],
                },
                False,
            ),
            "🟢 Ge:40%",
        )
        self.assertEqual(
            self.plugin._render_provider(
                "gemini",
                {
                    "cloudcode": True,
                    "remaining_fraction": 0.0,
                    "groups": [{"label": "3.5F", "used_pct": 100, "remaining": 0.0, "reset": "", "model_count": 5}],
                },
                False,
            ),
            "🔴 Ge:100%",
        )
        self.assertEqual(
            self.plugin._render_provider(
                "gemini",
                {"cloudcode": True, "prompt_credits": 600.0, "monthly_prompt_credits": 1500.0},
                False,
            ),
            "🟡 Ge:CREDITS 600/1500",
        )
        self.assertEqual(
            self.plugin._render_provider("gemini", {"cloudcode": True, "cloudcode_error": "auth"}, False),
            "🔴 Ge:AUTH",
        )

    def test_v2_provider_identity_order_and_cache_initialization(self) -> None:
        expected = ("claude", "codex", "gemini", "glm", "deepseek")
        active_fetchers = ("claude", "codex", "gemini")

        self.assertEqual(self.plugin.PROVIDERS, expected)
        self.assertEqual(tuple(self.plugin._PROVIDERS), active_fetchers)
        self.assertEqual(self.plugin.PROVIDER_SHORT["gemini"], "Ge")
        self.assertEqual(self.plugin.PROVIDER_SHORT["glm"], "GL")
        self.assertEqual(self.plugin.PROVIDER_SHORT["deepseek"], "D")
        for provider in expected:
            self.assertIn(provider, self.plugin._cache)
            self.assertIsNone(self.plugin._cache[provider])
            self.assertEqual(self.plugin._cache["next_retry"][provider], 0.0)

        self.assertEqual(self.plugin._due_providers(0.0), active_fetchers)

    def test_render_does_not_call_refresh_inline(self) -> None:
        with patch.object(self.plugin, "_refresh_cache", side_effect=AssertionError("sync refresh")), patch.object(
            self.plugin, "_start_refresh_if_needed"
        ) as start_refresh:
            rendered = self.plugin.on_status_bar_render()

        start_refresh.assert_called_once_with()
        self.assertEqual(rendered, "🔴 C:? │ 🔴 Cx:? │ 🔴 Ge:?")

    def test_render_catches_unexpected_exception(self) -> None:
        with patch.object(self.plugin, "_start_refresh_if_needed"), patch.object(
            self.plugin, "_render_provider", side_effect=RuntimeError("render failed")
        ):
            self.assertIsNone(self.plugin.on_status_bar_render())


class GeminiCloudCodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cloudcode = load_cloudcode()

    def test_cloudcode_format_status_returns_login_without_token(self) -> None:
        with patch.object(self.cloudcode, "cloudcode_token_exists", return_value=False):
            self.assertEqual(self.cloudcode.cloudcode_format_status(), "G:LOGIN")

    def test_cloudcode_get_token_refreshes_expired_token_and_chmods_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            token_path = Path(tmpdir) / "gemini_cloudcode_token.json"
            token_path.write_text(
                json.dumps({"access_token": "old", "refresh_token": "refresh", "expiry": 100.0}),
                encoding="utf-8",
            )

            with patch.object(self.cloudcode, "TOKEN_PATH", token_path), patch.object(
                self.cloudcode, "_post_form", return_value={"access_token": "new", "expires_in": 3600}
            ), patch.object(self.cloudcode.time, "time", return_value=200.0):
                token = self.cloudcode.cloudcode_get_token()

            self.assertEqual(token, "new")
            saved = json.loads(token_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["access_token"], "new")
            self.assertEqual(saved["refresh_token"], "refresh")
            self.assertEqual(saved["expiry"], 3800.0)
            self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)

    def test_cloudcode_get_token_deletes_revoked_refresh_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            token_path = Path(tmpdir) / "gemini_cloudcode_token.json"
            token_path.write_text(
                json.dumps({"access_token": "old", "refresh_token": "revoked", "expiry": 100.0}),
                encoding="utf-8",
            )

            def fail_refresh(*args, **kwargs):
                self.cloudcode._set_error("auth")
                return None

            with patch.object(self.cloudcode, "TOKEN_PATH", token_path), patch.object(
                self.cloudcode, "_post_form", side_effect=fail_refresh
            ), patch.object(self.cloudcode.time, "time", return_value=200.0):
                token = self.cloudcode.cloudcode_get_token()

            self.assertIsNone(token)
            self.assertFalse(token_path.exists())

    def test_oauth_callback_rejects_state_mismatch(self) -> None:
        server = self.cloudcode._OAuthServer(("127.0.0.1", 0), self.cloudcode._OAuthCallbackHandler)
        server.oauth_state = "expected"
        try:
            thread = self.cloudcode.threading.Thread(target=server.handle_request)
            thread.start()
            port = int(server.server_address[1])
            url = f"http://127.0.0.1:{port}/callback?code=abc&state=wrong"
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(url, timeout=2)
            thread.join(timeout=2)
        finally:
            server.server_close()

        self.assertEqual(raised.exception.code, 400)
        self.assertIsNone(server.oauth_code)
        self.assertEqual(server.oauth_error, "state_mismatch")

    def test_oauth_callback_accepts_matching_state(self) -> None:
        server = self.cloudcode._OAuthServer(("127.0.0.1", 0), self.cloudcode._OAuthCallbackHandler)
        server.oauth_state = "expected"
        try:
            thread = self.cloudcode.threading.Thread(target=server.handle_request)
            thread.start()
            port = int(server.server_address[1])
            url = f"http://127.0.0.1:{port}/callback?code=abc&state=expected"
            with urllib.request.urlopen(url, timeout=2) as response:
                self.assertEqual(response.status, 200)
            thread.join(timeout=2)
        finally:
            server.server_close()

        self.assertEqual(server.oauth_code, "abc")
        self.assertIsNone(server.oauth_error)


if __name__ == "__main__":
    unittest.main()
