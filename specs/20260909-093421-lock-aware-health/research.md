# Research: the dead-man's switch reads the lock as well as the heartbeat

**Feature**: [spec.md](spec.md) · **Baseline**: `main` at 64e8ebb

Every decision below was taken against the tree, not from memory. Where a claim about existing
code appears, the file and line it was read at appear with it.

---

## R1 — The lock reading is handed to the judgement; the judgement does not go and take one

**Decision**: `health.check` gains an optional parameter carrying what the caller observed of
the lock. It never probes the lock itself.

**Rationale**: Mechanical, then structural.

The mechanical half: `daemon.py` imports `health` (`src/robot_army/daemon.py:37`), so `health`
importing `daemon` is an import cycle. This is not a new discovery — `health.published_cap`
already exists in exactly this shape and its docstring already records exactly this reason:
"``daemon`` imports this module, so importing it back would be a cycle".

The structural half is the better argument and is also already written down in that docstring:
"every caller has already probed the lock for the effect level, so taking a second one would let
the two halves of one page answer differently across a daemon starting mid-request." `web.handle`
(`src/robot_army/web/server.py:1563`) goes to explicit trouble to take **one** reading of the
daemon per request and hand it down, precisely so that the chrome, the effect level and the
session cap cannot describe different instants. A `check` that probed for itself would punch a
hole in that on the very page the guarantee was written for.

**Alternatives considered**:

- *`health` imports `daemon` lazily inside the function.* Defeats the cycle, not the second
  probe. Rejected on the structural half.
- *Move the lock primitives into a third module both can import.* Solves a problem nobody has
  at the cost of a module, and leaves the second-probe problem untouched. Speculative
  generality under Principle I.

---

## R2 — The lock reading is three-valued, and *unknown* is a state we now have to have

**Decision**: `HELD`, `UNHELD`, `UNKNOWN`. A caller that supplies no reading at all is a fourth
case, expressed as the parameter's absence, and behaves exactly as today.

**Rationale**: `is_locked` (`src/robot_army/daemon.py:129`) returns a bool and collapses "I
could not probe" into `False` — `except OSError: return False` around the open. That collapse
is harmless for every caller it has today: a lock-aware command decides to act directly, or a
page renders `DAEMON NOT RUNNING`, and being wrong is a nuisance.

This feature changes what that `False` *means*. It becomes "the daemon has died", which exits
non-zero and pages the operator. A permission error or an `EIO` must not manufacture a death
notice. That is a concrete present need for the third value, not an anticipated one, so it
clears Principle I.

*Unknown* is distinct from *not consulted* because the output differs: with no reading supplied
the check must be exactly as it is today (FR-016), and with a reading that failed it must judge
the heartbeat alone **and say that the lock could not be read** (FR-013). Two different
sentences, so two different inputs.

**Alternatives considered**:

- *`running: bool | None` where `None` means unknown.* Three values, but then "not consulted"
  and "could not read" collide and FR-013 and FR-016 cannot both be satisfied.
- *Raise on a failed probe.* Turns a diagnostic into an outage: the dead-man's switch would
  crash instead of reporting on the heartbeat it can perfectly well read.

---

## R3 — One probe implementation, not two; `is_locked` keeps its behaviour and loses a side effect

**Decision**: add `daemon.observe_lock(path) -> LockReading`, and redefine
`is_locked(path)` as `observe_lock(path).running`. The probe opens the file **read-only,
without `O_CREAT`**, takes a **shared** lock, and reads the holder's pid from the same
descriptor when the lock is held.

**Rationale**:

- **The shared lock is not negotiable and must not be re-derived.** An exclusive probe measured
  1,558 false positives in 2,400 concurrent probes across six threads with no daemon running
  (`src/robot_army/daemon.py:129` docstring; the test is
  `tests/integration/test_single_instance.py:145`). A second probe written for `health` would be
  a second place for that to be got wrong. One implementation, one docstring, one test.
- **Dropping `O_CREAT` satisfies FR-012 and costs nothing.** Today the probe *creates* the lock
  file when it is absent — an empty file written into the state directory as a side effect of
  asking a question about it. Nothing depends on that: `SingleInstanceLock.acquire` opens with
  its own `O_CREAT` (`src/robot_army/daemon.py:86`), and every other caller only wants the
  answer. With `O_CREAT` gone, `ENOENT` becomes the ordinary "no daemon has ever run" case and
  maps to `UNHELD`, which is the same answer today's probe reaches by creating the file and
  successfully locking it. **Every existing caller's answer is unchanged**; only the file is no
  longer left behind.
