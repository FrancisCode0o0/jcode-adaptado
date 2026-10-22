#!/usr/bin/env bash
# Set up everything Abschrift needs in the VM, next to mejorador-audio.
#
#     bash ~/jcode-adaptado/vm/setup_abschrift.sh
#
# Origin: jcode 0.10.0-ma.3 (mejorador-audio plan step 1.0.b1); Abschrift
# plan v12, requirement E8 (and E5, A1-A9). Normally started by
# vm/install_ma3.sh; safe to run again: every step checks whether its work
# is already done, and it never overwrites, deletes or forces anything.
# It changes nothing of mejorador-audio: no file in ~/jcode-adaptado/code,
# venv-audio, our Node or our Playwright browsers (~/.cache/ms-playwright).
#
# Steps:
#   1. meeting repo ~/repos/abschrift.git (bare, empty at first)
#   2. clones: ~/jcode-projekte/abschrift/code (jcode verifier) and
#      ~/abschrift-gemini (Gemini CLI); identity per clone; the verifier
#      clone's commit hook -> vm/agent-hooks (trusted, outside every cell)
#   3. project folder: settings.json (copy of examples/abschrift), logs/
#   4. folders: ~/.gemini, ~/.cache/ms-playwright-abschrift, npm cache
#   5. Gemini CLI, fixed version, in ~/.local/opt/gemini-cli (install cell);
#      ~/.gemini/settings.json: no auto-update, login with the Google
#      account (oauth-personal) - only if the file does not exist yet
#   6. Playwright browsers of Abschrift (install cell) + probe package
#   7. both cells built for real and inspected from inside

set -Eeuo pipefail
# shellcheck source=vm/abschrift/paths.sh
source "$(dirname "${BASH_SOURCE[0]}")/abschrift/paths.sh"

readonly TOTAL_STEPS=7
# Identity for Fran's commits in the verifier clone (only if git has none).
# It ends up in the public repo: the GitHub "noreply" form keeps the real
# address private. Change both lines here if you prefer another one.
readonly FRAN_NAME="Francis García Reyes"
readonly FRAN_EMAIL="FrancisCode0o0@users.noreply.github.com"
readonly GEMINI_NAME="Gemini CLI (Abschrift)"
readonly GEMINI_EMAIL="gemini-cli@abschrift.invalid"
NODE_BIN_DIR="${NODE_BIN_DIR:-$HOME/.local/opt/node/bin}"
SKIP_DOWNLOADS="${SKIP_DOWNLOADS:-0}"   # tests: no npm, no browser download

current_step=0
step() { current_step=$1; echo; echo "==> [$1/$TOTAL_STEPS] $2"; }
die() { echo "ERROR: $*" >&2; exit 1; }
trap 'echo "STOPPED at step $current_step/$TOTAL_STEPS. Fix the message above and run again." >&2' ERR

check_preconditions() {
    [ "$(id -u)" -ne 0 ] || die "run as your normal user, without sudo"
    command -v git >/dev/null || die "git missing"
    command -v bwrap >/dev/null || die "bubblewrap missing (vm/install_ma3.sh installs it)"
    [ -x "$NODE_BIN_DIR/node" ] || die "Node missing in $NODE_BIN_DIR (block D: vm/setup_vm.sh)"
    export PATH="$NODE_BIN_DIR:$PATH"
}

in_cell() {  # in_cell <profile> <workdir> <command...>
    local profile=$1 workdir=$2
    shift 2
    python3 -I "$CELL_TOOL" run --settings "$ABSCHRIFT_PROFILES/$profile" --workdir "$workdir" -- "$@"
}

create_meeting_repo() {
    if [ -e "$ABSCHRIFT_REPO" ]; then
        [ "$(git -C "$ABSCHRIFT_REPO" rev-parse --is-bare-repository 2>/dev/null)" = "true" ] \
            || die "$ABSCHRIFT_REPO exists but is not a bare repository; nothing changed"
        echo "Meeting repo exists: $ABSCHRIFT_REPO"
        return
    fi
    mkdir -p "$(dirname "$ABSCHRIFT_REPO")"
    git init --quiet --bare --initial-branch=main "$ABSCHRIFT_REPO"
    echo "Meeting repo created (empty): $ABSCHRIFT_REPO"
}

