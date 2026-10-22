# shellcheck shell=bash
# shellcheck disable=SC2034  # the variables are used by the scripts that source this file
# Paths and fixed versions of the Abschrift setup in the VM. Sourced by the
# scripts in vm/abschrift/ and by vm/setup_abschrift.sh, never run on its own.
#
# Origin: jcode 0.10.0-ma.3 (mejorador-audio plan step 1.0.b1; Abschrift plan
# v12, "Repos in der VM", E5, E8, A8). Every value can be overridden with an
# environment variable of the same name - only the tests do that.

ABSCHRIFT_JCODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Meeting point: bare repository; only Fran (through push.sh) writes to it.
ABSCHRIFT_REPO="${ABSCHRIFT_REPO:-$HOME/repos/abschrift.git}"
# jcode project folder: settings.json, logs/ and code/ (= the verifier clone).
ABSCHRIFT_PROJECT="${ABSCHRIFT_PROJECT:-$HOME/jcode-projekte/abschrift}"
ABSCHRIFT_VERIFIER_CLONE="$ABSCHRIFT_PROJECT/code"
ABSCHRIFT_GEMINI_CLONE="${ABSCHRIFT_GEMINI_CLONE:-$HOME/abschrift-gemini}"
# Cell profiles (part of jcode-adaptado: only the MejoraDeAudio chat changes them).
ABSCHRIFT_PROFILES="$ABSCHRIFT_JCODE_DIR/examples/abschrift"
# Gemini CLI: fixed version, outside PATH (start only through start_gemini.sh).
# Abschrift plan v12 A4/A9: npm "latest" was 0.64.0 on 2026-10-20 (Node >= 20).
GEMINI_CLI_VERSION="${GEMINI_CLI_VERSION:-0.64.0}"
GEMINI_CLI_DIR="${GEMINI_CLI_DIR:-$HOME/.local/opt/gemini-cli}"
GEMINI_CLI_BIN="$GEMINI_CLI_DIR/node_modules/.bin/gemini"
# Playwright browsers of Abschrift, apart from ours (A8). The probe package
# only checks that they start in the verifier cell; Abschrift's package.json
# should pin the same version (otherwise run npm_install.sh after changing it).
PLAYWRIGHT_ABSCHRIFT_DIR="${PLAYWRIGHT_ABSCHRIFT_DIR:-$HOME/.cache/ms-playwright-abschrift}"
PLAYWRIGHT_PROBE_VERSION="${PLAYWRIGHT_PROBE_VERSION:-1.64.1}"
PLAYWRIGHT_PROBE_DIR="${PLAYWRIGHT_PROBE_DIR:-$HOME/.local/opt/playwright-probe-abschrift}"
NPM_CACHE_ABSCHRIFT="${NPM_CACHE_ABSCHRIFT:-$HOME/.cache/npm-abschrift}"
# jcode runs on the Python of venv-audio (openai is installed there).
JCODE_PYTHON="${JCODE_PYTHON:-$HOME/venv-audio/bin/python}"
JCODE_ENTRY="${JCODE_ENTRY:-$ABSCHRIFT_JCODE_DIR/jcode.py}"
CELL_TOOL="$ABSCHRIFT_JCODE_DIR/jcode_cell.py"
