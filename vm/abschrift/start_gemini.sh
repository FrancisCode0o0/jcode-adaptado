#!/usr/bin/env bash
# Start Gemini CLI for Abschrift - the ONLY way to start it (it is not on PATH).
#
#     bash ~/jcode-adaptado/vm/abschrift/start_gemini.sh [--branch <phase>] [gemini options]
#     bash ~/jcode-adaptado/vm/abschrift/start_gemini.sh --version
#
# Origin: jcode 0.10.0-ma.3 (mejorador-audio plan step 1.0.b1); Abschrift
# plan v12, requirements E5, A5, A6, A7, A9. In order:
#   1. git in the Gemini clone (~/abschrift-gemini) runs in the SYNC cell
#      (examples/abschrift/sync-cell.json): Gemini CLI can write its clone's
#      .git, so hooks or configuration found there must never run outside a
#      cell. Inside: no network, no home folder, the meeting repo read-only.
#      a) uncommitted changes -> stop and show them;
#      b) --branch <phase> -> switch (local, else from the meeting repo,
#         else a new local branch);
#      c) 'git pull --ff-only' from the meeting repo.
#   2. Gemini CLI starts in ITS cell (examples/abschrift/gemini-cell.json):
#      writes only the clone (tests/acceptance/ read-only) and ~/.gemini; no
#      ~/.bashrc, no API key, no other project. First start: log in with the
#      AI Pro account - NO_BROWSER=true prints a URL, open it on any device,
#      paste the code back.
# Difference to the Abschrift plan's wording (A6 "outside the cell"): the
# git commands run outside the AGENT's cell, in a cell of their own - see
# the jcode-ma.3 report. It never discards or restores anything.

set -Eeuo pipefail
# shellcheck source=vm/abschrift/paths.sh
source "$(dirname "${BASH_SOURCE[0]}")/paths.sh"

die() { echo "STOP: $*" >&2; exit 1; }

branch=""
if [ "${1:-}" = "--branch" ]; then
    [ -n "${2:-}" ] || die "--branch needs a name, e.g. --branch phase-2"
    branch=$2
    shift 2
    git check-ref-format --branch "$branch" >/dev/null || die "'$branch' is not a valid branch name"
fi
clone=$ABSCHRIFT_GEMINI_CLONE
[ -d "$clone/.git" ] || die "$clone is not a git clone (run: bash vm/setup_abschrift.sh)"
[ -x "$GEMINI_CLI_BIN" ] || die "Gemini CLI missing in $GEMINI_CLI_DIR (run: bash vm/setup_abschrift.sh)"

# Runs inside the sync cell. Exit 3 = uncommitted changes (listed).
# shellcheck disable=SC2016  # $1/$2 belong to the inner shell
SYNC_SCRIPT='
set -u
expected_origin=$1; branch=$2
origin=$(git config --get remote.origin.url || true)
if [ "$origin" != "$expected_origin" ]; then echo "clone pulls from \"$origin\", expected $expected_origin" >&2; exit 4; fi
changes=$(git status --porcelain --untracked-files=all)
if [ -n "$changes" ]; then printf "%s\n" "$changes"; exit 3; fi
remote_has() { git ls-remote --exit-code --heads origin "$1" >/dev/null 2>&1; }
current=$(git symbolic-ref --quiet --short HEAD || echo "")
if [ -n "$branch" ] && [ "$branch" != "$current" ]; then
    if git show-ref --verify --quiet "refs/heads/$branch"; then git checkout --quiet "$branch" || exit 5
    elif remote_has "$branch"; then
        git fetch --quiet origin "$branch" && git checkout --quiet -b "$branch" --track "origin/$branch" || exit 5
    else git checkout --quiet -b "$branch" || exit 5; echo "New local branch $branch (not in the meeting repo yet)."
    fi
    current=$branch
fi
if [ -n "$current" ] && remote_has "$current"; then
    git pull --quiet --ff-only origin "$current" || exit 6
    echo "Gemini clone: $current at $(git rev-parse --short HEAD), up to date."
else
    echo "Gemini clone: ${current:-no branch yet} - nothing to pull (not in the meeting repo yet)."
fi
'

set +e
python3 -I "$CELL_TOOL" run --settings "$ABSCHRIFT_PROFILES/sync-cell.json" --workdir "$clone" \
    -- /bin/sh -c "$SYNC_SCRIPT" sync "$ABSCHRIFT_REPO" "$branch"
status=$?
set -e
case "$status" in
    0) ;;
    3) die "the Gemini clone has uncommitted changes (listed above). Nothing started. Let Gemini CLI commit them or undo them yourself." ;;
    4) die "the Gemini clone does not pull from the meeting repo. Nothing started." ;;
    5) die "could not switch to branch '$branch'. Nothing started." ;;
    6) die "'git pull --ff-only' failed: the clone and the meeting repo have diverged. Nothing started." ;;
    *) die "the sync cell failed (exit $status; see above). Nothing started." ;;
esac

exec python3 -I "$CELL_TOOL" run --settings "$ABSCHRIFT_PROFILES/gemini-cell.json" \
    --workdir "$clone" -- "$GEMINI_CLI_BIN" "$@"
