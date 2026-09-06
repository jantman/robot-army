# Feature Specification: The session cap every surface shows is the one being enforced

**Feature Branch**: `robot-army/issue-30-web-header-renders-a-stale-session-cap`

**Created**: 2026-09-06

**Status**: Draft

**Input**: jantman/robot-army issue #30 — "Web header renders a stale session cap (`6/5`) because `serve` never refreshes config, and unlike the effect level nothing detects the disagreement"

## Context

Every surface that reports capacity prints one fraction: live sessions over the cap. The
numerator is observed from the machine on every read. The denominator is read out of
whatever configuration the reading process loaded when it started, and no process reloads
it. So the two halves of one fraction can be minutes or days apart in age, and nothing
says so.

The issue reproduces one direction of that. The web server was started at 09:09, the
configuration file was edited at 09:13 to raise the cap from 5 to 7, and the daemon was
restarted while the web service was not:

```
$ robot-army capacity            # fresh process, reads the new file
capacity : 6 of 7 sessions running

$ curl -s localhost:8420/active | grep -oE '[0-9]+/[0-9]+ sessions'
6/5 sessions                     # long-lived process, still holding the old file
```

`6/5` reads as *full and then some, nothing can dispatch*. The truth was `6/7`, with two
slots free. The reader consulting the one number that answers "why is nothing being
dispatched?" was told the opposite of the answer.

The other direction is the same defect and is reachable by the same procedure. Edit the
file and restart *nothing*: now the long-lived daemon is enforcing the old cap while a
freshly-run `robot-army capacity` prints the new one, and the fresh reading is the wrong
one. Reading the file more eagerly cannot fix this class of problem, because the file is
not the authority — the running daemon is. It is the only process that admits or withholds
a dispatch, it fixes its cap when it starts, and that cap cannot change while it runs.

The effect level had exactly this problem and it is already solved: the level shown is
taken from the daemon's heartbeat rather than from the reading process's own
configuration, and a genuine disagreement raises a banner. The cap has neither half. It is
read locally, never compared, and a wrong value is announced with the same confidence as a
right one.

The two are not, however, the same kind of fact, and the difference decides what a
disagreement should do:

- **The effect level is a safety boundary.** Acting at the wrong one launches real work
  that the reader believes is being simulated, so a disagreement refuses mutations.
- **The cap is a reported number.** A disagreement cannot make an action unsafe — the
  daemon enforces its own cap whatever any other process believes — so a disagreement is
  reported, not refused. Refusing work on the strength of it would take a display defect
  and make it an outage.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The fraction on the page is true (Priority: P1)

The maintainer raises the cap, follows the documented go-live procedure, and later looks
at the web interface from a phone to ask why nothing is being dispatched. The fraction in
the header counts live sessions against the cap the running daemon is actually enforcing,
so it answers the question it is being asked, whether or not the process rendering it has
read the configuration file since it changed.

**Why this priority**: This is the defect in the issue, and the failure is a wrong answer
delivered confidently — the worst of the available outcomes. Everything else here exists
to keep it fixed.

**Independent Test**: Run a daemon with one cap, render the web chrome from a context
holding a different cap, and confirm the fraction's denominator is the daemon's.

**Acceptance Scenarios**:

1. **Given** a daemon running with a cap of 7 and a web process still holding a
   configuration that says 5, **When** any page is rendered, **Then** the capacity
   summary reads `6/7`, and the "at capacity" styling reflects 6 of 7 — not full.
2. **Given** a daemon running with a cap of 5 and a freshly-started reader whose
   configuration says 7, **When** capacity is reported, **Then** it reads against 5: the
   fresh file loses to the running daemon in both directions.
3. **Given** a daemon running and a reader whose configuration agrees with it, **When**
   capacity is reported, **Then** the number is exactly what it is today and nothing extra
   is displayed.

---

### User Story 2 - A disagreement is announced, not silently absorbed (Priority: P1)

Having been shown the enforced cap, the maintainer is also told that the process they are
reading is holding stale configuration — which number came from where, and what to do
about it — so that a fraction differing from the one in their editor is explained rather
than mysterious.

