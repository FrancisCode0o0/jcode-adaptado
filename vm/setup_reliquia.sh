#!/usr/bin/env bash
# Set up (or refresh) the read-only review project of La Reliquia del Alba.
#
#     bash ~/jcode-adaptado/vm/setup_reliquia.sh <source> [ref]
#     bash ~/jcode-adaptado/vm/setup_reliquia.sh --refresh <source> [ref]
#
#   <source>  the game's git repository (a folder) or a git bundle file
#             (git bundle create reliquia.bundle <branch>, made on Windows)
#   [ref]     branch, tag or commit to review (default: HEAD of <source>)
#
# Origin: jcode 0.10.0-ma.5 (2026-11-17); ma.6: the modules come from the
# settings.gradle of <ref> (since the branch 'motor': motor, reliquia, lwjgl3,
# headless), no fixed list. Safe to run again: it never overwrites
# settings.json or AGENTS.md and never deletes the reports; --refresh only
# replaces code/juego with a newer snapshot.
#
# Only the Java code and the build files are copied: <module>/src and
# <module>/build.gradle of every module in settings.gradle, plus the root
# build.gradle, settings.gradle and gradle.properties. No res/, docs/ or
# arte/, so the story does not travel to the models. A module without src/
# stops the script: a snapshot without it would look complete and not be.
#
# Steps:
#   1. project folder ~/jcode-projekte/reliquia: settings.json (copy of
#      examples/reliquia), logs/, code/ with AGENTS.md and reports/
#   2. code/juego: snapshot of <ref> (git archive), read-only on disk too
#   3. code/ as a git repository with one commit per snapshot (the git
#      check after each task and session_check.sh need it)
#   4. the cell built for real and inspected from inside

set -Eeuo pipefail

JCODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="${RELIQUIA_PROJECT:-$HOME/jcode-projekte/reliquia}"
TEMPLATE="$JCODE_DIR/examples/reliquia"
readonly ROOT_FILES=(settings.gradle settings.gradle.kts build.gradle build.gradle.kts gradle.properties)
readonly SNAPSHOT_NAME="Snapshot (setup_reliquia.sh)"
readonly SNAPSHOT_EMAIL="snapshot@reliquia.invalid"
readonly TOTAL_STEPS=4

current_step=0
step() { current_step=$1; echo; echo "==> [$1/$TOTAL_STEPS] $2"; }
die() { echo "ERROR: $*" >&2; exit 1; }
trap 'echo "STOPPED at step $current_step/$TOTAL_STEPS. Fix the message above and run again." >&2' ERR

