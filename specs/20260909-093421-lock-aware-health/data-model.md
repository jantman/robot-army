# Data Model: the evidence the switch reads and the verdict it reaches

**Feature**: [spec.md](spec.md) · **Research**: [research.md](research.md)

Nothing here is persisted. There is no database table, no new state file, and no change to the
shape of `heartbeat.json` — this feature reads two files that already exist and adds vocabulary
for what it found. `docs/guide/state.md` therefore needs no change.

---

## `LockState` — what was observed of the single-instance lock

A `StrEnum` in `src/robot_army/health.py`, three members.

| member | value | means | reached when |
|---|---|---|---|
| `HELD` | `held` | a process holds the lock | the shared probe could not be taken |
| `UNHELD` | `unheld` | no process holds the lock | the shared probe succeeded, or the lock file does not exist |
| `UNKNOWN` | `unknown` | the lock could not be observed | the file could not be opened for any reason other than its absence, or `flock` failed for a reason other than contention |

**It lives in `health.py`, not `daemon.py`, and the import direction forces that**: `daemon`
imports `health` (R1), so a type `health.check` must name cannot be defined in `daemon`. The
placement is not merely tolerable — this is the checker's vocabulary for evidence handed to it,
and `daemon` is one supplier of that evidence.

`UNKNOWN` is not a fourth "not consulted" value. A caller that consulted nothing passes no
reading at all; see the contract.

---

## `LockReading` — one observation of the lock, taken at one instant

A frozen slotted dataclass in `src/robot_army/health.py`.

| field | type | notes |
|---|---|---|
| `state` | `LockState` | as above |
| `path` | `Path` | the lock file that was observed |
| `holder` | `str | None` | the pid text read from the lock file, **only** when `state is HELD`; `None` otherwise |
| `running` | `bool` (property) | `state is LockState.HELD`. The bool every existing caller already wanted |

**Why the path travels with the reading**: two of the sentences the check now writes name the
lock file — "no process holds *this* — the daemon is gone", and "*this* could not be read, so
what follows is the heartbeat alone". The judgement is handed a reading, not a path (R1), so a
reading that did not know what it observed would force every caller to pass the path a second
time for the message alone.

**Why the holder is `None` unless held**: an unheld lock file still contains the last holder's
pid. Returning it would name a process that has exited. `daemon.read_lock_holder` keeps
returning that text for the caller that wants it — `operations` explaining a refusal
(`src/robot_army/operations.py:3915`) — which is a different question.

**Why holder and state travel together**: they are read from one descriptor in one call, so
they cannot come from either side of a daemon restart. Two call sites ask them a line apart
today.

Produced by exactly one function: `daemon.observe_lock(path) -> LockReading`.

---

## `HealthState` — the verdict

A `StrEnum` in `src/robot_army/health.py`, seven members. Full derivation and exact text in
[contracts/health-verdict.md](contracts/health-verdict.md).

| member | value | healthy | lock | heartbeat |
|---|---|---|---|---|
| `OK` | `ok` | yes | held, or not consulted | inside the threshold |
| `DIED` | `died` | no | unheld | readable, any age |
| `HUNG` | `hung` | no | held | past the threshold, written by the holder |
| `STARTING` | `starting` | no | held | absent, or past the threshold and written by another pid |
| `NEVER_STARTED` | `never_started` | no | unheld, or not consulted | absent |
| `UNREADABLE` | `unreadable` | no | any | present, not parseable |
| `STALE` | `stale` | no | unknown, or not consulted | past the threshold |

`healthy` is `state is OK` and nothing else. The invariant is one-way and worth stating: the
report's `healthy` flag is derived from the state, never the other way round.

Each member carries a **label** used verbatim by every surface that prints a verdict, so the
word a reader sees in `health`, in `status` and in the web chrome is the same word:

| state | label |
|---|---|
| `ok` | `ok` |
| everything else | the value, upper-cased, underscores as spaces — `DIED`, `HUNG`, `STARTING`, `NEVER STARTED`, `UNREADABLE`, `STALE` |

---

## `HealthReport` — changed

| field | change |
|---|---|
| `healthy` | unchanged; now equals `state is OK` by construction |
| `reason` | unchanged in type; the sentences change, per the contract |
| `state` | **new, required, third positional.** No default: `OK` would let a hand-built failing report claim health, and an eighth `unknown` member would exist only to be the value nobody meant |
| `lock` | **new**, `LockState | None`, keyword with a `None` default. What the report was judged against — `None` means no reading was supplied. Defaulted, unlike `state`, because absence is its honest and commonest value |
| `age_seconds` | unchanged |
| `heartbeat` | unchanged |

`to_dict()` gains `"state"` and `"lock"` alongside the existing four keys, the latter carrying
the observed `LockState` value or `null` when none was supplied — FR-009 asks for both the verdict
and what was seen of the lock, and a consumer that cannot tell "unheld" from "not consulted" is
back to guessing.

**No compatibility is owed** to the old shape: the only consumers are in this repository and the
constitution's Principle V rules the question out. Adding keys is additive anyway; the required
`state` argument is the breaking part, and it breaks loudly at the handful of places that
construct a report by hand.

---

## Relationships

```
daemon.observe_lock(lock_path) ──► LockReading ──┐
                                                 ├──► health.check(...) ──► HealthReport
heartbeat.json ──────────────────────────────────┘                              │
                                                                                ├─ state ──► the printed label, the JSON, the alert
                                                                                └─ healthy ─► the exit code
```

One arrow that is deliberately absent: there is none from `health` back to `daemon`. The
judgement never fetches its own evidence (R1).
