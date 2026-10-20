#!/usr/bin/env bash
# Start-of-session traffic light for the jcode VM. Run before giving jcode
# any task:   bash ~/jcode-adaptado/vm/session_check.sh   [--quick]
#
# Origin: plan step 1.0 (automation "session check", 2026-10-20).
# What it does, in order:
#   1. venv-audio active and GEMINI_API_KEY present (length only, never shown)
#   2. both repos clean -> 'git pull --ff-only'; NOT clean -> stop and show
#      the changed paths. It never restores or discards anything: deciding
#      what to do with unexpected changes stays with Fran (plan decision).
#   3. jcode's own tests (skipped with --quick)
#   4. free disk space, node, ffmpeg, ntfy topic
#   5. datos/ mounted AND read-only (tries to create a file: it must fail)
# Exit code: 0 = no ERROR (warnings allowed), 1 = at least one ERROR.
#
# Paths can be overridden with environment variables (used by the tests).

set -u

JCODE_DIR="${JCODE_DIR:-$HOME/jcode-adaptado}"
CODE_DIR="${CODE_DIR:-$JCODE_DIR/code}"
VENV_DIR="${VENV_DIR:-$HOME/venv-audio}"
DATA_DIR="${DATA_DIR:-/mnt/datos}"
readonly EXPECTED_KEY_LENGTH=53   # "AQ." keys seen on 2026-10-20
readonly MIN_FREE_GB=10           # below this, sweeps and browser runs may fail
readonly GIT_TIMEOUT_S=60

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

check_venv() {
    if [ "${VIRTUAL_ENV:-}" = "$VENV_DIR" ]; then
        ok "virtual environment active ($VENV_DIR)"
    else
        fail "virtual environment not active: run 'source $VENV_DIR/bin/activate'"
    fi
}

check_api_key() {
    local length=${#GEMINI_API_KEY}
    if [ "$length" -eq 0 ]; then
        fail "GEMINI_API_KEY is empty (see the plan: how to set the key)"
    elif [ "$length" -eq "$EXPECTED_KEY_LENGTH" ]; then
        ok "GEMINI_API_KEY present ($length characters)"
    else
        warn "GEMINI_API_KEY has $length characters, expected $EXPECTED_KEY_LENGTH"
    fi
}

# Pull a repo only if its work tree is clean; never discard anything.
update_repo() {
    local name=$1 dir=$2 changes
    if ! git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        fail "$name: $dir is not a git repository"
        return
    fi
    changes=$(git -C "$dir" status --porcelain)
    if [ -n "$changes" ]; then
        fail "$name has changes nobody asked for. No pull done. Paths:"
        printf '%s\n' "$changes" | sed 's/^/        /'
        echo "        Review with: git -C $dir diff   (restoring is your decision)"
        return
    fi
    if timeout "$GIT_TIMEOUT_S" git -C "$dir" pull --ff-only --quiet 2>/dev/null; then
        ok "$name clean and up to date ($(git -C "$dir" rev-parse --short HEAD))"
    else
        warn "$name clean, but 'git pull' failed (offline or history diverged)"
    fi
}

run_jcode_tests() {
    local output status
    # No __pycache__ (it would make the repo look dirty in the next check,
    # unless .gitignore covers it) and no .pytest_cache (just tidiness).
    output=$(cd "$JCODE_DIR" && PYTHONDONTWRITEBYTECODE=1 \
        python -m pytest -q -p no:cacheprovider 2>&1)
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

echo "Session check $(date '+%Y-%m-%d %H:%M')"
check_venv
check_api_key
update_repo "jcode-adaptado" "$JCODE_DIR"
update_repo "mejorador-audio (code/)" "$CODE_DIR"
if [ "${1:-}" = "--quick" ]; then
    warn "jcode tests skipped (--quick)"
else
    run_jcode_tests
fi
check_disk
check_tool node --version
check_tool ffmpeg -version
check_ntfy
check_data_mount

echo
if [ "$errors" -gt 0 ]; then
    echo "${RED}RED: $errors error(s), $warnings warning(s). Do not start jcode yet.${RESET}"
    exit 1
fi
echo "${GREEN}GREEN: 0 errors, $warnings warning(s). jcode can start.${RESET}"
