# Feature Specification: A merged pull request retires the session

**Feature Branch**: `robot-army/issue-149-a-merged-pr-should-retire-the-session`

**Created**: 2026-09-06

**Status**: Draft

**Input**: GitHub issue #149, filed against the retirement feature that shipped for #138 (PR
#140), after watching that feature run end to end on the ordinary successful path for the
first time.

Source: [#149 — A merged PR should retire the session
immediately](https://github.com/jantman/robot-army/issues/149)

## What is actually happening

Retirement fixed the *permanent* version of #138 and left a *bounded* one behind. On the
ordinary successful path — worker finishes, maintainer merges, issue closes, item goes `done`
— merging still produces an `orphan_session` anomaly, a held capacity slot, an open terminal
tab, and a worktree reported `skipped`, all of which persist for up to 30 minutes.

Measured on issue #20 / PR #147, the first ordinary completion to run the shipped code:

| Time (UTC) | Event |
|---|---|
| 11:03:42 | session confirmed, item 49 → `active` |
| 11:40:49 | worker goes idle — it has finished and is sitting at the prompt |
| 11:41:35 | the item's pull request is recorded as `merged` |
| 11:41:36 | issue observed closed → item 49 → `done` |
| 11:41:36 | **`orphan_session` raised, the same second** |
| ~12:10:49 | retirement finally fires, 1800s after the worker went idle |

**The cause is that the quiet period and the merge are on different clocks, and the merge is
always first.** Retirement waits for the worker to have been quiet for 30 minutes. On the real
path the worker goes quiet, the maintainer merges within a few minutes, the issue closes, and
the item reaches `done` — so `done` reliably arrives *inside* the quiet period, never after it.
Retirement declines, correctly by its own rules, and the sweep that runs next in the same pass
finds a still-open row under a `done` item and reports the orphan it was built to report.

**Every part is still behaving as specified.** What is wrong is the specification: retirement's
first acceptance scenario begins "Given an item in `done` … whose worker has been quiet for
longer than the quiet period", and that precondition assumed away the case that actually
happens. Of the three completions since retirement shipped, the one that produced no anomaly
was the one whose worker had already been idle 2477 seconds when its item went `done` —
backlog, not a normal completion. Every `session.retire` in the audit log so far fired at idle
2477s, 11205s, 18049s and 35052s. **The gate has never once been crossed by an item completing
normally.**

**The signal that should have been used is already stored, already fresh, and already parsed.**
The pass refreshes each item's pull requests *before* it resolves closed issues, deliberately,
so by the time retirement runs the item's pull request set says `merged` and was written by
this same pass. Nothing new has to be fetched, and no new column, transition or API call is
required.

**A merged pull request is a stronger statement than any idleness timer.** Merging is the
maintainer saying "yes, this is complete" in as many words. Inferring the same thing from how
long a process has been quiet is a weaker claim reached later. The same principle already
governs tabs (#81); it should govern the retirement the tab close is now downstream of.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Merging finishes it, now rather than in half an hour (Priority: P1)

The maintainer merges a robot-army pull request. The issue closes. Within the next
reconciliation pass the item goes `done`, its worker is stopped, its session row closes, its
slot comes back, its tab closes, and its worktree becomes eligible for cleanup — and no anomaly
is ever raised for any of it, not on that pass and not on a later one.

**Why this priority**: It is the reported defect, and it is on the path every successful item
takes. Its cost is not merely cosmetic: for ~30 minutes per completed item the global and
per-repository capacity slots are held, so at the shipped cap of three, finished work throttles
dispatch — and the anomaly list, which is read as "things needing attention", fills with
entries that describe the system working correctly. That is precisely what #138 was filed
about.

**Independent Test**: Take an item to `done` with a merged pull request recorded while its
worker has been idle for well under the quiet period, run one reconciliation pass, and confirm
the process is gone, the row is closed, the slot is free, the tab is closed and
`robot-army anomalies` is empty. Delivers the whole of the issue on its own.

**Acceptance Scenarios**:

1. **Given** an item in `done` with at least one merged pull request, whose worker is idle and
   has been idle for only a few seconds, **When** a reconciliation pass runs, **Then** the
   worker is ended, its session row is closed with an end time and a reason naming retirement,
   and the slot it held is released from both the global and the per-repository count.
2. **Given** that same item and pass, **When** the pass completes, **Then** no `orphan_session`
   anomaly exists for that session — not raised and then resolved, but never raised, on that
   pass or any later one.
3. **Given** that same item and pass, **When** the pass completes, **Then** the terminal tab
   marked with that item is closed in the same pass, on the existing tab rule and with no
   change to it.
4. **Given** that same item with `cleanup.on_issue_close` enabled, **When** the next cleanup
   consideration runs, **Then** the worktree is no longer reported `skipped` for a live session
   and is reclaimed under the two existing guards, unchanged.
5. **Given** an item in `done` with a merged pull request whose worker is **not** idle — it is
   mid-tool-call, or its status cannot be established at all, **When** a pass runs, **Then**
   nothing is ended, nothing is recorded as a decision, and the question is asked again next
   pass. The merged pull request removes the duration requirement, never the idleness one.

---

### User Story 2 - An issue closed by hand keeps its half-hour of grace (Priority: P2)

The maintainer closes an issue directly — as not-planned, as a duplicate, or because they did
the work themselves. There is no merged pull request, so there is no explicit statement that
the session's work was accepted, and the session may be exactly what the maintainer is about to
attach to and read.

**Why this priority**: It is the guard that keeps User Story 1 from being an over-reach rather
than a fix. It is P2 because it is preservation of existing behaviour, not new value: getting
it wrong would end sessions the maintainer is still using.

**Independent Test**: Take an item to `done` with no merged pull request while its worker is
freshly idle, run passes, and confirm nothing is retired until the existing quiet period has
elapsed.

**Acceptance Scenarios**:

1. **Given** an item in `done` whose pull request set is empty, **When** a pass runs and the
   worker has been idle for less than the quiet period, **Then** nothing is retired and nothing
   is recorded.
2. **Given** an item in `done` whose only pull requests are `open` or `closed`-unmerged,
   **When** a pass runs and the worker has been idle for less than the quiet period, **Then**
   nothing is retired.
3. **Given** an item in `done` whose pull requests were **never looked up**, **When** a pass
   runs, **Then** it is treated as having no merged pull request. "Never asked" is not "not
   merged" and is certainly not "merged"; both unknowns delay a retirement rather than cause
   one.
4. **Given** any of the three items above, **When** the worker has been idle for longer than
   the quiet period, **Then** it is retired exactly as it is today, on the unchanged rule.

---

### Edge Cases

- **The item has several pull requests and only one of them is merged.** A merged pull request
  anywhere in the item's set is the signal. A retried item can easily carry a closed-unmerged
  attempt alongside the merged one, and the merged one is still the maintainer's acceptance.
- **The merge is recorded but the issue is not closed yet.** Nothing changes: the precondition
  is still `done`, which only the closed-issue pass writes. A merged pull request accelerates
  retirement; it does not authorise it on its own.
- **The pull request set is stale** — this pass's refresh failed and the column holds an answer
  from an earlier pass. Harmless in the direction that matters: a merged pull request never
  becomes unmerged, so a stale `merged` is still true. A stale `open` merely falls back to the
  quiet period.
- **The stored pull request set cannot be parsed, or holds something that is not a list of
  records.** It is read as no pull requests, and the item takes the quiet-period path.
- **The maintainer merges from the web interface while still reading the session.** The session
  is ended promptly and this is deliberate: the transcript survives untouched and
  `claude --resume` brings it back, so the cost is a keystroke. Waiting instead would reinstate
  the anomaly on the ordinary path, which is the whole defect. No floor is applied — with the
  measured timeline the worker had been idle 47 seconds when its item went `done`, so any floor
  above zero reproduces the reported failure on the one pass that matters.
- **The worker ends itself between the decision and the signal.** Unchanged: retirement settles
  on whatever the row says and does not report a failure for a session that ended cleanly a
  moment earlier.
- **The process survives the attempt.** Unchanged: the row stays open, the slot stays held, and
  the condition is still reported. Faster authorisation does not weaken any confirmation.
- **A simulated session, or a session with no recorded process.** Unchanged: neither is
  signalled, and neither is routed through a real termination.
- **An item in `abandoned` or `failed` that has a merged pull request.** Left alone. The
  precondition is `done`, and this feature does not widen it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST retire the worker of an item in `done` that has at least one
  merged pull request as soon as that worker is observed idle, without requiring any minimum
  idle duration.
- **FR-002**: The system MUST keep the existing idleness requirement on that path: a worker
  whose status is not idle, or whose idleness cannot be established, MUST NOT be retired. Every
  unknown MUST continue to delay a retirement rather than cause one.
- **FR-003**: The system MUST continue to require the existing quiet period for an item in
  `done` that has no merged pull request, with that period's value and meaning unchanged.
- **FR-004**: The system MUST treat "has a merged pull request" as a property of the item's
  already-stored pull request set, and MUST NOT make a network call to establish it.
- **FR-005**: An item whose pull requests were never looked up, whose stored set is empty, or
  whose stored set cannot be read MUST be treated as having no merged pull request.
- **FR-006**: Every other precondition on retirement MUST be unchanged: the item is in `done`,
  the session row is open, a process was recorded, that process is alive, and its identity
  matches the row before anything is signalled.
- **FR-007**: The system MUST NOT retire a worker under an item in any state other than `done`,
  whatever its pull requests say.
- **FR-008**: A retirement authorised by a merged pull request MUST settle its row, release its
  slot, close its tab, and free its worktree for cleanup by exactly the same mechanisms as one
  authorised by the quiet period. This feature changes when retirement is authorised and
  nothing about what retirement does.
- **FR-009**: The audit record written before the signal MUST say which condition authorised
  the retirement — a merged pull request or the elapsed quiet period — so that the log alone
  distinguishes "the maintainer accepted this" from "this had been quiet long enough".
- **FR-010**: A decision not to retire MUST continue to write nothing, on either path. That
  silence is the existing documented Principle III exception and its justification is unchanged.
- **FR-011**: On the ordinary successful path no `orphan_session` anomaly may be raised at any
  point. The requirement is that it is never reached, not that it is raised and then resolved.
- **FR-012**: The reasoning recorded in the code and in the guide MUST be corrected where this
  change falsifies it: the claim that retire-before-sweep makes "no anomaly on the successful
  path" free, and the argument that erring long on the quiet period is nearly free.

### Key Entities

- **Work item**: unchanged in shape. Its existing pull request set gains a second reader.
- **Pull request set**: the item's already-stored list of `{number, url, state}`, where `state`
  is one of `open`, `merged`, `closed`. Already normalised at the boundary and already parsed;
  read here, not extended.
- **Session row**: unchanged. Retirement closes it exactly as it does today.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An item taken from dispatch through to a merged pull request and a closed issue
  raises zero anomalies at any point in its life, with no action by the maintainer beyond
  merging.
- **SC-002**: The time between the pass that moves such an item to `done` and its session being
  retired is zero passes — both happen in the same reconciliation pass. Today it is up to 30
  passes.
- **SC-003**: The capacity slot of a completed item is released in the same pass the item
  reaches `done`, so finished work never throttles dispatch.
- **SC-004**: The tab of a completed item closes in the same pass the item reaches `done`.
- **SC-005**: A session whose item reached `done` without a merged pull request survives every
  pass until the existing quiet period has elapsed, unchanged from today.
- **SC-006**: A session whose worker is not idle is never retired, for any item, however the
  retirement was authorised.
- **SC-007**: The audit log alone answers, for every retirement, which of the two conditions
  authorised it.

## Assumptions

- **A merged pull request means the work was accepted.** The maintainer merges by hand; there
  is no automation that merges on their behalf. This is the premise the whole feature rests on
  and it is the issue author's own statement of intent.
- **No floor on the merged path.** Resolved deliberately rather than by default: the issue
  raised a 60-second floor as an option, and the measured timeline rules it out — the worker had
  been idle 47 seconds when the item went `done`, so any non-zero floor reproduces the anomaly
  on the pass that matters.
- **The idleness requirement stays.** Also resolved deliberately: it is what keeps a worker from
  being ended mid-tool-call, and it is the property that makes every unknown delay a retirement
  rather than cause one. What this feature removes is the *duration* requirement, on one path.
- **The stored pull request set is fresh enough.** The refresh runs before the closed-issue pass
  in the same reconciliation pass, deliberately and for this exact reason, so retirement reads
  an answer written moments earlier by the same pass.
- **No new configuration key.** Consistent with retirement itself, which has none: this is what
  the system does with a session whose work has been accepted, and retirement still destroys
  nothing — the transcript survives and the session stays resumable.
- **No new database column, migration, transition or API call.** Everything this feature reads
  already exists and is already written.
- **The tab and the worktree follow for free.** Both are gated on the session row being closed,
  so retiring earlier moves both earlier with no change to either rule. This is why the issue
  says fixing this fixes most of #81 as a consequence.
- **Detecting the moment *before* the merge is out of scope.** That is #146, and it is a
  different question with a different signal. This feature is about the moment after the merge,
  where the signal is unambiguous and already stored.
