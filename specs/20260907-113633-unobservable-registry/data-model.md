# Data Model: An Observation That Saw Nothing Is Not Evidence That Nothing Is Alive

**Feature**: `specs/20260907-113633-unobservable-registry/`

**Schema change**: none. `SCHEMA_VERSION` does not move, no migration is written, no column is
added, and no backfill runs. Everything this feature reads is already on the objects the pass
holds, and the one row type it writes already exists.

---

## 1. `sessions.RegistryScan` — read only, unchanged

The object one pass takes of the machine. Three of its fields answer "was this observation worth
concluding from"; all three are already populated and none is added here.

| Field | Type | Set when | Used here for |
|---|---|---|---|
| `directory_missing` | `bool` | the registry directory is absent, is not a directory, or raises on `os.listdir` | clause 1 of the predicate |
| `degraded` | `bool` | the scan came from `scan_via_proc` — reached in `reconcile.scan_registry` when a version was refused and no usable entry survived | clause 2 |
| `unknown_versions` | `tuple[str \| None, ...]` | one or more files were refused by the version gate | clause 3 |
| `unreadable` | `tuple[str, ...]` | a file could not be opened or parsed | **deliberately not used** (research.md R4) |
| `entries` | `tuple[RegistryEntry, ...]` | the live entries that were parsed | unchanged; the guards do not count them |

**The predicate is a function of the first three and nothing else.** In particular it does not
look at `len(entries)`: an observation that saw nothing is exactly as blind whether or not it
also happened to find a process, and a rule that counted entries would re-collapse "idle" and
"unobservable" into one — which is the defect.

---

## 2. `reconcile.ReconcileResult` — one new counter

| Field | Type | Default | Meaning |
|---|---|---|---|
| `liveness_withheld` | `int` | `0` | Liveness conclusions this pass declined to draw because the registry observation was unusable. Incremented once per work item or session row that would otherwise have been settled. |

Appears in `summary()`, and therefore in the `reconcile.pass` audit record, beside
`skipped_never_real` — which #33 added for the neighbouring reason, that `checked` counts items
before any skip and so cannot distinguish a pass that examined everything from one that examined
nothing.

**Why one counter rather than three.** FR-008 asks how many conclusions were withheld. Which
sweep would have drawn each is recoverable from the state its rows are left in, and three
counters that are always zero together are three ways of saying one thing.

---

## 3. `sessions.summarise()` — one new key

The dict merged into every `reconcile.pass` record. It reports `sessions_found`,
`under_worktree_root`, `unknown_versions`, `unreadable` and `degraded` — and today, not
`directory_missing`, which is why a pass across a vanished registry and a pass across an idle
machine write the same line.

| Key | Type | Meaning |
|---|---|---|
| `directory_missing` | `bool` | The registry directory was absent, was not a directory, or could not be listed |

With `liveness_withheld` alongside it, three passes that are indistinguishable today become
distinguishable:

| `directory_missing` / `degraded` / `unknown_versions` | `liveness_withheld` | The pass was |
|---|---|---|
| all clear | `0` | an ordinary pass over an idle or healthy machine |
| any set | `0` | blind, but nothing was running to withhold a conclusion about |
| any set | `> 0` | blind, with work in flight that was deliberately left alone |

---

## 4. `anomalies` — one new kind, no schema change

| Column | Value | Why |
|---|---|---|
| `kind` | `registry_unobservable` | Joins `ANOMALY_KINDS` in `models.py`, without which `robot-army anomalies` will not list it among the kinds the system can raise (FR-065) |
| `entity_type` | `NULL` | It is a fact about this machine's registry, not about a session, item, or repository |
| `entity_id` | `NULL` | Same |
| `dry_run` | `0` | A fact about the machine. `registry_version_unknown` and `capacity_unobservable` are both classified this way, and `test_migration_014_leaves_underivable_rows_real` names both — "I cannot tell" and "it was a rehearsal" are different answers, and only one is safe to guess |
| `detail` | see below | FR-011 |

`detail` carries what the reader needs to reconstruct the pass without re-running it:

| Key | Meaning |
|---|---|
| `reason` | the condition that made the observation unusable, in the predicate's own words |
| `liveness_withheld` | how many conclusions this pass declined to draw |
| `note` | why withholding is the safe direction, in the shape the other kinds carry |

**No migration.** The partial unique index is
`(kind, COALESCE(entity_type,''), COALESCE(entity_id,''), dry_run)`, so a kind carrying no entity
already dedupes to one open row — which is how the two neighbouring kinds survive the same
60-second loop. `detail` is not part of the index, so a repeat updates nothing; the first pass's
count is the one that stands, and the pass summary carries the current one every minute.

**Retraction**: `resolved_at` is set by the first pass whose observation was usable.
`db.resolve_anomaly` is guarded by `resolved_at IS NULL`, so a repeated pass is a genuine no-op
rather than a second write with the same effect.

---

## 5. `reclaim_stale_session` — one new return value

The function returns a string naming what it decided, in the same shape as `spool.apply_record`.

| Value | Existing? | Meaning |
|---|---|---|
| `"left"` | yes | the row is legitimate — its work item is still running one |
| `"reported"` | yes | the worker is alive under a finished item; an orphan anomaly is raised and the row stays open |
| `"reclaimed"` | yes | the worker is gone; the row is closed `lost` |
| `"withheld"` | **new** | the registry could not be observed **and** holds no entry for this session, so neither of the other two can be told from the other |

Its three callers:

| Caller | Handling |
|---|---|
| `_sweep_stale_sessions` | counts `"reclaimed"`; `"withheld"` increments `liveness_withheld` |
| `_retire_one` | reaches `"reclaimed"`, not `"withheld"` — and only because the guard tests for a missing *entry* rather than for a blind pass (R12). It is called after `scan.find()` returned an entry, so the settle finds one too. Keying on the pass alone left every worker it terminated with a `running` row and a leaked slot; its `settled in ("reclaimed", "left")` test is unchanged |
| `operations.abandon` | says the row was left open and why, so a maintainer is not told an item was abandoned while a slot silently stays subscribed |

---

## 6. State machines — untouched

No new state and no new edge. `running` → `lost` and `active` → `interrupted` are both existing
transitions; this feature makes some traversals of them not happen. `states.py`,
`SESSION_BEARING_STATES` and `TERMINAL_WORK_ITEM_STATES` are unchanged.

---

## 7. What is deliberately absent

| Not added | Why |
|---|---|
| A field on `RegistryScan` saying "unusable" | The judgement belongs to the consumer. `capacity.py` reaches a different one from the same object, and deliberately (R3) |
| A `blind_since` timestamp anywhere | State persisting between passes, to say what the standing anomaly already says |
| A configuration key | Declining to conclude from a failed observation is not a policy anyone chooses between (FR-013) |
| A column recording that a row was withheld | The condition is re-derivable from the registry at any instant, and the row is left byte-for-byte as found — which is the whole content of the decision |
