# Implementation Plan: The Dead-Man's Switch Reads the Lock as Well as the Heartbeat

**Branch**: `robot-army/issue-52-the-dead-man-s-switch-has-a-180s` | **Date**: 2026-09-09 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260909-093421-lock-aware-health/spec.md`

## Summary

`health.check` judges one signal — the heartbeat's age — so for up to the whole staleness
threshold after a daemon dies, the dead-man's switch reports `ok` and exits 0. The web interface,
reading the lock, knows within a second. The 180 seconds is not a detection target; it is a floor
imposed by consulting the weaker of two available signals, and it cannot be lowered by lowering
the threshold, because heartbeat age has to stay well above the tick interval.

The fix is to hand `check` a second reading. `daemon.observe_lock` produces a three-valued
observation of the single-instance lock — held, unheld, or unobservable, with the holder's pid
when held — taken with the same shared-lock probe `is_locked` already uses, which is then
reimplemented on top of it. `check` accepts that reading as an optional argument, never taking
one for itself: `daemon` imports `health`, so the reverse would be a cycle, and every call site
already holds a reading that the web goes to trouble to take exactly once per request.

With both signals the verdict becomes a named state rather than a bool: a released lock means
`died` regardless of heartbeat age, a held lock with a stale heartbeat from the holder means
`hung`, and a held lock with no beat of its own yet means `starting` — an ordinary restart, not a
wedge. The state travels in the human line, in `--json`, and in the alert, so "restart it" and
"look at it before restarting it" stop arriving as the same word. `status` and the web pass the
same reading, so no two surfaces can describe one machine differently — which is what the
reported incident actually was.

## Technical Context

**Language/Version**: Python 3.11+ (`enum.StrEnum`, already used throughout)

**Primary Dependencies**: none added. `fcntl`, `os`, `json`, `datetime` — standard library, all
already imported by the two modules being changed

**Storage**: none. Two existing files are read: `heartbeat.json` and the daemon lock. No database
table, no new state file, no change to any file's shape

**Testing**: `pytest`, `uv run pytest`. Existing fixtures (`tmp_path`, `layout`) and the existing
pattern of an explicit `now` to pin the staleness boundary

**Target Platform**: one Linux machine, one user, advisory `flock` on a local filesystem

**Project Type**: single Python package — a CLI plus a local web interface

**Performance Goals**: the check adds one `open`/`flock`/`close` on a local file to a command
that already reads a file. Immeasurable, and it *removes* a second file read at the two call
sites that ask "is it held" and "who holds it" separately

**Constraints**: the probe must stay **shared** (an exclusive probe measured 1,558 false
positives in 2,400 concurrent probes); a failed probe must not be reported as a death; a healthy
run's output must not change

**Scale/Scope**: two modules changed substantively (`health.py`, `daemon.py`), three surfaces
threaded through (`operations.health_check`, `operations.status`, the web), one documentation
page

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design — see the second table.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | No new dependency, no new module, no configuration knob. The one thing that could be called an abstraction — the three-valued lock reading — exists because this feature changes what a `False` from the probe *means*: it becomes "the daemon has died", which pages the operator, so a permission error may no longer be silently folded into it (research R2). The probe itself is not duplicated: `is_locked` is reimplemented on top of the new one, so the expensively established shared-lock shape has exactly one implementation and one test. The alternative designs — a parallel probe for `health`, a third module to break the import cycle, per-verdict exit codes — are each recorded and rejected in `research.md`. |
| **II. Single-User, Local-First** | Reinforces it. The lock is a local advisory `flock` and is trustworthy evidence precisely because there is one machine and one daemon. Nothing hosted, nothing networked, no account, no new path. |
| **III. Total Accountability** | **Nothing new is logged, and nothing goes unlogged.** The check reads two files and takes no action outside the process; reads have never been logged here and logging them would bury the record. The only outward action on this path is the notification, already recorded per channel as `health.notify`, whose `reason` field now carries a sharper sentence in an unchanged shape. The feature also **removes** an unlogged write: the probe stops creating the lock file it was asked about. No exception under Principle III's exception path is being claimed, because no action that changes state outside the process is being left unrecorded. |
| **IV. Interruption Tolerance** | **If it is killed halfway, there is nothing to recover.** The whole path is two reads and a probe released before the function returns; the kernel releases an `flock` on exit however the process exits, which `tests/integration/test_single_instance.py` already proves for `SIGKILL`. No partial file is written — after this change, not even an empty lock file. Rerunning is the recovery and the timer reruns every five minutes. No network call is added, so the timeout and retry rules have nothing new to bind. |
| **V. Public Code, Unsupported Project** | No credential, hostname or personal data is added; the sentences name local paths that are already printed by `status` today. `HealthReport` grows a required field and its `to_dict` grows two keys, which breaks nothing outside this repository and is owed nothing if it did. |
| **Operating Constraints** | Every verdict is reachable and legible from the terminal; failure still exits non-zero, with the existing check-failure code. No graphical interface is required for anything — the web change is the *removal* of a discrepancy, not a new capability. Nothing outward-facing becomes reachable by default: the notification path is untouched and still requires configuration. |
| **Development Workflow** | Spec → plan → tasks → implement, with this gate. The two questions the constitution requires of every plan are answered in the Principle III and IV rows above. Unit tests cover the full lock × heartbeat matrix; the probe additionally carries failure-path tests (absent file, unopenable file) and keeps its concurrency test, as required for code touching persistence and external input. |

**Verdict: PASS.** No violation, so Complexity Tracking is empty and omitted.

## Project Structure

### Documentation (this feature)

```text
specs/20260909-093421-lock-aware-health/
├── plan.md                        # this file
├── spec.md
├── research.md                    # R1–R10
├── data-model.md                  # LockState, LockReading, HealthState, HealthReport
├── quickstart.md                  # eight validation scenarios, all offline
├── contracts/
│   └── health-verdict.md          # the derivation, the exact sentences, the JSON shape
├── checklists/
│   └── requirements.md
└── tasks.md                       # /speckit-tasks output, not created here
```

### Source Code (repository root)

```text
src/robot_army/
├── health.py          # LockState, LockReading, HealthState; check() gains `lock=`;
│                      # HealthReport gains `state`; to_dict gains state + lock;
│                      # alert_fields carries the state
├── daemon.py          # observe_lock(); is_locked() reimplemented on it, O_CREAT dropped
├── operations.py      # health_check: observe, pass, print the label
│                      # status:       observe, pass, print the label
└── web/
    ├── server.py      # handle: one observation per request, passed to check and reused
    │                  # for published_cap's holder; the effective_level fallback likewise
    ├── pages.py       # chrome + effect_mismatch fallbacks observe; chrome payload gains state
    └── html.py        # the running-daemon suffix renders the label, not the word STALE

