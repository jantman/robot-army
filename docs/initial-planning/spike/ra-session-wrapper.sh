#!/usr/bin/env bash
# ra-session-wrapper.sh — the §9 session wrapper, M0 spike edition.
#
# Sits between the session host (dtach) and the AI worker (claude). It exists
# because claude is not the daemon's child — kitty forks it — so the daemon
# cannot waitpid() on it. This wrapper CAN, and pushes the exit code back.
#
# In M0 there is no daemon API yet, so "reporting" means appending a JSON line
# to a state file. In M1 that becomes an HTTP POST; the record shape is the
# same, which is the point of writing it now.
#
# Usage:
#   ra-session-wrapper.sh <item-id> -- CMD [ARGS...]
#
# Environment:
#   RA_STATE_DIR   default ~/.local/state/robot-army-spike
#   RA_DRY_RUN     if set to 1, do NOT exec the real command. Run a stand-in
#                  that behaves like an interactive session instead. See below.
#   RA_API         if set (e.g. http://localhost:8080/sessions/exit), also POST
#                  the exit record there with curl. Exercises the real M1 path.
#
# ---------------------------------------------------------------------------
# Why dry-run matters HERE, not just in M1:
#
# Phase 2 of the spike tests a launch chain with four moving parts —
#   kitty @ launch -> dtach -> wrapper -> claude
# If we test that chain with a real claude session, then every failure is
# ambiguous (was it the chain, or was it claude?), every run costs subscription
# usage, and the kill tests (E2.5/E2.6) throw away real sessions.
#
# With RA_DRY_RUN=1 the wrapper runs a stand-in that is interactive, holds a
# PTY, and exits with a controllable code. That validates the plumbing in
# isolation. Only once the chain is proven do we swap in the real claude.
#
# Stand-in commands (type at its prompt):
#   exit   -> exit 0    (models `/exit` via Remote Control, the §7 discriminator)
#   fail   -> exit 42   (models a non-zero exit, for E3.3)
#   hang   -> sleep forever (so you can kill -9 it from outside, for E4.2)
#   <else> -> echoed back, session continues
# ---------------------------------------------------------------------------

set -uo pipefail

ITEM_ID="${1:?usage: ra-session-wrapper.sh <item-id> -- CMD [ARGS...]}"
shift
[[ "${1:-}" == "--" ]] && shift

if [[ $# -eq 0 && "${RA_DRY_RUN:-0}" != "1" ]]; then
  echo "ra-session-wrapper: no command given" >&2
  exit 2
fi

RA_STATE_DIR="${RA_STATE_DIR:-$HOME/.local/state/robot-army-spike}"
REPORTS="$RA_STATE_DIR/reports"
LOGS="$RA_STATE_DIR/logs"
mkdir -p "$REPORTS" "$LOGS"

EXITS="$REPORTS/exits.jsonl"
LOGFILE="$LOGS/$ITEM_ID.log"

# --- JSON helpers (no jq dependency; the wrapper must run in a bare env) -----
jesc() {
  local s=$1
  s=${s//\\/\\\\}; s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}; s=${s//$'\r'/\\r}; s=${s//$'\t'/\\t}
  printf '%s' "$s"
}
jarr() {
  local out="" a
  for a in "$@"; do out+="\"$(jesc "$a")\","; done
  printf '[%s]' "${out%,}"
}
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# --- Recover the session ID from argv ---------------------------------------
# `claude --session-id <uuid>` exists (confirmed, 2.1.239), so the orchestrator
# GENERATES the id and passes it in — it is known before the process starts.
# We echo it back into the record so the report is self-describing.
SESSION_ID=""
prev=""
for a in "$@"; do
  [[ "$prev" == "--session-id" ]] && SESSION_ID="$a"
  [[ "$a" == --session-id=* ]] && SESSION_ID="${a#--session-id=}"
  prev="$a"
done

DRY="${RA_DRY_RUN:-0}"
STARTED="$(now)"

ARGV_JSON="$(jarr "$@")"
emit() {  # emit <event> [extra json]
  local event=$1; local extra="${2:-}"
  printf '{"event":"%s","item":"%s","ts":"%s","pid":%s,"ppid":%s,"cwd":"%s","session_id":"%s","dry_run":%s,"argv":%s%s}\n' \
    "$event" "$(jesc "$ITEM_ID")" "$(now)" "$$" "$PPID" \
    "$(jesc "$PWD")" "$(jesc "$SESSION_ID")" \
    "$([[ $DRY == 1 ]] && echo true || echo false)" \
    "$ARGV_JSON" \
    "${extra:+,$extra}" >> "$EXITS"
}

emit start "\"started\":\"$STARTED\""
{
  echo "=== ra-session-wrapper: item=$ITEM_ID started=$STARTED dry_run=$DRY"
  echo "=== cwd: $PWD"
  echo "=== session_id: ${SESSION_ID:-<none>}"
  echo "=== argv: $*"
} >> "$LOGFILE"

# --- Run the payload --------------------------------------------------------
# NOTE: deliberately NOT using `exec`. §9 suggests exec to avoid an extra
# process layer, but exec replaces this shell and we would never capture $?.
# Exit reporting is the wrapper's entire reason to exist, so the extra process
# stays. (Documented so nobody "optimizes" it later.)

if [[ "$DRY" == "1" ]]; then
  echo "--- DRY RUN: not launching the real worker ---"
  echo "--- would have run: $* ---"
  echo "--- type: exit | fail | hang | <anything else> ---"
  rc=0
  while true; do
    printf 'dry-run[%s]> ' "$ITEM_ID"
    if ! read -r line; then
      echo "(eof)"
      rc=0
      break
    fi
    case "$line" in
      exit) rc=0;  break ;;
      fail) rc=42; break ;;
      hang) echo "(sleeping forever; kill me from outside)"; sleep infinity ;;
      *)    echo "you said: $line" ;;
    esac
  done
else
  "$@"
  rc=$?
fi

ENDED="$(now)"

# --- Decode signal deaths ---------------------------------------------------
# bash reports signal-terminated children as 128+N. E3.3 needs to distinguish
# "crashed" from "a human killed it", so record both the raw code and the signal.
SIGNAL="null"
if (( rc > 128 && rc < 192 )); then
  SIGNAL="$(( rc - 128 ))"
fi

emit exit "\"started\":\"$STARTED\",\"ended\":\"$ENDED\",\"exit\":$rc,\"signal\":$SIGNAL"
echo "=== ra-session-wrapper: exit=$rc signal=$SIGNAL ended=$ENDED" >> "$LOGFILE"

# --- Push to the daemon API if one exists (M1 path, optional in M0) ---------
if [[ -n "${RA_API:-}" ]]; then
  curl -fsS -m 5 -X POST "$RA_API" \
    -H 'Content-Type: application/json' \
    -d "{\"item\":\"$(jesc "$ITEM_ID")\",\"session_id\":\"$(jesc "$SESSION_ID")\",\"exit\":$rc,\"signal\":$SIGNAL,\"ended\":\"$ENDED\"}" \
    >/dev/null 2>&1 \
    || echo "ra-session-wrapper: WARNING: failed to POST to $RA_API" >&2
fi

exit "$rc"