# Reading a config value runs no hook or helper, so it is safe also in the
# Gemini clone. A clone that pulls from elsewhere is never "fixed" silently.
create_clone() {
    local dir=$1 name=$2 email=$3 origin
    if [ -e "$dir" ]; then
        [ -d "$dir/.git" ] || die "$dir exists but is not a git clone; nothing changed"
        origin=$(git -C "$dir" config --get remote.origin.url || true)
        [ "$origin" = "$ABSCHRIFT_REPO" ] || die "$dir pulls from '$origin', expected $ABSCHRIFT_REPO"
        echo "Clone exists: $dir"
    else
        mkdir -p "$(dirname "$dir")"
        git clone --quiet "$ABSCHRIFT_REPO" "$dir" 2>/dev/null
        echo "Clone created: $dir"
    fi
    git -C "$dir" config --get user.name >/dev/null || git -C "$dir" config user.name "$name"
    git -C "$dir" config --get user.email >/dev/null || git -C "$dir" config user.email "$email"
}

create_clones() {
    local global_name global_email
    global_name=$(git config --global --get user.name || echo "$FRAN_NAME")
    global_email=$(git config --global --get user.email || echo "$FRAN_EMAIL")
    create_clone "$ABSCHRIFT_VERIFIER_CLONE" "$global_name" "$global_email"
    create_clone "$ABSCHRIFT_GEMINI_CLONE" "$GEMINI_NAME" "$GEMINI_EMAIL"
    local hooks="$ABSCHRIFT_JCODE_DIR/vm/agent-hooks" current
    current=$(git -C "$ABSCHRIFT_VERIFIER_CLONE" config --get core.hooksPath || true)
    if [ -z "$current" ]; then
        git -C "$ABSCHRIFT_VERIFIER_CLONE" config core.hooksPath "$hooks"
    elif [ "$current" != "$hooks" ]; then
        die "the verifier clone's core.hooksPath is '$current', expected $hooks; nothing changed"
    fi
    # A commit from Windows can drop the executable bit, and git silently
    # skips a hook that is not executable: then no secret check would run.
    [ -x "$hooks/pre-commit" ] || die "$hooks/pre-commit is not executable (vm/windows/install_ma3.ps1 sets the bit in git; then git pull)"
    echo "Verifier clone: commits as $(git -C "$ABSCHRIFT_VERIFIER_CLONE" config user.name), hook $hooks"
}

create_project_folder() {
    local source="$ABSCHRIFT_PROFILES/settings.json" target="$ABSCHRIFT_PROJECT/settings.json"
    mkdir -p "$ABSCHRIFT_PROJECT/logs"
    if [ ! -e "$target" ]; then
        cp "$source" "$target"
        echo "settings.json copied to $target"
    elif cmp -s "$source" "$target"; then
        echo "settings.json up to date"
    else
        echo "WARNING: $target differs from the shipped one and was NOT overwritten."
        echo "         Compare: diff $source $target"
    fi
}

create_folders() {
    mkdir -p "$HOME/.gemini" "$PLAYWRIGHT_ABSCHRIFT_DIR" "$NPM_CACHE_ABSCHRIFT"
    chmod 700 "$HOME/.gemini"
    echo "Folders ready: ~/.gemini, $PLAYWRIGHT_ABSCHRIFT_DIR, $NPM_CACHE_ABSCHRIFT"
}

installed_gemini_version() {
    python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' \
        "$GEMINI_CLI_DIR/node_modules/@google/gemini-cli/package.json" 2>/dev/null || true
}

