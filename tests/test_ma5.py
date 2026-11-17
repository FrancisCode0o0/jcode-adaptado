"""Tests for jcode 0.10.0-ma.5: settings for the read-only review of
La Reliquia del Alba (allowed_modes, fallback_same_provider,
report_model_suffix). No network, no key.

Run: python -m pytest tests/test_ma5.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import DAILY, FakeToolCall, rate_limit_error, reply

import jcode_extras as extras

REPO = Path(__file__).resolve().parent.parent
RELIQUIA_SETTINGS = REPO / "examples/reliquia/settings.json"


# ------------------------------------------------------------ allowed_modes
def test_allowed_modes_limits_the_modes():
    # A review project offers executor only, so '/mode builder' cannot open editing.
    config, warnings = extras.load_config({"allowed_modes": ["executor"]})
    assert config.modes == ("executor",) and warnings == []


def test_without_allowed_modes_every_configured_mode_stays():
    # Existing projects (mejorador-audio, Abschrift) must not change.
    config, _ = extras.load_config({})
    assert config.modes == ("executor", "builder")


def test_allowed_modes_accepts_spanish_names():
    config, warnings = extras.load_config({"allowed_modes": ["ejecutor"]})
    assert config.modes == ("executor",) and warnings == []


def test_unknown_or_unconfigured_allowed_modes_fall_back_to_executor():
    # 'verifier' is not configured here: ignored; nothing left -> executor alone, with warnings.
    config, warnings = extras.load_config({"allowed_modes": ["verifier", "foo"]})
    assert config.modes == ("executor",)
    assert len(warnings) == 3


def test_allowed_modes_not_a_list_means_executor_only():
    config, warnings = extras.load_config({"allowed_modes": "executor"})
    assert config.modes == ("executor",) and warnings


def test_default_mode_outside_allowed_modes_is_replaced():
    config, warnings = extras.load_config({"allowed_modes": ["executor"], "default_mode": "builder"})
    assert config.default_mode == "executor" and warnings


def test_mode_command_refuses_a_mode_that_is_not_allowed(jc, monkeypatch, capsys):
    config, _ = extras.load_config({"allowed_modes": ["executor"]})
    monkeypatch.setattr(jc.mod, "CONFIG", config)
    assert jc.mod._set_mode("builder") is None
    assert jc.mod.MODE == "executor"
    assert "not allowed" in capsys.readouterr().out


# --------------------------------------------------- fallback_same_provider
def chain_two_providers():
    return (extras.ChainEntry("gemini", "flash"), extras.ChainEntry("gemini", "lite"),
            extras.ChainEntry("other", "qwen"))


def test_same_provider_fallback_stays_in_the_family():
    chain = extras.FallbackChain(chain_two_providers(), day_fn=lambda: "d", same_provider=True)
    assert chain.next_entry("gemini", "flash") == extras.ChainEntry("gemini", "lite")
    chain.mark_exhausted("gemini", "lite")
    assert chain.next_entry("gemini", "flash") is None  # never jumps to 'other'


def test_model_outside_the_chain_never_falls_back_to_it():
    # A hand-picked OpenRouter model must not continue on Gemini when its quota ends.
    chain = extras.FallbackChain(chain_two_providers()[:2], day_fn=lambda: "d", same_provider=True)
    assert chain.next_entry("openrouter", "qwen/qwen3-coder:free") is None


def test_default_fallback_still_crosses_providers():
    chain = extras.FallbackChain(chain_two_providers(), day_fn=lambda: "d")
    chain.mark_exhausted("gemini", "lite")
    assert chain.next_entry("gemini", "flash") == extras.ChainEntry("other", "qwen")


def test_flag_is_read_from_settings():
    config, warnings = extras.load_config({"fallback_same_provider": True, "report_model_suffix": True})
    assert config.fallback_same_provider and config.report_model_suffix and warnings == []
    config, warnings = extras.load_config({"fallback_same_provider": "yes"})
    assert config.fallback_same_provider is False and warnings


def test_task_stops_instead_of_switching_provider(jc, monkeypatch):
    # Chain in the fixture: pa/model-a, pa/model-b, pb/model-c. With same_provider, a daily
    # 429 on pb/model-c (hand-picked) must not move the task to pa: it stops.
    monkeypatch.setattr(jc.mod, "FALLBACK",
                        extras.FallbackChain(jc.chain, day_fn=lambda: jc.day["value"], same_provider=True))
    jc.mod._activate_entry(jc.chain[2])
    jc.fake.script = [rate_limit_error(DAILY)]
    try:
        jc.mod.run("look at the code", jc.mod.new_session())
    except Exception as error:  # noqa: BLE001 - the stop is the expected outcome
        assert "quota" in str(error).lower()
    assert (jc.mod.BACKEND, jc.mod.MODEL) == ("pb", "model-c")
    assert len(jc.fake.calls) == 1


# ------------------------------------------------------ report_model_suffix
def test_model_slug():
    assert extras.model_slug("qwen/qwen3-coder:free") == "qwen-qwen3-coder-free"
    assert extras.model_slug("gemini-3.8-flash") == "gemini-3.8-flash"
    assert extras.model_slug("///") == "model"


def test_suffix_only_for_new_reports(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "old.md").write_text("earlier task")
    before = extras.report_names(reports)
    (reports / "t1.md").write_text("new")
    (reports / "t2__gemini-3.8-flash.md").write_text("already named")
    renamed = extras.add_model_suffix(reports, before, "gemini-3.8-flash")
    assert renamed == [("t1.md", "t1__gemini-3.8-flash.md")]
    assert sorted(p.name for p in reports.iterdir()) == [
        "old.md", "t1__gemini-3.8-flash.md", "t2__gemini-3.8-flash.md"]


def test_suffix_never_overwrites(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "t1__m.md").write_text("first run")
    before = extras.report_names(reports)
    (reports / "t1.md").write_text("second run")
    extras.add_model_suffix(reports, before, "m")
    assert (reports / "t1__m.md").read_text() == "first run"
    assert (reports / "t1__m-2.md").read_text() == "second run"


def test_missing_reports_folder_is_not_an_error(tmp_path):
    assert extras.report_names(tmp_path / "reports") == set()
    assert extras.add_model_suffix(tmp_path / "reports", set(), "m") == []


def test_task_renames_its_report_with_the_model(jc, monkeypatch):
    config, _ = extras.load_config({"report_model_suffix": True})
    monkeypatch.setattr(jc.mod, "CONFIG", config)
    jc.fake.script = [
        reply(tool_calls=[FakeToolCall("c1", "write_file", {"path": "reports/t1.md", "content": "# T1\n"})]),
        reply("OK reports/t1.md"),
    ]
    jc.mod.run("review and write reports/t1.md", jc.mod.new_session())
    assert (jc.sandbox / "reports/t1__model-a.md").read_text() == "# T1\n"
    assert not (jc.sandbox / "reports/t1.md").exists()


def test_without_the_flag_reports_keep_their_name(jc):
    jc.fake.script = [
        reply(tool_calls=[FakeToolCall("c1", "write_file", {"path": "reports/t1.md", "content": "x"})]),
        reply("OK"),
    ]
    jc.mod.run("write reports/t1.md", jc.mod.new_session())
    assert (jc.sandbox / "reports/t1.md").exists()


# ------------------------------------------------------- the shipped template
def test_reliquia_template_loads_without_warnings():
    settings = json.loads(RELIQUIA_SETTINGS.read_text(encoding="utf-8"))
    config, warnings = extras.load_config(settings)
    assert warnings == []
    assert config.modes == ("executor",) and config.default_mode == "executor"
    assert config.fallback_same_provider and config.report_model_suffix
    assert config.fallback_exhausted == "stop"
    assert [e.model for e in config.chain] == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
    assert config.env_passthrough == ()


def test_reliquia_template_cell_is_required_and_code_read_only():
    import jcode_cell
    settings = json.loads(RELIQUIA_SETTINGS.read_text(encoding="utf-8"))
    warnings: list[str] = []
    cell = jcode_cell.parse_cell_config(settings["sandbox"], warnings)
    assert warnings == []
    assert cell.enabled and cell.required and not cell.network
    assert cell.read_only_in_workdir == ("juego",)
    assert "~/.bashrc" in cell.expect_absent


def test_settings_reach_the_running_fallback_chain(tmp_path):
    # jcode builds FALLBACK at import from the project's settings.json: the flag must get there.
    import subprocess
    import sys
    (tmp_path / "settings.json").write_text(RELIQUIA_SETTINGS.read_text(encoding="utf-8"))
    probe = ("import sys; sys.argv = ['jcode.py', '--project', sys.argv[1]]; import jcode; "
             "print(jcode.FALLBACK._same_provider, jcode.CONFIG.report_model_suffix, jcode.CONFIG.modes)")
    result = subprocess.run([sys.executable, "-c", probe, str(tmp_path)], cwd=REPO, capture_output=True,
                            text=True, timeout=60, check=False)
    assert result.stdout.strip().splitlines()[-1] == "True True ('executor',)", result.stdout + result.stderr


def test_start_screen_names_the_read_only_folder(jc, monkeypatch):
    import jcode_cell
    cell = jcode_cell.parse_cell_config({"enabled": True, "read_only_in_workdir": ["juego"],
                                         "network": False}, [])
    monkeypatch.setattr(jc.mod, "CELL", cell)
    note = jc.mod._cell_note()
    assert "except juego/" in note and "no network" in note
