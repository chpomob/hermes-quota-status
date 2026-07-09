from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import time
import urllib.error
import urllib.request
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import yaml


PLUGIN_PATH = Path(__file__).with_name("__init__.py")
PLUGIN_METADATA_PATH = Path(__file__).with_name("plugin.yaml")
CLOUDCODE_PATH = Path(__file__).with_name("gemini_cloudcode.py")
QUOTA_API_PATH = Path(__file__).with_name("quota_api.py")
SPEC_PATH = Path(__file__).with_name("spec.md")

ACCEPTANCE_COVERAGE: dict[str, tuple[str, ...]] = {
    "AC1": ("test_acceptance_status_bar_contains_successful_glm_and_deepseek_segments",),
    "AC2": ("test_fetch_glm_quota_uses_raw_non_bearer_authorization",),
    "AC3": ("test_acceptance_glm_missing_credentials_omits_glm_from_successful_status_output",),
    "AC4": ("test_fetch_glm_quota_falls_back_to_open_bigmodel_after_primary_failure",),
    "AC5": ("test_acceptance_status_bar_contains_successful_glm_and_deepseek_segments",),
    "AC6": ("test_fetch_deepseek_balance_uses_exact_url_and_bearer_authorization",),
    "AC7": ("test_acceptance_deepseek_missing_credentials_omits_deepseek_from_successful_status_output",),
    "AC8": ("test_fetch_deepseek_balance_uses_exact_url_and_bearer_authorization",),
    "AC9": ("test_render_gemini_uses_ge_without_standalone_g",),
    "AC10": ("test_render_glm_success_uses_g_quota_segment",),
    "AC11": ("test_render_claude_dual_and_single_windows",),
    "AC12": ("test_render_codex_dual_and_single_windows",),
    "AC13": ("test_render_reads_provider_allowlist_from_snapshot_config",),
    "AC14": ("test_render_reads_provider_allowlist_from_snapshot_config",),
    "AC15": ("test_render_reset_countdown_is_relative_without_absolute_timestamp",),
    "AC16": ("test_render_current_and_expired_resets_as_zero_countdown",),
    "AC17": ("test_render_width_60_trims_to_limit",),
    "AC18": ("test_render_width_60_trims_at_well_formed_segment_boundaries",),
    "AC19": ("test_auth_failure_suppresses_provider_after_third_consecutive_failure",),
    "AC20": ("test_auth_failure_suppression_is_independent_per_provider",),
    "AC21": ("test_suppression_recovery_renders_provider_after_successful_refresh",),
    "AC22": ("test_acceptance_claude_codex_and_gemini_render_one_segment_each_when_available",),
    "AC23": ("test_plugin_metadata_documents_v2_providers_and_config_surface",),
    "AC24": (
        "test_acceptance_status_bar_contains_successful_glm_and_deepseek_segments",
        "test_get_glm_key_prefers_glm_api_key_over_zhipu_api_key",
        "test_fetch_glm_quota_falls_back_to_open_bigmodel_after_primary_failure",
        "test_render_gemini_uses_ge_without_standalone_g",
        "test_render_glm_success_uses_g_quota_segment",
        "test_render_claude_dual_and_single_windows",
        "test_render_codex_dual_and_single_windows",
        "test_render_reads_provider_allowlist_from_snapshot_config",
        "test_render_reset_countdown_is_relative_without_absolute_timestamp",
        "test_render_current_and_expired_resets_as_zero_countdown",
        "test_render_width_60_trims_to_limit",
        "test_auth_failure_suppresses_provider_after_third_consecutive_failure",
        "test_non_auth_failures_leave_auth_failure_counter_unchanged",
        "test_suppression_recovery_renders_provider_after_successful_refresh",
    ),
    "AC25": ("test_fetch_glm_quota_uses_glm_api_key_header_when_both_credentials_are_set",),
    "AC26": ("test_render_glm_uses_quota_data_from_fallback_host",),
    "AC27": ("test_render_claude_dual_and_single_windows",),
    "AC28": ("test_render_codex_dual_and_single_windows",),
    "AC29": ("test_render_provider_allowlist_is_case_sensitive",),
    "AC30": (
        "test_render_width_above_60_does_not_apply_fixed_60_character_cap",
        "test_render_missing_width_does_not_apply_60_character_cap",
    ),
    "AC31": (
        "test_v2_provider_identity_order_and_cache_initialization",
        "test_non_auth_failures_leave_auth_failure_counter_unchanged",
        "test_successful_authenticated_check_resets_auth_failure_counter",
    ),
    "AC32": ("test_suppressed_provider_is_fetched_on_later_refreshes",),
}


ACCEPTANCE_IDS_BY_TEST_METHOD: dict[str, tuple[str, ...]] = {
    "test_acceptance_status_bar_contains_successful_glm_and_deepseek_segments": ("AC1", "AC5", "AC24"),
    "test_fetch_glm_quota_uses_raw_non_bearer_authorization": ("AC2",),
    "test_acceptance_glm_missing_credentials_omits_glm_from_successful_status_output": ("AC3",),
    "test_fetch_glm_quota_falls_back_to_open_bigmodel_after_primary_failure": ("AC4", "AC24"),
    "test_fetch_deepseek_balance_uses_exact_url_and_bearer_authorization": ("AC6", "AC8"),
    "test_acceptance_deepseek_missing_credentials_omits_deepseek_from_successful_status_output": ("AC7",),
    "test_render_gemini_uses_ge_without_standalone_g": ("AC9", "AC24"),
    "test_render_glm_success_uses_g_quota_segment": ("AC10", "AC24"),
    "test_render_claude_dual_and_single_windows": ("AC11", "AC24", "AC27"),
    "test_render_codex_dual_and_single_windows": ("AC12", "AC24", "AC28"),
    "test_render_reads_provider_allowlist_from_snapshot_config": ("AC13", "AC14", "AC24"),
    "test_render_reset_countdown_is_relative_without_absolute_timestamp": ("AC15", "AC24"),
    "test_render_current_and_expired_resets_as_zero_countdown": ("AC16", "AC24"),
    "test_render_width_60_trims_to_limit": ("AC17", "AC24"),
    "test_render_width_60_trims_at_well_formed_segment_boundaries": ("AC18",),
    "test_auth_failure_suppresses_provider_after_third_consecutive_failure": ("AC19", "AC24"),
    "test_auth_failure_suppression_is_independent_per_provider": ("AC20",),
    "test_suppression_recovery_renders_provider_after_successful_refresh": ("AC21", "AC24"),
    "test_acceptance_claude_codex_and_gemini_render_one_segment_each_when_available": ("AC22",),
    "test_plugin_metadata_documents_v2_providers_and_config_surface": ("AC23",),
    "test_get_glm_key_prefers_glm_api_key_over_zhipu_api_key": ("AC24",),
    "test_non_auth_failures_leave_auth_failure_counter_unchanged": ("AC24", "AC31"),
    "test_fetch_glm_quota_uses_glm_api_key_header_when_both_credentials_are_set": ("AC25",),
    "test_render_glm_uses_quota_data_from_fallback_host": ("AC26",),
    "test_render_provider_allowlist_is_case_sensitive": ("AC29",),
    "test_render_width_above_60_does_not_apply_fixed_60_character_cap": ("AC30",),
    "test_render_missing_width_does_not_apply_60_character_cap": ("AC30",),
    "test_v2_provider_identity_order_and_cache_initialization": ("AC31",),
    "test_successful_authenticated_check_resets_auth_failure_counter": ("AC31",),
    "test_suppressed_provider_is_fetched_on_later_refreshes": ("AC32",),
}


