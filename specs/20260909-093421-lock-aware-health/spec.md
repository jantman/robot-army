# Feature Specification: The Dead-Man's Switch Reads the Lock as Well as the Heartbeat

**Feature Branch**: `robot-army/issue-52-the-dead-man-s-switch-has-a-180s`

**Created**: 2026-09-09

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#52 — "The dead-man's switch has a ~180s detection floor because `health` never checks the daemon lock, which the web UI reads in under a second" (labels: bug, robot-army). Found during the issue #1 verification round on 2026-08-30, running quickstart scenario 7 (T138).

**Baseline**: written against `main` at 64e8ebb. Verified in that tree rather than assumed:

- `health.check` (`src/robot_army/health.py:100`) takes a heartbeat path, a threshold and an
  optional `now`. It reads that one file and judges its age. It observes nothing else, and no
  caller can make it observe anything else.
- `daemon.is_locked` (`src/robot_army/daemon.py:129`) is the lock probe, and it takes a
  **shared** lock deliberately: an exclusive probe produced 1,558 false positives in 2,400
  concurrent probes, which is what its docstring records and what any new caller must not undo.
- `daemon.py` imports `health` (`src/robot_army/daemon.py:37`), so `health` cannot import
  `daemon`. This is not a new constraint invented here — `health.published_cap` already exists
  in exactly this shape, taking `running` and `lock_holder` as arguments and saying in its
  docstring that it does so because "``daemon`` imports this module, so importing it back would
  be a cycle".
- Every caller that reads the heartbeat for a verdict *already* has the lock reading to hand or
  takes one a line away: `web.handle` (`src/robot_army/web/server.py:1568`) takes exactly one of
  each per request and hands both down; `operations._enforced_cap`
  (`src/robot_army/operations.py:739`) takes both; `operations.status`
  (`src/robot_army/operations.py:304`) takes the heartbeat reading only.
  `operations.health_check` (`src/robot_army/operations.py:5051`) — the dead-man's switch — is
  the one that takes the heartbeat reading and nothing else.
- `status` prints one line about the daemon and it is the health line
  (`src/robot_army/operations.py:382`). There is no separate "daemon running" line to contradict
  it, so on that surface the heartbeat's verdict is the *whole* of what is said about the daemon.
- The timer runs every five minutes (`systemd/robot-army-health.timer`, `OnUnitActiveSec=5min`).
  That cadence is a second, independent latency this feature does not touch; see Assumptions.

## User Scenarios & Testing *(mandatory)*

<!--
  Three stories, ordered by how much damage the current answer does.

  Story 1 is the reported defect and the only one that matters to the switch's purpose: for
  up to the whole staleness threshold after the daemon dies, the one command whose job is to
  notice says everything is fine, and exits 0 so the timer says nothing.

  Story 2 is the other half of the same reading. Once both signals are consulted there are two
  distinct failures behind one former word, and they call for different things from the person
  reading at 2am: a dead daemon wants restarting, a wedged one wants looking at first.

  Story 3 is the consistency requirement. The bug is not only that `health` is slow — it is that
  two surfaces looking at the same machine gave opposite answers for three minutes. Fixing one
  surface and leaving the other blind reproduces the reported disagreement with the roles
  swapped.
-->

### User Story 1 - A dead daemon is reported dead on the next check, not three minutes later (Priority: P1)

The daemon dies — killed, OOM, a crash. The maintainer, or the systemd timer standing in for
them, runs the dead-man's switch:

```
$ robot-army health
ok: heartbeat is 41s old (pid 197630, idle)
$ echo $?
0
```

There is no process. The lock it held is released and any other program on the machine can see
that in under a second — the web interface says `DAEMON NOT RUNNING` on the very next page load.
The switch says `ok` and exits 0, and keeps saying it until the heartbeat crosses the staleness
threshold. In the reported incident that was 183 seconds of the check disagreeing with the
truth, and because the timer only fires every five minutes the first run that actually reported
the failure did so against a heartbeat 473 seconds old.

After this change the check consults the lock as well. With no daemon holding it, the heartbeat's
age is not the question — the process is gone, and the check says so and exits non-zero on the
first run after the death, whatever the heartbeat's age.

**Why this priority**: This is the reported defect, it is the entire purpose of the command, and
it is a false negative — the failure mode of a dead-man's switch that reports health is that
nobody looks.

**Independent Test**: Write a heartbeat dated now, hold no lock, run the check. Confirm it
reports the daemon as gone and exits non-zero, with the heartbeat well inside the threshold.
Repeat at several heartbeat ages from one second to past the threshold and confirm the verdict
does not depend on the age.

