# Implementation Plan: What Each Spec Kit Command Is Invoked With Is Configuration, Not Compiled-In Prose

**Branch**: `issues/39` | **Date**: 2026-09-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260901-064913-configurable-speckit-prompts/spec.md`

## Summary

Four optional strings, one per Spec Kit lifecycle command, configured globally in `[speckit.commands]`
and overridable per repository in `[repos.*].speckit_commands`. When a dispatch is already going to
send milestone 007's guidance block, whichever of those strings resolve non-empty are rendered into
it, in lifecycle order, immediately above the block's closing precedence sentence. Configured
nothing, the block is byte-identical to today's constant.

The change is confined to four modules and touches no boundary. `config.py` parses and validates the
strings and resolves them per repository with provenance, on the pattern `speckit_enabled_for`
already established. `speckit.py` grows a `guidance()` function beside the `GUIDANCE` constant that
renders the block. `dispatch.py` asks for the resolved instructions where it already asks whether the
block is enabled, and records which settings supplied them. `operations.py` carries the same
provenance into `robot-army repos --json`.

No new module, no new dependency, no new file on disk, no network call, and nothing written into a
worktree — 007's central property, unchanged for the same reason it held then.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`), standard library plus the
project's one runtime dependency (`httpx`, untouched here). No new dependency. Lint is `ruff` at
line-length 100 with the project's existing rule selection.

**Primary Dependencies**: `tomllib` (stdlib) for the configuration file — already the parser for
every other section. Nothing added.

**Storage**: None. The instructions live in `~/.config/robot-army/config.toml` and are read at
start-up like every other setting. No schema migration, no new column, no new table — the resolved
instructions are derived per dispatch and never persisted.

**Testing**: `pytest`, `tests/unit/` and `tests/integration/`, run via `uv run pytest`.

**Target Platform**: Linux, single machine, single user.

**Project Type**: Single Python package (`src/robot_army/`) with a CLI and a daemon.

**Performance Goals**: None applicable. The added work per dispatch is four dictionary lookups and
a string join, against a function that already performs four `stat` calls.

**Constraints**: The composed prompt is one `argv` entry. `prompt.MAX_BODY_CHARS` already caps the
issue body at 60,000 characters for that reason; the four instructions get a documented cap of 4,000
characters each, adding at most 16,000 to a prompt whose existing bound is already far inside
`ARG_MAX`.