**Why this priority**: Taking the number from the daemon fixes the fraction but hides the
condition that produced it: a process running on configuration that no longer matches the
file. The cap is only the visible half of that; the same restart that would fix the cap
also picks up every other key the process read at startup. A silent correction would leave
the maintainer with no way to discover that the interface needs restarting.

**Independent Test**: Render with the two caps differing and confirm the surface states
both values, which one is in force, and that a restart of the reading process is what
reconciles them.

**Acceptance Scenarios**:

1. **Given** a daemon enforcing 7 and a reader configured for 5, **When** any view is
   rendered, **Then** a notice names both numbers, says the daemon's is the one in force,
   and says that restarting this interface is what makes them agree.
2. **Given** the same disagreement, **When** the maintainer performs an action that
   changes work, **Then** it is **not** refused on account of the cap — the notice is
   informational, and a stale denominator never blocks work.
3. **Given** a reader and daemon that agree, **When** any view is rendered, **Then** no
   notice appears at all.

---

### User Story 3 - The terminal and the web agree (Priority: P2)

The maintainer asks the same question two ways — `robot-army status` or
`robot-army capacity` in a shell, and the header on a web page — and gets the same
fraction, because both are reporting the same fact rather than each reporting its own
process's configuration.

**Why this priority**: The issue's reproduction is two surfaces printing different
fractions a second apart. Fixing only the web would leave the terminal free to be wrong in
the other direction, and would leave the maintainer comparing two numbers with no way to
know which to believe. It is P2 only because the terminal's reading is usually the fresher
of the two.

**Independent Test**: With a daemon running at a cap that differs from the configuration
on disk, take a terminal reading and a web reading and confirm both denominators are the
daemon's.

**Acceptance Scenarios**:

1. **Given** a running daemon whose cap differs from the configuration file, **When**
   capacity is reported in the terminal, **Then** the denominator is the daemon's and the
   disagreement is stated there too.
2. **Given** the same, **When** the machine-readable output of those commands is read,
   **Then** it carries the enforced cap and enough to tell that a disagreement exists,
   without a consumer having to parse prose.

---

### Edge Cases

- **No daemon is running.** There is nothing enforcing a cap and nothing to disagree with,
  so the reader's own configured cap applies and no notice is shown. This is the rule the
  effect-level guard already follows, for the reason it already records: refusing to trust
  the configured value on the strength of a heartbeat left by a dead process is the same
  surprise in the other direction.
- **A daemon holds the lock but its heartbeat cannot be read at all.** The enforced cap is
  genuinely unknown. The reader's configured cap is used, and **no new notice is raised**:
  this state already renders a prominent banner saying nothing about the daemon can be
  read, and one account of a situation beats two competing ones.
- **The heartbeat is readable but stale.** Its cap is still used. A daemon's cap is fixed
  when it starts and cannot change while it runs, so a stale heartbeat from the process
  currently holding the lock still names that process's cap correctly; staleness means a
  tick is running long, which is exactly when the machine is busy and the number matters
  most. This mirrors the settled reasoning for the effect level.
- **The heartbeat is from a build that did not publish a cap.** The field is absent rather
  than wrong. The reader's configured cap is used and no notice is raised; the next tick of
  the current build supplies it.
- **The heartbeat carries a cap that is not a usable number** — absent is one thing, a
  string or a negative is another. It is treated as *not published* rather than believed.
- **Capacity is unobservable.** The existing "capacity UNOBSERVABLE" reading is unchanged
  and takes precedence; there is no fraction to correct.
- **The daemon reporting on itself.** The daemon's own dispatch decisions keep using its
  own configuration directly. It must not consult its own heartbeat to learn what it is
  enforcing.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The daemon MUST publish the session cap it is enforcing in its heartbeat, as
  a named field alongside the effect level and the pause flag, so that any process reading
  the heartbeat can learn the cap without reading a configuration file.