**Acceptance Scenarios**:

1. **Given** a heartbeat written seconds ago and no process holding the lock, **When** the
   check runs, **Then** it reports the daemon as having died and exits non-zero.
2. **Given** a heartbeat older than the threshold and no process holding the lock, **When** the
   check runs, **Then** it reports the daemon as having died — not merely as stale — and exits
   non-zero.
3. **Given** a daemon holding the lock and a heartbeat inside the threshold, **When** the check
   runs, **Then** it reports health and exits zero, exactly as it does today.
4. **Given** a daemon holding the lock and a heartbeat past the threshold, **When** the check
   runs, **Then** it still reports failure and exits non-zero — the wedged case is unchanged,
   and the threshold keeps its present meaning and its present default.
5. **Given** no heartbeat file at all and no lock held, **When** the check runs, **Then** it
   reports that the daemon has never run, as it does today.
6. **Given** the lock file does not exist on disk at all, **When** the check runs, **Then** it
   completes normally, treats the lock as unheld, and leaves no lock file behind.

---

### User Story 2 - "Died" and "hung" are told apart, in the text and in the record (Priority: P2)

The reader of a failed check has to decide what to do next, and the two failures the two signals
catch want different things:

- **The lock is released.** The process is gone. Restart it. Nothing is running that could be
  examined, and nothing is holding work.
- **The lock is held and the heartbeat has stopped.** A process is alive and not beating. It is
  wedged, and restarting it destroys the only evidence of why. Look at it first.

Today both arrive as the same word, `STALE`, and the second is the only one the sentence
describes. The check's own docstring already makes this argument for the distinction it does
draw — "absent, unreadable, and stale are three different reasons and are reported as such:
'never started' and 'died an hour ago' call for different actions" — and "died" and "hung"
belong on that list.

After this change the human line says which of the two it is, the machine-readable output names
it in a field of its own rather than leaving it to be parsed back out of English, and the
notification that goes to every configured channel carries the same distinction, because the
notification is what a person will be reading when they are not at the terminal.

**Why this priority**: It does not change *whether* the failure is caught — story 1 does that —
but it decides whether the person woken by it starts in the right place. It is also the part
that has to be got right first time: a message that says "died" of a wedged process would send
someone to restart a daemon whose stack was the only evidence worth having.

**Independent Test**: Produce each state — no lock with a heartbeat, lock held with a stale
heartbeat, lock held with no heartbeat, no lock and no heartbeat, an unparseable heartbeat — and
confirm each yields a distinct named verdict, a human line that says which, and a machine field
carrying the same name. Confirm the notification body carries it too.

**Acceptance Scenarios**:

1. **Given** the lock is unheld and a heartbeat exists, **When** the check reports, **Then** the
   verdict names the daemon as having *died*, and both the human line and the machine output
   carry that name.
2. **Given** the lock is held and the heartbeat is stale and was written by the process holding
   the lock, **When** the check reports, **Then** the verdict names the daemon as *hung*, and
   the line says a process is alive and has stopped beating.
3. **Given** the lock is held and no heartbeat can be read, or the newest heartbeat was written
   by a process other than the lock holder, **When** the check reports, **Then** the verdict
   names it as a daemon that holds the lock and has not yet published evidence of itself, it is
   reported as unhealthy — as it is today — and the line does not call it either dead or hung.
4. **Given** any failing verdict, **When** the check exits, **Then** the exit code is the
   existing check-failure code; the verdict is carried in the text and the machine output, not
   in a new set of exit codes.
5. **Given** notifications are configured and the check fails, **When** the alert is sent,
   **Then** its body names the same verdict as the terminal output.

---

### User Story 3 - Two surfaces looking at one machine give one answer (Priority: P3)

The incident in the issue is not only that the switch was slow. It is that at `t + 1s` the web
interface said `DAEMON NOT RUNNING` and the terminal said `ok`, about the same machine, at the
same moment, and both were reporting exactly what they had looked at.

`status` has the same blindness as `health` and less to protect it: its health line is the only
thing it prints about the daemon, so `health : ok — heartbeat is 41s old` next to a dead daemon
is the whole of what that command says. The web interface already reads both signals in one
place, once per request, and hands them down precisely so the halves of one page cannot disagree
— it simply does not hand the lock reading to the part that judges the heartbeat.

After this change, every surface that has both readings judges against both, and the reason
string a reader sees is the same sentence wherever they read it.

**Why this priority**: It is the smallest of the three and the most easily deferred, but leaving
it out reproduces the reported bug with the roles reversed — a `health` that knows and a `status`
that does not. The readings it needs are already taken at every one of these call sites.

