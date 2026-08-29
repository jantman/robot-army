# Research: Times Are Read in the Local Timezone

**Feature**: `010-local-timezone-display` | **Date**: 2026-08-29

Phase 0 decisions. No NEEDS CLARIFICATION markers were carried over from the spec — the three
candidate ambiguities were settled during specification against evidence in the codebase and
are recorded in its Assumptions section. What follows resolves the design questions the
Technical Context raised, several of them by running the code rather than by reasoning about
it.

---

## R1 — Where the conversion happens

**Decision**: at the last step before text reaches a person, and nowhere else. Concretely:
inside the f-strings that build `Result.lines` in `operations.py`, inside `pages.when()`, and
inside `html.py` where the chrome dict is turned into markup. Never inside anything that
populates `Result.data`, `View.data`, or the chrome dict itself.

**Rationale**: this project already draws the line the feature needs. `operations.py` builds
two representations of every answer — `Result.lines` for a person and `Result.data` for
`--json` — and `Result.render(as_json=...)` picks one. The web mirrors it: `View.data` is the
payload and `pages.*` builds the markup. The requirement "displayed local, recorded UTC" is
therefore not a new distinction to invent; it is the distinction the code already makes,
applied to one more thing. Putting the conversion anywhere upstream of that split would leak
local time into `--json` and violate FR-012.

**Alternatives considered**:

- *Convert at the database accessor, and carry local strings through.* Rejected outright: it
  would make every comparison, ordering, and threshold in the system read a value whose text
  no longer sorts and whose offset changes twice a year (FR-013). This is the mistake the
  whole feature is shaped to avoid.
- *Convert in the model dataclasses, as a computed property.* Rejected under Principle I: it
  puts a display concern on the state model, and every model that grows a `*_local` property
  invites a caller to compare against it.
- *Add a second field beside each stamp in the payloads.* Rejected: two representations of one
  fact in a machine-readable payload, and FR-012 asks for the payload to be unchanged, not
  augmented.

---

## R2 — How the host's timezone is obtained

**Decision**: `datetime.astimezone()` with no argument, on a UTC-aware datetime. No `zoneinfo`,
no `tzdata` package, no `TZ` parsing of our own, no configuration.

**Rationale**: `astimezone()` with no argument asks the platform for the local zone, which on
Linux means `TZ` if set and `/etc/localtime` otherwise — precisely the definition FR-007 gives.
It needs no dependency and no zone name, and it gets DST right by construction because it
resolves the offset *for the instant being converted*, not for now.

Verified on this machine across six zone settings:

| `TZ`                 | `2026-08-30T01:31:07Z` renders as | `2026-01-15T01:31:07Z` renders as |
|----------------------|-----------------------------------|-----------------------------------|
| `America/New_York`   | `2026-08-29 21:31:07 -04:00`      | `2026-01-14 20:31:07 -05:00`      |
| `Asia/Kolkata`       | `2026-08-30 07:01:07 +05:30`      | `2026-01-15 07:01:07 +05:30`      |
| `Australia/Lord_Howe`| `2026-08-30 12:01:07 +10:30`      | `2026-01-15 12:31:07 +11:00`      |
| `UTC`                | `2026-08-30 01:31:07 +00:00`      | `2026-01-15 01:31:07 +00:00`      |
| `Bogus/Nowhere`      | `2026-08-30 01:31:07 +00:00`      | `2026-01-15 01:31:07 +00:00`      |
| unset                | uses `/etc/localtime`             | uses `/etc/localtime`             |

Three of those rows are load-bearing. The half-hour and three-quarter-hour offsets prove the
format is not assuming whole hours. The `Bogus/Nowhere` row proves **FR-009 needs no code**:
an unresolvable zone is treated by the C library as UTC and no exception is raised, so the
fallback the spec requires is the behaviour we already get, and it labels itself `+00:00`
rather than pretending. The unset row proves FR-007's "honouring the standard environment
override" is satisfied without us reading the variable.

**Alternatives considered**:

- *`zoneinfo.ZoneInfo(os.environ.get("TZ") or read /etc/localtime)`.* Rejected: it
  re-implements what `astimezone()` already does, and it must then invent its own answer for
  the unresolvable case that the platform already answers correctly. More code, more failure
  modes, same output.
- *Depend on `tzdata`.* Rejected under Principle I. `tzdata` exists for platforms with no
  system zone database; the target is one Linux machine, which has one.
- *`pytz` or `python-dateutil`.* Rejected: a third-party dependency for a one-line standard
  library call, and `pytz`'s localize-based API is the historical source of exactly the DST
  bugs this feature must not introduce.

---

## R3 — The chrome dict is both a payload and a thing people read

**Decision**: `chrome["rendered_at"]` and `chrome["dispatch_paused_at"]` stay UTC. `html.py`
converts them at the point of rendering.

