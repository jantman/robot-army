# Contract: The Rendered Block

Extends [milestone 007's prompt contract](../../007-speckit-extensions/contracts/prompt.md). The
block's position inside `prompt.compose` is unchanged — repository instructions, then this block,
then the delivery block, then the issue.

## Position inside the block

`GUIDANCE` is four paragraphs. Configured instructions are inserted between the third and the
fourth, so the closing precedence sentence stays last:

```text
This repository uses Spec Kit for feature work. …            ← unchanged
Use the lifecycle for work that adds or changes behaviour. …  ← unchanged
If this repository has a constitution … Constitution Check.   ← unchanged

    ← the configured instructions go here

Where any instruction above this paragraph conflicts with this one, the instruction above wins.
```

**This is not a formatting preference.** That closing sentence's scope is literally "any instruction
above this paragraph", and it is how the block defers to a repository's own `.claude/robot-army.md`,
which `prompt.compose` places above it. Text appended *after* it would sit outside the precedence
rule the block advertises, and FR-015's guarantee would be false by construction with every test
still passing. See [research.md R4](../research.md#r4--where-the-instructions-render-inside-the-block).

A consequence worth stating rather than discovering: with the instructions above that sentence, the
sentence now also makes the **maintainer's configured text outrank the block's own generic
paragraphs**. That is the correct precedence and the reason no new wording is needed to establish it.

## The rendered text

When at least one instruction resolves, this is inserted — one lead-in sentence, then one block per
command, in `specify`, `plan`, `tasks`, `implement` order:

```text
When you run these commands, invoke each with the instruction given for it below — in
addition to, not instead of, any input named for it above.

`/speckit-specify`:

<the configured text, verbatim>

`/speckit-implement`:

<the configured text, verbatim>
```

| Element | Rule |
|---|---|
| The lead-in | Fixed text. Appears once, only when at least one instruction resolved. |
| "in addition to, not instead of, any input named for it above" | This is FR-012. It is what keeps a configured `specify` instruction from reading as a replacement for "the issue below is the input to `/speckit-specify`". One sentence, covering all four commands, rather than a special case for one. |
| Command heading | `` `/speckit-<command>`: `` on its own line, then a blank line. |
| The text | Verbatim. Not wrapped, not indented, not escaped, not bulleted, not truncated (FR-009). |
| Separation | One blank line between every element, so multi-paragraph instructions read correctly. |
| Unconfigured commands | Absent entirely. No heading, no placeholder, no "none" (FR-010). |

Instructions are **not** rendered as a bulleted list. A `-` prefix would break any instruction longer
than one paragraph, and the issue's own implement paragraph is four sentences.

## Absence is byte-identical

With no instruction resolving, the block is `GUIDANCE` unchanged — the same object, not a
reconstruction of it (FR-013).

That is guaranteed by construction rather than by care: `GUIDANCE` is defined as
`GUIDANCE_BODY + "\n\n" + GUIDANCE_CLOSING`, and `guidance(())` returns `GUIDANCE` itself. Splitting
the constant at render time by string-slicing off the final paragraph would make FR-013 depend on a
`rstrip` staying correct, which is exactly the class of accident
`tests/unit/test_speckit_prompt.py`'s golden string exists to catch.

## Interface

```text
speckit.guidance(instructions: Sequence[CommandInstruction] = ()) -> str
```

- Pure. No I/O, no configuration awareness — `speckit.py` must never import `config`, which is what
  keeps `config → speckit` acyclic (research R2). The caller passes already-resolved instructions.
- Deterministic: same instructions in, same string out (FR-014).
- `GUIDANCE` remains a module constant and keeps its current value, so anything importing it
  continues to work and the golden test keeps its meaning.

`dispatch.speckit_block` calls `config.speckit_commands_for(repo_key)` alongside the
`speckit_enabled_for` call it already makes, and returns `speckit.guidance(instructions)` in place of
`speckit.GUIDANCE`. Both are inside the existing `try` and the existing gate: when detection fails or
the block is suppressed, the return is `None` and no configured text reaches the session (FR-005, and
User Story 1 scenario 4).

## Worked example

Configuration:

```toml
[speckit.commands]
specify = "When the specification is written, commit it to the branch before continuing."
implement = "when finished with implementation, commit, push the branch to origin, and open a PR."
```

Rendered block:

```text
This repository uses Spec Kit for feature work. Its lifecycle is `/speckit-specify` →
`/speckit-plan` → `/speckit-tasks` → `/speckit-implement`, run in that order, and the
issue below is the input to `/speckit-specify` — hand it the issue rather than
re-describing it.

Use the lifecycle for work that adds or changes behaviour. Do not use it for a typo, a
one-line fix, a dependency bump, a documentation edit, or a question — going through four
phases for those costs more than it returns, and starting straight in is the right call.
That judgement is yours; nothing checks it and nothing is recorded as failed if you decide
this issue does not warrant the lifecycle.

If this repository has a constitution at `.specify/memory/constitution.md`, it governs, and
the plan must include its Constitution Check.

When you run these commands, invoke each with the instruction given for it below — in
addition to, not instead of, any input named for it above.

`/speckit-specify`:

When the specification is written, commit it to the branch before continuing.

`/speckit-implement`:

when finished with implementation, commit, push the branch to origin, and open a PR.

Where any instruction above this paragraph conflicts with this one, the instruction above wins.
```

`/speckit-plan` and `/speckit-tasks` are absent, which is the whole of FR-010.

## Rules

| Rule | Requirement |
|---|---|
| Present only when detection succeeded **and** the behaviour is enabled for the repository | FR-005, and 007's FR-007/FR-011, unchanged |
| Identical for a given effective configuration; differs between repositories only where their configuration differs | FR-014 — **amends 007's FR-009** |
| Nothing configured ⇒ byte-identical to `GUIDANCE` | FR-013 |
| Configured text carried verbatim | FR-009 |
| Lifecycle order regardless of file order | FR-011 |
| Unconfigured commands leave no trace | FR-010 |
| Closing precedence sentence stays last | FR-015 |
| Never states or implies that compliance is checked | FR-016, and 007's FR-008 |

## Recording the amendment to 007

FR-014 requires that 007's FR-009 — "identical text on every dispatch, in every repository" — not be
left quietly false. `specs/007-speckit-extensions/contracts/prompt.md` gains a short note under
**Rules** pointing here: the text is now fixed *per effective configuration*, and the byte-identity
test in that contract still holds for an installation that configures nothing.

This is the same courtesy milestone 012 paid when it superseded 007's FR-010, recorded in
`tests/unit/test_speckit_prompt.py`'s module docstring rather than left for a reader to discover from
a changed expected value.
