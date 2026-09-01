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

### What fixed it

Issue #69, specified and built in `specs/20260831-184927-guard-terminate-pid/`. Four guards, of
which the first three are separate on purpose — measurement showed that no one of them subsumes
the others:

1. **Impossible pids are refused on sight**: `0` and `1`, before any rung runs. `killpg(1, sig)`
   is `kill(-1, sig)`; `getpgid(0)` returns the *caller's* group, an ordinary number well above 1
   that a `pgid <= 1` test does not catch.
2. **Impossible process groups are refused**: anything resolving to `1` or lower, checked after
   the group is resolved, which is the only place that third route is visible.
3. **A pid with no recorded `proc_start` is not signalled**: it is a bare number, not an identity.
   This is what actually let the incident through — `procinfo.is_alive(1, None)` degrades to "does
   `/proc/1` exist", and answers `True`. It withholds the *signal* only; the recorded systemd scope
   names a unit rather than a process, carries none of this risk, and still runs.
4. **A session hosted by the simulated host is terminated by it** regardless of the configured
   effect level, which closes the one route to this code that needs no hand-edited database:
   dispatch at `local`, raise the level, cancel a row that still says `pid = 0`. The test is the
   full signature that host writes — `dry_run` *and* `pid = 0` *and* no `proc_start` — because
   `no-remote` rows are dry-run records with real processes behind them.

The guard sits at the boundary and again in the primitive that calls `os.killpg`, deliberately
redundantly. A refusal is a distinct outcome — `method="refused"` — that settles nothing, exits
non-zero, and records `signals_sent: 0`.

Worth recording separately: the suite already covered every case of the milestone-014 termination
contract and passed throughout. It never touched this bug because every one of those tests
replaced `_signal_group` with a stub, so the single function that actually delivers signals had no
test at all. The regression tests target it directly, and assert on an empty call list rather than
on an exception — proving the refusal branch is reachable is not the same as proving the signal is
unreachable.

**#67 is not fixed by this.** The scope rung's blast radius is unchanged: confirming that the
recorded target died still says nothing about what else died with it. This narrowed only what may
be signalled.

### Related issues

- **#67** — `cancel` can stop a systemd scope containing unrelated processes; the recorded scope
  is never verified to contain only this session. Same class of failure by the other rung.
- **#34** — the confirmed-termination work that added the post-rung checks these two cases evade.

### What was not affected

- Database: `pragma integrity_check` returns `ok`; the session row settled to `lost`.
- Git repositories and worktrees: no interrupted git operations were introduced by the kill.
- Remote state: nothing was pushed, commented, or deleted as part of this.
