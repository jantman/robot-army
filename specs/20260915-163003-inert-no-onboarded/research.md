# Research: Say so when nothing is onboarded

No `NEEDS CLARIFICATION` remained after the spec. These are the design choices and why.

## R1 — What "nothing onboarded" counts

**Decision**: `repos.resolved_all(conn, config)` being empty — the same mapping
`resolve_repository` matches against.

**Rationale**: the spec's edge case requires `doctor` and the card text never to disagree about
whether the system is inert. `repos.known()` (raw rows) can be non-empty while `resolved_all` is
empty — a pre-005 row with no section and no recorded path — and in that state no card can
resolve. Counting the resolved set means `doctor` fails exactly when every card would be held.

**Alternatives**: `repos.known()` — rejected for that disagreement.

## R2 — Where the short-circuit sits in `resolve_repository`

**Decision**: immediately after `onboarded` is read, before `_resolve_declarations`.

**Rationale**: with nothing onboarded, the declaration path would produce "the `robot-army:`
line names X, which is not an onboarded repository — onboarded: none", which is the same
blame-the-card failure in different words. Checking first makes the card's content irrelevant,
which is true: nothing the card could say resolves.

**Alternatives**: special-casing the two existing reasons' text — rejected; two places to keep in
step, and the comment still told the author to add a line.

## R3 — How the comment knows which text to use

**Decision**: a module constant `NOTHING_ONBOARDED` holds the reason; `_needs_info_comment`
compares its argument to it.

**Rationale**: the comment is built from the stored reason string (`_hold_for_info` receives only
the reason, and `commented_reason` persists it). The reason is fixed text — it lists no
repositories, because there are none — so equality is exact and needs no new field on
`Resolution`, no new parameter threaded through `_hold_for_info`, and no column.

**Alternatives**: a `kind` field on `Resolution` threaded to the comment — rejected as more
moving parts for the same answer (Principle I).

## R4 — The audit `source` value

**Decision**: `source: "onboarding"`.

**Rationale**: `source` names the origin of the *outcome* (`Resolution` docstring). Neither
`scan` nor `declaration` decided this one — the empty onboarding set did, and for a card that
carries a `robot-army:` line `scan` would be false. The value is free-form in the audit detail;
the guide's two mentions of the value set are updated.

## R5 — `doctor`'s check shape

**Decision**: one check named `onboarded repositories`, placed just before the per-repository
lines; passes with `N onboarded`, fails naming `robot-army onboard` and the database path.

**Rationale**: `doctor` already fails on any failed check with `EXIT_CHECK_FAILED` and lists
`failures` by name, so a check is the whole mechanism (FR-003). Naming the database path in the
failure is what lets an operator who did not delete it realise it was lost or replaced.

**Alternatives**: a warning rather than a failure — rejected; the issue's point is that nothing
drew the eye, and `doctor` exits zero on warnings.

## R7 — Getting the card re-evaluated after onboarding

**Decision**: in the held-card activity gate, a card whose `reason` is `NOTHING_ONBOARDED` is
not short-circuited when `resolved_all` is non-empty.

**Rationale**: the gate re-evaluates a held card only when its board activity changes, which is
right when the fix is an edit to the card. Here the fix is onboarding, which changes nothing on
the board — so without this the card stays held forever, and a comment promising it will be
picked up would be false. The onboarded set is read only for cards carrying this reason, so no
other card pays for it; and while nothing is onboarded the gate holds as before, so an inert
installation does not write a `trello.evaluated` record for every held card on every poll.

**Alternatives**: tell the operator to run `rescan --all-needs-info` — rejected; it is a second
step the operator has to know about, on a failure whose whole problem was advice that did not
work. Re-evaluating on every poll regardless — rejected for the log noise.

## R6 — Fresh installations

**Decision**: accept that `doctor` fails before the first `onboard`, and say so in the setup
guide.

**Rationale**: a fresh installation is as inert as one that lost its database. Making the check
pass when the database is "new" would need a notion of new that survives exactly the event this
issue is about.