**Rationale**: this is the one place the clean split of [R1](#r1--where-the-conversion-happens)
does not fall out for free. `server._render` builds the JSON body as:

```python
payload = {**view.data, **chrome}
```

so every key in the chrome dict is simultaneously a machine-readable field and a value that
`html.page` and `html._chrome_bar` print for a person. Converting it in `pages.chrome_for(...)`
would put a local time into every JSON response on the interface — a direct FR-012 violation,
and an especially bad one because `rendered_at` is the field a consumer would use to tell how
fresh a response is.

Converting inside `html.py` instead keeps the invariant stated simply: **the chrome dict holds
records; `html.py` holds renderings.** It also means the HTML and the JSON derive from one
value rather than from two, so they cannot drift.

**Alternatives considered**:

- *Add `rendered_at_local` beside `rendered_at`.* Rejected: two spellings of one fact in the
  payload, and a consumer now has to know which is authoritative.
- *Strip chrome keys out of the JSON body.* Rejected: that is a breaking change to the
  machine-readable surface, made in service of a display feature — precisely the direction of
  causation FR-012 forbids.

---

## R4 — Why a new module rather than an existing one

**Decision**: `src/robot_army/timefmt.py`, a new top-level module in the package, holding
`parse_stamp()` and `local()`. `web/pages.py` deletes its private `_parse` and imports
`parse_stamp` from it.

**Rationale**: the function has callers in two layers that must not import each other. The
terminal path (`operations.py`) cannot import from `web/`, because `web/` is a front end onto
`operations.py` and this package's dependency arrow runs one way. FR-005 requires that no
surface disagree with another about the same instant, and the only enforceable way to hold that
is one implementation both layers reach.

The module addition is *net simplifying*: `pages._parse` and `operations._age_seconds` each
carry their own copy of the `%Y-%m-%dT%H:%M:%SZ` parse today. Consolidating the parse into
`timefmt` takes the number of stamp-parsing implementations in display code from two to one.
The module is two functions and one constant — there is no class, no registry, and no
configuration.

**Alternatives considered**:

- *Put it in `states.py`, beside `utcnow()`.* Tempting, since `states.py`'s docstring already
  calls the format "the only timestamp format in the database". Rejected: `states.py` is the
  state machine, and display formatting has no business there. It would also drag the state
  machine into `web/html.py`'s imports.
- *Put it in `web/pages.py` and have `operations.py` import from `web/`.* Rejected: inverts the
  package's dependency direction so that running `robot-army status` imports the HTML layer.
- *Duplicate a four-line function in both layers.* Rejected: FR-005 is a requirement that the
  two agree, and two copies is the mechanism by which they eventually will not.

---

## R5 — Do the daemon, the shell, and the web process agree?

**Decision**: yes, with no code and no configuration. Nothing needs to be done.

**Rationale**: the spec names this as an edge case worth checking — a service process can have
a different environment from a login shell, and if it did, one machine would print one instant
two ways. It does not here. `systemd/robot-army-health.service` sets no `Environment=`, and
neither unit sets `TZ`; a systemd service therefore inherits no `TZ` and `astimezone()` falls
through to `/etc/localtime`, which is the same file an interactive shell resolves to when the
user has not overridden `TZ` either. Confirmed on this machine: with `TZ` unset,
`2026-08-30T01:31:07Z` renders as `2026-08-29 21:31:07 -04:00`, matching the explicit
`America/New_York` row in [R2](#r2--how-the-hosts-timezone-is-obtained).

The residual case — the author exports `TZ` in their shell profile but not for the service —
is real, is theirs to create, and is self-diagnosing, because every displayed stamp carries its
offset. That is a second reason for FR-003 beyond the one in [R8](#r8--the-display-format).

---

## R6 — Testing under a pinned timezone

**Decision**: a `conftest.py` fixture that sets `TZ`, calls `time.tzset()`, yields, then
restores both. Tests that assert on a converted value must request it; the rest of the suite
never touches the zone.

**Rationale**: the local zone is process-global state cached by the C library, and setting
`os.environ["TZ"]` alone does nothing until `time.tzset()` is called — a test that forgets it
passes or fails depending on what ran before it, which is the worst available outcome. The
fixture makes the call once and, critically, restores the *absence* of `TZ` as distinct from
an empty `TZ`: `TZ=""` means UTC, while unset means `/etc/localtime`, so `monkeypatch.delenv`
and `monkeypatch.setenv("TZ", "")` are not interchangeable.

Two zones are worth having:

- **`America/New_York`** — observes DST, so the fold in [R8](#r8--the-display-format) is
  reachable, and its offset is not zero so a test cannot pass by accident on a UTC machine.
- **`Asia/Kolkata`** — a `+05:30` offset, which catches any code that assumed whole hours and
  any format string that assumed a four-character offset.

**Alternatives considered**:

- *Set `TZ` for the whole suite in `pytest.ini`.* Rejected: it would hide the fact that the
  existing tests pass in any zone, which is the regression evidence [R7](#r7--blast-radius-on-the-existing-suite)
  depends on.
- *Inject a zone parameter into `timefmt.local()` for testability.* Rejected under Principle I:
  a parameter with one production caller that always omits it. The environment is the input;
  the test should set the input.

---

## R7 — Blast radius on the existing suite

**Decision**: no existing test is expected to change. The suite is left alone and is treated as
the regression proof for FR-010 through FR-013.

**Rationale**: this was checked rather than assumed. A sweep of `tests/` for assertions that a
UTC stamp appears in *displayed* output found none. What the suite asserts about timestamps
falls into two groups, and both are untouched by this feature:

- **Stored and recorded values**, compared as data — `record.trust_verified_at ==
  "2026-01-01T00:00:00Z"`, `card.archived_at == "2026-08-24T00:00:00Z"`,
  `record["ts"].endswith("Z")`, `beat.ts.endswith("Z")`. These are FR-010 and FR-011 already
  written as tests, and they now guard the feature.
- **Labels around a stamp, never the stamp** — `"PAUSED since" in line`,
  `"rendered " in body`. These pass regardless of what follows the label.

The suite also runs in whatever zone the machine has, which means it is already evidence that
nothing in the system's *decisions* depends on the display zone. That property is worth
keeping, which is why [R6](#r6--testing-under-a-pinned-timezone) pins the zone per test rather
than globally.

**One consequence worth stating**: because no existing test pins the displayed text, no
existing test would catch a regression that reverted a display site to UTC. The new tests must
therefore assert per-site rather than in aggregate — the enumeration in
[contracts/time-display.md](contracts/time-display.md) is what makes "every site" checkable
instead of aspirational.

---

## R8 — The display format

**Decision**: `%Y-%m-%d %H:%M:%S %:z`, giving `2026-08-29 21:31:07 -04:00`. Fixed width at 26
characters. A space separates the date from the time instead of the stored format's `T`, and a
space precedes the offset.

**Rationale**: three properties, in priority order.

**It cannot be ambiguous.** The strongest evidence for putting the offset on *every* stamp
rather than stating the zone once per page came from running the fold:

```text
2026-11-01T05:00:00Z  ->  2026-11-01 01:00:00 -04:00
2026-11-01T06:00:00Z  ->  2026-11-01 01:00:00 -05:00
```

Two distinct instants, one hour apart, rendering to the same wall-clock text. The offset is the
*only* thing that distinguishes them. A design that stated the zone once in the page footer and
printed bare local times would display two different events identically for one hour every
autumn — a direct SC-007 failure, and a silent one. This settles FR-003 as written.

**It is visually distinct from a stored value.** The space in place of `T` means a reader can
never mistake a rendered time for a record value they could paste into a query, and a
maintainer reading the source can tell at a glance which representation a test is asserting on.

**It stays scannable.** 26 characters against the stored format's 20. The concern was the web
interface, where milestone 002's SC-013 forbids horizontal scrolling of the page at 390px — but
`html.table` already wraps every table in a `.scroll` container with `overflow-x: auto`, and
`body` sets `overflow-x: hidden`. Six extra characters are absorbed by a mechanism that already
exists. The terminal has no column pressure at all: every CLI site is a standalone f-string
line, not a `_table` column.

**The honest limit.** FR-004 asks that displayed timestamps sort lexicographically in
chronological order. That holds within a single offset and *cannot* hold across a DST boundary
for any local rendering, because local wall-clock time genuinely goes backwards there. This is
not a defect in the format; it is a property of local time. It costs nothing here because no
displayed ordering is ever derived from the displayed string — every table's row order comes
from its SQL `ORDER BY` on the stored UTC column, and `read_log` orders records before
formatting them. The requirement is met in the sense that matters (a column of times reads in
order) and its literal reading is impossible for any candidate, so no alternative was
sacrificed to it.

**Alternatives considered**:

- *`datetime.isoformat(sep=" ", timespec="seconds")`*, giving `2026-08-29 21:31:07-04:00`.
  Nearly identical and one call shorter. Rejected for the missing space before the offset: at a
  glance `21:31:07-04:00` reads as an arithmetic expression, and the offset is the token
  carrying the disambiguation the fold case depends on. Worth one format string.
- *A zone abbreviation, `2026-08-29 21:31:07 EDT`.* Friendlier and three characters shorter.
  Rejected: abbreviations are not unique (`CST` and `IST` each name several zones), they are
  variable width, and `%Z` degrades to a bare numeric offset for zones that have none — so the
  format would be inconsistent across exactly the zones it was chosen to be readable in.
- *Keeping the `T`*, giving `2026-08-29T21:31:07 -04:00`. Rejected: it reads as the stored
  format with a suffix, which is the one impression this rendering should not give.
- *Stating the zone once per surface and printing bare local times.* Rejected on the fold
  evidence above. It was the most attractive alternative — it would have made displayed stamps
  *shorter* than today's — and it is wrong for one hour a year in a way nobody would notice
  until they were reconstructing an incident.
- *Sub-second precision.* Not considered further: nothing in the system stores it. The format
  is whole seconds because the record is.

**One boundary case, noted and dismissed**: zones with sub-minute historical offsets (Local
Mean Time, pre-1900) render a seconds component in the offset and would be 29 characters rather
than 26. No timestamp this system displays predates its own installation, so the width is fixed
in practice. Nothing depends on the width regardless — it is a scannability property, not a
parsing one.
