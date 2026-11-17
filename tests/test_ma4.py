"""Tests for jcode 0.10.0-ma.4: the parts of upstream jcode.py (2026-10-22) taken
into the fork (mejorador-audio plan, ma.4 points 1-8) and the fork's own
ma.4 changes (no 'pip install' in the builder prompt).

Run: python -m pytest tests/test_ma4.py -q
"""
from __future__ import annotations

import os
import subprocess
import time
import uuid

import pytest
from conftest import FakeToolCall, reply
from test_cell import needs_bwrap  # noqa: F401  (fixture-free marker)

import jcode_extras as extras


def bad_request(message: str, status: int = 400) -> Exception:
    """An openai APIStatusError with the given 4xx status."""
    import openai
    try:
        import httpx2 as http
    except ImportError:
        import httpx as http
    response = http.Response(status, request=http.Request("POST", "https://example.invalid"))
    error_class = {400: openai.BadRequestError, 404: openai.NotFoundError}[status]
    return error_class(message, response=response, body=None)


def tool_messages(session: list) -> list[str]:
    return [m["content"] for m in session if m["role"] == "tool"]


# ------------------------------------------------------- 1. run_command
def test_timeout_kills_the_process_tree_and_keeps_partial_output(jc):
    # A child that ignores the shell's end must die too; output printed before the timeout is returned.
    # Killing only the shell would leave the child holding the output pipe for 300 s.
    tag = f"ma4-tree-{uuid.uuid4().hex}"
    started = time.monotonic()
    out = jc.mod.run_command(f"echo started; (exec -a {tag} sleep 300) & wait", timeout=2)
    assert time.monotonic() - started < 15
    assert out.startswith("TIMEOUT after 2s") and "process tree killed" in out
    assert out.endswith("Output until then:\nstarted")
    alive = subprocess.run(["pgrep", "-f", tag], capture_output=True, check=False)
    assert alive.returncode == 1


def test_pipe_reports_the_failing_command(jc):
    # pipefail: 'false | cat' must not look like a success (upstream: a failing test piped into head).
    assert jc.mod.run_command("false | cat").startswith("exit_code=1")


def test_commands_run_in_bash(jc):
    # [[ ]] does not exist in dash (/bin/sh on Ubuntu); models write it all the time.
    assert jc.mod.run_command("[[ 1 == 1 ]] && echo yes") == "exit_code=0\nyes"


@needs_bwrap
def test_pipefail_and_bash_also_in_the_cell(jc, monkeypatch, tmp_path):
    import test_cell
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(jc.mod, "CELL", test_cell.config_of())
    args, shell, _ = jc.mod._command_process("true")
    assert shell is False and args[0].endswith("bwrap")
    assert jc.mod.run_command("false | cat").startswith("exit_code=1")
    assert jc.mod.run_command("[[ 1 == 1 ]] && echo yes") == "exit_code=0\nyes"


def test_pip_redirect_quotes_the_interpreter(jc, monkeypatch):
    # An interpreter path with a space must stay one word (the command is shown, not run).
    monkeypatch.setattr(jc.mod.sys, "executable", "/opt/My Py/bin/python")
    seen = {}
    monkeypatch.setattr(jc.mod, "_command_process", lambda c: seen.setdefault("c", c) and ("true", True, {}))
    jc.mod.run_command("pip --version")
    assert seen["c"] == "'/opt/My Py/bin/python' -m pip --version"


# ---------------------------------------------------------- 3. read_file
def test_read_file_returns_only_the_requested_lines(jc):
    (jc.sandbox / "big.txt").write_text("".join(f"line {i}\n" for i in range(1, 101)))
    out = jc.mod.read_file("big.txt", numbered=True, start_line=40, end_line=42)
    assert out.splitlines() == ["  40│line 40", "  41│line 41", "  42│line 42",
                                "[lines 40-42 of 100]"]
    assert "invalid" in jc.mod.read_file("big.txt", start_line=90, end_line=80)


