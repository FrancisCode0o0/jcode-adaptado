"""Tests for the mejorador-audio adaptations of jcode (plan step 1.0.b0).

They run without network or API key: a scripted FakeClient (conftest.py)
replaces the model. Run with:  python -m pytest -q
"""
from __future__ import annotations

import subprocess
import time

from conftest import DAILY, MINUTE, FakeToolCall, rate_limit_error, reply

import jcode_extras as extras

EDIT_TOOLS = {"edit_file", "insert_lines", "delete_lines"}
NET_TOOLS = {"web_search", "fetch_url"}


def tool_names(jc) -> set[str]:
    return {t["function"]["name"] for t in jc.mod._active_tools()}


# ------------------------------------------------------------ Executor mode
def test_executor_offers_no_edit_or_internet_tools(jc):
    # Checks the tool schemas sent to the model; edit and internet tools must be absent.
    names = tool_names(jc)
    assert not names & (EDIT_TOOLS | NET_TOOLS | {"take_screenshot"})
    assert {"run_command", "read_file", "write_file", "view_image", "view_pdf_page"} <= names


def test_executor_write_file_only_under_reports(jc):
    # write_file outside reports/ must fail AND leave no file behind; inside it works.
    result = jc.mod.write_file("src/app.js", "x")
    assert result.startswith("ERROR") and not (jc.sandbox / "src" / "app.js").exists()
    escape = jc.mod.write_file("reports/../app.js", "x")
    assert escape.startswith("ERROR") and not (jc.sandbox / "app.js").exists()
    ok = jc.mod.write_file("reports/run-1.md", "report")
    assert not ok.startswith("ERROR")
    assert (jc.sandbox / "reports" / "run-1.md").read_text(encoding="utf-8") == "report"


def test_executor_refuses_tool_it_was_not_offered(jc):
    # A model that calls edit_file anyway gets an error and the file stays untouched.
    target = jc.sandbox / "main.js"
    target.write_text("let a = 1;\n", encoding="utf-8")
    call = FakeToolCall("c1", "edit_file", {"path": "main.js", "old_string": "1", "new_string": "2"})
    jc.fake.script = [reply(tool_calls=[call]), reply("done")]
    session = jc.mod.run("fix it", jc.mod.new_session())
    assert target.read_text(encoding="utf-8") == "let a = 1;\n"
    tool_msgs = [m for m in session if m["role"] == "tool"]
    assert "not available in executor mode" in tool_msgs[0]["content"]


def test_builder_mode_still_edits(jc):
    # Builder mode keeps the original behaviour: write_file anywhere, edit tools offered.
    jc.mod._set_mode("builder")
    assert EDIT_TOOLS <= tool_names(jc)
    assert not jc.mod.write_file("src/app.js", "x").startswith("ERROR")


# ------------------------------------------------------------------- /mode
def test_mode_switch_changes_tools_prompt_and_steps(jc):
    # /mode replaces tools, system prompt and step limit, and starts a new session.
    handled, session = jc.mod._handle_command("/mode constructor", [])
    assert handled and jc.mod.MODE == extras.BUILDER and jc.mod.MAX_STEPS == 40
    assert session[0]["content"] == jc.mod.SYSTEM_PROMPT
    assert EDIT_TOOLS <= tool_names(jc)

    handled, session = jc.mod._handle_command("/mode ejecutor", session)
    assert handled and jc.mod.MODE == extras.EXECUTOR and jc.mod.MAX_STEPS == 15
    assert session[0]["content"] == extras.EXECUTOR_PROMPT
    assert not EDIT_TOOLS & tool_names(jc)


def test_unknown_mode_keeps_everything(jc):
    # A typo must not change the mode or drop the session.
    before = [{"role": "system", "content": "x"}]
    handled, session = jc.mod._handle_command("/mode builderr", before)
    assert handled and session is before and jc.mod.MODE == extras.EXECUTOR


def test_model_command_is_not_taken_for_mode(jc):
    # '/model x' starts with '/mode' - it must still set the model.
    jc.mod._handle_command("/model model-b", [])
    assert jc.mod.MODEL == "model-b" and jc.mod.MODE == extras.EXECUTOR


def test_step_limit_stops_the_task(jc):
    # MAX_STEPS bounds the number of requests per task.
    jc.mod.MAX_STEPS = 2
    calls = [reply(tool_calls=[FakeToolCall(f"c{i}", "list_files", {})]) for i in range(5)]
    jc.fake.script = calls
    jc.mod.run("loop", jc.mod.new_session())
    assert len(jc.fake.calls) == 2


# ---------------------------------------------------------- Model fallback
def test_daily_quota_switches_to_next_model(jc):
    # A daily-quota 429 switches immediately (no waiting) and the task carries on.
    jc.fake.script = [rate_limit_error(DAILY), reply("done")]
    jc.mod.run("task", jc.mod.new_session())
    assert [c["model"] for c in jc.fake.calls] == ["model-a", "model-b"]
    assert jc.mod.MODEL == "model-b" and jc.sleeps == []


