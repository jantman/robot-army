# Research: The session cap every surface shows is the one being enforced

Decisions taken before implementation, with what was rejected and why.

## R1 — The daemon publishes the cap; nobody reloads a file

**Decision**: the daemon writes `max_concurrent_sessions` into its heartbeat on every beat,
and every surface that reports capacity takes the cap from there when a daemon is running.

**Rationale**: the configuration file is not the authority and never was. The daemon is the
only process that admits or withholds a dispatch, it reads the cap once when it starts, and
that value cannot change while it runs. A number reported against anything else is a guess
about what some other process is doing.

The heartbeat is already the channel for exactly this class of fact — the effect level, the
pause flag, and the board's health all travel on it for the same reason — and the web reads
it on every page already. Adding a fourth field costs one key.

**Alternatives rejected**:

- **Reload the configuration file in `serve`, on a timer or on mtime.** It fixes only the
  direction where the file is newer than the reader and leaves the opposite direction — file
  edited, nothing restarted — reporting a cap nobody is enforcing. It would also silently
  change every *other* key mid-process, including the effect level, which is a guard the
  interface deliberately fixes at startup. More machinery, half a fix.
- **Have the web ask the daemon over a socket.** There is no such channel, and inventing one
  for a single integer is the "speculative generality" Principle I forbids when a file the
  daemon already writes every five seconds carries it.
- **Leave the number alone and only report the disagreement.** The issue names this as the
  weaker option and it is: it leaves the reader to work out which of two numbers to believe,
  on a page they are reading precisely because they do not know.

## R2 — A disagreement reports; it does not refuse

**Decision**: when the enforced cap and the reading process's configured cap differ, every
surface says so, and nothing is blocked.

**Rationale**: the effect-level mismatch refuses mutations because acting at the wrong level
does something in the world that the operator believes is being simulated. The cap has no
such property. The daemon enforces its own cap regardless of what any other process believes,
so no action taken through a stale interface can oversubscribe the machine. Refusing on the
strength of a cap disagreement would convert a display defect into an outage — and the most
likely time to hit it is right after raising the cap, which is to say the moment the operator
most wants work to flow.

**Alternative rejected**: treat it like the effect level and refuse. Rejected on the
asymmetry above; the spec makes it FR-005 so a later reading cannot quietly "improve" it into
a guard.

## R3 — The field is named for the key it mirrors, and is validated on read

**Decision**: the heartbeat field is `max_concurrent_sessions`, an `int`, defaulting to
`None`. A reader accepts it only when it is an integer of at least 1; anything else — absent,
a string, zero, negative — is treated as *not published*.

**Rationale**: the name is the configuration key verbatim, so someone reading
`heartbeat.json` at 2am maps it to the file they just edited without a translation step. It is
a first-class field rather than a member of `extra` for the reason `dispatch_paused` and
`board` are: the state guide documents this file's shape, and a named field is what that
reader will look for.

Validation matters because "the daemon published a cap" and "the daemon published something"
are different facts, and only the first may override the reader's own configuration.
Believing a garbled value would replace a stale number with a nonsensical one.

## R4 — Where the resolution lives

**Decision**: three small pieces, no new module.

1. `health.published_cap(report, *, running)` → `int | None`. Knows the heartbeat's shape,
   which is where the rest of that knowledge already lives. Takes `running` as an argument
   rather than probing the lock itself, which keeps `health` free of an import of `daemon`
   (`daemon` imports `health`, so the reverse would be a cycle).
2. `capacity.snapshot(..., enforced_cap=None)` → the snapshot's `global_cap` becomes the
   enforced cap when one was supplied, and the snapshot additionally carries
   `configured_cap` — set only when the two differ, `None` otherwise.
3. `CapacitySnapshot.cap_disagreement` → the one sentence, derived from those two fields, so
   the terminal and the web cannot word it differently or disagree about when to show it.

**Rationale**: the cap has to be *inside* the snapshot rather than substituted at the moment
of rendering, because the snapshot is what `ordering.plan` is planned against. The queue view
re-runs the planner to show each item's reason, and a planner run against the stale cap would
print "at capacity" underneath a pill reading `6/7`. Putting the cap in one place makes
"a surface cannot contradict its own wording" structural rather than a rule each renderer has
to keep.

