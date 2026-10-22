#!/usr/bin/env bash
# Install jcode 0.10.0-ma.3 in the VM (after block D) and set up Abschrift.
#
#     bash ~/jcode-adaptado/vm/install_ma3.sh
#
# Origin: mejorador-audio plan step 1.0.b1 (automation of 2026-10-20: "a
# VM script that ends in MA3 READY or stops at the first failure, naming
# the step"). Before it: Windows ran vm/windows/install_ma3.ps1 (commit +
# push of ma.3); take a VM snapshot first (vm/windows/snapshot_vm.ps1).
# Run with venv-audio active (it is, by ~/.bashrc). sudo asks once at most.
#
# Steps:
#   1. jcode-adaptado clean -> 'git pull --ff-only' -> must be 0.10.0-ma.3
#   2. bubblewrap (sudo apt-get, only if missing)
#   3. user namespaces for bwrap: tested for real. Only if Ubuntu's AppArmor
#      refuses them, the documented fix (Ubuntu's own bwrap-userns-restrict
#      profile) is loaded with sudo; anything else stops with a diagnosis.
#      The system-wide switch (sysctl) is never changed automatically.
#   4. SSH server (Abschrift step 1.4): checked, never installed or changed
#   5. vm/setup_abschrift.sh (meeting repo, clones, Gemini CLI, browsers, cells)
#   6. jcode's tests
#   7. session_check.sh for mejorador-audio -> GREEN
#   8. session_check.sh for Abschrift -> GREEN (only once its keys are set)
# Safe to run again. It never deletes, restores or forces anything.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JCODE_DIR="$(dirname "$SCRIPT_DIR")"
readonly SCRIPT_DIR JCODE_DIR
readonly EXPECTED_VERSION="0.10.0-ma.3"
readonly VENV_DIR="$HOME/venv-audio"
readonly ABSCHRIFT_PROJECT="$HOME/jcode-projekte/abschrift"
# Overridable only so the tests can use temporary files.
readonly APPARMOR_EXTRA="${APPARMOR_EXTRA:-/usr/share/apparmor/extra-profiles/bwrap-userns-restrict}"
readonly APPARMOR_TARGET="${APPARMOR_TARGET:-/etc/apparmor.d/bwrap-userns-restrict}"
readonly TOTAL_STEPS=8

current_step=0
step() { current_step=$1; echo; echo "==> [$1/$TOTAL_STEPS] $2"; }
die() { echo "ERROR: $*" >&2; exit 1; }
trap 'echo "STOPPED at step $current_step/$TOTAL_STEPS. Fix the message above and run again." >&2' ERR

check_preconditions() {
    [ "$(id -u)" -ne 0 ] || die "run as your normal user, without sudo"
    [ "${VIRTUAL_ENV:-}" = "$VENV_DIR" ] || die "activate venv-audio first: source $VENV_DIR/bin/activate"
}

update_jcode() {
    local changes version
    changes=$(git -C "$JCODE_DIR" status --porcelain)
    if [ -n "$changes" ]; then
        printf '%s\n' "$changes" | sed 's/^/    /' >&2
        die "jcode-adaptado has local changes (above); nothing pulled. Review them (restoring is your decision)."
    fi
    git -C "$JCODE_DIR" pull --ff-only --quiet \
        || die "'git pull --ff-only' failed (offline, or did Windows push ma.3?)"
    version=$(sed -n 's/^APP_VERSION = "\([^"]*\)".*/\1/p' "$JCODE_DIR/jcode.py")
    [ "$version" = "$EXPECTED_VERSION" ] \
        || die "jcode.py is $version, expected $EXPECTED_VERSION: run vm/windows/install_ma3.ps1 on Windows first"
    echo "jcode-adaptado at $(git -C "$JCODE_DIR" rev-parse --short HEAD), jcode $version"
}

install_bubblewrap() {
    if command -v bwrap >/dev/null; then
        echo "bubblewrap already installed: $(bwrap --version)"
        return
    fi
    sudo apt-get update
    sudo apt-get install -y bubblewrap
    echo "bubblewrap installed: $(bwrap --version)"
}