install_gemini_cli() {
    local installed
    installed=$(installed_gemini_version)
    if [ "$installed" = "$GEMINI_CLI_VERSION" ]; then
        echo "Gemini CLI $installed already installed. Skipped."
    elif [ -n "$installed" ]; then
        die "Gemini CLI $installed found, this jcode pins $GEMINI_CLI_VERSION; nothing changed (ask in the MejoraDeAudio chat)"
    elif [ "$SKIP_DOWNLOADS" = "1" ]; then
        echo "Gemini CLI download skipped (SKIP_DOWNLOADS=1)."
        return
    else
        mkdir -p "$GEMINI_CLI_DIR"
        echo "npm 'latest' today: $(in_cell install-cell.json "$GEMINI_CLI_DIR" npm view @google/gemini-cli version 2>/dev/null || echo unknown); installing the pinned $GEMINI_CLI_VERSION"
        in_cell install-cell.json "$GEMINI_CLI_DIR" \
            npm install --prefix "$GEMINI_CLI_DIR" "@google/gemini-cli@$GEMINI_CLI_VERSION"
    fi
    write_gemini_settings
    local reported
    # The cell may give Gemini CLI its own terminal, which ends lines with \r\n.
    reported=$(in_cell gemini-cell.json "$ABSCHRIFT_GEMINI_CLONE" "$GEMINI_CLI_BIN" --version | tr -d '\r')
    [ "$reported" = "$GEMINI_CLI_VERSION" ] || die "gemini --version says '$reported', expected $GEMINI_CLI_VERSION"
    echo "Gemini CLI $reported starts in its cell."
}

write_gemini_settings() {
    local file="$HOME/.gemini/settings.json"
    if [ -e "$file" ]; then
        echo "$file exists - left as it is."
        return
    fi
    cat > "$file" <<'JSON'
{
  "general": { "enableAutoUpdate": false, "enableAutoUpdateNotification": false },
  "security": { "auth": { "selectedType": "oauth-personal" } }
}
JSON
    echo "$file written (no auto-update; login with the Google account)."
}

install_browsers() {
    if [ "$SKIP_DOWNLOADS" = "1" ]; then
        echo "Browser download skipped (SKIP_DOWNLOADS=1)."
        return
    fi
    mkdir -p "$PLAYWRIGHT_PROBE_DIR"
    if [ ! -d "$PLAYWRIGHT_PROBE_DIR/node_modules/@playwright/test" ]; then
        in_cell install-cell.json "$PLAYWRIGHT_PROBE_DIR" \
            npm install --prefix "$PLAYWRIGHT_PROBE_DIR" "@playwright/test@$PLAYWRIGHT_PROBE_VERSION"
    fi
    in_cell install-cell.json "$PLAYWRIGHT_PROBE_DIR" \
        "$PLAYWRIGHT_PROBE_DIR/node_modules/.bin/playwright" install chromium firefox webkit
    echo "Playwright $PLAYWRIGHT_PROBE_VERSION browsers in $PLAYWRIGHT_ABSCHRIFT_DIR"
}

check_cells() {
    local failed=0
    echo "-- cell of jcode (verifier):"
    python3 -I "$CELL_TOOL" check --settings "$ABSCHRIFT_PROJECT/settings.json" \
        --workdir "$ABSCHRIFT_VERIFIER_CLONE" || failed=1
    echo "-- cell of Gemini CLI:"
    python3 -I "$CELL_TOOL" check --settings "$ABSCHRIFT_PROFILES/gemini-cell.json" \
        --workdir "$ABSCHRIFT_GEMINI_CLONE" || failed=1
    echo "-- sync cell (git in the Gemini clone):"
    python3 -I "$CELL_TOOL" check --settings "$ABSCHRIFT_PROFILES/sync-cell.json" \
        --workdir "$ABSCHRIFT_GEMINI_CLONE" || failed=1
    [ "$failed" -eq 0 ] || die "a cell check failed (FAIL lines above)"
}

main() {
    check_preconditions
    step 1 "Meeting repo";                   create_meeting_repo
    step 2 "Clones";                         create_clones
    step 3 "Project folder for jcode";       create_project_folder
    step 4 "Folders";                        create_folders
    step 5 "Gemini CLI $GEMINI_CLI_VERSION"; install_gemini_cli
    step 6 "Playwright browsers (Abschrift)"; install_browsers
    step 7 "Cells";                          check_cells
    echo
    echo "ABSCHRIFT SETUP OK"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main
fi
