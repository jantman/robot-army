# Feature Specification: Only the maintainer's own terminal socket may receive a dispatch

**Feature Branch**: `speckit/20260904-155257-guard-kitty-socket-owner`

**Created**: 2026-09-04

**Status**: Draft

**Input**: RA-15 in `docs/security-analysis.md` — "the kitty control socket is discovered by
globbing world-writable `/tmp`". Reported as jantman/robot-army issue #125, severity Medium.

The terminal control socket is found by globbing a configured pattern whose shipped default
lives in `/tmp`, and the first candidate that answers wins and is kept for the life of the
process. `/tmp` is world-writable; its sticky bit stops another local user *deleting* the real
socket but not *creating* a second one beside it. Candidates are tried in reverse
lexicographic order, so a name chosen to sort first is probed first. Any local user can
therefore stand up a listener that answers the probe, and from then on every dispatch — which
carries the whole composed prompt and every environment pair as arguments — goes to them
instead, for the life of the daemon. The diagnostic command uses the same discovery and would
report the impostor as healthy.

What the impostor cannot do is fabricate a session: confirmation waits for a session registry
entry carrying the exact identifier the daemon generated. So this is disclosure of everything
a dispatch carries, plus a persistent denial of dispatch — not code execution.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A socket the maintainer does not own never receives a dispatch (Priority: P1)

Another local user creates a listener matching the configured pattern, named so it is probed
before the genuine one, and answers the probe convincingly. The daemon declines it without
sending it anything beyond the probe, continues to the next candidate, and finds the real
socket. Dispatch proceeds normally. If the impostor is the *only* thing matching the pattern,
the daemon reports that it found no usable socket rather than using it.

**Why this priority**: This is the finding. It is the only story that stops the disclosure, and
it holds whatever the pattern is configured to be — including a pattern the maintainer already
has in a configuration file that this change does not rewrite.

**Independent Test**: Point the pattern at a directory containing a socket owned by the running
user and an entry that is not — a plain file, a directory, and a symlink standing in for the
other user's — and confirm the probe reaches only the owned socket, that the others are refused
before the probe command runs against them, and that a run where every candidate is unowned
ends in the same clear failure as a run where nothing matched at all.

**Acceptance Scenarios**:

1. **Given** a candidate that is not owned by the user the daemon runs as, **When** discovery
   runs, **Then** that candidate is refused and no command is run against it.
2. **Given** a candidate that is not a socket — a plain file, a directory, a symbolic link —
   **When** discovery runs, **Then** it is refused and no command is run against it.
3. **Given** an unowned candidate that sorts before the genuine socket, **When** discovery runs,
   **Then** the genuine socket is the one selected and cached.
4. **Given** every candidate is refused, **When** discovery runs, **Then** the caller is told no
   usable socket was found, in the same shape as when the pattern matched nothing.
5. **Given** any candidate is refused, **When** the audit log is read afterwards, **Then** it
   records which candidates were refused and for what reason, alongside the ones that were
   probed.

---

### User Story 2 - A candidate in a directory another user can rearrange is refused (Priority: P1)

A candidate can be owned by the maintainer at the moment it is inspected and be a different
file by the moment it is used, if the directory holding it allows another user to remove and
replace entries. The daemon refuses any candidate whose directory is writable by other users
without the restriction that stops them touching entries they do not own, and says so.

**Why this priority**: Without it, Story 1's check is a check with a window after it. It is the
same size of change, in the same place, and refusing is the only correct answer — the daemon
cannot make such a directory safe.

**Independent Test**: Place an owned socket in a directory that is world-writable without the
sticky restriction and confirm the candidate is refused with a reason naming the directory;
repeat with the restriction set and with a private directory, and confirm both are accepted.

**Acceptance Scenarios**:

1. **Given** an owned socket in a directory writable by all users without the sticky
   restriction, **When** discovery runs, **Then** the candidate is refused and the reason names
   the directory rather than the socket.
2. **Given** an owned socket in a world-writable directory that *does* carry the sticky
   restriction — the shape `/tmp` has — **When** discovery runs, **Then** the candidate is
   accepted, because no other user can replace an entry there.
3. **Given** an owned socket in a directory only the maintainer can write, **When** discovery
   runs, **Then** the candidate is accepted.

---

### User Story 3 - The shipped setup no longer puts the socket where anyone can crowd it (Priority: P2)

A maintainer following the documentation from scratch ends up with the socket in their own
per-user runtime directory, which no other user can write, rather than in the shared temporary
directory. The shipped example configuration, the documented terminal configuration line, and
the built-in default all agree. A maintainer who already has the old location keeps working
unchanged, and is told once, at configuration load, that the location is shared and why the
recommended one is better.

**Why this priority**: It removes the exposure at the source instead of defending against it,
and it is what a reader of the documentation will copy. It is second because it protects only
new setups: the existing one is protected by Stories 1 and 2 whatever the configured pattern.

**Independent Test**: Load a configuration that sets no pattern and confirm the default names
the per-user runtime directory; load one whose pattern is rooted in a shared world-writable
directory and confirm a warning is produced and the configuration still loads; confirm the
example configuration and the documented terminal setup line name the same location as the
default.

