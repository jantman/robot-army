# Implementation Plan: The session wrapper trusts only the identifiers its launcher gave it

**Branch**: `robot-army/issue-126-ra-16-the-wrapper-recovers-session-id` | **Date**: 2026-09-04 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260904-180332-trust-env-session-id/spec.md`

## Summary

The session wrapper stops deriving its own identity from the arguments it was handed. The
session id comes from `ROBOT_ARMY_SESSION_ID`, which the daemon already sets on every
launch; the argv scan that let the composed prompt override it is deleted. Both identifiers
that name a path — the session id and the item id — are shape-checked before any directory
is created or any path is composed, so a value the system did not issue cannot reach the
filesystem even in the act of being rejected. While in the same function, JSON escaping is
extended to cover the C0 control characters it omits, which is what presently lets an
ordinary vertical tab in an issue body quarantine that session's own exit record.

The change is small and almost entirely subtractive: one loop removed, two guards and one
substitution loop added, in one shell script. The surrounding work is tests that execute the
real script against hostile input, a guard test that the launcher keeps supplying the
variable the wrapper now depends on, and the security-analysis entries that mark RA-16 and
RA-48 resolved.

## Technical Context

**Language/Version**: Bash 5 for the wrapper (`share/robot-army-session-wrapper.sh`); Python 3.11+ for the daemon and the tests that drive it

**Primary Dependencies**: None added. The wrapper is restricted by M0 F19 to bash builtins plus `printf`, `date`, `mv` and `mkdir` — no `jq`, no `sed`, no Python — because it runs in whatever bare environment the terminal daemon provides

**Storage**: Unchanged. Exit records are one JSON file per event in the spool directory, written to a `.tmp` name and renamed

**Testing**: `pytest`. The wrapper's own behaviour is exercised by running the real script as a subprocess (`tests/integration/test_spool_recovery.py` already does this); records are read back with Python's strict JSON parser

**Target Platform**: A single Linux machine; sessions launched into `kitty` tabs via `dtach`

**Project Type**: Single project — a CLI plus a long-running local daemon, with one shell script at the process boundary

**Performance Goals**: No regression. The added escaping costs 0.079 s on a 100 KB argument, paid once per session (measured — research D4)

**Constraints**: The record format must not change (FR-007), so records already on disk and the daemon's existing drain are unaffected. The wrapper must not gain an external-program dependency (FR-008)

**Scale/Scope**: One shell script (~140 lines), one Python module touched only by a guard test, three existing tests updated, one documentation file

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design — result at the end of this section.*

### I. Simplicity First (YAGNI & KISS)

**Pass.** The change removes more than it adds: a nine-line argv scan goes, replaced by two
`[[ =~ ]]` guards. No new dependency — the escaping is built from `printf -v` and parameter
expansion precisely so the wrapper's bare-environment constraint is preserved. The one place
this principle actively decided the design is research D1: the issue offers a "keep a
validated fallback" option, and it is refused because no caller needs it. A second code path
serving a hypothetical user is the tax this principle exists to refuse — and here it would
also mean keeping, in weakened form, the mechanism the feature exists to remove.

### II. Single-User, Local-First

**Pass.** No accounts, no network, no service. The operating-system user remains the trust
boundary; this feature narrows what a *repository* can do to files owned by that user, which
is squarely inside that model rather than an extension of it.

### III. Total Accountability

**Pass, with one enumerated exception.**

*What does this log?* Nothing changes about the record path: start and exit records are
written as before, with the same fields and the same schema number. Launches continue to be
recorded in the audit log by `kitty.launch`, including the full `argv` and `env`.

*The exception.* A refusal — an identifier that fails its shape check — is written to the
wrapper's standard error and nowhere else. It does not reach the audit log. The wrapper is a
bare shell script with no access to the audit log by construction, and that is not an
oversight to correct: running in a minimal environment is why a file rather than an HTTP
POST was chosen for exit reporting (research R5), so that the record survives the daemon
being down. Granting it audit access would contradict the constraint that makes it work.

The action is not invisible. It is observable in three places that already exist: the
message on standard error, which the session host captures and `kitty --hold` leaves on
screen; the absence of a spool record, which the daemon's existing reconciliation reports as
a lost session; and the `kitty.launch` audit entry for the launch that was then refused.
This is a documented, justified gap under Principle III's exception clause rather than an
undocumented one. Reasoning in full at research D5.

*Silent failure?* None introduced. The refusal is loud and non-zero. The C0 escaping change
removes a silent failure that exists today: a record containing a control character is
written, looks fine, and is then rejected by the reader, with the session reported as lost
for reasons nothing explains.

### IV. Interruption Tolerance

**Pass.** The atomic write-then-rename is untouched. Validation is moved *above* the first
`mkdir`, so a process killed during validation has created nothing at all — strictly better
than today, where the directories are made before anything is checked. A refusal writes no
record, and the daemon's existing reconciliation is what covers a session with no record;
that path already exists and is already tested.

### V. Public Code, Unsupported Project

**Pass.** No credentials or personal data involved. The wrapper's usage line changes in a
way that breaks hand-invocation with `--session-id` in argv, which is exactly the kind of
break this principle permits: there are no outside consumers to keep compatible, and the
in-repository callers are updated in the same commit.

### Development Workflow

**Pass.** Unit tests are required and this is code parsing external input, so failure paths
must be covered, not only success paths — see the test tasks in Phase 1. The full suite must
pass before the feature is complete.

### Post-Design Re-check

Re-evaluated after the Phase 1 artifacts below were written. No gate changed status. The
design added no module, no dependency, no configuration knob and no abstraction; the only
addition beyond the script itself is test coverage and a documentation update. The
Principle III exception is unchanged in scope — it covers exactly the refusal path and
nothing else.

## Complexity Tracking

No Constitution Check violations. The table is omitted rather than filled with "N/A".

## Project Structure

### Documentation (this feature)

```text
specs/20260904-180332-trust-env-session-id/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0: six decisions, two of them changed by measurement
├── data-model.md        # Phase 1: the identifiers and the record they name
├── quickstart.md        # Phase 1: how to prove the fix by hand
├── contracts/
│   └── session-wrapper.md   # Phase 1: the wrapper's invocation contract
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2 output — created by /speckit-tasks, not here
```

### Source Code (repository root)

```text
share/
└── robot-army-session-wrapper.sh    # THE change: identifier sourcing, validation, escaping

