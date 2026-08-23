#!/usr/bin/env bash
# as-daemon.sh — run a command the way the robot-army systemd user service will.
#
# M0 spike tool. The point is fidelity: a test that passes from an interactive
# kitty prompt but fails from systemd is a test that lied to us. This runs the
# command as a real transient systemd user unit — no controlling TTY, no shell
# rc, only what the user manager's environment provides.
#
# Usage:
#   as-daemon.sh [--scrub] [--name NAME] [--keep] -- CMD [ARGS...]
#
#   --scrub   Additionally strip the environment down to a minimal set.
#             NOTE: this is NOT what the daemon will see. KDE Plasma imports a
#             full environment into the systemd user manager (WAYLAND_DISPLAY,
#             DISPLAY, DBUS_SESSION_BUS_ADDRESS, ...), so the *un-scrubbed* run
#             is the realistic baseline. Use --scrub as a dependency probe: what
#             does the launch chain minimally require? That answers what happens
#             when the daemon starts with no graphical session (see plan F3).
#   --name    Unit name (default: ra-spike-<pid>). Shows up in systemctl --user.
#   --keep    Don't pass --collect, so the unit lingers after exit and can be
#             inspected with `systemctl --user status <name>`.
#
# Exit code is the command's exit code (via --pipe --wait).

set -uo pipefail

SCRUB=0
KEEP=0
NAME="ra-spike-$$"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scrub) SCRUB=1; shift ;;
    --keep)  KEEP=1; shift ;;
    --name)  NAME="${2:?--name needs a value}"; shift 2 ;;
    --)      shift; break ;;
    -h|--help)
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) break ;;
  esac
done

if [[ $# -eq 0 ]]; then
  echo "as-daemon.sh: no command given" >&2
  echo "usage: $0 [--scrub] [--name NAME] [--keep] -- CMD [ARGS...]" >&2
  exit 2
fi

if [[ $SCRUB -eq 1 ]]; then
  set -- env -i \
    HOME="$HOME" \
    PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin" \
    XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" \
    USER="${USER:-$(id -un)}" \
    LOGNAME="${LOGNAME:-$(id -un)}" \
    LANG="${LANG:-en_US.UTF-8}" \
    "$@"
fi

COLLECT=(--collect)
[[ $KEEP -eq 1 ]] && COLLECT=()

# Deliberately NOT setting KillMode / Delegate / any property here. E4.1 asks
# whether a kitty-launched grandchild lands in this unit's cgroup (in which case
# stopping the daemon kills live sessions, regardless of the §9 "kitty is the
# parent" argument, because systemd kills by cgroup not by parentage). Overriding
# the defaults would hide that answer.
echo "as-daemon: unit=$NAME scrub=$SCRUB" >&2
echo "as-daemon: cmd: $*" >&2

systemd-run --user --pipe --wait "${COLLECT[@]}" \
  --service-type=exec \
  --unit="$NAME" \
  --description="robot-army M0 spike: $NAME" \
  -- "$@"
rc=$?

echo "as-daemon: exit=$rc" >&2
exit "$rc"
