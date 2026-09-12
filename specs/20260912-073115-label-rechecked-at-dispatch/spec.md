# Feature Specification: The Label Is Re-checked Before Dispatch, Not Only at Discovery

**Feature Branch**: `robot-army/issue-62-changing-github-label-does-not-de-scope`

**Created**: 2026-09-12

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#62 — "Changing [github] label does not de-scope
already-discovered items: they keep dispatching against issues that no longer carry it"
(labels: bug, robot-army). Found during the issue #1 verification round on 2026-08-30.

**Baseline**: written against `main` at 9e0296a. Verified in that tree rather than assumed:

- `poll.evaluate` (`src/robot_army/poll.py:82`) refuses an issue that does not carry
  `[github] label`. It is called at discovery, while a row sits in `discovered`, and by
  `retry` against a live re-read (`operations.py:3644`). Nothing calls it for a `ready` row.
- `ordering.plan` is the only producer of dispatch order; `select_and_dispatch`, `status` and
  the web queue render the same list, and each entry carries at most one `HoldReason`.
- A row's `labels` column is written once, at insert (`poll.py:221`). A later poll that finds
  the issue again skips the row unless it is still `discovered` (`poll.py:190`), so the stored
  labels are a snapshot from discovery. Only `retry` rewrites them (`operations.py:3634`).
- `insert_work_item` has exactly one caller, the GitHub poll. Every work item, including one
  that began as a Trello card, therefore came from a labelled GitHub listing and has labels.
- Issue #60 (merged) fixed the discovery half: a label change now makes the next poll send an
  unconditional request, so issues carrying the new label are found.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Queued work no longer carrying the label does not dispatch (Priority: P1)

The maintainer changes `[github] label` — to scope a verification round to test issues, say —
and restarts the daemon. Nine items discovered under the old label are sitting in `ready`.
Today they stay eligible, and the moment capacity allows they dispatch: real worktrees and
real branches in repositories nobody meant to touch, against issues that do not carry the
label the configuration now says means "dispatch this".

After this change each queued item is checked against the configured label every time the
queue is planned. An item whose issue, as last read, does not carry it stays in the queue
but is held, with a reason naming the label, and is not dispatched. Nothing is abandoned and
nothing changes state: changing the label back releases every one of them.

**Why this priority**: It is the reported defect, and its failure is outward-facing — branches
and sessions in the wrong repositories.

**Independent Test**: Seed `ready` items whose stored labels include `robot-army`, configure a
different label, run a dispatch pass with free capacity. Nothing is dispatched, each item is
reported held with the label named, and setting the label back makes them dispatchable.

**Acceptance Scenarios**:

1. **Given** a `ready` item whose issue carried `robot-army` when last read, and `[github]
   label = "scratch"`, **When** dispatch runs with free capacity, **Then** the item is not
   dispatched and no worktree, branch or session is created for it.
2. **Given** the same item, **When** the maintainer runs `status` or opens the web queue,
   **Then** the item appears in its usual position, held, with a reason saying its issue does
   not carry the `scratch` label.
3. **Given** the same item and the machine at its session cap, **When** the queue is shown,
   **Then** the item's reason is the label, not the cap — so raising the cap is not suggested
   as the way to start it.
4. **Given** the same item, **When** the label is set back to `robot-army` and the daemon
   restarted, **Then** the item is dispatchable again with no other action.
5. **Given** a de-scoped item ahead of a dispatchable one in the queue, **When** dispatch runs,
   **Then** the de-scoped item is skipped and the one behind it dispatches in the same pass.

---

### User Story 2 - Labelling an issue under the new label brings its queued item back (Priority: P2)

Having changed the label, the maintainer decides one of the old items should run after all
and puts the new label on its issue. Today that would change nothing: the poll finds the
issue, sees a row already exists, and skips it, so the stored labels — and therefore the
hold — would never move. The only way out would be to abandon the item, which is terminal.

After this change, when a poll finds an issue that already has a queued item, the item's
stored labels are brought up to date from the listing, so the hold lifts on the next plan.

**Why this priority**: Without it the hold in story 1 can only be lifted by reverting the
configuration; with it, labelling an issue does what labelling an issue has always meant.

**Independent Test**: With an item held for a missing label, deliver a poll listing that
contains its issue carrying the configured label; after the poll the item is dispatchable.

**Acceptance Scenarios**:

1. **Given** an item held because its stored labels lack `scratch`, **When** a poll's listing
   contains its issue carrying `scratch`, **Then** the stored labels are updated, the change
   is recorded, and the item is no longer held for the label.
2. **Given** a queued item whose issue appears in a listing with labels identical to those
   stored, **When** the poll runs, **Then** nothing is written and nothing is recorded — the
   steady-state poll stays as quiet as it is today.

---

### User Story 3 - The mismatch is announced at startup (Priority: P3)

The moment the maintainer can still act on a label change is when the daemon starts with it.
If any queued item's issue does not carry the configured label, startup records a single
warning saying how many, naming the configured label, and pointing at the queue — before any
dispatch pass runs.

