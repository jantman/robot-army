# Feature Specification: Give the Missing-Transcript Check Time to Be Right

**Feature Branch**: `20260831-202506-no-transcript-grace-period`

**Created**: 2026-08-31

**Status**: Draft

**Input**: User description: "issue #58 on this repo" — *`no_transcript` fires on every healthy live dispatch: the check runs a second after exec, before the worker has written the transcript*

## Context

When a session is confirmed running, the orchestrator immediately asks whether that session has
left a resumable transcript behind, and raises a `no_transcript` anomaly when it has not. The
question is the right one — a session that runs, exits cleanly, and can never be resumed is the
silent failure this detector exists for. The moment is wrong: the check runs within a second or
two of the worker starting, and the worker does not write its transcript until it begins
processing. The file reliably does not exist yet, so the anomaly fires on **every healthy
dispatch**.

This was found on the first live dispatch ever performed (2026-08-30). One dispatch, one anomaly,
and the transcript in question appeared healthy and complete less than eight seconds later.

Three things make this worse than an ordinary false positive:

1. **It is the detector for the failure that hides best.** Its whole purpose is catching a session
   that looks perfect and can never be resumed. A detector that fires every single time is a
   detector nobody reads, and this is precisely the one that must be read.
2. **Its guidance misdirects.** The anomaly tells the maintainer to hunt for stray environment
   variables in the terminal daemon's environment. On the machine where this fired that
   environment was verifiably clean, so the advice leads away from the answer rather than toward
   it.
3. **No rehearsal can see it.** The check is skipped for simulated sessions, and the condition it
   is skipped on is the effect level rather than "did a real session process ever run". At the
   rehearsal level where sessions are genuinely launched but nothing outward-facing happens, the
   session is real and writes a real transcript — yet the check is switched off there too. Every
   rehearsal is therefore blind to this behaviour, and the first observation of it was on a real
   dispatch against a real issue. That is the exact class of defect the rehearsal ladder exists
   to surface early.

Out of scope: changing what the transcript check looks for, where transcripts are discovered, or
how anomalies are reviewed and acknowledged.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A healthy dispatch raises no anomaly (Priority: P1)

The maintainer dispatches a work item. The session starts normally and writes its transcript a
few seconds later, as sessions always do. No anomaly is raised — not at dispatch, and not
afterwards.

**Why this priority**: This is the entire report. Until this holds, every dispatch produces a
false alarm, and the outstanding-anomaly list — the maintainer's one signal that something needs
attention — is guaranteed to be noise from the first dispatch onward.

**Independent Test**: Dispatch an item whose transcript appears shortly after the session starts,
then inspect outstanding anomalies. The story passes when no `no_transcript` anomaly exists for
that session at any point in its life.

**Acceptance Scenarios**:

1. **Given** a work item ready to dispatch, **When** the session is confirmed running and its
   transcript does not yet exist, **Then** no anomaly is raised at that moment.
2. **Given** a session confirmed running with no transcript yet, **When** its transcript appears
   before the grace period elapses, **Then** no anomaly is ever raised for that session.
3. **Given** a session whose transcript appears and which then runs for hours, **When**
   reconciliation examines it on every pass, **Then** it raises no anomaly on any pass.
4. **Given** a session that ends cleanly having written a transcript, **When** it is examined
   after it has ended, **Then** it raises no anomaly.

---

### User Story 2 - A genuinely unresumable session is still reported (Priority: P1)

A session runs, looks perfectly healthy, and never writes a transcript. It is reported as a
`no_transcript` anomaly once the absence has become meaningful — after the session has had ample
opportunity to write one — and the report names the session, the work item it belongs to, and a
first check that is actually worth making.

**Why this priority**: Equal to Story 1 and inseparable from it. Silencing the false positive by
deleting the detector would trade a noisy signal for no signal at all, and the failure it detects
is unrecoverable and invisible by any other means.

**Independent Test**: Run a session that never produces a transcript and let the grace period
elapse. The story passes when exactly one anomaly appears naming that session and that work item,
and its guidance stands up to being followed.

**Acceptance Scenarios**:

1. **Given** a session confirmed running whose transcript never appears, **When** the grace period
   has elapsed since the session started, **Then** exactly one `no_transcript` anomaly is raised
   naming the session and its work item.
2. **Given** a session with no transcript that has already been reported, **When** the system
   re-examines it repeatedly over the following day, **Then** no additional anomaly is created for
   that session.
3. **Given** a session that ended without ever writing a transcript, **When** it is examined after
   it has ended, **Then** the anomaly is raised for it just as it is for a still-running session —
   ending does not exempt it.
4. **Given** any raised `no_transcript` anomaly, **When** the maintainer reads it, **Then** its
   guidance describes a check that can distinguish the two real causes and does not assert a cause
   the system has not confirmed.
5. **Given** a session whose transcript is missing, **When** the anomaly is raised, **Then** the
   record states how long the transcript was waited for, so the maintainer can judge whether the
   wait was long enough.

---

### User Story 3 - A rehearsal can exercise the detector (Priority: P2)

The maintainer rehearses at the level where sessions really launch but nothing outward-facing
happens. A session that fails to write a transcript there is reported exactly as it would be on a
live dispatch. Only sessions that never ran a real process are exempt.

**Why this priority**: This is why the defect survived to the first live dispatch. Restoring the
detector below `live` does not fix the reported bug, but it is what makes the fix — and any future
regression in it — observable before a real issue is at stake. It ranks below the first two
because the system is correct without it and merely untestable in rehearsal.

