# Feature Specification: Every confirmation prompt survives being given up on

**Feature Branch**: `robot-army/issue-23-eof-ctrl-c-at-a-confirm-prompt`

**Created**: 2026-09-06

**Status**: Draft

**Input**: jantman/robot-army issue #23 — "EOF/Ctrl-C at a confirm prompt tracebacks everywhere except `onboard`, including the two destructive prompts"

## Context

Four commands stop and ask the maintainer a question before doing something they cannot
undo:

| Command | The question |
|---|---|
| `onboard` | "Approve `<repo>` for dispatch, recording this fingerprint? [y/N] " |
| `worktree remove --force` | "Type the item id (`<n>`) to force-remove `<path>` and discard its uncommitted work: " |
| `cancel` | "Stop session `<id>` for item `<n>`? [y/N] " |
| `purge-simulated` | "Delete N simulated work item(s), … ? [y/N] " |

Only `onboard` handles the two ways a maintainer gives up on a question: pressing Ctrl-C,
and having no input at all (`< /dev/null`, a pipeline, a cron entry, an editor's terminal
pane that has already closed its stdin). The other three crash with a Python traceback,
exit with a code that means nothing in particular, and — for the two destructive commands
— leave no usable trace that the destructive act was ever attempted.

That last part is the real defect. `worktree remove --force` is the command that discards
uncommitted work. Giving up at its prompt today produces a traceback and an audit record
whose only content is `EOFError`, and `cancel` and `purge-simulated` produce no record at
all, because neither has opened an audit action by the time it asks.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Giving up at a destructive prompt is recorded, not crashed (Priority: P1)

The maintainer runs `worktree remove <n> --force`, reads the prompt naming the path and
the uncommitted work it is about to discard, thinks better of it, and presses Ctrl-C.
Later — or during an incident review — they read the audit log and can see that a
force-removal of that worktree was attempted and abandoned at the prompt, without anything
having been removed.

**Why this priority**: This is the command with the highest cost of an unrecorded attempt,
and it is one of the two the issue calls out as unmet against the standard that already
justified fixing `onboard`. The same holds for `cancel`, which signals a running worker.

**Independent Test**: Run each destructive command with stdin closed, and with an
interrupt delivered at the prompt; check the process exits with a stated code and a
one-line message, and that the audit log holds a record naming the command, its target,
and why it ended.

**Acceptance Scenarios**:

1. **Given** a work item with a worktree, **When** `worktree remove <n> --force` is run
   with no input available, **Then** nothing is removed, no traceback is printed, the
   command reports that no answer was available, and the audit log records the attempted
   force-removal, its target, and that it ended for want of an answer.
2. **Given** the same command, **When** the maintainer interrupts at the prompt, **Then**
   nothing is removed, the command reports it was interrupted, and the audit log records
   the attempt and that cause — distinguishable from the no-input cause above.
3. **Given** a work item with a running session, **When** `cancel <n>` is given up on
   either way, **Then** no signal is sent to the session, the item's state is unchanged,
   and the audit log holds a record that a cancel of that session was attempted and
   abandoned.
4. **Given** simulated rows in the database, **When** `purge-simulated` is given up on
   either way, **Then** no rows are deleted and the audit log holds a record of the
   abandoned purge and the counts it was about to delete.

---

### User Story 2 - Absent input is never read as consent (Priority: P1)

The maintainer wires one of these commands into something non-interactive by accident — a
script, a pipeline, a `cron` entry. The command asks its question, gets nothing, and stops.
It never proceeds as though the answer had been "yes".

**Why this priority**: The failure this guards against is silent, destructive and
irreversible. It must hold for the typed-id prompt as strictly as for the `[y/N]` ones:
an empty answer is not the item id.

**Independent Test**: Run each of the four commands with `< /dev/null` and confirm the
guarded action did not happen and the exit code is non-zero.

**Acceptance Scenarios**:

1. **Given** any of the four prompting commands, **When** it is run with stdin at
   end-of-file, **Then** the guarded action does not happen and the exit is non-zero.
2. **Given** `worktree remove --force`, **When** input ends before the item id is typed,
   **Then** the removal does not happen — the absent answer is not treated as a match.

---

### User Story 3 - The next prompt added inherits the handling (Priority: P2)

Someone (likely a future coding session) adds a fifth confirmation prompt to a command. It
behaves like the four that exist without that author having to remember to write the
handling, and its abandonment is recorded under a label that author chose.

**Why this priority**: The issue's own diagnosis is that the gap exists because the
handling was written at the call site four times and got written once. Fixing the three
call sites without moving the handling somewhere shared re-arms the same trap. It is P2
rather than P1 because the maintainer-visible defect is fixed by the first two stories.

