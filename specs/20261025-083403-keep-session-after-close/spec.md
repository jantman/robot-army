# Feature Specification: Keep a session open after its issue closes

**Feature Branch**: `speckit/20261025-083403-keep-session-after-close`

**Created**: 2026-10-25

**Status**: Draft

## Clarifications

### Session 2026-10-25

- Q: How is a keep placed? → A: A new per-item keep (`keep <id>` / `unkeep <id>`, plus a web control). Not an extension of hold, and not a per-repo config key.
- Q: What ends a keep? → A: Only explicit release, or the operator exiting the session. No timeout.
- Q: Do pause or repository holds also suppress retirement? → A: No. Only a keep does.
- Q: How is a retirement after a released keep made distinguishable (FR-008)? → A: From the audit log: `unkeep.item` records `open_sessions`, joined to the later `session.retire` on session id. No field on `session.retire`, no soft-deleted keep row.

**Input**: User description: "Leave an existing Claude session open after the issue is closed.
Sometimes — especially with privatepuppet — I need to merge the PR and then do additional
testing and verification afterwards. Right now merging the PR closes the issue, which then
terminates and cleans up the running Claude session even if the repo is held or dispatching is
paused."

## Context

Since issues #138 and #149 the successful path has an ending: merging the pull request closes
the issue, the next reconciliation pass moves the item to `done`, and on that same pass an idle
worker under a `done` item with a merged pull request is retired — the process is ended, the
session row closes, the slot comes back, the kitty tab is closed, and (with
`[cleanup] on_issue_close = true`) the worktree and branch are reclaimed once no session is open.
There is no floor on that path, deliberately: a 60-second one would have declined on exactly the
pass #149 was about.

That is right for most work and wrong for one shape of it. In a configuration-management
repository the merge is not the end of the work, it is the *start* of the part that matters:
the change only reaches a machine once it is on the default branch, so applying it, watching
the result, and fixing what that turns up all happen after the merge. The session that wrote the
change is the best place to do that — it has the reasoning, the context, the checkout — and
today it is gone within a minute of the merge.

Neither existing control helps, and both are documented as not helping. A hold "never stops a
running session" and governs entry into dispatch only; a pause stops the queue and leaves
everything in flight alone. Neither has ever been consulted by retirement, tab closing or
cleanup. Retirement's own defence — "the transcript survives and `claude --resume` brings it
back, so the cost is a keystroke" — undercounts this case: the tab is gone, and with cleanup on,
the checkout the resumed session would work in can be gone too.

What is missing is a way to say, about one piece of work, "the merge is not the end of this
session".

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Merge, then verify in the same session (Priority: P1)

I have a privatepuppet item whose session has opened a pull request. Before merging, I mark the
item's session to be kept. I merge. The issue closes and the item reaches `done` as usual, but
the session keeps running in its tab, in its checkout, so I can apply the change on the target
host and ask the same session to look at the result and fix what it finds. When I am finished I
release it, and it is retired by the ordinary rules on the next pass.

**Why this priority**: This is the whole feature; without it the request is unmet.

**Independent Test**: Mark an item's session kept, close its issue with a merged pull request,
run reconciliation passes, and observe that the worker, its session row, its tab and its
worktree all survive; release the keep and observe retirement on the next pass.

**Acceptance Scenarios**:

1. **Given** a running, idle session whose item is marked kept, **When** its pull request is
   merged and the issue closes, **Then** the item reaches `done`, and the worker is not retired,
   its tab is not closed, and its worktree is not reclaimed, on that pass or any later one while
   the keep stands.
2. **Given** a kept session under a `done` item, **When** I release the keep, **Then** the next
   pass applies the ordinary retirement rules (merged pull request → retired as soon as it is
   observed idle; no merge → the quiet period), followed by the ordinary tab and cleanup rules.
3. **Given** a kept session under a `done` item, **When** I exit the session myself, **Then** the
   session ends as any session does, and the tab and cleanup rules proceed as they would for a
   retired one.
4. **Given** an item that is **not** marked kept, **When** its pull request is merged, **Then**
   behaviour is exactly today's: retirement on the pass it is observed idle.

---

### User Story 2 - See what is being kept, and why the machine is full (Priority: P2)

A kept session holds a real process and therefore a real capacity slot. I can see every kept
session — item, repository, how long it has been kept, how long since its item finished — from
the CLI and the web interface, with a control to release each one. A kept session under a `done`
item is not reported as an `orphan_session` anomaly, because it is not one: it is open because I
said so.

**Why this priority**: Without it a forgotten keep silently costs a slot indefinitely, and the
anomaly list cries wolf about sessions I deliberately kept.

**Independent Test**: Keep a session, finish its item, and check that it appears in the kept
listing, that capacity reports it as occupying a slot with a reason naming the keep, and that no
`orphan_session` is raised for it.

**Acceptance Scenarios**:

1. **Given** a kept session under a `done` item, **When** I look at status, the web interface or
   the capacity explanation, **Then** the session is shown as kept, with its age, and a
   capacity-full reason names kept sessions when they are part of the count.
2. **Given** a kept session under a `done` item, **When** reconciliation runs, **Then** no
   `orphan_session` anomaly is raised for it; **and when** the keep is released and the worker
   then cannot be retired, the anomaly is raised exactly as it is today.

---

### User Story 3 - Decide after the fact, as long as the session is still there (Priority: P3)

I sometimes realise only after merging that I want to verify in the session. If the session has
not yet been retired, placing a keep then still protects it. If it has already been retired, the
keep command says so plainly and points at resuming the transcript, rather than claiming to have
kept something.

