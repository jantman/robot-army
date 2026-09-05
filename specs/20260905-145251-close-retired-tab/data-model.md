# Phase 1 — Data model

**No schema change. No migration. Nothing new is persisted.**

That is the headline, and it is worth stating plainly because the previous feature in this area
needed a migration and it would be easy to assume this one does too. A sweep that reads state and
acts on the terminal has nothing to store: what it would have written down — "this window is gone" —
is answered more reliably by asking the terminal again next pass.

## What is read

| Source | Field | Used for |
|---|---|---|
| `work_items` | `state` | The gate. Only `done` qualifies (FR-003) |
| `work_items` | `id` | Matched against the window's `ra_item` marker (FR-007) |
| `sessions` | `state`, via `cleanup.live_sessions` | "Is anything still running for this item?" (FR-004) |
| `sessions` | `window_id` | **Not used to decide anything.** See below |
| kitty, per window | `user_vars["ra_item"]` | Identity: which item this window belongs to |
| kitty, per window | `id` | The handle passed to `close()` — obtained from the live listing, never from the database |

### Why `sessions.window_id` decides nothing

It is recorded at dispatch and it is correct at the moment it is written. It is not identity.

Kitty numbers windows per kitty process, monotonically from 1, and restarts the numbering when kitty
restarts. A `window_id` of 50 stored today can name an unrelated window — an editor, a build log —
after the next restart. Closing on that number alone would destroy something the system never
opened, which is the pid-reuse failure wearing different clothes; this codebase carries a
`proc_start` guard and issue #79 because of the same mistake made twice about processes.

The column stays where it is and keeps its diagnostic value: `robot-army show <id>` can still say
which window an attempt opened. Nothing in this feature reads it.

## The candidate set

Built from SQLite before the terminal is touched at all (R6):

```
items where state = 'done'
  and cleanup.live_sessions(item.id) is empty
```

Empty candidate set ⇒ the sweep returns without listing windows, without a subprocess, and without
the possibility of a terminal error. This is the ordinary state of an idle machine.

An item in the set with no session rows at all — a rebuilt database — is **excluded**: there is
nothing to establish that its session ended, and the spec's edge case says leave it alone. That is
one extra condition on the query and it is deliberate, because `live_sessions` returning an empty
list is the same answer for "all its sessions finished" and "it never had any".

## In-memory shapes

`DisplayHandle` is unchanged and already carries everything needed:

```python
@dataclass(frozen=True, slots=True)
class DisplayHandle:
    window_id: int
    title: str = ""
    user_vars: dict[str, str] = field(default_factory=dict)
    simulated: bool = False
```

The `Display` protocol gains one method:

```python
def list_by_var(self, key: str) -> list[DisplayHandle]: ...
```

Every window carrying `key` in its user variables, with the value readable from the returned
handle's `user_vars`. `find_by_var` is left exactly as it is — it has its own caller and its own
meaning, and widening it to return a list would change that caller's contract for no reason.

## What is deliberately not changed

| | Why |
|---|---|
| Every table, and `migrations.py` | Nothing is persisted (see above) |
| `sessions.window_id` | Kept for diagnostics; not identity (see above) |
| `cleanup.py` | `live_sessions` gains a third caller, not an edit (R5) |
| `KittyDisplay.open` and its `--hold` flag | FR-017. A failed launch must still leave a readable window, and that is preserved by the `done` gate rather than by editing the launch |
| `KittyDisplay.close` and `find_by_var` | Reused as they are (R2, R4) |
| `operations.cancel` | The rule is about the item's state, so a by-hand stop converges on the same outcome with no change (plan, finding 4) |
| `capacity.py`, `states.py`, `spool.py`, `db.py` | Untouched |
| `config.py`, `exampleconfig.py`, `share/config.example.toml` | No configuration key, so neither CLAUDE.md step is triggered |
