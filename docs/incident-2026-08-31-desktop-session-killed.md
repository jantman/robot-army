# Incident: `robot-army cancel` killed an entire desktop session

**2026-08-31, 06:32 EDT · phoenix · every running application lost · no data corruption**

Testing milestone 014's "a stop that cannot succeed" scenario, we needed a session whose recorded
pid couldn't be signalled. We set the pid to `1` by hand, on the reasoning that signalling
root-owned pid 1 would harmlessly fail. It didn't. `robot-army cancel --force` sent `SIGTERM` to
every process the user owns, waited ten seconds, then `SIGKILL`. The Plasma session died, the
machine dropped to the login manager, and every open application was lost — including
robot-army's own daemon, killed by its own command one second in.

The cause is one line. `_signal_group` (`boundaries/dtach.py`) calls
`os.killpg(os.getpgid(pid), sig)`. For pid 1 that becomes `kill(-1, sig)`, which in POSIX means
*signal every process the caller is allowed to* — not "signal group 1". The daemon runs as the
same user as the desktop, so nothing stood in the way.

The trigger was a hand-edited row, but the exposure is real: `_signal_group` never validates the
pid. Any session row holding pid 1 does this, and #34's confirmation would still call the stop
"confirmed", since the recorded pid is certainly gone afterwards. **Fix: refuse to signal process
group 1, and check the pid against the row's recorded `proc_start` before signalling.** Nothing
was corrupted — the database passes `integrity_check` and the services restarted cleanly.

---

## Appendix

### Timeline

| Time (EDT) | Event |
|---|---|
| 06:32:09 | `session.terminate [pending] {"scope": null, "pid": 1, "proc_start": null}` |
| 06:32:09 | `daemon.signal [ok] {"signal": "SIGTERM"}` — the daemon receives its own signal |
| 06:32:09 | `web.stop`, `daemon.stop` — both services down |
| +10s | `SIGKILL` to all surviving processes |
| — | Plasma session ends; login manager restarts; new empty session |

### The code path

`src/robot_army/boundaries/dtach.py`:

```python
def _signal_group(pid: int, outcome: dict[str, object]) -> None:
    pgid = os.getpgid(pid)            # getpgid(1) -> 1
    os.killpg(pgid, signal.SIGTERM)   # killpg(1, sig) == kill(-1, sig)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            outcome["signal"] = "SIGTERM"
            return
        time.sleep(0.25)
    os.killpg(pgid, signal.SIGKILL)   # kill(-1, SIGKILL)
```

There is no validation of `pid` anywhere on this path. The scope rung above it was skipped
because the row's `scope` was `NULL`, so the process-group rung ran unguarded.

### Why the confirmation did not help

Milestone 014 added a check after every termination rung, but the check asks only whether *the
recorded pid* is gone. After `kill(-1, SIGKILL)` it certainly is, so the outcome would be
recorded as `confirmed: true`. Collateral damage is invisible to that check — the same blind spot
described in issue #67, where a too-broad systemd scope produces a "confirmed" stop that also
kills unrelated processes.

### Related issues

- **#67** — `cancel` can stop a systemd scope containing unrelated processes; the recorded scope
  is never verified to contain only this session. Same class of failure by the other rung.
- **#34** — the confirmed-termination work that added the post-rung checks these two cases evade.

### What was not affected

- Database: `pragma integrity_check` returns `ok`; the session row settled to `lost`.
- Git repositories and worktrees: no interrupted git operations were introduced by the kill.
- Remote state: nothing was pushed, commented, or deleted as part of this.