src/robot_army/
├── dispatch.py                      # Read only — plan_launch already sets ROBOT_ARMY_SESSION_ID
└── boundaries/kitty.py              # Read only — delivers it as --env

tests/
├── integration/
│   └── test_spool_recovery.py       # Existing wrapper tests move the id to the environment
└── unit/
    └── test_session_wrapper_input.py  # New: hostile identifiers, C0 text, refusal behaviour

docs/
└── security-analysis.md             # RA-16 and RA-48 marked resolved, with the reasoning
```

**Structure Decision**: The existing layout is kept unchanged. The behaviour being fixed
lives entirely in one shell script, so the change is concentrated there. New tests go in
`tests/unit/` as a dedicated file because they test the script's input handling in isolation
— they neither need nor touch the database — while the existing end-to-end tests that drive
the script and then drain the spool stay where they are in `tests/integration/`.

## Implementation Notes

These are the decisions from research.md restated as what the implementation must do; the
reasoning for each is there, not repeated here.

1. **Order is load-bearing.** Validate the item id, then the session id, then `mkdir`, then
   compose `LOGFILE`. Any other order lets a rejected value name a path first.
2. **Delete the loop, and the comment that justified it.** The header's
   `ROBOT_ARMY_SESSION_ID  the session id, if it is not discoverable from argv` and the
   `--- Recover the session id from argv ---` block both describe behaviour that will no
   longer exist. A stale comment about a security fix is worse than no comment.
3. **Update the usage line.** The wrapper's documented invocation must state that the
   environment variable is required, since it is now the only source.
4. **Escaping order.** The C0 loop runs *after* the backslash substitution, so the
   backslashes it introduces are not themselves escaped.
5. **The daemon side needs a guard, not a change.** `plan_launch` already sets the variable.
   A test asserting that it does is what stops a future refactor removing the wrapper's only
   source of truth without anything failing.
