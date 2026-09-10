# Feature Specification: A Stored ETag Is Only Replayed Against the Request That Produced It

**Feature Branch**: `robot-army/issue-60-changing-github-label-silently-blinds`

**Created**: 2026-09-10

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#60 — "Changing [github] label silently blinds
polling: stored ETags are keyed by repo only, so the stale one is replayed against a different
query and GitHub answers 304" (labels: bug, robot-army). Found during the issue #1 verification
round on 2026-08-30, observed twice.

**Baseline**: written against `main` at 0d3f70d. Verified in that tree rather than assumed:

- `poll_state` (`src/robot_army/migrations.py:105`) has one row per `repo_key` and one `etag`
  column. Nothing in the row says what request the ETag answered.
- `GitHubReader.poll` (`src/robot_army/boundaries/github.py:446`) builds the request from
  `[github] label` plus four constants (`state=open`, `per_page=100`, `sort=updated`,
  `direction=desc`) and sends the stored ETag as `If-None-Match` whenever one exists.
- `poll.poll_repo` (`src/robot_army/poll.py:139`) passes `state.etag` straight through and, on
  a 304, saves the same ETag back — so a stale one is not merely replayed once, it is
  re-persisted every cycle and never ages out.
- The Trello board poll writes rows into the same table under `trello:board:<id>` with `etag`
  always `NULL` (`src/robot_army/intake.py:451`). It never sends a conditional request.
- `robot-army poll` (`operations.poll_now`) delegates to a running daemon through a marker file
  that carries no options; `--repo` is already recorded-but-not-honoured on that path.

## User Scenarios & Testing *(mandatory)*

<!--
  Two stories. Story 1 is the reported defect: after a configuration change the daemon is
  blind and says it is healthy. Story 2 is the upgrade: the machine that hit this still has
  rows whose ETags were captured under an unknown request, and it must recover without anyone
  opening sqlite.
-->

### User Story 1 - Changing the label does not blind discovery (Priority: P1)

The maintainer changes `[github] label` — to try a throwaway label during a verification
round, or back again afterwards — and restarts the daemon. Today every repository keeps
sending the ETag it captured under the *old* label. GitHub answers `304 Not Modified`, the poll
reads that as "nothing changed", and issues carrying the new label are never seen. Every health
surface reports normal, and it lasts until something else happens to change that repository's
open-issue list, which for a quiet repository may be never.

After this change the stored ETag is bound to the request that produced it. The first poll
after the label changes notices that the request it is about to send is not the one the ETag
answered, sends it unconditionally, gets the real listing, and stores the new ETag together with
the new request. From the second poll on, the 304 economy is back.

**Why this priority**: It is the reported defect. The failure is silent, indefinite, and hits
every configured repository at once — the worst shape a discovery failure can take.

**Independent Test**: Poll a repository so an ETag is stored, change the configured label,
poll again. Confirm the second request carries no `If-None-Match`, returns the issues carrying
the new label, and that the third poll is conditional again.

**Acceptance Scenarios**:

1. **Given** an ETag stored from a poll under label A, **When** the label is changed to B and
   the repository is polled, **Then** the request is sent without the stored ETag and the
   issues carrying label B are discovered on that poll.
2. **Given** the poll in scenario 1 succeeded, **When** the repository is polled again under
   label B, **Then** the ETag captured by that poll is sent, and an unchanged listing is a 304
   exactly as today.
3. **Given** an ETag stored under label A, **When** the label is changed to B and back to A
   before any poll runs, **Then** the stored ETag is replayed, because the request is once
   again the one it answered.
4. **Given** the label has not changed, **When** the repository is polled, **Then** behaviour
   is identical to today: the stored ETag is sent and an unchanged listing costs nothing.
5. **Given** a stored ETag is discarded because the request changed, **When** the poll's audit
   record is read, **Then** it says the ETag was not sent and why, so a first-poll-after-change
   200 is explained by the log rather than looking like an unexplained cache miss.

---

### User Story 2 - The machine that already hit this heals on upgrade (Priority: P2)

The machine this was found on has `poll_state` rows whose ETags were captured before any
request was recorded alongside them. Nothing can say which label those ETags answered — the
round that found this swapped the label twice. After this change, an ETag with no recorded
request is treated as not matching the current one: each repository makes one unconditional
request on its first poll after the upgrade and is conditional from then on. Nobody runs
`UPDATE poll_state SET etag = NULL`.

**Why this priority**: Without it, story 1 protects future changes and leaves the one machine
that is known to be affected still able to be blind. It is also what makes the issue's suggested
"startup check" unnecessary as a separate mechanism: the comparison happens on every poll,
including the first after a start.

**Independent Test**: Write a `poll_state` row with an ETag and no recorded request, poll, and
confirm the request is unconditional and the row afterwards carries both the new ETag and the
request that produced it.