**Independent Test**: Rehearse a dispatch at the level that launches real sessions, with the
transcript suppressed. The story passes when the anomaly is raised there, and when a fully
simulated dispatch — no process at all — raises nothing.

**Acceptance Scenarios**:

1. **Given** a rehearsal level at which sessions are genuinely launched, **When** a session runs
   and never writes a transcript, **Then** the anomaly is raised for it.
2. **Given** a simulated session that never ran a process, **When** it is examined at any age,
   **Then** no anomaly is raised, because there was no session to leave a transcript.
3. **Given** the missing-transcript check, **When** it decides whether a session is exempt,
   **Then** the decision is made from what the session record says about the process that ran, not
   from the effect level in force.

---

### Edge Cases

- **The daemon restarts between dispatch and the end of the grace period.** The check must still
  happen; a restart must not make a session permanently unexamined.
- **A session ends before the grace period elapses.** It is still not judged until the grace
  period has passed since it started — a fast, clean session gets the same benefit of the doubt as
  a slow one.
- **A transcript exists and is later deleted.** The session is not re-reported; the check is a
  once-per-session question, and a transcript removed after the fact is the maintainer's doing.
- **A session is resumed or restarted.** Each session is examined on its own terms; a new session
  is a new question, and the predecessor's transcript does not answer it.
- **Many sessions are awaiting their first examination at once.** The check must not delay
  dispatch or reconciliation, and its cost must stay bounded by the number of open sessions, not
  by the history of every session ever run.
- **A previously raised anomaly is acknowledged and the transcript is still missing.** The
  maintainer has seen it and decided; it is not raised again for that session.
- **A session that was never confirmed running.** There is no session to have written anything,
  so nothing is reported.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST NOT raise a `no_transcript` anomaly as part of dispatching a work
  item. Confirming a session running MUST NOT itself produce this anomaly.
- **FR-002**: The system MUST NOT report a missing transcript for a session until a defined grace
  period has elapsed since that session started.
- **FR-003**: The system MUST re-examine sessions that have not yet been judged as part of the
  periodic self-check it already performs, so a session whose transcript never appears is
  reported without the maintainer taking any action.
- **FR-004**: The system MUST report a missing transcript whether the session is still running or
  has already ended, once the grace period has elapsed.
- **FR-005**: The system MUST raise at most one `no_transcript` anomaly per session, including
  across daemon restarts and including after an earlier anomaly for that session has been
  acknowledged.
- **FR-006**: The system MUST NOT re-examine a session once its transcript has been observed to
  exist.
- **FR-007**: The system MUST exempt from this check only sessions for which no real process was
  ever recorded. The exemption MUST be derived from the session's own record, not from the effect
  level in force.
- **FR-008**: The `no_transcript` anomaly record MUST identify the session and the work item it
  belongs to, and MUST state how long the transcript was waited for.
- **FR-009**: The anomaly's guidance MUST NOT assert an unconfirmed cause. It MUST describe how to
  distinguish a suppressed transcript from a session that never produced one, without directing
  the maintainer to a single suspected cause as though it were established.
- **FR-010**: Determining whether a session still needs examining MUST be bounded by the number of
  sessions currently open or recently ended, not by the full session history.
- **FR-011**: The check and its outcome MUST leave a record: which sessions were examined, which
  were found without a transcript, and which were reported.
- **FR-012**: An interruption between a session starting and its examination MUST NOT lose the
  obligation to examine it; the state that says "not yet judged" MUST survive a restart.
- **FR-013**: The documented description of the `no_transcript` anomaly MUST match the behaviour
  actually implemented, including when it fires and what it means.

### Key Entities

- **Session record**: the row describing one dispatched session — when it started, whether a real
  process backed it, whether it has ended, and whether its transcript has been accounted for.
- **Anomaly record**: one reported observation of a session that produced no resumable transcript,
  identifying the session, its work item, and how long was waited.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Ten consecutive healthy dispatches produce zero `no_transcript` anomalies.
- **SC-002**: A session that never writes a transcript is reported unprompted, at most 7 minutes
  after that session started.
- **SC-003**: A session with a missing transcript, left unattended for 24 hours, produces exactly
  one anomaly record.
- **SC-004**: The detector can be exercised end to end without a live dispatch: a rehearsal that
  launches real sessions reproduces both the reported case and the healthy case.
- **SC-005**: Following the anomaly's guidance leads to the actual cause in a case where the
  transcript was suppressed, and does not assert a cause in a case where it was not.
- **SC-006**: The maintainer can tell, from the anomaly record alone, how long the system waited
  before concluding the transcript was missing.

## Assumptions

- The grace period is a fixed value in the system, not a configuration knob: there is one caller
  and no second use in hand. **300 seconds (5 minutes)** is the proposed value — the one
  measurement available shows a transcript appearing in under 8 seconds on a warm cache, and 5
  minutes is generously above that while still reporting a genuine failure within minutes.
- The examination runs on the existing periodic reconciliation pass rather than on a mechanism of
  its own. Reconciliation already sweeps open sessions on a timer and already reasons about their
  age.
- "No real process was ever recorded" is the existing signal that distinguishes a simulated
  session from one that genuinely ran — the same distinction reconciliation already draws when
  deciding whether a session could have been alive.
- Transcripts continue to be discovered where they are discovered today; nothing about how a
  transcript is located changes.
- Sessions that ended some time ago and were never judged are not chased indefinitely — a session
  still open, or recently ended, is the population this check covers.
- The two causes worth distinguishing are: the worker was configured such that it never saved a
  transcript, and the session died before it wrote one. The guidance names both.
