#!/usr/bin/env bash
# Fran's push of an agent clone to the meeting repo of Abschrift.
#
#     bash ~/jcode-adaptado/vm/abschrift/push.sh gemini   [branch]
#     bash ~/jcode-adaptado/vm/abschrift/push.sh verifier [branch]
#
# Origin: jcode 0.10.0-ma.3 (mejorador-audio plan step 1.0.b1); Abschrift
# plan v12: "git push zum Treffpunkt macht nur Fran", tests/acceptance/ tabu
# for Gemini CLI, no secrets in the public repo.
# Instead of 'git push' inside the clone (which would run the clone's hooks
# and configuration - written by an agent - outside any cell), git runs only
# in the meeting repo, whose configuration is Fran's:
#   1. fetch the branch from the clone into refs/incoming/<agent>/<branch>
#      (the clone only answers with data; its hooks never run);
#   2. new commits = what no branch of the meeting repo has yet;
#   3. check_secrets.py (jcode-adaptado's copy) over every line they add;
#   4. gemini: refuse if any of them touches tests/acceptance/;
#   5. fast-forward only: the branch moves only if it contains the old one;
#   6. the incoming ref is removed again in every case.
# It never forces, deletes branches or changes the clones.

set -Eeuo pipefail
# shellcheck source=vm/abschrift/paths.sh
source "$(dirname "${BASH_SOURCE[0]}")/paths.sh"

die() { echo "STOP: $*" >&2; exit 1; }
usage="usage: bash vm/abschrift/push.sh gemini|verifier [branch]"

case "${1:-}" in
    gemini) clone=$ABSCHRIFT_GEMINI_CLONE ;;
    verifier) clone=$ABSCHRIFT_VERIFIER_CLONE ;;
    *) echo "$usage" >&2; exit 2 ;;
esac
agent=$1
repo=$ABSCHRIFT_REPO
[ -d "$clone/.git" ] || die "$clone is not a git clone"
[ "$(git -C "$repo" rev-parse --is-bare-repository 2>/dev/null)" = "true" ] \
    || die "$repo is not the bare meeting repo (run: bash vm/setup_abschrift.sh)"

# The branch is read from .git/HEAD as text: no git command runs in the clone.
branch=${2:-}
if [ -z "$branch" ]; then
    head=$(cat "$clone/.git/HEAD")
    case "$head" in
        "ref: refs/heads/"*) branch=${head#ref: refs/heads/} ;;
        *) die "the $agent clone has no branch checked out; name one: $usage" ;;
    esac
fi
git check-ref-format --branch "$branch" >/dev/null || die "'$branch' is not a valid branch name"

incoming="refs/incoming/$agent/$branch"
cleanup() { git -C "$repo" update-ref -d "$incoming" 2>/dev/null || true; }
trap cleanup EXIT

git -C "$repo" fetch --quiet --no-tags "$clone" "+refs/heads/$branch:$incoming" \
    || die "the $agent clone has no branch '$branch'"
new=$(git -C "$repo" rev-parse "$incoming")
old=$(git -C "$repo" rev-parse --verify --quiet "refs/heads/$branch" || true)

if [ "$new" = "$old" ]; then
    echo "Nothing new: $branch is already at ${new:0:7} in the meeting repo."
    exit 0
fi
if [ -n "$old" ] && ! git -C "$repo" merge-base --is-ancestor "$old" "$new"; then
    die "not a fast-forward: $branch in the meeting repo (${old:0:7}) is not contained in the $agent clone. Nothing changed (never forced). Bring the clone up to date first (start script)."
fi

commits=$(git -C "$repo" rev-list --count "$new" --not --branches)
echo "$commits new commit(s) from the $agent clone on $branch."

# 3. Secrets: every line the new commits add (also lines removed later on).
git -C "$repo" log --format= -p --unified=0 --no-color --no-ext-diff --no-textconv \
    "$new" --not --branches \
    | python3 -I "$ABSCHRIFT_JCODE_DIR/tools/check_secrets.py" --diff - \
    || die "possible secret in the new commits (file and line above, value never shown). Nothing changed."

# 4. Gemini CLI must never change the acceptance tests.
if [ "$agent" = "gemini" ]; then
    touched=$(git -C "$repo" log --format= --name-only "$new" --not --branches -- tests/acceptance/ | sort -u)
    if [ -n "$touched" ]; then
        echo "Gemini CLI changed acceptance tests:" >&2
        printf '%s\n' "$touched" | sed 's/^/    /' >&2
        die "tests/acceptance/ is taboo for Gemini CLI (Abschrift plan). Nothing changed."
    fi
fi

# 5. Move the branch (compare-and-swap: fails if someone moved it meanwhile).
git -C "$repo" update-ref -m "push.sh $agent" "refs/heads/$branch" "$new" "${old:-}"
git -C "$repo" log --oneline --no-decorate "${old:+$old..}$new" | head -n 20 | sed 's/^/    /'
echo "Meeting repo: $branch now at ${new:0:7}${old:+ (was ${old:0:7})}."
