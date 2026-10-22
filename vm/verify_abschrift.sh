#!/usr/bin/env bash
# Check the Abschrift setup against the list of Abschrift plan v12, step 0.1.g,
# and write a report WITHOUT keys to paste into the Abschrift chat.
#
#     bash ~/jcode-adaptado/vm/verify_abschrift.sh
#
# Origin: jcode 0.10.0-ma.3 (mejorador-audio plan step 1.0.b1, automation of
# 2026-10-20). Runs everything, also after a failure, and marks each point
# OK / FAIL / PENDING (PENDING = waits for something only Fran can do: keys,
# Gemini login). The report goes to ~/abschrift-verify-<date>.txt and to the
# screen. Before it is shown, it is scanned with check_secrets.py (also for
# the VALUES of the keys set in this shell): if anything matches, nothing is
# shown and the script stops.
# It changes nothing: the probes write only temporary folders and their own
# probe files (created and removed inside the cells).
# Exit code: 0 = no FAIL, 1 = at least one FAIL.

set -uo pipefail
# shellcheck source=vm/abschrift/paths.sh
source "$(dirname "${BASH_SOURCE[0]}")/abschrift/paths.sh"

VM_DIR="$ABSCHRIFT_JCODE_DIR/vm"
REPORT="${REPORT:-$HOME/abschrift-verify-$(date '+%Y%m%d-%H%M').txt}"
NODE_BIN_DIR="${NODE_BIN_DIR:-$HOME/.local/opt/node/bin}"
export PATH="$NODE_BIN_DIR:$PATH"
fails=0
pending=0
: > "$REPORT"
chmod 600 "$REPORT"

line() { printf '%s\n' "$*" >> "$REPORT"; }
section() { line ""; line "$*"; }
# One result line: OK|FAIL|PENDING text
result() {
    local level=$1
    shift
    case "$level" in
        FAIL) fails=$((fails + 1)) ;;
        PENDING) pending=$((pending + 1)) ;;
    esac
    line "  [$level] $*"
}
# Lines "OK text"/"FAIL text"/"PENDING text"/"WARN text" from a helper.
relay() {
    local raw level text count=0
    while IFS= read -r raw; do
        [ -z "$raw" ] && continue
        count=$((count + 1))
        level=${raw%% *}
        text=${raw#* }
        case "$level" in
            OK|FAIL|PENDING) result "$level" "$text" ;;
            WARN) result OK "(warning) $text" ;;
            note:) result OK "note: $text" ;;
            *) result FAIL "$raw" ;;
        esac
    done
    [ "$count" -gt 0 ] || result FAIL "the check printed nothing (crashed?)"
}
keys_set() { [ -n "${OPENCODE_API_KEY:-}" ] || [ -n "${NVIDIA_API_KEY:-}" ]; }

session_check() {  # session_check <label> [project]
    local label=$1 output verdict
    shift
    output=$(bash "$VM_DIR/session_check.sh" --quick "$@" 2>&1)
    verdict=$(printf '%s\n' "$output" | grep -E '^(GREEN|RED)' | tail -n 1)
    if [ "${verdict%%:*}" = "GREEN" ]; then
        result OK "$label: $verdict"
    elif [ -n "${1:-}" ] && ! keys_set; then
        result PENDING "$label: $verdict (the Abschrift keys are not set yet)"
    else
        result FAIL "$label: ${verdict:-no verdict}"
    fi
    printf '%s\n' "$output" | grep -E '^(ERROR|WARN)' | sed 's/^/        /' >> "$REPORT"
}

jcode_probe() { python3 -I "$ABSCHRIFT_JCODE_DIR/vm/abschrift/verify_jcode.py" "$1" "$ABSCHRIFT_PROJECT"; }

cell_check() {  # cell_check <settings> <workdir>
    python3 -I "$CELL_TOOL" check --settings "$1" --workdir "$2" 2>&1
}