- **FR-002**: Every surface that reports capacity MUST report it against the enforced cap:
  the daemon's published cap when a daemon is running and has published one, and the
  reading process's configured cap otherwise. This holds for the web chrome on every page,
  the queue view, `robot-army status`, `robot-army capacity`, and the machine-readable
  output of each.
- **FR-003**: Whether the machine counts as full — for the purpose of what a surface
  displays and how it is styled — MUST be decided against the same enforced cap that is
  displayed, so a surface can never show a fraction that contradicts its own wording.
- **FR-004**: When the enforced cap and the reading process's configured cap disagree,
  every surface that reports capacity MUST say so, naming both values, stating that the
  daemon's is in force, and stating that restarting the reading process is what reconciles
  them.
- **FR-005**: A disagreement MUST NOT refuse, block, or alter any action. It is a report
  about a number, not a safety interlock.
- **FR-006**: When no daemon is running, when no heartbeat can be read, or when the
  heartbeat publishes no usable cap, the configured cap MUST be used and no disagreement
  MUST be reported — an unknown enforced cap is not a disagreement, and the state where
  nothing about the daemon can be read is already announced by the existing banner.
- **FR-007**: The daemon's own dispatch decisions MUST continue to be made against its own
  configuration and MUST NOT depend on reading its own heartbeat. Nothing about which
  sessions launch, or when, changes.
- **FR-008**: The machine-readable capacity payload MUST carry the enforced cap as the cap,
  and MUST additionally carry the reading process's configured cap when the two differ, so
  a consumer can detect the disagreement without parsing a sentence.
- **FR-009**: Reporting capacity MUST remain a read: no audit record, no anomaly, and no
  state written on account of a cap disagreement.
- **FR-010**: The documentation for the heartbeat's shape, and for the surfaces that report
  capacity, MUST describe the new field and the rule that decides which cap is shown.

### Key Entities

- **Enforced cap**: the maximum number of concurrent sessions the running daemon will
  allow. Fixed when the daemon starts, published on every heartbeat, and the authority for
  every reported fraction.
- **Configured cap**: the value in the configuration the reading process loaded at its own
  startup. The fallback when no enforced cap can be learned, and the thing a disagreement
  reports as stale.
- **Cap disagreement**: the condition where both are known and differ. Carries both values
  and resolves to a sentence naming which is in force.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In the issue's exact scenario — cap raised from 5 to 7, daemon restarted, web
  service not — the header reads `6/7`. Today it reads `6/5`.
- **SC-002**: In the reverse scenario — the file edited and nothing restarted — a freshly
  run `robot-army capacity` reports the cap the daemon is enforcing, not the newer number
  in the file. Today it reports the file's.
- **SC-003**: Two surfaces read within the same minute report the same denominator, in
  every combination of stale and fresh configuration. Today they can differ.
- **SC-004**: Whenever a surface shows a denominator that differs from the configuration
  the process rendering it holds, that surface also says so. The count of silently
  substituted numbers is zero.
- **SC-005**: No action that the system permits today becomes refused, and no dispatch
  decision changes, in any of the above states.
- **SC-006**: A reader of the heartbeat file can determine the cap in force without access
  to the configuration file.

## Assumptions

- The daemon is the sole enforcer of the cap, and its cap is immutable for the life of the
  process. Both are true today: the cap is read once into the loaded configuration, and
  every dispatch decision is made inside the daemon.
- One daemon runs at a time, enforced by the existing lock. The heartbeat therefore
  describes the only daemon there is.
- A disagreement is worth a notice on every view rather than a status page, for the reason
  the capacity line is already on every view: "why is nothing running?" is asked from
  wherever the reader happens to be looking.
- Making the web process reload its configuration file was considered and rejected. It
  fixes only the direction where the file is newer than the reader, leaves the opposite
  direction wrong, would silently change every other key mid-process, and still would not
  make the number match what the daemon is doing.
- No configuration key is added. Nothing here is optional or tunable.
- The `privatepuppet` repository — adding `notify` to the config file resource, and the
  go-live comment naming only one unit — is a separate repository and **out of scope**.
  This work makes the number correct whether or not that procedure is fixed, which is the
  point: the display must not depend on an operator remembering a restart.
