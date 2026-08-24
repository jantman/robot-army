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

**Status:** specified

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

**Status:** not yet specified

Planning doc M2. Active-sessions, queue, and interrupted views; resume / abandon / restart / attach
controls with the resume-decision signals from §8; the audit log with clickable issue, card, and PR
links; pause-dispatch and force-poll controls. Reachable from the author's phone, which is the
ergonomic point of it.

Depends on 001 for the state model, the audit record, and the session-control operations — the UI
is a second front-end onto commands 001 already exposes, not a new set of capabilities.

## 003 — Trello Source (`specs/003-trello-source/`)

**Status:** not yet specified

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

**Status:** not yet specified

Planning doc M4. The full concurrency model — global and per-repo caps, counting the author's own
out-of-band sessions against the global cap, queue position — plus per-repo configuration
overrides, priority and ordering modes, worktree cleanup policy, and event notifications.

Depends on 001's session registry scan, which already does most of the observation work this
milestone needs; 004 is largely policy layered on top of it.

## 005 — Whatever survives contact with reality

Planning doc M5. Not planned. Parked items from §16 that are still genuinely open — kitty control
socket hardening, multi-machine dispatch, scheduled/proactive work — land here or get dropped.

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
