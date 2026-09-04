# Implementation Plan: Fence untrusted issue text, and stop the prompt handing it authority

**Branch**: `speckit/20260904-093845-fence-untrusted-issue-text` | **Date**: 2026-09-04 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260904-093845-fence-untrusted-issue-text/spec.md`

## Summary

One module changes: `src/robot_army/prompt.py`. One paragraph of prose is deleted, two are
rewritten, one function is added, and the issue section gains a fence. Then the three test files
that hold the prompt to its text are updated, and one README passage that now describes the old
behaviour is corrected.

No new module, no new dependency (`secrets` is stdlib), no configuration key, no CLI flag, no
database column, no migration, no network call, and no change to any call site: `compose`'s
signature is untouched, so `dispatch.build_launch_plan` and `operations.prompt_preview` are not
edited at all.

Three decisions carry the shape of it:

- **The boundary is a random nonce, generated inside `compose` and reachable by no caller.** A
  fixed marker in a public repository is one an issue body can write out; a parameter would put
  the one value that must not be guessable within reach of a future call site
  ([R1](research.md#r1-the-fence-delimiter-is-a-per-compose-random-nonce-not-a-fixed-marker),
  [R2](research.md#r2-the-nonce-is-generated-inside-compose-with-no-parameter-for-a-caller-to-supply)).
- **The override paragraph is deleted with nothing put in its place.** No CLI flag, no config
  key, no narrowed exception. The channel that already outranks everything is
  `.claude/robot-army.md`, by position, and building a second one for a need nobody has yet is
  the knob Principle I forbids
  ([R5](research.md#r5-deliverys-last-paragraph-is-removed-not-replaced-with-a-narrower-override)).
- **The fence is unbreakable by construction, not by probability.** Every occurrence of the
  nonce is stripped from the fenced payload before the markers go on, so FR-003 is a property of
  the code rather than a statement about 64 bits
  ([R3](research.md#r3-the-fence-cannot-be-closed-from-inside-by-construction)).

Two things are recorded rather than quietly done. `docs/security-analysis.md` is **not** edited
— it is a dated report, and the RA-01 and RA-05 fixes did not amend it either
([R12](research.md#r12-docssecurity-analysismd-is-not-edited)). And the `DELIVERY` size budget
moves from 1,500 to 1,800 characters, because the rewritten opening and closing have to do work
the deleted paragraph was doing for free
([R13](research.md#r13-the-delivery-size-budget-moves-from-1500-to-1800-characters)).

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`).

**Primary Dependencies**: none added. `secrets` and `re` are stdlib; `re` is already imported by
`prompt.py`. `httpx` remains the only runtime dependency and is not reached by this feature.

**Storage**: none. `prompt.compose` is a pure function of its arguments and touches no file,
no database and no socket. **No migration.**

**Testing**: `pytest`. Three existing unit files are updated
(`tests/unit/test_speckit_prompt.py`, `tests/unit/test_delivery_prompt.py`,
`tests/unit/test_prompt_preview.py`), one existing integration file is updated
(`tests/integration/test_prompt_preview_matches_dispatch.py`), and one new unit file is added
(`tests/unit/test_prompt_fence.py`). `uv run pytest`, `uv run ruff check`.

**Target Platform**: one Linux machine with a shell.

**Project Type**: single-process CLI daemon with a read-only-plus-controls web view.

**Performance Goals**: composition already walks the body twice (a `strip` and a length check).
It now walks it three more times — one regex substitution, one `replace` for the nonce, one
whitespace collapse on the title, which is short. On a 60,000-character body that is a few
hundred microseconds, paid once per dispatch and once per `robot-army prompt`. Nothing here runs
per tick or per page render.

**Constraints**: `compose`'s signature does not change, so no call site is touched and the
preview cannot drift from the dispatch by construction. `prompt.py` stays free of imports from
anything but `boundaries`, so the module keeps its position at the bottom of the import graph.
The output must stay one `argv` entry, which `MAX_BODY_CHARS` already guarantees and which
sanitising-before-truncating preserves.

**Scale/Scope**: one author, a handful of repositories, one composed prompt per dispatch.
Roughly 90 lines of source changed or added, and roughly 300 lines of tests.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design. No violations, so
**Complexity Tracking** below is empty.*

### I. Simplicity First (YAGNI & KISS)

