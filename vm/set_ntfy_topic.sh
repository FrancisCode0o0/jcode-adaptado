#!/usr/bin/env bash
# Create a random ntfy.sh topic for phone notifications and store it in
# ~/.bashrc as NTFY_TOPIC. Run once; the phone app subscribes to the topic.
#
# Origin: plan step 1.0 (automation "phone notifications", 2026-10-20).
# The topic name works as a password on the public ntfy.sh server, so it is
# long and random (about 144 bits). It only ever carries job status lines
# (see notify_run.sh), so a leak would reveal "sweep OK", nothing more.
#
# Usage:  bash set_ntfy_topic.sh          # create (refuses if one exists)
#         bash set_ntfy_topic.sh --new    # replace the existing topic

set -euo pipefail

readonly BASHRC="${BASHRC:-$HOME/.bashrc}"
readonly TOPIC_PREFIX="ma-"         # "mejorador-audio", easy to recognise
readonly RANDOM_BYTES=18            # 18 bytes -> 24 URL-safe characters

if grep -q '^export NTFY_TOPIC=' "$BASHRC" 2>/dev/null; then
    if [ "${1:-}" != "--new" ]; then
        echo "A topic already exists in $BASHRC. Use --new to replace it."
        exit 1
    fi
    sed -i '/^export NTFY_TOPIC=/d' "$BASHRC"
fi

topic="$TOPIC_PREFIX$(head -c "$RANDOM_BYTES" /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n')"
echo "export NTFY_TOPIC=\"$topic\"" >> "$BASHRC"
export NTFY_TOPIC="$topic"

echo "Topic saved in $BASHRC:"
echo
echo "    $topic"
echo
echo "In the ntfy app on the phone: + (subscribe) -> paste this topic."
echo "Sending a test notification..."
script_dir=$(cd "$(dirname "$0")" && pwd)
bash "$script_dir/notify_run.sh" "test from the VM" true
echo "Open a NEW terminal so the variable is loaded there too."
