# Contract: the numbering block on the onboarding approval screen

What `robot-army onboard` prints, when, and what the machine-readable forms carry. The tests in
`tests/unit/test_speckit_numbering.py` and `tests/integration/test_onboard.py` are written against
this document, one per row.

## When the block appears

| Repository | `speckit.detect` | `speckit.numbering` | Block |
|---|---|---|---|
| No `.specify/` scaffolding | not detected | *not asked* | none |
| Scaffolding but a lifecycle command missing | not detected | *not asked* | none |
| Spec Kit, `"feature_numbering": "timestamp"` | detected | `timestamp` | none |
| Spec Kit, `"feature_numbering": "sequential"` | detected | `scanned` | **scanned block** |
| Spec Kit, `feature_numbering` absent | detected | `scanned` | **scanned block** |
| Spec Kit, no `init-options.json` at all | detected | `scanned` | **scanned block** |
| Spec Kit, unparseable / non-object / wild value | detected | `unknown` | **unknown block** |
| Onboarding refuses before the screen | *not asked* | *not asked* | none |

**Detection gates the read.** A directory containing `.specify/init-options.json` and nothing else
is never read from — the block belongs to repositories this system will actually send Spec Kit
guidance to.

## Where it appears

Immediately before `result.flush_to(out)` in `operations.onboard`, after the committed-settings
block and after any re-approval fingerprint diff. That position is fixed by two things at once:
the block must reach the maintainer *before* the approval prompt (FR-009), which is what the single
flush point guarantees; and it must not push the committed permission settings further from the top
of the screen, which is the text that most needs reading.

`operations.onboard` has exactly one flush point on purpose. This block adds lines above it; it
does not add a second one.

## The scanned block

Configured to something other than `timestamp`:

```text
spec kit: this repository numbers feature directories by scanning
  feature_numbering is "sequential" in .specify/init-options.json.
  Two sessions running at once scan the same specs/ and cannot see each other's
  worktrees, so both can claim the same number. Nothing here prevents that.
  Set "feature_numbering": "timestamp" in that file to number by time instead.
```

Configured nowhere — no key, or no file:

```text
spec kit: this repository numbers feature directories by scanning
  feature_numbering is not set in .specify/init-options.json, and scanning is the
  default.
  Two sessions running at once scan the same specs/ and cannot see each other's
  worktrees, so both can claim the same number. Nothing here prevents that.
  Set "feature_numbering": "timestamp" in that file to number by time instead.
```

The two differ in exactly one sentence, because "change this setting" and "add this setting" are
different instructions to the person who has to carry them out.

"Nothing here prevents that" is deliberate and load-bearing. Issue #41 established that no check
this system could perform would close the race — the competing number exists only as untracked
files in a sibling worktree — and a warning that left the reader expecting the daemon to catch it
would be worse than none.

## The unknown block

```text
spec kit: the feature numbering could not be determined
  .specify/init-options.json: not a JSON object.
  If it does not say "timestamp", two sessions running at once can claim the same
  feature number. Set "feature_numbering": "timestamp" to be sure.
```

The second line is the `Numbering.reason`, printed verbatim. It is one of:

| Situation | `reason` |
|---|---|
| `OSError` on open or read | `could not be read: <the OS error>` |
| Larger than 64 KiB | `too large to be a spec kit options file` |
| Invalid JSON | `invalid JSON: <the decoder's message>` |
| Not a JSON object | `not a JSON object` |
| `feature_numbering` is not a plain identifier | `feature_numbering is not a plain value` |

### The value is never trusted to be text

The `"sequential"` in the scanned block comes out of the repository being approved, and the screen
it lands on is what a human uses to decide whether to trust that repository. So it is quoted back
only after it has been confirmed to be a string of at most 32 characters matching
`[A-Za-z0-9_.-]+`. A value with a newline in it could otherwise add lines to the screen; a
100 KB one could push the committed permission settings out of scrollback.

The match is a `fullmatch` against a pattern with no anchors of its own. An anchored
`re.match(r"^...$", value)` would *not* be equivalent: Python's `$` matches immediately before
one trailing newline, so `"sequential\n"` passes it. That escape was found in review of
PR #145 and is now a test in its own right.

A value failing that test produces the **unknown** block, and the value does not appear in the
`reason`. That is the honest classification as well as the safe one: a file this system cannot
make sense of is one whose numbering it does not know.

## The block never changes an outcome

- The exit code is whatever it would have been without the block.
- The approval prompt is unchanged, in wording and in behaviour.
- `--yes` behaves identically. The block is not "unapproved settings" and does not interact with
  that refusal.
- No refusal is introduced, and no existing refusal is suppressed.
- Nothing is written to the onboarded repository, ever.

## `onboard --json`

Three keys, added to the existing document. Present on every run that reaches the screen — that is,
every run that is not an early refusal.

| Key | Type | Value |
|---|---|---|
| `speckit` | `bool` | whether the clone is detected as a Spec Kit project |
| `speckit_numbering` | `str \| null` | `"timestamp"`, `"scanned"`, `"unknown"`, or `null` when `speckit` is `false` |
| `speckit_numbering_value` | `str \| null` | the configured value when there is one this system will quote; `null` otherwise |

No warning prose appears in the document. `--json` passes `out=None` for exactly that reason and
this feature does not change it.

## `repo.onboard` audit detail

Two keys, added to the detail the action already carries:

| Key | Type | Value |
|---|---|---|
| `speckit` | `bool` | as above |
| `speckit_numbering` | `str \| null` | as above |

Written only on the success path, alongside `clone_path`, `verified_origin` and the rest — the
record of *what was approved*. A refusal records its `cause` and does not reach the read.

```bash
jq -r 'select(.action == "repo.onboard" and .detail.speckit_numbering == "scanned")
       | "\(.ts) \(.entity_id)"' \
  ~/.local/state/robot-army/logs/audit-*.jsonl
```

## What is deliberately not logged

The `stat` and the read of `.specify/init-options.json`. Same exception, and same reasoning, as the
Spec Kit detection reads the guide already lists: they change no state outside the process, and the
decision they inform is recorded on the `repo.onboard` line. The exception is named in
[plan.md](../plan.md) under Principle III, as the constitution requires, and
[`docs/guide/audit-log.md`](../../../docs/guide/audit-log.md) is extended to cover it.
