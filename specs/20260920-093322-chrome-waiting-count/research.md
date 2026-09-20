# Research: the waiting count, and two pills that stop stating the default

**Feature**: [spec.md](spec.md) | **Date**: 2026-09-20

Every decision below was reached by reading the code named beside it. Nothing here is
speculative; R6 in particular is the verification the spec's FR-013 demands *before* anything
is deleted.

## R1 — Where the count comes from

**Decision**: `db.count_work_items_by_state(conn, include_simulated=...)`, summed over
`awaiting_review`, `interrupted` and `failed`, called once in `pages.chrome`.

**Rationale**: It already exists (`db.py:249`), it already takes `include_simulated` and
applies it through the same `_scope` clause every other listing uses, and it is the function
`robot-army status` counts with (`operations.py:316`). Using it is what makes "the web now
carries the fact the CLI always had" literally true rather than approximately true — one
`GROUP BY` against one table, and both surfaces read the same numbers.

The three states are summed in the renderer's payload, not in SQL, so the dictionary stays
the general-purpose thing it is and the choice of which states count stays visible as a named
constant beside the code that explains it.

**Alternatives considered**:

- Three `db.list_work_items` calls and `len()` on each — three queries, three lists built and
  thrown away, for a number.
- A new `db.count_waiting(...)` — a database function with one caller, encoding a web
  presentation decision in the storage layer. Principle I.
- Reusing `operations.status`, which returns counts among much else — it also runs
  `health.check`, `ordering.plan` and a capacity snapshot. `chrome` already has all three from
  the request's single reading; paying for them again to reach a count would undo RA-14's
  "one reading per page".

## R2 — What the pill does when there is no database to count

**Decision**: `pages.chrome` sets the count; `server._bare` does not. `_chrome_bar` renders the
pill only when the key is present.

**Rationale**: `_bare` (`server.py:1675`) renders 404, 405 and schema refusals with no
`Context` at all — "a 503 that cannot render is not a 503". There is no count to be had there.
The bar already has exactly this pattern for exactly this reason: the visibility pill is
guarded by `if included is not None`, and `_visibility_suffix` documents at length why the
absent key must not be read as a stated zero. The waiting pill follows it.

This is deliberately *not* what `anomaly_count` does — `_bare` sets it to `0`, and the error
page therefore prints "0 anomalies" having counted nothing. That is a pre-existing wart and not
this feature's to fix, but it is not a pattern to copy either. The spec's edge case says the
pill "must not appear there claiming a number it could not compute", and the guard is one
`in` check.

**Alternatives considered**: `"waiting_count": 0` in `_bare`, for symmetry with the anomaly
count — rejected above. Rendering the pill with a `?` — a third state to explain, on a page
whose whole job is to be a 404.

## R3 — Where the pill points, and what lives there

**Decision**: the existing `/interrupted` route grows a third section for `failed`. The route
is unchanged. The nav entry, the page heading and the pill all use one new name.

**Rationale**: the spec's binding constraint is that the pill and its destination agree. Two
ways to get there: grow the page that already holds two of the three states, or build a new
page that holds all three and leave the old one behind. `interrupted_view`'s own docstring
already gives the argument for growing it — awaiting-review is listed there "precisely so
those items are navigable at all", and `failed` is in exactly that position today, listed
nowhere. The section is a fourth call to helpers the function already calls three times over.
A new page is a new route, a new handler, a new terminal mapping, a new nav entry and a page
left over that nothing links to. Principle I: fewer moving parts wins.

**The route stays `/interrupted`.** The issue asks for the *nav entry* to be renamed, not the
path. Renaming the path would touch every `path="/interrupted"` disclosure link, the `Route`
table, the `terminal="status"` mapping and the tests, and would buy a tidier URL for a
single-user interface where nobody types URLs. The label is what the reader sees.

**Alternatives considered**: a `/waiting` route redirecting to `/interrupted` — a redirect
with one caller, and two names for one page. A pill pointing at `/interrupted` while `failed`
lives elsewhere — the disagreement the spec forbids.

## R4 — The words

**Decision**: the pill reads `{n} need{s} me` — "1 needs me", "2 need me" — and the nav entry
and page heading both read `needs me`.

**Rationale**: it is the issue author's own phrase, from their sketch, and they are the only
reader this interface has. It names the operational fact the three states share — the work is
parked and the machine will not move it without a decision — which is precisely what a name
covering all three has to do. "interrupted" named one state of three; "needs me" names none of
them and describes all of them.

The plural switch matches the anomaly pill's (`anomal{'y' if n == 1 else 'ies'}`), so the two
counts beside each other in the bar inflect the same way.

The heading gains a one-line `meta` beneath it naming the three states, because "needs me" is
honest about the *why* and says nothing about the *what*.

**Alternatives considered**: "waiting on me" — grammatical at every count and so needing no
plural switch, but it reads as a status rather than a call, and it is not the word the issue
used. "parked" — the word `/cards` already uses for something else entirely (a card in an
excluded column), and reusing it for work items would be a second meaning for one word.

## R5 — Where the pill sits in the bar

**Decision**: immediately before the anomaly pill.

**Rationale**: the issue's sketch puts it there, and the ordering it produces reads as a
widening scope — what the machine is doing (daemon, capacity, order), then what is waiting on
the operator, then what the system thinks is wrong. The two counts are adjacent, which is what
makes "the same shape as the anomaly one" legible at a glance rather than a claim in a commit
message.

## R6 — Verifying FR-013 before deleting the visibility pill

**Decision**: verified. Every view that can withhold a simulated row discloses it on the view
itself. The pill can be removed without stranding the reader.

**Method**: enumerate the routes (`server.py:1332`), take each view function, and find its
disclosure. A view withholds only if it filters rows by `include_simulated`.

