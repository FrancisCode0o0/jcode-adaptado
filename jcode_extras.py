#!/usr/bin/env python3
# MIT License - Copyright (c) 2026 Prof. Dr. Joern Fischer (full text: see README.md)
# Additions for the mejorador-audio and Abschrift projects (c) 2026 Francis Garcia Reyes,
# same license.
"""Additions to jcode for the mejorador-audio project (plan step 1.0.b0) and
the Abschrift project (Abschrift plan step 0.1, extensions E1-E4).

Kept apart from jcode.py so the diff against Prof. Fischer's original stays
small and reviewable. Nothing in this module talks to a model or has side
effects at import time, so every piece is testable on its own:

    parse_cli_args      --project <folder>: settings.json, code/, logs/
                        from that folder instead of next to jcode.py (E1)
    load_config         reads the new settings.json keys, with safe defaults
    MODES               'executor' (default: run, read, diagnose, report),
                        'builder' (the original jcode behaviour) and
                        'verifier' (writes acceptance tests, reviews diffs;
                        exists only if settings.json configures it - E2)
    is_under_any        the only places executor/verifier mode may write to
    classify_rate_limit tells a daily quota (switch model) from a
                        per-minute one (wait and retry)
    FallbackChain       which model to try next when a daily quota is gone
    sanitized_env       environment for run_command without secrets (E4)
    git_changes         files changed in the sandbox outside the allowed dirs
"""
from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath

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
SETTINGS_FILE_NAME = "settings.json"
PROJECT_OPTION = "--project"

EXECUTOR, BUILDER, VERIFIER = "executor", "builder", "verifier"
MODE_ALIASES = {"executor": EXECUTOR, "ejecutor": EXECUTOR,
                "builder": BUILDER, "constructor": BUILDER,
                "verifier": VERIFIER, "verificador": VERIFIER, "pruefer": VERIFIER}
# plan: fewer steps = fewer requests. The verifier writes whole test files,
# so it gets more steps than the executor.
DEFAULT_MAX_STEPS = {EXECUTOR: 15, BUILDER: 40, VERIFIER: 30}

# What happens when no model of model_fallback can be used (E3).
#   "default_provider": as the original fork - at start-up jcode falls back to
#                       default_provider or the local backend.
#   "stop":             jcode never leaves the chain; without a usable chain
#                       model it does not start (Abschrift: the reviewing
#                       model must never silently become a Google model).
FALLBACK_DEFAULT, FALLBACK_STOP = "default_provider", "stop"

# E4: environment variables whose NAME contains one of these parts are not
# passed to run_command, so no shell command (and no model reading its
# output) can see an API key. Plan 1A names KEY, TOKEN and SECRET; PASSWORD,
# PASSWD and CREDENTIAL are added because they hold the same kind of value
# (e.g. GOOGLE_APPLICATION_CREDENTIALS).
SECRET_NAME_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# git reads extra configuration from GIT_CONFIG_COUNT + GIT_CONFIG_KEY_<n> +
# GIT_CONFIG_VALUE_<n> (values may be auth headers). The KEY_ names look
# secret; removing only them makes every git command fail, so the three
# travel together: all removed, or all kept when GIT_CONFIG_COUNT is passed.
_GIT_CONFIG_GROUP = re.compile(r"^GIT_CONFIG_(?:COUNT|KEY_\d+|VALUE_\d+)$")
_GIT_CONFIG_COUNT = "GIT_CONFIG_COUNT"

# Tools offered to the model in executor mode. Missing on purpose: edit_file,
# insert_lines, delete_lines (no code changes), web_search and fetch_url (no
# internet input), take_screenshot (writes into the sandbox; Playwright
# scripts save their own screenshots, which view_image can open).
EXECUTOR_TOOL_NAMES = ("list_files", "read_file", "search_files", "write_file",
                       "run_command", "view_image", "view_pdf_page")
# The verifier gets the same tools; only write_file reaches more places.
VERIFIER_TOOL_NAMES = EXECUTOR_TOOL_NAMES
# Exit condition of a review (Abschrift plan, section 4): an exact answer the
# person can rely on, so the model has no reason to invent findings.
VERIFIER_NO_FINDINGS = "KEINE FEHLER"

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


