"""Scrape agy /usage output for quota status.

Agy's `/usage` command shows per-model remaining percentages that come from
a different data source than the REST fetchAvailableModels API. This module
launches agy in a headless tmux session, sends /usage, and parses the output.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import subprocess
import time
from typing import Literal, TypedDict

logger = logging.getLogger(__name__)

TMUX_SESSION_PREFIX = "hermes-agy-quota"
AGY_BIN = "agy"
STARTUP_SECONDS = 3
QUOTA_SECONDS = 5
ESCAPE_SHUTDOWN_SECONDS = 0.2
EXIT_SHUTDOWN_SECONDS = 0.5

ProviderTag = Literal["gemini", "claude", "gpt"]


class ModelQuota(TypedDict):
    """One quota pool parsed from the Agy usage overlay."""

    provider: ProviderTag
    name: str
    remaining_pct: float
    used_pct: int
    reset_hours: int


_PROVIDER_TAGS: dict[str, ProviderTag] = {
    "Gemini": "gemini",
    "Claude": "claude",
    "GPT": "gpt",
}
_MODEL_HEADER_RE = re.compile(r"^(?P<provider>Gemini|Claude|GPT)(?:\s+|(?<=GPT)-)\S(?:.*\S)?$")
_PERCENTAGE_RE = re.compile(r"^[█░]+\s+(?P<remaining>100|\d{1,2})%\s*$")
_RESET_RE = re.compile(r"^Refreshes\s+in\s+(?P<hours>\d+)h\s+(?P<minutes>\d+)m\s*$")
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def _plain_line(raw_line: str) -> str:
    """Remove terminal styling before applying anchored TUI line patterns."""
    return _ANSI_ESCAPE_RE.sub("", raw_line).strip()


def _new_tmux_session_name() -> str:
    """Return a process-identifiable, collision-resistant session name."""
    return f"{TMUX_SESSION_PREFIX}-{os.getpid()}-{secrets.token_hex(8)}"


def _kill_tmux_session(session_name: str) -> None:
    """Best-effort cleanup that never obscures the scrape's real outcome."""
    try:
        subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            capture_output=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("failed to clean up tmux session %s", session_name, exc_info=True)


def _capture_usage(output: str) -> list[ModelQuota]:
    """Parse agy /usage TUI output into structured quota data.

    Handles truncated output (the TUI shows only ~14 of ~33 lines). We extract
    whatever models appear on screen — the key info (Gemini pool % remaining)
    is the same for all Gemini models.
    """
    models: list[ModelQuota] = []
    lines = output.split("\n")

    # Walk lines looking for model blocks: name line → bar line → optional reset line
    i = 0
    while i < len(lines):
        raw = _plain_line(lines[i])

        # Match model name (e.g. "Gemini 3.5 Flash (Medium)")
        name_match = _MODEL_HEADER_RE.fullmatch(raw)
        if not name_match:
            i += 1
            continue

        model_name = raw

        # A header is not enough to establish quota. Fail closed if the
        # adjacent usage bar is missing, malformed, or outside 0..100.
        if i + 1 >= len(lines):
            i += 1
            continue
        bar_line = _plain_line(lines[i + 1])
        pct_match = _PERCENTAGE_RE.fullmatch(bar_line)
        if pct_match is None:
            i += 1
            continue
        remaining_pct = float(pct_match.group("remaining"))
        if not 0.0 <= remaining_pct <= 100.0:
            i += 1
            continue

        # Next+1 line may be reset info or scroll indicator — try both
        reset_hours = 0
        if i + 2 < len(lines):
            reset_line = _plain_line(lines[i + 2])
            reset_match = _RESET_RE.fullmatch(reset_line)
            if reset_match:
                reset_hours = int(reset_match.group("hours"))
                i += 3  # consumed name + bar + reset
            else:
                i += 2  # consumed name + bar (reset info scrolled off)
        else:
            i += 2

        models.append({
            "provider": _PROVIDER_TAGS[name_match.group("provider")],
            "name": model_name,
            "remaining_pct": remaining_pct,
            "used_pct": round(100 - remaining_pct),
            "reset_hours": reset_hours,
        })

    return models


def fetch_agy_quota() -> list[ModelQuota] | None:
    """Run agy in tmux, send /usage, parse and return model quotas."""
    session_name = _new_tmux_session_name()
    try:
        proc = subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name],
            capture_output=True,
            timeout=5,
        )
        if proc.returncode != 0:
            logger.warning("failed to create tmux session")
            return None

        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, AGY_BIN, "Enter"],
            capture_output=True,
            timeout=5,
        )
        time.sleep(STARTUP_SECONDS)

        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "/usage", "Enter"],
            capture_output=True,
            timeout=5,
        )
        time.sleep(QUOTA_SECONDS)

        # Capture the full pane content (scrollback buffer).
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session_name, "-p", "-S", "-"],
            capture_output=True,
            timeout=5,
            text=True,
        )
        output = result.stdout

        # Graceful shutdown is useful when Agy can process it, while the
        # finally block remains the authoritative cleanup path.
        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "Escape"],
            capture_output=True,
            timeout=2,
        )
        time.sleep(ESCAPE_SHUTDOWN_SECONDS)
        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "exit", "Enter"],
            capture_output=True,
            timeout=2,
        )
        time.sleep(EXIT_SHUTDOWN_SECONDS)
    finally:
        _kill_tmux_session(session_name)

    if not output:
        logger.warning("agy /usage returned no output")
        return None

    models = _capture_usage(output)
    if not models:
        logger.warning("failed to parse agy /usage output")
        return None

    return models
