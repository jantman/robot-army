# Contract: Configuration

Extends [milestone 007's configuration contract](../../007-speckit-extensions/contracts/config.md),
which this replaces nothing in. `[speckit] enabled` and `[repos.*] speckit` keep their exact meaning
and their exact resolution.

## `[speckit.commands]`

```toml
[speckit]
enabled = true                # unchanged

[speckit.commands]
specify = "When the specification is written, commit it to the branch before continuing."
plan = "When the plan is written, commit it to the branch before continuing."
tasks = "When the task list is written, commit it to the branch before continuing."
implement = """
when finished with implementation, commit, push the branch to origin, and open a PR. Once
that's done, monitor the CI jobs on the PR. Once all are complete, use /answer-reviews to
respond to any reviews. Repeat this until claude reviews with a comment of "No issues found.
Checked for bugs and CLAUDE.md compliance.".
"""
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `specify` | string | absent | What `/speckit-specify` is invoked with |
| `plan` | string | absent | What `/speckit-plan` is invoked with |
| `tasks` | string | absent | What `/speckit-tasks` is invoked with |
| `implement` | string | absent | What `/speckit-implement` is invoked with |

An absent `[speckit.commands]` table is exactly equivalent to the table being empty, which is exactly
equivalent to the state before this milestone (FR-004, FR-013).

**The text above is an example, not a default.** Nothing ships configured. The two paragraphs shown
are the ones issue #39 named, reproduced here so the shape is legible — they are the maintainer's
practice, not this project's opinion (FR-022).

## `[repos.*] speckit_commands`

```toml
[repos."jantman/other-repo"]
speckit = true                        # the existing boolean gate, unchanged

[repos."jantman/other-repo".speckit_commands]
implement = "when finished, commit and push the branch. Do not open a pull request here."
tasks = ""                            # no instruction for /speckit-tasks in this repository
```

| Value | Effect on that command, in that repository |
|---|---|
| a non-empty string | replaces the global instruction |
| `""` | **no instruction here** — the global one does not apply, and the block does not mention that command |
| absent | inherits `[speckit.commands]` |

**Why the key is not called `speckit`.** The repository section already has a `speckit` key holding
the boolean gate, and TOML cannot make one name both a boolean and a table. See
[research.md R1](../research.md#r1--where-the-configuration-sits-and-why-the-two-halves-cannot-share-a-name).

**Why an empty string is legal here and not globally.** Globally, empty and absent are the same
state, so an empty value says nothing and is reported as a mistake. Here they are different states —
absent inherits, empty overrides with nothing — so empty is the only way to drop one instruction in
one repository without `speckit = false` removing the whole guidance block. See
[research.md R5](../research.md#r5--empty-string-means-different-things-in-the-two-places).

The section remains what milestone 005 made it: a set of overrides for exceptions, not a
registration. A repository needs no section to receive the global instructions (FR-023).

## Validation

Every violation is a **problem**, collected with every other problem in the file and raised together
as one `ConfigError` — never an abort at the first, and never a warning.

| Condition | Message shape |
|---|---|
| `[speckit] commands` is not a table | `[speckit] commands must be a table` |
| `[repos.<key>] speckit_commands` is not a table | `[repos.<key>] speckit_commands must be a table` |
| unknown command name | `[speckit.commands] unknown command 'X'; valid commands are specify, plan, tasks, implement` (and the `[repos.<key>.speckit_commands]` equivalent) |
| value is not a string | `[speckit.commands] X must be a string, got <repr>` |
| value empty or whitespace-only, **globally** | `[speckit.commands] X is empty; omit the key instead` |
| value longer than 4,000 characters | `[speckit.commands] X is <n> characters; the limit is 4000` |

A typo in the table *name* is caught by machinery that already exists: `[speckit]` is in
`_STRICT_KEY_SECTIONS` and `[repos.*]` validates against `_REPO_KEYS`, so `[speckit.command]` and
`speckit_command` are both already unknown-key problems. `commands` is added to
`_KNOWN_KEYS["speckit"]` and `speckit_commands` to `_REPO_KEYS`.

### The limit

`MAX_INSTRUCTION_CHARS = 4000`, per command. Four of them add at most 16,000 characters to a prompt
whose issue body is already capped at 60,000 (`prompt.MAX_BODY_CHARS`) for the same reason: the
composed prompt is one `argv` entry. The number is not tuned for anything — it is generous for an
instruction and small enough that a pasted document is refused rather than dispatched.

## Resolution

```text
Config.speckit_commands_for(repo_key) -> tuple[CommandInstruction, ...]
```

Returns the commands that have an effective instruction, in `speckit.LIFECYCLE` order, each carrying
its text and the setting that produced it. Rules and the full matrix are in
[data-model.md](../data-model.md#configspeckit_commands_forrepo_key---tuplecommandinstruction-).

Shape follows `speckit_enabled_for` exactly — answer plus provenance from one function — for the
reason that function's own docstring gives: two callers need the provenance, and computing it
separately at each site is how they come to disagree. Here the two are the audit record and
`robot-army repos --json` (FR-026).

`source` strings are quotable verbatim into a record or a listing:

- `[speckit.commands] implement`
- `[repos."jantman/other-repo".speckit_commands] implement`

## Audit detail

The existing `speckit.detect` record, one per dispatch, gains one field:

| Field | Meaning |
|---|---|
| `instructions` | mapping of command name → the `source` string that supplied it; **absent when none resolved** |

Example:

```json
{"detected": true, "reason": "spec kit present (skills)", "enabled": true,
 "form": "skills", "path": "/home/jantman/worktrees/robot-army/issue-39",
 "instructions": {"specify": "[speckit.commands] specify",
                  "implement": "[repos.\"jantman/x\".speckit_commands] implement"}}
```

**The instruction text is deliberately not recorded.** This is the Principle III gap the plan
enumerates and justifies — the log does not reconstruct a composed prompt today (the issue body, the
repository's `.claude/robot-army.md` and the delivery block are all absent from it), and privileging
configured prose over the issue body sitting beside it is indefensible. The record names the exact
setting; the setting is in a local hand-edited file. See
[plan.md](../plan.md#iii-total-accountability).

## `robot-army repos`

The human table is **unchanged** — seven columns, and the Spec Kit cell keeps its four values
(`yes` / `no` / `off` / `?`), which answer "is this repository getting the block at all". That
question and its answers are untouched by this milestone.

The `--json` payload's existing `speckit` object gains the same `instructions` mapping as the audit
record, from the same `speckit_commands_for` call, so the two cannot disagree (FR-026, FR-027).

Reasoning for not adding an eighth column, and why provenance-plus-the-file is a complete offline
answer, is in [research.md R7](../research.md#r7--where-what-will-this-repository-be-told-is-answerable-offline).

## Doctor

`doctor` gains nothing, for the reason 007's contract already gives: there is no credential to check
and no remote to reach. A malformed instruction is a `ConfigError` at load, which every command
including `doctor` already surfaces; a *well-formed* instruction cannot be wrong, because whether the
prose is wise is not this project's business.