def verifier_prompt(write_paths: Iterable[str]) -> str:
    """System prompt of verifier mode; names the folders it may write to."""
    folders = ", ".join(f"{path}/" for path in write_paths)
    return (
        "You are a VERIFIER agent for a software project. Another agent builds "
        "the code; you check its work and never build or fix it yourself. Your "
        "working directory is the project directory: give paths relative to it, "
        "e.g. 'tests/acceptance/upload.spec.js' - NEVER with a leading 'code/' "
        "and NEVER absolute.\n\n"
        "A task asks for one of two things:\n"
        "A) Write acceptance tests. Derive every test ONLY from the numbered "
        "criteria of the specification the task names (usually SPEC.md) and "
        "name the criterion number in each test. Do not read the source code "
        "to fit the tests to it: they must hold for any correct "
        "implementation. Run them once to check they load; failing because the "
        "feature is not built yet is expected.\n"
        "B) Review a change (usually a git diff). Check it ONLY against the "
        "documents the task names (usually SPEC.md and STIL.md). Report only "
        "real errors: the violated criterion or rule, file and line, and how to "
        "reproduce it. Keep ERRORS (they block the merge) apart from "
        "SUGGESTIONS (optional, at most 3). Never invent a problem to have "
        "something to report.\n\n"
        f"Exit condition: if a review finds no errors, reply exactly "
        f"\"{VERIFIER_NO_FINDINGS}\" and nothing else. That is a valid and "
        "expected answer.\n\n"
        "Rules:\n"
        f"1. You may write files ONLY under {folders} (write_file refuses "
        "anything else).\n"
        "2. You NEVER change source code, configuration, AGENTS.md or the "
        "specification documents - neither with tools nor through shell "
        "commands (no 'sed -i', no redirection into project files, no git "
        "add/commit/checkout/restore/reset/stash/push). Reading git is fine: "
        "git diff, git log, git show.\n"
        "3. Use run_command to run tests and read-only commands; pass a long "
        "timeout for slow suites instead of polling.\n"
        "4. Read only what you need: search_files before read_file.\n"
        "5. When done, without a tool call, reply with a short summary - or, "
        f"for a review without errors, exactly \"{VERIFIER_NO_FINDINGS}\".\n\n"
        f"Language: reply in the same language as the task; \"{VERIFIER_NO_FINDINGS}\" "
        "stays exactly as written."
    )


# ------------------------------------------------------- Command line (E1)
def parse_cli_args(argv: list[str]) -> tuple[Path | None, list[str], str | None]:
    """Splits jcode's own options off the command line.

    Only '--project <folder>' (or '--project=<folder>') at the START is an
    option; everything after it is the task, as in the original. Returns
    (project folder or None, task words, error message or None). An error
    must stop jcode: silently using the folder next to jcode.py would run
    the task on the wrong project with the wrong models.
    """
    words = list(argv)
    if not words or not words[0].startswith(PROJECT_OPTION):
        return None, words, None
    option = words.pop(0)
    if option == PROJECT_OPTION:
        if not words:
            return None, [], f"{PROJECT_OPTION} needs a folder, e.g. {PROJECT_OPTION} ~/jcode-projekte/abschrift"
        value = words.pop(0)
    elif option.startswith(PROJECT_OPTION + "="):
        value = option[len(PROJECT_OPTION) + 1:]
    else:
        return None, [], f"unknown option {option!r} (did you mean {PROJECT_OPTION}?)"
    if words and words[0].startswith(PROJECT_OPTION):
        return None, [], f"{PROJECT_OPTION} given twice"
    folder, error = _check_project_folder(value)
    return (folder, words, None) if error is None else (None, [], error)


