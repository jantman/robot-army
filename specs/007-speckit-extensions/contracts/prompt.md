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
| Identical text on every dispatch, in every repository | FR-009 |
| Absent ⇒ the composed prompt is byte-identical to the pre-milestone output | FR-010 |
| Never states or implies that compliance is checked | FR-008 |
| Names commands, not files, so both installation forms are covered by one sentence | R1, R5 |

## The byte-identity test

`compose()` gains one optional keyword argument defaulting to `None`. With it `None`, the function
must return exactly what it returns today — asserted against a stored expected string built from a
fixture issue, not against a re-derivation of the same code. FR-010 is the requirement most likely to
be broken by an innocent refactor of the surrounding sections, and a golden string is what notices.