refresh=0
if [ "${1:-}" = "--refresh" ]; then refresh=1; shift; fi
[ $# -ge 1 ] && [ $# -le 2 ] || die "usage: bash vm/setup_reliquia.sh [--refresh] <repo folder or .bundle> [ref]"
source_path=$1
ref=${2:-HEAD}

check_preconditions() {
    [ "$(id -u)" -ne 0 ] || die "run as your normal user, without sudo"
    command -v git >/dev/null || die "git missing"
    command -v bwrap >/dev/null || die "bubblewrap missing (vm/install_ma3.sh installs it)"
    [ -e "$source_path" ] || die "source '$source_path' does not exist"
    [ -f "$TEMPLATE/settings.json" ] || die "template missing: $TEMPLATE (jcode-adaptado older than ma.5?)"
}

copy_once() {  # copy_once <file> <target>: never overwrites
    if [ -e "$2" ]; then
        if cmp -s "$1" "$2"; then echo "   $2 up to date"; else echo "   $2 exists and was NOT overwritten"; fi
    else
        cp "$1" "$2"
        echo "   created $2"
    fi
}

create_project() {
    mkdir -p "$PROJECT/logs" "$PROJECT/code/reports"
    copy_once "$TEMPLATE/settings.json" "$PROJECT/settings.json"
    copy_once "$TEMPLATE/AGENTS.md" "$PROJECT/code/AGENTS.md"
    chmod a-w "$PROJECT/code/AGENTS.md"
}

# Prints the paths to copy, one per line: root build files that exist and,
# for every module in settings.gradle (or settings.gradle.kts), its src/ and
# its build file. Stops (exit 1, message on stderr) if there is no
# settings file, no module, or a module without src/.
snapshot_paths() {  # snapshot_paths <repo> <commit>
    local repo=$1 commit=$2 file settings="" module modules
    for file in settings.gradle settings.gradle.kts; do
        if git -C "$repo" cat-file -e "$commit:$file" 2>/dev/null; then settings=$file; break; fi
    done
    if [ -z "$settings" ]; then echo "ERROR: no settings.gradle in $ref: the modules are unknown" >&2; return 1; fi
    modules=$(git -C "$repo" show "$commit:$settings" | gradle_modules) || {
        echo "ERROR: $settings of $ref could not be read (see above)" >&2; return 1; }
    if [ -z "$modules" ]; then echo "ERROR: $settings of $ref includes no module" >&2; return 1; fi
    for file in "${ROOT_FILES[@]}"; do
        if git -C "$repo" cat-file -e "$commit:$file" 2>/dev/null; then echo "$file"; fi
    done
    while IFS= read -r module; do
        if [ "$(git -C "$repo" cat-file -t "$commit:$module/src" 2>/dev/null)" != "tree" ]; then
            echo "ERROR: module '$module' of $settings has no src/ folder in $ref; nothing was copied" >&2
            return 1
        fi
        echo "$module/src"
        for file in build.gradle build.gradle.kts; do
            if git -C "$repo" cat-file -e "$commit:$module/$file" 2>/dev/null; then echo "$module/$file"; fi
        done
    done <<< "$modules"
}

# Module folders named by the include lines of a settings.gradle on stdin
# (Groovy or Kotlin): include 'a', 'b' / include("a", ":b:c") -> a, b/c.
# A projectDir change would move a module elsewhere: refused, not guessed.
gradle_modules() {
    python3 -I -c '
import re, sys
text = re.sub(r"/\*.*?\*/", "", sys.stdin.read(), flags=re.S)
text = re.sub(r"//[^\n]*", "", text)
if "projectDir" in text:
    sys.exit("ERROR: settings.gradle changes a projectDir; setup_reliquia.sh cannot follow it")
seen = []
for args in re.findall(r"\binclude\b\s*\(?((?:\s*[\x27\x22][^\x27\x22]+[\x27\x22]\s*,?)+)", text):
    for name in re.findall(r"[\x27\x22]([^\x27\x22]+)[\x27\x22]", args):
        folder = name.strip().lstrip(":").replace(":", "/")
        if not re.fullmatch(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*", folder) or ".." in folder.split("/"):
            sys.exit(f"ERROR: module name {name!r} in settings.gradle is not a plain folder")
        if folder not in seen:
            seen.append(folder)
print("\n".join(seen))
'
}

# A bundle is cloned into a temporary folder first: git archive needs a repo.
import_snapshot() {
    local juego="$PROJECT/code/juego" repo tmp="" commit list paths=()
    if [ -d "$juego" ] && [ "$refresh" -ne 1 ]; then
        die "$juego already exists; use --refresh to replace it with a new snapshot"
    fi
    if [ -f "$source_path" ]; then
        tmp=$(mktemp -d)
        git clone -q "$source_path" "$tmp/repo" || die "'$source_path' is not a readable git bundle"
        repo="$tmp/repo"
    else
        repo="$source_path"
    fi
    commit=$(git -C "$repo" rev-parse --verify --quiet "$ref^{commit}") || die "ref '$ref' not found in $source_path"
    list=$(snapshot_paths "$repo" "$commit") || die "$juego was not created (see the message above)"
    mapfile -t paths <<< "$list"
    if [ -d "$juego" ]; then  # --refresh: the new list is valid, the old copy can go
        chmod -R u+w "$juego"
        rm -rf "$juego"
    fi
    mkdir -p "$juego"
    git -C "$repo" archive "$commit" -- "${paths[@]}" | tar -x -C "$juego"
    printf 'source: %s\nref: %s\ncommit: %s\ncopied: %s\nwhen: %s\n' "$source_path" "$ref" "$commit" \
        "${paths[*]}" "$(date '+%Y-%m-%d %H:%M')" > "$PROJECT/code/ORIGEN.txt"
    [ -z "$tmp" ] || rm -rf "$tmp"
    chmod -R a-w "$juego"
    echo "   modules: $(printf '%s\n' "${paths[@]}" | sed -n 's#/src$##p' | tr '\n' ' ')"
    echo "   code/juego = $commit ($(find "$juego" -name '*.java' | wc -l) Java files)"
}

commit_snapshot() {
    local code="$PROJECT/code"
    if ! git -C "$code" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        git -C "$code" init -q
    fi
    git -C "$code" add -A -- juego AGENTS.md ORIGEN.txt
    if git -C "$code" diff --cached --quiet; then
        echo "   snapshot already committed"
    else
        git -C "$code" -c user.name="$SNAPSHOT_NAME" -c user.email="$SNAPSHOT_EMAIL" \
            commit -q -m "Snapshot $(sed -n 's/^commit: //p' "$code/ORIGEN.txt")"
        echo "   committed: $(git -C "$code" log -1 --format='%h %s')"
    fi
}

check_preconditions
step 1 "Project folder $PROJECT"
create_project
step 2 "Snapshot of the game's code"
import_snapshot
step 3 "git in code/ (one commit per snapshot)"
commit_snapshot
step 4 "Cell, built and inspected from inside"
python3 -I "$JCODE_DIR/jcode_cell.py" check --settings "$PROJECT/settings.json" --workdir "$PROJECT/code"

echo
echo "RELIQUIA SETUP OK"
echo "Next: bash $JCODE_DIR/vm/session_check.sh $PROJECT"
echo "      python3 $JCODE_DIR/jcode.py --project $PROJECT"
