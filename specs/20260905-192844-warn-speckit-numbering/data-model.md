# Data model: the numbering answer

No stored data. No table, no column, no migration, no file. Everything here is derived on each
`onboard` run and discarded with the process (FR-012), for the same reason `speckit.Detection` is:
a value cached at onboarding is exactly what would stop a repository that fixes its numbering from
being treated as fixed.

## `Numbering`

A frozen dataclass in `src/robot_army/speckit.py`, shaped after `Detection` in the same module —
a verdict, the evidence, and a sentence fit to print.

| Field | Type | Meaning |
|---|---|---|
| `kind` | `str` | `"timestamp"`, `"scanned"`, or `"unknown"`. The whole verdict |
| `value` | `str \| None` | What `feature_numbering` actually said, when it said something this system will quote back. `None` for absent, and for every `unknown` |
| `reason` | `str` | One sentence naming the evidence. Printed verbatim for `unknown`; available for the other two but not printed |

`kind` is three named outcomes rather than two booleans because three states cannot be encoded in
two flags without inventing a fourth combination that never occurs (research R2).

### The three kinds

| `kind` | Means | Warned? |
|---|---|---|
| `timestamp` | `feature_numbering` is exactly `"timestamp"` — directories are `YYYYMMDD-HHMMSS-<name>` and cannot collide between concurrent sessions | no |
| `scanned` | Anything else that is *legible*: a different recognised value, an unrecognised one, no `feature_numbering` key, or no `.specify/init-options.json` at all. Numbers come from scanning `specs/`, so two concurrent sessions can take the same one | yes |
| `unknown` | The file is there and cannot be trusted to say. Invalid JSON, not a JSON object, a `feature_numbering` that is not a plain identifier, too large to be that file, or unreadable | yes, differently |

**Absent is `scanned`, not `unknown`** (FR-002). Nothing missing is being reported: scanning is
precisely what Spec Kit does when no file says otherwise, so the system knows the answer.

## How the kind is decided

Ordered; the first rule that applies wins. Every step's failure is an outcome, never an exception
(FR-008).

1. **The file does not exist** → `scanned`, `value=None`. Spec Kit's default.
2. **The file cannot be opened or read** (`OSError` — permissions, a directory in its place, a
   dangling symlink) → `unknown`, `reason` naming the error.
3. **The file is larger than 64 KiB** → `unknown`. `init-options.json` holds seven short keys;
   something else is in that path, and it is not going to be parsed to find out what.
4. **The content is not valid JSON** → `unknown`, `reason` carrying the decoder's message. That
   message names a line and column and never quotes the input, so it is safe to print.
5. **The parsed content is not a JSON object** → `unknown`.
6. **There is no `feature_numbering` key** → `scanned`, `value=None`.
7. **The value is not a string, is longer than 32 characters, or contains anything outside
   `[A-Za-z0-9_.-]`** → `unknown`, and the value itself is *not* quoted into the reason
   (research R8).
8. **The value is `timestamp`** → `timestamp`, `value="timestamp"`.
9. **Otherwise** → `scanned`, `value=` the value.

Rule 7 is the one that would be easy to leave out. It exists because rule 9's value is echoed onto
the screen a human is using to decide whether to trust this repository, and that screen must not be
composable by the repository being decided about.

### `branch_numbering` is not a rule

The deprecated key is not consulted at all (research R6). A repository using it reaches rule 6 and
is reported as `scanned`, which is both true and the right advice.

## Where the answer goes

| Destination | Carries | Shape |
|---|---|---|
| Approval screen | `kind`, `value`, and `reason` when `unknown` | the block in [contracts/numbering-warning.md](contracts/numbering-warning.md) |
| `onboard --json` document | `speckit`, `speckit_numbering`, `speckit_numbering_value` | `bool`, `str \| null`, `str \| null` |
| `repo.onboard` audit detail | `speckit`, `speckit_numbering` | `bool`, `str \| null` |

The audit detail carries the verdict but not the value: it answers *what was the maintainer shown
when they approved*, and the verdict is that. The JSON document mirrors the screen, and the screen
names the value.

`null` for `speckit_numbering` means **not asked**, not "no answer" — the repository is not a Spec
Kit project, so nothing read the file. Detection gates the read for the same reason it gates phase
observation in `speckit.record_phase`: `.specify/` is not a rare enough name to carry meaning on
its own.
