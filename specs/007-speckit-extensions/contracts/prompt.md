# Contract: The Prompt Block

The one piece of user-visible prose this milestone ships. It is fixed text: it does not vary with
which command form was detected, which files were found, which repository it is, or which issue.
That is what makes FR-009's determinism a one-line test.

## Position

`prompt.compose()` currently assembles, in order:

```text
[.claude/robot-army.md, if present]
---
You are working on <repo> issue #<n> …
<the issue body>
```

The block is inserted as a third section, between them:

```text
[.claude/robot-army.md, if present]
---
[the Spec Kit block, if detected and enabled]
---
You are working on <repo> issue #<n> …
<the issue body>
```

Repository instructions stay first because position is how `prompt.py` already encodes precedence,
and the block's own last sentence says so explicitly rather than leaving it to be inferred.

## The text

> This repository uses Spec Kit for feature work. Its lifecycle is `/speckit-specify` →
> `/speckit-plan` → `/speckit-tasks` → `/speckit-implement`, run in that order, and the issue below
> is the input to `/speckit-specify` — hand it the issue rather than re-describing it.
>
> Use the lifecycle for work that adds or changes behaviour. Do not use it for a typo, a one-line
> fix, a dependency bump, a documentation edit, or a question — going through four phases for those
> costs more than it returns, and starting straight in is the right call. That judgement is yours;
> nothing checks it and nothing is recorded as failed if you decide this issue does not warrant the
> lifecycle.
>
> If this repository has a constitution at `.specify/memory/constitution.md`, it governs, and the
> plan must include its Constitution Check.
>
> Where any instruction above this paragraph conflicts with this one, the instruction above wins.

## Rules

| Rule | Requirement |
|---|---|
| Present only when detection succeeded **and** the behaviour is enabled for the repository | FR-007, FR-011 |
| ~~Identical text on every dispatch, in every repository~~ — **amended, see below** | FR-009 |
| Absent ⇒ the composed prompt is byte-identical to the pre-milestone output | FR-010 |
| Never states or implies that compliance is checked | FR-008 |
| Names commands, not files, so both installation forms are covered by one sentence | R1, R5 |

## The byte-identity test

`compose()` gains one optional keyword argument defaulting to `None`. With it `None`, the function
must return exactly what it returns today — asserted against a stored expected string built from a
fixture issue, not against a re-derivation of the same code. FR-010 is the requirement most likely to
be broken by an innocent refactor of the surrounding sections, and a golden string is what notices.

## Amendment: FR-009, by milestone 039

**FR-009 as written above is no longer true, and was superseded deliberately rather than
drifted away from.** [Milestone 039](../../20260901-064913-configurable-speckit-prompts/spec.md)
made the block carry the maintainer's own per-command instructions, configured in
`[speckit.commands]` and overridable per repository. The rule becomes:

> Identical text for a given **effective configuration** — across issues and repeated
> compositions, and between two repositories except where their configuration differs.

What survives unchanged is the guarantee this contract was actually written to protect: **an
installation that configures nothing gets these exact bytes.** The byte-identity test above
still holds and `tests/unit/test_speckit_prompt.py`'s `GOLDEN` still passes unedited — a
second test now guards the other direction, so a configured instruction leaking outside the
block is caught too.

The insertion point is **above** the closing precedence sentence, never after it, because
that sentence's scope is literally "any instruction above this paragraph" and it is how the
block defers to a repository's own `.claude/robot-army.md`. Text placed after it would sit
outside the precedence rule the block advertises. See
[`contracts/prompt-block.md`](../../20260901-064913-configurable-speckit-prompts/contracts/prompt-block.md).
