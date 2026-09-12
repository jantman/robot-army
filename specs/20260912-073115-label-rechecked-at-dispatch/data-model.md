# Data Model: The Label Is Re-checked Before Dispatch

No schema change and no new stored state.

## `work_items.labels` (existing column)

A JSON array of the issue's labels as last read, and the input to the new hold.

| Writer | When | Change |
|---|---|---|
| `poll.poll_repo` insert | discovery | unchanged |
| `operations.retry` | a `failed` item is retried, from a live read | unchanged |
| `poll.poll_repo` existing-row branch | a `ready` row's issue appears in a listing with a different label **set** | **new** |

**Validation**: a value that does not decode to a list of strings is read as "does not carry
the label" by the hold (R3). Nothing rewrites it except the three writers above.

## `HoldReason` (computed, never stored)

Declaration order is precedence:

```
paused, held, not_labelled, capacity_unobservable, global_cap, repo_cap,
awaiting_merge, not_onboarded, off_column, preparation_failed
```

`not_labelled` applies when `[github] label` is not in the item's stored labels. It is
per-item: not in `_GLOBAL_HOLDS`, and not in `launch_holds`.

## State transitions

None. A held item stays `ready`. The hold lifts on the next plan once the configured label
is back in the stored labels, whether because the configuration was reverted or because a
refresh wrote them.
