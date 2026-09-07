# Implementation Plan: The delivery block follows the effect level

**Branch**: `robot-army/issue-32-the-effect-ladder-constrains-robot-army` | **Date**: 2026-09-07 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260907-063858-effect-aware-delivery/spec.md`

## Summary

The effect ladder is enforced at the five seams robot-army owns. The session it launches is not
one of them, so at `no-remote` a dispatched session pushed a branch to GitHub — and since the
standing delivery block landed, that is now what every dispatch at every level asks for.

The delivery block gains a second form. `effects.wire()` picks one, the way it already picks
every implementation, and hands it to the composer on `Boundaries`; nothing downstream learns
that a level exists. At `live` the form is the one that ships today, unchanged. Below `live` it
tells the session to commit its work on the branch and stop there — no push, no pull request, no
comment on the issue, nothing else outward — and says, in one narrowly scoped sentence, that an
instruction composed above it asking for a push describes a `live` dispatch and not this run.
That sentence is load-bearing: this repository's own `[speckit] implement` string tells sessions
to push, and it sits above the delivery block where position gives it precedence.

The rest is the half of the issue that no code can fix. `contracts/boundaries.md`, quickstart
scenario 3, and the guide's setup and session pages stop reading as promises about the machine
and start saying what the ladder governs, what it asks for, and that asking is not enforcing.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: standard library only; no new dependency

**Storage**: none. No database change, no migration, no state file, no configuration key.

**Testing**: `pytest`, via `uv run pytest`

**Target Platform**: one Linux machine, one shell

**Project Type**: single-user CLI plus daemon plus a local web interface

**Performance Goals**: none new. One dataclass field selected once at startup, and one extra
string reference per composed prompt.

**Constraints**: the `live` prompt must be byte-for-byte what it is today (SC-003); no module
outside the wiring may name `EffectLevel` (FR-009, enforced by an existing test); the audit log
must not start carrying prompt text; `share/config.example.toml` must be regenerated from its
generator rather than edited.

**Scale/Scope**: one dataclass and two constants in `prompt.py`, one field and one selection in
`effects.py`, two call sites, one CLI flag, one audit detail key, one `describe()` entry, four
documentation pages, two milestone-001 spec documents, and the tests for all of it.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design — see the re-check at the end of
this section.*

| Principle | Verdict | Reasoning |
|---|---|---|
| **I. Simplicity First** | Pass, with one addition argued rather than assumed | The one thing that is not forced by the spec's behaviour is `prompt --effect-level` (FR-008a). It is justified by what this feature does to the preview: the preview's whole job is "what would this session be told?", and after this change the answer depends on a value the preview could not be asked about. Without the flag the only way to ask is to edit `config.toml`, which is a worse knob than the flag. It reuses the `--effect-level` that `run` and `serve` already define and the `effect_level=` parameter `build_context` already takes; nothing new is invented. Otherwise: two string constants, a four-line frozen dataclass, one field on an existing frozen dataclass, and one required argument on a function with two callers. No new module, no new dependency, no configuration knob — [research.md R7](research.md) records why a key to choose the form independently of the level would have exactly one correct setting. The value object over a bare string (R3) is justified by two consumers needing the form's *name* and one needing its *text*; the alternative puts a second copy of the selection in the audit call site. |
| **II. Single-User, Local-First** | Pass | Nothing networked, hosted, or multi-user. The change is prose handed to a local process, chosen by a local configuration value. It *reduces* what a run reaches for. |
| **III. Total Accountability** | Pass, and the record gets better | Composing a prompt is not an action outside the process and is not logged today; that is unchanged and is not a new gap — the form is a pure function of the effect level, which `daemon.start` already records, and every row below `live` already carries `dry_run`. Two existing records gain a field rather than a new record being invented: `daemon.start`'s `boundaries` map gains `delivery`, so the startup record answers "what will every session this daemon launches be told?", and `prompt.preview` gains `delivery` beside the `instructions` and `speckit` flags it already carries (FR-010). Neither gains prompt text, keeping the asymmetry milestone 020 chose: the rehearsal must not be recorded in more detail than the performance. |
| **IV. Interruption Tolerance** | Pass | No persistent write, no file, no network call, no retry. A kill during wiring leaves nothing partial; a kill during composition loses a string rebuilt from scratch next attempt. The one durable artefact is `share/config.example.toml`, a build product whose drift is already a failing test rather than a memory (`tests/unit/test_example_config_drift.py`). |
| **V. Public Code, Unsupported Project** | Pass | No credential, no personal data, no hostname; the new prose names no host and no account. `prompt.compose` gains a required argument and `prompt.DELIVERY` is replaced by two named forms — breaking changes for an importer, which this project explicitly does not maintain compatibility for, and which R4 argues are the point: a defaulted argument reinstates the bug for the next call site. |

**Post-design re-check**: unchanged. Phase 1 added no dependency, no configuration key, no
persistent state and no new audit action. The one design decision that grew during Phase 1 —
the sentence in R8 that reconciles the local form with push instructions composed above it —
adds prose, not machinery, and its precedence carve-out is scoped to outward writes and to the
form that only a below-`live` run can compose. It hands nothing to the issue's author, so RA-06's
reason for keeping `.claude/robot-army.md` above everything is untouched.

## Project Structure

### Documentation (this feature)

```text
specs/20260907-063858-effect-aware-delivery/
├── plan.md              # This file
├── spec.md              # Phase -1 output (/speckit-specify)
├── research.md          # Phase 0 output — R1..R10
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output — how this was verified
├── contracts/
│   └── delivery-forms.md   # Phase 1 output — the two forms, and the rules each must satisfy
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source code and documents touched

```text
src/robot_army/
├── prompt.py            # Delivery dataclass; DELIVERY_PUSH / DELIVERY_LOCAL; compose(delivery=…)
├── effects.py           # delivery selection at wire(); Boundaries.delivery; describe() entry
├── dispatch.py          # passes boundaries.delivery to compose()
├── operations.py        # preview passes ctx.boundaries.delivery; records delivery in the detail
├── cli.py               # `prompt --effect-level`, the override run and serve already accept
└── exampleconfig.py     # the effect_level comment stops promising containment

share/
└── config.example.toml  # regenerated, never hand-edited

docs/guide/
├── 1-setup.md           # the ladder's reach, and what it asks of the session
├── 4-session.md         # both delivery forms, and the instruction-above case
└── audit-log.md         # the two records that gained a field

specs/001-minimum-daemon/
├── contracts/boundaries.md  # what the level governs, and what it does not
└── quickstart.md            # scenario 3 retitled, and what it actually checks

tests/unit/
├── test_delivery_prompt.py      # parametrised over both forms; new local-only assertions
├── test_effects.py              # the selection, and the describe() entry
├── test_prompt_preview.py       # the preview composes the level's form, and records which
├── test_speckit_prompt.py       # golden assembly, which now has a form in it
└── test_speckit_dispatch_prompt.py
```

**Structure Decision**: no new module. Every change lands in a file that already owns the
concern: the two forms live beside the composer that emits them, the selection lives in the only
module permitted to know an effect level exists, and the documentation changes land on the pages
`CLAUDE.md`'s table already names for effect levels, the composed prompt, and a record's shape.

## Complexity Tracking

No Constitution Check violations. Nothing to justify.
