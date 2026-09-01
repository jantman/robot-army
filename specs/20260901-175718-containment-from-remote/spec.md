# Feature Specification: Containment Proved From the Remote, Not From a Stale Ref

**Feature Branch**: `20260901-175718-containment-from-remote`

**Created**: 2026-09-01

**Status**: Draft

**Input**: User description: "issue #105 on this repo" — *verify the cleanup guards (004 scenario 10)
before enabling any automatic cleanup: the one remaining gap that destroys unrecoverable work*

## Context

Issue #105 exists to run one scenario by hand, because the thing it checks cannot be checked any
other way and because getting it wrong is unrecoverable: cleanup deletes branches, and a commit on
a branch that is deleted here is gone.

**The scenario has now been run.** A disposable clone with a real bare `origin`, its own `HOME` so
neither the real session registry nor the real worktree root was reachable, four `done` work items
with worktrees made by the product's own preparation path, and — for the live-session case — a
genuinely running process with a real `/proc` entry rather than a recorded process id. Driven
through the real command-line interface, not a fixture.

Three of the four guards hold, and the manual removal guard added for #79 refuses correctly:

| Setup | Required | Observed |
|---|---|---|
| an untracked file in the worktree | `retained` | `retained`; worktree and branch both intact, reason recorded |
| a commit on an unpushed branch | `branch_retained`, commits reachable | `branch_retained`; worktree reclaimed, branch kept, the commit still on the branch |
| a live session | `skipped` | `skipped`; cleaned once the session ended |
| a hand-deleted worktree directory | a `prunable_worktree` anomaly | see Out of Scope |

The fourth row and one further finding are dealt with below. **The finding this feature exists for
is the one in the dangerous row's neighbourhood**, and it is exactly the thing #105 said automated
coverage could not establish: whether the containment evidence authorising a `force` delete was
read from the remote we think it was.

It was not.

The containment check has two ways to prove a branch safe: every commit is contained in the
published base, or the branch itself is pushed and up to date under its own name. The first fetches
`<remote>/<base>` before asking, and is sound. **The second asks about `<remote>/<branch>` without
ever fetching it.** That name resolves to a local remote-tracking ref — a cached copy of what the
remote said the last time anything asked — and the fetch that does happen is scoped to the base
branch, so it neither refreshes nor prunes it. The check therefore answers a question about the
past and reports it as proof.

Reproduced end to end in the same disposable environment. The branch was pushed. The branch on the
remote then went away, the way a "delete branch" on a merged-or-closed pull request does it, done
against the remote so that the clone's tracking ref was left exactly as a real clone's would be.
Cleanup then reported *"the branch is pushed and up to date with `origin/<branch>`"* and deleted the
branch with `force`. After an ordinary garbage collection on the remote the commit existed nowhere
but the stale tracking ref that had been mistaken for proof — and the next full `git fetch --prune`
removes that too. A force-push that rewinds the remote branch reaches the same place by the same
route: the tracking ref still names the old tip, so the local commits read as already published.

This is silent, permanent, and looks like a successful cleanup while it happens — which is the
description #105 gives of the failure mode it was opened to find.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A branch is only "pushed" if the remote says so now (Priority: P1)

Cleanup considers a finished item whose branch was pushed at some point. Before treating "the
branch is on the remote under its own name" as proof, it asks the remote — at the time of the
check — whether that branch is still there and what it points at. If the remote no longer has the
branch, or has it at a different commit, or cannot be asked, the branch is kept.

**Why this priority**: This is the entire report. Without it, one routine configuration change
turns an ordinary remote-side branch deletion into permanent loss of work, with a log line claiming
the opposite. Everything else in this feature describes or defends this behaviour.

**Independent Test**: Push a branch, remove it from the remote without touching the clone, and run
cleanup. The story passes when the branch survives, the recorded reason says containment is
unproven rather than proved, and the commit is still reachable from the branch in the clone.

**Acceptance Scenarios**:

1. **Given** a finished item whose branch was pushed and is still on the remote at the same commit,
   **When** cleanup runs, **Then** the branch is deleted and the recorded reason cites the branch
   being pushed and up to date — the behaviour that exists today, unchanged.
2. **Given** a finished item whose branch was pushed and has since been deleted on the remote,
   **When** cleanup runs, **Then** the branch is kept, the item is `branch_retained`, and the
   commits on it are still reachable in the clone.
3. **Given** a finished item whose branch was pushed and the remote branch has since been rewound
   by a force-push, **When** cleanup runs, **Then** the branch is kept, because commits exist on it
   that are not on the remote.
