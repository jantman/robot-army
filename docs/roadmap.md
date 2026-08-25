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

## 005 — Whatever survives contact with reality

**Status:** not specified

Planning doc M5. Parked items from §16 that are still genuinely open — kitty control
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