**Scale/Scope**: Four strings, one maintainer, roughly a dozen repositories. One new resolution
method, one new render function, and validation for six malformed shapes.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design — see [Post-Design Re-check](#post-design-re-check).*

### I. Simplicity First (YAGNI & KISS)

**Pass, with the knob justified in writing as Governance requires.**

Principle I forbids "configuration knobs that have exactly one caller and no second use in hand".
This milestone's entire content is a configuration knob, so the burden is squarely on it.

The second caller arrived with the first. Issue #39 contains two independent requests — commit at
the end of each of the first three phases, and a paragraph attached to `/speckit-implement` — and
they are the same mechanism applied to different commands. A knob whose second use case is stated in
the issue that asked for it is not speculative generality.

What was refused, and why, matters as much:

| Rejected | Why |
|---|---|
| A second, free-form "append this to the block" string | Overlaps the per-command mechanism and duplicates `.claude/robot-army.md`. Spec, Out of Scope. |
| Configurable text for commands outside the four lifecycle rungs | Detection does not require them and the block does not name them; the text would have nowhere to render. |
| Append-to-global override semantics | Four existing settings resolve by replacement; a fifth that concatenated would be a novel rule and would leave the reader guessing at the order. |
| A new enable/disable switch for the instructions | `[speckit] enabled` and the per-repository `speckit` override already gate the block the text lives inside. FR-005. |
| Caching resolved instructions per repository | Derived on demand is one line; a cache is a second source of truth about a file the maintainer edits by hand. |

No new module, no abstraction layer, no strategy interface, and no new third-party dependency.

### II. Single-User, Local-First

**Pass.** A local TOML file gains a sub-table. No multi-tenancy, no accounts, no hosted anything, no
network. The instructions are prose, not credentials, and nothing here reads or writes a secret.

### III. Total Accountability

**Pass, with one gap enumerated and justified — as Governance requires the plan to do.**

*What does this log?* The existing `speckit.detect` record, written once per dispatch, gains an
`instructions` field: a mapping of command name to the setting that supplied it — for example
`{"implement": "[speckit.commands] implement", "specify": "[repos.\"jantman/x\".speckit_commands] specify"}`.
That is what makes an override reconstructible after the fact, and it is why FR-026 insists the
resolution be computed in one place: the record and `robot-army repos --json` must not derive the
same answer separately.

Configuration problems are reported through the existing aggregate `ConfigError` path, so a
malformed instruction is refused at start-up with every other problem in the file rather than
silently dropped. FR-006, FR-028, and User Story 3 exist for exactly this.

*The enumerated gap*: **the instruction text itself is not written to the log — only the name of the
setting that supplied it.** Justified on two grounds. First, the log already does not record the
composed prompt: the issue body, the repository's `.claude/robot-army.md`, and the delivery block are
all absent from it today, and recording up to 16,000 characters of configured prose per dispatch
while continuing to omit the issue body it sits beside would make configured text uniquely
privileged for no reason anyone could defend. Second, the record names the exact setting, and the
configuration file is a local, human-readable, hand-edited file — so the text is one `grep` away
rather than lost. SC-006 is written to that standard ("given the log and the configuration file")
rather than to log-alone reconstruction, deliberately and visibly.

### IV. Interruption Tolerance

**Pass.** *What happens if it is killed halfway through?* Nothing is written, so there is nothing to
half-write. Configuration is read at start-up and parsed into frozen dataclasses; a prompt is
composed once, in memory, immediately before dispatch. A daemon killed mid-compose loses a prompt
that was never sent, and the item stays in the state it was already in — the existing dispatch
recovery path, untouched here. No new network call, so no new timeout or retry bound is needed.

The one interruption-adjacent behaviour worth naming: an edit to `config.toml` reaches dispatches
after the next daemon start, consistent with every other setting, and items already dispatched keep
the prompt they were dispatched with. That is stated in the spec's Assumptions rather than fixed.

### V. Public Code, Unsupported Project

**Pass.** The README gains configuration documentation written for the author's future self, with the
issue's own two paragraphs shown as examples of use and explicitly not as defaults (FR-021, FR-022).
No credential, hostname, or personal data enters the repository — the example text is prose about git
and pull requests.

Milestone 007's FR-009 is amended rather than preserved (FR-014), which Principle V explicitly
permits: there is no outside consumer owed backward compatibility, and the amendment is recorded in
007's own contract so a reader of that milestone finds it.

### Development Workflow

Unit tests are required for every new or changed unit of behaviour, and configuration parsing is
"code parsing external input", which additionally requires failure-path tests. Both are planned:
`tests/unit/test_speckit_commands_config.py` covers the six malformed shapes and the resolution
matrix, and the golden-string test in `tests/unit/test_speckit_prompt.py` is extended rather than
replaced.

## Project Structure

### Documentation (this feature)

```text
specs/20260901-064913-configurable-speckit-prompts/
├── plan.md              # This file
├── research.md          # Phase 0 output — the seven decisions and what was rejected
├── data-model.md        # Phase 1 output — the config shapes and the resolution rules
├── quickstart.md        # Phase 1 output — how to verify it end to end
├── contracts/
│   ├── config.md        # The configuration surface, validation, and resolution
│   └── prompt-block.md  # What the rendered block looks like, exactly
├── checklists/
│   └── requirements.md  # Written by /speckit-specify
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/robot_army/
├── config.py        # [speckit.commands], [repos.*].speckit_commands, validation,
│                    # CommandInstruction, Config.speckit_commands_for()
├── speckit.py       # LIFECYCLE (unchanged), GUIDANCE (unchanged), new guidance() renderer
├── dispatch.py      # speckit_block(): resolve, render, record provenance
└── operations.py    # _speckit_column(): carry provenance into `repos --json`

tests/
├── unit/
│   ├── test_speckit_commands_config.py   # NEW — parsing, validation, resolution matrix
│   ├── test_speckit_guidance_render.py   # NEW — rendering, ordering, absence
│   ├── test_speckit_config.py            # extended — untouched gate behaviour still holds
│   ├── test_speckit_prompt.py            # extended — golden string, configured and not
│   └── test_speckit_dispatch_prompt.py   # extended — block reaches the prompt
└── integration/
    ├── test_speckit_dispatch.py          # extended — audit provenance end to end
    └── test_speckit_writes_nothing.py    # unchanged — must still pass untouched

docs and contracts updated (FR-014, FR-021):
├── README.md                                        # the Spec Kit section
├── specs/007-speckit-extensions/contracts/config.md # the section this extends
└── specs/007-speckit-extensions/contracts/prompt.md # FR-009's amendment, recorded
```

**Structure Decision**: The existing single-package layout is unchanged. This feature adds no
module and no directory; every edit lands in one of four existing files, each of which already owns
the concern being extended — configuration parsing and resolution in `config.py`, Spec Kit knowledge
in `speckit.py`, the dispatch-time decision and its record in `dispatch.py`, and the offline listing
in `operations.py`. Two new unit test files are added because the two new units of behaviour
(validation and rendering) are large enough that folding them into `test_speckit_config.py` would
bury the gate tests that file exists for.

## Complexity Tracking

No Constitution Check violations. The one item that required written justification — a configuration
knob, against Principle I — is justified in the Constitution Check above rather than here, because it
is a knob the constitution permits once a second caller is in hand, not a violation being accepted.

## Phase 0: Research

See [research.md](./research.md). Seven decisions, of which three were live questions rather than
confirmations of the obvious:

- **R1** — where the configuration sits, and why the per-repository key cannot mirror the global one.
- **R2** — whether `config.py` may import from `speckit.py` (it may; verified acyclic).
- **R4** — where the instructions render inside the block, which FR-015 constrains more tightly than
  it first appears.
- **R5** — empty string meaning different things in the two places, and why that is not an
  inconsistency.

## Phase 1: Design

See [data-model.md](./data-model.md), [contracts/config.md](./contracts/config.md),
[contracts/prompt-block.md](./contracts/prompt-block.md), and [quickstart.md](./quickstart.md).

### Post-Design Re-check

Re-run after the contracts were written. No gate changed its answer.

The design added nothing that was not in the pre-design sketch: one sub-table, one repository key,
one dataclass, one resolution method, one render function, one audit field, one JSON field. The
count of new public names is seven, all in existing modules.

Two things the contracts settled that the Constitution Check should be read as covering:

- **Rendering position** (R4) is above the block's closing precedence sentence, not below it. That is
  what keeps FR-015 true — the sentence's scope is "any instruction above this paragraph", so text
  placed after it would fall outside the precedence rule the block advertises. A design that read
  FR-015 as "put it at the end" would have silently broken the one guarantee the block makes about
  a repository's own instructions outranking it.
- **The audit gap** named under Principle III is unchanged by the design: the `instructions` field
  carries provenance and not text, and `contracts/config.md` states that as the contract rather than
  leaving it as an implementation detail to be re-litigated during `/speckit-implement`.