4. **Given** a finished item whose branch was never pushed at all but whose commits are contained in
   the published base, **When** cleanup runs, **Then** the branch is deleted on the base-containment
   evidence, which is unaffected by this feature.
5. **Given** a finished item whose branch was never pushed and is not contained in the base,
   **When** cleanup runs, **Then** the branch is kept — the behaviour verified as already correct.
6. **Given** a clone holding a stale remote-tracking ref for a branch the remote no longer has,
   **When** cleanup evaluates that branch, **Then** the stale ref is not accepted as evidence of
   anything.

---

### User Story 2 - A remote that cannot be asked keeps the branch (Priority: P2)

The remote is unreachable, the fetch times out, or the answer cannot be parsed. Cleanup keeps the
branch and says why, exactly as it already does when the base-branch fetch fails.

**Why this priority**: The failure direction is the whole design of this module — every unresolved
doubt keeps what it was unsure about. A change that adds a new question to ask must not add a new
way for "could not ask" to read as "yes". P2 rather than P1 because the harm is prevented by Story
1 only if this holds too; it is separated because it is a distinct testable behaviour, not because
it matters less.

**Independent Test**: Point the clone's remote at something that does not exist and run cleanup on
an item whose branch was pushed. The story passes when the branch is kept and the reason names the
failure.

**Acceptance Scenarios**:

1. **Given** a remote that has gone away, **When** cleanup evaluates a pushed branch, **Then** the
   branch is kept and the recorded reason says containment could not be established.
2. **Given** a remote that answers about the base branch but not about the item's branch, **When**
   cleanup evaluates that branch, **Then** the pushed-under-its-own-name test does not pass, and
   the decision falls back to the base-containment evidence alone.
3. **Given** a repository with no configured remote at all, **When** cleanup evaluates a branch,
   **Then** the branch is kept, because nothing can be proved published without a remote.

---

### User Story 3 - The evidence in the record names what was actually checked (Priority: P3)

A reader of the durable action record, months later, can tell which evidence authorised a `force`
delete and that the evidence was current at the time — not merely which ref name was consulted.

**Why this priority**: Required by the accountability principle, and practically necessary here:
the reason string is the only thing that distinguishes a sound deletion from the unsound one this
feature removes, and the two read identically today. P3 because the harm is prevented by Stories 1
and 2.

**Acceptance Scenarios**:

1. **Given** a branch deleted on pushed-and-up-to-date evidence, **When** the action record is read,
   **Then** it shows that the remote was asked about that branch as part of the check.
2. **Given** a branch kept because the remote no longer has it, **When** the item is inspected,
   **Then** the retention reason distinguishes "the remote does not have this branch" from "the
   remote has it and there are commits ahead of it".
3. **Given** either outcome, **When** the record is read alone, **Then** no re-running is needed to
   tell which of the two containment tests decided it.

---

### User Story 4 - The verification itself survives without being re-run (Priority: P3)

The scenario-10 run is written down: the environment it was run in, what each of the four guards
did, the two findings, and the commands that produced them. The next person asking "have the
cleanup guards actually been checked against a real repository?" gets an answer from the repository
rather than by staging four hazards again.

**Why this priority**: #105 exists because this answer was unavailable. A fix that closes the issue
without recording the verification leaves the same question open for the next milestone. P3 because
it defends the result rather than producing it.

**Acceptance Scenarios**:

1. **Given** the committed record, **When** it is read, **Then** it states what was staged, what
   each guard decided, and which findings came out — including the one deliberately left unfixed.
2. **Given** the committed record, **When** the reader wants to repeat it, **Then** the setup is
   described in enough detail to rebuild it.

---

### Edge Cases

- **The remote branch exists but is ahead of the local one.** Someone else pushed to it. Nothing on
  the local branch is unpublished, so it is safe to delete; the existing "commits ahead" question
  already answers this correctly once the ref is current.
- **The remote branch was deleted and a different branch of the same name later created.** The
  current answer is whatever the remote says now, which is the right question — if the local
  commits are not on it, they are not published, whatever the name once meant.
- **The remote has the branch, and the base fetch failed.** Containment must not be provable by the
  pushed test while the module's existing rule says a failed fetch keeps the branch; a failure to
  establish the state of the remote at all keeps the branch regardless of which question was being
  asked.
- **Refreshing the branch's tracking ref changes what other code sees.** Cleanup runs against the
  author's own clone. Updating a remote-tracking ref for a branch cleanup is about to consider must
  not disturb anything else in that clone — no local branch, no working tree, no checked-out ref.
