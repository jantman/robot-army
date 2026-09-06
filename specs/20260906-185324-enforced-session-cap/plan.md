# Implementation Plan: The session cap every surface shows is the one being enforced

**Branch**: `robot-army/issue-30-web-header-renders-a-stale-session-cap` | **Date**: 2026-09-06 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260906-185324-enforced-session-cap/spec.md`

## Summary

Every capacity fraction pairs a numerator observed from the machine with a denominator read
out of whatever configuration the reading process loaded at startup. The two halves can be
days apart in age and nothing says so, which is how the web header came to read `6/5` — over
capacity, apparently — when the truth was `6/7` with two slots free.

The daemon starts publishing `max_concurrent_sessions` on its heartbeat. Every surface that
reports capacity resolves the cap from there when a daemon holds the lock, falls back to its
own configuration when it cannot, and says so on every view when the two disagree. The cap
goes *into* the snapshot rather than being substituted at render time, so the queue's per-item
"at capacity" reasons are planned against the same number the pill shows. Nothing is refused:
the cap is a reported number, not a safety boundary, and the daemon enforces its own cap
whatever any other process believes.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: standard library only; no new dependency

**Storage**: `heartbeat.json`, already written atomically on every beat, gains one key. No
database change, no migration, no configuration key.

**Testing**: `pytest`, via `uv run pytest`

**Target Platform**: one Linux machine, one shell

**Project Type**: single-user CLI plus daemon plus a local web interface

**Performance Goals**: none new. The resolution adds one small JSON read and one shared-lock
probe per *report*, both of which the web already performs on every page.

**Constraints**: no dispatch decision may change; no action may become refused; the existing
`global_cap` key keeps its name and gets narrower meaning rather than a sibling that
consumers must learn to prefer.

**Scale/Scope**: one new function in `health.py`, one parameter and one field in
`capacity.py`, five read call sites, one renderer, three guide pages, and their tests.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design — see the re-check at the end
of this section.*

| Principle | Verdict | Reasoning |
|---|---|---|
| **I. Simplicity First** | Pass | One function (`health.published_cap`), one optional parameter and one derived field on an existing dataclass, and one sentence built in one place. No new module, no new dependency, no configuration knob — the behaviour is not optional, so there is nothing to toggle. Two heavier designs were rejected in [research.md](research.md) R1 and R4: reloading the config file in `serve` (more machinery, fixes one direction of two) and a `CapReading` dataclass threaded as a required argument (a third representation of two integers). The chosen shape is the one with fewer moving parts that still satisfies FR-003. |
| **II. Single-User, Local-First** | Pass | Nothing networked, hosted, or multi-user. The daemon and its readers communicate through a local file that already exists for exactly this purpose. No new path, no new port, no new service. |
| **III. Total Accountability** | Pass, with one pre-existing documented omission | This feature performs no action outside the process: reporting capacity is a read, and it writes nothing — no audit record, no anomaly, no state (FR-009). The one outward write it touches is the heartbeat, whose omission from the log is already documented and justified in `docs/guide/audit-log.md` ("the heartbeat file *is* the record"), and which this change does not make any more or less loggable. The cap in force is already reconstructable from the log: `daemon.start` records `max_concurrent_sessions` in its detail. **No new gap in the record is opened.** See [research.md](research.md) R10 for why an anomaly was considered and rejected. |
| **IV. Interruption Tolerance** | Pass | The heartbeat write is unchanged: write, fsync, rename, so a kill mid-write never exposes a partial file — the new key rides that same write. A kill between the config change and the daemon restart is precisely the state this feature reports honestly rather than hides. Every reader tolerates the file being absent, unparseable, stale, or written by a build with no cap in it, and each of those is a named row in the decision table with a test. No network call is added. |
| **V. Public Code, Unsupported Project** | Pass | No credential, no personal data, no hostname. `global_cap`'s meaning narrows for anything parsing `--json` — a breaking change the constitution permits outright, and one whose direction is *toward* correctness: a consumer reading only `global_cap` starts getting the right number without being changed. The `privatepuppet` half of the issue is a different repository and is recorded as out of scope in the spec. |

**What does this log?** Nothing new, and nothing that needs to be. The full argument is in the
Principle III row above and in [research.md](research.md) R10.

**What happens if it is killed halfway through?** There is no "halfway" to be killed in: every
path this feature adds is a read of a file that is written atomically elsewhere. The daemon
killed between beats leaves a complete previous heartbeat naming the cap of the process that
wrote it — which is correct until a daemon with a different cap takes the lock, at which point
the lock probe is what changes the answer, not the file. A reader killed mid-render has
written nothing.

**Post-Phase-1 re-check**: unchanged. Phase 1 added no dependency, no persistent structure, no
configuration key and no new module; `contracts/enforced-cap.md` documents behaviour the plan
had already committed to.

## Project Structure

### Documentation (this feature)

```text
specs/20260906-185324-enforced-session-cap/
├── plan.md                        # This file
├── spec.md
├── research.md                    # Phase 0
├── data-model.md                  # Phase 1
├── quickstart.md                  # Phase 1
├── contracts/
│   └── enforced-cap.md            # Phase 1
├── checklists/
│   └── requirements.md
└── tasks.md                       # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
src/robot_army/
├── health.py          # the heartbeat field, and `published_cap` — the one reader of it
├── daemon.py          # publishes the cap it is enforcing on every beat
├── capacity.py        # `enforced_cap` parameter, `configured_cap` field,
│                      #   `cap_disagreement`, and `describe()`'s trailing clause
├── operations.py      # `status` and `capacity` resolve it; `_capacity_dict` carries it
└── web/
    ├── server.py      # one reading of report+lock per request, handed down
    ├── pages.py       # `chrome` accepts that reading instead of retaking it
    └── html.py        # the notice, beside the effect-level one, and its `banner warn` rule