def acceptance_ids_from_spec() -> set[str]:
    return set(re.findall(r"^- (AC\d+) \(", SPEC_PATH.read_text(encoding="utf-8"), flags=re.MULTILINE))


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


def load_quota_api() -> ModuleType:
    spec = importlib.util.spec_from_file_location("quota_api_test", QUOTA_API_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load quota API module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PluginMetadataTests(unittest.TestCase):
    def test_plugin_metadata_documents_v2_providers_and_config_surface(self) -> None:
        plugin = load_plugin()
        metadata = yaml.safe_load(PLUGIN_METADATA_PATH.read_text(encoding="utf-8"))

        self.assertIsInstance(metadata, dict)
        self.assertEqual(metadata["version"], "2.0.0")
        description = metadata["description"]
        for provider_display_name in ("Claude", "Codex", "Gemini", "GLM/Zhipu", "DeepSeek"):
            self.assertIn(provider_display_name, description)

        self.assertIn("quota_status.providers", description)
        self.assertIn("case-sensitive provider names", description)
        documented_provider_names = (
            description.split("Valid case-sensitive provider names are ", 1)[1]
            .rstrip(".")
            .replace(", and ", ", ")
            .split(", ")
        )
        self.assertEqual(tuple(documented_provider_names), plugin.PROVIDERS)

        self.assertEqual(metadata["provides_hooks"], ["on_status_bar_render"])


class AcceptanceCoverageAuditTests(unittest.TestCase):
    def test_acceptance_coverage_maps_every_ac_to_existing_unittest_methods(self) -> None:
        expected_ids = acceptance_ids_from_spec()
        self.assertEqual(set(ACCEPTANCE_COVERAGE), expected_ids)

        test_methods = {
            method_name: method
            for obj in globals().values()
            if isinstance(obj, type) and issubclass(obj, unittest.TestCase)
            for method_name, method in obj.__dict__.items()
            if method_name.startswith("test_")
        }
        for acceptance_id, method_names in ACCEPTANCE_COVERAGE.items():
            with self.subTest(acceptance_id=acceptance_id):
                self.assertTrue(method_names)
                for method_name in method_names:
                    self.assertIn(method_name, test_methods)
                    self.assertIn(acceptance_id, ACCEPTANCE_IDS_BY_TEST_METHOD.get(method_name, ()))

        for method_name, acceptance_ids in ACCEPTANCE_IDS_BY_TEST_METHOD.items():
            with self.subTest(method_name=method_name):
                self.assertIn(method_name, test_methods)
                self.assertLessEqual(set(acceptance_ids), expected_ids)
                for acceptance_id in acceptance_ids:
                    self.assertIn(method_name, ACCEPTANCE_COVERAGE[acceptance_id])


class QuotaApiDeepSeekTests(unittest.TestCase):
    def setUp(self) -> None:
        self.quota_api = load_quota_api()

    def test_get_deepseek_key_returns_none_when_unset_or_empty(self) -> None:
        for env in ({}, {"DEEPSEEK_API_KEY": ""}):
            with self.subTest(env=env), patch.dict(self.quota_api.os.environ, env, clear=True):
                self.assertIsNone(self.quota_api.get_deepseek_key())

    def test_fetch_deepseek_balance_missing_credentials_returns_none(self) -> None:
        with patch.dict(self.quota_api.os.environ, {}, clear=True), patch.object(
            self.quota_api, "fetch_json"
        ) as fetch_json:
            self.assertIsNone(self.quota_api.fetch_deepseek_balance())

        fetch_json.assert_not_called()

    def test_fetch_deepseek_balance_success_normalizes_balance_shape(self) -> None:
        with patch.dict(self.quota_api.os.environ, {"DEEPSEEK_API_KEY": "deepseek-raw"}, clear=True), patch.object(
            self.quota_api,
            "fetch_json",
            return_value={
                "is_available": True,
                "balance_infos": [
                    {
                        "currency": "CNY",
                        "total_balance": "110.00",
                        "granted_balance": "10.00",
                        "topped_up_balance": "100.00",
                    }
                ],
            },
        ):
            result = self.quota_api.fetch_deepseek_balance()

        self.assertIsNotNone(result)
        self.assertTrue(result["is_available"])
        self.assertEqual(result["currency"], "CNY")
        self.assertEqual(result["balance"], 110.0)
        self.assertEqual(result["total_balance"], 110.0)
        self.assertEqual(result["granted_balance"], 10.0)
        self.assertEqual(result["topped_up_balance"], 100.0)
        self.assertEqual(result["total_balance_display"], "110.00")
        self.assertEqual(
            result["balances"],
            [
                {
                    "currency": "CNY",
                    "total_balance": 110.0,
                    "total_balance_display": "110.00",
                    "granted_balance": 10.0,
                    "granted_balance_display": "10.00",
                    "topped_up_balance": 100.0,
                    "topped_up_balance_display": "100.00",
                }
            ],
        )

    def test_fetch_deepseek_balance_uses_exact_url_and_bearer_authorization(self) -> None:
        requests = []

        def fake_fetch(req):
            requests.append(req)
            return {
                "is_available": True,
                "balance_infos": [{"currency": "USD", "total_balance": "3.50"}],
            }

        with patch.dict(self.quota_api.os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=True), patch.object(
            self.quota_api, "fetch_json", side_effect=fake_fetch
        ):
            self.quota_api.fetch_deepseek_balance()

        self.assertEqual(requests[0].full_url, "https://api.deepseek.com/user/balance")
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer sk-test")

    def test_fetch_deepseek_balance_defensively_parses_amounts_and_currency(self) -> None:
        with patch.dict(self.quota_api.os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=True), patch.object(
            self.quota_api,
            "fetch_json",
            return_value={
                "is_available": "yes",
                "balance_infos": [
                    {"currency": "CNY", "total_balance": {"value": "7.25"}},
                    {"currency": 123, "total_balance": {"amount": "bad"}, "granted_balance": {"amount": "1.25"}},
                    "ignored",
                    {"currency": "USD", "total_balance": {"amount": "2.50"}, "topped_up_balance": "2.00"},
                ],
            },
        ):
            result = self.quota_api.fetch_deepseek_balance()

        self.assertIsNotNone(result)
        self.assertFalse(result["is_available"])
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["balance"], 2.5)
        self.assertEqual(result["total_balance_display"], "2.50")
        self.assertEqual(result["topped_up_balance"], 2.0)
        self.assertEqual(result["balances"][1]["currency"], "")
        self.assertEqual(result["balances"][1]["granted_balance"], 1.25)

    def test_fetch_deepseek_balance_malformed_authenticated_response_raises_value_error(self) -> None:
        cases = (
            {"is_available": True},
            {"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "n/a"}]},
        )
        for payload in cases:
            with self.subTest(payload=payload), patch.dict(
                self.quota_api.os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=True
            ), patch.object(self.quota_api, "fetch_json", return_value=payload):
                with self.assertRaises(ValueError):
                    self.quota_api.fetch_deepseek_balance()


class QuotaApiClaudeCodexWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.quota_api = load_quota_api()

    def test_fetch_claude_quota_preserves_dual_windows(self) -> None:
        with patch.object(self.quota_api, "get_claude_token", return_value="claude-token"), patch.object(
            self.quota_api,
            "fetch_json",
            return_value={
                "five_hour": {"utilization": "18", "resets_at": "2026-06-02T18:10:00Z"},
                "seven_day": {"utilization": 57, "resets_at": "2026-06-08T18:10:00Z"},
            },
        ):
            result = self.quota_api.fetch_claude_quota()

        self.assertIsNotNone(result)
        self.assertEqual(result["session_pct"], 18.0)
        self.assertEqual(result["session_reset"], "2026-06-02T18:10:00Z")
        self.assertEqual(result["weekly_pct"], 57.0)
        self.assertEqual(result["weekly_reset"], "2026-06-08T18:10:00Z")

    def test_fetch_claude_quota_omits_missing_window(self) -> None:
        with patch.object(self.quota_api, "get_claude_token", return_value="claude-token"), patch.object(
            self.quota_api,
            "fetch_json",
            return_value={"seven_day": {"utilization": 57, "resets_at": "2026-06-08T18:10:00Z"}},
        ):
            result = self.quota_api.fetch_claude_quota()

        self.assertIsNotNone(result)
        self.assertNotIn("session_pct", result)
        self.assertNotIn("session_reset", result)
        self.assertEqual(result["weekly_pct"], 57.0)

    def test_fetch_codex_quota_preserves_dual_windows(self) -> None:
        with patch.object(self.quota_api, "get_codex_token", return_value="codex-token"), patch.object(
            self.quota_api,
            "fetch_json",
            return_value={
                "rate_limit": {
                    "primary_window": {"used_percent": "12", "reset_at": 1777819631},
                    "secondary_window": {"used_percent": 48, "reset_at": 1778262784},
                }
            },
        ):
            result = self.quota_api.fetch_codex_quota()

        self.assertIsNotNone(result)
        self.assertEqual(result["session_pct"], 12.0)
        self.assertEqual(result["session_reset"], "2026-05-03T14:47:11Z")
        self.assertEqual(result["weekly_pct"], 48.0)
        self.assertEqual(result["weekly_reset"], "2026-05-08T17:53:04Z")

    def test_fetch_codex_quota_omits_missing_window(self) -> None:
        with patch.object(self.quota_api, "get_codex_token", return_value="codex-token"), patch.object(
            self.quota_api,
            "fetch_json",
            return_value={"rate_limit": {"secondary_window": {"used_percent": 48, "reset_at": 1778262784}}},
        ):
            result = self.quota_api.fetch_codex_quota()

        self.assertIsNotNone(result)
        self.assertNotIn("session_pct", result)
        self.assertNotIn("session_reset", result)
        self.assertEqual(result["weekly_pct"], 48.0)


class QuotaApiGlmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.quota_api = load_quota_api()

    def test_fetch_glm_quota_missing_credentials_returns_none(self) -> None:
        with patch.dict(self.quota_api.os.environ, {}, clear=True), patch.object(
            self.quota_api, "fetch_json"
        ) as fetch_json:
            self.assertIsNone(self.quota_api.fetch_glm_quota())

        fetch_json.assert_not_called()

    def test_fetch_glm_quota_success_normalizes_quota_shape(self) -> None:
        requests = []

        def fake_fetch(req):
            requests.append(req)
            return {
                "data": {
                    "availableLimitPercentage": "72",
                    "remainingFraction": "0.72",
                    "balance": "18.5",
                    "resetTime": "2026-07-10T00:00:00Z",
                }
            }

        with patch.dict(self.quota_api.os.environ, {"GLM_API_KEY": "glm-raw"}, clear=True), patch.object(
            self.quota_api, "fetch_json", side_effect=fake_fetch
        ):
            result = self.quota_api.fetch_glm_quota()

        self.assertIsNotNone(result)
        self.assertEqual(requests[0].full_url, "https://api.z.ai/api/monitor/usage/quota/limit")
        self.assertEqual(requests[0].get_header("Authorization"), "glm-raw")
        self.assertEqual(result["host"], "https://api.z.ai")
        self.assertEqual(result["available_limit_pct"], 72.0)
        self.assertEqual(result["remaining_fraction"], 0.72)
        self.assertEqual(result["balance"], 18.5)
        self.assertEqual(result["session_pct"], 28.0)
        self.assertEqual(result["session_reset"], "2026-07-10T00:00:00Z")
        self.assertEqual(result["reset_iso"], "2026-07-10T00:00:00Z")

    def test_get_glm_key_prefers_glm_api_key_over_zhipu_api_key(self) -> None:
        with patch.dict(
            self.quota_api.os.environ,
            {"GLM_API_KEY": "glm-raw", "ZHIPU_API_KEY": "zhipu-raw"},
            clear=True,
        ):
            self.assertEqual(self.quota_api.get_glm_key(), "glm-raw")

    def test_get_glm_key_falls_back_to_zhipu_when_glm_empty_or_unset(self) -> None:
        cases = (
            {"ZHIPU_API_KEY": "zhipu-raw"},
            {"GLM_API_KEY": "", "ZHIPU_API_KEY": "zhipu-raw"},
        )
        for env in cases:
            with self.subTest(env=env), patch.dict(self.quota_api.os.environ, env, clear=True):
                self.assertEqual(self.quota_api.get_glm_key(), "zhipu-raw")

    def test_fetch_glm_quota_uses_raw_non_bearer_authorization(self) -> None:
        requests = []

        def fake_fetch(req):
            requests.append(req)
            return {"data": {"availableLimitPercentage": 100}}

        with patch.dict(self.quota_api.os.environ, {"ZHIPU_API_KEY": "raw-zhipu-key"}, clear=True), patch.object(
            self.quota_api, "fetch_json", side_effect=fake_fetch
        ):
            self.quota_api.fetch_glm_quota()

        self.assertEqual(requests[0].get_header("Authorization"), "raw-zhipu-key")
        self.assertNotIn("Bearer", requests[0].get_header("Authorization"))

    def test_fetch_glm_quota_uses_glm_api_key_header_when_both_credentials_are_set(self) -> None:
        requests = []

        def fake_fetch(req):
            requests.append(req)
            return {"data": {"availableLimitPercentage": 100}}

        with patch.dict(
            self.quota_api.os.environ,
            {"GLM_API_KEY": "glm-raw", "ZHIPU_API_KEY": "zhipu-raw"},
            clear=True,
        ), patch.object(self.quota_api, "fetch_json", side_effect=fake_fetch):
            self.quota_api.fetch_glm_quota()

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].get_header("Authorization"), "glm-raw")

    def test_fetch_glm_quota_falls_back_to_open_bigmodel_after_primary_failure(self) -> None:
        urls = []

        def fake_fetch(req):
            urls.append(req.full_url)
            if len(urls) == 1:
                raise urllib.error.URLError("primary unavailable")
            return {"data": {"availableLimitPercentage": 64}}

        with patch.dict(self.quota_api.os.environ, {"GLM_API_KEY": "glm-raw"}, clear=True), patch.object(
            self.quota_api, "fetch_json", side_effect=fake_fetch
        ):
            result = self.quota_api.fetch_glm_quota()

        self.assertIsNotNone(result)
        self.assertEqual(
            urls,
            [
                "https://api.z.ai/api/monitor/usage/quota/limit",
                "https://open.bigmodel.cn/api/monitor/usage/quota/limit",
            ],
        )
        self.assertEqual(result["host"], "https://open.bigmodel.cn")
        self.assertEqual(result["session_pct"], 36.0)

    def test_fetch_glm_quota_falls_back_after_primary_auth_error(self) -> None:
        urls = []
        auth_error = urllib.error.HTTPError(
            "https://api.z.ai/api/monitor/usage/quota/limit",
            401,
            "unauthorized",
            {},
            None,
        )

        def fake_fetch(req):
            urls.append(req.full_url)
            if len(urls) == 1:
                raise auth_error
            return {"data": {"availableLimitPercentage": 91}}

        with patch.dict(self.quota_api.os.environ, {"GLM_API_KEY": "mainland-key"}, clear=True), patch.object(
            self.quota_api, "fetch_json", side_effect=fake_fetch
        ):
            result = self.quota_api.fetch_glm_quota()

        self.assertIsNotNone(result)
        self.assertEqual(
            urls,
            [
                "https://api.z.ai/api/monitor/usage/quota/limit",
                "https://open.bigmodel.cn/api/monitor/usage/quota/limit",
            ],
        )
        self.assertEqual(result["host"], "https://open.bigmodel.cn")
        self.assertEqual(result["session_pct"], 9.0)

    def test_fetch_glm_quota_raises_auth_error_after_all_hosts_fail_auth(self) -> None:
        urls = []

        def fake_fetch(req):
            urls.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, None)

        with patch.dict(self.quota_api.os.environ, {"GLM_API_KEY": "bad-key"}, clear=True), patch.object(
            self.quota_api, "fetch_json", side_effect=fake_fetch
        ):
            with self.assertRaises(urllib.error.HTTPError):
                self.quota_api.fetch_glm_quota()

        self.assertEqual(
            urls,
            [
                "https://api.z.ai/api/monitor/usage/quota/limit",
                "https://open.bigmodel.cn/api/monitor/usage/quota/limit",
            ],
        )

    def test_fetch_glm_quota_supports_bigmodel_limit_shape(self) -> None:
        with patch.dict(self.quota_api.os.environ, {"GLM_API_KEY": "glm-raw"}, clear=True), patch.object(
            self.quota_api,
            "fetch_json",
            return_value={
                "data": {
                    "limits": [
                        {"type": "TIME_LIMIT", "unit": 5, "percentage": 0, "nextResetTime": 1780336384978},
                        {"type": "TOKENS_LIMIT", "unit": 3, "percentage": 16, "nextResetTime": 1777819631597},
                        {"type": "TOKENS_LIMIT", "unit": 6, "percentage": 4, "nextResetTime": 1778262784969},
                    ]
                }
            },
        ):
            result = self.quota_api.fetch_glm_quota()

        self.assertIsNotNone(result)
        self.assertEqual(result["session_pct"], 16.0)
        self.assertEqual(result["session_reset"], "2026-05-03T14:47:11Z")
        self.assertEqual(result["reset_iso"], "2026-05-03T14:47:11Z")

    def test_fetch_glm_quota_balance_only_leaves_percentage_unknown(self) -> None:
        with patch.dict(self.quota_api.os.environ, {"GLM_API_KEY": "glm-raw"}, clear=True), patch.object(
            self.quota_api,
            "fetch_json",
            return_value={"data": {"balance": 0.01}},
        ):
            result = self.quota_api.fetch_glm_quota()

        self.assertIsNotNone(result)
        self.assertNotIn("session_pct", result)
        self.assertEqual(result["balance"], 0.01)

    def test_render_glm_uses_quota_data_from_fallback_host(self) -> None:
        plugin = load_plugin()
        calls = []

        def fake_fetch(req):
            calls.append(req.full_url)
            if len(calls) == 1:
                return {"error": "malformed primary response"}
            return {"data": {"availableLimitPercentage": 91}}

        with patch.dict(self.quota_api.os.environ, {"GLM_API_KEY": "glm-raw"}, clear=True), patch.object(
            self.quota_api, "fetch_json", side_effect=fake_fetch
        ):
            result = self.quota_api.fetch_glm_quota()

        self.assertIsNotNone(result)
        self.assertEqual(result["host"], "https://open.bigmodel.cn")
        self.assertEqual(plugin._render_provider("glm", result, False), "🟢 G:9%")


class HermesQuotaStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = load_plugin()

    def _seed_all_provider_cache(self) -> None:
        self.plugin._cache["claude"] = {"session_pct": 24.0, "reset_iso": ""}
        self.plugin._cache["codex"] = {"session_pct": 11.0, "reset_iso": ""}
        self.plugin._cache["gemini"] = {"key_valid": True, "probe": {"status": 200}}
        self.plugin._cache["glm"] = {"session_pct": 28.0, "reset_iso": ""}
        self.plugin._cache["deepseek"] = {
            "is_available": True,
            "currency": "USD",
            "balance": 3.5,
            "total_balance": 3.5,
            "total_balance_display": "3.50",
        }
        self.plugin._cache["stale"].clear()

    def _seed_wide_provider_cache(self) -> None:
        self.plugin._cache["claude"] = {
            "windows": [
                {"name": "five_hour", "label": "5h", "pct": 24.0, "reset_iso": ""},
                {"name": "seven_day", "label": "7d", "pct": 61.0, "reset_iso": ""},
            ]
        }
        self.plugin._cache["codex"] = {
            "windows": [
                {"name": "primary", "label": "P", "pct": 22.0, "reset_iso": ""},
                {"name": "secondary", "label": "S", "pct": 63.0, "reset_iso": ""},
            ]
        }
        self.plugin._cache["gemini"] = {"cloudcode": True, "prompt_credits": 600.0, "monthly_prompt_credits": 1500.0}
        self.plugin._cache["glm"] = {"session_pct": 28.0, "reset_iso": ""}
        self.plugin._cache["deepseek"] = {
            "is_available": True,
            "currency": "USD",
            "balance": 3.5,
            "total_balance": 3.5,
            "total_balance_display": "3.50",
        }
        self.plugin._cache["stale"].clear()

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
        self.assertNotIn("session_pct", result)
        self.assertEqual(result["weekly_pct"], 0.0)
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

    def test_fetch_claude_preserves_dual_windows_for_rendering(self) -> None:
        with patch.object(
            self.plugin,
            "fetch_claude_quota",
            return_value={
                "session_pct": 24.0,
                "session_reset": "2026-06-02T18:10:00Z",
                "weekly_pct": 61.0,
                "weekly_reset": "2026-06-08T18:10:00Z",
            },
        ):
            result = self.plugin._fetch_claude()

        self.assertIsNotNone(result)
        self.assertEqual(result["session_pct"], 24.0)
        self.assertEqual(result["weekly_pct"], 61.0)
        self.assertEqual(
            result["windows"],
            [
                {"name": "five_hour", "label": "5h", "pct": 24.0, "reset_iso": "2026-06-02T18:10:00Z"},
                {"name": "seven_day", "label": "7d", "pct": 61.0, "reset_iso": "2026-06-08T18:10:00Z"},
            ],
        )
        rendered = self.plugin._render_provider("claude", result, False)
        self.assertIn("5h:24%", rendered)
        self.assertIn("7d:61%", rendered)

    def test_fetch_claude_preserves_five_hour_only_window_for_rendering(self) -> None:
        with patch.object(
            self.plugin,
            "fetch_claude_quota",
            return_value={"session_pct": 23.0, "session_reset": "2026-06-02T18:10:00Z"},
        ):
            result = self.plugin._fetch_claude()

        self.assertIsNotNone(result)
        self.assertEqual(result["session_pct"], 23.0)
        self.assertNotIn("weekly_pct", result)
        rendered = self.plugin._render_provider("claude", result, False)
        self.assertIn("5h:23%", rendered)
        self.assertNotIn("7d", rendered)

    def test_fetch_claude_preserves_seven_day_only_window_for_rendering(self) -> None:
        with patch.object(
            self.plugin,
            "fetch_claude_quota",
            return_value={"weekly_pct": 64.0, "weekly_reset": "2026-06-08T18:10:00Z"},
        ):
            result = self.plugin._fetch_claude()

        self.assertIsNotNone(result)
        self.assertNotIn("session_pct", result)
        self.assertEqual(result["weekly_pct"], 64.0)
        self.assertEqual(result["reset_iso"], "")
        rendered = self.plugin._render_provider("claude", result, False)
        self.assertIn("7d:64%", rendered)
        self.assertNotIn("5h", rendered)

    def test_fetch_codex_preserves_dual_windows_for_rendering(self) -> None:
        with patch.object(
            self.plugin,
            "fetch_codex_quota",
            return_value={
                "session_pct": 22.0,
                "session_reset": "2026-06-02T18:10:00Z",
                "weekly_pct": 63.0,
                "weekly_reset": "2026-06-08T18:10:00Z",
            },
        ):
            result = self.plugin._fetch_codex()

        self.assertIsNotNone(result)
        self.assertEqual(result["session_pct"], 22.0)
        self.assertEqual(result["weekly_pct"], 63.0)
        self.assertEqual(
            result["windows"],
            [
                {"name": "primary", "label": "P", "pct": 22.0, "reset_iso": "2026-06-02T18:10:00Z"},
                {"name": "secondary", "label": "S", "pct": 63.0, "reset_iso": "2026-06-08T18:10:00Z"},
            ],
        )
        rendered = self.plugin._render_provider("codex", result, False)
        self.assertIn("P:22%", rendered)
        self.assertIn("S:63%", rendered)

    def test_fetch_codex_preserves_primary_only_window_for_rendering(self) -> None:
        with patch.object(
            self.plugin,
            "fetch_codex_quota",
            return_value={"session_pct": 32.0, "session_reset": "2026-06-02T18:10:00Z"},
        ):
            result = self.plugin._fetch_codex()

        self.assertIsNotNone(result)
        self.assertEqual(result["session_pct"], 32.0)
        self.assertNotIn("weekly_pct", result)
        rendered = self.plugin._render_provider("codex", result, False)
        self.assertIn("P:32%", rendered)
        self.assertNotIn("S:", rendered)

    def test_fetch_codex_preserves_secondary_only_window_for_rendering(self) -> None:
        with patch.object(
            self.plugin,
            "fetch_codex_quota",
            return_value={"weekly_pct": 41.0, "weekly_reset": "2026-06-08T18:10:00Z"},
        ):
            result = self.plugin._fetch_codex()

        self.assertIsNotNone(result)
        self.assertNotIn("session_pct", result)
        self.assertEqual(result["weekly_pct"], 41.0)
        rendered = self.plugin._render_provider("codex", result, False)
        self.assertIn("S:41%", rendered)
        self.assertNotIn("P:", rendered)

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
                self.plugin._start_refresh_if_needed(self.plugin.PROVIDERS)

        self.assertFalse(self.plugin._cache["refreshing"])

    def test_fmt_reset_edge_cases(self) -> None:
        self.assertEqual(self.plugin._fmt_reset(""), "?")
        self.assertEqual(self.plugin._fmt_reset(None), "?")
        self.assertEqual(self.plugin._fmt_hours_until(["bad"]), "?")
        self.assertEqual(self.plugin._fmt_reset("not-a-date"), "?")
        base = datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc)
        future = (base + timedelta(hours=3, minutes=42)).isoformat().replace("+00:00", "Z")
        multi_day = (base + timedelta(days=6, hours=12)).isoformat().replace("+00:00", "Z")
        current = base.isoformat().replace("+00:00", "Z")
        expired = (base - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        with patch.object(self.plugin, "_now_utc", return_value=base):
            self.assertEqual(self.plugin._fmt_reset(future), "3h42m")
            self.assertEqual(self.plugin._fmt_reset(multi_day), "6d12h")
            self.assertEqual(self.plugin._fmt_reset(current), "0h0m")
            self.assertEqual(self.plugin._fmt_reset(expired), "0h0m")

    def test_render_provider_thresholds_and_stale_states(self) -> None:
        cases = [
            (None, False, "🔴 C:?"),
            ({"session_pct": 42.0, "reset_iso": ""}, True, "🔴 C:?"),
            ({"session_pct": 49.9, "reset_iso": ""}, False, "🟢 C:49%"),
            ({"session_pct": 50.0, "reset_iso": ""}, False, "🟡 C:50%"),
            ({"session_pct": 79.9, "reset_iso": ""}, False, "🟡 C:79%"),
            ({"session_pct": 80.0, "reset_iso": ""}, False, "🟡 C:80%"),
            ({"session_pct": 99.9, "reset_iso": ""}, False, "🟡 C:99%"),
            ({"session_pct": 100.0, "reset_iso": ""}, False, "🔴 C:FULL"),
        ]

        for data, stale, expected in cases:
            with self.subTest(data=data, stale=stale):
                self.assertEqual(self.plugin._render_provider("claude", data, stale), expected)

    def test_render_glm_success_uses_g_quota_segment(self) -> None:
        self.assertEqual(
            self.plugin._render_provider(
                "glm",
                {"session_pct": 28.0, "session_reset": "2026-07-10T00:00:00Z", "reset_iso": "2026-07-10T00:00:00Z"},
                False,
            ),
            "🟢 G:28%",
        )

    def test_render_glm_balance_only_uses_balance_segment(self) -> None:
        self.assertEqual(
            self.plugin._render_provider(
                "glm",
                {"host": "https://open.bigmodel.cn", "balance": 12.5, "reset_iso": None, "session_reset": None},
                False,
            ),
            "🟢 G:12.50",
        )

    def test_render_glm_derives_quota_from_raw_available_fields(self) -> None:
        self.assertEqual(
            self.plugin._render_provider(
                "glm",
                {"available_limit_pct": 25.0, "reset_iso": ""},
                False,
            ),
            "🟡 G:75%",
        )
        self.assertEqual(
            self.plugin._render_provider(
                "glm",
                {"remaining_fraction": 0.8, "reset_iso": ""},
                False,
            ),
            "🟢 G:20%",
        )

    def test_render_deepseek_success_uses_balance_segment(self) -> None:
        self.assertEqual(
            self.plugin._render_provider(
                "deepseek",
                {
                    "is_available": True,
                    "currency": "USD",
                    "balance": 3.5,
                    "total_balance": 3.5,
                    "total_balance_display": "3.50",
                },
                False,
            ),
            "🟢 D:$3.50",
        )

    def test_render_deepseek_uses_granted_balance_when_total_is_missing(self) -> None:
        self.assertEqual(
            self.plugin._render_provider(
                "deepseek",
                {
                    "is_available": True,
                    "currency": "USD",
                    "granted_balance": 5.0,
                    "granted_balance_display": "5.00",
                },
                False,
            ),
            "🟢 D:$5.00",
        )

    def test_render_gemini_uses_ge_without_standalone_g(self) -> None:
        rendered = self.plugin._render_provider("gemini", {"key_valid": True, "probe": {"status": 200}}, False)

        self.assertEqual(rendered, "🟢 Ge:OK")
        self.assertNotIn(" G:", rendered)

    def test_render_claude_dual_and_single_windows(self) -> None:
        dual = self.plugin._render_provider(
            "claude",
            {
                "windows": [
                    {"name": "five_hour", "label": "5h", "pct": 24.0, "reset_iso": ""},
                    {"name": "seven_day", "label": "7d", "pct": 61.0, "reset_iso": ""},
                ]
            },
            False,
        )
        single = self.plugin._render_provider(
            "claude",
            {"windows": [{"name": "five_hour", "label": "5h", "pct": 23.0, "reset_iso": ""}]},
            False,
        )

        self.assertIn("C:", dual)
        self.assertIn("5h:24%", dual)
        self.assertIn("7d:61%", dual)
        self.assertIn("C:", single)
        self.assertIn("5h:23%", single)
        self.assertNotIn("7d", single)

    def test_render_codex_dual_and_single_windows(self) -> None:
        dual = self.plugin._render_provider(
            "codex",
            {
                "windows": [
                    {"name": "primary", "label": "P", "pct": 22.0, "reset_iso": ""},
                    {"name": "secondary", "label": "S", "pct": 63.0, "reset_iso": ""},
                ]
            },
            False,
        )
        single = self.plugin._render_provider(
            "codex",
            {"windows": [{"name": "secondary", "label": "S", "pct": 41.0, "reset_iso": ""}]},
            False,
        )

        self.assertIn("Cx:", dual)
        self.assertIn("P:22%", dual)
        self.assertIn("S:63%", dual)
        self.assertIn("Cx:", single)
        self.assertIn("S:41%", single)
        self.assertNotIn("P:", single)

    def test_render_reset_countdown_is_relative_without_absolute_timestamp(self) -> None:
        base = datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc)
        future = (base + timedelta(hours=3, minutes=42)).isoformat().replace("+00:00", "Z")

        with patch.object(self.plugin, "_now_utc", return_value=base):
            rendered = self.plugin._render_provider("claude", {"session_pct": 100.0, "reset_iso": future}, False)

        self.assertEqual(rendered, "🔴 C:FULL 3h42m")
        self.assertNotIn("2026", rendered)
        self.assertNotIn("18h", rendered)

    def test_render_current_and_expired_resets_as_zero_countdown(self) -> None:
        base = datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc)
        current = base.isoformat().replace("+00:00", "Z")
        expired = (base - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")

        with patch.object(self.plugin, "_now_utc", return_value=base):
            current_rendered = self.plugin._render_provider("claude", {"session_pct": 100.0, "reset_iso": current}, False)
            expired_rendered = self.plugin._render_provider("claude", {"session_pct": 100.0, "reset_iso": expired}, False)

        self.assertEqual(current_rendered, "🔴 C:FULL 0h0m")
        self.assertEqual(expired_rendered, "🔴 C:FULL 0h0m")
        self.assertNotIn("14h00", current_rendered)
        self.assertNotIn("13h59", expired_rendered)

    def test_render_provider_omits_missing_reset_for_explicit_windows(self) -> None:
        rendered = self.plugin._render_provider(
            "claude",
            {"windows": [{"label": "5h", "pct": 100.0, "reset_iso": ""}, {"label": "7d", "pct": 30.0}]},
            False,
        )

        self.assertEqual(rendered, "🔴 C:5h:FULL 7d:30%")

    def test_render_provider_drops_unlabeled_explicit_windows(self) -> None:
        rendered = self.plugin._render_provider(
            "claude",
            {"windows": [{"pct": 20}, {"label": 12, "pct": 30}]},
            False,
        )

        self.assertEqual(rendered, "🟡 C:?")

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
        active_fetchers = expected

        self.assertEqual(self.plugin.PROVIDERS, expected)
        self.assertEqual(tuple(self.plugin._PROVIDERS), active_fetchers)
        self.assertEqual(self.plugin.PROVIDER_SHORT["gemini"], "Ge")
        self.assertEqual(self.plugin.PROVIDER_SHORT["glm"], "G")
        self.assertEqual(self.plugin.PROVIDER_SHORT["deepseek"], "D")
        for provider in expected:
            self.assertIn(provider, self.plugin._cache)
            self.assertIsNone(self.plugin._cache[provider])
            self.assertEqual(self.plugin._cache["next_retry"][provider], 0.0)
            self.assertEqual(self.plugin._auth_failure_counts[provider], 0)

        self.assertEqual(self.plugin._due_providers(0.0), active_fetchers)

    def test_auth_failure_suppresses_provider_after_third_consecutive_failure(self) -> None:
        auth_error = urllib.error.HTTPError("https://example.test", 401, "unauthorized", {}, None)

        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"claude": MagicMock(side_effect=auth_error)},
            clear=True,
        ):
            self.plugin._refresh_cache(("claude",))
            self.assertEqual(self.plugin._auth_failure_counts["claude"], 1)
            self.plugin._refresh_cache(("claude",))
            self.assertEqual(self.plugin._auth_failure_counts["claude"], 2)
            self.plugin._refresh_cache(("claude",))

        self.assertEqual(self.plugin._auth_failure_counts["claude"], 3)
        with patch.object(self.plugin, "_start_refresh_if_needed") as start_refresh:
            rendered = self.plugin.on_status_bar_render({"quota_status": {"providers": ["claude"]}})

        start_refresh.assert_called_once_with(("claude",))
        self.assertIsNone(rendered)

    def test_auth_failure_suppression_is_independent_per_provider(self) -> None:
        auth_error = urllib.error.HTTPError("https://example.test", 403, "forbidden", {}, None)
        self.plugin._cache["codex"] = {"session_pct": 12.0, "reset_iso": ""}

        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"claude": MagicMock(side_effect=auth_error)},
            clear=True,
        ):
            for _ in range(3):
                self.plugin._refresh_cache(("claude",))

        self.assertEqual(self.plugin._auth_failure_counts["claude"], 3)
        self.assertEqual(self.plugin._auth_failure_counts["codex"], 0)
        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"quota_status": {"providers": ["claude", "codex"]}})

        self.assertEqual(rendered, "🟢 Cx:12%")

    def test_non_auth_failures_leave_auth_failure_counter_unchanged(self) -> None:
        errors = (
            urllib.error.HTTPError("https://example.test", 500, "server error", {}, None),
            urllib.error.URLError("network unavailable"),
            TimeoutError("slow response"),
            json.JSONDecodeError("bad json", "not-json", 0),
            ValueError("malformed response"),
        )

        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.plugin._auth_failure_counts["claude"] = 2
                with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
                    self.plugin._PROVIDERS,
                    {"claude": MagicMock(side_effect=error)},
                    clear=True,
                ):
                    self.plugin._refresh_cache(("claude",))

                self.assertEqual(self.plugin._auth_failure_counts["claude"], 2)

    def test_missing_credentials_reset_auth_failure_counter(self) -> None:
        self.plugin._auth_failure_counts["claude"] = 2

        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"claude": MagicMock(return_value=None)},
            clear=True,
        ):
            self.plugin._refresh_cache(("claude",))

        self.assertEqual(self.plugin._auth_failure_counts["claude"], 0)
        self.assertIsNone(self.plugin._cache["claude"])
        self.assertEqual(self.plugin._cache["next_retry"]["claude"], 200.0 + self.plugin.AUTH_RETRY_TTL)

    def test_missing_credentials_unsuppress_previously_auth_failed_provider(self) -> None:
        self.plugin._auth_failure_counts["gemini"] = self.plugin.AUTH_FAILURE_SUPPRESSION_THRESHOLD

        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"gemini": MagicMock(return_value=None)},
            clear=True,
        ):
            self.plugin._refresh_cache(("gemini",))

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"quota_status": {"providers": ["gemini"]}})

        self.assertEqual(self.plugin._auth_failure_counts["gemini"], 0)
        self.assertEqual(rendered, "🔴 Ge:?")

    def test_gemini_invalid_api_key_counts_toward_auth_suppression(self) -> None:
        invalid_key_result = {
            "key_valid": False,
            "available_models": [],
            "model_count": 0,
            "probe": None,
            "error": "HTTP Error 400: Bad Request",
        }

        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"gemini": MagicMock(return_value=invalid_key_result)},
            clear=True,
        ):
            self.plugin._refresh_cache(("gemini",))
            self.assertEqual(self.plugin._auth_failure_counts["gemini"], 1)
            self.plugin._refresh_cache(("gemini",))
            self.assertEqual(self.plugin._auth_failure_counts["gemini"], 2)
            self.plugin._refresh_cache(("gemini",))

        self.assertEqual(self.plugin._auth_failure_counts["gemini"], 3)
        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"quota_status": {"providers": ["gemini"]}})

        self.assertIsNone(rendered)

    def test_gemini_cloudcode_forbidden_counts_toward_auth_suppression(self) -> None:
        forbidden_result = {"cloudcode": True, "cloudcode_error": "forbidden"}

        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"gemini": MagicMock(return_value=forbidden_result)},
            clear=True,
        ):
            for _ in range(3):
                self.plugin._refresh_cache(("gemini",))

        self.assertEqual(self.plugin._auth_failure_counts["gemini"], 3)
        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"quota_status": {"providers": ["gemini"]}})

        self.assertIsNone(rendered)

    def test_gemini_cloudcode_non_auth_errors_do_not_reset_auth_failure_counter(self) -> None:
        for error in ("rate_limited", "unknown", "KeyError"):
            with self.subTest(error=error):
                self.plugin._auth_failure_counts["gemini"] = 2

                with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
                    self.plugin._PROVIDERS,
                    {"gemini": MagicMock(return_value={"cloudcode": True, "cloudcode_error": error})},
                    clear=True,
                ):
                    self.plugin._refresh_cache(("gemini",))

                self.assertEqual(self.plugin._auth_failure_counts["gemini"], 2)

    def test_successful_authenticated_check_resets_auth_failure_counter(self) -> None:
        self.plugin._auth_failure_counts["claude"] = 2

        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"claude": MagicMock(return_value={"session_pct": 18.0, "reset_iso": ""})},
            clear=True,
        ):
            self.plugin._refresh_cache(("claude",))

        self.assertEqual(self.plugin._auth_failure_counts["claude"], 0)
        self.assertEqual(self.plugin._cache["claude"], {"session_pct": 18.0, "reset_iso": ""})
        self.assertNotIn("claude", self.plugin._cache["stale"])

    def test_suppressed_provider_is_still_scheduled_from_render(self) -> None:
        self.plugin._auth_failure_counts["claude"] = self.plugin.AUTH_FAILURE_SUPPRESSION_THRESHOLD
        self.plugin._cache["claude"] = {"session_pct": 18.0, "reset_iso": ""}

        with patch.object(self.plugin, "_start_refresh_if_needed") as start_refresh:
            rendered = self.plugin.on_status_bar_render({"quota_status": {"providers": ["claude"]}})

        start_refresh.assert_called_once_with(("claude",))
        self.assertIsNone(rendered)

    def test_suppressed_provider_is_fetched_on_later_refreshes(self) -> None:
        fetcher = MagicMock(return_value={"session_pct": 18.0, "reset_iso": ""})
        self.plugin._auth_failure_counts["claude"] = self.plugin.AUTH_FAILURE_SUPPRESSION_THRESHOLD
        self.plugin._cache["next_retry"]["claude"] = 0.0

        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"claude": fetcher},
            clear=True,
        ):
            self.plugin._refresh_cache()

        fetcher.assert_called_once_with()
        self.assertEqual(self.plugin._auth_failure_counts["claude"], 0)

    def test_suppression_recovery_renders_provider_after_successful_refresh(self) -> None:
        self.plugin._auth_failure_counts["claude"] = self.plugin.AUTH_FAILURE_SUPPRESSION_THRESHOLD
        self.plugin._cache["claude"] = None
        self.plugin._cache["stale"].add("claude")

        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"claude": MagicMock(return_value={"session_pct": 18.0, "reset_iso": ""})},
            clear=True,
        ):
            self.plugin._refresh_cache(("claude",))

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"quota_status": {"providers": ["claude"]}})

        self.assertEqual(self.plugin._auth_failure_counts["claude"], 0)
        self.assertEqual(rendered, "🟢 C:18%")

    def test_render_reads_provider_allowlist_from_snapshot_config(self) -> None:
        self._seed_all_provider_cache()

        with patch.object(self.plugin, "_start_refresh_if_needed") as start_refresh:
            rendered = self.plugin.on_status_bar_render(
                {"config": {"quota_status": {"providers": ["claude", "deepseek"]}}}
            )

        start_refresh.assert_called_once_with(("claude", "deepseek"))
        self.assertEqual(rendered, "🟢 C:24% │ 🟢 D:$3.50")

    def test_render_reads_provider_allowlist_from_render_context_kwargs(self) -> None:
        self._seed_all_provider_cache()

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render(
                render_context={"config_snapshot": {"quota_status": {"providers": ["deepseek"]}}}
            )

        self.assertEqual(rendered, "🟢 D:$3.50")

    def test_render_provider_allowlist_is_case_sensitive(self) -> None:
        self._seed_all_provider_cache()

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"quota_status": {"providers": ["claude", "GLM"]}})

        self.assertEqual(rendered, "🟢 C:24%")
        self.assertNotIn(" G:", rendered)

    def test_render_without_configured_allowlist_preserves_available_provider_rendering(self) -> None:
        self._seed_all_provider_cache()

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"config": {}})

        self.assertEqual(rendered, "🟢 C:24% │ 🟢 Cx:11% │ 🟢 Ge:OK │ 🟢 G:28% │ 🟢 D:$3.50")

    def test_acceptance_status_bar_contains_successful_glm_and_deepseek_segments(self) -> None:
        glm_fetcher = MagicMock(return_value={"session_pct": 28.0, "reset_iso": ""})
        deepseek_fetcher = MagicMock(
            return_value={
                "is_available": True,
                "currency": "USD",
                "balance": 3.5,
                "total_balance": 3.5,
                "total_balance_display": "3.50",
            }
        )

        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"glm": glm_fetcher, "deepseek": deepseek_fetcher},
            clear=True,
        ):
            self.plugin._refresh_cache(("glm", "deepseek"))

        with patch.object(self.plugin, "_start_refresh_if_needed") as start_refresh:
            rendered = self.plugin.on_status_bar_render({"quota_status": {"providers": ["glm", "deepseek"]}})

        glm_fetcher.assert_called_once_with()
        deepseek_fetcher.assert_called_once_with()
        start_refresh.assert_called_once_with(("glm", "deepseek"))
        self.assertEqual(rendered, "🟢 G:28% │ 🟢 D:$3.50")

    def test_acceptance_glm_missing_credentials_omits_glm_from_successful_status_output(self) -> None:
        glm_fetcher = MagicMock(return_value=None)
        deepseek_fetcher = MagicMock(
            return_value={
                "is_available": True,
                "currency": "USD",
                "balance": 3.5,
                "total_balance": 3.5,
                "total_balance_display": "3.50",
            }
        )

        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"glm": glm_fetcher, "deepseek": deepseek_fetcher},
            clear=True,
        ):
            self.plugin._refresh_cache(("glm", "deepseek"))

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"quota_status": {"providers": ["glm", "deepseek"]}})

        glm_fetcher.assert_called_once_with()
        deepseek_fetcher.assert_called_once_with()
        self.assertEqual(rendered, "🟢 D:$3.50")
        self.assertNotIn(" G:", rendered)

    def test_acceptance_deepseek_missing_credentials_omits_deepseek_from_successful_status_output(self) -> None:
        glm_fetcher = MagicMock(return_value={"session_pct": 28.0, "reset_iso": ""})
        deepseek_fetcher = MagicMock(return_value=None)

        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"glm": glm_fetcher, "deepseek": deepseek_fetcher},
            clear=True,
        ):
            self.plugin._refresh_cache(("glm", "deepseek"))

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"quota_status": {"providers": ["glm", "deepseek"]}})

        glm_fetcher.assert_called_once_with()
        deepseek_fetcher.assert_called_once_with()
        self.assertEqual(rendered, "🟢 G:28%")
        self.assertNotIn(" D:", rendered)

    def test_acceptance_claude_codex_and_gemini_render_one_segment_each_when_available(self) -> None:
        self._seed_wide_provider_cache()

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"quota_status": {"providers": ["claude", "codex", "gemini"]}})

        self.assertIsNotNone(rendered)
        segments = rendered.split(" │ ")
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[0], "🟡 C:5h:24% 7d:61%")
        self.assertEqual(segments[1], "🟡 Cx:P:22% S:63%")
        self.assertEqual(segments[2], "🟡 Ge:CREDITS 600/1500")

    def test_render_width_60_trims_to_limit(self) -> None:
        self._seed_wide_provider_cache()

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"terminal_width": 60})

        self.assertIsNotNone(rendered)
        self.assertLessEqual(self.plugin._display_width(rendered), 60)
        self.assertEqual(rendered, "🟡 C:5h:24% 7d:61% │ 🟡 Cx:P:22% S:63%")

    def test_render_width_30_uses_actual_reported_width(self) -> None:
        self._seed_wide_provider_cache()

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"terminal_width": 30})

        self.assertIsNotNone(rendered)
        self.assertLessEqual(self.plugin._display_width(rendered), 30)
        self.assertEqual(rendered, "🟡 C:5h:24% 7d:61%")

    def test_render_width_60_trims_at_well_formed_segment_boundaries(self) -> None:
        self._seed_wide_provider_cache()

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render(render_context={"terminal": {"columns": 60}})

        self.assertEqual(rendered, "🟡 C:5h:24% 7d:61% │ 🟡 Cx:P:22% S:63%")
        self.assertFalse(rendered.startswith(" │ "))
        self.assertFalse(rendered.endswith(" │ "))
        self.assertNotIn("│ │", rendered)
        self.assertEqual(
            rendered.split(" │ "),
            ["🟡 C:5h:24% 7d:61%", "🟡 Cx:P:22% S:63%"],
        )

    def test_render_width_61_trims_to_reported_width(self) -> None:
        self._seed_wide_provider_cache()

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"terminal_width": 61})

        self.assertIsNotNone(rendered)
        self.assertLessEqual(self.plugin._display_width(rendered), 61)
        self.assertEqual(rendered, "🟡 C:5h:24% 7d:61% │ 🟡 Cx:P:22% S:63%")

    def test_render_width_above_60_does_not_apply_fixed_60_character_cap(self) -> None:
        self._seed_wide_provider_cache()

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"terminal_width": 80})

        self.assertIsNotNone(rendered)
        self.assertGreater(self.plugin._display_width(rendered), 60)
        self.assertLessEqual(self.plugin._display_width(rendered), 80)
        self.assertEqual(
            rendered,
            "🟡 C:5h:24% 7d:61% │ 🟡 Cx:P:22% S:63% │ 🟡 Ge:CREDITS 600/1500 │ 🟢 G:28%",
        )

    def test_render_too_narrow_for_first_segment_returns_none(self) -> None:
        self._seed_wide_provider_cache()

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"terminal_width": 10})

        self.assertIsNone(rendered)

    def test_render_reads_plain_width_from_nested_terminal_context(self) -> None:
        self._seed_wide_provider_cache()

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"terminal": {"width": 30}})

        self.assertEqual(rendered, "🟡 C:5h:24% 7d:61%")

    def test_render_missing_width_does_not_apply_60_character_cap(self) -> None:
        self._seed_wide_provider_cache()

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"config": {}})

        self.assertEqual(
            rendered,
            "🟡 C:5h:24% 7d:61% │ 🟡 Cx:P:22% S:63% │ 🟡 Ge:CREDITS 600/1500 │ 🟢 G:28% │ 🟢 D:$3.50",
        )
        self.assertGreater(self.plugin._display_width(rendered), 60)

    def test_render_does_not_call_refresh_inline(self) -> None:
        with patch.object(self.plugin, "_refresh_cache", side_effect=AssertionError("sync refresh")), patch.object(
            self.plugin, "_start_refresh_if_needed"
        ) as start_refresh:
            rendered = self.plugin.on_status_bar_render()

        start_refresh.assert_called_once_with(self.plugin.PROVIDERS)
        self.assertEqual(rendered, "🔴 C:? │ 🔴 Cx:? │ 🔴 Ge:? │ 🔴 G:? │ 🔴 D:?")

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