- **Reading the pid from the same descriptor removes a second file read and a race.** Two call
  sites already ask both questions a line apart — `web.handle`
  (`src/robot_army/web/server.py:1571` and `:1590`) and `operations._enforced_cap`
  (`src/robot_army/operations.py:744-745`) — so today the "is it held" and "who holds it" answers
  can come from either side of a restart. `SingleInstanceLock.acquire` already demonstrates the
  technique: it reads the holder from the descriptor it failed to lock
  (`src/robot_army/daemon.py:93`). flock is advisory, so reading is always permitted.
- The holder is reported **only** when the lock is held. An unheld lock file still contains the
  last holder's pid, and returning that as "the holder" would name a process that is gone.
  `read_lock_holder` keeps its present behaviour for its own callers, which want exactly that
  text for a different purpose (`src/robot_army/operations.py:3915`).

**Alternatives considered**:

- *Leave `is_locked` alone and add a parallel probe for health.* Two implementations of the one
  thing whose exact shape was expensively established. Rejected.
- *Change `is_locked` to return the three-valued reading directly.* Every one of its nine
  callers wants a bool and would grow the same two-line collapse. The bool wrapper is the
  cheaper shape.

**Behaviour preserved, checked case by case**: open fails other than `ENOENT` → today `False`,
now `UNKNOWN` → `running` `False`. `flock` fails → today `True`, now `HELD` → `True`. `flock`
succeeds → today `False`, now `UNHELD` → `False`. `ENOENT` → today impossible (the file is
created), now `UNHELD` → `False`, which is what creating-then-locking would have concluded.

---

## R4 — The verdict is a named state on the report, not a phrase in the reason

**Decision**: `HealthReport` gains a required `state` field, a `StrEnum` with seven members:
`ok`, `stale`, `died`, `hung`, `starting`, `never_started`, `unreadable`.

**Rationale**: FR-009 requires a machine consumer to name the state without matching strings
against English. `StrEnum` is this codebase's settled way of doing that — `WorkItemState`,
`SessionState`, `CardState`, `EffectLevel` and `HoldReason` are all `StrEnum`
(`src/robot_army/states.py:25`, `src/robot_army/effects.py:67`, and others) — and it serialises
into `--json` as its own value with nothing added to `to_dict`.

Seven members, each earning its place by being a different thing to do next:

| state | what happened | what to do |
|---|---|---|
| `ok` | lock held, heartbeat fresh | nothing |
| `died` | lock unheld, a heartbeat exists | restart it |
| `hung` | lock held, heartbeat stale, written by the holder | look at the process **before** restarting it |
| `starting` | lock held, no readable heartbeat or one written by another pid | look again shortly; a startup this long is itself worth investigating |
| `never_started` | lock unheld, no heartbeat at all | it has never run here |
| `unreadable` | a heartbeat exists and cannot be parsed | look at the file |
| `stale` | heartbeat past the threshold with no usable lock reading | today's verdict, kept for the two paths that have no lock evidence |

`state` is **required**, not defaulted. Any default would be a lie waiting to be told: `ok`
would make an unhealthy hand-built report claim health, and an eighth `unknown` member would
exist solely to be the thing nobody meant. The report is built in one place in `src/` and a
handful of tests, all of which should say which state they mean.

---

## R5 — `starting` is unhealthy, and a fresh heartbeat from another pid is not

**Decision**: `starting` exits non-zero. A heartbeat *inside* the threshold makes the verdict
`ok` regardless of whose pid wrote it.

**Rationale**: The restart window is real and documented: `run_daemon` acquires the lock and then
wires boundaries, checks preconditions and runs `startup` — network work, seconds of it — before
the first beat, and nothing unlinks the previous daemon's heartbeat
(`health.published_cap` docstring, `src/robot_army/health.py:199`). So "lock held, newest
heartbeat belongs to a dead pid" is an ordinary state, not a fault.

Two different rules fall out of that, and getting either backwards costs something:

- **Stale + mismatched pid must not be called `hung`.** Nothing is wedged; a new daemon simply
  has not beaten yet. Sending someone to take a stack of a healthy starting process is the wrong
  instruction. It is still unhealthy — it is unhealthy *today*, since a stale heartbeat is all
  today's check sees — so this is no new alarm, only a better sentence.