def test_read_file_without_range_is_verbatim(jc):
    # edit_file copies need the exact text, trailing newline included.
    (jc.sandbox / "a.txt").write_text("x\n")
    assert jc.mod.read_file("a.txt") == "x\n"


def test_read_file_names_binary_files(jc):
    (jc.sandbox / "a.bin").write_bytes(b"\xff\xfe\x00\x81")
    assert "binary file (4 bytes)" in jc.mod.read_file("a.bin")


# ------------------------------------------------- 4. list and search
def test_list_files_skips_generated_folders(jc):
    (jc.sandbox / "node_modules" / "pkg").mkdir(parents=True)
    (jc.sandbox / "node_modules" / "pkg" / "index.js").write_text("x")
    (jc.sandbox / "app.js").write_text("x")
    out = jc.mod.list_files()
    assert "app.js" in out and "node_modules  (2 entries, not listed)" in out
    assert "index.js" not in out


def test_list_files_is_capped(jc):
    for i in range(310):
        (jc.sandbox / f"f{i:03}.txt").write_text("x")
    lines = jc.mod.list_files().splitlines()
    assert len(lines) == 301 and "310 entries total" in lines[-1]


def test_search_skips_node_modules_unless_named(jc):
    (jc.sandbox / "node_modules").mkdir()
    (jc.sandbox / "node_modules" / "dep.js").write_text("NEEDLE\n")
    (jc.sandbox / "app.js").write_text("NEEDLE\n")
    assert jc.mod.search_files("NEEDLE") == "app.js:1: NEEDLE"
    assert jc.mod.search_files("NEEDLE", "node_modules/*.js") == "node_modules/dep.js:1: NEEDLE"


def test_search_refuses_absolute_glob(jc):
    assert jc.mod.search_files("root", "/etc/passwd").startswith("ERROR")


# ------------------------------------------------------- 8. _resolve
def test_absolute_path_outside_is_refused_with_the_real_directory(jc):
    with pytest.raises(ValueError, match="outside the project.*project directory is"):
        jc.mod._resolve("/etc/passwd")
    with pytest.raises(ValueError, match="outside the project"):
        jc.mod._resolve("~/.bashrc")


def test_garbled_absolute_prefix_is_rerooted(jc):
    # '/wrong/<parent>/code/a.txt' -> ./code/a.txt (seen in upstream's logs).
    garbled = f"/wrong/{jc.sandbox.parent.name}/code/a.txt"
    assert jc.mod._resolve(garbled) == jc.sandbox / "a.txt"


def test_symlink_out_of_the_sandbox_is_refused(jc, tmp_path):
    (tmp_path / "secret.txt").write_text("key")
    (jc.sandbox / "link.txt").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(ValueError, match="outside the project"):
        jc.mod._resolve("link.txt")
    assert jc.mod._resolve("code/sub/x.txt") == jc.sandbox / "sub" / "x.txt"


# --------------------------------------------- 7. empty writes and edits
def test_write_without_change_warns(jc):
    (jc.sandbox / "reports").mkdir()
    jc.mod.write_file("reports/r.md", "same")
    assert jc.mod.write_file("reports/r.md", "same").startswith("WARNING")


def test_edit_with_identical_strings_is_an_error(jc, monkeypatch):
    monkeypatch.setattr(jc.mod, "MODE", extras.BUILDER)
    (jc.sandbox / "a.js").write_text("let a = 1;\n")
    assert jc.mod.edit_file("a.js", "1", "1").startswith("ERROR: old_string and new_string are identical")


# --------------------------------------------------------- 2. loop guard
def test_identical_calls_get_a_reflection_step_then_stop(jc, capsys):
    (jc.sandbox / "a.txt").write_text("x\n")
    call = lambda n: FakeToolCall(f"c{n}", "read_file", {"path": "a.txt"})  # noqa: E731
    jc.fake.script = [reply(tool_calls=[call(n)]) for n in range(4)]
    jc.fake.script += [reply(tool_calls=[call(n)]) for n in range(4, 8)]
    session = jc.mod.run("look", jc.mod.new_session())
    users = [m["content"] for m in session if m["role"] == "user"]
    assert any(text.startswith("STOP. You called read_file") for text in users)
    assert jc.fake.script == [] and len(jc.fake.calls) == 8  # 4 calls, reflection, 4 more, stop
    assert "Task stopped: the same tool call was repeated again" in capsys.readouterr().out
    assert "identical call #2" in tool_messages(session)[1]