`configured_cap` is `None` when there is nothing to report rather than always carrying the
reader's own value, so "is there a disagreement?" is one field being present and not a
comparison every consumer performs — which is how two consumers come to disagree about it.

**Alternative rejected**: a `CapReading` dataclass threaded through as a required argument to
`snapshot`. It would force every call site to state which cap it means — attractive — but it
is a third representation of two integers, and it would touch every existing caller and test
for no behavioural gain over an optional integer whose absence means exactly what it says.

## R5 — The lock decides whether there is anything to defer to; staleness does not

**Decision**: the daemon's cap is used when a daemon holds the lock and the heartbeat carries
a usable cap — **including when that heartbeat is stale**. When no daemon holds the lock, the
reader's configured cap applies.

**Rationale**: both halves are the settled reasoning of the effect-level guard, and the same
argument holds for the cap:

- A heartbeat left by a dead process is not an authority. Nothing is enforcing anything, and
  deferring to a corpse is the same class of surprise in the other direction.
- A daemon's cap, like its level, is fixed when it starts and cannot change while it runs. So
  a stale heartbeat from the process *currently holding the lock* still names that process's
  cap correctly. Staleness means a tick is running long — which is when the machine is busy,
  which is exactly when the fraction is being read.

## R6 — No second banner when nothing about the daemon can be read

**Decision**: when a daemon holds the lock but no heartbeat can be read, the reader's
configured cap is used and **no cap notice is rendered**.

**Rationale**: that state already raises a prominent banner saying the daemon's state cannot
be read at all and that actions are refused. A second banner saying the cap could not be
confirmed adds nothing the reader does not already know and competes with the account that
matters. The renderer already carries this rule for the simulated-consequences banner, in
those words: one account of a situation beats two competing ones.

## R7 — The terminal is fixed at the same time, and it is not cosmetic

**Decision**: `robot-army status` and `robot-army capacity` resolve the enforced cap the same
way the web does.

**Rationale**: the issue's reproduction is two surfaces printing different fractions seconds
apart. Fixing one leaves them free to disagree — and the terminal is wrong in the *more*
dangerous direction, because a freshly-started process reading a freshly-edited file looks
maximally trustworthy while reporting a cap that nothing is enforcing. Both surfaces already
route through `capacity.snapshot` and `_capacity_dict`, so this costs the resolution call at
each entry point and nothing else.

## R8 — The daemon does not read its own heartbeat

**Decision**: `dispatch` keeps calling `capacity.snapshot` with no `enforced_cap`, so the
daemon plans against its own configuration.

**Rationale**: the daemon *is* the authority; asking the file it wrote what it thinks would be
circular, would put a file read in the dispatch path, and would make the cap a value that
could in principle be influenced from outside the process. The value would be identical
anyway, which is the point: nothing about dispatch changes.

## R9 — One reading of the daemon per rendered page

**Decision**: the web request handler takes the health report and the lock probe once and
passes both to `effective_level`, to the cap resolution, and to `pages.chrome`.

**Rationale**: the handler already resolves the effect level once and hands it down, for the
stated reason that deriving it twice "could — across a daemon starting mid-request — answer
differently in the two halves of one page". The cap now depends on the same two facts, and
adding a third and fourth read of them would re-create the bug in a new place. `chrome` grows
two optional parameters; `effective_level` and `effect_mismatch` already take them.

## R10 — Nothing is logged, and that is not a gap

**Decision**: no audit record and no anomaly on a cap disagreement.

**Rationale** (Principle III): reporting capacity is a read. It changes no state outside the
process, launches nothing, and writes nothing. The one action in this feature that changes
outside state is the heartbeat write, which is deliberately unlogged already — the guide's
own rule is that the heartbeat file *is* the record and 17,000 records a day of "the daemon
still lives" is noise. The cap in force at startup is already written to the log by
`daemon.start`, which records `max_concurrent_sessions` in its detail, so the log alone still
answers "what cap was that daemon enforcing?" without this feature adding a line.

An anomaly was considered and rejected: an anomaly is a fault of the system needing an
operator decision, and it persists until retracted. A reader whose process holds older
configuration is a fact about that process, true only while it lives, and shown on every page
it renders. A row in `robot-army anomalies` would outlive the condition and would have to be
swept.
