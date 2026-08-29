# robot-army — Spec Roadmap

Maps the milestones in [`initial-planning/robot-army-planning.md`](initial-planning/robot-army-planning.md) §15
onto Spec Kit features. Each entry below becomes its own `specs/NNN-*/` directory with its own
spec → plan → tasks → implement cycle.

**Why more than one spec.** The planning document covers sixteen subsystem areas. Collapsing them
into a single specification would produce a requirements list too large to plan against, and would
make the constitution's Simplicity First review gate unenforceable — you cannot honestly ask "is
this the design with fewest moving parts?" of a hundred requirements at once. The milestones in the
planning document already draw the seams; this roadmap follows them.

**Order is dependency order, not preference.** Each milestone assumes the one before it exists.

---

## 001 — Minimum Daemon (`specs/001-minimum-daemon/`)

**Status:** implemented (#2)

Planning doc M1. The end-to-end loop with no Trello and no web UI: watch GitHub for labelled
issues, prepare an isolated worktree, launch a real Claude Code session in the running kitty
instance, track its state, and recover correctly from every way that can go wrong.

Includes the cross-cutting requirements the planning doc states as non-deferrable: the graduated
dry-run effect levels (§2), reconciliation on startup *and* on a timer (§8), the health signal
(§14, explicitly "not a stretch goal"), and terminal-reachable inspection and control, since there
is no other interface until 002.

**Deliberately excluded:** Trello, the web UI, per-repo concurrency caps, priority modes, out-of-band
session accounting, and automatic worktree cleanup.

## 002 — Web UI & HTTP API (`specs/002-web-ui/`)

**Status:** implemented (#4)

Planning doc M2. Active-sessions, queue, and interrupted views; resume / abandon / restart / attach
controls with the resume-decision signals from §8; the audit log with clickable issue, card, and PR
links; pause-dispatch and force-poll controls. Reachable from the author's phone, which is the
ergonomic point of it.

Depends on 001 for the state model, the audit record, and the session-control operations — the UI
is a second front-end onto commands 001 already exposes, not a new set of capabilities.

Two decisions were taken during specification. The interface **serves on the local network with no
in-application access control**, with the author's existing VPN providing remote reach — the reading
that honours Principle II's "authentication and authorization MUST NOT be built" literally, at the
cost that anything able to reach the port has full control. And it **runs as its own command rather
than inside the daemon**, so the audit log and the interrupted-item list stay readable during
exactly the incident that makes them worth reading.

Pausing dispatch is the one genuinely new capability here rather than a second door onto an existing
one; per the constitution's terminal-reachability rule it gains a terminal command at the same time.

## 003 — Trello Source (`specs/003-trello-source/`)

**Status:** implemented (#6)

Planning doc M3. Card → issue creation, the `needs_info` state with `dateLastActivity` auto-rescan,
In Progress / Done card lifecycle, and the §11 loop-prevention invariant (one work item ⇒ at most
one issue ⇒ at most one card) with the mapping table as source of truth.

Depends on 001 having made the Work Item Source boundary real. This milestone is the first genuine
second implementation of that interface, and is therefore also the test of whether it was drawn in
the right place — 001 must not over-fit it to GitHub.

Note the planning doc's own warning: dry-run cannot validate the loop-prevention invariant, because
it does not write the mapping table. That invariant needs unit tests and a real run against a
throwaway board.

## 004 — Concurrency & Polish (`specs/004-concurrency-polish/`)

**Status:** implemented (#7)

Planning doc M4. The full concurrency model — global and per-repo caps, counting the author's own
out-of-band sessions against the global cap, queue position — plus per-repo configuration
overrides, priority and ordering modes, worktree cleanup policy, and event notifications.

Depends on 001's session registry scan, which already does most of the observation work this
milestone needs; 004 is largely policy layered on top of it.

Four decisions the planning document leaves open in §16 were taken during specification, each
recorded with its reasoning in the spec's Assumptions. **Worktree cleanup triggers on issue close
and is opt-in** — issue close is the trigger §6's 499 MB measurement argues for, and the
constitution's rule that irreversible actions must not be reachable by default settles the opt-in
half. **The per-repo cap defaults to one**, per §10. **Aging is not built**, per §5's own deferral,
so starvation under repository-priority ordering is accepted and documented rather than mitigated.
And **notifications reuse the health channel** rather than introducing a second delivery mechanism.

The global concurrency cap's *value* is deliberately not decided here. §16 lists it as open; the
spec treats it as configuration with a documented default, because only running the system answers
it. A branch guard is added beyond what §6 describes: git refuses to remove a dirty worktree for
free, but nothing stops a branch deletion from destroying unpushed commits, so that check has to be
made explicitly.

### What running it taught

Two things the design could not have known, both from `robot-army capacity` on the real machine.

**The cap's value is smaller than it looks, because I am most of it.** The first live run reported
`2 of 2 sessions running, 0 ours, 2 other` — with the daemon having started nothing at all. Two
Claude sessions of my own is my ordinary working state, not a busy day, so a global cap of 2 leaves
the daemon exactly zero slots and it would never dispatch. §16 left the number open on the grounds
that only running the system answers it; the answer is that **the cap has to be my usual session
count plus however many robots I actually want**, and the shipped default of 2 is therefore a value
that looks conservative and behaves as "off". Treat 3–4 as the starting point and expect to raise it.
That is exactly the class of thing this milestone existed to make visible, and it was invisible
before, because the old cap counted only the daemon's own bookkeeping.

**The `/proc` fallback matches more than Claude Code.** With the registry moved aside, the degraded
path counted **11** processes where the registry counts 2. Eight of them are Claude Desktop
(`/usr/lib/claude-desktop-bin/claude`), which shares the binary *name* the fallback matches on. The
over-count is in the safe direction and is announced as `degraded`, exactly as designed — but the
practical consequence is that a degraded observation on this machine means *permanently at capacity*,
not "slightly conservative". The fallback is milestone 001's, written for the orphan sweep where an
extra candidate is harmless; 004 is the first thing to gate dispatch on it. Narrowing it would mean a
new identification rule and FR-002 forbids the obvious one (command-line matching), so it is recorded
here rather than fixed: **if the registry ever becomes unreliable, fix the registry, do not lean on
the fallback.**

## 005 — Onboarding Is Enough (`specs/005-onboard-is-enough/`)

**Status:** implemented

Not from the planning document. This milestone came from *using* 001–004: adding a repository meant
editing a file, restarting the daemon, and then running `onboard` — three steps where one would do,
repeated 227 times if I ever wanted the repositories I actually own to be usable.

The answer to "which repositories does this system know about" moves from the `[repos.*]` section
keys to the `repos` table. `robot-army onboard owner/name` is the whole job: the clone's location is
derived as `<repo_root>/<name>` — **one** candidate, no searching — and a `[repos.*]` section becomes
a set of overrides for the exceptions rather than a registration for everything.

The derivation rule alone would have been a bad idea, and the milestone is shaped around why. It is
right for 222 of my 252 repositories and **wrong for five**, and the five fail by finding a real
clone of a *different* repository at the derived path. So derivation is paired with an origin check
that runs at onboarding, where a human is already reading an approval screen, and the **outcome** is
recorded rather than the rule. Nothing re-derives afterwards: a clone that moves produces a refusal
naming the recorded path instead of a worktree in a repository nobody named. The same three local
reads run again before every dispatch, because months pass between an approval and a work item.

It also closes [issue #8](https://github.com/jantman/robot-army/issues/8): `include_owned` and
`extra_repos` were parsed, validated, and read by nothing. They now govern what may be **onboarded**
— a mistake guard against a mistyped name, explicitly *not* a security boundary, since the author can
edit them and the issue-author check cannot be disabled. The enumeration the original 001 decision
implied was deleted rather than given a caller: nothing needs to enumerate 252 repositories to answer
a question one `GET /repos/{owner}/{name}` answers.

**User story 7 was dropped.** A `repos --onboardable` listing was the only story that would have
added a surface rather than removing a step, and the spec named the consequence in advance: dropping
it means deleting `list_owned_repos()` rather than leaving it uncalled. That was done.

### What running it taught

*To be filled in after the live round — in particular whether the derivation rule holds beyond the
222 repositories that were measured, and whether five is really the number of collisions.* The three
things CI cannot establish are recorded in [issue #1](https://github.com/jantman/robot-army/issues/1):
the five real wrong-location clones being refused, a clone moving out from under an approval, and the
request count being one against an account with 252 repositories rather than against a fake with
three.

## 006 — Trello Column Ignore List (`specs/006-trello-ignore-lists/`)

**Status:** implemented

Not from the planning document. [Issue #3](https://github.com/jantman/robot-army/issues/3),
filed from using 003. Milestone 003's intake rule is *a tagged card is intake*, and position
on the board is no part of it — but the tag is something I set once and the column is
something I change constantly, because moving cards between columns is what a board is for.
The first time a tagged card ends up somewhere I do not want it acted on, my only option is
to remove the tag: a destructive, one-way answer about what the card *is*, to a question that
is nearly always about *when*.

So intake becomes: a tagged card, in a column I have not excluded. `[trello] ignore_lists`
holds the column names, empty by default.

**Parking is reversible where untagging is not**, and that asymmetry is the milestone. A
parking space you cannot drive out of is a scrapyard, and the bug would be the worst kind
here — I do something reasonable, nothing happens, and I have no reason to suspect the system
rather than myself.

Making that true turned out to be the whole design problem. `_reconcile_board_contents` drops
every tracked card absent from the poll listing, and `dropped` is terminal: `CARD_TRANSITIONS`
gives it no exit. So the obvious implementation — filter ignored cards out of what the reader
returns — would have made parking an already-tracked card destroy it permanently and
silently. Ignored cards therefore stay in the listing, the exclusion happens in two guards
over one predicate, and **parked is derived rather than stored**: tracked, not linked, current
column in the ignored set. No new card state, so `cardstates.py` is untouched.

Two things the code forced that the plan had not anticipated. `BoardInfo.lists` is name-keyed,
so two board columns of the same name collapse and one would have quietly stayed intake;
`lists_by_id` fixes it by the shape of the data, from the same response, with no extra
request. And the listing commands must answer "is this parked?" with the board unreachable,
which the id alone cannot do — so `cards` stores the column's **name** beside its id, one for
each consumer, written by the same statement.

The gate's position inside `evaluate_card` is a contract rather than a style choice, and
`contracts/surfaces.md` states it as a table: each neighbour is fixed by a different
requirement, so a reordering breaks exactly one and nothing else notices. There is a test per
row.

### What running it taught

*To be filled in after the live round.* One item is design-relevant rather than merely
confirmatory, and is the first thing to check: whether dragging a card between columns moves
Trello's `dateLastActivity`. If it does not, the release path has to force one re-evaluation
instead of leaning on the activity-baseline short circuit. CI cannot settle it, and neither
can it prove that a real board with two same-named columns behaves as the constructed
`BoardInfo` fixture assumes.

## 007 — Spec Kit Awareness (`specs/007-speckit-extensions/`)

**Status:** implemented

Not from the planning document. [Issue #9](https://github.com/jantman/robot-army/issues/9),
which asks a question rather than describing a feature: spec-kit has an extensions mechanism,
more than half my work uses spec-kit, so should robot-army populate extensions to monitor
and/or drive the process.

**The answer is no, and that is most of the milestone.** A spec-kit hook is read and executed
by the *agent* as part of following its own command instructions. Nothing in spec-kit calls
out to anything; there is no daemon-side event; a hook can only name a command that exists in
that repository's integration. So a hook is a report the session chose to make, and an absent
report means either "not there yet" or "did not bother" with nothing to tell them apart. A
design whose failure mode is silence is the one this project has twice gone out of its way to
avoid.

The filesystem is not that. Spec Kit writes `spec.md`, `plan.md` and `tasks.md` at documented
paths, and reading them needs no cooperation, no injection, and no trust in the session.
Nearly the same question, and only one of the two mechanisms can be wrong about it. The three
conditions that would make hooks worth revisiting are written down in the spec's Out of Scope
section rather than left as an omission — a deferral with no stated trigger is how a decision
gets re-litigated from scratch a year later.

What ships instead is two layers. **Tell the session**: detect spec-kit from the worktree's
own contents and put the lifecycle in the prompt, which stops me writing the same
`.claude/robot-army.md` into every spec-kit repository — 005's lesson repeating verbatim.
**Watch the files**: derive which stage a running session reached, so `/active` stops showing
a session five minutes into specify and one three hours into implement as the same row.

Three decisions were taken during specification. The prompt states the convention for when
the lifecycle applies and **the session judges**, because a second label puts the decision at
the moment I am already labelling and a size heuristic is the daemon guessing at something
the session reading the issue knows better; the cost is that SC-001 is a rate measured over
the live round rather than an assertion, and the spec says so. Detection switches the
behaviour **on by itself**, with a global and per-repository kill switch, because
per-repository opt-in reintroduces exactly the step 005 spent a milestone removing — and user
story 3, the `repos` listing column, exists as the price of that rather than as a nicety. And
**nothing is written into a worktree at all**, which is a requirement (FR-018) with a test
that hashes the whole tree rather than a habit.

The load-bearing design problem was attribution. A fresh worktree of this repository contains
six finished features, each with a `tasks.md` full of ticked boxes, so a phase derived from
"which artifacts exist" reports `implement` the instant the worktree exists — confidently
wrong on every row. Modification times cannot separate them, because `git worktree add`
stamps every checked-out file with the creation time. `git status` answers correctly until
the session commits its spec. So the set of feature directories present at creation is
recorded on the item, and `/speckit-specify` always creating a new one makes "not here
before" mean "this session's feature".

Two measurements removed mechanisms that would otherwise have looked obvious.
`.specify/feature.json` is **gitignored** — machine-local state, absent from a fresh worktree
— so nothing can depend on it. And no `git` subprocess is invoked anywhere, because
`git status` can refresh `.git/index` and not having the argument is better than winning it
with `--no-optional-locks`.

### What running it taught

*To be filled in after the live round.* The number to watch is SC-001: how often a session in
a spec-kit repository actually starts with the lifecycle, given that FR-008 hands it the
judgement. CI cannot settle that one, by construction.

## 008 — Status tells one story (`specs/008-status-hidden-simulated/`)

**Status:** implemented

Not from the planning document.
[Issue #13](https://github.com/jantman/robot-army/issues/13), found during the 001–007 human
verification round with the daemon at `effect_level = "plan"`: `robot-army status` printed a
four-row queue and then "no work items yet" and "no matching work items" in the same output.

**Nothing underneath was broken, and that is the interesting part.** `ordering.plan` includes
simulated rows because simulated rows occupy capacity, so the queue has to name the item the
next dispatch would actually select. The counts and the listing exclude them because FR-056
made exclusion the default and `purge-simulated` exists so they do not accumulate as real
history. Both halves are right; neither was the thing to change. What was missing is that
nothing reconciled them at the point of rendering, so the command printed two statements that
could not both be true and the only recourse was to disbelieve the surface or read the source.

The fix is a number: how many rows this invocation matched and did not show, printed wherever
the command would otherwise claim absence or undercount. Two numbers, in fact — the counts
section has never honoured `--state` or `--repo` and the listing always has, so one figure
would be wrong in whichever section it did not belong to as soon as a filter was in play. It
comes from a dedicated `COUNT(*)` sharing its filter construction with the listing, because a
withheld count that is merely *close* replaces an obvious contradiction with a subtler one.

Two things the issue did not ask for. The disclosure fires whenever anything is withheld
rather than only when the listing came out empty, because two visible rows beneath a six-row
queue is the same defect one notch quieter. And the queue marks its simulated rows, which it
never did: FR-057 requires the marking wherever rows are shown, and the reader most likely to
be misled is the one who reads the first table, gets their answer, and stops.

`cards` and `worktree list` had the quiet half of the same defect — claiming nothing was
tracked or recorded while withholding rows — and say what they withheld too. The web
interface has the loud half and is [issue #14](https://github.com/jantman/robot-army/issues/14):
below `live` it renders as an empty system with a neutral pill, which is harder to notice than
a contradiction. This milestone puts the count into the payload the web already consumes and
stops there.

### What running it taught

*To be filled in after the live round.*

## 009 — Whatever survives contact with reality

**Status:** not specified

Planning doc M5. Parked items from §16 that are still genuinely open — kitty control
socket hardening, multi-machine dispatch, scheduled/proactive work — land here or get dropped.

Moved from the 008 slot, which 008 claimed for the same reason 007, 006 and 005 claimed
theirs: a milestone with a shape displaces a parking lot without one. Four times now.

---

## Decisions applied across the roadmap

**Storage is SQLite, not MariaDB.** The planning document §12 selects MariaDB. That conflicts with
constitution Principle II ("Core function MUST NOT require a hosted database") and with the
Operating Constraints storage rule ("Persistent data MUST use plain text, structured line formats,
or SQLite"). The conflict was raised before work began, per the Governance section, and resolved in
favour of the constitution: SQLite at a documented local path. This also removes the startup-ordering
dependency and the DB-outage failure mode that §12 itself flags, and keeps the daemon portable to a
laptop. §12's instruction to keep the persistence layer thin still applies, now in the other
direction.

**Repository CI is not the "no GitHub Actions" settled decision.** Planning §2 lists "Local
execution only. No GitHub Actions, no hosted runners, no cloud dispatch" as settled and not open
for reconsideration, and the pre-M0 question "GitHub Actions as a complement?" was closed with the
same answer. Both are about **where dispatched work runs** — whether the daemon could farm sessions
out to hosted runners instead of a real terminal on this machine. Neither is about testing this
repository's own code, and the settled decision stands untouched: no work item will ever be
dispatched to a runner.

A test-and-lint workflow (`.github/workflows/tests.yml`) is therefore in scope, and was added after
milestone 001. It is not a release pipeline — nothing is packaged or published, which Principle V
would forbid without a demonstrated need. It enforces a rule the constitution already states, that
implementation is not complete until the suite passes, mechanically rather than by discipline.

Its limit is worth stating alongside it, because a green check is easy to over-read: CI cannot run
anything needing a live session registry, a running kitty, or real credentials. One test skips there
for precisely that reason, and it is the one that caught the worst bug in milestone 001. CI raises
the floor. It does not replace the human verification round.