**Acceptance Scenarios**:

1. **Given** a configuration that does not set the pattern, **When** it is loaded, **Then** the
   pattern used is rooted in the per-user runtime directory.
2. **Given** a configuration whose pattern is rooted in a directory writable by all users,
   **When** it is loaded, **Then** a warning explains the exposure and names the recommended
   location, and the configuration loads successfully.
3. **Given** the per-user runtime directory is not defined in the environment, **When** the
   default is resolved, **Then** it resolves to a documented owned-and-private location rather
   than silently falling back to the shared one.
4. **Given** the documentation and the shipped example configuration, **When** they are read
   together, **Then** the terminal setup line and the pattern name the same directory, and the
   README states why it is not the shared temporary directory.

---

### User Story 4 - The diagnostic tells the truth about a refused socket (Priority: P2)

The maintainer runs the diagnostic command after another user has planted a listener, or after
moving the socket and forgetting to update one of the two places it is named. Instead of
"nothing answered", the diagnostic says a candidate was found and refused, and why — so the
maintainer can tell "kitty is not running" apart from "something is impersonating kitty" and
from "the file is there but the directory is unsafe".

**Why this priority**: The diagnostic exists to save the maintainer the debugging session. A
refusal that is indistinguishable from an absence sends them looking in the wrong place, and in
the impersonation case it hides the one thing they most need to know.

**Independent Test**: Run the diagnostic against a pattern matching only a refused candidate and
confirm the reported detail names the refusal and its reason; run it with nothing matching and
confirm the existing wording is unchanged; run it with a good socket and confirm it reports the
socket exactly as it does today.

**Acceptance Scenarios**:

1. **Given** the pattern matches only candidates that were refused, **When** the diagnostic
   runs, **Then** the terminal socket check fails and its detail states that candidates were
   found and refused, with the reason.
2. **Given** the pattern matches nothing at all, **When** the diagnostic runs, **Then** the
   detail is the same as it is today.
3. **Given** a usable socket, **When** the diagnostic runs, **Then** it reports that socket and
   nothing about refusals.
4. **Given** the daemon's own startup check, **When** no usable socket is found, **Then** the
   startup problem it reports distinguishes the same three cases.

---

### User Story 5 - The maintainer is told what a dispatch puts in plain view (Priority: P3)

The prompt and every environment pair a dispatch carries are passed as command arguments, which
any local process can read while the session is starting. Nothing in this feature changes that.
The documentation says so plainly, next to the configuration key that invites a maintainer to
put a value there, so that a credential is a decision rather than an accident.

**Why this priority**: It is a real exposure that needs no attacker, and the shipped example
demonstrates exactly the key most likely to hold a credential. Documenting it is honest and
cheap; removing it is a different change with a different design.

**Independent Test**: Read the operator documentation and the shipped example configuration and
confirm both state that values placed in a repository's environment mapping are visible to other
local processes for the life of the launch.

**Acceptance Scenarios**:

1. **Given** the shipped example configuration, **When** the environment mapping is read,
   **Then** a comment states the values are visible to other local processes and are not the
   place for a credential.
2. **Given** the operator documentation, **When** the section describing dispatch is read,
   **Then** the same exposure is stated once, in the maintainer's own terms.

---

### Edge Cases

- A candidate that is a symbolic link, including one pointing at the genuine socket. It is
  refused: the link's own ownership is what is inspected, and following it would reintroduce the
  substitution the check exists to prevent.
- A candidate that disappears between being listed and being inspected. It is treated as
  refused, with that as its reason, and discovery continues.
- A candidate that cannot be inspected at all — permission denied on the directory. Refused,
  reason recorded, discovery continues; never treated as acceptable by default.
- A configured pattern whose wildcard falls in a *directory* component rather than the filename,
  so that different candidates sit in different directories. Each candidate's own directory is
  what is judged, not the pattern's literal prefix.
- A configured pattern already carrying the `unix:` prefix the terminal uses. The prefix is not
  part of the filesystem path and must be stripped before anything is inspected.
- Several sockets owned by the maintainer matching at once — two of their own terminal instances.
  Both are acceptable; which one is chosen is unchanged by this feature.
- The per-user runtime directory being undefined, which happens in a session started outside a
  graphical login. The default must not silently become the shared temporary directory.
- A maintainer whose existing configuration names the old location. It must keep working; this
  feature warns, it does not break a running setup.
- The simulated display, used at reduced effect levels, which answers with a fictional socket
  path that exists nowhere. It must not be subjected to a filesystem check.
- The cached socket. Once chosen it is kept for the life of the process, deliberately; the checks
  are part of choosing, and this feature does not add re-validation on every use.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Before any command is run against a discovered candidate, the system MUST inspect
  it and MUST refuse it unless it is a socket owned by the user identity the process runs as.
- **FR-002**: The inspection MUST NOT follow symbolic links; a candidate that is itself a link
  MUST be refused.