tests/
├── unit/
│   ├── test_health.py            # the lock × heartbeat matrix, the sentences, to_dict,
│   │                             # alert_fields, and the unchanged heartbeat-only path
│   └── test_web_views.py         # the chrome agrees with the check
└── integration/
    └── test_single_instance.py   # observe_lock: absent, unopenable, held, holder pid,
                                  # no file created; the shared-probe concurrency test

docs/guide/operating.md            # "Noticing it has died" — what the switch now consults
```

**Structure Decision**: no new files in `src/`. Both new types belong in `health.py` because the
import direction forces it — `daemon` imports `health` — and because they are the checker's
vocabulary for the evidence it is handed. A third module to hold them would exist to solve a
problem that does not exist.

## Phase 1 re-check (post-design)

| Question | Answer |
|---|---|
| Did the design add a dependency? | No. |
| Did it add a module, a table, a state file, or a config key? | No. `share/config.example.toml` therefore needs no regeneration, and `docs/guide/configuration.md` needs no new entry. |
| Did it add an abstraction with one implementation? | `LockReading` has exactly one producer, `observe_lock`, and that is the point of it — one observation, taken once, handed down. It is a value, not a strategy interface. |
| Did the verdict vocabulary grow beyond need? | Seven states, each mapping to a different action a reader would take, tabulated in research R4. `stale` is retained rather than renamed because two paths — no reading, and an unreadable lock — genuinely reach a verdict without the lock. |
| Does anything now happen by default that did not before? | No. The check reads one more file it could always have read. Notifications remain off until configured. |
| Which documentation page? | `docs/guide/operating.md`, per the table in `CLAUDE.md`: this is the health/recovery surface. `state.md` is untouched (no file shape changes), `audit-log.md` is untouched (no new action, no record shape change), `configuration.md` is untouched (no key changes). |

**Verdict: PASS, unchanged.**
