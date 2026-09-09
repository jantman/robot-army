# Contract: what the health verdict is, and the exact words each surface says

**Feature**: [../spec.md](../spec.md) · **Surfaces**: `robot-army health`, `robot-army status`,
the web chrome, and the `--json` payload of the first two.

This fixes three things: how the verdict is derived from the two readings, the exact sentence
each verdict produces, and the machine-readable shape. It is a contract because the whole point
of the feature is that these surfaces cannot disagree, and they can only fail to disagree by
rendering one thing.

---

## The signature

```python
health.check(
    path: Path,
    *,
    max_age_seconds: float,
    now: datetime | None = None,
    lock: LockReading | None = None,   # new
) -> HealthReport
```

`lock` is the whole of the new input. **Omitting it is a supported, distinct case** — the check
then judges the heartbeat alone and produces exactly today's verdicts and exactly today's
sentences (FR-016). The judgement never probes; the caller observes and hands it in (R1).

The reading comes from one function:

```python
daemon.observe_lock(path: Path) -> LockReading
```

and `daemon.is_locked(path)` is `observe_lock(path).running` — the same probe underneath, so
there is one shared-lock implementation and one place it can be got wrong.

---

## The derivation

Read top to bottom; the first row that matches wins.

| # | heartbeat | lock | verdict | healthy |
|---|---|---|---|---|
| 1 | absent | `HELD` | `starting` | no |
| 2 | absent | anything else, or not supplied | `never_started` | no |
| 3 | unreadable — I/O error, bad JSON, no `ts`, unparseable `ts` | any | `unreadable` | no |
| 4 | readable, any age | `UNHELD` | `died` | **no** |
| 5 | readable, inside the threshold | anything else | `ok` | yes |
| 6 | past the threshold | `HELD`, `pid` == holder | `hung` | no |
| 7 | past the threshold | `HELD`, `pid` != holder or holder unreadable | `starting` | no |
| 8 | past the threshold | `UNKNOWN`, or not supplied | `stale` | no |

Three rows carry the whole of the fix and are worth reading twice:

- **Row 4 is the bug.** It is above the freshness test, so with the lock released the
  heartbeat's age does not enter into it. That is the entire ~180-second floor, removed.
- **Row 5 makes a fresh heartbeat `ok` whatever pid wrote it.** A restart within the threshold
  is the normal case and must not become an alarm (SC-008, R5).
- **Row 7 keeps `hung` honest.** A daemon that has just restarted holds the lock while the
  newest heartbeat on disk still belongs to the process it replaced; calling that a wedge would
  send someone to take a stack of a healthy starting process.

Row 3 sits above row 4 deliberately: a corrupt heartbeat is not evidence of a death, and FR-007
keeps it its own reason. What it says about the lock is carried in the sentence, not the verdict.

---

## The sentences

`{hb}` is the heartbeat path, `{lock}` the lock path, `{age}` whole seconds, `{max}` the
threshold in whole seconds. Unchanged sentences are marked; they are unchanged so that a
healthy run's output is byte-for-byte what it is today.

| verdict | reason |
|---|---|
| `ok` | `heartbeat is {age}s old (pid {pid}, {activity})` — **unchanged** |
| `never_started` | `no heartbeat file at {hb} — the daemon has never run` — **unchanged** |
| `unreadable` | today's four sentences, **unchanged**, with the lock clause appended when a reading was supplied |
| `stale` | `heartbeat is {age}s old, past the {max}s threshold (pid {pid}, last activity {activity!r})` — **unchanged** |
| `died` | `no process holds {lock} — the daemon is gone; its last heartbeat is {age}s old (pid {pid}, last activity {activity!r})` |
| `hung` | `pid {holder} still holds {lock} but its heartbeat is {age}s old, past the {max}s threshold — the daemon is wedged, not gone (last activity {activity!r})` |
| `starting`, no heartbeat | `a daemon holds {lock} but nothing has been written to {hb} yet — it is starting, or it stopped before its first beat` |
| `starting`, mismatched pid | `a daemon holds {lock} (pid {holder}) but has not beaten yet; the newest heartbeat is {age}s old and belongs to pid {pid}, which is not the holder` |

