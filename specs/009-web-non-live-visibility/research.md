# Research: The Web Interface Shows Its Work and Announces Non-Live Mode

**Feature**: `009-web-non-live-visibility` | **Date**: 2026-08-29

Phase 0 decisions. Every NEEDS CLARIFICATION from Technical Context is resolved here; there
were none carried over from the spec, because the one contested question — which polarity the
alarm takes — was settled by the issue author in the issue thread before specification.

---

## R1 — Where the default is resolved

**Decision**: at the request edge, inside `web/server.py`, from two values that are both
already in scope there: the operator's stated preference (parsed from the request) and the
effect level (`ctx.effect_level`, reached through the effective-level rule of [R4](#r4--which-effect-level-drives-the-banner-and-the-pill)).
A single pure function, `include_simulated_for(request, ctx) -> bool`, called at each of the
~12 sites that read `request.include_simulated` today.

**Rationale**: `Request` is parsed from the wire before any database handle exists, so it
cannot know the effect level; `pages.*` receives a boolean and should keep receiving one, so
the views stay pure functions of their arguments. The edge is the only place both facts meet.
A free function rather than a value threaded through `params` because every call site —
including `_perform`, which builds the redirect target — already holds both `request` and
`ctx`, so plumbing would add a parameter to four action-handler factories to carry something
they can derive in one line.

**Alternatives considered**:

- *Compute once in `handle()` and pass through `params`*. `handle` already injects `chrome`
  this way, so the precedent exists. Rejected because `_perform` is not reached through
  `params` — it is called from inside handler bodies — so the value would have to be threaded
  a second way regardless, and two mechanisms for one fact is worse than one recomputation of
  a comparison.
- *A configuration key, `[web] include_simulated`*. Rejected under Principle I: it is a knob
  with one correct setting per effect level, which is to say it is the effect level wearing a
  disguise, plus a way for the two to disagree.
- *Have `Request` carry the level*. Rejected: `parse_request` is the one place external input
  is parsed and it deliberately touches nothing else. Handing it a database context to satisfy
  a display default inverts that.

---

## R2 — A tri-state preference, not a boolean

**Decision**: `Request.include_simulated` (a `bool` property) becomes
`Request.simulated_preference` returning `bool | None`. `1/true/yes/on` → `True`;
`0/false/no/off` → `False`; absent, empty or unrecognised → `None`, meaning *unstated*.

**Rationale**: "the operator did not say" and "the operator said no" are different facts and
the whole feature turns on telling them apart. The existing `TRUTHY` frozenset gains a
`FALSEY` twin; the parse stays a set membership test on a lowercased string.

FR-004 requires an unrecognised value to resolve to the default rather than error. Folding
unrecognised into `None` rather than into `False` is what makes that true, and it is also the
forgiving direction: a mistyped `?include_simulated=treu` below `live` shows the rows, which
is what the person wanted.

**Alternatives considered**:

- *Keep the boolean and add a second `simulated_preference_stated` flag*. Two fields that must
  agree, where one three-valued field cannot disagree with itself.
- *Reject an unrecognised value with a `400`*. Rejected: the parameter is typed by hand from a
  phone, which is precisely when a typo should not produce an error page.

---

## R3 — How the preference survives navigation

**Decision**: every link and form the interface generates states the value **explicitly and in
both directions** — `include_simulated=1` or `include_simulated=0`, never omitted. Two
functions change: `pages._query` and `server.html_query`, plus the hidden form field that
mirrors them.

**Rationale**: FR-003 requires the operator's choice to survive navigation, action submission
and auto-refresh. Today's propagation omits the parameter when the value is false, which was
correct while false was also the default — omission and `False` meant the same thing. Once the
default varies by level, omission means "use the default" and can no longer stand in for
`False`: an operator who explicitly hid rows below `live` would have them reappear on the next
click.

Always stating it makes a single boolean round-trip correctly in both directions, with no
second field threaded anywhere. The cost is a slightly longer URL and the fact that a link
copied from a page below `live` carries `=1` if pasted at `live` — which is the explicit
override behaving exactly as specified, not a defect.

**Alternatives considered**:

- *Thread the tri-state through `pages.*`*. A `Visibility` value object carrying both the
  resolved boolean and the stated preference, replacing the `include_simulated: bool` keyword
  on roughly fifteen functions and in every test that calls them. Rejected under Principle I:
  fifteen signatures and a new type, so that one function can decide whether to emit a
  parameter — a tax paid everywhere for a decision made in one place. The two-line alternative
  above is behaviourally identical.
- *A cookie or `localStorage`*. Rejected: it is state, it needs an expiry policy, it survives
  in a place the URL does not show, and it makes two browser tabs disagree about what they are
  looking at. The URL is already the interruption-tolerant place to keep a per-view preference
  (Principle IV).

---

## R4 — Which effect level drives the banner and the pill

**Decision**: one **effective level**, defined as the more simulated of two values — the
interface's own configured level (`ctx.effect_level`) and the running daemon's level (from the
heartbeat) — with "a daemon is running but its level cannot be read" treated as most
simulated. `EffectLevel`'s members are declared in ascending order of consequence, so the
comparison is `min(..., key=list(EffectLevel).index)` and needs no new ordering table. The
banner and the pill both read this one value (FR-018).

**Rationale**: the two levels can disagree — the daemon may have been started with
`--effect-level plan` while the configuration says `live` — and the interface already detects
this and refuses mutations. But for the *display* question the two levels answer different
halves: the rows on the page were written by the daemon at the daemon's level, while an action
the operator takes next would run at the interface's. Taking the more simulated of the two is
the only rule under which the page cannot claim to be real about either half, and it makes the
edge case in the spec — a `live` pill above rows a `plan` daemon invented — unreachable.

When the daemon's level is genuinely unknown, the existing `EFFECT LEVEL UNKNOWN` banner
already explains the situation and says actions are refused. The pill takes alarm styling and
**no second banner is emitted**, so the page carries one explanation rather than two competing
ones.

**Alternatives considered**:

- *`ctx.effect_level` alone*. Simpler, and wrong in exactly the case the spec's first edge case
  names: interface `live`, daemon `plan`, page full of invented issue numbers, pill calm.
- *The daemon's level alone*. Wrong in the other direction, and undefined when no daemon holds
  the lock — which is a normal state in which the configured level is the right answer.
- *Two rules, one per surface*. Rejected by FR-018: a pill and a banner that can disagree is
  the defect this feature exists to remove, reproduced one layer up.

---

## R5 — Where the banner's consequences come from

**Decision**: a table in `effects.py`, `SIMULATED_CONSEQUENCES: dict[str, str]`, mapping each
boundary name in `REAL_AT` to one operator-facing phrase, plus
`consequences(level) -> list[str]` returning the phrases for boundaries that are *not* real at
that level, in the table's declaration order. A unit test asserts that every key of `REAL_AT`
has a phrase and that no phrase is orphaned.

**Rationale**: FR-013 requires the stated consequences to be those that actually hold at the
current level, not one message reused below `live`. `REAL_AT` is already the single source of
truth for which boundary is simulated where, and it is already written as data specifically so
a test can assert the whole table. Deriving the banner's membership from it means the banner
cannot drift when a boundary's level set changes; keeping the wording hand-written means the
sentences read like a person wrote them.

This falls out naturally as level-specific text. At `plan` every boundary but the two readers
is simulated; at `local` the version-control and hook boundaries become real; at `no-remote`
the session host and display do too, leaving the three outward-facing writers; at `live` the
list is empty and no banner renders.

The issue's specific complaint — that `#900001` is on screen looking like a real issue link —
is the `issue_writer` phrase, which names both the unwritten comment and the invented number.

**Alternatives considered**:

- *Three hand-written paragraphs keyed by level*. Rejected: four strings that must be kept in
  agreement with a table that already exists, with nothing to catch the drift.
- *Generating sentences mechanically from boundary names*. Rejected: "no `version_control` is
  really performed" is the implementation talking, and FR-012 requires operator terms.

---

## R6 — The simulated row marker is already built

**Decision**: no new marker and no new CSS. `html.SIMULATED_MARK` is already
`<span class="sim" title="simulated (dry-run) row">simulated</span>`, and `.sim` already has a
badge rule — a warn-coloured background, dark text, bold, its own padding and radius. US3's
requirement is met by what exists.

**Rationale**: the issue describes "the current `*` suffix", which is the **CLI's** convention,
introduced by milestone 008 for the terminal tables. The web has rendered a word-badge since
milestone 002. Reading the source rather than carrying the issue's assumption forward removes
a whole work item.

What is *not* already guaranteed is coverage: `mark_simulated` is called from six sites in
`pages.py`, and nothing pins that every row-bearing view is one of them. So US3 becomes a test
rather than a change — a rendering test that walks each view holding a simulated row and
asserts the badge is present, which is what FR-019 actually asks for and what would catch a
seventh table added later without one.

The genuinely unstyled class is the one the issue named second: `.pill.level` has no CSS rule
at all, which is [R7](#r7--how-the-level-pill-is-styled).

**Alternatives considered**:

- *Strengthen the badge anyway*. Rejected: it is already the loudest thing in a table row.
  Making it louder to satisfy a requirement written from a misreading is change for its own
  sake.

---

## R7 — How the level pill is styled

**Decision**: `_chrome_bar` gives the level pill a second class — `pill level simulated` below
`live`, `pill level live` at `live` — and `html.py`'s stylesheet gains two rules: the simulated
form takes the error colour for its border and text and a bolder weight, the live form takes
the muted treatment `.quiet` already defines.

**Rationale**: FR-016 and FR-017, with the polarity the issue author settled. The error colour
rather than warn because the existing `banner error` used by `DAEMON NOT RUNNING` and by the
mismatch is the stated precedent for weight, and because warn is already spent on capacity and
on a paused dispatch — two conditions that are *less* consequential than "none of this is
real" and would otherwise outrank it.

The pill's text also gains the word `simulated` below `live`, so colour is not the sole carrier
of the signal for a colour-blind reader or a monochrome screenshot.

**Alternatives considered**:

- *Reuse `.pill.warn`*. Rejected: it would render identically to the capacity pill beside it,
  which is the exact failure the issue reports — a pill that means "nothing here is real"
  drawn no louder than one that means "two sessions are running".
- *Style the `live` pill green (`.ok`)*. Rejected by the settled polarity: `live` is the
  expected state and gets no decoration at all, so the alarm keeps its meaning.

---

## R8 — Where the withheld counts come from

**Decision**: reuse milestone 008's numbers; add no query.

- Work-item views (`/active`, `/queue`, `/interrupted`) already call `operations.status`
  through `pages._items`, whose payload carries `withheld_simulated: {counts, items}`. `_items`
  currently returns only `data["items"]` and discards the rest; it returns the withheld count
  alongside the rows.
- The cards view calls `operations.cards`, which **computes** `withheld` and prints it in the
  text lines but does not put it in `data`. One key added to that payload closes the gap for
  the web view and, incidentally, for `robot-army cards --json` — the same absent-versus-zero
  ambiguity 008 removed from `status`'s payload, still present in `cards`'s.

**Rationale**: FR-007 requires the stated count to be the number the override would reveal,
under the view's own filters. 008 already established that discipline, computing the item count
with the same `states`/`repo_key` filters the listing used, and the web views filter by state
through the same call — so the number is correct for free. Computing a second one here would
risk it being merely close, which 008's plan calls "a subtler contradiction".

**Alternatives considered**:

- *A new `db` accessor per view*. Rejected: the accessors exist, and a third counting path is a
  third place for the filters to fall out of step.
- *Only disclose when the view came out empty*. Rejected by FR-006 and by 008's own finding —
  two visible rows beneath a six-row queue is the same defect one notch quieter.

---

## R9 — Making the override discoverable

**Decision**: the existing chrome pill that reads `simulated rows included` becomes a link that
toggles the preference, and it renders in **both** states — `simulated rows included` and
`simulated rows hidden` — rather than only when they are included.

**Rationale**: the issue's actual complaint is not that the parameter is missing but that
"nothing on the page suggests the parameter exists". A toggle in the chrome, which every view
already renders, is one tap on a phone and satisfies SC-007's "reachable in a single request"
without a new control, a new route or a form. The withheld note ([R8](#r8--where-the-withheld-counts-come-from))
links to the same target, so the disclosure and the remedy are the same gesture.

It stays `.pill.quiet` in both states: it is a control, not a warning, and the warning is the
banner's job.

**Alternatives considered**:

- *A checkbox in a form*. Rejected: it needs a `POST` or JavaScript, and every mutating request
  goes through the audit path — auditing a display preference as an action would dilute the
  action log for no gain.
- *Leaving the pill as a label and relying on the withheld note*. Rejected: below `live` with
  everything shown, nothing is withheld and there is no note, so the way back to the hidden
  view would be undiscoverable in exactly the default state.

---

## R10 — Why the existing test suite is expected to pass untouched

**Decision**: treat the current web suite as regression coverage for `live` and add a
level-parameterised harness rather than changing the existing fixtures.

**Rationale**: `tests/conftest.py:132` sets `effect_level: "live"` in the base configuration, so
every existing web test runs at the one level where this feature changes no default. That makes
the 2,455 existing lines a direct check on the strongest constraint in Technical Context —
behaviour at `live` with no stated preference is byte-identical — without writing anything.

The one place existing tests will need adjusting is any assertion on a *generated URL*, because
[R3](#r3--how-the-preference-survives-navigation) makes links state `include_simulated=0` where
they previously omitted it. That is a small, mechanical, and deliberately visible break: a test
that asserts on the query string is exactly the test that should notice this change.

**Alternatives considered**:

- *Flip the base fixture to `plan` to exercise the new path everywhere*. Rejected: it would
  silently rewrite what several hundred existing assertions are testing, and lose the `live`
  regression coverage that makes this change safe.
