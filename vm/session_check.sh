#!/usr/bin/env bash
# Start-of-session traffic light for jcode in the VM. Run before giving jcode
# any task:
#     bash ~/jcode-adaptado/vm/session_check.sh [--quick]
#         mejorador-audio: settings.json, code/ and logs/ next to jcode.py
#     bash ~/jcode-adaptado/vm/session_check.sh [--quick] ~/jcode-projekte/abschrift
#         another project (jcode --project <folder>), e.g. Abschrift
#
# Origin: mejorador-audio plan step 1.0 (automation "session check",
# 2026-10-20); merged in jcode 0.10.0-ma.3 (step 1.0.b1) with the version of
# the Abschrift chat (0.10.0-ma.2, Abschrift plan E6).
#
# Always (both projects):
#   python >= 3.10 + openai; settings.json valid for this fork (modes, model
#   chain, cell); API key variable of every chain model set (never shown);
#   jcode-adaptado clean -> 'git pull --ff-only'; sandbox (code/) clean and
#   without secrets; logs/ writable; commit hook; cell (if switched on, it is
#   built for real and inspected from inside); jcode's own tests.
# Without a project (mejorador-audio): venv-audio active, GEMINI_API_KEY
#   length, code/ -> 'git pull --ff-only', disk, node, ffmpeg, ntfy, datos/
#   read-only.
# With a project (Abschrift): model chain without Google, fallback_exhausted
#   "stop", cell switched on AND required, hook of the verifier clone. No
#   pull of code/: the start script does it (vm/abschrift/start_jcode.sh).
#
# It never restores or discards anything: deciding what to do with
# unexpected changes stays with Fran (plan decision). Keys are never printed.
# Exit code: 0 = GREEN (warnings allowed), 1 = RED, 2 = wrong usage.
# Paths can be overridden with environment variables (used by the tests);
# SESSION_CHECK_SKIP_TESTS=1 is the same as --quick.

set -u

EXTRAS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # this jcode-adaptado
JCODE_DIR="${JCODE_DIR:-$EXTRAS_DIR}"
VENV_DIR="${VENV_DIR:-$HOME/venv-audio}"
DATA_DIR="${DATA_DIR:-/mnt/datos}"
readonly EXPECTED_KEY_LENGTH=53   # "AQ." keys seen on 2026-10-20
readonly MIN_FREE_GB=10           # below this, sweeps and browser runs may fail
readonly GIT_TIMEOUT_S=60

quick=0
project=""
for argument in "$@"; do
    case "$argument" in
        --quick) quick=1 ;;
        -*) echo "usage: bash vm/session_check.sh [--quick] [project-folder]" >&2; exit 2 ;;
        *) if [ -n "$project" ]; then
               echo "usage: bash vm/session_check.sh [--quick] [project-folder]" >&2; exit 2
           fi
           project=$argument ;;
    esac
done
[ "${SESSION_CHECK_SKIP_TESTS:-0}" = "1" ] && quick=1

errors=0
warnings=0
if [ -t 1 ]; then
    GREEN=$'\e[32m' YELLOW=$'\e[33m' RED=$'\e[31m' RESET=$'\e[0m'
else
    GREEN="" YELLOW="" RED="" RESET=""
fi
ok()    { echo "${GREEN}OK   ${RESET} $*"; }
warn()  { echo "${YELLOW}WARN ${RESET} $*"; warnings=$((warnings + 1)); }
fail()  { echo "${RED}ERROR${RESET} $*"; errors=$((errors + 1)); }

# Lines "OK text" / "WARN text" / "FAIL text" from a helper -> counted output.
# Must read from a redirection ('relay "name" < <(helper)'), not a pipe: a
# pipe would run it in a subshell and lose the counts. A helper that prints
# nothing (crashed) counts as an error: never fail open.
relay() {
    local name=$1 line level text lines=0
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        lines=$((lines + 1))
        level=${line%% *}
        text=${line#* }
        case "$level" in
            OK) ok "$text" ;;
            WARN) warn "$text" ;;
            *) fail "$text" ;;
        esac
    done
    [ "$lines" -gt 0 ] || fail "$name: the check printed nothing (crashed?)"
}

if [ -n "$project" ]; then
    if [ ! -d "$project" ]; then
        echo "${RED}ERROR${RESET} project folder '$project' does not exist"
        echo "${RED}RED: 1 error(s). Do not start jcode yet.${RESET}"
        exit 1
    fi
    BASE_DIR="$(cd "$project" && pwd)"