- **The item's branch name contains characters that are meaningful to a refspec.** Branch names are
  generated by the product from an issue number and a slug, so this is not reachable today, but the
  question asked of the remote must not be constructible into something other than a single branch
  lookup.
- **A dry-run item.** Simulated cleanup must ask no more of the network than it does today.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The pushed-under-its-own-name containment test MUST be evaluated against information
  obtained from the remote during the check, not against a remote-tracking ref left over from an
  earlier operation.
- **FR-002**: When the remote no longer has the branch, the pushed test MUST NOT pass, regardless of
  what any local ref says.
- **FR-003**: When the remote has the branch at a commit that does not contain every commit on the
  local branch, the pushed test MUST NOT pass.
- **FR-004**: Any failure to establish what the remote holds for that branch — an unreachable
  remote, a timeout, an unparseable answer — MUST leave the pushed test unproven, and unproven MUST
  keep the branch.
- **FR-005**: The base-containment test MUST continue to behave exactly as it does today, including
  its existing rule that a failed fetch of the base keeps the branch.
- **FR-006**: A branch MUST NOT be deleted unless one of the two containment tests passed on current
  evidence; `force` on the delete MUST continue to mean that a stronger guard than git's own has
  passed.
- **FR-007**: The retention reason recorded for a branch kept by this rule MUST distinguish "the
  remote does not have this branch" from "the remote has it and commits exist ahead of it".
- **FR-008**: Every request made of the remote MUST be written to the durable action record when it
  occurs, as the existing base fetch already is.
- **FR-009**: Consulting the remote about the item's branch MUST NOT modify the clone's local
  branches, working tree, checked-out ref, or any remote-tracking ref outside the one that names
  that branch.
- **FR-010**: The other three cleanup guards — git's refusal on a dirty worktree, the live-session
  `skipped` outcome, and the reclaiming of an already-missing directory — MUST be unchanged.
- **FR-011**: The scenario-10 verification run MUST be recorded in the repository, naming the
  environment, each guard's observed outcome, and every finding including those not fixed here.
- **FR-012**: The stale-ref case MUST be covered by an automated test against a real repository and
  a real remote, so that a future change cannot silently reintroduce it.

### Key Entities

- **Containment evidence**: the statement that authorises deleting a branch, and the reason string
  recorded alongside the decision. Its two forms are "contained in the published base" and "pushed
  and up to date under its own name". This feature changes only how the second is established.
- **Remote-tracking ref**: a local cache of a remote branch's position. Currently treated as the
  remote's answer; after this feature, only as current when the check itself refreshed it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A branch whose commits are not on the remote is never deleted, whatever local refs
  survive from an earlier push — zero occurrences across the staged cases.
- **SC-002**: Scenario 10 run end to end against a real repository produces zero removals that
  should have been kept, and the unpushed branch's commits remain reachable afterwards.
- **SC-003**: The stale-remote-tracking-ref case fails the test suite if the fix is reverted.
- **SC-004**: For any branch cleanup deleted or kept, the record alone says which containment test
  decided it and on what evidence, without re-running anything.
- **SC-005**: The repository answers "have the cleanup guards been verified against a real
  repository, and what was found?" without staging the scenario again.

## Assumptions

- The remote is reachable at cleanup time in the normal case. When it is not, keeping the branch is
  the correct and already-established outcome, and the additional question asked here does not
  change how often cleanup runs.
- `origin` remains the conventional remote name, and the existing default-remote resolution is
  unchanged by this feature.
- Branch names remain those the product generates. No support for arbitrary operator-supplied branch
  names is added.
- The verification environment described in the record is disposable and is not committed; the
  record describes it rather than shipping it.

## Out of Scope

- **The `prunable_worktree` finding.** Scenario 10 requires a hand-deleted worktree directory to
  surface as a `prunable_worktree` anomaly. It does not, and cannot: the sweep that raises that
  anomaly excludes `done` and `abandoned` items, and scenario 10's item is `done` by construction
  because its issue is closed. With `[cleanup] on_issue_close` at its shipped default of false —
  the currently recommended setting — the directory is gone, git marks the worktree prunable, and
  `robot-army anomalies` reports nothing at all. This is a visibility gap, not a data-loss one, and
  the obvious fix is not obvious: including terminal items in that sweep would raise an anomaly for
  every worktree cleanup legitimately removed, so it needs the manual removal path to record what
  it did first. It is recorded in the verification document and left for its own issue.
- **Turning on `[cleanup] on_issue_close`.** That decision belongs to the operator after this fix
  lands, not to this feature.
- **Any change to the automatic pass's scheduling, the effect-level rules, or the audit format.**
