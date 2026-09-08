# Implementation Plan: A Reattach Line Only Where Reattaching Would Reach That Session

**Branch**: `robot-army/issue-51-show-offers-a-dtach-a-reattach-command` | **Date**: 2026-09-08 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260908-170313-attachable-reattach-line/spec.md`

## Summary

`show` prints `reattach: dtach -a <path>` for every session attempt that recorded a socket
path, in every session state (research.md R1). The issue reports the harmless half of that: a
`lost` attempt offering a command that fails at the shell.

Phase 0 found the other half, and it is the one that decides the design. Sockets are named
after the **work item**, not the session (`paths.py:150`), so every attempt on an item records
the same path. Once the item is dispatched again, the command printed under the row marked
`lost` **succeeds** — and attaches the maintainer to a different session than the row they read
it from (R2). The guard cannot be "does that path answer"; it has to be "could this row be what
is answering", and only then "is anything answering".

So two guards, in that order, and both already exist in the codebase:

1. **`states.TERMINAL_SESSION_STATES`** decides attribution. A finished attempt is not what is
   listening, whatever is — nothing reopens a session row. Free, and it is the same rule the
   web already uses to decide whether to offer its attach button (R3).
2. **`boundaries.session_host.is_alive`** decides reachability, for the rows the first guard
   lets through. It probes rather than trusting that the file exists, which is the distinction
   this feature is about, and it is the same question `attach` will ask when the maintainer
   acts on the line (R4).

Three changes, in two files, plus one paragraph of documentation:

1. **One helper** in `operations.py` — `_reattach_lines(ctx, session)`, returning the nought or
   one lines that attempt is entitled to. Four outcomes, contracted in
   [contracts/show-reattach-line.md](./contracts/show-reattach-line.md): nothing, the command,
   a refusal naming the path, or the command with a caveat.
2. **One call site** — the `if session.host_socket:` block in `show`, which becomes a loop over
   what the helper returns.
3. **One `try`/`except BoundaryError`** inside the helper. This is not decoration: `show` calls
   nothing that raises today, so introducing the probe introduces a new way for a read-only
   inspection command to die, exactly when the machine is already misbehaving (R5).

No schema change, no configuration key, no new dependency, no change to the `--json` payload
and therefore none to the web (R7).

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`).

**Primary Dependencies**: none added. Nothing outside the standard library and the code already
in this repository is involved.

**Storage**: none touched. No table, no column, no migration; `SCHEMA_VERSION` does not move.
The feature reads a session row and asks the operating system a question about a path.

**Testing**: pytest. One new unit module covering the four outcomes across all five session
states, the shared-socket case from R2, the no-socket case, and the raising probe. The seam is
`StubSessionHost.alive` — a set of socket paths the stub reports as live — which conftest
already provides, so no new fixture is needed. Nothing in the suite asserts on this line today
(R6), so every assertion about it is new.

**Target Platform**: single Linux machine with a shell.

**Project Type**: single Python package (`src/robot_army`) — CLI plus daemon plus a small web
interface. This feature touches the CLI rendering path only.

**Performance Goals**: none meaningful, and one thing to not regress. A finished attempt must
cost nothing, which the guard order guarantees: the state check short-circuits before any
syscall. Open attempts cost one measured 0.02–0.05 ms probe each on this machine (R4), bounded
above by the boundary's own 3-second timeout, and an item has at most a handful of them.

**Constraints**:

- `show` must stay read-only (FR-007). A `connect()` that is closed immediately without sending
  changes nothing, and the boundary's docstring records that an attached viewer is unaffected.
- The implementation must not pick a host implementation from stored session state. That
  happens in exactly one place in this system, `operations.py:2733` says so, and a test holds it
  there (R4).
- The `--json` payload keeps its exact keys, because `web/pages.py` renders the item view from
  it (R7).

**Scale/Scope**: `operations.py` (one helper, one call site), `docs/guide/4-session.md` (one
paragraph), one new unit test module. No other file changes.

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 — see below.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | PASS. One helper function, one call site, two guards that both already exist in the tree. No new module, class, dependency, configuration knob, column, or command. Five tempting elaborations rejected below. |
| **II. Single-User, Local-First** | PASS. Reads a session row from the local SQLite database and probes a unix socket under `XDG_RUNTIME_DIR`. No network, no service, no account, no new state. |
| **III. Total Accountability** | PASS, with one omission enumerated below. The feature changes what a read-only command prints; it takes no action on anything outside the process except a connect-and-close probe, which is deliberately not logged and justified below. |
| **IV. Interruption Tolerance** | PASS. Nothing is written, so there is no half-written state to leave behind. The one call that can block carries the boundary's existing 3-second timeout, and its failure is caught and reported rather than swallowed. See "What happens if it is killed halfway". |
| **V. Public Code, Unsupported** | PASS. No credentials, personal data, or private hostnames. The paths this prints are the maintainer's own runtime directory, printed to their own terminal, unchanged from today. Phase 0 probe modules were deleted after measurement; what they printed is quoted in research.md and contains only synthetic ids. |

### Rejected elaborations (Principle I)