def test_reasoning_effort_follows_the_active_model(jc):
    # model-a sends reasoning_effort=low; model-b (None) must not send the parameter.
    jc.fake.script = [rate_limit_error(DAILY), reply("done")]
    jc.mod.run("task", jc.mod.new_session())
    assert jc.fake.calls[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in jc.fake.calls[1]


def test_minute_quota_waits_and_keeps_model(jc):
    # A per-minute 429 waits the server's retryDelay (7 s) and retries the same model.
    jc.fake.script = [rate_limit_error(MINUTE), reply("done")]
    jc.mod.run("task", jc.mod.new_session())
    assert [c["model"] for c in jc.fake.calls] == ["model-a", "model-a"]
    assert jc.sleeps == [7.0]


def test_persistent_minute_quota_finally_switches(jc):
    # If a 'per-minute' 429 survives all retries it is treated as exhausted.
    attempts = len(jc.mod.RETRY_WAITS) + 1
    jc.fake.script = [rate_limit_error("Error code: 429 - too many")] * attempts + [reply("done")]
    jc.mod.run("task", jc.mod.new_session())
    assert jc.fake.calls[-1]["model"] == "model-b"
    assert len(jc.sleeps) == attempts - 1


def test_provider_switch_inside_chain(jc):
    # The chain may cross providers: after model-a and model-b, pb/model-c is used.
    jc.fake.script = [rate_limit_error(DAILY), rate_limit_error(DAILY), reply("done")]
    jc.mod.run("task", jc.mod.new_session())
    assert jc.mod.BACKEND == "pb" and jc.mod.MODEL == "model-c"


def test_all_models_exhausted_preserves_session(jc):
    # With the whole chain out of quota the task stops cleanly; the session survives.
    jc.fake.script = [rate_limit_error(DAILY)] * 3
    session = jc.mod.run("keep me", jc.mod.new_session())
    assert any(m.get("content") == "keep me" for m in session)
    assert len(jc.fake.calls) == 3


def test_other_errors_do_not_switch_model(jc):
    # A non-429 error (server down) retries the same model, as in the original.
    attempts = len(jc.mod.RETRY_WAITS) + 1
    jc.fake.script = [ConnectionError("down")] * attempts
    jc.mod.run("task", jc.mod.new_session())
    assert {c["model"] for c in jc.fake.calls} == {"model-a"}


def test_primary_model_returns_on_a_new_quota_day(jc):
    # After a fallback, the first model is used again once its quota day has turned over.
    jc.fake.script = [rate_limit_error(DAILY), reply("done")]
    session = jc.mod.run("task 1", jc.mod.new_session())
    assert jc.mod.MODEL == "model-b"
    jc.fake.script = [reply("still b")]
    jc.mod.run("task 2", session)
    assert jc.fake.calls[-1]["model"] == "model-b"  # same day: stays on b
    jc.day["value"] = "day-2"
    jc.fake.script = [reply("back on a")]
    jc.mod.run("task 3", session)
    assert jc.fake.calls[-1]["model"] == "model-a"


# --------------------------------------------------------------- Compaction
def big_history(jc, n: int = 30) -> list:
    session = jc.mod.new_session()
    session.insert(1, {"role": "system", "content": "AGENTS.md: never modify source"})
    for i in range(n):
        session.append({"role": "user", "content": f"task {i} " + "x" * 400})
        session.append({"role": "assistant", "content": f"answer {i} " + "y" * 400})
    return session


def test_compaction_triggers_above_threshold_and_keeps_agents_md(jc):
    # Above the threshold the first request is the summary; both system messages survive.
    jc.mod.COMPACT_THRESHOLD = 1_000
    session = big_history(jc)
    jc.fake.script = [reply("SUMMARY"), reply("done")]
    session = jc.mod.run("next", session)
    summary_request = jc.fake.calls[0]["messages"]
    assert "Summarize" in summary_request[0]["content"]
    assert [m["role"] for m in session[:2]] == ["system", "system"]
    assert session[1]["content"].startswith("AGENTS.md")
    assert "SUMMARY" in session[2]["content"]


def test_no_compaction_below_threshold(jc):
    # Below the threshold no summary request is sent.
    session = big_history(jc, n=2)
    jc.fake.script = [reply("done")]
    jc.mod.run("next", session)
    assert len(jc.fake.calls) == 1


def test_chain_entry_tightens_threshold(jc):
    # pb/model-c declares compact_threshold 500: the effective threshold drops to it.
    jc.mod._activate_entry(jc.chain[2])
    assert jc.mod._compact_threshold() == 500


# ------------------------------------------------------------- run_command
def test_run_command_timeout_capped_by_settings(jc):
    # A requested timeout above max_command_timeout_s is cut down to it.
    jc.mod.MAX_CMD_TIMEOUT = 1
    start = time.monotonic()
    result = jc.mod.run_command("sleep 5", timeout=9999)
    assert result.startswith("TIMEOUT after 1s") and time.monotonic() - start < 4


def test_run_command_description_states_real_default(jc):
    # The schema must state the real default (60 s), not the original's wrong '30'.
    schema = next(t for t in jc.mod.TOOLS if t["function"]["name"] == "run_command")
    desc = schema["function"]["parameters"]["properties"]["timeout"]["description"]
    assert f"default {jc.mod.CMD_TIMEOUT}" in desc and "default 30" not in desc


# --------------------------------------------------------------- Logs, git
def test_log_is_dated_and_outside_sandbox(jc):
    # Each session writes its own dated log in logs/, never inside ./code.
    jc.mod.new_session()
    log = jc.mod.LOG_FILE
    assert log.parent == jc.tmp / "logs" and log.name.startswith("jcode-")
    assert jc.sandbox not in log.parents


def test_git_check_flags_changes_outside_reports(jc, capsys):
    # After a task, files changed outside reports/ are listed; reports/ is ignored.
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(git + ["init", "-q"], cwd=jc.sandbox, check=True)
    (jc.sandbox / "a.js").write_text("1", encoding="utf-8")
    subprocess.run(git + ["add", "."], cwd=jc.sandbox, check=True)
    subprocess.run(git + ["commit", "-qm", "init"], cwd=jc.sandbox, check=True)
    (jc.sandbox / "a.js").write_text("2", encoding="utf-8")
    (jc.sandbox / "reports").mkdir()
    (jc.sandbox / "reports" / "r.md").write_text("ok", encoding="utf-8")
    assert extras.git_changes(jc.sandbox) == ["a.js"]
    jc.fake.script = [reply("done")]
    jc.mod.run("task", jc.mod.new_session())
    out = capsys.readouterr().out
    assert "a.js" in out and "git restore" in out


def test_git_check_without_repository(jc):
    # Not a git repository: the check reports None instead of failing.
    assert extras.git_changes(jc.sandbox) is None


# ------------------------------------------------------------ Configuration
def test_config_defaults_when_keys_missing():
    # An original settings.json (no new keys) must give the documented defaults.
    config, warnings = extras.load_config({"providers": {}})
    assert config.default_mode == extras.EXECUTOR
    assert config.max_steps == {"executor": 15, "builder": 40}
    assert config.compact_threshold == 60_000 and config.chain == () and warnings == []


def test_config_invalid_values_fall_back_with_warning():
    # Typos fall back to defaults with a warning instead of crashing at start-up.
    settings = {"default_mode": "fast", "modes": {"executor": {"max_steps": -3}},
                "compact_threshold_tokens": "big",
                "model_fallback": [{"provider": "nope", "model": "m"}, {"model": "m"}],
                "providers": {}}
    config, warnings = extras.load_config(settings)
    assert config.default_mode == extras.EXECUTOR and config.max_steps["executor"] == 15
    assert config.compact_threshold == 60_000 and config.chain == ()
    assert len(warnings) == 5


def test_shipped_settings_json_is_valid():
    # The settings.json in this repo must load without warnings.
    import json

    from conftest import REPO
    config, warnings = extras.load_config(json.loads((REPO / "settings.json").read_text()))
    assert warnings == [] and len(config.chain) == 3


def test_classify_rate_limit():
    # Daily vs per-minute vs not a rate limit at all.
    assert extras.classify_rate_limit(rate_limit_error(DAILY)) == "daily"
    assert extras.classify_rate_limit(rate_limit_error(MINUTE)) == "minute"
    assert extras.classify_rate_limit(ConnectionError("x")) is None
    assert extras.retry_delay_seconds(rate_limit_error(MINUTE)) == 7.0
    assert extras.retry_delay_seconds(rate_limit_error("retryDelay: 999s")) == extras.MAX_RETRY_DELAY_S


# ------------------------------------------------- Fixes from the 2nd review
def test_manual_model_choice_is_not_undone_by_fallback(jc):
    # After an automatic fallback, a manual /model must stick on the next task.
    jc.fake.script = [rate_limit_error(DAILY), reply("done")]
    session = jc.mod.run("task 1", jc.mod.new_session())
    jc.mod._handle_command("/model my-model", session)
    jc.day["value"] = "day-2"  # model-a would be available again
    jc.fake.script = [reply("ok")]
    jc.mod.run("task 2", session)
    assert jc.fake.calls[-1]["model"] == "my-model"


def test_start_skips_chain_entries_without_key(jc, monkeypatch):
    # If the first model's key is missing, start-up uses the next usable entry.
    monkeypatch.delenv("TEST_KEY_A", raising=False)
    jc.mod.ACTIVE_ENTRY = None
    jc.mod._start_backend()
    assert (jc.mod.BACKEND, jc.mod.MODEL) == ("pb", "model-c")


def test_two_sessions_in_one_second_keep_both_logs(jc):
    # A log is never overwritten, even when sessions start within the same second.
    first = (jc.mod.new_session(), jc.mod.LOG_FILE)[1]
    second = (jc.mod.new_session(), jc.mod.LOG_FILE)[1]
    assert first != second and first.exists() and second.exists()


def test_config_mode_entry_of_wrong_type_does_not_crash():
    # "executor": 15 instead of {"max_steps": 15} -> warning and default, no crash.
    config, warnings = extras.load_config({"modes": {"executor": 15}, "providers": {}})
    assert config.max_steps["executor"] == 15 and len(warnings) == 1
