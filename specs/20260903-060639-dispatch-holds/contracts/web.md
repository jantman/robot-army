# Contract: web interface

Four new routes. Everything not restated here is unchanged.

## The routes

| Method | Path | Form fields | `terminal=` |
|---|---|---|---|
| POST | `/item/<id>/hold` | — | `hold` |
| POST | `/item/<id>/unhold` | — | `unhold` |
| POST | `/repos/hold` | `repo=<owner/name>` | `hold` |
| POST | `/repos/unhold` | `repo=<owner/name>` | `unhold` |

**Item holds are per-item paths**, matching `abandon`, `cancel`, `retry`, and `attach`. They
reuse the existing `<id>` binder, which already refuses a non-integer segment.

**A repository key travels in the form body, never in the path** (research R7). A key contains a
slash, so a path parameter would mean two segments or an encoded one, and `_bind` matches on
segment count. The standing position on this is in `server.py` beside `_CARD_ID`: *a route
parameter that reaches a page is one an attacker would like to control.* A two-segment
repository parameter would create exactly the shapes — `..`, encoded separators — that the
strict card pattern exists to foreclose.

This is not a workaround; it is the pattern already in use. `_job_action` reads
`request.first("repo")` for `POST /poll`. The value is validated against `repos.known(conn)`
before it reaches anything, so an unknown key is a refusal rather than a stored row.

## Guards

Every one of the four goes through `_perform`, so all of them get same-origin checking and the
intent-before-action audit pair — the record is written and flushed *before* the checks run, so
a refusal, a crash, and a success all leave a record.

**None of the four calls `require_effect_agreement`**, matching `_pause_action`.

The tempting asymmetry — guard `unhold` because releasing can lead to a session starting —
guards the wrong side of the causal chain. Unholding starts nothing; it removes one row, after
which the *dispatcher* decides whether to dispatch and applies the effect level itself at the
moment it acts. Guarding the release would be the same mistake as putting a network read inside
`plan`: attaching a decision to the surface that displays a fact rather than to the code that
acts on it.

`_pause_action`'s existing comment carries the other half: a stopping action must remain
available precisely when the guard would fire, or the interface has no safe action at the moment
one is most wanted. Holding is a stopping action, and an author who cannot undo a hold placed
during a mismatch is worse off, not safer.

**None of the four calls `require_daemon`** either. A hold is meaningful against a stopped
daemon — it takes effect when it starts (FR-022) — unlike `resume` and `restart`, which need the
daemon to drain the spool afterwards.

## Refusals

| Condition | Status | Body |
|---|---|---|
| No such work item | 404 | `no work item with id <n>`, via the existing `require_item` |
| `repo` field missing or not onboarded | 404 | names the key and that it is not onboarded |
| Cross-origin | as today | unchanged, and still recorded |

## Redirects

`303 See Other` back to the referring view (`_referring_view(request, "/queue")`), carrying
`include_simulated` forward through `html_query` in both directions, exactly as every other
action does. A reload re-issues a `GET` and never re-posts.

Messages: `held` and `released`.

## Rendering

**On the queue page**, a held item renders like any other held item — in the position it would
occupy anyway, marked with its reason and detail (FR-014). No special case: the existing
`row["hold"]` / `row["hold_detail"]` rendering already covers it.

**Repository holds get their own section on the queue page**, and it lists the **union** of two
sets — every repository with queued work, and every held repository — for two separate reasons.

*Every repository with queued work*, so a repository can be **held** from the page that shows the
problem. Listing only held repositories would make release reachable from the page and leave
`POST /repos/hold` with no control anywhere in the rendered HTML — a route working perfectly with
nothing offering it. (This is not hypothetical: the first implementation did exactly that, and
review caught it.)

*Every held repository*, queued work or not, which is FR-019. A hold matching no currently queued
item has no row of its own to attach to, and without the section it would be invisible —
suppressing every future item in that repository while the page looked completely normal. This is
the same problem `held_off_column` solved for parked boards, where a repository with ready items
dispatching none of them *reads exactly like a repository with no work at all*, and it takes the
same shape.

The section is absent only when both sets are empty, since a heading over an empty table is noise
on the page whose subject is what dispatch is doing.

**Controls.** Each queue row carries hold or release for that item, whichever applies; each
repository row carries hold or release for that repository, whichever applies. Never both — an
item is held or it is not, and offering the inapplicable control invites a tap that reports a
no-op.

**Where the checks run.** Every check — the item's existence, the repository key's validity —
runs **inside** `_perform`'s body, never before it, matching every other handler. `_perform`
writes and flushes the intent record before calling the body, so a refusal leaves a record saying
a request arrived and passes through the same-origin check on the way. A check placed before
`_perform` would make these the only POSTs whose refusals were invisible.

**No confirmation page.** Holds are trivially reversible and outward-facing in no sense; the
confirm-then-act flow is for `cancel` and the destructive verbs. Holding is one tap, which is
the point on a phone.

## Terminal parity

Each route declares its `terminal=` verb in the table above, and
`tests/unit/test_web_routing.py`'s enumeration checks that every control has one — with
`test_cli_exit_codes.py` checking the same correspondence from the parser side. FR-007 is
verified rather than asserted.

## No-store

Unchanged. Every response carries `Cache-Control: no-store`; a page claiming to describe what is
held now must never be served from a cache.
