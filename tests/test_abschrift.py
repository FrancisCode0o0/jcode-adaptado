"""Tests for the Abschrift extensions of jcode (Abschrift plan step 0.1, E1-E4)
and for compatibility with mejorador-audio: without --project, without
modes.verifier and without fallback_exhausted everything works as before.

They run without network or API key, like test_adaptations.py. Run with:
python -m pytest -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO, FakeToolCall, rate_limit_error, reply

import jcode_extras as extras

ABSCHRIFT_SETTINGS = REPO / "examples" / "abschrift" / "settings.json"
EDIT_TOOLS = {"edit_file", "insert_lines", "delete_lines"}


def load_settings(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def tool_names(jc) -> set[str]:
    return {t["function"]["name"] for t in jc.mod._active_tools()}


def clean_env(**extra: str) -> dict:
    """Environment for a child jcode without any real API key."""
    env = extras.sanitized_env(os.environ)[0]
    env.update(extra)
    return env


def make_project(tmp_path: Path, settings: dict | None = None) -> Path:
    """A project folder like ~/jcode-projekte/abschrift with its settings.json."""
    project = tmp_path / "abschrift"
    project.mkdir()
    data = settings if settings is not None else load_settings(ABSCHRIFT_SETTINGS)
    (project / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    return project


def import_jcode(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Imports jcode in a child process with the given command line and
    prints where it looks for its files; main() is not run."""
    code = ("import sys; sys.argv = ['jcode.py'] + {args!r}; sys.path.insert(0, {repo!r}); "
            "import jcode as j; "
            "print(j.SETTINGS_FILE); print(j.SANDBOX); print(j.LOG_DIR); print(j.MODE); "
            "print(j.MAX_STEPS); print(j.CONFIG.chain[0].model if j.CONFIG.chain else '-'); "
            "print(j.CLI_TASK)").format(args=args, repo=str(REPO))
    return subprocess.run([sys.executable, "-c", code], cwd=cwd, capture_output=True,
                          text=True, env=clean_env(), timeout=60, check=False)


@pytest.fixture
def abschrift(jc, monkeypatch):
    """The jc fixture with the Abschrift settings (verifier configured, 'stop')."""
    config, warnings = extras.load_config(load_settings(ABSCHRIFT_SETTINGS))
    assert warnings == []
    monkeypatch.setattr(jc.mod, "CONFIG", config)
    return jc


# ======================================================== E1 · --project
def test_cli_without_options_is_the_task():
    # No --project: every word is the task, exactly as before.
    assert extras.parse_cli_args(["write", "fib.py"]) == (None, ["write", "fib.py"], None)
    assert extras.parse_cli_args([]) == (None, [], None)


def test_cli_project_option_both_forms(tmp_path, monkeypatch):
    # '--project <dir>' and '--project=<dir>' both work; '~' is expanded; the rest is the task.
    project = make_project(tmp_path)
    assert extras.parse_cli_args(["--project", str(project), "do", "it"]) == (project, ["do", "it"], None)
    assert extras.parse_cli_args([f"--project={project}"]) == (project, [], None)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert extras.parse_cli_args(["--project=~/abschrift"])[0] == project


@pytest.mark.parametrize("args, message", [
    (["--project"], "needs a folder"),
    (["--project", "/does/not/exist"], "does not exist"),
    (["--projects", "x"], "unknown option"),
])
def test_cli_project_errors(args, message):
    # Every wrong --project is an error - never a silent fallback to the folder of jcode.py.
    project, task, error = extras.parse_cli_args(args)
    assert project is None and task == [] and message in error


def test_cli_project_without_settings_or_twice_is_an_error(tmp_path):
    # A project folder without settings.json, or two --project options, is refused.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "no settings.json" in extras.parse_cli_args(["--project", str(empty)])[2]
    project = make_project(tmp_path)
    assert "twice" in extras.parse_cli_args(["--project", str(project), "--project", str(project)])[2]


