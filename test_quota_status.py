from __future__ import annotations

import importlib.util
import json
import re
import socket
import sys
import subprocess
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
AGY_QUOTA_PATH = Path(__file__).with_name("agy_quota.py")
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
    "AC18": (
        "test_render_width_60_trims_at_well_formed_segment_boundaries",
        "test_render_width_60_drops_whole_next_segment_after_stale_segments",
    ),
    "AC19": (
        "test_auth_failure_suppresses_provider_after_third_consecutive_failure",
        "test_public_render_drives_suppression_background_refresh_and_recovery",
    ),
    "AC20": (
        "test_auth_failure_suppression_is_independent_per_provider",
        "test_public_render_drives_suppression_background_refresh_and_recovery",
    ),
    "AC21": (
        "test_suppression_recovery_renders_provider_after_successful_refresh",
        "test_public_render_drives_suppression_background_refresh_and_recovery",
    ),
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
        "test_render_width_61_does_not_trim",
        "test_render_width_above_60_does_not_apply_fixed_60_character_cap",
        "test_render_missing_width_does_not_apply_60_character_cap",
    ),
    "AC31": (
        "test_v2_provider_identity_order_and_cache_initialization",
        "test_non_auth_failures_leave_auth_failure_counter_unchanged",
        "test_successful_authenticated_check_resets_auth_failure_counter",
        "test_concurrent_auth_failure_recordings_are_atomic",
    ),
    "AC32": (
        "test_suppressed_provider_is_fetched_on_later_refreshes",
        "test_public_render_drives_suppression_background_refresh_and_recovery",
    ),
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
    "test_render_width_60_drops_whole_next_segment_after_stale_segments": ("AC18",),
    "test_auth_failure_suppresses_provider_after_third_consecutive_failure": ("AC19", "AC24"),
    "test_auth_failure_suppression_is_independent_per_provider": ("AC20",),
    "test_suppression_recovery_renders_provider_after_successful_refresh": ("AC21", "AC24"),
    "test_public_render_drives_suppression_background_refresh_and_recovery": (
        "AC19",
        "AC20",
        "AC21",
        "AC32",
    ),
    "test_acceptance_claude_codex_and_gemini_render_one_segment_each_when_available": ("AC22",),
    "test_plugin_metadata_documents_v2_providers_and_config_surface": ("AC23",),
    "test_get_glm_key_prefers_glm_api_key_over_zhipu_api_key": ("AC24",),
    "test_non_auth_failures_leave_auth_failure_counter_unchanged": ("AC24", "AC31"),
    "test_fetch_glm_quota_uses_glm_api_key_header_when_both_credentials_are_set": ("AC25",),
    "test_render_glm_uses_quota_data_from_fallback_host": ("AC26",),
    "test_render_provider_allowlist_is_case_sensitive": ("AC29",),
    "test_render_width_61_does_not_trim": ("AC30",),
    "test_render_width_above_60_does_not_apply_fixed_60_character_cap": ("AC30",),
    "test_render_missing_width_does_not_apply_60_character_cap": ("AC30",),
    "test_v2_provider_identity_order_and_cache_initialization": ("AC31",),
    "test_successful_authenticated_check_resets_auth_failure_counter": ("AC31",),
    "test_concurrent_auth_failure_recordings_are_atomic": ("AC31",),
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


def load_agy_quota() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agy_quota_test", AGY_QUOTA_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load Agy quota module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SynchronousThread:
    """Test double that runs a background-thread target deterministically."""

    def __init__(self, *, target, args=(), kwargs=None, **_thread_options) -> None:
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self) -> None:
        self._target(*self._args, **self._kwargs)


class ContextManagedResponse:
    """HTTP response double that records bounded reads and context cleanup."""

    def __init__(self, body: bytes = b"", read_error: Exception | None = None) -> None:
        self.body = body
        self.read_error = read_error
        self.read_sizes: list[int] = []
        self.entered = False
        self.closed = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.closed = True

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        if self.read_error is not None:
            raise self.read_error
        return self.body[:size]


class AgyQuotaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agy_quota = load_agy_quota()

    def test_capture_usage_parses_and_tags_well_formed_gemini_block(self) -> None:
        output = """
            Gemini 3.5 Flash (Medium)
            ███████░░░ 72%
            Refreshes in 4h 19m
        """

        self.assertEqual(
            self.agy_quota._capture_usage(output),
            [{
                "provider": "gemini",
                "name": "Gemini 3.5 Flash (Medium)",
                "remaining_pct": 72.0,
                "used_pct": 28,
                "reset_hours": 4,
            }],
        )

    def test_capture_usage_skips_block_without_trailing_percentage(self) -> None:
        output = """
            Gemini 3.5 Flash (Medium)
            ███████░░░ unavailable
            Refreshes in 4h 19m
        """

        self.assertEqual(self.agy_quota._capture_usage(output), [])

    def test_capture_usage_rejects_out_of_range_multi_digit_percentage(self) -> None:
        output = """
            Gemini 3.5 Flash (Medium)
            ███████░░░ 1072%
            Refreshes in 4h 19m
        """

        self.assertEqual(self.agy_quota._capture_usage(output), [])

    def test_capture_usage_tags_mixed_provider_scrollback(self) -> None:
        output = """
            Gemini 3.5 Flash (Medium)
            ███████░░░ 72%
            Refreshes in 4h 19m
            Claude Sonnet 4
            █████░░░░░ 50%
            GPT-5
            ██░░░░░░░░ 20%
            Refreshes in 1h 05m
        """

        models = self.agy_quota._capture_usage(output)

        self.assertEqual([model["provider"] for model in models], ["gemini", "claude", "gpt"])
        self.assertEqual([model["remaining_pct"] for model in models], [72.0, 50.0, 20.0])

    def test_capture_usage_ignores_stray_provider_prefixed_line(self) -> None:
        output = """
            Claude authentication is managed in settings
            Press Escape to return
            Gemini 3.5 Pro (High)
            █████████░ 90%
        """

        models = self.agy_quota._capture_usage(output)

        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["provider"], "gemini")

    def test_capture_usage_ignores_provider_prose_followed_by_progress(self) -> None:
        output = """
            Claude replied to your last message
            download progress 72%
        """

        self.assertEqual(self.agy_quota._capture_usage(output), [])

    def test_new_tmux_session_names_are_collision_resistant(self) -> None:
        with patch.object(self.agy_quota.os, "getpid", return_value=1234), patch.object(
            self.agy_quota.secrets, "token_hex", side_effect=("a" * 16, "b" * 16)
        ):
            first = self.agy_quota._new_tmux_session_name()
            second = self.agy_quota._new_tmux_session_name()

        self.assertEqual(first, "hermes-agy-quota-1234-aaaaaaaaaaaaaaaa")
        self.assertEqual(second, "hermes-agy-quota-1234-bbbbbbbbbbbbbbbb")
        self.assertNotEqual(first, second)

    def test_fetch_agy_quota_cleans_up_own_session_when_capture_raises(self) -> None:
        calls: list[list[str]] = []

        def fake_run(args, **_kwargs):
            calls.append(args)
            if args[1] == "capture-pane":
                raise subprocess.TimeoutExpired(args, timeout=5)
            return subprocess.CompletedProcess(args, 0, stdout="")

        session_name = "hermes-agy-quota-1234-unique"
        with patch.object(self.agy_quota, "_new_tmux_session_name", return_value=session_name), patch.object(
            self.agy_quota.subprocess, "run", side_effect=fake_run
        ), patch.object(self.agy_quota.time, "sleep"):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.agy_quota.fetch_agy_quota()

        self.assertEqual(calls[0], ["tmux", "new-session", "-d", "-s", session_name])
        self.assertEqual(calls[-1], ["tmux", "kill-session", "-t", session_name])
        self.assertEqual(sum(call[:2] == ["tmux", "kill-session"] for call in calls), 1)

    def test_fetch_agy_quota_allows_graceful_shutdown_before_cleanup(self) -> None:
        output = """
            Gemini 3.5 Flash (Medium)
            ███████░░░ 72%
        """

        def fake_run(args, **_kwargs):
            stdout = output if args[1] == "capture-pane" else ""
            return subprocess.CompletedProcess(args, 0, stdout=stdout)

        session_name = "hermes-agy-quota-1234-unique"
        with patch.object(self.agy_quota, "_new_tmux_session_name", return_value=session_name), patch.object(
            self.agy_quota.subprocess, "run", side_effect=fake_run
        ), patch.object(self.agy_quota.time, "sleep") as sleep:
            models = self.agy_quota.fetch_agy_quota()

        self.assertIsNotNone(models)
        self.assertEqual(
            [call.args[0] for call in sleep.call_args_list],
            [
                self.agy_quota.STARTUP_SECONDS,
                self.agy_quota.QUOTA_SECONDS,
                self.agy_quota.ESCAPE_SHUTDOWN_SECONDS,
                self.agy_quota.EXIT_SHUTDOWN_SECONDS,
            ],
        )


class QuotaApiNumberValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.quota_api = load_quota_api()

    def test_json_number_or_none_rejects_non_finite_json_numbers(self) -> None:
        for raw_value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(raw_value=raw_value):
                parsed_value = json.loads(raw_value)
                self.assertIsNone(self.quota_api.json_number_or_none(parsed_value))

    def test_json_number_or_none_rejects_non_finite_numeric_strings(self) -> None:
        for value in ("NaN", "Infinity", "-Infinity", "1e10000"):
            with self.subTest(value=value):
                self.assertIsNone(self.quota_api.json_number_or_none(value))

    def test_json_number_or_none_rejects_integer_too_large_for_float(self) -> None:
        self.assertIsNone(self.quota_api.json_number_or_none(10**400))


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


class QuotaApiGeminiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.quota_api = load_quota_api()

    def test_fetch_gemini_quota_reports_credential_http_failures_as_invalid_key(self) -> None:
        for status in (400, 401, 403):
            error = urllib.error.HTTPError(
                "https://generativelanguage.googleapis.com/v1beta/models",
                status,
                "credentials rejected",
                {},
                None,
            )
            with self.subTest(status=status), patch.dict(
                self.quota_api.os.environ,
                {"GOOGLE_API_KEY": "invalid-key"},
                clear=True,
            ), patch.object(self.quota_api, "fetch_json", side_effect=error):
                result = self.quota_api.fetch_gemini_quota()

            self.assertIsNotNone(result)
            self.assertFalse(result["key_valid"])
            self.assertTrue(result["auth_error"])
            self.assertEqual(result["http_status"], status)
            self.assertEqual(result["available_models"], [])

    def test_fetch_gemini_quota_propagates_non_auth_validation_failures(self) -> None:
        failures = (
            urllib.error.URLError("network unavailable"),
            socket.timeout("request timed out"),
            urllib.error.HTTPError(
                "https://generativelanguage.googleapis.com/v1beta/models",
                500,
                "server error",
                {},
                None,
            ),
        )

        for failure in failures:
            with self.subTest(failure=type(failure).__name__), patch.dict(
                self.quota_api.os.environ,
                {"GOOGLE_API_KEY": "configured-key"},
                clear=True,
            ), patch.object(self.quota_api, "fetch_json", side_effect=failure):
                with self.assertRaises(type(failure)):
                    self.quota_api.fetch_gemini_quota()


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

    def test_fetch_glm_quota_uses_zhipu_header_when_glm_key_is_empty(self) -> None:
        requests = []

        def fake_fetch(req):
            requests.append(req)
            return {"data": {"availableLimitPercentage": 100}}

        with patch.dict(
            self.quota_api.os.environ,
            {"GLM_API_KEY": "", "ZHIPU_API_KEY": "zhipu-raw"},
            clear=True,
        ), patch.object(self.quota_api, "fetch_json", side_effect=fake_fetch):
            self.quota_api.fetch_glm_quota()

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].get_header("Authorization"), "zhipu-raw")

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

    def test_fetch_glm_quota_falls_back_after_every_primary_failure_class(self) -> None:
        primary_failures = (
            urllib.error.HTTPError("https://api.z.ai", 503, "unavailable", {}, None),
            TimeoutError("primary timed out"),
            urllib.error.HTTPError("https://api.z.ai", 401, "unauthorized", {}, None),
            urllib.error.HTTPError("https://api.z.ai", 403, "forbidden", {}, None),
            json.JSONDecodeError("invalid JSON", "not-json", 0),
        )

        for primary_failure in primary_failures:
            requests = []

            def fake_fetch(req):
                requests.append(req)
                if len(requests) == 1:
                    raise primary_failure
                return {"data": {"availableLimitPercentage": 91}}

            with self.subTest(
                failure=type(primary_failure).__name__,
                code=getattr(primary_failure, "code", None),
            ), patch.dict(
                self.quota_api.os.environ,
                {"GLM_API_KEY": "glm-raw"},
                clear=True,
            ), patch.object(self.quota_api, "fetch_json", side_effect=fake_fetch):
                result = self.quota_api.fetch_glm_quota()

            self.assertIsNotNone(result)
            self.assertEqual(
                [request.full_url for request in requests],
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

    def test_fetch_glm_quota_uses_final_host_failure_for_classification(self) -> None:
        errors = (
            urllib.error.HTTPError("https://api.z.ai", 401, "unauthorized", {}, None),
            urllib.error.HTTPError("https://open.bigmodel.cn", 500, "server error", {}, None),
        )

        with patch.dict(self.quota_api.os.environ, {"GLM_API_KEY": "bad-key"}, clear=True), patch.object(
            self.quota_api,
            "fetch_json",
            side_effect=errors,
        ):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.quota_api.fetch_glm_quota()

        self.assertEqual(raised.exception.code, 500)

    def test_fetch_glm_quota_does_not_mask_programming_errors(self) -> None:
        with patch.dict(self.quota_api.os.environ, {"GLM_API_KEY": "glm-raw"}, clear=True), patch.object(
            self.quota_api,
            "fetch_json",
            return_value={"data": {"availableLimitPercentage": 91}},
        ) as fetch_json, patch.object(
            self.quota_api,
            "_normalize_glm_quota",
            side_effect=TypeError("normalizer bug"),
        ):
            with self.assertRaisesRegex(TypeError, "normalizer bug"):
                self.quota_api.fetch_glm_quota()

        fetch_json.assert_called_once()

    def test_fetch_glm_quota_keeps_all_host_server_failures_non_auth(self) -> None:
        errors = (
            urllib.error.HTTPError("https://api.z.ai", 500, "server error", {}, None),
            urllib.error.HTTPError("https://open.bigmodel.cn", 503, "unavailable", {}, None),
        )

        with patch.dict(self.quota_api.os.environ, {"GLM_API_KEY": "glm-raw"}, clear=True), patch.object(
            self.quota_api,
            "fetch_json",
            side_effect=errors,
        ):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.quota_api.fetch_glm_quota()

        self.assertEqual(raised.exception.code, 503)

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

    def _agy_keyring_item(self, payload, *, label="antigravity", attributes=None):
        secret_value = MagicMock()
        secret_value.get_text.return_value = (
            payload if isinstance(payload, str) else json.dumps(payload)
        )
        item = MagicMock()
        item.get_attributes.return_value = attributes or {}
        item.get_label.return_value = label
        item.retrieve_secret_sync.return_value = secret_value
        return item

    def _agy_keyring_modules_for_items(self, items, *, get_sync_side_effect=None):
        collection = MagicMock()
        collection.get_items.return_value = items
        service = MagicMock()
        service.get_collections.return_value = [collection]

        secret_api = MagicMock()
        secret_api.ServiceFlags.OPEN_SESSION = 1
        secret_api.ServiceFlags.LOAD_COLLECTIONS = 2
        if get_sync_side_effect is None:
            secret_api.Service.get_sync.return_value = service
        else:
            secret_api.Service.get_sync.side_effect = get_sync_side_effect
        repository_module = ModuleType("gi.repository")
        repository_module.Secret = secret_api
        gi_module = ModuleType("gi")
        gi_module.require_version = MagicMock()
        modules = patch.dict(
            sys.modules,
            {"gi": gi_module, "gi.repository": repository_module},
        )
        return modules, secret_api

    def _agy_keyring_modules(self):
        modules, _secret_api = self._agy_keyring_modules_for_items(
            [
                self._agy_keyring_item(
                    {"token": {"access_token": "agy-access-token"}}
                )
            ]
        )
        return modules

    def test_agy_keyring_exact_attribute_match_returns_token(self) -> None:
        item = self._agy_keyring_item(
            {"token": {"access_token": "exact-token"}},
            label="unrelated-label",
            attributes={"service": "gemini", "username": "antigravity"},
        )
        modules, _secret_api = self._agy_keyring_modules_for_items([item])

        with modules:
            token = self.plugin._read_agy_keyring_token()

        self.assertEqual(token, "exact-token")
        item.retrieve_secret_sync.assert_called_once_with()

    def test_agy_keyring_substring_label_is_not_a_match(self) -> None:
        item = self._agy_keyring_item(
            {"token": {"access_token": "wrong-token"}},
            label="not-antigravity-backup",
            attributes={"service": "gemini", "username": "someone-else"},
        )
        modules, _secret_api = self._agy_keyring_modules_for_items([item])

        with modules:
            token = self.plugin._read_agy_keyring_token()

        self.assertIsNone(token)
        item.retrieve_secret_sync.assert_not_called()

    def test_agy_keyring_malformed_payloads_return_none(self) -> None:
        malformed_payloads = (
            [],
            {},
            {"token": []},
            {"token": {}},
            {"token": {"access_token": 123}},
        )
        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                item = self._agy_keyring_item(payload)
                modules, _secret_api = self._agy_keyring_modules_for_items([item])
                with modules:
                    self.assertIsNone(self.plugin._read_agy_keyring_token())

    def test_agy_keyring_service_lookup_obeys_deadline(self) -> None:
        release_lookup = self.plugin.threading.Event()
        modules, _secret_api = self._agy_keyring_modules_for_items(
            [], get_sync_side_effect=lambda _flags: release_lookup.wait()
        )

        started_at = time.monotonic()
        try:
            with modules, patch.object(
                self.plugin, "AGY_KEYRING_DEADLINE_SECONDS", 0.02
            ):
                self.assertIsNone(self.plugin._read_agy_keyring_token())
        finally:
            release_lookup.set()

        self.assertLess(time.monotonic() - started_at, 0.5)

    def test_agy_token_cache_avoids_second_keyring_lookup_within_ttl(self) -> None:
        item = self._agy_keyring_item(
            {"token": {"access_token": "cached-token"}}
        )
        modules, secret_api = self._agy_keyring_modules_for_items([item])

        with modules:
            self.assertEqual(self.plugin._get_agy_token(), "cached-token")
            self.assertEqual(self.plugin._get_agy_token(), "cached-token")

        secret_api.Service.get_sync.assert_called_once_with(3)
        item.retrieve_secret_sync.assert_called_once_with()

    def test_agy_token_cache_expiry_reloads_keyring_token(self) -> None:
        item = self._agy_keyring_item(
            {"token": {"access_token": "rotated-token"}}
        )
        modules, secret_api = self._agy_keyring_modules_for_items([item])

        with modules, patch.object(
            self.plugin, "AGY_TOKEN_CACHE_TTL", 10.0
        ), patch.object(
            self.plugin.time,
            "monotonic",
            side_effect=(0.0, 0.0, 11.0, 11.0),
        ):
            self.assertEqual(self.plugin._get_agy_token(), "rotated-token")
            self.assertEqual(self.plugin._get_agy_token(), "rotated-token")

        self.assertEqual(secret_api.Service.get_sync.call_count, 2)
        self.assertEqual(item.retrieve_secret_sync.call_count, 2)

    def test_agy_auth_failure_invalidates_cache_and_next_fetch_reads_keyring(self) -> None:
        item = self._agy_keyring_item(
            {"token": {"access_token": "cached-token"}}
        )
        modules, secret_api = self._agy_keyring_modules_for_items([item])
        auth_error = urllib.error.HTTPError(
            "https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
            401,
            "unauthorized",
            {},
            None,
        )
        response = ContextManagedResponse(b'{"models": {}}')

        with modules, patch.object(
            self.plugin.urllib.request,
            "urlopen",
            side_effect=(auth_error, response),
        ):
            with self.assertRaises(urllib.error.HTTPError):
                self.plugin._agy_api_fetch_models()
            result = self.plugin._agy_api_fetch_models()

        self.assertEqual(result, {"models": {}})
        self.assertEqual(secret_api.Service.get_sync.call_count, 2)
        self.assertEqual(item.retrieve_secret_sync.call_count, 2)

    def test_agy_token_invalidation_during_refill_does_not_restore_stale_token(self) -> None:
        refill_started = self.plugin.threading.Event()
        allow_refill = self.plugin.threading.Event()
        results = []
        errors = []

        def blocked_read():
            refill_started.set()
            allow_refill.wait(timeout=1)
            return "stale-token"

        def refill():
            try:
                results.append(self.plugin._get_agy_token())
            except Exception as exc:
                errors.append(exc)

        with patch.object(
            self.plugin, "_read_agy_keyring_token", side_effect=blocked_read
        ):
            thread = self.plugin.threading.Thread(target=refill, daemon=True)
            thread.start()
            self.assertTrue(refill_started.wait(timeout=1))
            self.plugin._invalidate_agy_token_cache()
            allow_refill.set()
            thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [None])
        self.assertIsNone(self.plugin._agy_token_cache)
        self.assertEqual(self.plugin._agy_token_cache_expires_at, 0.0)

    def _agy_json_body(self, size: int) -> bytes:
        empty_body = json.dumps(
            {"models": {}, "padding": ""}, separators=(",", ":")
        ).encode("utf-8")
        padding_size = size - len(empty_body)
        if padding_size < 0:
            raise ValueError("requested Agy test body is too small")
        return json.dumps(
            {"models": {}, "padding": "x" * padding_size},
            separators=(",", ":"),
        ).encode("utf-8")

    def _render_with_synchronous_refresh(
        self, providers: tuple[str, ...], *, now: float
    ) -> str | None:
        with patch.object(self.plugin.time, "time", return_value=now), patch.object(
            self.plugin, "_refresh_thread_factory", SynchronousThread
        ):
            return self.plugin.on_status_bar_render(
                {"quota_status": {"providers": list(providers)}}
            )

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
            patch.object(self.plugin, "_fetch_gemini_agy_api", return_value=None),
            patch.object(self.plugin, "_fetch_gemini_agy", return_value=None),
            patch.object(self.plugin, "fetch_gemini_quota", return_value=None),
            patch("gemini_cloudcode.cloudcode_token_exists", return_value=False),
        ):
            self.assertIsNone(self.plugin._fetch_gemini())

    def test_fetch_gemini_agy_filters_non_gemini_pools_before_rendering(self) -> None:
        models = [
            {
                "provider": "gemini",
                "name": "Gemini 3.5 Flash (Medium)",
                "remaining_pct": 90.0,
                "used_pct": 10,
                "reset_hours": 4,
            },
            {
                "provider": "claude",
                "name": "Claude Sonnet 4",
                "remaining_pct": 10.0,
                "used_pct": 90,
                "reset_hours": 1,
            },
            {
                "provider": "gpt",
                "name": "GPT-5",
                "remaining_pct": 20.0,
                "used_pct": 80,
                "reset_hours": 2,
            },
        ]

        with patch.object(self.plugin.agy_quota, "fetch_agy_quota", return_value=models):
            result = self.plugin._fetch_gemini_agy()

        self.assertIsNotNone(result)
        self.assertEqual(result["model_count"], 1)
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["used_pct"], 10)
        rendered = self.plugin._render_provider("gemini", result, False)
        self.assertTrue(rendered.startswith("🟢 Ge:10%"), rendered)
        self.assertNotIn("🔴", rendered)

    def test_fetch_gemini_agy_converts_relative_reset_to_iso_countdown(self) -> None:
        base = datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc)
        models = [
            {
                "provider": "gemini",
                "name": "Gemini 3.5 Flash (Medium)",
                "remaining_pct": 0.0,
                "used_pct": 100,
                "reset_hours": 3,
            },
            {
                "provider": "gemini",
                "name": "Gemini 3.5 Pro (High)",
                "remaining_pct": 0.0,
                "used_pct": 100,
                "reset_hours": "+3h",
            },
        ]

        with patch.object(self.plugin.agy_quota, "fetch_agy_quota", return_value=models), patch.object(
            self.plugin, "_now_utc", return_value=base
        ):
            result = self.plugin._fetch_gemini_agy()

        self.assertIsNotNone(result)
        reset_iso = result["reset_iso"]
        self.assertEqual(datetime.fromisoformat(reset_iso), base + timedelta(hours=3))
        self.assertEqual(result["model_count"], 2)
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["reset"], reset_iso)
        with patch.object(self.plugin, "_now_utc", return_value=base):
            self.assertEqual(self.plugin._fmt_reset(reset_iso), "3h0m")
            self.assertEqual(self.plugin._fmt_hours_until(reset_iso), "3h0m")

    def test_fetch_gemini_agy_omits_malformed_relative_reset(self) -> None:
        models = [{
            "provider": "gemini",
            "name": "Gemini 3.5 Flash (Medium)",
            "remaining_pct": 0.0,
            "used_pct": 100,
            "reset_hours": "+soon",
        }]

        with patch.object(self.plugin.agy_quota, "fetch_agy_quota", return_value=models):
            result = self.plugin._fetch_gemini_agy()

        self.assertIsNotNone(result)
        self.assertEqual(result["reset_iso"], "")
        self.assertEqual(result["groups"][0]["reset"], "")
        self.assertEqual(self.plugin._render_provider("gemini", result, False), "🔴 Ge:100%")

    def test_fetch_gemini_prefers_structured_agy_api_over_partial_scrape(self) -> None:
        structured_result = {
            "cloudcode": True,
            "remaining_fraction": 0.72,
            "model_count": 1,
            "groups": [{"label": "3.5F", "remaining": 0.72, "used_pct": 28, "reset": "", "model_count": 1}],
        }
        partial_scrape = [{
            "provider": "gemini",
            "name": "Gemini 3.5 Pro (High)",
            "remaining_pct": 5.0,
            "used_pct": 95,
            "reset_hours": 1,
        }]

        with patch.object(self.plugin, "_fetch_gemini_agy_api", return_value=structured_result), patch.object(
            self.plugin.agy_quota, "fetch_agy_quota", return_value=partial_scrape
        ) as scrape:
            result = self.plugin._fetch_gemini()

        self.assertEqual(result, structured_result)
        scrape.assert_not_called()

    def test_fetch_gemini_prefers_cloudcode_over_partial_scrape(self) -> None:
        cloudcode_result = {
            "cloudcode": True,
            "remaining_fraction": 0.8,
            "model_count": 1,
            "groups": [{"label": "3.5F", "remaining": 0.8, "used_pct": 20, "reset": "", "model_count": 1}],
        }

        with patch.object(self.plugin, "_fetch_gemini_agy_api", return_value=None), patch.object(
            self.plugin, "_fetch_gemini_cloudcode", return_value=cloudcode_result
        ), patch.object(self.plugin.agy_quota, "fetch_agy_quota") as scrape:
            result = self.plugin._fetch_gemini()

        self.assertEqual(result, cloudcode_result)
        scrape.assert_not_called()

    def test_fetch_gemini_uses_scrape_when_cloudcode_returns_error(self) -> None:
        cloudcode_error = {"cloudcode": True, "cloudcode_error": "network"}
        scrape_result = {
            "cloudcode": True,
            "agy_scrape": True,
            "remaining_fraction": 0.65,
            "model_count": 1,
            "groups": [{"label": "3.5F", "remaining": 0.65, "used_pct": 35, "reset": "", "model_count": 1}],
        }

        with (
            patch.object(self.plugin, "_fetch_gemini_agy_api", return_value=None),
            patch.object(self.plugin, "_fetch_gemini_cloudcode", return_value=cloudcode_error),
            patch.object(self.plugin, "_fetch_gemini_agy", return_value=scrape_result),
            patch.object(self.plugin, "_fetch_gemini_probe") as probe,
        ):
            result = self.plugin._fetch_gemini()

        self.assertEqual(result, scrape_result)
        probe.assert_not_called()

    def test_fetch_gemini_returns_cloudcode_error_when_fallbacks_fail(self) -> None:
        cloudcode_error = {"cloudcode": True, "cloudcode_error": "network"}

        with (
            patch.object(self.plugin, "_fetch_gemini_agy_api", return_value=None),
            patch.object(self.plugin, "_fetch_gemini_cloudcode", return_value=cloudcode_error),
            patch.object(self.plugin, "_fetch_gemini_agy", return_value=None),
            patch.object(self.plugin, "_fetch_gemini_probe", return_value=None),
        ):
            result = self.plugin._fetch_gemini()

        self.assertEqual(result, cloudcode_error)

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
        with patch.object(self.plugin, "_fetch_gemini_agy_api", return_value=None), patch.object(
            self.plugin, "_fetch_gemini_agy", return_value=None
        ), patch.object(
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

    def test_fetch_gemini_cloudcode_preserves_auth_failure_type(self) -> None:
        with patch.object(self.plugin.gemini_cloudcode, "cloudcode_token_exists", return_value=True), patch.object(
            self.plugin.gemini_cloudcode,
            "cloudcode_format_json",
            return_value={"ok": False, "error": "auth", "models": []},
        ):
            result = self.plugin._fetch_gemini_cloudcode()

        self.assertEqual(result, {"cloudcode": True, "cloudcode_error": "auth"})

    def test_agy_api_oversized_body_is_non_auth_and_retains_cache(self) -> None:
        cached = {
            "cloudcode": True,
            "remaining_fraction": 0.65,
            "model_count": 1,
        }
        self.plugin._cache["gemini"] = cached
        self.plugin._auth_failure_counts["gemini"] = 2
        capped_body = self._agy_json_body(self.plugin.MAX_RESPONSE_BYTES)
        response = ContextManagedResponse(capped_body + b" ")

        with (
            self._agy_keyring_modules(),
            patch.object(self.plugin.time, "sleep"),
            patch.object(self.plugin.time, "time", return_value=200.0),
            patch.object(
                self.plugin.urllib.request, "urlopen", return_value=response
            ),
            patch.dict(
                self.plugin._PROVIDERS,
                {"gemini": self.plugin._fetch_gemini},
                clear=True,
            ),
        ):
            self.plugin._refresh_cache(("gemini",))

        self.assertTrue(response.entered)
        self.assertTrue(response.closed)
        self.assertEqual(
            response.read_sizes, [self.plugin.MAX_RESPONSE_BYTES + 1]
        )
        self.assertIs(self.plugin._cache["gemini"], cached)
        self.assertIn("gemini", self.plugin._cache["stale"])
        self.assertEqual(self.plugin._auth_failure_counts["gemini"], 2)
        self.assertEqual(self.plugin._last_errors[-1]["type"], "ValueError")

    def test_agy_api_body_exactly_at_cap_parses_and_closes_response(self) -> None:
        body = self._agy_json_body(self.plugin.MAX_RESPONSE_BYTES)
        response = ContextManagedResponse(body)

        with (
            self._agy_keyring_modules(),
            patch.object(self.plugin.time, "sleep"),
            patch.object(
                self.plugin.urllib.request, "urlopen", return_value=response
            ),
        ):
            result = self.plugin._agy_api_fetch_models()

        self.assertEqual(len(body), self.plugin.MAX_RESPONSE_BYTES)
        self.assertIsNotNone(result)
        self.assertEqual(result["models"], {})
        self.assertTrue(response.entered)
        self.assertTrue(response.closed)
        self.assertEqual(
            response.read_sizes, [self.plugin.MAX_RESPONSE_BYTES + 1]
        )

    def test_agy_api_mid_read_disconnect_closes_and_surfaces_transport_error(self) -> None:
        transport_error = urllib.error.URLError("connection dropped")
        response = ContextManagedResponse(read_error=transport_error)

        with (
            self._agy_keyring_modules(),
            patch.object(self.plugin.time, "sleep"),
            patch.object(
                self.plugin.urllib.request, "urlopen", return_value=response
            ),
            self.assertRaises(urllib.error.URLError) as raised,
        ):
            self.plugin._fetch_gemini_agy_api()

        self.assertIs(raised.exception, transport_error)
        self.assertTrue(response.entered)
        self.assertTrue(response.closed)
        self.assertEqual(
            response.read_sizes, [self.plugin.MAX_RESPONSE_BYTES + 1]
        )

    def test_fetch_gemini_agy_api_propagates_auth_http_error(self) -> None:
        for status in (401, 403):
            auth_error = urllib.error.HTTPError(
                "https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
                status,
                "authentication failed",
                {},
                None,
            )
            with self.subTest(status=status), patch.object(
                self.plugin,
                "_agy_api_fetch_models",
                side_effect=auth_error,
            ), self.assertRaises(urllib.error.HTTPError) as raised:
                self.plugin._fetch_gemini_agy_api()

            self.assertEqual(raised.exception.code, status)

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
        with patch.object(self.plugin, "_fetch_gemini_agy_api", return_value=None), patch.object(
            self.plugin, "_fetch_gemini_agy", return_value=None
        ), patch.object(
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

    def test_provider_claim_is_released_after_unexpected_provider_exception(self) -> None:
        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"claude": MagicMock(side_effect=OverflowError("bad epoch"))},
            clear=True,
        ):
            self.plugin._refresh_cache()

        self.assertNotIn("claude", self.plugin._refresh_claims)
        self.assertIn("claude", self.plugin._cache["stale"])
        self.assertEqual(self.plugin._cache["next_retry"]["claude"], 200.0 + self.plugin.ERROR_RETRY_TTL)

    def test_thread_start_failure_releases_unstarted_provider_claims(self) -> None:
        fake_thread = MagicMock()
        fake_thread.start.side_effect = RuntimeError("thread failed")

        with patch.object(self.plugin, "_refresh_thread_factory", return_value=fake_thread):
            with self.assertRaises(RuntimeError):
                self.plugin._start_refresh_if_needed(self.plugin.PROVIDERS)

        self.assertEqual(self.plugin._refresh_claims, {})

    def test_blocked_gemini_refresh_does_not_block_glm_or_deepseek(self) -> None:
        release_gemini = self.plugin.threading.Event()
        gemini_started = self.plugin.threading.Event()
        gemini_finished = self.plugin.threading.Event()
        glm_finished = self.plugin.threading.Event()
        deepseek_finished = self.plugin.threading.Event()
        glm_result = {"available_limit_pct": 72.0}
        deepseek_result = {"is_available": True, "balance": 4.25, "currency": "USD"}

        def blocked_gemini_fetch():
            gemini_started.set()
            release_gemini.wait(timeout=3)
            gemini_finished.set()
            return {"key_valid": True, "model_count": 1}

        def glm_fetch():
            glm_finished.set()
            return glm_result

        def deepseek_fetch():
            deepseek_finished.set()
            return deepseek_result

        with patch.dict(
            self.plugin._PROVIDERS,
            {"gemini": blocked_gemini_fetch, "glm": glm_fetch, "deepseek": deepseek_fetch},
            clear=True,
        ):
            try:
                self.plugin._start_refresh_if_needed(("gemini", "glm", "deepseek"))
                self.assertTrue(gemini_started.wait(timeout=1))
                self.assertTrue(glm_finished.wait(timeout=1))
                self.assertTrue(deepseek_finished.wait(timeout=1))
                deadline = time.monotonic() + 1
                while (
                    (self.plugin._cache["glm"] != glm_result or self.plugin._cache["deepseek"] != deepseek_result)
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                self.assertEqual(self.plugin._cache["glm"], glm_result)
                self.assertEqual(self.plugin._cache["deepseek"], deepseek_result)
                self.assertIn("gemini", self.plugin._refresh_claims)
            finally:
                release_gemini.set()
                self.assertTrue(gemini_finished.wait(timeout=1))

    def test_expired_claim_is_reclaimed_and_stale_generation_result_is_discarded(self) -> None:
        release_first = self.plugin.threading.Event()
        first_started = self.plugin.threading.Event()
        first_finished = self.plugin.threading.Event()
        fetch_lock = self.plugin.threading.Lock()
        fetch_count = 0
        old_result = {"key_valid": False, "auth_error": True, "available_models": ["old"], "model_count": 1}
        new_result = {"key_valid": True, "available_models": ["new"], "model_count": 1}

        def fetch_gemini():
            nonlocal fetch_count
            with fetch_lock:
                fetch_count += 1
                generation_call = fetch_count
            if generation_call == 1:
                first_started.set()
                release_first.wait(timeout=3)
                first_finished.set()
                return old_result
            return new_result

        with patch.dict(self.plugin._PROVIDERS, {"gemini": fetch_gemini}, clear=True):
            try:
                with patch.object(self.plugin.time, "time", return_value=100.0):
                    self.plugin._start_refresh_if_needed(("gemini",))
                self.assertTrue(first_started.wait(timeout=1))

                reclaim_time = 100.0 + self.plugin.REFRESH_CLAIM_TTL
                with patch.object(self.plugin.time, "time", return_value=reclaim_time):
                    self.plugin._start_refresh_if_needed(("gemini",))

                deadline = time.monotonic() + 1
                while self.plugin._cache["gemini"] != new_result and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(self.plugin._cache["gemini"], new_result)
                self.assertEqual(self.plugin._refresh_generations["gemini"], 2)
            finally:
                release_first.set()
                self.assertTrue(first_finished.wait(timeout=1))

        self.assertEqual(self.plugin._cache["gemini"], new_result)
        self.assertEqual(self.plugin._auth_failure_counts["gemini"], 0)
        self.assertFalse(any(error["provider"] == "gemini" for error in self.plugin._last_errors))
        self.assertNotIn("gemini", self.plugin._refresh_claims)

    def test_concurrent_provider_auth_failures_update_independent_suppression_counters(self) -> None:
        failure_barrier = self.plugin.threading.Barrier(2, timeout=2)

        def auth_failure_fetch():
            failure_barrier.wait()
            raise urllib.error.HTTPError("https://example.test", 401, "unauthorized", {}, None)

        with self.plugin._cache_lock:
            self.plugin._auth_failure_counts["claude"] = self.plugin.AUTH_FAILURE_SUPPRESSION_THRESHOLD - 1
            self.plugin._auth_failure_counts["codex"] = self.plugin.AUTH_FAILURE_SUPPRESSION_THRESHOLD - 1

        with patch.dict(
            self.plugin._PROVIDERS,
            {"claude": auth_failure_fetch, "codex": auth_failure_fetch},
            clear=True,
        ):
            self.plugin._start_refresh_if_needed(("claude", "codex"))
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                counts = self.plugin._auth_failure_counts_snapshot()
                if (
                    counts["claude"] == self.plugin.AUTH_FAILURE_SUPPRESSION_THRESHOLD
                    and counts["codex"] == self.plugin.AUTH_FAILURE_SUPPRESSION_THRESHOLD
                ):
                    break
                time.sleep(0.01)

        counts = self.plugin._auth_failure_counts_snapshot()
        self.assertEqual(counts["claude"], self.plugin.AUTH_FAILURE_SUPPRESSION_THRESHOLD)
        self.assertEqual(counts["codex"], self.plugin.AUTH_FAILURE_SUPPRESSION_THRESHOLD)
        self.assertTrue(self.plugin._provider_auth_suppressed("claude", counts))
        self.assertTrue(self.plugin._provider_auth_suppressed("codex", counts))
        self.assertNotIn("claude", self.plugin._refresh_claims)
        self.assertNotIn("codex", self.plugin._refresh_claims)

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
            ({"session_pct": 42.0, "reset_iso": ""}, True, "🟢 C:42% (stale)"),
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

    def test_transient_errors_render_retained_values_as_stale_and_success_clears_marker(self) -> None:
        cases = {
            "claude": (
                {"session_pct": 42.0, "reset_iso": ""},
                {"session_pct": 18.0, "reset_iso": ""},
                "🟢 C:42% (stale)",
                "🟢 C:18%",
            ),
            "codex": (
                {"session_pct": 42.0, "reset_iso": ""},
                {"session_pct": 19.0, "reset_iso": ""},
                "🟢 Cx:42% (stale)",
                "🟢 Cx:19%",
            ),
            "gemini": (
                {"key_valid": True, "probe": {"status": 200}},
                {"key_valid": True, "probe": {"status": 403}},
                "🟢 Ge:OK (stale)",
                "🟡 Ge:403",
            ),
            "glm": (
                {"session_pct": 28.0, "reset_iso": ""},
                {"session_pct": 21.0, "reset_iso": ""},
                "🟢 G:28% (stale)",
                "🟢 G:21%",
            ),
            "deepseek": (
                {"is_available": True, "currency": "USD", "balance": 3.5},
                {"is_available": True, "currency": "USD", "balance": 4.5},
                "🟢 D:$3.50 (stale)",
                "🟢 D:$4.50",
            ),
        }

        for provider, (cached, recovered, stale_segment, recovered_segment) in cases.items():
            fetcher = MagicMock(
                side_effect=(urllib.error.URLError("network unavailable"), recovered)
            )
            self.plugin._cache[provider] = cached

            with self.subTest(provider=provider), patch.object(
                self.plugin.time, "time", return_value=200.0
            ), patch.dict(self.plugin._PROVIDERS, {provider: fetcher}, clear=True):
                self.plugin._refresh_cache((provider,))
                self.assertIs(self.plugin._cache[provider], cached)
                self.assertIn(provider, self.plugin._cache["stale"])
                self.assertEqual(
                    self.plugin._render_provider(provider, self.plugin._cache[provider], True),
                    stale_segment,
                )
                with patch.object(self.plugin, "_start_refresh_if_needed"):
                    rendered = self.plugin.on_status_bar_render(
                        {"quota_status": {"providers": [provider]}}
                    )
                self.assertEqual(rendered, stale_segment)

                self.plugin._refresh_cache((provider,))
                self.assertEqual(self.plugin._cache[provider], recovered)
                self.assertNotIn(provider, self.plugin._cache["stale"])
                self.assertEqual(
                    self.plugin._render_provider(provider, self.plugin._cache[provider], False),
                    recovered_segment,
                )
                with patch.object(self.plugin, "_start_refresh_if_needed"):
                    rendered = self.plugin.on_status_bar_render(
                        {"quota_status": {"providers": [provider]}}
                    )
                self.assertEqual(rendered, recovered_segment)

    def test_transient_errors_without_retained_data_keep_existing_error_form(self) -> None:
        for provider in self.plugin.PROVIDERS:
            self.plugin._cache[provider] = None
            self.plugin._cache["stale"].discard(provider)

            with self.subTest(provider=provider), patch.object(
                self.plugin.time, "time", return_value=200.0
            ), patch.dict(
                self.plugin._PROVIDERS,
                {provider: MagicMock(side_effect=urllib.error.URLError("network unavailable"))},
                clear=True,
            ), patch.object(self.plugin, "cloudcode_login_pending", return_value=False):
                self.plugin._refresh_cache((provider,))

            self.assertIsNone(self.plugin._cache[provider])
            self.assertIn(provider, self.plugin._cache["stale"])
            self.assertEqual(
                self.plugin._render_provider(provider, None, True),
                f"🔴 {self.plugin.PROVIDER_SHORT[provider]}:?",
            )
            with patch.object(self.plugin, "_start_refresh_if_needed"), patch.object(
                self.plugin, "cloudcode_login_pending", return_value=False
            ):
                rendered = self.plugin.on_status_bar_render(
                    {"quota_status": {"providers": [provider]}}
                )
            self.assertEqual(rendered, f"🔴 {self.plugin.PROVIDER_SHORT[provider]}:?")

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

    def test_concurrent_auth_failure_recordings_are_atomic(self) -> None:
        worker_count = 16
        start_barrier = self.plugin.threading.Barrier(worker_count, timeout=2)
        worker_errors: list[BaseException] = []
        threading_module = self.plugin.threading

        class RaceDetectingCounterDict(dict[str, int]):
            """Make an unguarded read-modify-write lose an update deterministically."""

            def __init__(self) -> None:
                super().__init__(claude=0)
                self._state_lock = threading_module.Lock()
                self._second_reader_started = threading_module.Event()
                self._active_readers = 0
                self._get_calls = 0
                self.overlap_observed = False

            def get(self, key: str, default: int = 0) -> int:
                value = super().get(key, default)
                with self._state_lock:
                    self._get_calls += 1
                    call_number = self._get_calls
                    self._active_readers += 1
                    if self._active_readers > 1:
                        self.overlap_observed = True
                        self._second_reader_started.set()
                try:
                    if call_number == 1:
                        self._second_reader_started.wait(timeout=0.5)
                    return value
                finally:
                    with self._state_lock:
                        self._active_readers -= 1

        counters = RaceDetectingCounterDict()

        def record_failure() -> None:
            try:
                start_barrier.wait()
                self.plugin._record_auth_failure("claude")
            except BaseException as exc:
                worker_errors.append(exc)

        workers = [
            self.plugin.threading.Thread(target=record_failure, daemon=True)
            for _ in range(worker_count)
        ]
        with patch.object(self.plugin, "_auth_failure_counts", counters):
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=3)

            self.assertEqual(worker_errors, [])
            self.assertFalse(counters.overlap_observed)
            self.assertEqual(
                self.plugin._auth_failure_counts_snapshot()["claude"],
                worker_count,
            )

        self.assertTrue(all(not worker.is_alive() for worker in workers))

    def test_public_render_drives_suppression_background_refresh_and_recovery(self) -> None:
        auth_error = urllib.error.HTTPError(
            "https://example.test", 401, "unauthorized", {}, None
        )
        claude_fetcher = MagicMock(
            side_effect=(
                auth_error,
                auth_error,
                auth_error,
                {"session_pct": 18.0, "reset_iso": ""},
            )
        )
        codex_fetcher = MagicMock(
            return_value={"session_pct": 12.0, "reset_iso": ""}
        )

        with patch.dict(
            self.plugin._PROVIDERS,
            {"claude": claude_fetcher, "codex": codex_fetcher},
            clear=True,
        ):
            first = self._render_with_synchronous_refresh(
                ("claude", "codex"), now=200.0
            )
            second = self._render_with_synchronous_refresh(
                ("claude", "codex"), now=500.0
            )
            suppressed = self._render_with_synchronous_refresh(
                ("claude", "codex"), now=800.0
            )
            suppressed_counts = self.plugin._auth_failure_counts_snapshot()
            recovered = self._render_with_synchronous_refresh(
                ("claude", "codex"), now=1100.0
            )

        self.assertEqual(first, "🔴 C:? │ 🟢 Cx:12%")
        self.assertEqual(second, "🔴 C:? │ 🟢 Cx:12%")
        self.assertEqual(suppressed, "🟢 Cx:12%")
        self.assertEqual(suppressed_counts["claude"], 3)
        self.assertEqual(suppressed_counts["codex"], 0)
        self.assertEqual(recovered, "🟢 C:18% │ 🟢 Cx:12%")
        self.assertEqual(self.plugin._auth_failure_counts_snapshot()["claude"], 0)
        self.assertEqual(claude_fetcher.call_count, 4)
        self.assertEqual(codex_fetcher.call_count, 4)

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

    def test_glm_fallback_success_after_auth_rejection_resets_auth_counter(self) -> None:
        quota_api = load_quota_api()
        primary_auth_error = urllib.error.HTTPError(
            "https://api.z.ai/api/monitor/usage/quota/limit",
            401,
            "unauthorized",
            {},
            None,
        )
        self.plugin._auth_failure_counts["glm"] = 2

        with patch.dict(quota_api.os.environ, {"GLM_API_KEY": "glm-raw"}, clear=True), patch.object(
            quota_api,
            "fetch_json",
            side_effect=(primary_auth_error, {"data": {"availableLimitPercentage": 91}}),
        ), patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"glm": quota_api.fetch_glm_quota},
            clear=True,
        ):
            self.plugin._refresh_cache(("glm",))

        self.assertEqual(self.plugin._auth_failure_counts["glm"], 0)
        self.assertEqual(self.plugin._cache["glm"]["host"], "https://open.bigmodel.cn")
        self.assertNotIn("glm", self.plugin._cache["stale"])

    def test_glm_auth_accounting_uses_combined_host_outcome(self) -> None:
        quota_api = load_quota_api()
        cases = (
            (
                (
                    urllib.error.HTTPError("https://api.z.ai", 401, "unauthorized", {}, None),
                    urllib.error.HTTPError("https://open.bigmodel.cn", 500, "server error", {}, None),
                ),
                0,
                "http",
            ),
            (
                (
                    urllib.error.HTTPError("https://api.z.ai", 403, "forbidden", {}, None),
                    urllib.error.HTTPError("https://open.bigmodel.cn", 503, "unavailable", {}, None),
                ),
                0,
                "http",
            ),
            (
                (
                    urllib.error.HTTPError("https://api.z.ai", 500, "server error", {}, None),
                    urllib.error.HTTPError("https://open.bigmodel.cn", 503, "unavailable", {}, None),
                ),
                0,
                "http",
            ),
        )

        for host_errors, expected_auth_failures, expected_error_type in cases:
            self.plugin._auth_failure_counts["glm"] = 0
            self.plugin._last_errors.clear()
            with self.subTest(error_type=expected_error_type), patch.dict(
                quota_api.os.environ,
                {"GLM_API_KEY": "glm-raw"},
                clear=True,
            ), patch.object(quota_api, "fetch_json", side_effect=host_errors), patch.object(
                self.plugin.time,
                "time",
                return_value=200.0,
            ), patch.dict(
                self.plugin._PROVIDERS,
                {"glm": quota_api.fetch_glm_quota},
                clear=True,
            ):
                self.plugin._refresh_cache(("glm",))

            self.assertEqual(self.plugin._auth_failure_counts["glm"], expected_auth_failures)
            self.assertEqual(self.plugin._last_errors[-1]["type"], expected_error_type)

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

    def test_missing_credentials_leave_auth_failure_counter_unchanged(self) -> None:
        self.plugin._auth_failure_counts["gemini"] = 2

        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"gemini": MagicMock(return_value=None)},
            clear=True,
        ):
            self.plugin._refresh_cache(("gemini",))

        self.assertEqual(self.plugin._auth_failure_counts["gemini"], 2)
        self.assertIsNone(self.plugin._cache["gemini"])
        self.assertEqual(self.plugin._cache["next_retry"]["gemini"], 200.0 + self.plugin.AUTH_RETRY_TTL)

    def test_missing_credentials_do_not_unsuppress_previously_auth_failed_provider(self) -> None:
        self.plugin._auth_failure_counts["gemini"] = self.plugin.AUTH_FAILURE_SUPPRESSION_THRESHOLD

        with patch.object(self.plugin.time, "time", return_value=200.0), patch.dict(
            self.plugin._PROVIDERS,
            {"gemini": MagicMock(return_value=None)},
            clear=True,
        ):
            self.plugin._refresh_cache(("gemini",))

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"quota_status": {"providers": ["gemini"]}})

        self.assertEqual(
            self.plugin._auth_failure_counts["gemini"],
            self.plugin.AUTH_FAILURE_SUPPRESSION_THRESHOLD,
        )
        self.assertIsNone(rendered)

    def test_gemini_agy_api_expired_token_uses_scrape_without_incrementing_counter(self) -> None:
        agy_auth_error = urllib.error.HTTPError(
            "https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
            401,
            "unauthorized",
            {},
            None,
        )
        scrape_result = {
            "agy_scrape": True,
            "remaining_fraction": 0.8,
            "model_count": 1,
            "groups": [{"label": "3F", "remaining": 0.8, "used_pct": 20, "reset": "", "model_count": 1}],
        }

        with (
            patch.object(self.plugin.time, "time", return_value=200.0),
            patch.object(self.plugin, "_fetch_gemini_agy_api", side_effect=agy_auth_error),
            patch.object(self.plugin, "_fetch_gemini_cloudcode", return_value=None),
            patch.object(self.plugin, "_fetch_gemini_agy", return_value=scrape_result),
            patch.object(self.plugin, "_fetch_gemini_probe", return_value=None),
            patch.dict(self.plugin._PROVIDERS, {"gemini": self.plugin._fetch_gemini}, clear=True),
        ):
            self.plugin._refresh_cache(("gemini",))

        self.assertEqual(self.plugin._auth_failure_counts["gemini"], 0)
        self.assertEqual(self.plugin._cache["gemini"], scrape_result)

    def test_gemini_cloudcode_auth_failure_increments_counter(self) -> None:
        with (
            patch.object(self.plugin.time, "time", return_value=200.0),
            patch.object(self.plugin, "_fetch_gemini_agy_api", return_value=None),
            patch.object(
                self.plugin,
                "_fetch_gemini_cloudcode",
                return_value={"cloudcode": True, "cloudcode_error": "auth"},
            ),
            patch.object(self.plugin, "_fetch_gemini_agy") as scrape,
            patch.object(self.plugin, "_fetch_gemini_probe", return_value=None),
            patch.dict(self.plugin._PROVIDERS, {"gemini": self.plugin._fetch_gemini}, clear=True),
        ):
            self.plugin._refresh_cache(("gemini",))

        self.assertEqual(self.plugin._auth_failure_counts["gemini"], 1)
        scrape.assert_not_called()

    def test_gemini_authenticated_cloudcode_success_overrides_agy_api_auth_failure(self) -> None:
        agy_auth_error = urllib.error.HTTPError(
            "https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
            401,
            "unauthorized",
            {},
            None,
        )
        cloudcode_success = {
            "cloudcode": True,
            "remaining_fraction": 0.75,
            "model_count": 1,
            "groups": [{"label": "3F", "remaining": 0.75, "used_pct": 25, "reset": "", "model_count": 1}],
        }
        self.plugin._auth_failure_counts["gemini"] = 2

        with (
            patch.object(self.plugin.time, "time", return_value=200.0),
            patch.object(self.plugin, "_fetch_gemini_agy_api", side_effect=agy_auth_error),
            patch.object(self.plugin, "_fetch_gemini_cloudcode", return_value=cloudcode_success),
            patch.object(self.plugin, "_fetch_gemini_agy") as scrape,
            patch.dict(self.plugin._PROVIDERS, {"gemini": self.plugin._fetch_gemini}, clear=True),
        ):
            self.plugin._refresh_cache(("gemini",))

        self.assertEqual(self.plugin._auth_failure_counts["gemini"], 0)
        self.assertEqual(self.plugin._cache["gemini"], cloudcode_success)
        self.assertEqual(self.plugin._render_provider("gemini", self.plugin._cache["gemini"], False), "🟢 Ge:25%")
        scrape.assert_not_called()

    def test_gemini_scrape_success_is_neutral_for_auth_failure_counter(self) -> None:
        scrape_result = {
            "agy_scrape": True,
            "remaining_fraction": 0.7,
            "model_count": 1,
            "groups": [{"label": "3F", "remaining": 0.7, "used_pct": 30, "reset": "", "model_count": 1}],
        }
        self.plugin._auth_failure_counts["gemini"] = 2

        with (
            patch.object(self.plugin.time, "time", return_value=200.0),
            patch.object(self.plugin, "_fetch_gemini_agy_api", return_value=None),
            patch.object(self.plugin, "_fetch_gemini_cloudcode", return_value=None),
            patch.object(self.plugin, "_fetch_gemini_agy", return_value=scrape_result),
            patch.object(self.plugin, "_fetch_gemini_probe") as probe,
            patch.dict(self.plugin._PROVIDERS, {"gemini": self.plugin._fetch_gemini}, clear=True),
        ):
            self.plugin._refresh_cache(("gemini",))

        self.assertEqual(self.plugin._auth_failure_counts["gemini"], 2)
        self.assertEqual(self.plugin._cache["gemini"], scrape_result)
        self.assertEqual(self.plugin._render_provider("gemini", scrape_result, False), "🟢 Ge:30%")
        probe.assert_not_called()

    def test_gemini_invalid_api_key_counts_toward_auth_suppression(self) -> None:
        invalid_key_result = {
            "key_valid": False,
            "auth_error": True,
            "http_status": 400,
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

    def test_gemini_api_key_transport_failures_do_not_count_as_auth_failures(self) -> None:
        quota_api = load_quota_api()
        failures = (
            (urllib.error.URLError("network unavailable"), "URLError"),
            (socket.timeout("request timed out"), "TimeoutError"),
            (
                urllib.error.HTTPError(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    500,
                    "server error",
                    {},
                    None,
                ),
                "http",
            ),
        )

        for failure, expected_error_type in failures:
            self.plugin._auth_failure_counts["gemini"] = 2
            self.plugin._last_errors.clear()
            with self.subTest(failure=type(failure).__name__), patch.dict(
                quota_api.os.environ,
                {"GOOGLE_API_KEY": "configured-key"},
                clear=True,
            ), patch.object(
                quota_api,
                "fetch_json",
                side_effect=failure,
            ), patch.object(
                self.plugin.time,
                "time",
                return_value=200.0,
            ), patch.object(
                self.plugin,
                "_fetch_gemini_agy_api",
                return_value=None,
            ), patch.object(
                self.plugin,
                "_fetch_gemini_agy",
                return_value=None,
            ), patch.object(
                self.plugin,
                "_fetch_gemini_cloudcode",
                return_value=None,
            ), patch.object(
                self.plugin,
                "fetch_gemini_quota",
                new=quota_api.fetch_gemini_quota,
            ), patch.dict(
                self.plugin._PROVIDERS,
                {"gemini": self.plugin._fetch_gemini},
                clear=True,
            ):
                self.plugin._refresh_cache(("gemini",))

            self.assertEqual(self.plugin._auth_failure_counts["gemini"], 2)
            self.assertEqual(self.plugin._last_errors[-1]["type"], expected_error_type)

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
        self.plugin._cache["stale"].add("claude")

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

    def test_render_width_60_drops_whole_next_segment_after_stale_segments(self) -> None:
        self._seed_wide_provider_cache()
        self.plugin._cache["stale"].update(("claude", "codex"))

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            untrimmed = self.plugin.on_status_bar_render()
            rendered = self.plugin.on_status_bar_render({"terminal_width": 60})

        self.assertIsNotNone(untrimmed)
        self.assertGreater(self.plugin._display_width(untrimmed), 60)
        self.assertEqual(
            rendered,
            "🟡 C:5h:24% 7d:61% (stale) │ 🟡 Cx:P:22% S:63% (stale)",
        )
        self.assertLessEqual(self.plugin._display_width(rendered), 60)
        next_segment = self.plugin._render_provider("gemini", self.plugin._cache["gemini"], False)
        boundary_candidate = self.plugin.STATUS_SEGMENT_SEPARATOR.join((rendered, next_segment))
        self.assertLessEqual(
            self.plugin._display_width(rendered + self.plugin.STATUS_SEGMENT_SEPARATOR), 60
        )
        self.assertGreater(self.plugin._display_width(boundary_candidate), 60)
        self.assertEqual(
            rendered.split(self.plugin.STATUS_SEGMENT_SEPARATOR),
            [
                "🟡 C:5h:24% 7d:61% (stale)",
                "🟡 Cx:P:22% S:63% (stale)",
            ],
        )
        self.assertFalse(rendered.startswith(self.plugin.STATUS_SEGMENT_SEPARATOR))
        self.assertFalse(rendered.endswith(self.plugin.STATUS_SEGMENT_SEPARATOR))

    def test_render_width_61_does_not_trim(self) -> None:
        self._seed_wide_provider_cache()

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"terminal_width": 61})

        self.assertIsNotNone(rendered)
        self.assertGreater(self.plugin._display_width(rendered), 61)
        self.assertEqual(
            rendered,
            "🟡 C:5h:24% 7d:61% │ 🟡 Cx:P:22% S:63% │ 🟡 Ge:CREDITS 600/1500 │ 🟢 G:28% │ 🟢 D:$3.50",
        )

    def test_render_width_above_60_does_not_apply_fixed_60_character_cap(self) -> None:
        self._seed_wide_provider_cache()

        with patch.object(self.plugin, "_start_refresh_if_needed"):
            rendered = self.plugin.on_status_bar_render({"terminal_width": 80})

        self.assertIsNotNone(rendered)
        self.assertGreater(self.plugin._display_width(rendered), 80)
        self.assertEqual(
            rendered,
            "🟡 C:5h:24% 7d:61% │ 🟡 Cx:P:22% S:63% │ 🟡 Ge:CREDITS 600/1500 │ 🟢 G:28% │ 🟢 D:$3.50",
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