**Independent Test**: With a heartbeat inside the threshold and no lock held, run each surface in
turn and confirm none of them reports the daemon as healthy.

**Acceptance Scenarios**:

1. **Given** a fresh heartbeat and no daemon holding the lock, **When** `status` renders,
   **Then** its health line reports the daemon as having died rather than as `ok`.
2. **Given** the same state, **When** a web page is served, **Then** the daemon account in its
   chrome and its machine-readable payload agree with what `health` would say, and the banner
   continues to read `DAEMON NOT RUNNING` as it does today.
3. **Given** a daemon running normally, **When** any of these surfaces renders, **Then** nothing
   about the output changes from today.

---

### Edge Cases

- **The lock file does not exist.** Never started, or a state directory cleaned out. The check
  must treat this as "no daemon holds the lock" and must not create the file: the switch is a
  reader, and a check that writes into the state directory to answer a question about it is a
  side effect nobody asked for.
- **The lock file exists but cannot be opened** — permissions, a directory in its place, an
  I/O error. The lock cannot be read, so the lock signal is *unknown* rather than *unheld*, and
  the check must not report a death it has not observed. It falls back to judging the heartbeat
  alone, and says that is what it did.
- **A daemon restarts while the check runs.** The new process holds the lock before it writes
  its first heartbeat — it wires boundaries, checks preconditions and runs startup first, which
  is network work and can take seconds — and nothing unlinks the previous daemon's heartbeat. So
  "lock held, newest heartbeat belongs to a dead pid" is a real, ordinary window, not a
  hypothetical, and must not be reported as a hang.
- **Concurrent probes.** More than one thing may probe the lock at once — a web request and a
  timer run, or several web requests. The probe must remain shared; an exclusive probe reports
  the other prober as a running daemon, which is the 1,558-in-2,400 false positive rate already
  measured and recorded.
- **A heartbeat present but unparseable.** Corrupt or truncated. It is neither a death nor a
  hang and must not be reported as either; it stays its own reason, with the lock state named
  alongside it.
- **A daemon holding the lock with a heartbeat inside the threshold whose pid is not the lock
  holder's.** The same restart window as above, caught early. Something is beating and something
  holds the lock, so this stays healthy — as it is today — rather than becoming a new alarm that
  fires on every restart.
- **A very long startup.** A daemon that holds the lock and has not beaten for longer than the
  threshold is reported unhealthy, as it is today. The change must not make an ordinary restart
  quieter than it is now, nor louder.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The health verdict MUST be reached from two signals: whether a daemon holds the
  single-instance lock, and the age of the heartbeat. Neither alone decides it.
- **FR-002**: With no daemon holding the lock and a readable heartbeat, the verdict MUST be
  failure, **independent of the heartbeat's age**, and MUST name the daemon as having died.
- **FR-003**: With a daemon holding the lock and a heartbeat older than the threshold written by
  that same process, the verdict MUST be failure and MUST name the daemon as hung — alive and no
  longer beating.
- **FR-004**: With a daemon holding the lock and either no readable heartbeat or a heartbeat
  written by a different process, the verdict MUST be failure, MUST NOT be described as either a
  death or a hang, and MUST say that a daemon holds the lock and has not yet published evidence
  of itself.
- **FR-005**: With a daemon holding the lock and a heartbeat inside the threshold, the verdict
  MUST be healthy, and its human line MUST be materially unchanged from today's.
- **FR-006**: With no daemon holding the lock and no heartbeat at all, the verdict MUST remain
  "never run", as it is today.
- **FR-007**: A heartbeat that exists but cannot be read or parsed MUST keep its own distinct
  reason and MUST NOT be reported as a death or a hang, whatever the lock says.
- **FR-008**: Every failing verdict MUST exit with the existing check-failure code. No new exit
  codes are introduced; the distinction lives in the text and the machine-readable output.
- **FR-009**: The machine-readable output of the check MUST carry the verdict as a field of its
  own, so a consumer names the state rather than parsing English out of the reason string. It
  MUST also carry what was observed of the lock, including that it could not be observed.
- **FR-010**: The alert sent to configured notification channels MUST carry the same verdict as
  the terminal output, in its body and in its structured fields.
- **FR-011**: The lock probe used by the check MUST take a **shared** lock, not an exclusive
  one, so that concurrent probes cannot report each other as a running daemon.
- **FR-012**: The check MUST tolerate the lock file being absent, treating it as no daemon
  holding the lock, and MUST NOT create the lock file as a side effect of probing.