| Tempting | Why it was rejected |
|---|---|
| Per-session socket paths, so a path identifies the attempt that recorded it | It is the *correct* fix for R2 in the abstract, and wildly out of proportion: it changes the dispatch path, the reboot story and the socket-pruning story to fix an output line. The row's own state answers the attribution question for free |
| `procinfo.is_alive(pid, proc_start)` as a third guard | Strictly more precise, and it degrades to a bare "does `/proc/<pid>` exist" when `proc_start` is missing — a legitimate row shape. That middle "cannot identify it" case would need its own wording on the line, for a distinction the state check has already made (R3) |
| An `attachable` key in the `--json` payload | `_session_dict` has two callers, one of which renders a web page per request. Adding a probe inside it serves a consumer that has not asked, and annotating only `show`'s copy would give one helper two shapes depending on the caller (R7) |
| A `--probe` / `--no-probe` flag to opt out of the check | A knob with one caller and no second use in hand. The check costs microseconds and the whole point is that the line is not trustworthy without it |
| Suppressing the line for terminal states and stopping there — the issue's first suggestion | It fixes the reported symptom and leaves the `running` row whose socket answers nothing still offering a broken command, which is the same bug with the states swapped. The two guards together are barely more code than one |
| Fixing the same pattern in `worktree remove`'s refusal (`operations.py:2474`) | That site only fires for an item with open sessions, so its guard is already the attribution half, and its message is about a worker that may be writing rather than an offer to attach. Changing it here would be scope this issue did not ask for. Noted, not done |

### What this logs (required by Development Workflow)

| Record | When | Meaning |
|---|---|---|
| — | — | **Nothing new is written to the audit log by this feature.** |

**The omission, named and justified (Principle III's exception path).** The socket probe is an
action outside the process — a `connect()` and an immediate close on a unix socket — and it is
deliberately not logged. Three reasons: it changes nothing (no file is created, removed, or
written, and the boundary documents that an attached viewer is unaffected), it is a read taken
to render a line the maintainer is looking at as it happens, and `is_alive` is already called
without logging by `reconcile` and `capacity` on every pass — logging it here alone would make
the record misleading about which probes are recorded. The standard of reconstruction in
Principle III is unharmed: nothing happened to reconstruct.

`show` itself logs nothing today and continues not to, which is the existing and correct
treatment of a read-only inspection command.

### What happens if it is killed halfway (required by Development Workflow)

Nothing is left behind. The feature writes no file and opens no transaction; a `show` killed
mid-render leaves a partial line on a terminal and no other trace. The one blocking call is the
probe, which the boundary already bounds at `PROBE_TIMEOUT = 3.0` seconds, and the socket is
closed in a `finally` inside the boundary. A probe interrupted by a signal aborts the command
without having changed anything on either side of the socket.

The interruption case that *does* matter here is the one the feature reports rather than one it
creates: a session killed halfway leaves a `running` row and no listener, and User Story 2 is
that row learning to say so.

## Project Structure

### Documentation (this feature)

```text
specs/20260908-170313-attachable-reattach-line/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   └── show-reattach-line.md
├── checklists/
│   └── requirements.md
└── tasks.md             # /speckit-tasks, not created here
```

### Source Code (repository root)

```text
src/robot_army/
├── operations.py            # _reattach_lines(), and the call site inside show()
├── states.py                # TERMINAL_SESSION_STATES — read, not changed
└── boundaries/
    ├── __init__.py          # SessionHost.is_alive, BoundaryError — read, not changed
    └── dtach.py             # the probe itself — read, not changed

docs/guide/
└── 4-session.md             # one paragraph under "Attaching to a running session"

tests/unit/
└── test_show_reattach_line.py   # new
```

**Structure Decision**: no structural change. The helper lives beside `show` in
`operations.py`, next to `_speckit_lines` and `_pull_request_line`, which are the two existing
helpers that answer "what, if anything, does this row print here?" — the same shape of
question, and the reason no new module is warranted.

## Phase 1 Design

**The helper's signature and why it takes `ctx`.** `_reattach_lines(ctx, session)` needs the
boundary, and `ctx` is how every other function in this file reaches one. Passing the host
alone would be a narrower dependency and a different convention from its neighbours; the
convention wins.

**Why it returns a list rather than a string or `None`.** `_speckit_lines` next door already
returns `list[str]` for exactly this — "nought or more lines, and the caller does not decide
which" — and the call site becomes a `for` loop identical to the one already below it. A
`str | None` would need the caller to branch on emptiness, putting a piece of the decision back
in `show`.

**The four outcomes** are contracted in
[contracts/show-reattach-line.md](./contracts/show-reattach-line.md) with the exact text of
each. Two of them are new wording and the contract is where they are fixed, so the tests and
the guide page quote one source.

**Test seam.** `StubSessionHost.alive` is a set of socket paths; `is_alive` reports membership.
A test makes a socket "live" by adding its path and "dead" by leaving it out, and makes the
probe raise by substituting a host whose `is_alive` raises `BoundaryError`. No temporary files
and no real sockets are needed in the unit tests; the real probe's behaviour against real
sockets was measured in R4 and belongs to the boundary's own contract, not to this one.

## Constitution Re-Check (post-design)

Unchanged from the gate above. The design added no file, no dependency, and no state; the only
thing Phase 1 settled that could have moved a verdict — whether to annotate the JSON payload —
was decided *against* adding, on Principle I grounds (R7). The single Principle III omission is
enumerated above rather than left implicit.

## Complexity Tracking

No Constitution Check violations. Nothing to justify.