def test_project_moves_settings_code_and_logs(tmp_path):
    # With --project, jcode reads settings.json from there and uses its code/ and logs/.
    project = make_project(tmp_path)
    out = import_jcode(["--project", str(project), "check", "this"], cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    settings, sandbox, logs, mode, steps, model, task = out.stdout.splitlines()
    assert Path(settings) == project / "settings.json"
    assert Path(sandbox) == project / "code" and Path(logs) == project / "logs"
    assert (mode, steps, model) == ("verifier", "30", "deepseek-v4.1-flash-free")
    assert task == "['check', 'this']"


def test_compat_without_project_everything_next_to_jcode(tmp_path):
    # COMPATIBILITY: without --project nothing changes for mejorador-audio,
    # whatever the current directory is.
    out = import_jcode(["a", "task"], cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    settings, sandbox, logs, mode, steps, model, task = out.stdout.splitlines()
    assert Path(settings) == REPO / "settings.json"
    assert Path(sandbox) == REPO / "code" and Path(logs) == REPO / "logs"
    assert (mode, steps, model) == ("executor", "15", "gemini-3.5-flash-lite")
    assert task == "['a', 'task']"


def test_wrong_project_stops_jcode_before_anything_else(tmp_path):
    # A typo in the path ends jcode with exit code 2 and a clear message.
    out = subprocess.run([sys.executable, str(REPO / "jcode.py"), "--project", str(tmp_path / "nope")],
                         capture_output=True, text=True, env=clean_env(), timeout=60, check=False)
    assert out.returncode == 2 and "does not exist" in out.stderr


# ===================================================== E2 · verifier mode
def test_compat_verifier_mode_does_not_exist_without_settings(jc):
    # COMPATIBILITY: mejorador-audio's settings.json has no modes.verifier, so the mode is refused.
    config, _ = extras.load_config(load_settings(REPO / "settings.json"))
    assert extras.VERIFIER not in config.modes and config.verifier_write_paths == ()
    before = [{"role": "system", "content": "x"}]
    handled, session = jc.mod._handle_command("/mode verifier", before)
    assert handled and session is before and jc.mod.MODE == extras.EXECUTOR


def test_verifier_mode_switch(abschrift):
    # /mode verifier: executor tools only, 30 steps, own prompt with exit answer and write paths.
    handled, session = abschrift.mod._handle_command("/mode verificador", [])
    assert handled and abschrift.mod.MODE == extras.VERIFIER and abschrift.mod.MAX_STEPS == 30
    assert tool_names(abschrift) == set(extras.EXECUTOR_TOOL_NAMES)
    prompt = session[0]["content"]
    assert '"KEINE FEHLER"' in prompt and "tests/acceptance/, reports/" in prompt


@pytest.mark.parametrize("path", ["tests/acceptance/upload.spec.js", "tests/acceptance/sub/a.js",
                                  "reports/review-1.md"])
def test_verifier_writes_inside_write_paths(abschrift, path):
    abschrift.mod._set_mode("verifier")
    assert not abschrift.mod.write_file(path, "x").startswith("ERROR")
    assert (abschrift.sandbox / path).read_text(encoding="utf-8") == "x"


@pytest.mark.parametrize("path", ["public/js/main.js", "tests/unit/a.test.js",
                                  "tests/acceptance/../../public/x.js", "tests/acceptanceX/a.js",
                                  "tests/acceptance", "AGENTS.md"])
def test_verifier_refuses_everything_else(abschrift, path):
    # Outside the write paths nothing is written, also not via '..' or a look-alike folder name.
    abschrift.mod._set_mode("verifier")
    assert abschrift.mod.write_file(path, "x").startswith("ERROR")
    target = (abschrift.sandbox / path).resolve()
    assert not target.is_file()


def test_verifier_refuses_edit_tool(abschrift):
    # A model that calls edit_file anyway gets an error; the file stays as it was.
    abschrift.mod._set_mode("verifier")
    target = abschrift.sandbox / "main.js"
    target.write_text("let a = 1;\n", encoding="utf-8")
    call = FakeToolCall("c1", "edit_file", {"path": "main.js", "old_string": "1", "new_string": "2"})
    abschrift.fake.script = [reply(tool_calls=[call]), reply("KEINE FEHLER")]
    session = abschrift.mod.run("review", abschrift.mod.new_session())
    assert target.read_text(encoding="utf-8") == "let a = 1;\n"
    assert "not available in verifier mode" in [m for m in session if m["role"] == "tool"][0]["content"]


def test_verifier_git_check(abschrift, capsys):
    # New acceptance tests are expected; a change in public/ is flagged in the verifier warning.
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    sandbox = abschrift.sandbox
    subprocess.run(git + ["init", "-q"], cwd=sandbox, check=True)
    (sandbox / "public").mkdir()
    (sandbox / "public" / "main.js").write_text("1", encoding="utf-8")
    subprocess.run(git + ["add", "."], cwd=sandbox, check=True)
    subprocess.run(git + ["commit", "-qm", "init"], cwd=sandbox, check=True)
    abschrift.mod._set_mode("verifier")
    abschrift.mod.write_file("tests/acceptance/a.spec.js", "test")
    assert extras.git_changes(sandbox, ("tests/acceptance", "reports")) == []
    (sandbox / "public" / "main.js").write_text("2", encoding="utf-8")
    abschrift.fake.script = [reply("done")]
    abschrift.mod.run("task", abschrift.mod.new_session())
    out = capsys.readouterr().out
    assert "public/main.js" in out and "Verifier mode may only change" in out
    assert "tests/acceptance/a.spec.js" not in out.split("changed outside")[-1]


@pytest.mark.parametrize("raw, expected", [
    ("tests/acceptance/", "tests/acceptance"), ("./reports", "reports"),
    ("tests\\acceptance", "tests/acceptance"), ("/etc", None), ("../x", None),
    ("a/../../b", None), ("", None), (".", None), ("C:/x", None), (5, None),
])
def test_normalize_write_path(raw, expected):
    assert extras.normalize_write_path(raw) == expected


def test_verifier_config_errors_disable_the_mode():
    # Bad write_paths never widen the mode: they are skipped, and without a valid one the mode is off.
    config, warnings = extras.load_config({"modes": {"verifier": {"write_paths": ["../x", "/"]}},
                                           "default_mode": "verifier", "providers": {}})
    assert config.verifier_write_paths == () and extras.VERIFIER not in config.modes
    assert config.default_mode == extras.EXECUTOR and len(warnings) == 4
    config, warnings = extras.load_config({"modes": {"verifier": {"max_steps": 5}}, "providers": {}})
    assert extras.VERIFIER not in config.modes and len(warnings) == 1


def test_shipped_abschrift_settings_are_valid():
    config, warnings = extras.load_config(load_settings(ABSCHRIFT_SETTINGS))
    assert warnings == [] and config.default_mode == extras.VERIFIER
    assert config.verifier_write_paths == ("tests/acceptance", "reports")
    assert config.fallback_exhausted == extras.FALLBACK_STOP and len(config.chain) == 2
    assert all(entry.reasoning_effort is None for entry in config.chain)
    assert not any("google" in entry.model or "gemini" in entry.model or "gemma" in entry.model
                   for entry in config.chain)


# ============================================ E3 · fallback_exhausted
def test_compat_start_without_chain_key_uses_default_provider(jc, monkeypatch):
    # COMPATIBILITY: without 'stop', a chain without usable key falls back to default_provider.
    calls = []
    monkeypatch.setattr(jc.mod, "_switch_backend", calls.append)
    monkeypatch.setattr(jc.mod, "SETTINGS", dict(jc.mod.SETTINGS, default_provider="pb"))
    monkeypatch.delenv("TEST_KEY_A")
    monkeypatch.delenv("TEST_KEY_B")
    assert jc.mod._start_backend() is True and calls == ["pb"]


def test_stop_never_leaves_the_chain_at_start(jc, monkeypatch):
    # With 'stop' and no usable chain model, jcode does not start and touches no other provider.
    calls = []
    monkeypatch.setattr(jc.mod, "_switch_backend", calls.append)
    monkeypatch.setattr(jc.mod, "SETTINGS", dict(jc.mod.SETTINGS, default_provider="pb"))
    monkeypatch.setattr(jc.mod, "CONFIG", extras.Config(chain=jc.chain, fallback_exhausted="stop"))
    monkeypatch.delenv("TEST_KEY_A")
    monkeypatch.delenv("TEST_KEY_B")
    assert jc.mod._start_backend() is False and calls == []


def test_stop_exits_jcode_without_key(tmp_path):
    # End to end: Abschrift project, no OPENCODE_API_KEY -> exit code 2, no task is run.
    project = make_project(tmp_path)
    out = subprocess.run([sys.executable, str(REPO / "jcode.py"), "--project", str(project), "hello"],
                         capture_output=True, text=True, env=clean_env(), timeout=60, check=False)
    assert out.returncode == 2 and "fallback_exhausted is 'stop'" in out.stderr
    assert not (project / "logs").exists()  # no session was started, so no log either


def test_stop_ends_the_task_when_the_chain_is_exhausted(jc, monkeypatch, capsys):
    # All models out of quota: the task stops, the message says no other model is used.
    monkeypatch.setattr(jc.mod, "CONFIG", extras.Config(chain=jc.chain, fallback_exhausted="stop"))
    jc.fake.script = [rate_limit_error("Error code: 429 - daily limit")] * 3
    session = jc.mod.run("keep me", jc.mod.new_session())
    assert len(jc.fake.calls) == 3 and any(m.get("content") == "keep me" for m in session)
    assert "no other model is used" in capsys.readouterr().out


def test_non_gemini_429_switches_after_five_attempts(jc):
    # A DeepSeek-style 429 has no Gemini quota words: 5 attempts on the same model, then the next one.
    attempts = len(jc.mod.RETRY_WAITS) + 1
    error = rate_limit_error("Error code: 429 - Rate limit reached for requests")
    jc.fake.script = [error] * attempts + [reply("done")]
    jc.mod.run("task", jc.mod.new_session())
    models = [c["model"] for c in jc.fake.calls]
    assert models == ["model-a"] * attempts + ["model-b"]


def test_fallback_exhausted_config_values():
    chain = [{"provider": "p", "model": "m"}]
    providers = {"p": {"base_url": "x", "api_key_env": "K"}}
    config, warnings = extras.load_config({"fallback_exhausted": "halt", "model_fallback": chain,
                                           "providers": providers})
    assert config.fallback_exhausted == extras.FALLBACK_DEFAULT and len(warnings) == 1
    config, warnings = extras.load_config({"fallback_exhausted": "stop", "providers": {}})
    assert config.fallback_exhausted == extras.FALLBACK_DEFAULT and len(warnings) == 1
    config, _ = extras.load_config({"providers": {}})
    assert config.fallback_exhausted == extras.FALLBACK_DEFAULT  # COMPATIBILITY: default as before


# ===================================== E4 · run_command without secrets
def test_sanitized_env_removes_secret_names():
    environ = {"PATH": "/bin", "HOME": "/h", "GEMINI_API_KEY": "g", "gemini_api_key": "g2",
               "GH_TOKEN": "t", "AWS_SECRET_ACCESS_KEY": "s", "DB_PASSWORD": "p",
               "GOOGLE_APPLICATION_CREDENTIALS": "/c.json", "KAGGLE_KEY": "k", "KAGGLE_USERNAME": "u"}
    env, removed = extras.sanitized_env(environ, ["KAGGLE_KEY"])
    assert env == {"PATH": "/bin", "HOME": "/h", "KAGGLE_KEY": "k", "KAGGLE_USERNAME": "u"}
    assert removed == sorted(set(environ) - set(env))


def test_run_command_sees_no_keys(jc, monkeypatch):
    # The shell of run_command does not see GEMINI_API_KEY; jcode itself still has it.
    monkeypatch.setenv("GEMINI_API_KEY", "value-must-not-leak")
    monkeypatch.setenv("OPENCODE_API_KEY", "value-must-not-leak")
    out = jc.mod.run_command("env | grep -c value-must-not-leak; echo g=$GEMINI_API_KEY")
    assert out.endswith("\n0\ng=")
    assert jc.mod.run_command("env | cut -d= -f1 | grep -ci -e key -e token -e secret").endswith("\n0")
    assert os.environ["GEMINI_API_KEY"] == "value-must-not-leak"


def test_env_passthrough_keeps_named_variables(jc, monkeypatch):
    # mejorador-audio: Kaggle variables listed in env_passthrough reach run_command.
    config, _ = extras.load_config(load_settings(REPO / "settings.json"))
    assert config.env_passthrough == ("KAGGLE_KEY", "KAGGLE_API_TOKEN")
    monkeypatch.setattr(jc.mod, "CONFIG", config)
    monkeypatch.setenv("KAGGLE_KEY", "kaggle-value")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-value")
    out = jc.mod.run_command("echo k=$KAGGLE_KEY g=$GEMINI_API_KEY")
    assert out.endswith("k=kaggle-value g=")


def test_env_passthrough_config_errors():
    config, warnings = extras.load_config({"env_passthrough": ["OK_NAME", "bad name", 3],
                                           "providers": {}})
    assert config.env_passthrough == ("OK_NAME",) and len(warnings) == 2
    config, warnings = extras.load_config({"env_passthrough": "KAGGLE_KEY", "providers": {}})
    assert config.env_passthrough == () and len(warnings) == 1


def test_splash_lists_hidden_names_never_values(jc, monkeypatch):
    # Isolated environment: the note shows the names (at most 6) and never a value.
    for name in list(os.environ):
        monkeypatch.delenv(name)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("GEMINI_API_KEY", "value-must-not-leak")
    note = jc.mod._hidden_secrets_note()
    assert note.startswith("1 variable(s) hidden from run_command (GEMINI_API_KEY)")
    assert "value-must-not-leak" not in note


def test_git_config_variables_travel_together():
    # GIT_CONFIG_KEY_0 looks secret; dropping it alone would break every git command.
    environ = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
               "GIT_CONFIG_VALUE_0": "Authorization: x", "PATH": "/bin"}
    assert extras.sanitized_env(environ)[0] == {"PATH": "/bin"}
    assert extras.sanitized_env(environ, ["GIT_CONFIG_COUNT"])[0] == environ