**Independent Test**: Read the code and see that no prompting command carries its own
interrupt handling; verify by test that a prompt routed through the shared path is
guarded without any per-call-site code.

**Acceptance Scenarios**:

1. **Given** the shared prompt mechanism, **When** a caller asks a question through it and
   input ends, **Then** the caller receives a distinguishable "gave up" outcome rather
   than an exception escaping to the top level.
2. **Given** the same, **When** the maintainer interrupts, **Then** the caller receives a
   "gave up" outcome that says it was an interrupt, not an absent answer.

---

### Edge Cases

- **An interrupt while the guarded work is already running** (after the answer, during the
  removal, the signal, the delete) is out of scope and MUST keep behaving as it does now.
  This feature covers only the window in which the question is unanswered.
- **`--force` / `--yes` runs never reach a prompt**, so nothing about them changes.
- **`worktree remove --force` already holds an open audit action** when it asks, so its
  record must be the abandonment of *that* action rather than a second, unrelated one.
- **`cancel` and `purge-simulated` hold no audit action** when they ask, so an abandonment
  record has to be written by something.
- **A `--json` run** that is given up on must still emit a parseable document on stdout and
  keep the prompt and the message off it.
- **Typing something that is not the item id** at the force prompt is a decline, not an
  abandonment, and keeps today's wording and exit code.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every command that asks the maintainer a question before acting MUST treat an
  interrupt at that question, and input ending at that question, as terminations of the
  command rather than as errors escaping to the top level. No traceback reaches the
  maintainer from any of the four prompts.
- **FR-002**: Neither an interrupt nor an absent answer may be read as consent. In every
  case the guarded action MUST NOT happen: no worktree or branch removed, no signal sent to
  a session, no rows deleted, no repository approved.
- **FR-003**: Each such termination MUST be recorded in the audit log, naming the command,
  the entity it was about to act on, and which of the two causes ended it — an interrupt
  and an absent answer being separately identifiable in the record.
- **FR-004**: For `worktree remove --force`, the record MUST make plain that a *forced*
  removal was the thing attempted, and MUST identify the worktree path, so the log alone
  answers what was nearly discarded.
- **FR-005**: Each termination MUST print one plain line saying what happened, and MUST
  exit non-zero. The two causes MUST be distinguishable by exit code, using the codes the
  already-correct `onboard` prompt uses for the same two causes, so that the four commands
  agree with each other.
- **FR-006**: The handling MUST live in one place that all four prompts pass through, so
  that a prompt added later is guarded without its author writing anything, and MUST let
  the caller say what the abandonment should be recorded as.
- **FR-007**: The wording of the four prompts themselves MUST NOT change, nor may the
  behaviour of any answered prompt: "yes", "no", and a mistyped item id keep today's
  messages, exit codes and records exactly.
- **FR-008**: A `--json` run of a command given up on MUST still produce a valid document
  on stdout, with the prompt and the human-readable message on stderr.

### Key Entities

- **Prompt abandonment**: what happened when a question went unanswered. Carries which of
  the two causes it was, the command it belonged to, and the target the command was about
  to act on.
- **Audit record for an abandonment**: an entry in the existing append-only log, under the
  same action name the command uses when it succeeds, with an outcome that marks it as not
  having gone through and a cause that says why.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All four prompting commands, run with input closed, exit with a stated code
  and a single line of explanation. Zero produce a traceback. Today three of four do.
- **SC-002**: All four, given up on either way, leave a record in the audit log from which
  the command, the target and the cause can be read without re-running anything. Today one
  of four does.
- **SC-003**: In every abandonment, the guarded action's effect on the world is nil —
  verifiable by inspecting the worktree, the session's state, and the row counts afterward.
- **SC-004**: The number of places in the codebase that separately handle interrupt or
  absent input at a confirmation prompt is one.
- **SC-005**: Every existing behaviour of an *answered* prompt is unchanged, demonstrated by
  the existing tests for those paths continuing to pass unmodified.

## Assumptions

- The two exit codes `onboard` already uses for these two causes are the right ones for the
  other three commands, and consistency across the four is worth more than any per-command
  distinction. (Issue #23 quotes the `onboard` behaviour approvingly as the model.)
- The abandonment record belongs under each command's existing audit action name rather
  than a new one shared across the four, because the log is read by asking what a command
  did.
- `cancel` and `purge-simulated` do not open their audit action until after the answer
  because opening one means announcing an intent to act; recording an abandonment for them
  therefore writes a record rather than opening and abandoning an action.
- Ctrl-C delivered during the guarded work, after the question is answered, is a different
  problem with a different answer and is not addressed here.
- No configuration key is added: nothing here is optional or tunable.
