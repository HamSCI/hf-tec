#!/bin/bash
# qa-snapshot.sh — periodic snapshot of `hf-tec qa --json`.
#
# Appends one compact-JSON line per invocation to
#   /var/lib/hf-tec/<instance>/qa/YYYY-MM-DD.jsonl
# and prints a one-line summary to stdout (which systemd captures
# into journald via the templated hf-tec-qa@<instance>.service).
#
# Designed for the systemd timer `hf-tec-qa@<instance>.timer`
# at OnCalendar=*:00/15.  Safe to run manually for debugging:
#
#   sudo -u hftec scripts/qa-snapshot.sh AC0G-B1
#
# Exit code:
#   0 — snapshot written (regardless of NULL/WEAK/SIGNAL verdict).
#   1 — invocation error (no instance, missing binary, write failure).
#
# We deliberately return 0 even when qa itself reports an
# `error` field (e.g. no records in window) so the systemd timer
# does not enter a failure-rate-limit loop on operationally-quiet
# windows — the error is preserved in the JSONL row for forensics.

set -euo pipefail

INSTANCE="${1:?usage: qa-snapshot.sh <reporter_id>}"
DATA_ROOT="${HF_TEC_DATA_ROOT:-/var/lib/hf-tec}"
HF_TEC_BIN="${HF_TEC_BIN:-/usr/local/bin/hf-tec}"

QA_DIR="${DATA_ROOT}/${INSTANCE}/qa"
TODAY="$(date -u +%Y-%m-%d)"
OUT="${QA_DIR}/${TODAY}.jsonl"

mkdir -p "${QA_DIR}"

# Capture pretty JSON; if the qa subcommand itself fails, build a
# synthetic error row so the JSONL stream remains continuous.
if json="$("${HF_TEC_BIN}" qa --since 1h --instance "${INSTANCE}" --json 2>&1)"; then
    :
else
    json="$(jq -n --arg msg "$json" --arg inst "${INSTANCE}" \
        '{instance: $inst, error: ("invocation failure: " + $msg)}')"
fi

# Compact to one line per snapshot for true JSONL.  Fall back to
# writing whatever we got if jq can't parse it (corrupt stderr in
# stdout, etc.) — better to keep a malformed row than to drop it.
compact="$(printf '%s' "${json}" | jq -c '.' 2>/dev/null || printf '%s' "${json}")"
printf '%s\n' "${compact}" >> "${OUT}"

# One-line summary to stdout.  Pulled out of the JSON itself so the
# journald row tracks whatever qa thinks is interesting.
printf '%s' "${compact}" | jq -r '
  if .error then
    "ERROR instance=\(.instance // "?") msg=\(.error)"
  else
    "instance=\(.instance) records=\(.n_records) " +
    "signal=\(.verdict_summary.signal) weak=\(.verdict_summary.weak) null=\(.verdict_summary.null) " +
    "palmer/min=\(.palmer_rate_per_min)"
  end
' 2>/dev/null || printf 'qa-snapshot: unparseable output for %s\n' "${INSTANCE}"