def _check_project_folder(value: str) -> tuple[Path | None, str | None]:
    """Resolves a --project value; the folder must exist and hold settings.json."""
    if not value.strip():
        return None, f"{PROJECT_OPTION} needs a folder"
    folder = Path(value).expanduser().resolve()  # '--project=~/x' is not expanded by the shell
    if not folder.is_dir():
        return None, f"project folder '{folder}' does not exist"
    if not (folder / SETTINGS_FILE_NAME).is_file():
        return None, (f"project folder '{folder}' has no {SETTINGS_FILE_NAME} - "
                      "a project brings its own models and modes")
    return folder, None


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
    # One entry per AVAILABLE mode: 'verifier' only appears when configured.
    max_steps: dict[str, int] = field(
        default_factory=lambda: {m: DEFAULT_MAX_STEPS[m] for m in (EXECUTOR, BUILDER)})
    compact_threshold: int = DEFAULT_COMPACT_THRESHOLD_TOKENS
    max_command_timeout: int = DEFAULT_MAX_COMMAND_TIMEOUT_S
    chain: tuple[ChainEntry, ...] = ()
    verifier_write_paths: tuple[str, ...] = ()  # empty = no verifier mode
    fallback_exhausted: str = FALLBACK_DEFAULT
    env_passthrough: tuple[str, ...] = ()

    @property
    def modes(self) -> tuple[str, ...]:
        """Modes that /mode accepts with this configuration."""
        return tuple(self.max_steps)

    def write_dirs(self, mode: str) -> tuple[str, ...] | None:
        """Folders write_file may write to in a mode; None = anywhere (builder)."""
        if mode == EXECUTOR:
            return (REPORTS_DIR_NAME,)
        if mode == VERIFIER:
            return self.verifier_write_paths
        return None


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


def normalize_write_path(raw) -> str | None:
    """'tests/acceptance/' -> 'tests/acceptance'; None if it could reach
    beyond its own folder (absolute, '..', empty or the whole sandbox)."""
    if not isinstance(raw, str):
        return None
    text = raw.strip().replace("\\", "/")
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return None
    parts = [p for p in PurePosixPath(text).parts if p != "."]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def _parse_verifier(raw_modes: dict, warnings: list[str]) -> tuple[str, ...]:
    """Write paths of verifier mode; () when the mode is not configured."""
    entry = raw_modes.get(VERIFIER)
    if entry is None:
        return ()
    raw_paths = entry.get("write_paths") if isinstance(entry, dict) else None
    if not isinstance(raw_paths, list):
        warnings.append("modes.verifier needs \"write_paths\": [\"tests/acceptance/\", ...] "
                        "- verifier mode disabled")
        return ()
    paths = []
    for raw in raw_paths:
        path = normalize_write_path(raw)
        if path is None:
            warnings.append(f"modes.verifier.write_paths: {raw!r} is not a folder inside "
                            "the project - skipped")
        elif path not in paths:
            paths.append(path)
    if not paths:
        warnings.append("modes.verifier has no valid write_paths - verifier mode disabled")
    return tuple(paths)


def _parse_fallback_exhausted(settings: dict, chain: tuple, warnings: list[str]) -> str:
    value = settings.get("fallback_exhausted", FALLBACK_DEFAULT)
    if value not in (FALLBACK_DEFAULT, FALLBACK_STOP):
        warnings.append(f"fallback_exhausted={value!r} unknown - using {FALLBACK_DEFAULT!r}")
        return FALLBACK_DEFAULT
    if value == FALLBACK_STOP and not chain:
        warnings.append("fallback_exhausted='stop' needs model_fallback entries - ignored")
        return FALLBACK_DEFAULT
    return value


def _parse_env_passthrough(raw, warnings: list[str]) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        warnings.append("env_passthrough must be a list of variable names - ignored")
        return ()
    names = []
    for item in raw:
        if isinstance(item, str) and _ENV_NAME.match(item):
            names.append(item)
        else:
            warnings.append(f"env_passthrough: {item!r} is not a variable name - skipped")
    return tuple(names)


def _parse_max_steps(raw_modes: dict, modes: tuple[str, ...],
                     warnings: list[str]) -> dict[str, int]:
    max_steps = {}
    for name in modes:
        entry = raw_modes.get(name)
        if entry is not None and not isinstance(entry, dict):  # e.g. "executor": 15
            warnings.append(f"modes.{name} must be an object like {{\"max_steps\": 15}}")
            entry = None
        max_steps[name] = _positive_int((entry or {}).get("max_steps"), DEFAULT_MAX_STEPS[name],
                                        f"modes.{name}.max_steps", warnings)
    return max_steps


