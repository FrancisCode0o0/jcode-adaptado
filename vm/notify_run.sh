#!/usr/bin/env bash
# Run a command and send its outcome to the phone through ntfy.sh.
#
# Origin: plan step 1.0 (automation "phone notifications", 2026-10-20).
# Why: long jobs (sweeps, browser batteries, Kaggle) finish while Fran is at
# work; without a notification a finished job can sit unnoticed for hours.
#
# Usage:
#     notify_run.sh "<job name>" <command> [arguments...]
#     notify_run.sh "F3 sweep" python3 scripts/sweep.py --config sweep.json
#
# Privacy rules (ntfy.sh is a public server; the topic name is the password):
#   - Only the job name, OK/FAILED, exit code and duration are sent.
#     Never output, paths, file names or data.
#   - The job name is cut to 60 characters and stripped to [A-Za-z0-9 ._-].
# Robustness: a failed notification never changes the job's result; the
# script always exits with the command's own exit code.
#
# Settings (environment variables, set by set_ntfy_topic.sh):
#   NTFY_TOPIC   required to notify; if empty the job still runs, silently
#   NTFY_SERVER  default https://ntfy.sh (overridable for tests)

set -u

readonly NOTIFY_TIMEOUT_S=10      # never let a slow server delay the shell
readonly MAX_JOB_NAME_CHARS=60

if [ "$#" -lt 2 ]; then
    echo "usage: notify_run.sh \"<job name>\" <command> [arguments...]" >&2
    exit 2
fi

job_name=$(printf '%s' "$1" | tr -cd 'A-Za-z0-9 ._-' | cut -c1-"$MAX_JOB_NAME_CHARS")
[ -n "$job_name" ] || job_name="job"
shift

format_duration() {
    local total_s=$1
    printf '%dh %02dm %02ds' $((total_s / 3600)) $((total_s % 3600 / 60)) $((total_s % 60))
}

send_notification() {
    local title=$1 message=$2 priority=$3 tags=$4
    if [ -z "${NTFY_TOPIC:-}" ]; then
        echo "notify_run: NTFY_TOPIC not set; no notification sent." >&2
        return 0
    fi
    curl --silent --show-error --max-time "$NOTIFY_TIMEOUT_S" \
        -H "Title: $title" -H "Priority: $priority" -H "Tags: $tags" \
        -d "$message" "${NTFY_SERVER:-https://ntfy.sh}/$NTFY_TOPIC" >/dev/null \
        || echo "notify_run: notification could not be sent (job result unaffected)." >&2
}

# Ctrl+C reaches the child too; remember it so the message says "interrupted".
interrupted=0
trap 'interrupted=1' INT TERM

start_s=$(date +%s)
"$@"
exit_code=$?
duration=$(format_duration $(( $(date +%s) - start_s )))

if [ "$interrupted" -eq 1 ]; then
    send_notification "$job_name: interrupted" "Stopped by hand after $duration." default warning
elif [ "$exit_code" -eq 0 ]; then
    send_notification "$job_name: OK" "Finished in $duration." default white_check_mark
else
    send_notification "$job_name: FAILED" \
        "Exit code $exit_code after $duration. Check the report on the VM." high x
fi
exit "$exit_code"
