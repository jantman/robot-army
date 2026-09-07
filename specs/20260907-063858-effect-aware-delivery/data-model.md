# Data Model: The delivery block follows the effect level

No table, no state file, no configuration key, no migration. Everything here is in-process and
selected once.

## `prompt.Delivery`

A frozen dataclass with two fields and one method. Two instances exist, both module-level
constants; no third can be constructed by anything that matters, because nothing but
`effects.wire()` chooses one.

| Field | Type | Meaning |
|---|---|---|
| `name` | `str` | What the audit log and the startup record call this form: `"push"` or `"local"`. |
| `text` | `str` | The prose composed into the prompt. |

| Method | Returns | Why it exists |
|---|---|---|
| `describe_name()` | `name` | The hook `effects._describe_one` already looks for, so `Boundaries.describe()` can carry the form without a second mapping. `MultiNotifier` uses the same hook. |

### The two instances

| Constant | `name` | Instructs |
|---|---|---|
| `prompt.DELIVERY_PUSH` | `push` | Commit on the feature branch, push to `origin`, open a pull request. Byte-for-byte the text `prompt.DELIVERY` holds today. |
| `prompt.DELIVERY_LOCAL` | `local` | Commit on the feature branch and stop: no push, no pull request, no comment on the issue, nothing written to any remote. |

`prompt.DELIVERY` is **removed** rather than aliased. An alias is a name whose meaning is now
"the one that pushes", which is exactly the assumption this feature exists to stop code making
implicitly (research.md R4).

## `effects.Boundaries.delivery`

One new field on the existing frozen dataclass, of type `prompt.Delivery`. It sits with the
wired implementations because it is the same kind of thing: a per-level selection made once, at
startup, by the only module allowed to know a level exists.

| Level | `delivery` |
|---|---|
| `plan` | `DELIVERY_LOCAL` |
| `local` | `DELIVERY_LOCAL` |
| `no-remote` | `DELIVERY_LOCAL` |
| `live` | `DELIVERY_PUSH` |

The table is the same shape as `REAL_AT["issue_writer"]` and is derived from it in spirit, not
in code: `is_real("issue_writer", level)` answers "does robot-army's own write reach GitHub",
and the delivery form answers "is the session asked to make one". Writing the second as a
lookup of the first would tie a change in one to a silent change in the other, so it is its own
one-line selection with its own test.

`Boundaries.describe()` gains `"delivery"`, giving the `daemon.start` record a `boundaries`
map that reads `{"issue_writer": "SimulatedIssueWriter", …, "delivery": "local"}`.

## `prompt.compose(..., delivery=…)`

A required keyword argument of type `Delivery`. Not optional, and not defaulted — R4. The
composer never asks why it got the form it got; it places `delivery.text` where `DELIVERY` used
to go, in the same position, with the same separator.

## Audit record fields

Neither record is new; each gains one key.

| Record | Key | Value |
|---|---|---|
| `daemon.start` | `boundaries.delivery` | `"push"` or `"local"` |
| `prompt.preview` | `delivery` | `"push"` or `"local"` |

No record gains prompt text. `prompt.preview` already carries `instructions` and `speckit` as
booleans saying *whether* an optional section was included; `delivery` says *which* of two
mandatory forms was, which is the same question asked of a section that is never absent.