- **FR-013**: A lock that cannot be probed at all MUST be reported as *unknown*, not as
  *unheld*. In that state the check MUST fall back to judging the heartbeat's age alone and MUST
  say in its reason that the lock could not be read.
- **FR-014**: The staleness threshold MUST keep its present meaning, its present configuration
  key, and its present default. This feature MUST NOT lower it, and MUST NOT make the heartbeat
  signal redundant — the hung case is only visible through it.
- **FR-015**: Every surface that judges the heartbeat and has a lock reading available MUST
  reach its verdict the same way, so that no two surfaces describe the same machine differently
  at the same moment. This covers the dead-man's switch, `status`, and the web interface's
  account of the daemon.
- **FR-016**: A surface that supplies no lock reading MUST behave exactly as it does today,
  judging the heartbeat alone. Consulting the lock is something a caller asks for, never
  something the judgement reaches out and takes for itself.
- **FR-017**: The documentation page for the operating surface MUST describe what the switch now
  consults and what each verdict means, so a person reading a failed check at 2am is reading the
  same account the code implements.

### Key Entities

- **Lock reading**: what was observed of the single-instance lock at one instant — *held*,
  *unheld*, or *unknown*. Taken by the caller, handed to the judgement. Three values, not two,
  because a probe that could not run is not evidence of an absent daemon.
- **Heartbeat reading**: the existing evidence — absent, unreadable, or a parsed record carrying
  a timestamp, a pid and an activity, judged against the threshold.
- **Verdict**: the named state the two readings combine to, one per distinct action a reader
  would take: running normally, died, hung, holding the lock without evidence, never started,
  unreadable. Carried in the human line, in the machine-readable output, and in the alert.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the daemon killed and its heartbeat one second old, the very next run of the
  check reports failure. The detection delay contributed by the check itself falls from up to the
  full staleness threshold (180 seconds by default, 183 seconds as measured in the incident) to
  zero.
- **SC-002**: The verdict for a dead daemon does not vary with heartbeat age: checked at 1s, 30s,
  179s and 300s after death, all four report the same failure for the same reason.
- **SC-003**: A daemon that is alive but has stopped beating is still caught, at exactly the
  threshold it is caught at today — no case that fails today passes after this change.
- **SC-004**: A healthy running daemon produces no failure and no notification across repeated
  checks, including checks running concurrently with web requests probing the same lock.
- **SC-005**: A reader given only the check's output can tell which of "restart it" and "look at
  it before restarting it" applies, from the first line, without consulting any other command.
- **SC-006**: A machine consumer can distinguish every verdict by reading one field, with no
  string matching on the reason text.
- **SC-007**: With a fresh heartbeat and no daemon, no surface — the switch, `status`, or a web
  page — reports the daemon as healthy. The three-minute disagreement in the incident cannot be
  reproduced in either direction.
- **SC-008**: An ordinary daemon restart produces no new alert that it does not produce today.

## Assumptions

- **The timer's cadence is a separate latency and stays as it is.** The switch runs every five
  minutes. This feature removes the delay the check *itself* imposed on top of that; end-to-end
  detection remains bounded by when the timer next fires. The issue's "~180s to ~5s" describes
  the check's own judgement, and the honest statement of the outcome is that a run which happens
  after the daemon has died now reports it, rather than reporting it one threshold later.
- **Lowering the threshold is not part of this.** Heartbeat age has to stay well above the tick
  interval or a busy daemon trips its own alarm, which is precisely why the floor could not come
  down while the lock went unread.
- **The lock is trustworthy evidence on this machine.** One user, one Linux host, one daemon,
  advisory `flock` on a local filesystem. A released lock means the holder is gone, because the
  kernel releases it when the process exits however it exits. No network filesystem case is
  considered.
- **The lock reading is taken by the caller and passed in.** Every call site already has one or
  takes one a line away, and the module that judges the heartbeat cannot import the module that
  owns the lock without a cycle. This also keeps the two halves of a single web page describing
  the same instant, which is what that page already goes to trouble to guarantee.
- **The existing shared-lock probe is reused as it stands**, rather than a second probe being
  written for this caller. The measured false-positive result belongs to that probe's exact
  shape, and a second implementation would be a second thing to keep correct.
- **No backward compatibility is owed to the machine-readable shape.** The repository is public
  but unsupported; adding a field to the check's output and changing a reason string are
  ordinary changes, and the only consumers are in this repository.
- **Nothing new is logged.** The check reads two files and takes no action outside the process;
  the notification it may send is already recorded under `health.notify`, and that record will
  carry the sharper reason without changing shape.
