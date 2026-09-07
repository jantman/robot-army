# Research: The delivery block follows the effect level

Everything here was settled against the code as it stands on `main`, not against a memory of
it. Where a decision looks over-thought for the size of the change, it is because the prose is
the deliverable — nothing enforces it — and because this system already has one test file
(`tests/unit/test_delivery_prompt.py`) whose entire job is to hold that prose to its reasons.

## R1 — Which levels get which form

**Decision**: `live` gets the push-and-pull-request form; `plan`, `local` and `no-remote` all
get the local-only form. Two forms, one rule, and the rule is the same one every outward-facing
boundary already follows (`issue_writer`, `card_writer`, `notifier`: real only at `live`).

**Rationale**: the ladder's own shape answers this. A push and a pull request are writes to the
same remote whose comment writes are simulated below `live`; asking the session to make them
while robot-army refuses to make its own is the incoherence the issue reported. Aligning with
`REAL_AT["issue_writer"]` means the sentence "below `live`, nothing this system does or asks
for reaches GitHub" becomes true with no carve-outs.

**Alternatives considered**:

- *Special-case `no-remote` only, since `plan` and `local` never launch a session.* True today,
  and it would make the preview lie at exactly the two levels where the preview is the only
  output there is. A rule with a footnote about which levels have sessions is a second thing to
  remember for no gain.
- *A third, `plan`-specific form.* Nothing to say that the local-only form does not already
  say, and a form no dispatch can ever compose is dead prose.

## R2 — Where the choice between the two forms is made

**Decision**: in `effects.wire()`. The chosen form is carried on `Boundaries` alongside the
wired implementations, and `prompt.compose()` receives it as an argument. Neither `prompt.py`
nor `dispatch.py` learns that an effect level exists.

**Rationale**: this is FR-009, and it is not a stylistic preference — it is enforced by
`tests/unit/test_effects.py::test_no_module_outside_the_allowed_set_names_the_effect_level`,
whose allowlist is `config.py`, `cli.py`, `daemon.py`, `operations.py`, `web/server.py` and
`effects.py`. Writing `boundaries.level is EffectLevel.LIVE` in `dispatch.py`, or taking a
`level` parameter in `prompt.compose`, fails that test. The test is right: this is precisely
the "scattered `if dry_run:`" that FR-053 exists to prevent, and the effect ladder's guarantee
is structural only for as long as the selection stays in one place.

Both call sites already hold a `Boundaries`: `dispatch.build_launch_plan` takes one as a
parameter, and `operations.prompt_preview` reaches `ctx.boundaries`. Nothing has to be threaded
anywhere new.

**Alternatives considered**:

- *`prompt.compose(level=...)`.* Fails the allowlist test, and puts the ladder into the module
  furthest from the wiring.
- *A module-level `effects.delivery_for(level)` called at each site.* Same failure: the caller
  still has to hold a level to pass it.
- *A configuration key.* R7.

## R3 — A small value object rather than a bare string

**Decision**: `prompt.Delivery`, a frozen dataclass of `name` and `text`, with two module-level
instances: `DELIVERY_PUSH` and `DELIVERY_LOCAL`. `Boundaries.delivery` holds one of the two.

**Rationale**: two consumers need the *name* and one needs the *text*. FR-010 wants the
`prompt.preview` record to say which form was composed, and `daemon.start` already records
`boundaries.describe()`, which is the natural place for a reader to learn what every session
this daemon dispatches will be told. A bare string would force one of those consumers to map
text back to a name — a second copy of the selection, in the worst possible place.

`Delivery.describe_name()` costs one line and makes `describe()` include the form through the
hook `_describe_one` already has for `MultiNotifier`; no new mechanism, and the startup record
gains `"delivery": "push"` or `"delivery": "local"`.

**Alternatives considered**:

- *Two fields on `Boundaries` (`delivery_text`, `delivery_name`).* The same data with a way to
  set them inconsistently.
- *A `StrEnum` of form names, mapped to text in `prompt.py`.* The mapping is a branch, and it
  would sit in the module that must not know how the choice is made.

## R4 — `compose(delivery=...)` is required, not defaulted

**Decision**: no default. Every caller passes a form.

**Rationale**: a default of `DELIVERY_PUSH` is the bug this feature exists to fix, installed as
the behaviour a future call site gets for free. There are two call sites and one test module
that constructs prompts directly; making the parameter required means a third call site cannot
silently instruct a `no-remote` session to push. The constitution's "no backward compatibility
for outside consumers" (Principle V) makes this cost nothing.

## R5 — What the local-only form actually says

**Decision**: it keeps the shape and the register of the existing block, and changes the middle:
the work is committed on the feature branch and stops there; the session must not push, must not
open a pull request, and must not comment on the issue or write to any other remote system; the
commits in the worktree are the finished job, which is where they will be read.

