# Quickstart: proving the flag is no longer inert

How to convince yourself this works, in the order that finds a mistake soonest. Every command
runs from the repository root.

## Prerequisites

```bash
uv sync
uv run pytest          # must be green before and after
```

Nothing here needs a network, a Trello board, or a GitHub token. The retraction check needs no
board either, by design — see [research R8](research.md).

## 1. The suite, which is where most of this is proved

```bash
uv run pytest
```

The behaviour is verified by unit tests rather than by eyeballing a live database, because the
states that matter — a rehearsed anomaly beside a real one of the same kind, an audit file of
mixed records, a card that failed and then linked — are tedious to produce by hand and trivial to
seed. The manual walkthrough below is for the surfaces a test renders but does not *read*.

Test files that must be green, and what each one is standing guard over:

| File | Guards |
|---|---|
| `tests/unit/test_migrations.py` | 014 applies from 013; the column defaults to `0`; a pre-014 row reads as real; the rebuilt index admits a rehearsed and a real anomaly for the same kind and entity, and still refuses two of the same |
| `tests/unit/test_db_scope.py` | `list_anomalies` is in `LISTING_ACCESSORS` — the parameter exists, defaults to `False`, is keyword-only |
| `tests/unit/test_listing_withheld.py` | the withheld sentence on `anomalies`, `status`'s anomaly block, `log`, and `worktree list`; and that each count equals what the flag then reveals |
| `tests/unit/test_anomalies_since.py` | `--since` and `--all` compose with the flag, and the withheld count is scoped to the window |
| `tests/unit/test_anomaly_resolution.py` | `card_create_failing` retracts on `linked`, is left alone on a still-failing card, is left alone on a card that is not there, and a second pass writes and logs nothing |
| the new cross-verb guard | every verb in `SIMULATED_SCOPED_COMMANDS` produces different output in the two spellings; `repos` refuses the option with exit 2 |

## 2. The defect, reproduced and then not reproduced

The issue's own measurements, run against a database seeded with rehearsed rows. Before the
change both numbers in each pair are equal; after it they differ.

```bash
export RA=~/.local/state/robot-army          # wherever your state lives

uv run robot-army anomalies                      | grep -cE '^\[[0-9]+\]'
uv run robot-army anomalies --include-simulated   | grep -cE '^\[[0-9]+\]'

uv run robot-army log --since 2d                     | grep -c '\[simulated\]'
uv run robot-army log --since 2d --include-simulated | grep -c '\[simulated\]'
```

**Expected after the change**: the first pair differs by the number of rehearsed anomalies. The
second pair's first number is **zero** — the default reader shows no record carrying the marker
at all — and the second is the count the withheld line named.

**Failure to watch for**: a first `log` number that is non-zero. That means a record is marked
`[simulated]` by `_format_record` but judged real by `_judge_record`, which is the two halves of
"is this rehearsed?" having drifted apart. They read the same two fields for exactly this reason.

## 3. The withheld count means what it says

The one property worth checking by hand, because it is the one milestone 008 had to build
`_work_item_filters` to guarantee and this feature has to earn again on two more verbs:

```bash
uv run robot-army anomalies | tail -3          # note N in the withheld sentence
uv run robot-army anomalies --include-simulated | grep -cE '^\[[0-9]+\]'   # call this B
uv run robot-army anomalies | grep -cE '^\[[0-9]+\]'                       # call this A
```

**Expected**: `B - A == N`, and it stays true with `--since 1h` added to all three, and with
`--all` added to all three.

**Failure to watch for**: the identity holding without a window and breaking with one. That means
the withheld count is computed in SQL while the listing is filtered in Python, which is the exact
trap [research R4](research.md) exists to avoid — `--since` is applied in Python deliberately, so
a malformed timestamp is shown rather than silently dropped.

## 4. `repos` refuses the option

```bash
uv run robot-army repos --include-simulated; echo "exit $?"
uv run robot-army repos --help | grep -c include-simulated
```

**Expected**: exit `2`, argparse naming the unrecognised option on stderr, and `0` occurrences in
the help.

**Failure to watch for**: exit `0`. That is the original defect — a flag accepted and ignored —
wearing the fix's clothes.

## 5. The web surfaces

```bash
uv run robot-army serve --bind 127.0.0.1 --port 8765
```

Then, with rehearsed anomalies in the database:

- `/anomalies` with the toggle **off** lists only real anomalies, and states what it withheld.
- The anomaly pill in the header shows the same number **on every page** — `/queue`, `/cards`,
  `/log` — and flipping the toggle changes it everywhere.
- `/log` with the toggle off shows no `[simulated]` record, and says how many the page's scan
  withheld.
- There is no `/repos` page; that verb is terminal-only, which is why removing its flag needed no
  web decision ([research R6](research.md)).

**Failure to watch for**: a pill that disagrees with the `/anomalies` page it links to. Both read
one scope from the request; if they disagree, one of them is computing its own.

## 6. Retraction, end to end

Reproducible without a board by seeding, which is what the unit test does. By hand, on a machine
that has one:

1. Find an outstanding `card_create_failing`: `uv run robot-army anomalies | grep card_create`.
2. Confirm its card is linked: `uv run robot-army cards --include-simulated | grep <card-id>`
   shows state `linked`.
3. `uv run robot-army reconcile`
4. `uv run robot-army anomalies` — the row is gone.
5. `uv run robot-army anomalies --all` — the row is there, marked **resolved**, not acknowledged.
6. `uv run robot-army log --since 5m | grep anomaly.resolved` — one record, carrying the kind and
   the card.
7. `uv run robot-army reconcile` again, then step 6 again — still **one** record.

**Expected at step 5**: `resolved`. If it says `acknowledged`, the resolution wrote the wrong
column and the distinction migration 012 exists for has been lost.

**Failure to watch for at step 7**: a second `anomaly.resolved` record. That means the
`resolved_at IS NULL` guard was bypassed and the pass is not idempotent, so every reconciliation
tick would log a resolution for a row that resolved once.

## 7. The two standing obligations

```bash
uv run pytest tests/unit/test_example_config_drift.py    # no config key changed; must pass untouched
uv run pytest tests/unit/test_docs_links.py
wc -l README.md                                          # still under 150
```

And read the four guide pages that changed —
[`operating.md`](../../docs/guide/operating.md),
[`1-setup.md`](../../docs/guide/1-setup.md),
[`audit-log.md`](../../docs/guide/audit-log.md),
[`state.md`](../../docs/guide/state.md) — against
[the contract](contracts/simulated-scope.md). The set of verbs named in the guide and the set in
`SIMULATED_SCOPED_COMMANDS` must be the same set; the whole feature is about a document and a
program disagreeing on exactly that.

## Concerns for whoever comes next

- **The paged log reader's withheld count describes its scanned region, not history.** That is
  stated in the words it prints and in [R5](research.md), and it is the only honest number a
  bounded scan can produce. If the page ever gains a whole-history count, these are different
  numbers and both need naming.
- **`_orphan_sweep`'s registry-scan branch always raises a real anomaly**, even when the process
  it found belongs to a rehearsed session, because there may be no session row to ask. That is
  correct today — the slot is really taken — but if that branch ever gains a reliable way to
  identify the session, revisit it.
- **Only two anomaly kinds retract themselves.** The others each have their own settling story
  and none of them is guessed at here. The next one worth doing is probably `prunable_worktree`,
  whose condition is re-checkable on disk.
