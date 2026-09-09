# Quickstart: Validating the Lock-Aware Dead-Man's Switch

**Feature**: `specs/20260909-093421-lock-aware-health/`

Everything here runs offline. A "daemon" is a process — or a thread — holding an `flock` on a
temporary path, and a "heartbeat" is a JSON file with a `ts` of the tester's choosing. Nothing
needs GitHub, a real daemon, systemd, or a network.

```bash
uv sync
uv run pytest                     # the whole suite must pass
```

---

## The one-line proof

The defect and the fix are the same question: with a heartbeat written **one second ago** and
no process holding the lock, what does the switch say?

```bash
uv run pytest tests/unit/test_health.py -q
```

Before this feature: `ok`, exit 0, and it keeps saying that until the heartbeat crosses 180
seconds. After it: `DIED`, exit 4, on the first run — and a heartbeat one second old under a
*held* lock still says `ok`, which is the half of the property that is easy to lose.

---

## Scenario 1 — A dead daemon, reported dead immediately (US1, FR-002)

**Setup**: a heartbeat file dated `now`; no lock held; the lock file present but free.

**Run**: `health.check(hb, max_age_seconds=180, lock=daemon.observe_lock(lock_path))`.

**Expect**: `state` is `died`, `healthy` is `False`, and the reason names the lock file and the
last heartbeat's age. Then repeat with the heartbeat dated 1s, 30s, 179s and 300s old and
confirm all four give the same verdict for the same reason (SC-002) — the age appears in the
sentence and nowhere in the decision.

Through the command:

```bash
uv run robot-army health ; echo "exit $?"
```

with no daemon running is the end-to-end form, and the exit code is 4.

## Scenario 2 — A running daemon is untouched (US1, FR-005, SC-004)

**Setup**: a thread or subprocess holding `LOCK_EX` on the lock path; a heartbeat dated `now`.

**Expect**: `state` is `ok`, `healthy` is `True`, and the printed line is **byte-for-byte** the
line printed today — `ok: heartbeat is 0s old (pid N, idle)`. This is the regression that
matters most: the switch must not learn to cry wolf.

## Scenario 3 — Wedged, not dead (US2, FR-003)

**Setup**: the lock held by pid *P*; a heartbeat 400 seconds old whose `pid` is *P*.

**Expect**: `state` is `hung`, exit 4, and the sentence says a process still holds the lock and
has stopped beating. This is the case the heartbeat threshold exists for, and it must behave
exactly as it does today apart from the word (SC-003).

## Scenario 4 — A restart is not a wedge (US2, FR-004, SC-008)

Two setups, one property.

| Setup | Expect |
|---|---|
| lock held by pid *P*, heartbeat 400s old written by pid *Q* | `starting`, **not** `hung` — no instruction to go and take a stack of a healthy process |
| lock held by pid *P*, heartbeat **1s** old written by pid *Q* | `ok` — an ordinary restart raises no new alarm |
| lock held, no heartbeat file at all | `starting`, exit 4 as it is today |

The mismatch is not hypothetical: `run_daemon` takes the lock, then wires boundaries and runs
`startup` before its first beat, and nothing unlinks the previous daemon's heartbeat
(research.md R5).

## Scenario 5 — The lock cannot be read (FR-013)

**Setup**: a lock path whose `os.open` refuses — patched, not `chmod 000`, because a test
that silently passes as root is checking nothing. A directory in its place does **not** work:
Linux opens and `flock`s a directory happily and the reading comes back `UNHELD`.

**Expect**: `observe_lock` returns `UNKNOWN`; the check falls back to the heartbeat alone, so a
fresh heartbeat is `ok` and a stale one is `stale` — and **both** sentences end with the clause
saying the lock could not be read. A permission error must never manufacture a death notice.

## Scenario 6 — The lock file is absent, and stays absent (FR-012)

**Setup**: a lock path that does not exist.

**Expect**: `observe_lock` returns `UNHELD`, the verdict is `died` (or `never_started` with no
heartbeat), and **the path still does not exist afterwards**. Assert the last part explicitly:
the probe used to create the file it was asked about, and the switch has no business writing
into the state directory.

## Scenario 7 — Concurrent probes stay shared (FR-011, SC-004)

The existing test at `tests/integration/test_single_instance.py` drives six threads through
2,400 probes with no daemon running and asserts zero false positives. It now exercises the one
probe underneath both `is_locked` and `observe_lock`, and must still pass unchanged.

## Scenario 8 — No two surfaces disagree (US3, FR-015, SC-007)

**Setup**: a heartbeat dated `now`; no lock held. One machine, one instant.

**Expect**, all three:

| Surface | Says |
|---|---|
| `robot-army health` | `DIED: …`, exit 4 |
| `robot-army status` | `health       : DIED — …` |
| a web page | `DAEMON NOT RUNNING` in the banner, `"state": "died"` in the chrome payload |

This is the incident from the issue, reproduced deliberately: at `t + 1s` the web said
`DAEMON NOT RUNNING` and the terminal said `ok`. After this change no combination of these
three surfaces can be made to disagree.

---

## Manual end-to-end (optional, the scenario the issue was found in)

Quickstart scenario 7 of the issue #1 verification round, in short form:

```bash
uv run robot-army daemon &          # or via the systemd unit
sleep 10
kill -9 %1                          # a genuinely dead daemon
uv run robot-army health ; echo "exit $?"
```

The check must report `DIED` and exit 4 **immediately**, not after 180 seconds. Then:

```bash
uv run robot-army status | head -3
```

must agree with it on the same line it prints about the daemon.