tests/unit/
├── test_health.py             # the field round-trips; `published_cap`'s decision table
├── test_capacity.py           # the two fields, the sentence, `at_capacity`, `describe()`
├── test_capacity_cli.py       # `capacity` in both directions, and `--json`
├── test_web_views.py          # the pill, the notice, and the JSON payload
└── test_web_effect_guard.py   # a cap disagreement refuses nothing

tests/integration/
└── test_dispatch_capacity.py  # the daemon still plans against its own configuration

docs/guide/
├── state.md           # what the heartbeat carries, and the new key
├── 3-selection.md     # which cap the surfaces report, and why the daemon is the authority
└── operating.md       # the notice on the web, and what to do about it
```

**Structure Decision**: no new module. The heartbeat's shape and the single reader of its new
field belong in `health.py`, which already owns both the writing and the judging of that file;
the cap belongs in `capacity.py`, whose stated job is "how full is the machine? one
observation, one answer". A third module holding a resolution function and a sentence would be
a file to find rather than a file to read.

## Implementation Approach

1. **Publish it.** `Heartbeat` gains `max_concurrent_sessions: int | None = None`;
   `write_heartbeat` gains the matching keyword; `Daemon._heartbeat` passes
   `self.config.daemon.max_concurrent_sessions`. The default keeps an older file parsing.

2. **Read it, once, in one place.** `health.published_cap(report, *, running) -> int | None`
   implements §1 of [the contract](contracts/enforced-cap.md): `None` unless a daemon holds
   the lock and the heartbeat carries an `int` (not a `bool`) of at least 1. It takes
   `running` rather than probing the lock, because `daemon` imports `health` and the reverse
   would be a cycle.

3. **Carry it in the snapshot.** `capacity.snapshot` gains `enforced_cap: int | None = None`.
   `global_cap` becomes the enforced cap when one is supplied; the new `configured_cap` field
   holds the reading process's own **only when the two differ**. `cap_disagreement` builds the
   sentence from those two fields; `describe()` appends it. The unobservable path resolves the
   cap the same way, so a surface that cannot count still reports the right limit.

4. **Resolve it at each read surface**, and nowhere else:
   - `operations.status` — already has the report; add the lock probe (`daemon_mod.is_locked`,
     already imported) and pass the resolved cap into its snapshot.
   - `operations.capacity` — takes both readings, passes the cap, and adds the `cap` line and
     the two JSON keys.
   - `web.server.handle` — takes the health report and the lock probe **once** and hands them
     to `effective_level`, to the cap resolution, and to `chrome`, which grows `report` and
     `running` parameters rather than retaking them (R9). This removes a duplicate read rather
     than adding one.
   - The two `capacity=None` fallbacks in `pages` stay as they are: they exist for a direct
     caller with no snapshot to hand, and resolve to the configured cap, which is the
     documented meaning of "no enforced cap supplied".
   - `dispatch` is **not** touched: the daemon plans against its own configuration (R8).

5. **Render it.** `html._chrome_bar` appends the sentence as a `banner warn` notice after the
   effect-level one, when `capacity["cap_disagreement"]` is set; a `.banner.warn` CSS rule is
   added beside `.banner.ok`. The pill's fraction and its "at capacity" styling already read
   `global_cap` from the payload, so both follow with no change.

6. **Test it**, including every row of the decision table: no daemon, unreadable heartbeat,
   stale heartbeat (which still counts), a heartbeat with no cap, a garbled cap, agreement,
   and both directions of disagreement. Plus: the daemon's own dispatch numbers are unchanged;
   nothing becomes refused; the queue's reasons are planned against the reported cap.

7. **Document it** on the three pages named above. No configuration key changes, so
   `exampleconfig.py` and `share/config.example.toml` are untouched and the drift test stays
   green.

## Complexity Tracking

No Constitution Check violations. Nothing to justify.
