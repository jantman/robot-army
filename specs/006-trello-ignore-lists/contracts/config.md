# Contract: Configuration Addition

One new key in a section that already exists. No new section, and nothing outside `[trello]` changes.

```toml
[trello]
board_id         = "5f3a..."
label            = "AI-task"
in_progress_list = "In Progress"
done_list        = "Done"

# NEW. Columns whose cards are not intake. Empty by default, so an installation that
# does not write this line behaves exactly as milestone 003 did.
ignore_lists     = ["Icebox", "Blocked", "Someday"]
```

## What it means

A card carrying the tag **and sitting in one of these columns** is not intake: no issue is created,
nothing is written to the card, and it is not surfaced as awaiting clarification. Moving it out makes
it intake again on the next poll, with no re-tag, no re-scan and no restart.

It gates **intake only**. A card that already has a recorded issue is unaffected in either direction
— its mapping survives, its session continues, and its lifecycle moves still happen. That is what
makes listing `in_progress_list` or `done_list` here a harmless no-op rather than a contradiction:
by the time the daemon puts a card in either, the card is `linked` and the ignore list no longer
applies to it.

## Validation, at load

Following the existing rule that a typo inside `[trello]` is an **error** rather than a warning,
because a board that quietly polls the wrong thing looks healthy:

| Rule | Message |
|---|---|
| Must be a list of strings | `[trello] ignore_lists must be a list of strings` |
| No empty entry | `[trello] ignore_lists contains an empty column name` |
| Duplicates are accepted and collapsed | — (`dict.fromkeys`, as `[notifications] events` already does) |
| `ignore_lists` is a **recognised** key | it joins `_SECTION_KEYS["trello"]`, so writing it is not itself an unknown-key error |
| A value that looks like a literal credential | the `[trello]` sweep, **extended** to look inside lists — it tested `isinstance(value, str)` and stopped, so this section's first list-valued key arrived as a hole in the choke point rather than a key it covered. One problem per key, however many elements carry a secret |

The loader makes no network call, so **existence on the board is not checked here**.

## Validation, at startup and by `doctor`

One check per configured name, appended after the tag and lifecycle-column checks, using the same
`_present()` helper — so the message already names what is missing and lists what the board actually
has:

```
$ robot-army doctor
  ...
  [ok]   tag exists                 'AI-task' found
  [ok]   in-progress list exists    'In Progress' found
  [ok]   done list exists           'Done' found
  [ok]   ignored list exists        'Icebox' found
  [FAIL] ignored list exists        'Blocked' not found — the board has:
                                    Doing, Done, Icebox, Inbox, In Progress
```

A failing check makes `BoardStatus.ok` false, which `poll_board` already gates on. The consequence is
therefore **ingestion is refused; dispatch is not** — issues the author wrote themselves continue to
be polled and dispatched exactly as before (FR-018). This is the same blast radius as a missing tag
or a missing lifecycle column.

**Why a refusal rather than a warning.** Every other check in this section refuses, and the failure
mode here is the one that hides: a warning would leave intake silently widened back to milestone
003's, the icebox would file issues, and nothing would look broken. A warning that widens what the
system acts on is not a warning.

## Matching

**Exact, including letter case. Whitespace is not stripped.** The same rule `label`,
`in_progress_list` and `done_list` already use, kept identical deliberately — two matching rules
inside one section is how a reader gets it wrong.

A near-miss is *reported*, not silently ignored, which is what makes exactness the friendly choice
here rather than the strict one:

```
[FAIL] ignored list exists    'icebox' not found — the board has: Icebox, Doing, Done
```

Where the board has **more than one column of the configured name**, cards in **all** of them are
excluded (FR-019b). Trello permits duplicate list names; the author expressed an intent as a name,
so the name is what is honoured. This works because the ids are resolved through
`BoardInfo.lists_by_id` (id → name), which cannot collapse duplicates the way a name-keyed map does.

## What is deliberately absent

- **No default value.** Nothing is ignored until the author says so. Defaulting to `["Done"]` would
  change existing installations' behaviour on upgrade, which FR-002 forbids.
- **No per-card override.** There is no way to say "ignore this column except this card". If the card
  should be intake, it belongs in a column that is intake — that is what the columns are for.
- **No column ids.** The key holds names, matching every other list setting in this section. A
  24-hex id in a file a human reads and edits is unreadable and unverifiable at a glance.
- **No second key for behaviour.** There is no `ignore_mode`, no `ignore_action`. The list either
  contains a column or it does not.
