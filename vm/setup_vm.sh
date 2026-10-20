#!/usr/bin/env bash
# Plan step 1.0.b, block D: install everything the jcode VM still needs.
#
# Origin: automation "setup script", 2026-10-20. Replaces ~15 commands pasted
# by hand (pasting caused most incidents in blocks A-C).
#
# Run from a terminal with venv-audio active (it is, by ~/.bashrc):
#     bash ~/jcode-adaptado/vm/setup_vm.sh
# Take a VM snapshot BEFORE running it (vm/windows/snapshot_vm.ps1).
#
# Safe to run again: every step checks whether its work is already done.
# Stops at the first error and says which step failed.
#
# Steps:
#   1. system packages (sudo):  ffmpeg, curl, xz-utils
#   2. Node.js LTS in ~/.local/opt (no sudo), checked against nodejs.org's
#      SHA-256 list; added to PATH in ~/.bashrc once
#   3. Python packages: pinned + hashed (vm/requirements-ref.txt)
#   4. Playwright: system libraries (may warn on new Ubuntu) + 3 browsers
#   5. jcode's own tests (the package update must not break jcode)
#   6. vm/check_vm.py: every tool does a small real job

set -Eeuo pipefail   # -E: the ERR trap also fires inside functions

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JCODE_DIR="$(dirname "$SCRIPT_DIR")"
readonly SCRIPT_DIR JCODE_DIR
readonly VENV_DIR="$HOME/venv-audio"
readonly NODE_PARENT_DIR="$HOME/.local/opt"
readonly NODE_LINK="$NODE_PARENT_DIR/node"
# Single quotes: $HOME and $PATH must stay unexpanded inside ~/.bashrc.
# shellcheck disable=SC2016
readonly NODE_PATH_LINE='export PATH="$HOME/.local/opt/node/bin:$PATH"'
# Overridable only so the tests can serve a fake release locally.
readonly NODE_DIST_URL="${NODE_DIST_URL:-https://nodejs.org/dist}"
readonly REQUIRED_PYTHON="3.14"
readonly TOTAL_STEPS=6

current_step=0
step() { current_step=$1; echo; echo "==> [$1/$TOTAL_STEPS] $2"; }
die() { echo "ERROR: $*" >&2; exit 1; }
trap 'echo "STOPPED at step $current_step/$TOTAL_STEPS. Fix the message above and run again." >&2' ERR

check_preconditions() {
    [ "$(id -u)" -ne 0 ] || die "run as your normal user, without sudo"
    [ "${VIRTUAL_ENV:-}" = "$VENV_DIR" ] || die "activate venv-audio first: source $VENV_DIR/bin/activate"
    local python_version
    python_version=$(python -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    [ "$python_version" = "$REQUIRED_PYTHON" ] \
        || die "Python $python_version; requirements-ref.txt is pinned for $REQUIRED_PYTHON"
    echo "Python $python_version in $VIRTUAL_ENV. sudo will ask for your password once."
}

install_system_packages() {
    sudo apt-get update
    sudo apt-get install -y ffmpeg curl xz-utils ca-certificates
}

node_platform() {
    case "$(uname -m)" in
        x86_64) echo "linux-x64" ;;
        aarch64) echo "linux-arm64" ;;
        *) die "unsupported CPU $(uname -m)" ;;
    esac
}

newest_node_lts_version() {
    # index.json lists releases newest first; "lts" is false or a codename.
    python - "$NODE_DIST_URL/index.json" <<'EOF'
import json, sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=30) as response:
    releases = json.load(response)
print(next(release["version"] for release in releases if release["lts"]))
EOF
}

# Download one Node release and verify it before unpacking anything.
download_and_verify_node() {
    local version=$1 archive=$2 folder=$3
    curl -fsSL -o "$folder/$archive" "$NODE_DIST_URL/$version/$archive"
    curl -fsSL -o "$folder/SHASUMS256.txt" "$NODE_DIST_URL/$version/SHASUMS256.txt"
    (cd "$folder" && grep " $archive\$" SHASUMS256.txt | sha256sum --check --status) \
        || die "Node archive does not match its SHA-256 sum; nothing installed"
    echo "SHA-256 verified."
}

install_node() {
    if [ -x "$NODE_LINK/bin/node" ]; then
        # Already installed: keep it. A silent Node upgrade would change the
        # test environment without anyone deciding it.
        echo "Node already installed: $("$NODE_LINK/bin/node" --version). Skipped."
    else
        local version archive folder
        version=$(newest_node_lts_version)
        archive="node-$version-$(node_platform).tar.xz"
        echo "Newest LTS: $version"
        folder=$(mktemp -d)
        download_and_verify_node "$version" "$archive" "$folder"
        mkdir -p "$NODE_PARENT_DIR"
        tar -xJf "$folder/$archive" -C "$NODE_PARENT_DIR"
        ln -sfn "$NODE_PARENT_DIR/${archive%.tar.xz}" "$NODE_LINK"
        rm -rf "$folder"
    fi
    grep -qxF "$NODE_PATH_LINE" "$HOME/.bashrc" || printf '%s\n' "$NODE_PATH_LINE" >> "$HOME/.bashrc"
    export PATH="$NODE_LINK/bin:$PATH"
    echo "node $(node --version), npm $(npm --version)"
}

install_python_packages() {
    python -m pip install --require-hashes -r "$SCRIPT_DIR/requirements-ref.txt"
}

install_browsers() {
    # install-deps calls sudo apt-get itself. Ubuntu 26.04 may be newer than
    # Playwright's list of supported systems: warn and go on; step 6 opens
    # each browser for real, so a missing library is caught there.
    if ! python -m playwright install-deps chromium firefox webkit; then
        echo "WARNING: install-deps failed; step 6 will show which browser is affected."
    fi
    python -m playwright install chromium firefox webkit
}

run_jcode_tests() {
    (cd "$JCODE_DIR" && PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider)
}

main() {
    check_preconditions
    step 1 "System packages (ffmpeg)";         install_system_packages
    step 2 "Node.js LTS";                      install_node
    step 3 "Python packages (pinned, hashed)"; install_python_packages
    step 4 "Playwright browsers";              install_browsers
    step 5 "jcode tests";                      run_jcode_tests
    step 6 "Final check";                      python "$SCRIPT_DIR/check_vm.py"
    echo
    echo "Block D done. Next: open a NEW terminal (PATH changed), shut the VM down"
    echo "and take a snapshot with vm/windows/snapshot_vm.ps1."
}

# Run only when executed; the tests 'source' this file to call one function.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main
fi