# The three Playwright browsers of Abschrift, started in the VERIFIER cell.
# shellcheck disable=SC2016  # JavaScript template literals, not shell
PLAYWRIGHT_JS='
const pw = require(process.argv[1] + "/node_modules/@playwright/test");
(async () => {
  for (const name of ["chromium", "firefox", "webkit"]) {
    try {
      const browser = await pw[name].launch();
      const page = await browser.newPage();
      await page.setContent("<p>ok</p>");
      const text = await page.textContent("p");
      console.log(`${text === "ok" ? "OK" : "FAIL"} ${name} ${browser.version()} starts in the verifier cell`);
      await browser.close();
    } catch (error) {
      console.log(`FAIL ${name}: ${String(error.message).split("\n")[0]}`);
    }
  }
})();'

browsers() {
    if [ ! -d "$PLAYWRIGHT_PROBE_DIR/node_modules/@playwright/test" ]; then
        echo "FAIL Playwright probe package missing in $PLAYWRIGHT_PROBE_DIR (setup_abschrift.sh step 6)"
        return
    fi
    timeout 300 python3 -I "$CELL_TOOL" run --settings "$ABSCHRIFT_PROJECT/settings.json" \
        --workdir "$ABSCHRIFT_VERIFIER_CLONE" --extra-ro "$PLAYWRIGHT_PROBE_DIR" \
        -- node -e "$PLAYWRIGHT_JS" "$PLAYWRIGHT_PROBE_DIR" 2>&1 | grep -E '^(OK|FAIL)'
}

gemini_version() {
    local output
    output=$(bash "$ABSCHRIFT_JCODE_DIR/vm/abschrift/start_gemini.sh" --version 2>&1 | tr -d '\r' | tail -n 1)
    if [ "$output" = "$GEMINI_CLI_VERSION" ]; then
        echo "OK start_gemini.sh --version: $output (pinned $GEMINI_CLI_VERSION)"
    else
        echo "FAIL start_gemini.sh --version: $output (expected $GEMINI_CLI_VERSION)"
    fi
}

ssh_server() {
    if systemctl is-active --quiet ssh 2>/dev/null || systemctl is-active --quiet ssh.socket 2>/dev/null; then
        if ss -ltn 2>/dev/null | grep -qE '[:.]22 '; then
            echo "OK SSH server active and listening on port 22 (VM address: $(hostname -I 2>/dev/null | awk '{print $1}'))"
        else
            echo "FAIL SSH service active but nothing listens on port 22"
        fi
    else
        echo "PENDING no SSH server active (Abschrift 1.4 needs it; guide, section SSH)"
    fi
}

clean_clones() {
    local changes
    changes=$(git -C "$ABSCHRIFT_VERIFIER_CLONE" status --porcelain --untracked-files=all 2>&1)
    if [ -z "$changes" ]; then echo "OK verifier clone clean"; else echo "FAIL verifier clone not clean: $(printf '%s' "$changes" | head -n 3 | tr '\n' ' ')"; fi
    # Gemini clone: git only inside the sync cell (its hooks are not trusted).
    changes=$(python3 -I "$CELL_TOOL" run --settings "$ABSCHRIFT_PROFILES/sync-cell.json" \
        --workdir "$ABSCHRIFT_GEMINI_CLONE" -- git status --porcelain --untracked-files=all 2>/dev/null)
    if [ -z "$changes" ]; then echo "OK Gemini clone clean (checked in the sync cell)"; else echo "FAIL Gemini clone not clean: $(printf '%s' "$changes" | head -n 3 | tr '\n' ' ')"; fi
}

