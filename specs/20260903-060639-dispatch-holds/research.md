# Phase 0 Research: Holding Items and Repositories Out of Dispatch

**Feature**: [spec.md](spec.md) | **Issue**: [#117](https://github.com/jantman/robot-army/issues/117)

The Technical Context carried no `NEEDS CLARIFICATION`: this feature adds no dependency, makes
no network call, and introduces no technology the project does not already run. What it does
have is a set of placement decisions inside machinery that already exists, and getting those
wrong is how a small feature becomes a permanent tax. Each is recorded here with the
alternative it displaced.

---

## R1: Two tables with real foreign keys, not one table with a scope column

**Decision.** `item_holds` keyed on `work_items(id)` and `repo_holds` keyed on
`repos(repo_key)`, each with `ON DELETE CASCADE`.

**Rationale.** FR-025 says a hold must not outlive the thing it holds. `db.connect` sets
`PRAGMA foreign_keys=ON` — the module docstring says the schema relies on them, and
`tests/unit/test_migrations.py` asserts it — so a foreign key with `ON DELETE CASCADE` makes
FR-025 true by construction and costs nothing to keep true.

A single `dispatch_holds(scope, target, ...)` table cannot have that. `target` would point at
`work_items.id` for one scope and `repos.repo_key` for the other, and no foreign key can
express "one of these two depending on a sibling column". FR-025 would then have to be
maintained by hand at every deletion site — today that is `db.purge_simulated`, tomorrow it is
whatever deletes next, and the one that forgets leaves an unattributable hold row holding an
item id that has been recycled. `ordering.py` already states the general form of this
objection about copying configuration into a table: *the sync would be the bug*.

Making the target column the `PRIMARY KEY` of each table also delivers FR-004 for free: at
most one hold per target, so "hold it again" collides with itself and becomes the reported
no-op rather than a second row that would have to be deduplicated on read.

**Alternatives considered.**

- *One polymorphic table with a `scope` discriminator.* Rejected above. It looks smaller — one
  table instead of two — but it trades a constraint the database enforces for an invariant the
  code has to remember, which is the opposite of the trade this project makes everywhere else.
- *A `held` column on `work_items`, plus a `held` column on `repos`.* Rejected. `repos` is an
  approval record — migration 005 is emphatic that it stores what a human approved at a
  verified location and that nothing re-derives after approval. A temporary, frequently
  toggled runtime flag does not belong in it, and `repo_projects` already established that
  discovered/mutable per-repository facts live in their own table rather than in `repos`. On
  the item side, a column would put a dispatch *policy* inside the item's own row, where every
  reader of `WorkItem` would have to know about it; a sibling table keeps the item's lifecycle
  untouched, which is what the spec's Key Entities section promises.
- *A JSON file beside the database.* Rejected. Two stores, two interruption stories, and
  `dispatch_control` already demonstrated that a durable dispatch decision belongs in the
  database alongside the data it governs.

---

## R2: `held` ranks directly below `paused`

**Decision.** `HoldReason`'s declaration order becomes:

```
paused > held > capacity_unobservable > global_cap > repo_cap > awaiting_merge
       > not_onboarded > off_column > preparation_failed
```

**Rationale.** `HoldReason`'s docstring establishes what the ordering is *for*: the first
applicable reason is the only one reported, and the rank answers "which fix actually works".
Every reason below `held` sends the author somewhere that cannot help — freeing a session
slot, merging a pull request, re-onboarding a clone, moving a card, clearing stale failure
residue — while the item stays exactly where it is, because the author held it. Only `paused`
outranks it, and only because a paused system is not dispatching anything at all, so naming a
single item's hold would understate what is stopping the queue.

Sitting above `capacity_unobservable` deserves its own sentence, since that reason otherwise
outranks everything below it. Its stated justification is that when capacity cannot be
observed the cap *numbers* are untrustworthy and showing an untrustworthy number is worse than
showing none. `held` is not a number and is not derived from the observation, so that
justification does not reach it: a held item is held whether or not `/proc` could be read.

**Alternatives considered.**

- *Below `global_cap`, beside `repo_cap`.* Rejected. It is superficially attractive because a
  repository hold resembles a repository cap, but it would tell an author with a full machine
  to wait for a slot that will free and change nothing.
- *A hold that suppresses the item from the plan entirely, with no reason at all.* Rejected by
  FR-014 and by the queue's whole design: a surface that silently omits work is the failure the
  queue view exists to prevent.

---

## R3: `held` is a per-item hold, not a global one

**Decision.** `dispatch._GLOBAL_HOLDS` is unchanged: it stays
`{paused, capacity_unobservable, global_cap}`. A held entry is skipped (`continue`), never a
pass-ending `break`.

**Rationale.** That set's docstring already draws the line this decision falls on: a global
condition ends the pass because no later item could fit where this one could not; anything
else is a condition of one item, and *a queue that stops on one item's condition is a queue
where one blocked repository stalls every other*. A hold is the most literal possible instance
of that — FR-011 says holding a repository holds that repository's work and never the queue,
and the issue's own scenario is four items from one repository sitting in front of work the
author does want to run. Making `held` global would leave the reported problem exactly as it
was.

`awaiting_merge` set the same precedent one milestone ago for the same reason, so this is
consistency rather than a new judgement.

**Consequence, recorded so it is not mistaken for an omission.** `select_and_dispatch` already
handles per-item holds in the log: `first_held` remembers the first one seen, and a pass that
dispatches nothing records it through `_note_hold`. Holds inherit that with no change to
`dispatch.py` at all.

---

## R4: One reason, with a detail that names both holds when both apply

**Decision.** When an item is held individually *and* its repository is held, `_hold_for`
returns a single `HoldReason.HELD` whose detail states both and says that releasing one leaves
the other in force.

**Rationale.** FR-015 keeps the one-reason-per-item rule the whole precedence exists to
enforce; `HoldReason`'s docstring is explicit that *two reasons shown at once is how a surface
stops being read*. But collapsing to one reason without naming both would produce the specific
failure FR-017 is about: the author releases the item hold, expects it to run, and it does not,
with the surface still saying "held" and looking like it ignored the release.

The detail field is the right place because it already carries exactly this kind of
specificity elsewhere — a repository and its two numbers, a named blocking issue, the column a
card is parked in.

**Alternatives considered.** Two separate reasons (`held` and `repo_held`) with a defined
precedence between them. Rejected: it doubles the enum for a distinction the detail already
carries, and whichever ranked lower would be the one the author never sees at the moment they
most need to — which is the whole bug.

---

## R5: Holds are resolved once per plan, alongside the other per-plan facts

**Decision.** `plan` gains two reads — `db.list_item_holds(conn)` and
`db.list_repo_holds(conn)` — taken once for the whole plan and passed into `_hold_for`, in the
same position and for the same reason as `resolved`, `unfinished`, and `boards`.

**Rationale.** `plan` runs on every dispatch tick *and* every web page render, and the module
already establishes the rule: a fact needed by many items is resolved once for the whole plan
rather than per item. Both tables are keyed by their target and hold at most a handful of rows
for one author, so each read is a single scan into a dict.

`plan` stays pure — two more reads, no writes, no network, no filesystem — which is what keeps
the queue view and the dispatcher the same function rather than two functions that agree.

---

## R6: The target is stated, never inferred from its shape

**Decision.** `robot-army hold <item_id>` and `robot-army hold --repo owner/name`, exactly one
of the two. Neither, or both, is a usage error (exit 2). Same for `unhold`.

**Rationale.** An item id is an integer and a repository key contains a slash, so a single
argument could be classified by shape. This codebase refuses that class of guess on principle
and has the scars to show for it: an ambiguous board column is reported rather than guessed at,
a card id is matched against a strict pattern rather than accepted as an opaque segment. A
mistyped repository key that happened to parse as something else would silently hold the wrong
thing.

Both spellings already exist in the CLI, so nothing new is invented: per-item verbs
(`cancel`, `retry`, `abandon`, `attach`) take a positional id, and `poll --repo` already takes
a repository key as a flag.

Unknown targets are refused rather than accepted (FR-006). A repository key is checked against
`repos.known(conn)` — the established answer to "which repositories does this system watch",
and deliberately not `sorted(config.repos)`, since a `[repos.*]` section for a repository that
was never onboarded describes one the system does not watch.

---

## R7: A repository key travels in the form body, never in the URL path

**Decision.** Item holds are `POST /item/<id>/hold` and `POST /item/<id>/unhold`, matching
every other per-item action. Repository holds are `POST /repos/hold` and `POST /repos/unhold`,
carrying `repo=owner/name` as a form field.

**Rationale.** A repository key contains a slash, so putting it in the path means either two
path segments or an encoded one. `web/server.py`'s `_bind` matches on segment count, and the
`_CARD_ID` comment states the standing position on this directly: *a route parameter that
reaches a page is one an attacker would like to control*. A two-segment repository parameter
would create exactly the kind of path shape — `..`, encoded separators — that the strict card
pattern exists to foreclose.

The form body is not a workaround; it is the pattern already in use. `_job_action` reads
`request.first("repo")` for `POST /poll`, so "a repository key arrives in the form" is
established, and the value is validated against `repos.known` before it reaches anything.

Both routes declare their `terminal=` verb, which is what
`test_web_routing`/`test_cli_exit_codes`'s paired enumeration checks — so the parity required
by FR-007 is verified rather than asserted.

---

## R8: Neither hold nor unhold is effect-guarded

**Decision.** `require_effect_agreement` is not called by any of the four routes, matching
`_pause_action`.

**Rationale.** The tempting asymmetry — guard `unhold` because releasing can lead to a session
starting — guards the wrong side of the causal chain. Unholding starts nothing. It removes one
row, after which the *dispatcher* decides whether to dispatch, and the dispatcher applies the
effect level itself at the moment it acts. Guarding the release would be the same mistake as
putting a network read inside `plan`: attaching a decision to the surface that displays a fact
rather than to the code that acts on it.

`_pause_action`'s existing comment carries the other half: a stopping action must remain
available precisely when the guard would fire, or the interface has no safe action at the
moment one is most wanted. Holding is a stopping action. Unholding is its undo, and an author
who cannot undo a mistake made during a mismatch is worse off, not safer.

These four routes still pass through `_perform`, so same-origin checking and the
intent-before-action audit pair apply unchanged.

---

## R9: A dedicated `holds` verb, plus a conditional line in `status`

**Decision.** `robot-army holds` lists every hold in force and says plainly that nothing is
held when there are none. `robot-army status` gains a single summary line **only when at least
one hold exists**, pointing at that verb. `holds` joins `READ_COMMANDS`.

**Rationale.** US3's failure mode is a hold set and forgotten, so it must be discoverable
without remembering that holds exist — which is why `status` mentions them. But a permanent
"no holds in force" line in `status` is noise on every one of the overwhelmingly common runs
where nothing is held, and noise is how a surface stops being read. Making the line
conditional on there being something to say gives US3's discoverability at zero cost when
there is nothing to discover.

The full listing is a separate verb rather than a `status` section because it must answer
US3 AS3 explicitly — *nothing is held* is an answer, not an empty table — and because it shows
things `status`'s item-oriented sections structurally cannot: a repository hold matching no
queued item, and a hold on an item that is no longer eligible.

**Alternatives considered.** Folding the listing into `status` (rejected: cannot answer AS3
without adding a permanent line, and cannot show a hold that matches no row it renders) and a
`hold list` subcommand group (rejected: `pause`/`unpause` are flat verbs and holds are their
sibling; a group would make the common verbs longer to type for no gain).

---

## R10: No expiry, no note, no sweeper, no configuration

**Decision.** A hold is present or absent. It carries `held_at` and `held_by` and nothing else.
Nothing releases it but the author. No configuration key is added.

**Rationale.** FR-026 forbids automatic expiry outright — a hold that lapses on its own
silently starts work the author stopped — and with no expiry there is nothing for a background
sweeper to do, so none is built. A free-text note is the other plausible field, and the audit
log already records the placement with far more context than a note would carry, while the
listings show the age; adding one now would be a field with one writer and no reader.

The consequence worth stating: `share/config.example.toml` is **unchanged**, and so is
`config.py`. Repository holds are deliberately not configuration — they are temporary, and the
issue asks for them to be settable from the web interface, which does not edit TOML. The
standing preference that *does* live in configuration, `[repos.*].priority` with
`order = "repo-priority"`, keeps its meaning untouched.

---

## R11: A hold on an item that has finished is left visible rather than swept

**Decision.** Holds are not cleared when an item reaches `done` or `abandoned`. `robot-army
holds` shows each held item's current state, so a hold on finished work is visible as such and
the author clears it.

**Rationale.** Clearing on a state transition is automatic release under another name, which
FR-026 rules out, and it would mean every transition site had to know about holds. Leaving the
row costs one line in one listing for one author. The genuinely dangerous case — a hold
outliving the *row* and attaching itself to a recycled id — is foreclosed by R1's cascade, and
that is the case worth spending a constraint on.

`db.purge_simulated` therefore needs no change: deleting a simulated `work_items` row cascades
its hold away. That this is true rather than merely intended is worth a test, since it is the
only deletion path in the system today.