| View | Can withhold? | Discloses where |
|---|---|---|
| `/` | no — redirects to `/active` (`server.py:950`) | n/a |
| `/active` | yes | `_nothing` (`pages.py:773`) when empty, `withheld_note` (`pages.py:818`) when not |
| `/queue` | yes, in three sections | `_nothing` per section (`pages.py:1037`, `1078`, `1103`), `withheld_note` (`pages.py:1134`) |
| `/interrupted` | yes, per section | `_nothing` per section (`pages.py:1354`, `1367`), `withheld_note` (`pages.py:1375`) |
| `/cards` | yes | `_nothing` (`pages.py:1556`), `withheld_note` (`pages.py:1573`) |
| `/anomalies` | yes | `_nothing` (`pages.py:1412`), `withheld_note` (`pages.py:1453`) |
| `/log` | yes | `_nothing_on_this_page` (`pages.py:2034`) when empty, its own scoped note (`pages.py:2174`) when not |
| `/item/<id>` | **no** | `db.get_work_item` takes no `include_simulated` — "an explicit id is already explicit" (`db.py:169`). A simulated item is shown in full, so nothing is withheld to disclose. |
| `/item/<id>/confirm/<action>`, `/card/<id>/confirm/...` | no | same: lookup by identity |
| 404, 405, refusals | no | no rows, and `_bare` carries no visibility key at all |

Two of the three "yes" rows beyond `WITHHELD_VIEWS` — `/cards` and `/anomalies` — use the same
`_nothing` / `withheld_note` pair the parametrised tests already exercise on `/active`,
`/queue` and `/interrupted`. `/log` says it in its own words because its rows are audit
records read from a bounded page of a file rather than database rows, and its count is
honestly scoped to that page.

**Consequence**: the parametrised `WITHHELD_VIEWS` list covers three of the six. The new
section on `/interrupted` adds a fourth state to one of those three, and the other three views
are covered by their own tests. FR-013 asks that the verification be *made*, and the table
above is it; the implementation adds the failed section to the parametrised coverage so the
new section is held to the same rule as its neighbours.

**The one case the removal changes**: a `plan` instance where the operator has explicitly
asked for rows to be hidden, on a page with nothing withheld. There the pill was the only way
back to `include_simulated=1`. It is now reached through the nav — every generated link
restates the preference, and the operator who stated `0` restated it themselves. The route
back that R9 was protecting is the one offered when there is something to reveal, and that one
stays.

## R7 — The withheld accounting on the grown page

**Decision**: the failed section joins the existing arithmetic unchanged in form —
`_visible` gives it its own withheld count, `_nothing` carries that count when the section
renders nothing, and the page's `withheld_note` adds it only when the section rendered rows.

**Rationale**: `withheld_note`'s docstring states the invariant — "a view discloses each
withheld row exactly once", the empty sections' counts and the note's count being disjoint and
together the whole. Adding a fourth term to both sides of that sum keeps it true. Getting this
wrong in either direction is a number stated twice or a number lost, which is the family of
defect milestone 009 exists to prevent.

`data["withheld_simulated"]` becomes the sum of all three sections, and `data["counts"]` gains
a `failed` key, so the JSON body says the same thing the page does.

## R8 — The cost of a third section

**Decision**: accepted. `interrupted_view` calls `_items` a third time.

**Rationale**: `_items` calls `operations.status`, which takes a health reading, an ordering
plan and (when not handed one) a capacity snapshot per call. The view already pays this twice;
a third is the same proportional cost on a page one person opens. The alternative — a bulk
path that fetches three states in one call and splits them — is a new helper with one caller,
built to save work nobody has measured, on a local interface. Principle I says no, and
matching the shape of the two sections beside it is what makes the third section obviously
correct to read.

The capacity snapshot, which is the expensive part, is already handed down by `handle` and
shared across all three calls.

## R9 — Accountability and interruption

**Decision**: this feature logs nothing, and has nothing to recover.

**Rationale**: every change is in rendering. No state outside the process changes: no file is
written, no command is run, no network call is made, no notification is sent. Principle III's
obligation attaches to actions that change external state, and there are none — this is not an
unlogged action but the absence of one. The reads it adds are one extra `SELECT ... GROUP BY`
per page render and one extra listing on one page.

Killed halfway, the consequence is a truncated HTTP response. The next request renders from
scratch. There is no checkpoint to take, because there is no progress to lose.

## R10 — What replaces the two overruled comments

**Decision**: both comments are rewritten to carry the overruling argument, the earlier
position, and why the earlier position lost. Neither is deleted.

**Rationale**: the issue asks for this explicitly, and this codebase's convention is that
docstrings and comments carry "the reasoning for decisions that look wrong without it". Both
decisions look wrong without it — a bar that shows the effect level except when it is live, and
a visibility toggle that appears only in one direction, are each surprising enough that the next
reader's first instinct will be to "fix" them back. The comment is what stops that.

For the level pill: the old argument (decorating `live` trains the reader to ignore the pill)
was about not *alarming* at `live` and remains right; it simply does not reach the question of
whether a calm pill belongs there, which the rest of the bar — pause pill, effect-mismatch
banner, cap-disagreement note, simulated-consequences banner, all absent when silent — already
answers the other way. And `unknown` keeps its pill, because "we could not tell" is news.

For the visibility pill: 009 R9's complaint was that "nothing on the page suggests the
parameter exists". `withheld_note` did not exist when that was written and now answers it
exactly — at the moment there is something to reveal, beneath the table that withheld it. The
comment records that R9 is satisfied elsewhere rather than abandoned, and records the
asymmetry with the level pill (below `live` the default is to *include*, so this pill is
normally visible on a testing instance) so that it reads as intended rather than as a bug.