**The `UNKNOWN` clause** (FR-013). When the reading is `UNKNOWN`, the sentence is the
heartbeat-only sentence above plus:

```
 — {lock} could not be read, so this is the heartbeat's age alone
```

A verdict reached without the lock must never be mistaken for one reached with it. This is the
only place the check admits to judging on partial evidence, and it must say so whether it
concluded health or staleness.

**The lock clause on `unreadable`**, when a reading was supplied: ` (no process holds {lock})`,
` (a daemon holds {lock})`, or ` ({lock} could not be read)`. The reader learns both facts even
though only one of them names the verdict.

---

## The label, and what each surface prints

One label per verdict, defined once and rendered verbatim everywhere:

| verdict | label |
|---|---|
| `ok` | `ok` |
| all others | the value upper-cased, underscores as spaces: `DIED`, `HUNG`, `STARTING`, `NEVER STARTED`, `UNREADABLE`, `STALE` |

**`robot-army health`** — one line, `{label}: {reason}`:

```
ok: heartbeat is 41s old (pid 197630, idle)
DIED: no process holds /run/user/1000/robot-army/daemon.lock — the daemon is gone; its last heartbeat is 41s old (pid 197630, last activity 'idle')
HUNG: pid 197630 still holds /run/user/1000/robot-army/daemon.lock but its heartbeat is 400s old, past the 180s threshold — the daemon is wedged, not gone (last activity 'reconcile')
```

The healthy line is byte-for-byte today's. `STALE:` still exists and is still reachable — it is
what a run with an unreadable lock says.

**`robot-army status`** — the health line becomes `health       : {label} — {reason}`, replacing
today's `{'ok' if healthy else 'STALE'}`. This is the only line `status` prints about the
daemon, which is why it may not say `ok` beside a dead one.

**The web chrome** — the banner's `DAEMON NOT RUNNING` / `daemon running (pid N)` is derived
from `running` and is **unchanged**. What changes is the suffix on a running daemon: today
` — heartbeat STALE` unconditionally, now ` — {label}` , so a wedged daemon says `HUNG` and a
restarting one says `STARTING`. The chrome payload gains `state` next to the `healthy` and
`reason` it already publishes.

**Exit codes** — `0` for `ok`, `EXIT_CHECK_FAILED` (4) for every other verdict. No new codes
(R6).

---

## The machine-readable shape

`HealthReport.to_dict()`, which is `health --json`'s `data` and `status --json`'s `health` key:

```json
{
  "healthy": false,
  "state": "died",
  "lock": "unheld",
  "reason": "no process holds /run/user/1000/robot-army/daemon.lock — the daemon is gone; its last heartbeat is 41s old (pid 197630, last activity 'idle')",
  "age_seconds": 41.0,
  "heartbeat": { "...": "the parsed heartbeat, unchanged" }
}
```

- `state` is one of the seven values. A consumer names the state by reading this key and never
  by matching on `reason` (FR-009).
- `lock` is `held`, `unheld`, `unknown`, or `null` — `null` meaning no reading was supplied.
  Distinguishing `null` from `unheld` matters: one is "nobody looked", the other is "we looked
  and nothing is there".
- `healthy` remains, and remains `state == "ok"`.

**The alert** (FR-010). `alert_fields` keeps its title, `robot-army health check failed`, and its
message stays `report.reason` — so the sharper sentence reaches every configured channel with no
work. Its structured fields gain `state` beside the existing `healthy` and `age_seconds`, which
is also what lands in the `health.notify` audit record's `reason`.

---

## What this contract does not change

- `[health] max_age_seconds`: same key, same default of 180, same meaning. Nothing is lowered,
  and the threshold is still the only way `hung` is visible (FR-014).
- `heartbeat.json`: same shape, same writer, same fields.
- The timer: still every five minutes. The check answers correctly whenever it runs; when it
  runs is a separate question this feature does not touch.
- `daemon.read_lock_holder`, `daemon.is_locked`: same names, same return types, same answers.
  `is_locked` additionally stops creating the lock file it was asked about.
