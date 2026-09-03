# Contract: CLI

Three new verbs. Everything not restated here is unchanged.

## `robot-army hold`

```
robot-army hold <item_id>
robot-army hold --repo <owner/name>
```

Exactly one of the two forms. Both together, or neither, is a **usage error (exit 2)** raised by
argparse before anything is read.

The target is stated, never inferred from its shape (research R6). An item id is an integer and
a repository key contains a slash, so a single argument *could* be classified by looking at it —
and this codebase refuses that class of guess on principle: an ambiguous board column is
reported rather than guessed at, a card id is matched against a strict pattern rather than
accepted as an opaque segment. A mistyped key that happened to parse as something else would
silently hold the wrong thing.

Neither spelling is new. Per-item verbs (`cancel`, `retry`, `abandon`, `attach`) already take a
positional id, and `poll --repo` already takes a repository key as a flag.

| Condition | Exit | Output |
|---|---|---|
| Placed | 0 | what was held, and that it will not dispatch until released |
| Already held | 0 | the **existing** hold with its **original** time and placer (FR-004) |
| No such work item | 1 | `no work item with id <n>` |
| Repository not onboarded | 1 | names the key and that it is not onboarded (FR-006) |
| Both or neither target given | 2 | argparse usage |

Works whether or not the daemon is running, for the reason `pause` does: it writes to the
database, which the daemon reads before each dispatch decision. Holding against a stopped daemon
is meaningful — it takes effect when it starts (FR-022).

**Audit**: `hold.item` / `hold.repo`, carrying the target, whether a hold was already in force,
and the resulting `held_at` and `held_by`.

## `robot-army unhold`

```
robot-army unhold <item_id>
robot-army unhold --repo <owner/name>
```

Same argument contract, same refusals for unknown targets.

| Condition | Exit | Output |
|---|---|---|
| Released | 0 | what was released and how long it had been held |
| Was not held | 0 | reported as a no-op (FR-005) |

**Releasing something that was not held is not a failure.** "I already released that" and "that
was never held" are the same outcome to the author, and neither deserves a non-zero exit. The
audit record still names the attempt, so the distinction survives in the log even though it does
not survive in the exit code.

**Audit**: `unhold.item` / `unhold.repo`, carrying the target and whether anything was removed.

## `robot-army holds`

```
robot-army holds
```

Read-only; joins `READ_COMMANDS`. Lists **every** hold in force (FR-018):

- each held item — its id, repository, issue number, current state, when it was held, by which
  surface, and how long ago;
- each held repository — the key, when it was held, by which surface, how long ago, and how many
  of its items are currently in the queue.

Two things it shows that no item-oriented view structurally can, and they are why it exists as a
verb rather than a section of `status`:

- **a repository hold matching no queued item** — the exact shape of a hold set and forgotten,
  which would otherwise be diagnosed as "polling is broken";
- **a hold on an item that is no longer eligible** — held then finished, or held then abandoned.
  These are left in place deliberately (research R11) and shown with the item's state, so they
  are visible rather than mysterious.

With no holds at all it says so plainly — *nothing is held* — rather than printing an empty
table (US3 AS3).

## `robot-army status`

Gains **one summary line, and only when at least one hold is in force**: how many items and how
many repositories are held, and that `robot-army holds` lists them.

Conditional on purpose. A permanent "no holds in force" line would be noise on the
overwhelmingly common runs where nothing is held, and noise is how a surface stops being read —
which would cost exactly the discoverability the line is there to buy. Saying something only
when there is something to say gives US3 its discoverability at zero cost the rest of the time.

The per-item queue listing needs no change: it already renders `entry.hold` and `entry.detail`
for every reason, so `held` appears in it the moment the reason exists.

## Local time

Every timestamp these verbs display goes through `timefmt.local`, as milestone 010 requires of
every terminal display site. Stored values remain UTC.

## Terminal parity

`hold`, `unhold`, and `holds` are added to the enumeration in
`tests/unit/test_cli_exit_codes.py::test_every_web_control_has_a_terminal_verb_here`, whose
counterpart in `test_web_routing` checks the same correspondence from the route table. FR-007's
parity is verified by enumeration rather than asserted in prose.