**Rationale**: the existing block's second paragraph justifies pushing by saying an unpushed
branch can be lost to a reclaimed worktree. That reason does not disappear below `live` — it is
simply outranked by the operator having asked for a contained run, and the local form says where
the work does end up instead so the session is not left inferring it (FR-005).

The prohibition names the issue comment as well as the push, because robot-army's own comments
are simulated below `live` and a session that comments instead is the same leak with a different
author. "Any other remote system" catches the rest without a list — an exception list is how you
know a rule is drawn in the wrong place, and this project has the scar (milestone 012, D3).

The scope paragraph is kept and re-aimed: build, run, test, install dependencies, start things
locally, and read whatever is needed *including live systems*, because reads are real at every
level (FR-052) and a form that forbade reading would misdescribe the ladder in the other
direction.

## R6 — Everything RA-06 added is kept in both forms

**Decision**: both forms open by naming whose rules they are, both keep "when there is work to
deliver" rather than "when the work is done", both close with the paragraph that asserts
precedence over the issue text however it is worded, and neither names an override the issue
could ask for.

**Rationale**: those properties are about the untrusted region below the block, and the effect
level has nothing to do with them. `tests/unit/test_delivery_prompt.py` holds each of them
today against `prompt.DELIVERY`; the plan is to parametrise that file over both forms so the
local one cannot drift into the shape the fenced-issue milestone spent a spec removing.

## R7 — No configuration key

**Decision**: none. The effect level is the only input.

**Rationale**: Principle I, and one caller. A key that let an operator ask for push-and-PR at
`no-remote` would be a knob whose only setting is the bug, and a key that let them ask for
local-only at `live` is `--effect-level no-remote`.

## R8 — The contradiction this feature has to answer: instructions above that say "push"

**Decision**: the local-only form states, in one sentence, that where an instruction above it
asks for a push, a pull request or any other outward write, that instruction describes a `live`
dispatch and does not apply to this run. It carves out nothing else from those instructions.

**Rationale**: this is not hypothetical, and it is not about `.claude/robot-army.md` in the
abstract. This repository's own configuration sets

```toml
[speckit]
implement = "when finished with implementation, commit, push the branch to origin, and open a PR…"
```

which is composed into the Spec Kit block — **above** the delivery block, where position gives
it precedence. Without this sentence, a `no-remote` dispatch of any Spec Kit issue in this
repository would still be told to push, by the operator's own words, and SC-001 would fail on
the very repository the bug was found in.

The carve-out is narrow and it is defensible in exactly one direction: a standing file or a
configured string cannot know which level today's run is at, and the effect level *is* the
operator's per-run containment decision. Nothing about it hands anything to the issue's author —
the level is not reachable from inside the fence — so RA-06's reason for putting
`.claude/robot-army.md` above everything (an exception channel the issue author does not
control) is untouched. The sentence appears only in the local-only form, so a `live` dispatch's
prompt is byte-for-byte what it is today (SC-003).

**Alternatives considered**:

- *Leave the contradiction and document it.* Honest, and useless: the guide would have to say
  "if you run at `no-remote`, first edit your Spec Kit instructions", which is a manual step
  standing exactly where the bug was.
- *Suppress or rewrite the configured Spec Kit instructions below `live`.* Editing the
  operator's own words is worse than adding one sentence that says which of them apply.
- *Move the delivery block above the Spec Kit block.* It would change what happens at `live`
  and undo milestone 012's D2 reasoning, to fix a case that only arises below `live`.

## R9 — What this feature does not attempt

**Decision**: the session keeps its credentials, its network and its shell. No sandbox, no
credential-less environment, no post-hoc detection of a push that happened anyway.

**Rationale**: option 3 in the issue, and the issue's own assessment is right — the session is
meant to be a real interactive session with the maintainer's tooling. The honest consequence is
that the reduced-level instruction is an instruction, and every document this feature touches
says so rather than implying a boundary that is not there. That statement is a deliverable of
this feature, not a disclaimer attached to it.

## R10 — What this logs, and what happens if it is killed halfway

**Logging**: composing a prompt is not an action outside the process and is not logged today;
this feature does not change that. It *adds* two facts to records that already exist —
`daemon.start` gains `boundaries["delivery"]`, and `prompt.preview` gains `delivery` alongside
the `instructions` and `speckit` flags it already carries. Neither record gains any prompt text,
which keeps the asymmetry milestone 020 chose deliberately (the rehearsal must not be recorded
in more detail than the performance). No new gap in the record is opened: the form is a pure
function of the effect level, which `daemon.start` already records, and every row created below
`live` already carries `dry_run`.

**Interruption**: nothing here writes to persistent state, opens a file, or makes a network
call. A kill during `wire()` leaves no partial anything; a kill during `compose()` loses a
string that is rebuilt from scratch on the next attempt, which is already true of every prompt
this system has composed. The one durable artefact in the neighbourhood — the regenerated
`share/config.example.toml` — is a build product of a committed generator, and a half-written
copy is caught by `tests/unit/test_example_config_drift.py` rather than by anyone's memory.
