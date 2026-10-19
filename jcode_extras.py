#!/usr/bin/env python3
# MIT License - Copyright (c) 2026 Prof. Dr. Joern Fischer (full text: see README.md)
# Additions for the mejorador-audio project (c) 2026 Francis Garcia Reyes, same license.
"""Additions to jcode for the mejorador-audio project (plan step 1.0.b0).

Kept apart from jcode.py so the diff against Prof. Fischer's original stays
small and reviewable. Nothing in this module talks to a model or has side
effects at import time, so every piece is testable on its own:

    load_config         reads the new settings.json keys, with safe defaults
    MODES               'executor' (default: run, read, diagnose, report)
                        and 'builder' (the original jcode behaviour)
    is_under_reports    the only place executor mode may write to
    classify_rate_limit tells a daily quota (switch model) from a
                        per-minute one (wait and retry)
    FallbackChain       which model to try next when a daily quota is gone
    git_changes         files changed in the sandbox outside reports/
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

# ----------------------------------------------------------------- Constants
# Compaction threshold: every request re-sends the whole history and the free
# Gemini Flash Lite tier allows ~250 K tokens per minute (plan, 2026-10-19).
# At 60 K, four consecutive requests still fit into one minute.
DEFAULT_COMPACT_THRESHOLD_TOKENS = 60_000
# Upper bound for one run_command call. Project scripts (parameter sweeps,
# browser batteries) can run for hours; jcode waits without sending requests.
DEFAULT_MAX_COMMAND_TIMEOUT_S = 4 * 3600
REPORTS_DIR_NAME = "reports"            # executor mode may only write here
QUOTA_TIMEZONE = "America/Los_Angeles"  # Gemini daily quotas reset at midnight Pacific
MAX_RETRY_DELAY_S = 120.0               # never trust a server-suggested wait beyond this

EXECUTOR, BUILDER = "executor", "builder"
MODE_ALIASES = {"executor": EXECUTOR, "ejecutor": EXECUTOR,
                "builder": BUILDER, "constructor": BUILDER}
DEFAULT_MAX_STEPS = {EXECUTOR: 15, BUILDER: 40}  # plan: fewer steps = fewer requests

# Tools offered to the model in executor mode. Missing on purpose: edit_file,
# insert_lines, delete_lines (no code changes), web_search and fetch_url (no
# internet input), take_screenshot (writes into the sandbox; Playwright
# scripts save their own screenshots, which view_image can open).
EXECUTOR_TOOL_NAMES = ("list_files", "read_file", "search_files", "write_file",
                       "run_command", "view_image", "view_pdf_page")

EXECUTOR_PROMPT = (
    "You are an EXECUTOR agent for a software project. Your working directory "
    "is the project directory: give paths relative to it, e.g. 'tests/run.py' - "
    "NEVER with a leading 'code/' and NEVER absolute.\n\n"
    "Your job: run what the task asks (usually a script or a test suite), read "
    "its output and reports, diagnose failures and report them. You NEVER fix, "
    "edit, refactor or format source code, tests, configuration or AGENTS.md - "
    "neither with tools nor through shell commands (no 'sed -i', no redirection "
    "into project files, no git checkout/commit/push/restore, no 'pip install' "
    "unless the task says so). When something fails, your work ends with the "
    "diagnosis; a person decides what to change.\n\n"
    "How to work:\n"
    "1. Run exactly the commands the task names, with run_command. Scripts in "
    "this project can run for hours: pass a timeout (seconds) large enough for "
    "them, e.g. timeout=7200, instead of polling in a loop.\n"
    "2. Read only what you need: search_files before read_file. Use view_image "
    "or view_pdf_page only when the task asks you to look at an image or page "
    "- images cost many tokens. Describe only what you actually see.\n"
    f"3. You may write files ONLY under {REPORTS_DIR_NAME}/ (write_file refuses "
    "anything else): a report, or a minimal script that reproduces a failure.\n"
    "4. For every failure state: which test or command failed; the relevant "
    "output, shortened; the probable cause with the file and line it points "
    f"to; a minimal reproducible case (saved in {REPORTS_DIR_NAME}/) when "
    "possible; how sure you are. Say plainly what you did not check.\n"
    "5. Then, without a tool call, reply with a short summary.\n\n"
    "Language: reply in the same language as the task."
)


# ------------------------------------------------------------- Configuration
@dataclass(frozen=True)
class ChainEntry:
    """One model in the automatic fallback chain."""

    provider: str                        # key in settings.json "providers"
    model: str                           # model id as the provider expects it
    reasoning_effort: str | None = None  # None = do not send the parameter
    compact_threshold: int | None = None  # tighter limit for small-quota models


@dataclass(frozen=True)
class Config:
    """The settings this fork adds to settings.json, after validation."""

    default_mode: str = EXECUTOR
    max_steps: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_MAX_STEPS))
    compact_threshold: int = DEFAULT_COMPACT_THRESHOLD_TOKENS
    max_command_timeout: int = DEFAULT_MAX_COMMAND_TIMEOUT_S
    chain: tuple[ChainEntry, ...] = ()


def _positive_int(value, fallback: int, name: str, warnings: list[str]) -> int:
    """Returns value if it is a positive int, else fallback (and records why)."""
    if value is None:
        return fallback
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    warnings.append(f"{name}={value!r} is not a positive integer - using {fallback}")
    return fallback


def _parse_chain(raw, providers: dict, warnings: list[str]) -> tuple[ChainEntry, ...]:
    """Builds the fallback chain; skips entries that cannot work."""
    if not isinstance(raw, list):
        if raw is not None:
            warnings.append("model_fallback must be a list - automatic fallback disabled")
        return ()
    chain = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not item.get("provider") or not item.get("model"):
            warnings.append(f"model_fallback[{i}] needs 'provider' and 'model' - skipped")
            continue
        if item["provider"] not in providers:
            warnings.append(f"model_fallback[{i}]: provider '{item['provider']}' "
                            "is not defined under 'providers' - skipped")
            continue
        threshold = item.get("compact_threshold_tokens")
        chain.append(ChainEntry(
            provider=item["provider"],
            model=item["model"],
            reasoning_effort=item.get("reasoning_effort"),
            compact_threshold=_positive_int(threshold, 0, f"model_fallback[{i}]"
                                            ".compact_threshold_tokens", warnings) or None,
        ))
    return tuple(chain)


def load_config(settings: dict) -> tuple[Config, list[str]]:
    """Reads this fork's keys from a parsed settings.json.

    Missing keys fall back to the defaults above; invalid values fall back
    too and produce a warning, so a typo never stops jcode from starting.
    Returns (config, warnings).
    """
    warnings: list[str] = []
    mode = MODE_ALIASES.get(str(settings.get("default_mode", EXECUTOR)).lower())
    if mode is None:
        warnings.append(f"default_mode={settings.get('default_mode')!r} unknown - using {EXECUTOR}")
        mode = EXECUTOR
    raw_modes = settings.get("modes") if isinstance(settings.get("modes"), dict) else {}
    max_steps = {}
    for name in (EXECUTOR, BUILDER):
        entry = raw_modes.get(name)
        if entry is not None and not isinstance(entry, dict):  # e.g. "executor": 15
            warnings.append(f"modes.{name} must be an object like {{\"max_steps\": 15}}")
            entry = None
        max_steps[name] = _positive_int((entry or {}).get("max_steps"), DEFAULT_MAX_STEPS[name],
                                        f"modes.{name}.max_steps", warnings)
    config = Config(
        default_mode=mode,
        max_steps=max_steps,
        compact_threshold=_positive_int(settings.get("compact_threshold_tokens"),
                                        DEFAULT_COMPACT_THRESHOLD_TOKENS,
                                        "compact_threshold_tokens", warnings),
        max_command_timeout=_positive_int(settings.get("max_command_timeout_s"),
                                          DEFAULT_MAX_COMMAND_TIMEOUT_S,
                                          "max_command_timeout_s", warnings),
        chain=_parse_chain(settings.get("model_fallback"),
                           settings.get("providers") or {}, warnings),
    )
    return config, warnings


def resolve_mode(name: str) -> str | None:
    """Maps a user-typed mode name (English or Spanish) to its canonical name."""
    return MODE_ALIASES.get(name.strip().lower())


# -------------------------------------------------------------- Write guard
def is_under_reports(path: Path, sandbox: Path) -> bool:
    """True if the (already resolved) path lies inside <sandbox>/reports/."""
    reports = (sandbox / REPORTS_DIR_NAME).resolve()
    return reports in path.parents


# ------------------------------------------------------------- Rate limits
_DAILY_MARKERS = ("perday", "per day", "per_day", "daily")
_RETRY_DELAY = re.compile(r"retry(?:_?delay|\s+in)\W{0,4}(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)


def classify_rate_limit(error: Exception) -> str | None:
    """'daily' or 'minute' for an HTTP 429, None for any other error.

    Gemini answers both limits with 429 RESOURCE_EXHAUSTED; the quota id in
    the message tells them apart (e.g. ...RequestsPerDay... vs
    ...RequestsPerMinute...). An unrecognised 429 counts as 'minute': the
    caller waits and retries, and only switches model if it persists.
    """
    if getattr(error, "status_code", None) != 429:
        return None
    text = str(error).lower()
    return "daily" if any(marker in text for marker in _DAILY_MARKERS) else "minute"


def retry_delay_seconds(error: Exception) -> float | None:
    """Wait time the server suggests in a 429 message, capped; None if absent."""
    match = _RETRY_DELAY.search(str(error))
    return min(float(match.group(1)), MAX_RETRY_DELAY_S) if match else None


def quota_day() -> date:
    """Today's date in the time zone where the daily quotas reset."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(QUOTA_TIMEZONE)).date()
    except Exception:  # noqa: BLE001 - no tz database: UTC is off by hours, still usable
        return datetime.now(timezone.utc).date()


