# Data model: Naming the repository outright on a card

There is no schema change, no migration, and no new persisted value. The whole of this
feature's state lives for the duration of one call to `resolve_repository`. What follows is
the shape of the values that pass through it.

## Persistent state: unchanged

| Store | Change |
|---|---|
| `cards` table | none. `title` and `body` already hold the card's text, declaration included |
| `repos` / onboarding | none. The onboarded set is read, never written |
| poll state, session state, config | none |
| audit log | one added key inside an existing record's `detail` — see below |

Nothing here needs a migration and nothing needs an interruption path, because nothing is
written.

## `Resolution` — extended

The existing frozen dataclass returned by `resolve_repository`, with one field added.

| Field | Type | Meaning |
|---|---|---|
| `repo_key` | `str \| None` | the one onboarded repository, or `None` when the card is to be held |
| `reason` | `str \| None` | why it could not resolve; the text shown to the author and written to the record |
| `candidates` | `tuple[str, ...]` | the onboarded repositories the card selected — one when resolvable, several when ambiguous, empty when nothing was recognised |
| `source` | `str` | **new.** `"declaration"` when the card's `robot-army:` lines produced this outcome, `"scan"` when the ordinary text scan did. Defaults to `"scan"` |
| `resolvable` | `bool` (property) | `repo_key is not None`; unchanged |

`source` defaults so that every existing construction site — and every existing test that
builds one — keeps compiling and keeps meaning what it meant. It describes the *outcome's*
origin, not merely a success: a card held because its declarations disagreed carries
`source="declaration"`, which is the fact the log needs in order to explain why a card with
lines on it was still held.

### The four outcomes, as values

| Situation | `repo_key` | `candidates` | `source` |
|---|---|---|---|
| one declaration, resolves | the key | `(key,)` | `declaration` |
| declarations disagree | `None` | the keys, sorted | `declaration` |
| a declaration selects nothing | `None` | the keys that *did* resolve, sorted, possibly empty | `declaration` |
| no declaration | as today | as today | `scan` |

## Declaration — a transient value

Not a class. A declaration is one string: the reference text a matching line gave, in the
order the lines appear. `_declared_references(text) -> list[str]` is the whole of it.

Giving it a dataclass would add a type whose only field is a string, and whose only consumer
is the function on the next line down. The list is enough.

| Property | Value |
|---|---|
| Cardinality | zero or more per card |
| Order | source order; the list is not deduplicated, so two lines saying the same thing are two entries |
| Content | one run of non-whitespace characters, backticks already removed |
| Lifetime | one call to `resolve_repository` |

Deduplication happens later, on the *resolved key* rather than on the reference text, which
is what makes `robot-army: jantman/demo` and `robot-army: ~/git/demo` on the same card one
instruction rather than two.

## `trello.evaluated` — one added key in `detail`

```json
{
  "resolvable": false,
  "repo_key": null,
  "candidates": ["jantman/demo", "jantman/other"],
  "reason": "this card gives more than one `robot-army:` line and they name different repositories (jantman/demo, jantman/other); it must name exactly one",
  "source": "declaration"
}
```

No new action name, no new entity type, no change to the record's envelope. `docs/guide/audit-log.md`
describes this record's contents in prose and is updated to match.