- **FR-003**: The system MUST refuse a candidate whose containing directory is writable by users
  other than its owner unless that directory restricts entry removal to the entry's owner.
- **FR-004**: A candidate that cannot be inspected — missing, unreadable, or vanished between
  listing and inspection — MUST be refused, never accepted by default.
- **FR-005**: Refusal of a candidate MUST NOT end discovery; remaining candidates MUST still be
  considered, and the selection among acceptable candidates MUST be unchanged from today.
- **FR-006**: Every refused candidate MUST appear in the audit record discovery already writes,
  with the reason it was refused, distinguishable from a candidate that was probed and did not
  answer.
- **FR-007**: When no candidate is acceptable, the failure reported to the caller MUST state
  whether candidates were found and refused, or whether nothing matched at all.
- **FR-008**: The built-in default pattern MUST name the per-user runtime directory rather than
  the shared temporary directory.
- **FR-009**: When the per-user runtime directory is not defined in the environment, the default
  MUST resolve to a documented location that is owned by the user and not writable by others,
  and MUST NOT fall back to the shared temporary directory.
- **FR-010**: Configuration loading MUST warn when the configured pattern is rooted in a
  directory writable by users other than its owner, naming the recommended location, and MUST
  still load the configuration.
- **FR-011**: The existing warning about a pattern with no wildcard MUST be retained unchanged.
- **FR-012**: The shipped example configuration and the operator documentation MUST name the
  same socket location as the built-in default, and MUST state why it is not the shared
  temporary directory.
- **FR-013**: The diagnostic command's terminal-socket check MUST distinguish, in its reported
  detail, a socket found, no candidates at all, and candidates found but refused with the
  reason.
- **FR-014**: The daemon's startup check MUST make the same distinction in the problem it
  reports.
- **FR-015**: The operator documentation and the shipped example configuration MUST state that
  values in a repository's environment mapping, and the composed prompt, are visible to other
  local processes while a session is launched.
- **FR-016**: The security analysis document MUST record RA-15 as resolved, describing what now
  refuses the impostor and what remains true about the argument exposure.
- **FR-017**: The simulated display used at reduced effect levels MUST NOT be subjected to the
  filesystem checks, and its behaviour MUST be unchanged.

### Key Entities

- **Candidate socket**: A filesystem path produced by expanding the configured pattern. Today
  anything the pattern matches; after this feature, a candidate must also be an owned socket in
  a directory no other user can rearrange before it is spoken to.
- **Socket pattern**: The configured glob naming where the terminal's control socket may be
  found. A pattern rather than a path because the terminal appends its process id.
- **Refusal reason**: Why a candidate was not used — not owned, not a socket, a symbolic link,
  unsafe directory, could not be inspected. Carried into the audit record, the diagnostic, and
  the startup problem, so all three describe the same event in the same terms.
- **Dispatch payload**: The composed prompt and the environment pairs a launch carries. Passed as
  command arguments, therefore readable by other local processes; the object of the disclosure
  this feature prevents from reaching a stranger's socket, and of the documentation this feature
  adds about the exposure that remains.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With a hostile listener present that sorts ahead of the genuine socket, the number
  of dispatch payloads it receives is zero, and dispatch continues to succeed against the
  genuine socket.
- **SC-002**: With a hostile listener present as the only match, no dispatch is attempted at all
  and the failure names the refusal.
- **SC-003**: A maintainer following the documentation from scratch ends up with a socket
  location that no other local user can write to, without having to know why.
- **SC-004**: An existing configuration naming the old location continues to dispatch
  successfully after the change, with one warning at load explaining the exposure.
- **SC-005**: From the audit log alone, a reader can say which candidates were refused, why, and
  which was selected, without re-running anything.
- **SC-006**: The three failure states — nothing running, something impersonating, unsafe
  location — are distinguishable in the diagnostic's output without reading the audit log.
- **SC-007**: The exposure of launch arguments to other local processes is stated in both the
  documentation and the shipped example configuration, next to the key most likely to carry a
  credential.

## Assumptions

- The operating-system user is the trust boundary, per the project constitution. "Owned by the
  user the process runs as" is therefore the whole of the identity check; no other user is ever
  legitimate, including root.
- The recommended location is the per-user runtime directory the desktop session already
  provides, because it exists, is private by construction, and is cleaned up on logout. Where it
  is undefined, a documented directory under the user's own state directory is used instead.
- Refusing an unsafe *directory* rather than attempting to make it safe is correct: the daemon
  does not own the shared temporary directory and must not modify it.
- The world-writable-without-sticky refusal is what closes the window between inspecting a
  candidate and speaking to it. A re-inspection after connecting is out of scope, and would not
  be reachable through the terminal's own client anyway.
- Caching the selected socket for the process lifetime stays as it is; it is a deliberate,
  documented decision and this feature does not revisit it.
- Removing the prompt and environment values from command arguments — passing them by another
  means — is a separate change with its own design, and is out of scope here. This feature
  documents the exposure rather than closing it.
- No new third-party dependency is needed; everything required is standard filesystem inspection.