def load_config(settings: dict) -> tuple[Config, list[str]]:
    """Reads this fork's keys from a parsed settings.json.

    Missing keys fall back to the defaults above; invalid values fall back
    too and produce a warning, so a typo never stops jcode from starting.
    Returns (config, warnings).
    """
    warnings: list[str] = []
    raw_modes = settings.get("modes") if isinstance(settings.get("modes"), dict) else {}
    write_paths = _parse_verifier(raw_modes, warnings)
    modes = (EXECUTOR, BUILDER) + ((VERIFIER,) if write_paths else ())
    mode = MODE_ALIASES.get(str(settings.get("default_mode", EXECUTOR)).lower())
    if mode not in modes:
        warnings.append(f"default_mode={settings.get('default_mode')!r} unknown or not "
                        f"configured - using {EXECUTOR}")
        mode = EXECUTOR
    chain = _parse_chain(settings.get("model_fallback"), settings.get("providers") or {}, warnings)
    config = Config(
        default_mode=mode,
        max_steps=_parse_max_steps(raw_modes, modes, warnings),
        compact_threshold=_positive_int(settings.get("compact_threshold_tokens"),
                                        DEFAULT_COMPACT_THRESHOLD_TOKENS,
                                        "compact_threshold_tokens", warnings),
        max_command_timeout=_positive_int(settings.get("max_command_timeout_s"),
                                          DEFAULT_MAX_COMMAND_TIMEOUT_S,
                                          "max_command_timeout_s", warnings),
        chain=chain,
        verifier_write_paths=write_paths,
        fallback_exhausted=_parse_fallback_exhausted(settings, chain, warnings),
        env_passthrough=_parse_env_passthrough(settings.get("env_passthrough"), warnings),
    )
    return config, warnings


def resolve_mode(name: str) -> str | None:
    """Maps a user-typed mode name (English, Spanish or German) to its canonical name."""
    return MODE_ALIASES.get(name.strip().lower())


# -------------------------------------------------------------- Write guard
def is_under_any(path: Path, sandbox: Path, folders: Iterable[str]) -> bool:
    """True if the (already resolved) path lies inside <sandbox>/<folder>/ for
    one of the folders. The folder itself is not a file, so it never matches."""
    return any((sandbox / folder).resolve() in path.parents for folder in folders)


def is_under_reports(path: Path, sandbox: Path) -> bool:
    """True if the (already resolved) path lies inside <sandbox>/reports/."""
    return is_under_any(path, sandbox, (REPORTS_DIR_NAME,))


# ---------------------------------------------------- run_command env (E4)
def is_secret_name(name: str) -> bool:
    """True for variable names that typically hold a key, token or password."""
    upper = name.upper()
    return any(part in upper for part in SECRET_NAME_PARTS)


def sanitized_env(environ: Mapping[str, str],
                  passthrough: Iterable[str] = ()) -> tuple[dict[str, str], list[str]]:
    """Copy of environ without secret-looking variables, except those named in
    passthrough (exact names). Returns (environment, sorted removed names)."""
    keep = set(passthrough)
    keep_git_config = _GIT_CONFIG_COUNT in keep
    env: dict[str, str] = {}
    removed = []
    for name, value in environ.items():
        if _GIT_CONFIG_GROUP.match(name):
            secret = not keep_git_config
        else:
            secret = name not in keep and is_secret_name(name)
        if secret:
            removed.append(name)
        else:
            env[name] = value
    return env, sorted(removed)


# ------------------------------------------------------------- Rate limits
_DAILY_MARKERS = ("perday", "per day", "per_day", "daily")
_RETRY_DELAY = re.compile(r"retry(?:_?delay|\s+in)\W{0,4}(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)


def classify_rate_limit(error: Exception) -> str | None:
    """'daily' or 'minute' for an HTTP 429, None for any other error.

    Gemini answers both limits with 429 RESOURCE_EXHAUSTED; the quota id in
    the message tells them apart (e.g. ...RequestsPerDay... vs
    ...RequestsPerMinute...). An unrecognised 429 counts as 'minute': the
    caller waits and retries, and only switches model if it persists. Other
    providers (DeepSeek, Qwen) rarely use these words, so for them this means
    "5 attempts, then switch" (Abschrift plan, E3).
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
def git_changes(sandbox: Path,
                allowed_dirs: Iterable[str] = (REPORTS_DIR_NAME,)) -> list[str] | None:
    """Paths git reports as changed or new in the sandbox, except those inside
    allowed_dirs (by default reports/, where executor mode writes).

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
    prefixes = tuple(folder.rstrip("/") + "/" for folder in allowed_dirs)
    changed = []
    for line in proc.stdout.splitlines():
        path = line[3:].split(" -> ")[-1].strip().strip('"')  # 'XY path' or 'XY old -> new'
        if path and not (prefixes and path.startswith(prefixes)):
            changed.append(path)
    return changed