**Pass.** No new module, no abstraction layer, no strategy interface. Two module-level helpers
(`sanitize`, `_fence_nonce`) are added and each has a caller today. Deliberately *not* built:
an exception channel to replace the deleted paragraph
([R5](research.md#r5-deliverys-last-paragraph-is-removed-not-replaced-with-a-narrower-override)),
a `nonce=` parameter on `compose`
([R2](research.md#r2-the-nonce-is-generated-inside-compose-with-no-parameter-for-a-caller-to-supply)),
and a configurable fence marker. No new third-party dependency; `secrets` is stdlib and is the
obvious source of an unguessable token.

### II. Single-User, Local-First

**Pass.** No accounts, no authorization, no hosted anything. The trust boundary this feature
draws is *inside a prompt*, between the operator's words and an issue author's — which is not a
user model, it is a data-labelling decision about text.

### III. Total Accountability

**Pass, with one omission named as the constitution requires.** No action that changes state
outside the process is added, so no new audit record is added either.

- **What this logs: nothing new.** `dispatch` already records the launch that carries the
  prompt, and `operations.prompt_preview` already records `prompt.preview` with the sections
  present but not their contents.
- **The nonce is deliberately not recorded.** It is not an action, it changes no state, and it
  is reconstructible from the prompt itself for anyone who has the prompt. Recording it would
  mean writing part of a prompt into a log that has never held one — the asymmetry
  `operations.py` already refuses.
- **The composed prompt is still not logged**, unchanged from today and for the same reason
  (`README.md`: "The log has never reconstructed a composed prompt").
- **Sanitisation removes characters silently.** This is not a swallowed failure: an issue body
  containing a NUL is not an error condition, it is input, and there is no outcome to report.
  The removal is total and uniform, so a reader of the prompt sees exactly what the session saw.

### IV. Interruption Tolerance

**Pass, vacuously and worth saying so.** **What happens if it is killed halfway through:**
nothing. `compose` is a pure function that writes nothing; a process killed inside it leaves no
partial file, no half-written row and no lock. The prompt is rebuilt from the issue on the next
attempt. No timeout, retry or checkpoint is needed because there is no I/O to interrupt.

### V. Public Code, Unsupported Project

**Pass.** No credentials, hostnames or personal data are added. The fence marker's *shape* is
public — necessarily, since the repository is — and the security rests on the per-dispatch
random component, not on the shape being secret. `compose`'s signature is unchanged, so nothing
outside the repository could break even if anything outside the repository existed.

### Development Workflow

**Pass.** Unit tests ship with the change and are the substance of it: this module parses
external input, so the constitution requires failure-path tests specifically, and
`tests/unit/test_prompt_fence.py` is exactly that — control characters, a body that forges the
section separator, a body that forges the marker shape, an empty body, an over-long body.

## Project Structure

### Documentation (this feature)

```text
specs/20260904-093845-fence-untrusted-issue-text/
├── plan.md              # This file
├── research.md          # Phase 0: R1–R13
├── data-model.md        # Phase 1: the prompt's sections and their trust levels
├── quickstart.md        # Phase 1: how to see and verify the change
├── contracts/
│   └── prompt.md        # Phase 1: the exact text, and how the fence is built
├── checklists/
│   └── requirements.md
├── spec.md
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
src/robot_army/
└── prompt.py            # the only source file this feature changes

tests/
├── unit/
│   ├── test_prompt_fence.py            # NEW — fencing, sanitisation, truncation
│   ├── test_delivery_prompt.py         # the override tests invert; the rest stands
│   ├── test_speckit_prompt.py          # GOLDEN takes its new value
│   └── test_prompt_preview.py          # the truncation assertion loses its URL
└── integration/
    └── test_prompt_preview_matches_dispatch.py   # pins the nonce, keeps byte equality

README.md                # "What every session is told" — one passage
```

**Structure Decision**: unchanged single-project layout. The feature lives entirely in
`prompt.py` because that is where the prompt is assembled, and the whole point of the existing
design — one composition function that both the dispatcher and the preview call — is what makes
this a one-file change.

## Phase 1 design notes

The full text is in [contracts/prompt.md](contracts/prompt.md); the section trust levels are in
[data-model.md](data-model.md). Two points from the design worth surfacing here:

- **The existing `---` between the header lines and the body is removed.** The fence is the
  separator now, and leaving a second one in place would give an issue body a structural cue to
  imitate for no benefit.
- **The test that holds the preview equal to a dispatch keeps asserting string equality**,
  with `prompt._fence_nonce` pinned for the test
  ([R11](research.md#r11-the-preview-keeps-comparing-byte-for-byte-against-a-dispatch)). The
  claim that test exists to make — the preview *is* the prompt, not something resembling it —
  is the claim a fuzzy comparison would give up.

## Complexity Tracking

No Constitution Check violations. Nothing to justify.