else
    BASE_DIR="$JCODE_DIR"
fi
CODE_DIR="${CODE_DIR:-$BASE_DIR/code}"
SETTINGS_FILE="$BASE_DIR/settings.json"

# ------------------------------------------------------------------ checks
check_venv() {
    if [ "${VIRTUAL_ENV:-}" = "$VENV_DIR" ]; then
        ok "virtual environment active ($VENV_DIR)"
    else
        fail "virtual environment not active: run 'source $VENV_DIR/bin/activate'"
    fi
}

check_python() {
    if python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        ok "python $(python3 -c 'import platform; print(platform.python_version())')"
    else
        fail "python3 >= 3.10 missing"
    fi
    if python3 -c 'import openai' 2>/dev/null; then
        ok "package openai"
    else
        fail "package openai missing (it comes with venv-audio: is the venv active?)"
    fi
}

check_api_key_length() {
    local length=${#GEMINI_API_KEY}
    if [ "$length" -eq 0 ]; then
        fail "GEMINI_API_KEY is empty (see the plan: how to set the key)"
    elif [ "$length" -eq "$EXPECTED_KEY_LENGTH" ]; then
        ok "GEMINI_API_KEY has the expected length ($length characters)"
    else
        warn "GEMINI_API_KEY has $length characters, expected $EXPECTED_KEY_LENGTH"
    fi
}

# settings.json read with the fork's own parser; prints OK/WARN/FAIL lines.
check_settings() {
    relay "settings.json" < <(python3 -I - "$EXTRAS_DIR" "$SETTINGS_FILE" "${project:+project}" <<'PY' 2>&1
import json, os, sys
sys.path.insert(0, sys.argv[1])
from jcode_extras import FALLBACK_STOP, load_config
from jcode_cell import parse_cell_config
path, is_project = sys.argv[2], sys.argv[3] == "project"
try:
    with open(path, encoding="utf-8") as handle:
        settings = json.load(handle)
except (OSError, ValueError) as error:
    print(f"FAIL settings.json unreadable: {error}")
    sys.exit()
config, warnings = load_config(settings)
cell_warnings = []
cell = parse_cell_config(settings.get("sandbox"), cell_warnings)
for warning in warnings + cell_warnings:
    print(f"FAIL settings.json: {warning}")
print(f"OK settings.json: default mode {config.default_mode}, modes {'/'.join(config.modes)}, "
      f"fallback_exhausted {config.fallback_exhausted}, cell {'ON' if cell.enabled else 'off'}")
if not config.chain:
    print("WARN no model_fallback: jcode uses default_provider or a local server")
providers = settings.get("providers") or {}
for number, entry in enumerate(config.chain, 1):
    variable = providers.get(entry.provider, {}).get("api_key_env", "")
    present = bool(variable and os.environ.get(variable))
    level = "OK" if present else ("FAIL" if number == 1 else "WARN")
    print(f"{level} model {number}: {entry.provider}/{entry.model} - "
          f"${variable} {'set' if present else 'NOT set'}")
if is_project:
    google = [f"{e.provider}/{e.model}" for e in config.chain
              if any(word in e.model.lower() for word in ("gemini", "gemma"))
              or "googleapis" in providers.get(e.provider, {}).get("base_url", "")]
    print(f"FAIL Google model in the chain: {', '.join(google)} (the builder is Gemini)"
          if google else "OK model chain without Google")
    print("OK fallback_exhausted is 'stop'" if config.fallback_exhausted == FALLBACK_STOP else
          "FAIL fallback_exhausted must be 'stop' for this project")
    print("OK cell switched on and required" if cell.enabled and cell.required else
          "FAIL sandbox.enabled and sandbox.required must both be true for this project")
PY
)
}

# Changes inside the folders the agent may write are its output (waiting
# for review): a warning. Anything else: an error, shown, never undone.
check_sandbox_clean() {
    local changes others own
    if [ ! -d "$CODE_DIR" ]; then
        fail "sandbox $CODE_DIR missing (clone the project there)"
        return 1
    fi
    if ! git -C "$CODE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        fail "sandbox $CODE_DIR is not a git repository (the git check after each task needs it)"
        return 1
    fi
    changes=$(git -C "$CODE_DIR" status --porcelain --untracked-files=all)
    own=$(printf '%s\n' "$changes" | grep -E "^.. ($ALLOWED_PATTERN)" || true)
    others=$(printf '%s\n' "$changes" | grep -vE "^.. ($ALLOWED_PATTERN)" | grep -v '^$' || true)
    if [ -n "$others" ]; then
        fail "sandbox has changes nobody asked for. Nothing pulled. Paths:"
        printf '%s\n' "$others" | sed 's/^/        /'
        echo "        Review with: git -C $CODE_DIR diff   (restoring is your decision)"
        return 1
    fi
    if [ -n "$own" ]; then
        warn "sandbox: $(printf '%s\n' "$own" | wc -l) file(s) written by the agent, waiting for your review ($ALLOWED_TEXT)"
    fi
    return 0
}

# Pull a repo only if its work tree is clean; never discard anything.
# With "sandbox" as third argument the agent's own output folders are left
# out (check_sandbox_clean already reported them).
update_repo() {
    local name=$1 dir=$2 scope=${3:-} changes
    if ! git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        fail "$name: $dir is not a git repository"
        return
    fi
    changes=$(git -C "$dir" status --porcelain)
    if [ "$scope" = "sandbox" ]; then
        changes=$(printf '%s\n' "$changes" | grep -vE "^.. ($ALLOWED_PATTERN)" | grep -v '^$' || true)
    fi
    if [ -n "$changes" ]; then
        fail "$name has changes nobody asked for. No pull done. Paths:"
        printf '%s\n' "$changes" | sed 's/^/        /'
        echo "        Review with: git -C $dir diff   (restoring is your decision)"
        return
    fi
    if timeout "$GIT_TIMEOUT_S" git -C "$dir" pull --ff-only --quiet 2>/dev/null; then
        ok "$name clean and up to date ($(git -C "$dir" rev-parse --short HEAD 2>/dev/null || echo empty))"
    else
        warn "$name clean, but 'git pull' failed (offline, no commits yet or history diverged)"
    fi
}

check_sandbox_secrets() {
    if (cd "$CODE_DIR" && python3 -I "$EXTRAS_DIR/tools/check_secrets.py" --all >/dev/null 2>&1); then
        ok "no secrets in the sandbox's tracked files"
    else
        fail "secret found in the sandbox (or git error): cd $CODE_DIR && python3 $EXTRAS_DIR/tools/check_secrets.py --all"
    fi
}

check_logs() {
    if mkdir -p "$BASE_DIR/logs" 2>/dev/null && [ -w "$BASE_DIR/logs" ]; then
        ok "logs/ writable ($BASE_DIR/logs)"
    else
        fail "logs/ not writable ($BASE_DIR/logs)"
    fi
}

# Abschrift: Fran commits the acceptance tests in the verifier clone, so its
# hook must be the trusted one in jcode-adaptado (the agent cannot see or
# change it). mejorador-audio: nobody commits in the VM (no credentials).
check_hook() {
    local hooks
    hooks=$(git -C "$CODE_DIR" config --get core.hooksPath 2>/dev/null || true)
    if [ -n "$project" ]; then
        if [ "$hooks" = "$EXTRAS_DIR/vm/agent-hooks" ] && [ -x "$hooks/pre-commit" ]; then
            ok "commit hook of the clone: $hooks (secret check)"
        else
            fail "commit hook of the clone is '${hooks:-none}', expected an executable $EXTRAS_DIR/vm/agent-hooks/pre-commit (bash vm/setup_abschrift.sh sets it)"
        fi
    elif [ -n "$hooks" ]; then
        ok "commit hook of code/: $hooks"
    else
        ok "no commit hook in code/ (nobody commits in the VM: no credentials)"
    fi
}

check_cell() {
    local enabled
    enabled=$(python3 -I -c 'import json, sys
s = json.load(open(sys.argv[1], encoding="utf-8")).get("sandbox") or {}
print("yes" if s.get("enabled") is True else "no")' "$SETTINGS_FILE" 2>/dev/null || echo no)
    if [ "$enabled" != "yes" ]; then
        if [ -z "$project" ]; then
            ok "cell off (switched on and tested in plan step 1.0.e)"
        fi
        return  # for a project, check_settings already reported it
    fi
    relay "cell" < <(python3 -I "$EXTRAS_DIR/jcode_cell.py" check --settings "$SETTINGS_FILE" \
        --workdir "$CODE_DIR" 2>&1 | sed -e 's/^OK /OK cell: /' -e 's/^FAIL /FAIL cell: /' \
        -e 's/^WARN /WARN cell: /')
}

run_jcode_tests() {
    local output status
    if [ "$quick" -eq 1 ]; then
        warn "jcode tests skipped (--quick)"
        return
    fi
    # No __pycache__ (it would make the repo look dirty in the next check,
    # unless .gitignore covers it) and no .pytest_cache (just tidiness).
    output=$(cd "$JCODE_DIR" && PYTHONDONTWRITEBYTECODE=1 \
        python3 -m pytest -q -p no:cacheprovider 2>&1)
    status=$?
    if [ "$status" -eq 0 ]; then
        ok "jcode tests: $(printf '%s\n' "$output" | tail -n 1)"
    else
        fail "jcode tests: $(printf '%s\n' "$output" | tail -n 1)"
    fi
}

check_disk() {
    local free_kb free_gb
    free_kb=$(df -Pk "$HOME" | awk 'NR == 2 {print $4}')
    free_gb=$((free_kb / 1024 / 1024))
    if [ "$free_gb" -ge "$MIN_FREE_GB" ]; then
        ok "free disk space: $free_gb GB"
    else
        warn "free disk space: only $free_gb GB (minimum $MIN_FREE_GB GB)"
    fi
}

check_tool() {
    local tool=$1 version
    if version=$("$tool" "${@:2}" 2>/dev/null | head -n 1); then
        ok "$tool: $version"
    else
        warn "$tool not found (installed by setup_vm.sh, block D)"
    fi
}

check_ntfy() {
    if [ -n "${NTFY_TOPIC:-}" ]; then
        ok "phone notifications configured"
    else
        warn "NTFY_TOPIC not set (run vm/set_ntfy_topic.sh once)"
    fi
}

# The share must be read-only. The only reliable test is to try a write.
check_data_mount() {
    local probe
    if [ ! -d "$DATA_DIR" ]; then
        warn "datos/ not mounted at $DATA_DIR (see vm/mount_data.sh)"
        return
    fi
    probe="$DATA_DIR/.session_check_write_probe_$$_$RANDOM"
    if ( : > "$probe" ) 2>/dev/null; then
        rm -f "$probe"
        fail "datos/ is WRITABLE at $DATA_DIR. Stop: jcode could delete datasets."
    else
        ok "datos/ mounted read-only ($(find "$DATA_DIR" -mindepth 1 -maxdepth 1 | wc -l) entries)"
    fi
}

# Folders the agent may write (from settings.json), as a grep pattern.
allowed_folders() {
    python3 -I - "$EXTRAS_DIR" "$SETTINGS_FILE" <<'PY' 2>/dev/null || echo "reports"
import json, sys
sys.path.insert(0, sys.argv[1])
from jcode_extras import load_config
config, _ = load_config(json.load(open(sys.argv[2], encoding="utf-8")))
folders = set(config.write_dirs("executor") or ()) | set(config.verifier_write_paths)
print(" ".join(sorted(folders)))
PY
}

# ------------------------------------------------------------------- main
echo "Session check $(date '+%Y-%m-%d %H:%M')  jcode: $JCODE_DIR  project: $BASE_DIR"
read -r -a ALLOWED <<< "$(allowed_folders)"
ALLOWED_TEXT=$(printf '%s/ ' "${ALLOWED[@]}")
ALLOWED_PATTERN=$(printf '%s/|' "${ALLOWED[@]}" | sed 's/[.]/[.]/g; s/|$//')

if [ -z "$project" ]; then
    check_venv
fi
check_python
check_settings
if [ -z "$project" ]; then
    check_api_key_length
fi
update_repo "jcode-adaptado" "$JCODE_DIR"
if check_sandbox_clean; then
    if [ -z "$project" ]; then
        update_repo "sandbox (code/)" "$CODE_DIR" sandbox
    else
        ok "sandbox clean ($CODE_DIR; the start script pulls it)"
    fi
    check_sandbox_secrets
fi
check_logs
check_hook
check_cell
run_jcode_tests
if [ -z "$project" ]; then
    check_disk
    check_tool node --version
    check_tool ffmpeg -version
    check_ntfy
    check_data_mount
fi

echo
if [ "$errors" -gt 0 ]; then
    echo "${RED}RED: $errors error(s), $warnings warning(s). Do not start jcode yet.${RESET}"
    exit 1
fi
echo "${GREEN}GREEN: 0 errors, $warnings warning(s). jcode can start.${RESET}"