def test_same_error_with_different_arguments_warns_then_stops(jc, capsys):
    # Names differ in letters, not only digits, so only the masking of quoted text joins them.
    names = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]
    calls = [FakeToolCall(f"c{n}", "read_file", {"path": f"/nowhere/{name}.js"})
             for n, name in enumerate(names)]
    jc.fake.script = [reply(tool_calls=[c]) for c in calls]
    session = jc.mod.run("look", jc.mod.new_session())
    assert "failure #3 of read_file" in tool_messages(session)[2]
    assert any(m["role"] == "user" and m["content"].startswith("STOP. Your last 3 calls")
               for m in session)
    assert jc.fake.script == [] and len(jc.fake.calls) == 6
    assert "Task stopped: read_file failed 3x in a row" in capsys.readouterr().out


def test_different_successful_calls_are_not_a_loop(jc):
    for n in range(5):
        (jc.sandbox / f"{n}.txt").write_text(str(n))
    calls = [FakeToolCall(f"c{n}", "read_file", {"path": f"{n}.txt"}) for n in range(5)]
    jc.fake.script = [reply(tool_calls=[c]) for c in calls] + [reply("done")]
    session = jc.mod.run("look", jc.mod.new_session())
    assert session[-1]["content"] == "done"
    assert not any("WARNING" in text for text in tool_messages(session))


# ------------------------------------------------------ 5. 4xx and images
def test_bad_request_is_not_retried_and_keeps_the_session(jc):
    jc.fake.script = [bad_request("tool schema invalid")]
    session = jc.mod.run("go", jc.mod.new_session())
    assert len(jc.fake.calls) == 1 and jc.sleeps == []
    assert session[-1]["role"] == "user"  # task kept, nothing appended


def test_request_with_images_is_retried_without_them(jc):
    session = jc.mod.new_session()
    session.append({"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:,"}}]})
    jc.fake.script = [bad_request("image input not supported"), reply("ok")]
    session = jc.mod.run("describe", session)
    assert session[-1]["content"] == "ok" and len(jc.fake.calls) == 2
    assert not jc.mod._has_images(session)


def test_context_too_long_compacts_once(jc, monkeypatch):
    compactions = []
    monkeypatch.setattr(jc.mod, "_compact_messages", lambda m: compactions.append(1) or m)
    jc.fake.script = [bad_request("context length exceeded"), reply("ok")]
    session = jc.mod.run("go", jc.mod.new_session())
    assert compactions == [1] and session[-1]["content"] == "ok"


def test_not_found_model_is_rejected_at_once(jc):
    jc.fake.script = [bad_request("model not found", status=404)]
    jc.mod.run("go", jc.mod.new_session())
    assert len(jc.fake.calls) == 1


# ------------------------------------------------------------ 6. stats
def test_each_response_prints_a_stats_line(jc, capsys):
    jc.fake.script = [reply("done")]
    jc.mod.run("go", jc.mod.new_session())
    assert "tok/s" in capsys.readouterr().out


# -------------------------------------------------- fork changes in ma.4
def test_builder_prompt_and_tool_text_do_not_ask_to_install(jc):
    # Plan ma.4: upstream told the builder to 'pip install' on ModuleNotFoundError.
    assert "pip install" not in jc.mod.SYSTEM_PROMPT
    run_command_text = jc.mod._TOOL_META["run_command"]["description"]
    assert "pip install" not in run_command_text and "npm install" not in run_command_text


def test_version_is_ma6(jc):  # version bumped since ma.4, file name unchanged
    assert jc.mod.APP_VERSION == "0.10.0-ma.6"
    assert os.path.basename(jc.mod.__file__) == "jcode.py"
