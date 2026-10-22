#!/usr/bin/env bash
# Start jcode for Abschrift (verifier mode, DeepSeek, inside its cell).
#
#     bash ~/jcode-adaptado/vm/abschrift/start_jcode.sh [--branch <phase>] [task ...]
#
# Origin: jcode 0.10.0-ma.3 (mejorador-audio plan step 1.0.b1); Abschrift
# plan v12, requirements A6 (fetch the state before starting, stop on
# uncommitted changes) and A7 (branch change only here or by Fran, outside
# the cell). What happens, in order, all OUTSIDE the cell:
#   1. the verifier clone (~/jcode-projekte/abschrift/code) must be clean:
#      tests the verifier wrote are waiting for Fran's review -> stop and
#      show them (commit them, then push with push.sh verifier);
#   2. --branch <phase>: switch to it (local, else from the meeting repo,
#      else a new local branch);
#   3. 'git pull --ff-only' from the meeting repo (~/repos/abschrift.git);
#   4. jcode --project ~/jcode-projekte/abschrift: jcode itself refuses to
#      start without a working cell or without a key of its model chain.
# Running git here is safe: inside the cell the clone's .git is read-only,
# so the agent cannot plant hooks or configuration that would run here.
# It never discards or restores anything.

set -Eeuo pipefail
# shellcheck source=vm/abschrift/paths.sh
source "$(dirname "${BASH_SOURCE[0]}")/paths.sh"

die() { echo "STOP: $*" >&2; exit 1; }

branch=""
if [ "${1:-}" = "--branch" ]; then
    [ -n "${2:-}" ] || die "--branch needs a name, e.g. --branch phase-2"
    branch=$2
    shift 2
fi
clone=$ABSCHRIFT_VERIFIER_CLONE

[ -d "$clone/.git" ] || die "$clone is not a git clone (run: bash vm/setup_abschrift.sh)"
origin=$(git -C "$clone" config --get remote.origin.url || true)
[ "$origin" = "$ABSCHRIFT_REPO" ] || die "$clone pulls from '$origin', expected $ABSCHRIFT_REPO"

changes=$(git -C "$clone" status --porcelain --untracked-files=all)
if [ -n "$changes" ]; then
    echo "The verifier clone has uncommitted changes (review them first):" >&2
    printf '%s\n' "$changes" | sed 's/^/    /' >&2
    die "nothing started. Review, then 'git -C $clone add ...' + commit + 'bash vm/abschrift/push.sh verifier' (or undo them yourself)."
fi

remote_has() { git -C "$clone" ls-remote --exit-code --heads origin "$1" >/dev/null 2>&1; }

current=$(git -C "$clone" symbolic-ref --quiet --short HEAD || echo "")
if [ -n "$branch" ] && [ "$branch" != "$current" ]; then
    git check-ref-format --branch "$branch" >/dev/null || die "'$branch' is not a valid branch name"
    if git -C "$clone" show-ref --verify --quiet "refs/heads/$branch"; then
        git -C "$clone" checkout --quiet "$branch"
    elif remote_has "$branch"; then
        git -C "$clone" fetch --quiet origin "$branch"
        git -C "$clone" checkout --quiet -b "$branch" --track "origin/$branch"
    else
        git -C "$clone" checkout --quiet -b "$branch"
        echo "New local branch $branch (not in the meeting repo yet)."
    fi
    current=$branch
fi

if [ -n "$current" ] && remote_has "$current"; then
    git -C "$clone" pull --quiet --ff-only origin "$current" \
        || die "'git pull --ff-only' failed: the clone and the meeting repo have diverged. Nothing changed."
    echo "Verifier clone: $current at $(git -C "$clone" rev-parse --short HEAD), up to date."
else
    echo "Verifier clone: ${current:-no branch yet} - nothing to pull (not in the meeting repo yet)."
fi

exec "$JCODE_PYTHON" "$JCODE_ENTRY" --project "$ABSCHRIFT_PROJECT" "$@"
