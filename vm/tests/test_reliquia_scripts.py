"""Tests for the read-only review project of La Reliquia del Alba (jcode
0.10.0-ma.5; ma.6: modules from settings.gradle): vm/setup_reliquia.sh, session_check.sh with
"project_profile": "review", and the real cell. Fake home under tmp_path,
no network, no key, no VM.

Run: python -m pytest vm/tests/test_reliquia_scripts.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

VM_DIR = Path(__file__).resolve().parents[1]
JCODE_DIR = VM_DIR.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_abschrift_scripts import needs_bwrap
from test_vm_scripts import make_repo_with_origin

FAKE_GEMINI_KEY = "AQ." + "k" * 50


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def run(script: str, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(VM_DIR / script), *args], capture_output=True, text=True,
                          env=env, timeout=180, check=False, stdin=subprocess.DEVNULL)


@pytest.fixture
def game(tmp_path: Path) -> Path:
    """A small stand-in for the game's repository: code, build files and the
    parts that must NOT be copied (story, docs, art)."""
    repo = tmp_path / "ReliquiaDelAlba"
    write_files(repo, GAME_FILES)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    commit_all(repo, "one")
    return repo


# Layout of the branch 'motor' (2026-11-17), plus what must stay out.
GAME_FILES = {
    "settings.gradle": "// modules\ninclude 'motor', 'reliquia', 'lwjgl3', 'headless'\n",
    "build.gradle": "// root\n",
    "gradle.properties": "gdxVersion=1.14.2\n",
    "motor/build.gradle": "// motor\n",
    "motor/src/main/java/castillafantasx/Contenido.java": "package castillafantasx;\ninterface Contenido {}\n",
    "reliquia/build.gradle": "// reliquia\n",
    "reliquia/src/main/java/reliquia/Juego.java": "package reliquia;\nclass Juego {}\n",
    "lwjgl3/build.gradle": "// lwjgl3\n",
    "lwjgl3/src/main/java/reliquia/lwjgl3/Main.java": "class Main {}\n",
    "lwjgl3/portatil/leeme.txt": "portable folder\n",
    "headless/build.gradle": "// headless\n",
    "headless/src/main/java/reliquia/pruebas/PruebaHumo.java": "class PruebaHumo {}\n",
    "headless/referencias/foto.txt": "reference images\n",
    "res/ink/historia.ink": "Don Santiago...\n",
    "docs/Biblia.txt": "story\n",
    "arte/x.txt": "art\n",
}
JUEGO = "reliquia/src/main/java/reliquia/Juego.java"
MOTOR = "motor/src/main/java/castillafantasx/Contenido.java"


def write_files(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)


def commit_all(repo: Path, message: str) -> None:
    git("add", "-A", cwd=repo)
    git("-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", message, cwd=repo)


@pytest.fixture
def home_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home" / "fran"
    home.mkdir(parents=True)
    (home / ".bashrc").write_text("export GEMINI_API_KEY=not-a-real-key\n")
    keep = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "TZ")}
    return {**keep, "HOME": str(home), "GIT_TERMINAL_PROMPT": "0"}


def project_of(env: dict[str, str]) -> Path:
    return Path(env["HOME"]) / "jcode-projekte/reliquia"


@needs_bwrap
def test_setup_copies_only_the_code_read_only(home_env, game):
    result = run("setup_reliquia.sh", home_env, str(game))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RELIQUIA SETUP OK" in result.stdout
    project = project_of(home_env)
    juego = project / "code/juego"
    copied = sorted(str(p.relative_to(juego)) for p in juego.rglob("*") if p.is_file())
    assert copied == sorted(["settings.gradle", "build.gradle", "gradle.properties",
                             "motor/build.gradle", MOTOR, "reliquia/build.gradle", JUEGO,
                             "lwjgl3/build.gradle", "lwjgl3/src/main/java/reliquia/lwjgl3/Main.java",
                             "headless/build.gradle",
                             "headless/src/main/java/reliquia/pruebas/PruebaHumo.java"])
    # Nothing of res/, docs/, arte/ or the non-source module folders: the story stays home.
    assert "modules: motor reliquia lwjgl3 headless" in result.stdout
    assert not os.access(juego / "build.gradle", os.W_OK)
    assert not os.access(project / "code/AGENTS.md", os.W_OK)
    assert (project / "settings.json").read_bytes() == (JCODE_DIR / "examples/reliquia/settings.json").read_bytes()
    assert (project / "logs").is_dir() and (project / "code/reports").is_dir()
    assert git("rev-parse", "HEAD", cwd=game) in (project / "code/ORIGEN.txt").read_text()
    assert git("status", "--porcelain", cwd=project / "code") == ""


@needs_bwrap
def test_second_run_needs_refresh_and_keeps_settings(home_env, game):
    assert run("setup_reliquia.sh", home_env, str(game)).returncode == 0
    project = project_of(home_env)
    settings = project / "settings.json"
    settings.write_text(settings.read_text().replace('"max_steps": 20', '"max_steps": 25'))
    (project / "code/reports/t1__m.md").write_text("a report")
    again = run("setup_reliquia.sh", home_env, str(game))
    assert again.returncode == 1 and "--refresh" in again.stderr
    (game / "reliquia/src/main/java/reliquia/Nuevo.java").write_text("class Nuevo {}\n")
    commit_all(game, "two")
    refreshed = run("setup_reliquia.sh", home_env, "--refresh", str(game))
    assert refreshed.returncode == 0, refreshed.stdout + refreshed.stderr
    assert "was NOT overwritten" in refreshed.stdout and '"max_steps": 25' in settings.read_text()
    assert (project / "code/juego/reliquia/src/main/java/reliquia/Nuevo.java").is_file()
    assert (project / "code/reports/t1__m.md").read_text() == "a report"
    assert git("rev-list", "--count", "HEAD", cwd=project / "code") == "2"


@needs_bwrap
def test_setup_reads_a_bundle_and_a_ref(home_env, game, tmp_path):
    bundle = tmp_path / "reliquia.bundle"
    git("bundle", "create", str(bundle), "--all", cwd=game)
    result = run("setup_reliquia.sh", home_env, str(bundle))
    assert result.returncode == 0, result.stdout + result.stderr
    missing = run("setup_reliquia.sh", {**home_env, "RELIQUIA_PROJECT": str(tmp_path / "p2")},
                  str(game), "no-such-branch")
    assert missing.returncode == 1 and "not found" in missing.stderr


@needs_bwrap
def test_modules_follow_settings_gradle_not_a_fixed_list(home_env, game):
    # A module added or renamed in settings.gradle travels; an old 'core' folder left over does not.
    write_files(game, {"settings.gradle": "include(':motor', \"reliquia\")\n/* include 'lwjgl3' */\n"
                                          "include ':herramientas:mapas'\n",
                       "herramientas/mapas/src/main/java/M.java": "class M {}\n",
                       "core/src/main/java/Viejo.java": "class Viejo {}\n"})
    commit_all(game, "modules changed")
    result = run("setup_reliquia.sh", home_env, str(game))
    assert result.returncode == 0, result.stdout + result.stderr
    juego = project_of(home_env) / "code/juego"
    assert (juego / "herramientas/mapas/src/main/java/M.java").is_file()
    assert (juego / MOTOR).is_file() and (juego / JUEGO).is_file()
    assert not (juego / "core").exists() and not (juego / "lwjgl3").exists()


def test_a_module_without_src_stops_with_its_name(home_env, game):
    write_files(game, {"settings.gradle": "include 'motor', 'reliquia', 'vacio'\n",
                       "vacio/build.gradle": "// no sources yet\n"})
    commit_all(game, "empty module")
    result = run("setup_reliquia.sh", home_env, str(game))
    assert result.returncode == 1
    assert "module 'vacio' of settings.gradle has no src/ folder" in result.stderr
    assert not (project_of(home_env) / "code/juego").exists()


@needs_bwrap
def test_a_failed_refresh_keeps_the_old_snapshot(home_env, game):
    assert run("setup_reliquia.sh", home_env, str(game)).returncode == 0
    write_files(game, {"settings.gradle": "include 'motor', 'falta'\n"})
    commit_all(game, "broken")
    result = run("setup_reliquia.sh", home_env, "--refresh", str(game))
    assert result.returncode == 1 and "module 'falta'" in result.stderr
    assert (project_of(home_env) / "code/juego" / JUEGO).is_file()


@pytest.mark.parametrize("settings, message", [
    (None, "no settings.gradle"),
    ("rootProject.name = 'x'\n", "includes no module"),
    ("include 'motor'\nproject(':motor').projectDir = file('lib/motor')\n", "changes a projectDir"),
    ("include '../fuera'\n", "is not a plain folder"),
])
def test_unusable_settings_gradle_stops(home_env, game, settings, message):
    if settings is None:
        (game / "settings.gradle").unlink()
    else:
        write_files(game, {"settings.gradle": settings})
    commit_all(game, "settings")
    result = run("setup_reliquia.sh", home_env, str(game))
    assert result.returncode == 1 and message in result.stderr, result.stderr
    assert not (project_of(home_env) / "code/juego").exists()


def test_setup_refuses_a_missing_source(home_env, tmp_path):
    result = run("setup_reliquia.sh", home_env, str(tmp_path / "nothing"))
    assert result.returncode == 1 and "does not exist" in result.stderr


# ------------------------------------------------- the cell, from inside
def in_cell(project: Path, command: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(JCODE_DIR / "jcode_cell.py"), "run",
                           "--settings", str(project / "settings.json"),
                           "--workdir", str(project / "code"), "--", "bash", "-c", command],
                          capture_output=True, text=True, timeout=60, check=False,
                          env={"PATH": os.environ["PATH"], "HOME": str(project.parents[1])})


@needs_bwrap
def test_cell_reads_the_code_writes_only_reports(home_env, game):
    assert run("setup_reliquia.sh", home_env, str(game)).returncode == 0
    project = project_of(home_env)
    assert "class Juego" in in_cell(project, f"cat juego/{JUEGO}").stdout
    assert in_cell(project, "echo x > reports/ok.md").returncode == 0
    for attempt in ("echo x > juego/build.gradle", "chmod u+w juego/build.gradle",
                    "rm -rf juego", "mv juego other", "echo x > .git/config"):
        assert in_cell(project, attempt).returncode != 0, attempt
    assert (project / "code/juego/build.gradle").read_text() == "// root\n"
    assert in_cell(project, "test -e ~/.bashrc").returncode != 0
    assert in_cell(project, "test -e $HOME/jcode-projekte/reliquia/settings.json").returncode != 0


# ------------------------------------------- session_check, review profile
@pytest.fixture
def review_ready(home_env, game, tmp_path):
    assert run("setup_reliquia.sh", home_env, str(game)).returncode == 0
    fake_jcode = make_repo_with_origin(tmp_path, "jcode-fake",
                                       {"test_ok.py": "def test_ok():\n    assert True\n",
                                        ".gitignore": "code/\nlogs/\n"})
    env = {**home_env, "JCODE_DIR": str(fake_jcode), "GEMINI_API_KEY": FAKE_GEMINI_KEY}
    return env, project_of(home_env)


@needs_bwrap
def test_session_check_review_green(review_ready):
    env, project = review_ready
    result = run("session_check.sh", env, "--quick", str(project))
    assert result.returncode == 0, result.stdout
    for text in ("executor mode only", "cell switched on and required",
                 "read-only inside code/: juego", "fallback stays inside one provider",
                 "reports get the model in their name", "no commit hook needed", "GREEN"):
        assert text in result.stdout
    assert "Google model in the chain" not in result.stdout
    assert FAKE_GEMINI_KEY not in result.stdout + result.stderr


@needs_bwrap
@pytest.mark.parametrize("change, message", [
    (lambda s: s.update(allowed_modes=["executor", "builder"]), "allowed_modes must be"),
    (lambda s: s.update(fallback_same_provider=False), "fallback_same_provider must be true"),
    (lambda s: s["sandbox"].update(required=False), "must both be true"),
    (lambda s: s["sandbox"].update(read_only_in_workdir=[]), "read_only_in_workdir must name"),
    (lambda s: s.update(project_profile="other"), "project_profile 'other' unknown"),
])
def test_session_check_review_red(review_ready, change, message):
    env, project = review_ready
    settings_file = project / "settings.json"
    settings = json.loads(settings_file.read_text())
    change(settings)
    settings_file.write_text(json.dumps(settings))
    result = run("session_check.sh", env, "--quick", str(project))
    assert result.returncode == 1 and message in result.stdout, result.stdout


@needs_bwrap
def test_session_check_review_sees_changes_to_the_code(review_ready):
    env, project = review_ready
    (project / "code/reports/t1__m.md").write_text("report")
    waiting = run("session_check.sh", env, "--quick", str(project))
    assert waiting.returncode == 0 and "waiting for your review" in waiting.stdout
    target = project / "code/juego/build.gradle"
    target.chmod(0o644)
    target.write_text("// changed\n")
    changed = run("session_check.sh", env, "--quick", str(project))
    assert changed.returncode == 1 and "juego/build.gradle" in changed.stdout