# The smallest real cell: own PID namespace, /proc, /dev. Prints bwrap's error.
bwrap_probe() {
    bwrap --ro-bind / / --dev /dev --proc /proc --unshare-pid --die-with-parent true 2>&1
}

enable_user_namespaces() {
    local message
    if message=$(bwrap_probe); then
        echo "bwrap can create its namespaces (no AppArmor change needed)."
        return
    fi
    echo "bwrap refused: $message"
    echo "AppArmor setting kernel.apparmor_restrict_unprivileged_userns:" \
         "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || echo unknown)"
    if [ -e "$APPARMOR_TARGET" ]; then
        die "the profile $APPARMOR_TARGET is already there and bwrap still fails. Nothing changed automatically; paste this output in the MejoraDeAudio chat."
    fi
    if [ ! -e "$APPARMOR_EXTRA" ]; then
        sudo apt-get install -y apparmor-profiles
    fi
    [ -e "$APPARMOR_EXTRA" ] || die "Ubuntu's bwrap profile ($APPARMOR_EXTRA) not found; nothing changed."
    echo "Loading Ubuntu's own profile for bwrap (documented fix, one time):"
    sudo cp "$APPARMOR_EXTRA" "$APPARMOR_TARGET"
    sudo apparmor_parser -r "$APPARMOR_TARGET"
    message=$(bwrap_probe) || die "bwrap still refused after loading the profile: $message"
    echo "bwrap works now."
}

check_ssh_server() {
    local address
    if systemctl is-active --quiet ssh 2>/dev/null || systemctl is-active --quiet ssh.socket 2>/dev/null; then
        address=$(hostname -I 2>/dev/null | awk '{print $1}')
        echo "SSH server active. From Windows (Abschrift 1.4): ssh $(id -un)@${address:-<VM address>}"
    else
        echo "WARNING: no SSH server active. Abschrift step 1.4 needs one; installing it is"
        echo "         your decision (guide, section 'SSH'): sudo apt-get install -y openssh-server"
    fi
}

run_jcode_tests() {
    (cd "$JCODE_DIR" && PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider)
}

session_check_ours() {
    bash "$SCRIPT_DIR/session_check.sh" --quick || die "session_check.sh (mejorador-audio) is not GREEN"
}

# Abschrift's keys are entered by hand (guide); until then this is pending.
session_check_abschrift() {
    if [ -z "${OPENCODE_API_KEY:-}" ] && [ -z "${NVIDIA_API_KEY:-}" ]; then
        echo "PENDING: OPENCODE_API_KEY and NVIDIA_API_KEY are not set yet (guide, section 'keys')."
        ABSCHRIFT_PENDING=1
        return
    fi
    bash "$SCRIPT_DIR/session_check.sh" --quick "$ABSCHRIFT_PROJECT" \
        || die "session_check.sh $ABSCHRIFT_PROJECT is not GREEN"
}

main() {
    ABSCHRIFT_PENDING=0
    check_preconditions
    step 1 "jcode-adaptado -> $EXPECTED_VERSION";     update_jcode
    step 2 "bubblewrap";                              install_bubblewrap
    step 3 "User namespaces for the cells (AppArmor)"; enable_user_namespaces
    step 4 "SSH server (check only)";                 check_ssh_server
    step 5 "Abschrift setup";                         bash "$SCRIPT_DIR/setup_abschrift.sh"
    step 6 "jcode tests";                             run_jcode_tests
    step 7 "Session check: mejorador-audio";          session_check_ours
    step 8 "Session check: Abschrift";                session_check_abschrift
    echo
    if [ "$ABSCHRIFT_PENDING" -eq 1 ]; then
        echo "MA3 READY (Abschrift: keys pending - then run step 8 again: bash vm/session_check.sh ~/jcode-projekte/abschrift)"
    else
        echo "MA3 READY"
    fi
    echo "Next: bash vm/verify_abschrift.sh (report for the Abschrift chat)."
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main
fi
