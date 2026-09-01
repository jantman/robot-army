# Phase 1: Data Model

Nothing is persisted. There is no migration, no new column, and no new table — this milestone's
"data" is three in-memory shapes parsed from the configuration file at start-up and one derived
answer computed per dispatch.

## Parsed shapes

### `SpecKitConfig` (extended, `config.py`)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | `bool` | `True` | Unchanged. Whether a detected Spec Kit worktree gets the block. |
| `commands` | `dict[str, str]` | `{}` | The global instruction for each lifecycle command. Only non-empty values are ever present — an empty one is refused at parse time (R5). Absent commands are absent keys. |

### `RepoConfig` (extended, `config.py`)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `speckit` | `bool \| None` | `None` | Unchanged. `None` inherits `[speckit] enabled`. |
| `speckit_commands` | `dict[str, str]` | `{}` | This repository's overrides. **An empty-string value is meaningful here**: it overrides the global instruction with nothing. An absent key inherits. |

`speckit_commands` follows `env`'s precedent — a mutable `dict` inside a frozen dataclass, via
`field(default_factory=dict)` — rather than inventing a second convention for the same shape.

### `CommandInstruction` (new, `config.py`)

One resolved instruction, and where it came from.

| Field | Type | Meaning |
|---|---|---|
| `command` | `str` | One of `specify`, `plan`, `tasks`, `implement`. |
| `text` | `str` | The effective instruction. Never empty — a command that resolves empty is omitted from the result entirely. |
| `source` | `str` | The setting that produced it, verbatim and quotable into a record: `[speckit.commands] implement` or `[repos."jantman/x".speckit_commands] implement`. |

Frozen, slots, like every other dataclass in `config.py`.

## Derived answer

### `Config.speckit_commands_for(repo_key) -> tuple[CommandInstruction, ...]`

Resolves all four commands for one repository and returns those that end up with text.

**Rules**, per command, in this order:

1. If the repository has a section and that section's `speckit_commands` contains this command, its
   value wins — **including when that value is the empty string**, which resolves the command to "no
   instruction" and drops it from the result.
2. Otherwise, if `[speckit.commands]` contains this command, its value is used.
3. Otherwise the command has no instruction and is absent from the result.

**Ordering**: the returned tuple is in `speckit.LIFECYCLE` order — `specify`, `plan`, `tasks`,
`implement` — regardless of the order either table used (FR-011). Sorting here rather than in the
renderer puts the guarantee at the one place it can be tested once.

**Never raises.** Every value in it was validated at parse time; a `Config` that exists has already
survived `ConfigError`.

**Never cached.** Derived per call, like `speckit_enabled_for`. There is nothing to cache but a
four-element tuple, and a cache would be a second answer about a file the maintainer edits by hand.

#### Resolution matrix

| `[speckit.commands]` | `[repos.*].speckit_commands` | Effective | `source` |
|---|---|---|---|
| absent | absent | none | — |
| `"G"` | absent | `"G"` | `[speckit.commands] <cmd>` |
| absent | `"R"` | `"R"` | `[repos."<key>".speckit_commands] <cmd>` |
| `"G"` | `"R"` | `"R"` | `[repos."<key>".speckit_commands] <cmd>` |
| `"G"` | `""` | none | — |
| absent | `""` | none | — |

The last row is legal and inert: overriding an absent global with nothing says the same thing as
saying nothing, but refusing it would mean the maintainer's file could not state "definitely nothing
here" ahead of a global instruction being added later.

## Validation rules

Applied at parse time, contributing to the aggregate `ConfigError` alongside every other problem in
the file — never aborting at the first (FR-006, FR-028).

| Condition | Where | Outcome |
|---|---|---|
| `commands` is not a table | `[speckit]` | problem: `[speckit] commands must be a table` |
| `speckit_commands` is not a table | `[repos.*]` | problem naming the repository |
| Key is not one of the four lifecycle commands | both | problem naming the key and listing the four |
| Value is not a string | both | problem naming the key and the value found |
| Value is empty or whitespace-only | `[speckit.commands]` **only** | problem — it says nothing that omitting it does not (R5) |
| Value is empty or whitespace-only | `[repos.*].speckit_commands` | **accepted** — means "no instruction here" |
| Value longer than `MAX_INSTRUCTION_CHARS` (4,000) | both | problem naming the limit and the length found |

`_KNOWN_KEYS["speckit"]` gains `commands`; `_REPO_KEYS` gains `speckit_commands`. Both sections
already treat an unknown key as a problem rather than a warning, so a typo like `[speckit.command]`
or `speckit_command` is refused by machinery that already exists.

**Text is otherwise uninterpreted.** Markdown, backticks, quotation marks, blank lines, and text
naming commands that do not exist are all carried through unexamined. The only properties checked are
structural — it is a string, it is not empty where empty would be meaningless, it is not absurdly
long, and it is keyed on a real command.

## What is deliberately not modelled

- **No persistence.** No SQLite column, no migration, no `speckit_instructions` on `WorkItem`. The
  instructions are resolved when a prompt is composed and then exist only inside that prompt.
- **No per-item record of the text.** See [research.md](./research.md#r6--what-the-audit-record-carries)
  and the Principle III gap enumerated in [plan.md](./plan.md#iii-total-accountability).
- **No effective-configuration object.** `speckit_commands_for` returns a tuple; there is no
  `EffectiveSpecKitConfig` aggregate wrapping it and `speckit_enabled_for` together. Two callers, two
  calls, no third shape to keep in step.