**Why this priority**: The hold in story 1 already prevents harm; this makes the consequence of
the edit visible without having to go and look.

**Independent Test**: Start the daemon with `ready` items whose stored labels lack the
configured label; exactly one warning record is written, naming the count and the label. Start
it with none; no such record is written.

**Acceptance Scenarios**:

1. **Given** four `ready` items not carrying the configured label and five that do, **When**
   the daemon starts, **Then** one warning is recorded naming 4, the configured label, and the
   ids of the four items.
2. **Given** every `ready` item carries the configured label, **When** the daemon starts,
   **Then** no warning is recorded.

### Edge Cases

- **Stored labels that cannot be read** (malformed or not a list): the item is treated as not
  carrying the label and held, never dispatched. Failing open here would dispatch exactly the
  work the check exists to stop.
- **Precedence**: only `paused` and `held` outrank the new reason. Both are statements that
  would stop the item whatever its label; everything below them — capacity, `wait_for_merge`,
  onboarding, the board column, failure residue — names a fix that cannot start this item
  while its label is wrong.
- **Work already begun** (`dispatching`, `active`, `awaiting_review`, `interrupted`): not in the
  queue and not affected. `resume` and `restart` of such work are not refused on the label.
- **`failed` items**: not in the queue. `retry` already re-reads the issue and re-evaluates it,
  label included, before returning it to `ready`.
- **Removing the label from an issue on GitHub** while the configuration is unchanged: the
  issue drops out of the label-filtered listing, so no refresh reaches its row and its stored
  labels still carry the label. Not addressed here (see Assumptions).
- **Simulated and dry-run rows**: held by the same rule; they are in the same queue.
- **Configuration with the label unchanged**: every item discovered under it carries it, so
  nothing is held and the queue is identical to today's.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Planning the queue MUST hold any `ready` item whose stored labels do not include
  `[github] label`, using the same comparison discovery uses.
- **FR-002**: The hold MUST be a distinct, named hold reason, and its detail MUST name the
  configured label and the labels the issue was last read with.
- **FR-003**: The new hold MUST rank directly below `held` and above every capacity reason, so
  that on a full machine a de-scoped item reports its label rather than the cap.
- **FR-004**: The hold MUST be per-item: dispatch skips the held item and continues to the next
  candidate in the same pass.
- **FR-005**: The hold MUST NOT change the item's state or write any column; lifting the cause
  lifts the hold on the next plan.
- **FR-006**: An item whose stored labels cannot be read MUST be held, not dispatched.
- **FR-007**: When a poll's listing contains an issue that already has a `ready` item, the
  item's stored labels MUST be updated to the listing's if they differ, and the update MUST be
  recorded in the audit log with the old and new labels. Identical labels MUST write and record
  nothing.
- **FR-008**: At daemon startup, before the first dispatch pass, if one or more `ready` items do
  not carry the configured label, exactly one warning record MUST be written naming the count,
  the configured label and the items' ids. None MUST be written when there are none.
- **FR-009**: A dispatch pass that dispatches nothing because of this hold MUST be recorded the
  way any other stalled pass is today, by the existing hold record.
- **FR-010**: `resume`, `restart` and `retry` MUST behave as they do today.
- **FR-011**: The hold MUST appear on every surface that shows the queue today — `status`, the
  web queue — through the one planned order, with no surface-specific copy of the rule.

### Key Entities

- **Work item's stored labels**: the labels its issue carried when last read. Written at
  discovery and by `retry` today; after this change also by a poll that finds the issue again.
- **Hold reason**: gains one value for "the issue does not carry the configured label",
  computed on read like every other and never stored.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The incident's situation — nine queued items discovered under the old label,
  the label changed, the cap raised — dispatches none of the nine.
- **SC-002**: With the label unchanged, every existing ordering and dispatch test passes
  unmodified: an installation that never changes its label sees no difference.
- **SC-003**: Every de-scoped item's queue entry names the configured label, so the maintainer
  can tell why it is held without opening the log.
- **SC-004**: A held item is released by either supported route — reverting the label, or
  labelling its issue — with no `abandon`, database edit or other manual step.

## Assumptions

- The check uses the labels stored on the row rather than re-reading the issue, as the issue
  proposes: no extra request per queued item per plan, and `plan` stays free of I/O, which it
  must because the web interface calls it on every page render.
- Detecting a label *removed on GitHub* is out of scope. It would mean treating absence from a
  label-filtered listing as a label change, which is only sound when the listing is known to be
  complete and unconditional, and the issue asks for neither. The configuration change is the
  reported failure and the one this feature closes.
- A hold reason rather than a state change, as the issue argues: silently abandoning rows on a
  configuration edit would be its own surprise, and a held item is released by undoing the edit.
- The startup warning goes to the audit log like the existing `daemon.config_warning` and
  `daemon.environment_warning` records; it does not block startup.