- **Fresh + mismatched pid must stay `ok`.** Making it an alarm would fire on *every* restart,
  which is a daily event. SC-008 forbids exactly that. Something holds the lock and something
  beat a moment ago; that is a running system.

---

## R6 — Exit codes do not multiply

**Decision**: every failing verdict exits `EXIT_CHECK_FAILED` (4), as today
(`src/robot_army/operations.py:76`).

**Rationale**: The systemd unit and any shell around it care about pass/fail. The distinction a
human or a script needs is in the first line and in the `state` field, and a per-verdict exit
code would be a second encoding of the same fact for one caller that does not exist. Principle I.

---

## R7 — `published_cap` keeps its present signature

**Decision**: `published_cap(report, *, running, lock_holder)` is left alone. Callers that now
hold a `LockReading` pass `running=lock.running, lock_holder=lock.holder`.

**Rationale**: Threading the reading through it would churn a well-tested function and its tests
for no behavioural gain. The reading is a strictly better *source* for those two arguments — one
observation instead of two file reads a line apart — and that improvement is available without
touching the signature. Revisit if a third caller ever wants the pair.

---

## R8 — What this logs, and what happens if it is killed halfway

Required of every plan by the constitution's Development Workflow, and answered here so the
Constitution Check can point at it.

**Logs: nothing new, and one existing record gets sharper.** The check reads two files and takes
no action outside the process. Reads are not state changes and have never been logged here —
the heartbeat is read on every `status` and every web page. The only outward action on this path
is the notification, which is already recorded per channel under `health.notify`
(`src/robot_army/operations.py:5083`, documented at `docs/guide/audit-log.md:230`); its `reason`
field will carry the sharper sentence, which is a better record of the same shape.

**This feature also removes a write.** Dropping `O_CREAT` from the probe (R3) means the check no
longer creates a file in the state directory as a side effect of reading it — an unlogged
filesystem write that existed before this feature and will not exist after it.

**Killed halfway: nothing to recover.** The whole path is two reads and a `flock` probe that is
released before the function returns; a process killed at any point leaves no partial state,
no lock held (the kernel releases it on exit, which
`tests/integration/test_single_instance.py:94` already proves), and — after R3 — not even an
empty lock file. Rerunning is the recovery, and the timer reruns every five minutes.

---

## R9 — Which surfaces change, and the one that deliberately does not

**Decision**: the three surfaces that judge the heartbeat and have a lock reading available all
pass it in — `operations.health_check`, `operations.status`, and the web
(`web/server.handle` plus the two direct-caller fallbacks in `web/pages.py`). `_enforced_cap`
(`src/robot_army/operations.py:739`) does not: it reads the heartbeat only to hand it to
`published_cap` and never renders a verdict, so a verdict it would not use is work with no
reader.

**Rationale for including `status`**: it prints exactly one line about the daemon and it is the
health line (`src/robot_army/operations.py:382`), so `health : ok — heartbeat is 41s old` beside
a dead daemon is the whole of what that command says. It is the same defect on a second surface
and the readings it needs are already taken in that function's neighbourhood.

**Rationale for including the web**: it already takes both readings in one place and hands them
down (R1); it simply does not hand the lock to the part that judges the heartbeat. Passing it
makes `report.reason` — which the page already publishes in its chrome payload
(`src/robot_army/web/pages.py:252`) — agree with what `health` would say about the same instant,
and lets the banner say `HUNG` where it says `heartbeat STALE` today. The banner's own
`DAEMON NOT RUNNING` logic is unchanged; it reads `running`, which is unchanged.

---

## R10 — Testing

**Decision**: unit tests drive the full lock × heartbeat matrix through `check`; integration
tests cover the probe's failure and concurrency paths.

**Rationale**: Constitution: unit tests for every changed unit of behaviour, and *additionally*
failure- and interruption-path tests for anything parsing external input or touching
persistence. The matrix is the behaviour. The probe's paths that need naming:

- absent lock file → `UNHELD`, **and the file is still absent afterwards** (FR-012);
- unopenable lock file → `UNKNOWN`, and the check falls back to heartbeat-only and says so;
- concurrent probes → still shared, still no false positives. The existing test at
  `tests/integration/test_single_instance.py:145` covers this for `is_locked`; it now covers the
  one implementation underneath both.

No new dependency, no new fixture kind: `tmp_path`, the existing `layout` fixture, and the
existing pattern of writing a heartbeat with a chosen `ts` and passing an explicit `now` to pin
the staleness boundary exactly (`tests/unit/test_health.py:51`).
