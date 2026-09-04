# Research: Only the maintainer's own terminal socket may receive a dispatch

**Feature**: `specs/20260904-155257-guard-kitty-socket-owner` · **Date**: 2026-09-04

Everything below was resolved before the plan was written; no `NEEDS CLARIFICATION` remains.

## R1 — What makes a candidate acceptable, and with which system call

**Decision**: `os.lstat(path)`, then require `stat.S_ISSOCK(st.st_mode)` and
`st.st_uid == os.getuid()`. Nothing else about the file is examined.

**Rationale**: `lstat` answers about the name the glob produced, not about whatever it points
at. That matters here specifically: another user may create `/tmp/mykitty-zzz` as a *symbolic
link*. `stat` would follow it, report the target's ownership, and hand back "owned by me" for a
name the attacker controls. `lstat` reports the link itself, whose owner is the attacker, and
`S_ISLNK` is not `S_ISSOCK`, so such a candidate fails both halves of the test. Requiring
`S_ISSOCK` also disposes of the plain file and the directory for free.

`os.getuid()` rather than `geteuid()`: the project never runs setuid, the two are equal, and
`getuid()` is the identity the constitution names as the trust boundary (Principle II).

**Alternatives considered**:

- `os.stat` with a follow-up `os.path.islink` check — two calls and a window between them, to
  reach the same verdict `lstat` gives in one.
- Connecting and reading the peer credentials with `SO_PEERCRED`. That is the strongest check
  available, and it is not available *here*: we do not open the socket, `kitty @` does. Getting
  it would mean replacing the vendor's client with our own protocol implementation, which is a
  far larger change than the finding warrants.
- Trusting the probe response's content. The impostor writes the response.

## R2 — The window between checking a candidate and speaking to it

**Decision**: Refuse a candidate unless every directory on its path, from the candidate's own
parent up to the filesystem root, is owned by this user or by root **and** is either not
writable by group or other, or carries the sticky bit.

**Rationale**: `lstat` describes the file at an instant. `kitty @ --to` resolves the name again
some milliseconds later. If any directory on the path lets a stranger remove and re-create
entries, the name checked and the name used can be different files. The sticky bit is exactly
the property that closes this: it restricts removing and renaming an entry to the entry's owner,
which is why `/tmp` (root-owned, `1777`) is safe to *hold* a socket even though it is not safe
to *trust names in* — and it is why the shipped `/tmp/mykitty-*` setup keeps working after this
change rather than being broken by it. A directory owned by another user is refused whatever its
mode, because its owner can always replace what is inside it.

Walking to the root rather than checking only the immediate parent avoids having to answer "how
far up is far enough" with a special case: a hostile directory anywhere on the path is the same
attack. It is one loop over `Path.parents`, and on the two paths that matter it is four `stat`
calls (`/run/user/1000`, `/run/user`, `/run`, `/`) or two (`/tmp`, `/`).

**Alternatives considered**:

- Check only the immediate parent. Cheaper by two `stat` calls on a code path that runs a
  handful of times per process, and it leaves `/tmp/somedir/mykitty-*` — a shape a maintainer
  could plausibly configure — unprotected.
- Open the directory once and use `os.stat` on a file descriptor with `openat`-style resolution.
  Genuinely removes the race rather than narrowing it, but we still hand a *path* to `kitty @`
  at the end, so the race returns at the last step regardless. The descriptor dance buys nothing
  we can keep.
- Re-check after the probe. The probe is what leaks; checking afterwards is checking after the
  disclosure.

## R3 — Where the socket should live by default, and how the default is computed

**Decision**: The default becomes `f"{paths.runtime_dir()}/mykitty-*"`, computed at
configuration load rather than frozen at import.

**Rationale**: `paths.runtime_dir()` already exists and already encodes the answer this feature
needs: `XDG_RUNTIME_DIR` when set — `/run/user/<uid>`, mode `0700`, owned by the user, on tmpfs,
cleared at logout — and `paths.state_home()` (`~/.local/state`) when it is not, which is owned
by the user and not writable by anyone else. That satisfies FR-009 without inventing a second
fallback rule, and it is the same reasoning `docs/state.md` already records for the daemon's own
sockets.

It must be computed, not a class-level constant: the value depends on the environment, and the
tests set `XDG_RUNTIME_DIR` per case. `dataclasses.field(default_factory=...)` on the frozen
dataclass, and an explicit call in the loader's `_str(...)` default, keep the two agreeing.