**Why this priority**: Merged-path retirement happens on the very next pass, so the window after
merge is short; this story only governs honesty about that window, not a new capability.

**Independent Test**: Place a keep on a `done` item whose worker is still running and observe it
survive; place one on a `done` item whose session has already ended and observe the refusal text.

**Acceptance Scenarios**:

1. **Given** a `done` item whose worker has not yet been retired, **When** I place a keep, **Then**
   it is honoured from the next pass.
2. **Given** an item with no open session, **When** I place a keep, **Then** the command refuses,
   says there is no session to keep, and names the transcript-resume route.

### Edge Cases

- **Keep placed before dispatch.** A keep placed on an item that has not yet started a session
  applies to the session it later starts. [Assumption; see Assumptions.]
- **Item ends `failed` or `abandoned`.** Those already keep their workers and tabs. A keep on
  `failed` is accepted (a retry can still reach `done`); a keep on `abandoned` is refused,
  because it can never reach `done` and the keep could never take effect (decided 2026-10-25).
- **Item retried or resumed after a keep.** The keep belongs to the item, so it covers the item's
  current session, not just the one open when it was placed.
- **Daemon restart or reboot.** A keep is durable runtime state, like a hold: it survives both.
  After a reboot the worker process is gone anyway; the keep then has nothing to protect and the
  ordinary stale-session sweep applies.
- **Worker busy when released.** Release does not end anything itself; it returns the item to the
  ordinary rules, whose idleness check is unchanged — a busy worker is left alone.
- **Manual `cancel`.** `cancel` still ends a kept session; the keep restricts the automatic path,
  never an explicit command.
- **Manual `cleanup <id>` or `worktree remove <id>`.** Unchanged: their existing session guard
  already refuses while the kept session's row is open.
- **Capacity full of kept sessions.** Dispatch waits, and the reason says kept sessions are
  occupying slots, so the fix (release one) is named.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The operator MUST be able to mark work as "keep its session after the issue closes"
  from the CLI and from the web interface. A keep is its own per-item statement, placed with a
  `keep <id>` command or the web control and released with `unkeep <id>` or the web control. It
  is not an extension of hold and not a configuration key: a hold keeps meaning "governs entry
  into dispatch", and no repository pins every session by default.
- **FR-002**: While a keep stands, reconciliation MUST NOT retire the session's worker, close its
  tab, or reclaim its worktree and branch automatically, whatever the item's state.
- **FR-003**: A keep MUST end only by explicit release (CLI or web), never by time. Exiting the
  session ends the session, and the keep has nothing left to protect. When a keep is released,
  the ordinary retirement, tab and cleanup rules apply from the next pass, unchanged.
- **FR-004**: A keep MUST NOT change how the item reaches `done`, what is posted on the issue,
  notifications, or Trello card handling; it governs the session's ending only.
- **FR-005**: A kept session MUST continue to count against capacity, and any "machine full"
  explanation MUST name kept sessions when they contribute to the count.
- **FR-006**: A kept session under a `done` item MUST NOT be reported as an `orphan_session`
  anomaly; every other condition that raises that anomaly MUST be unaffected.
- **FR-007**: The operator MUST be able to list every standing keep with its item, repository,
  age, and the item's state, and release any one of them, from the CLI (`keeps`) and the web
  interface.
- **FR-008**: Placing and releasing a keep MUST each write an audit record; a retirement that is
  withheld because of a keep is not written on every pass (the existing "not yet" silence), but
  a retirement that follows a release MUST be distinguishable from the audit log from one that
  did not: `unkeep.item` names the session rows open at release, and the later `session.retire`
  for one of those session ids follows it.
- **FR-009**: Keeps MUST be durable across daemon restart and reboot.
- **FR-010**: Placing a keep on an item with no open session and no possibility of a future one
  (a terminal item whose session has ended) MUST be refused with a message naming the
  transcript-resume route.
- **FR-011**: Explicit `cancel`, `cleanup <id>` and `worktree remove <id>` MUST behave exactly as
  today for a kept item.
- **FR-012**: Pause, item holds and repository holds MUST continue to have no effect on
  retirement, tab closing or cleanup. Only a keep does.

### Key Entities

- **Keep**: an operator statement that a piece of work's session outlives its issue. Attributes:
  what it applies to, when it was placed, how it was placed (CLI or web), and when and how it
  ended. Durable runtime state, not configuration; it never expires.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a kept item, merging its pull request leaves the session usable in the same tab
  and checkout for as long as the keep stands — zero retirements, tab closures or worktree
  removals across any number of passes.
- **SC-002**: Releasing a keep on a merged, idle session results in retirement on the first
  following pass, with no extra wait.
- **SC-003**: Items without a keep show no change in behaviour: every existing retirement, tab and
  cleanup test passes unmodified.
- **SC-004**: Zero `orphan_session` anomalies are raised for kept sessions, and every standing
  keep is visible from both the CLI and the web interface.

## Assumptions

- The verification work happens in the existing session interactively; robot-army does not need
  to know when verification is complete beyond the operator releasing the keep or exiting.
- The keep is placed before the merge in the normal case. Merged-path retirement runs on the very
  next pass and that is not changed here; a keep placed after retirement cannot bring a session
  back (`claude --resume` remains the route).
- A keep placed before dispatch is accepted and applies once a session exists, because the
  operator often knows at filing time that a change will need post-merge verification.
- No change to the composed prompt: the worker is not told it may be kept.
- Documentation updates go to `5-outcome.md` (the session's ending, the tab, cleanup),
  `3-selection.md` (capacity reasons), `operating.md` (web interface, anomalies),
  `audit-log.md` and `state.md`. No configuration key is added, so `configuration.md` and the
  example config are untouched.
