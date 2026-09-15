# Data Model: Say so when nothing is onboarded

No tables, columns, state files, or migrations change.

## Values that change

### Held-card reason (`cards.reason`, `cards.commented_reason`)

A new possible value, fixed text (see [contracts/inert-installation.md](contracts/inert-installation.md)).
It is stored and compared exactly like every other reason: a card gets one comment per distinct
reason, so a card previously held with the old "no onboarded repository could be identified"
text gets **one** new comment when its reason becomes this one, and none after.

Re-evaluation of a held card is gated on board activity (003 FR-023). A card held with this
reason also passes the gate once the onboarded set is non-empty, so onboarding the repository is
enough — nobody has to touch the card or run `rescan`. The transition is one-way per
evaluation: the next resolution either links the card or records an ordinary reason, after
which the activity gate governs it as before.

### `trello.evaluated` audit detail

`source` gains the value `"onboarding"`, meaning the empty onboarding set decided the outcome.
`resolvable` is `false`, `repo_key` is `null`, `candidates` is `[]`.

### `doctor` result

`data["checks"]` gains one entry, `{"name": "onboarded repositories", "ok": <bool>, "detail": ...}`,
and when `ok` is false, `data["failures"]` contains `"onboarded repositories"`.
