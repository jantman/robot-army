# Data Model: The Web Interface Shows Its Work and Announces Non-Live Mode

**Feature**: `009-web-non-live-visibility` | **Date**: 2026-08-29

## Schema changes

**None.** No table, column, index or migration is added, altered or dropped.
`SCHEMA_VERSION` is unchanged. Every row this feature displays already exists and every count
it states is already computed by an accessor milestone 008 added.

The entities below are **request-scoped values** — they live for the duration of one HTTP
request and are never persisted. That is deliberate and is the reason there is no schema
change: the operator's preference lives in the URL, where it survives a browser reload, a
server restart and a killed process without any storage at all (Principle IV).

---

## Values

### SimulatedPreference

What the operator said about simulated rows on this request, as distinct from what the
interface decided.

| Field | Type | Meaning |
|---|---|---|
| *(the value itself)* | `bool \| None` | `True` show, `False` withhold, `None` unstated |

**Source**: the `include_simulated` query parameter, or the form field of the same name on a
`POST`. Parsed by `Request.simulated_preference`.

**Validation**:

- `1`, `true`, `yes`, `on` (case-insensitive) → `True`
- `0`, `false`, `no`, `off` (case-insensitive) → `False`
- absent, empty, or anything else → `None`

An unrecognised value **must not** produce an error response (FR-004). It is folded into
"unstated", which is the forgiving direction.

**Lifetime**: one request. Re-stated in every link and form the response generates
(research [R3](research.md)), so it round-trips through navigation without being stored.

---

### EffectiveLevel

The single level that drives the non-live banner, the level pill, and the visibility default
(FR-018).

| Field | Type | Meaning |
|---|---|---|
| *(the value itself)* | `EffectLevel \| None` | `None` means "a daemon is running and its level cannot be read" |

**Derivation**: the more simulated of the interface's configured level (`ctx.effect_level`) and
the running daemon's level (`heartbeat["effect_level"]`), where `EffectLevel`'s declaration
order — `PLAN`, `LOCAL`, `NO_REMOTE`, `LIVE` — is the ordering. When no daemon holds the lock,
the configured level stands alone. When a daemon holds the lock but no heartbeat can be read,
the value is unknown, which is treated as most simulated for styling and defaults, and is
explained by the existing `EFFECT LEVEL UNKNOWN` banner rather than by a second one.

**Invariant**: exactly one value per request, computed once in `pages.chrome` and carried in
its payload. The banner and the pill read that payload key and never re-derive it, which is
what makes FR-018's "must not disagree" structural rather than a matter of discipline.

---

### ResolvedVisibility

Whether this request's rows include simulated ones.

| Field | Type | Meaning |
|---|---|---|
| *(the value itself)* | `bool` | passed as the existing `include_simulated=` keyword to every `pages.*` view |

**Derivation**, in full (FR-001, FR-002):

| SimulatedPreference | EffectiveLevel | Resolved |
|---|---|---|
| `True` | any | `True` |
| `False` | any | `False` |
| `None` | `plan`, `local`, `no-remote` | `True` |
| `None` | unknown | `True` |
| `None` | `live` | `False` |

**Scope**: display only. This value reaches `db.list_*` accessors through `operations.status`
and `operations.cards`. It **must not** reach `ordering.plan` or `capacity.snapshot`, both of
which pass `include_simulated=True` unconditionally because a simulated row occupies a
dispatch slot regardless of who is looking (FR-005, SC-008).

---

### WithheldCount

For one view under its own filters, how many matching rows the resolved visibility is hiding.

| Field | Type | Meaning |
|---|---|---|
| *(the value itself)* | `int` | `0` when nothing is withheld — always present, never absent |

**Source**: existing accessors, no new query.

| View | Comes from | Filters honoured |
|---|---|---|
| `/active` | `operations.status(state="active").data["withheld_simulated"]["items"]` | state |
| `/queue` | `operations.status().data["withheld_simulated"]["items"]` | none (the queue is unfiltered) |
| `/interrupted` | `operations.status(state=…)`, summed over `interrupted` and `awaiting_review` | state |
| `/cards` | `operations.cards().data["withheld_simulated"]` *(payload key added by this feature)* | card state |

**Invariant (FR-007)**: the number stated equals the number the override would reveal for that
same request. This holds because the count is computed by the same call, under the same
filters, as the listing it accompanies — the discipline milestone 008 established rather than a
new one.

**Rendering rule (FR-006, FR-008, FR-009)**:

- `0` → no disclosure at all, and an empty view says plainly that there is nothing.
- non-zero with rows visible → a note beneath the table stating the count and linking to the
  same URL with the preference flipped.
- non-zero with no rows visible → the empty-state text says nothing is *shown*, not that
  nothing *exists*, and carries the same count and link.

---

## Reference tables (static, not data)

### `effects.SIMULATED_CONSEQUENCES`

A new module-level table in `effects.py`, keyed by the same boundary names as the existing
`REAL_AT`, valued by one operator-facing phrase each.

| Boundary | Real at | Phrase names |
|---|---|---|
| `issue_reader` | every level | *(never simulated — phrase unused, present so the table is total)* |
| `card_reader` | every level | *(never simulated — as above)* |
| `issue_writer` | `live` | no issue or comment is really written, and the issue numbers shown are invented |
| `card_writer` | `live` | no card really moves on the board |
| `notifier` | `live` | no notification is really sent |
| `session_host` | `no-remote`, `live` | no session is really launched |
| `display` | `no-remote`, `live` | no terminal window really opens |
| `version_control` | `local`, `no-remote`, `live` | no branch, commit or worktree is really created |
| `hook_runner` | `local`, `no-remote`, `live` | no hook really runs |

`consequences(level)` returns the phrases whose boundary is **not** real at that level, in
declaration order. By construction it is empty at `live`, which is what makes FR-014 —
no banner at `live` — fall out of the derivation rather than out of a branch.

**Drift guard**: a unit test asserts `set(SIMULATED_CONSEQUENCES) == set(REAL_AT)`, so a
boundary added to one table without the other fails the suite rather than rendering a banner
that quietly omits a consequence.

### `EffectLevel` ordering

No new table. `EffectLevel`'s members are already declared least-to-most consequential
(`PLAN`, `LOCAL`, `NO_REMOTE`, `LIVE`), and `list(EffectLevel).index` is the ordering the
effective-level rule needs. A unit test pins the declaration order so a future reordering
cannot silently invert the comparison.

---

## Payload additions

Three keys appear in the JSON representation every view already returns
(`_render` merges `view.data` with the chrome payload). See
[contracts/web-visibility.md](contracts/web-visibility.md) for the full contract.

| Key | Where | Type | Meaning |
|---|---|---|---|
| `effective_level` | chrome | `str` | the value from [EffectiveLevel](#effectivelevel); `"unknown"` when unreadable |
| `simulated_preference` | chrome | `bool \| null` | what the operator stated, `null` if nothing |
| `withheld_simulated` | view data | `int` | added to the cards payload; already present in status-derived views |

`include_simulated` and `effect_level` are unchanged in name and meaning. `include_simulated`
continues to report the **resolved** value, which is what the rows on the page were selected
with — a consumer that wants to know whether the operator asked reads `simulated_preference`.