class FallbackChain:
    """Remembers which models ran out of daily quota and picks the next one.

    An exhausted model stays blocked for the rest of its quota day, then
    becomes available again (the caller switches back to the first one).
    """

    def __init__(self, entries: tuple[ChainEntry, ...], day_fn=quota_day) -> None:
        self.entries = entries
        self._day_fn = day_fn
        self._exhausted: dict[tuple[str, str], date] = {}

    def mark_exhausted(self, provider: str, model: str) -> None:
        self._exhausted[(provider, model)] = self._day_fn()

    def is_exhausted(self, entry: ChainEntry) -> bool:
        return self._exhausted.get((entry.provider, entry.model)) == self._day_fn()

    def find(self, provider: str, model: str) -> ChainEntry | None:
        return next((e for e in self.entries
                     if (e.provider, e.model) == (provider, model)), None)

    def next_entry(self, provider: str, model: str) -> ChainEntry | None:
        """First usable entry in chain order that is not the current model."""
        return next((e for e in self.entries
                     if (e.provider, e.model) != (provider, model)
                     and not self.is_exhausted(e)), None)

    def first_available(self) -> ChainEntry | None:
        return next((e for e in self.entries if not self.is_exhausted(e)), None)


# ------------------------------------------------------------ Sandbox check
def git_changes(sandbox: Path) -> list[str] | None:
    """Paths git reports as changed or new in the sandbox, except reports/.

    Returns None when the sandbox is not a git repository or git is missing:
    then nothing can be checked and the caller says so.
    """
    try:
        proc = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                              cwd=sandbox, capture_output=True, text=True, timeout=30,
                              check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    changed = []
    for line in proc.stdout.splitlines():
        path = line[3:].split(" -> ")[-1].strip().strip('"')  # 'XY path' or 'XY old -> new'
        if path and not path.startswith(REPORTS_DIR_NAME + "/"):
            changed.append(path)
    return changed
