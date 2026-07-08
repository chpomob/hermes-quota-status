"""Scrape agy /usage output for quota status.

Agy's `/usage` command shows per-model remaining percentages that come from
a different data source than the REST fetchAvailableModels API. This module
launches agy in a headless tmux session, sends /usage, and parses the output.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

TMUX_SESSION = "hermes-agy-quota"
AGY_BIN = "agy"
STARTUP_SECONDS = 3
QUOTA_SECONDS = 5

ModelQuota = dict[str, Any]  # {"name": str, "remaining_pct": float, "used_pct": int, "reset_hours": int}


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
        raw = lines[i].strip()

        # Match model name (e.g. "Gemini 3.5 Flash (Medium)")
        name_match = re.match(r"^(Gemini|Claude|GPT)", raw)
        if not name_match:
            i += 1
            continue

        model_name = raw

        # Next line: bar with percentage at the end
        remaining_pct = 100.0
        reset_hours = 0
        if i + 1 < len(lines):
            bar_line = lines[i + 1].strip()
            pct_match = re.search(r"(\d+)%\s*$", bar_line)
            if pct_match:
                remaining_pct = float(pct_match.group(1))

        # Next+1 line may be reset info or scroll indicator — try both
        if i + 2 < len(lines):
            reset_line = lines[i + 2].strip()
            reset_match = re.search(r"Refreshes in (\d+)h\s+(\d+)m", reset_line)
            if reset_match:
                reset_hours = int(reset_match.group(1))
                i += 3  # consumed name + bar + reset
            else:
                i += 2  # consumed name + bar (reset info scrolled off)
        else:
            i += 2

        models.append({
            "name": model_name,
            "remaining_pct": remaining_pct,
            "used_pct": round(100 - remaining_pct),
            "reset_hours": reset_hours,
        })

    return models


def fetch_agy_quota() -> list[ModelQuota] | None:
    """Run agy in tmux, send /usage, parse and return model quotas."""
    # Clean up any stale session
    subprocess.run(
        ["tmux", "kill-session", "-t", TMUX_SESSION],
        capture_output=True, timeout=3,
    )
    time.sleep(0.1)

    proc = subprocess.run(
        ["tmux", "new-session", "-d", "-s", TMUX_SESSION],
        capture_output=True, timeout=5,
    )
    if proc.returncode != 0:
        logger.warning("failed to create tmux session")
        return None

    subprocess.run(
        ["tmux", "send-keys", "-t", TMUX_SESSION, AGY_BIN, "Enter"],
        capture_output=True, timeout=5,
    )
    time.sleep(STARTUP_SECONDS)

    subprocess.run(
        ["tmux", "send-keys", "-t", TMUX_SESSION, "/usage", "Enter"],
        capture_output=True, timeout=5,
    )
    time.sleep(QUOTA_SECONDS)

    # Capture the full pane content (scrollback buffer)
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", TMUX_SESSION, "-p", "-S", "-"],
        capture_output=True, timeout=5, text=True,
    )
    output = result.stdout

    # Dismiss /usage overlay and cleanup
    subprocess.run(
        ["tmux", "send-keys", "-t", TMUX_SESSION, "Escape"],
        capture_output=True, timeout=2,
    )
    time.sleep(0.2)
    subprocess.run(
        ["tmux", "send-keys", "-t", TMUX_SESSION, "exit", "Enter"],
        capture_output=True, timeout=2,
    )
    time.sleep(0.5)
    subprocess.run(
        ["tmux", "kill-session", "-t", TMUX_SESSION],
        capture_output=True, timeout=3,
    )

    if not output:
        logger.warning("agy /usage returned no output")
        return None

    models = _capture_usage(output)
    if not models:
        logger.warning("failed to parse agy /usage output")
        return None

    return models