facts() {
    # shellcheck disable=SC1091  # system file, read only for its PRETTY_NAME
    line "  Ubuntu:   $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"
    line "  Kernel:   $(uname -r)"
    line "  bwrap:    $(bwrap --version 2>/dev/null || echo missing)"
    line "  AppArmor: kernel.apparmor_restrict_unprivileged_userns=$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || echo n/a); profile bwrap-userns-restrict $([ -e /etc/apparmor.d/bwrap-userns-restrict ] && echo loaded || echo absent)"
    line "  TIOCSTI:  dev.tty.legacy_tiocsti=$(cat /proc/sys/dev/tty/legacy_tiocsti 2>/dev/null || echo n/a) (0 = no keystroke injection into the terminal)"
    line "  jcode:    $(sed -n 's/^APP_VERSION = "\([^"]*\)".*/\1/p' "$ABSCHRIFT_JCODE_DIR/jcode.py"), commit $(git -C "$ABSCHRIFT_JCODE_DIR" rev-parse --short HEAD 2>/dev/null)"
    line "  Python:   $(python3 --version 2>&1)   Node: $(node --version 2>/dev/null || echo missing)"
    line "  Gemini CLI pinned $GEMINI_CLI_VERSION; Playwright probe $PLAYWRIGHT_PROBE_VERSION"
    line "  Keys:     OPENCODE_API_KEY $([ -n "${OPENCODE_API_KEY:-}" ] && echo set || echo 'NOT set'), NVIDIA_API_KEY $([ -n "${NVIDIA_API_KEY:-}" ] && echo set || echo 'NOT set') (values never read into this report)"
}

line "Abschrift setup check (Abschrift plan v12, 0.1.g) - $(date '+%Y-%m-%d %H:%M')"
line "Generated by jcode-adaptado/vm/verify_abschrift.sh. Contains no keys."
section "Environment"; facts

section "1. session_check.sh - both projects GREEN"
session_check "session_check.sh ~/jcode-projekte/abschrift" "$ABSCHRIFT_PROJECT"
session_check "session_check.sh (mejorador-audio)"
section "2. jcode --project starts as VERIFIER with the DeepSeek chain; no key or no cell -> no start"
relay < <(jcode_probe start; jcode_probe nokey; jcode_probe nocell)
section "3. verifier: write_file in tests/acceptance/ yes, in public/ no"
relay < <(jcode_probe write-guard)
section "4. cell of the verifier (from inside): no ~/.bashrc, ~/.gemini, /mnt/datos, ~/abschrift-gemini; no key variable"
relay < <(cell_check "$ABSCHRIFT_PROJECT/settings.json" "$ABSCHRIFT_VERIFIER_CLONE"; jcode_probe env-count)
section "5. cell of Gemini CLI: tests/acceptance/ read-only; no ~/.bashrc, ~/jcode-projekte, /mnt/datos"
relay < <(cell_check "$ABSCHRIFT_PROFILES/gemini-cell.json" "$ABSCHRIFT_GEMINI_CLONE")
section "6. short request to DeepSeek -> answer with a tool call"
relay < <(jcode_probe deepseek)
section "7. Gemini CLI only through the start script, logged in with AI Pro (no browser)"
if command -v gemini >/dev/null 2>&1; then
    result FAIL "a 'gemini' command is on PATH ($(command -v gemini)): it would start without its cell"
else
    result OK "no 'gemini' on PATH (only vm/abschrift/start_gemini.sh starts it)"
fi
relay < <(gemini_version)
if [ -s "$HOME/.gemini/oauth_creds.json" ]; then
    result OK "Gemini CLI login stored (~/.gemini/oauth_creds.json exists; not read)"
else
    result PENDING "Gemini CLI login not done yet (guide, section 'Gemini CLI login')"
fi
section "8. start scripts: git pull --ff-only, stop on uncommitted changes, --branch outside the cell; push.sh"
relay < <(python3 -I "$ABSCHRIFT_JCODE_DIR/vm/abschrift/replica_check.py")
section "9. verifier finds the Playwright browsers in ~/.cache/ms-playwright-abschrift"
relay < <(browsers)
section "10. SSH server of the VM (Abschrift 1.4)"
relay < <(ssh_server)
section "11. git status clean in both clones"
relay < <(clean_clones)

section "Result: $fails FAIL, $pending PENDING"

# Never show a report that contains a key (patterns and values of this shell).
if ! python3 -I "$ABSCHRIFT_JCODE_DIR/tools/check_secrets.py" "$REPORT" >/dev/null 2>&1; then
    echo "STOP: the report matched the secret check; it is NOT shown. Paste nothing; ask in the MejoraDeAudio chat." >&2
    exit 1
fi
cat "$REPORT"
echo
echo "Report saved: $REPORT  (paste it into the Abschrift chat)"
[ "$fails" -eq 0 ]