Verified against the installed terminal (kitty 0.48.2): `listen_on` expands environment
variables, so `listen_on unix:${XDG_RUNTIME_DIR}/mykitty` is a valid line for the documentation
to give, and the PID is appended with a hyphen, so the glob shape is unchanged.

**Alternatives considered**:

- An abstract socket (`unix:@mykitty`), which the terminal also supports. Rejected, and worth a
  line in the README to say so: abstract-namespace sockets carry no filesystem permissions at
  all, so any local user can connect to them. It is strictly worse than the `/tmp` default it
  would replace.
- Hard-coding `/run/user/<uid>`. Breaks the login-less case the existing fallback already
  handles, and duplicates a decision `paths.py` owns.
- Leaving the default alone and relying solely on R1/R2. The checks do protect it, but a reader
  copying the README would still be building the exposed setup, and the finding would recur the
  moment the checks are relaxed.

## R4 — Reject, or warn, a pattern rooted somewhere shared

**Decision**: Warn at configuration load; do not reject. The warning fires when the pattern's
fixed leading directory fails the same directory test R2 applies at discovery.

**Rationale**: Rejecting would break the maintainer's running setup — `/tmp/mykitty-*`, which
R2 deliberately keeps working — on the strength of a hazard that R1 and R2 have already closed.
The value of saying something is that the reader learns the recommended location exists; the
value of *refusing* is nil once the discovery checks are in place. Warnings already have a
delivery path: `Config.warnings`, printed by the CLI, the daemon's startup, `doctor`, and the
web interface's non-live banner.

**Alternatives considered**:

- `ConfigError`. Would have refused to start on the maintainer's own machine as it is configured
  today. A security fix that requires an unrelated edit before the daemon runs again is a fix
  that gets reverted.
- Silence. The existing wildcard warning shows the house style is to say something when a
  configured value is defensible but not what you want.

## R5 — Telling the three failure states apart

**Decision**: `probe()` keeps returning `str | None` and gains a companion attribute holding the
refusals from the last discovery. `doctor` and the daemon's startup check read it to compose
their detail line; the audit record for `kitty.probe` carries the same refusals inline.

**Rationale**: The three states a maintainer confuses — nothing running, something
impersonating, a location that cannot be trusted — are distinguished only by what was refused
and why. Changing `probe()`'s return type would touch the display protocol and the simulated
implementation for the benefit of two call sites; an attribute set alongside the cached socket
is read by the two callers that want it and ignored by everyone else. The audit record already
aggregates the probe attempts into one entry, so the refusals belong in that same entry rather
than in records of their own (Principle III: one action, one record, reconstructible).

**Alternatives considered**:

- A richer return value (`ProbeResult`). More faithful, and it forces `SimulatedDisplay` and the
  `Display` protocol to carry a shape that exists for a diagnostic string.
- Logging refusals and leaving the surfaces to say "nothing answered". That is the current
  misleading wording, kept.

## R6 — What the simulated display does

**Decision**: Nothing changes. `SimulatedDisplay.probe()` keeps returning its fictional path and
performs no filesystem check.

**Rationale**: The path it returns does not exist and is not spoken to; the whole point of the
simulated level is that no outward effect occurs. Subjecting it to the checks would make reduced
effect levels fail on a machine where the terminal is not running, which is precisely the
machine where reduced effect levels are used. `doctor` is unaffected because it deliberately
constructs the *real* display rather than the wired one, and says so in a comment.

## R7 — Testing an ownership check without a second user

**Decision**: Exercise the refusals with filesystem shapes a single user can build in a
`tmp_path`: a real `AF_UNIX` socket (accepted), a plain file, a directory, a symlink pointing at
the accepted socket, a vanished path, and a directory whose mode is set to `0777` without the
sticky bit (refused). Ownership-by-another-user is proved by monkeypatching `os.getuid` to
return a different id, which makes every candidate unowned without needing a second account.

**Rationale**: The suite must run as one unprivileged user in CI. Every branch of the check is
reachable that way except "owned by someone else", and that branch is a single comparison whose
other side is the only thing that can vary — so moving the comparison's other side proves it.
The world-writable-directory case needs no privilege at all: `chmod 0777` on a directory this
user owns is refused by the rule as written, because it is writable by others without the sticky
bit.

**Alternatives considered**: `unittest.mock` over `os.lstat` for everything. Faster, and it
would have passed just as happily against a check that inspected the wrong field, because
nothing real would ever have been inspected.