**Acceptance Scenarios**:

1. **Given** a row holding an ETag and no recorded request, **When** the repository is polled,
   **Then** the ETag is not sent and the audit record says it was discarded because its request
   was never recorded.
2. **Given** the database is upgraded, **When** the upgrade completes, **Then** existing rows
   keep their ETags, backoff and failure counters; only the new "which request" record is added,
   empty.

---

### Edge Cases

- **A transport failure on the poll after a label change.** The stored ETag and the request it
  belongs to are left as they were, together. The next attempt compares again and still
  discards it. A failure must never leave an ETag paired with a request it did not answer.
- **A 200 that carries no ETag header.** Today the old ETag is carried forward in that case. It
  must not be carried forward *paired with the new request*: an ETag is stored only with the
  request whose response supplied it.
- **A 304 answered to an unconditional request.** Cannot happen from GitHub, but if it did the
  result must not store an ETag for the new request that no response supplied.
- **Any other part of the request changes** — a constant in the code, the repository's path
  encoding, the page size. The same mechanism covers it without anyone having to remember to
  extend it: the whole request is what is compared, not a list of the parts known to vary today.
- **The Trello board row** shares the table and never sends a conditional request. It is
  unaffected: it stores no ETag and no request.
- **Simulated and live runs** poll the same repositories with the same request and share one
  row per repository today; nothing about that changes.
- **Killed between the listing and the save.** The ETag and its request are written in the same
  single-row write, so they are either both the old pair or both the new pair. The next poll
  after restart repeats at most one request.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST store, alongside each GitHub poll ETag, a record of the complete
  request that response answered — the repository path and every query parameter.
- **FR-002**: The system MUST send a stored ETag only when the request about to be sent is
  identical to the one recorded with it. Any difference in any part of the request MUST cause
  the request to be sent without it.
- **FR-003**: A stored ETag with no recorded request MUST be treated as not matching.
- **FR-004**: The request record MUST be derived from the same values the request is built
  from, in one place, so that the request sent and the request recorded cannot drift apart.
- **FR-005**: After a successful poll, the ETag MUST be stored together with the request whose
  response supplied it, in the same write. An ETag MUST NOT be stored paired with a request
  whose response did not supply it.
- **FR-006**: A failed poll MUST leave the stored ETag and its recorded request unchanged, and
  still paired.
- **FR-007**: The poll's audit record MUST state whether a stored ETag was sent and, when one
  existed and was not sent, why: the request changed, or the request was never recorded.
- **FR-008**: The recorded request MUST be human-readable when the database is inspected, and
  MUST NOT contain a credential.
- **FR-009**: The database upgrade MUST preserve every existing `poll_state` value and MUST be
  safe to interrupt and re-run.
- **FR-010**: The Trello board poll's bookkeeping MUST be unaffected.

### Out of scope

- **A `--no-cache` / `--force` flag on `robot-army poll`.** Suggested in the issue as a
  diagnostic. With the cause removed it has no known remaining use, and when a daemon is running
  `poll` delegates through a marker file that carries no options — the flag would be silently
  ignored on exactly the path a live machine takes, which is the existing limitation of
  `--repo`. Adding a second marker format for a diagnostic with no present need is the
  speculative generality the constitution forbids.
- **A separate startup check** comparing the configured label to the one ETags were captured
  under. FR-002 and FR-003 perform that comparison on every poll, which includes the first after
  a start; a second mechanism would duplicate it.
- **Detecting "discovery is blind" in general** on the health surfaces. This fixes the known
  cause; it does not add a heuristic for unknown ones.

### Key Entities

- **Poll bookkeeping row**: one per repository (and one per Trello board). Gains a record of the
  request its ETag answered. An ETag and its request are one fact; neither is meaningful alone.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After any change to the configured label, issues carrying the new label are
  discovered on the first poll of each repository — not after some unrelated change to that
  repository, and not after manual database edits.
- **SC-002**: With no configuration change, the number of full (non-304) listings per
  repository is unchanged from today: zero while the listing is unchanged.
- **SC-003**: Upgrading the machine that exhibited the defect costs at most one unconditional
  request per configured repository, and requires no manual step.
- **SC-004**: Every poll on which a stored ETag was not sent can be explained from the audit log
  alone.

## Assumptions

- GitHub's validators are request-specific. The observed 304s under a changed label show that a
  validator can match across different queries; the fix does not depend on why, only on never
  offering one across requests.
- Request headers that affect the representation (`Accept`, API version) are constants of the
  client, not of the request, and are not part of the recorded request. A change to them is a
  code change that ships with a restart; if one is ever made configurable, it belongs in the
  record.
- The author credential is not part of the recorded request, because it must never be stored
  there (FR-008). A token change that altered visibility would change the response body and
  therefore GitHub's own validator.
