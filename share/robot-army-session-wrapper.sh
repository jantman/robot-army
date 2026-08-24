#!/usr/bin/env bash
# robot-army-session-wrapper — sits between the session host (dtach) and the worker.
#
# It exists because the worker is not the daemon's child: kitty forks it, so the daemon
# cannot waitpid() on it. This wrapper CAN, and reports the exit status by writing an
# atomic spool file the daemon drains.
#
# Seeded from docs/initial-planning/spike/ra-session-wrapper.sh. Two properties are
# load-bearing and must survive future editing:
#
#   1. It must NOT `exec` the worker (see the note at the call site below).
#   2. It must run in a bare launch environment with no virtualenv and a minimal PATH,
#      because it runs in whatever environment the terminal daemon happens to provide
#      (M0 F19). It may use only bash builtins plus printf, date, mv and mkdir. No jq,
#      no curl, no Python.
#
# The spike POSTed to a daemon API. This does not: a POST to a daemon that is down loses
# the record permanently, and the daemon is legitimately down during restarts and
# upgrades (research.md R5). A file survives that, and survives a reboot.
#
# Usage:
#   robot-army-session-wrapper <item-id> -- CMD [ARGS...]
#
# Environment (all supplied by the daemon via kitty's --env):
#   ROBOT_ARMY_SPOOL_DIR   where to write exit records
#   ROBOT_ARMY_LOG_DIR     where to write this session's human-readable log
#   ROBOT_ARMY_SESSION_ID  the session id, if it is not discoverable from argv

set -uo pipefail

SCHEMA=1

ITEM_ID="${1:?usage: robot-army-session-wrapper <item-id> -- CMD [ARGS...]}"
shift
[[ "${1:-}" == "--" ]] && shift

if [[ $# -eq 0 ]]; then
  echo "robot-army-session-wrapper: no command given" >&2
  exit 2
fi

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/robot-army"
SPOOL_DIR="${ROBOT_ARMY_SPOOL_DIR:-$STATE_DIR/spool/exits}"
LOG_DIR="${ROBOT_ARMY_LOG_DIR:-$STATE_DIR/logs/sessions}"
mkdir -p "$SPOOL_DIR" "$LOG_DIR" || {
  echo "robot-army-session-wrapper: cannot create $SPOOL_DIR" >&2
  exit 2
}
LOGFILE="$LOG_DIR/$ITEM_ID.log"

# --- JSON helpers -----------------------------------------------------------
# No jq: the wrapper must run in a bare environment (M0 F19).
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

# --- Recover the session id from argv ---------------------------------------
# The daemon GENERATES the id and passes it via --session-id, so it is known before the
# process starts (FR-020). Echoing it back makes the record self-describing, and it is
# the join key the daemon uses on the way in.
SESSION_ID="${ROBOT_ARMY_SESSION_ID:-}"
prev=""
for a in "$@"; do
  [[ "$prev" == "--session-id" ]] && SESSION_ID="$a"
  [[ "$a" == --session-id=* ]] && SESSION_ID="${a#--session-id=}"
  prev="$a"
done

if [[ -z "$SESSION_ID" ]]; then
  echo "robot-army-session-wrapper: no --session-id in argv and none in the environment" >&2
  exit 2
fi

STARTED="$(now)"
ARGV_JSON="$(jarr "$@")"

# --- Atomic emit ------------------------------------------------------------
# write to .tmp, then rename. rename within a directory is atomic on Linux, so the
# daemon never observes a partial record. One file per event; never append to a shared
# file, which would interleave between concurrent sessions.
emit() {  # emit <event> <extra-json>
  local event=$1 extra=${2:-}
  local final="$SPOOL_DIR/$SESSION_ID.$event.json"
  local tmp="$final.tmp"
  printf '{"schema":%s,"event":"%s","item":"%s","session_id":"%s","ts":"%s","pid":%s,"ppid":%s,"cwd":"%s","argv":%s%s}\n' \
    "$SCHEMA" "$event" "$(jesc "$ITEM_ID")" "$(jesc "$SESSION_ID")" "$(now)" \
    "$$" "$PPID" "$(jesc "$PWD")" "$ARGV_JSON" "${extra:+,$extra}" > "$tmp" || return 1
  # Best-effort fsync. `sync` is not in the guaranteed toolset, so its absence must not
  # break the wrapper — the rename still gives atomicity, only durability across a power
  # cut is weakened, and the daemon's own reconciliation covers that case.
  command -v sync >/dev/null 2>&1 && sync "$tmp" 2>/dev/null
  mv -f "$tmp" "$final"
}

emit start "\"started\":\"$STARTED\""
{
  echo "=== robot-army-session-wrapper: item=$ITEM_ID started=$STARTED"
  echo "=== cwd: $PWD"
  echo "=== session_id: $SESSION_ID"
  echo "=== argv: $*"
} >> "$LOGFILE"

# --- Run the payload --------------------------------------------------------
# NOTE: deliberately NOT using `exec`. Planning §9 suggests exec to avoid an extra
# process layer, but exec replaces this shell and we would never capture $?. Exit
# reporting is the wrapper's entire reason to exist, so the extra process stays.
# (Documented so nobody "optimizes" it later.)
"$@"
rc=$?

ENDED="$(now)"

# --- Decode signal deaths ---------------------------------------------------
# bash reports signal-terminated children as 128+N. FR-032 needs to distinguish
# "crashed" from "a human killed it", and both arrive as a single number — so record the
# decoded signal here, at the point where the information is unambiguous, rather than
# leaving the daemon to guess whether 137 meant SIGKILL or a program that returned 137.
SIGNAL="null"
if (( rc > 128 && rc < 192 )); then
  SIGNAL="$(( rc - 128 ))"
fi

emit exit "\"started\":\"$STARTED\",\"ended\":\"$ENDED\",\"exit\":$rc,\"signal\":$SIGNAL"
echo "=== robot-army-session-wrapper: exit=$rc signal=$SIGNAL ended=$ENDED" >> "$LOGFILE"

exit "$rc"
