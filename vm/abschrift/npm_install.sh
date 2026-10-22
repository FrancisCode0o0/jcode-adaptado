#!/usr/bin/env bash
# Install the npm packages of an Abschrift clone, and the Playwright browsers
# its package.json asks for - inside the INSTALL cell.
#
#     bash ~/jcode-adaptado/vm/abschrift/npm_install.sh gemini|verifier
#
# Origin: jcode 0.10.0-ma.3 (mejorador-audio plan step 1.0.b1); Abschrift
# plan v12: "npm install, Playwright-Browser und andere Installationen
# startet Fran außerhalb der Zellen". Fran starts it, outside the agents'
# cells - but npm runs the install scripts of every package, and
# package.json is written by Gemini CLI. So npm runs in a cell of its own
# (examples/abschrift/install-cell.json): it writes only the clone, the npm
# cache and ~/.cache/ms-playwright-abschrift; no key, no ~/.bashrc, no other
# project, our Node read-only. Our own browsers (~/.cache/ms-playwright) and
# venv-audio are never touched.
# System libraries for the browsers came with block D (setup_vm.sh). Never
# run 'sudo npx playwright install-deps' from a clone: that would run the
# agent's package as root.

set -Eeuo pipefail
# shellcheck source=vm/abschrift/paths.sh
source "$(dirname "${BASH_SOURCE[0]}")/paths.sh"

die() { echo "STOP: $*" >&2; exit 1; }
case "${1:-}" in
    gemini) clone=$ABSCHRIFT_GEMINI_CLONE ;;
    verifier) clone=$ABSCHRIFT_VERIFIER_CLONE ;;
    *) echo "usage: bash vm/abschrift/npm_install.sh gemini|verifier" >&2; exit 2 ;;
esac
[ -f "$clone/package.json" ] || die "$clone has no package.json yet (Abschrift phase 1)"
mkdir -p "$NPM_CACHE_ABSCHRIFT" "$PLAYWRIGHT_ABSCHRIFT_DIR"

in_install_cell() {
    python3 -I "$CELL_TOOL" run --settings "$ABSCHRIFT_PROFILES/install-cell.json" \
        --workdir "$clone" -- "$@"
}

if [ -f "$clone/package-lock.json" ]; then
    in_install_cell npm ci
else
    in_install_cell npm install
fi
if [ -d "$clone/node_modules/@playwright/test" ]; then
    in_install_cell npx playwright install chromium firefox webkit
fi
echo "npm packages installed in $clone (inside the install cell)."
