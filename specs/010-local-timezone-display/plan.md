# Implementation Plan: Times Are Read in the Local Timezone

**Branch**: `010-local-timezone-display` | **Date**: 2026-08-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/010-local-timezone-display/spec.md`

## Summary

A rendering change and nothing else. Every timestamp in this system is stored, logged, and
transmitted as `%Y-%m-%dT%H:%M:%SZ` and that does not move. What moves is the last step
before a human reads one.

**One new function, called from two layers.** `timefmt.local(stamp)` takes a stored UTC string
and returns the same instant in the host's zone, as `2026-08-29 21:31:07 -04:00`. It is a pure
function of a string, it never raises, and it returns its input verbatim when the input is not
a stamp. `datetime.astimezone()` with no argument does the whole conversion — the host's zone
is a fact the standard library already reads from `/etc/localtime` and `TZ`, so there is no
new dependency, no `zoneinfo` lookup, and no configuration key.

**Ten call sites in the terminal.** Every one is an f-string inside `operations.py` that
interpolates a stamp into `Result.lines` — the pause line, the anomaly lines in `status` and in
`anomalies`, the Spec Kit phase, `show`'s cleaned-at line, its six-row history, its session
row, the two `pause` confirmations, and `_format_record`, which serves both `log` and
`log --follow`. None of them touch `Result.data`, which is what `--json` renders.

**Four call sites in the web.** `pages.when()` is a single funnel for seven of them, so it
changes once. `pages.py:1738` renders the audit `ts` in the log view. `html.py` carries the
other two — the "DISPATCH PAUSED since …" pill and the "rendered …" footer.

**The one thing that makes this non-trivial.** `server._render` builds the JSON body as
`{**view.data, **chrome}`. The chrome dict carries `rendered_at` and `dispatch_paused_at`, so
those two values are *simultaneously* a machine-readable field and a thing a person reads.
They stay UTC in the dict and are converted by `html.py` at the moment of rendering — the
conversion belongs to the HTML, not to the payload. That is the rule the whole feature turns
on and the reason [R3](research.md) exists.

Nothing else changes. No schema change, no migration, no new dependency, no configuration key,
no change to any comparison, ordering, age, threshold, or dispatch decision. Existing tests are
expected to pass untouched: a sweep of the suite found no assertion anywhere that a UTC stamp
appears in rendered output ([R7](research.md)).

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: none added. `datetime.astimezone()` from the standard library is the
entire conversion. `httpx` remains the sole runtime dependency and is untouched. No `zoneinfo`
import and no `tzdata` package — the host zone is read by the C library, which is correct here
precisely because the target is one Linux machine.

**Storage**: the existing SQLite database, read-only for this feature. No migration, no schema
change, no new column, no new accessor, no rewrite of any stored value.

**Testing**: pytest. The timezone a test runs under is process-global state read through
`time.tzset()`, so tests that assert on a converted value must set `TZ`, call `time.tzset()`,
and restore both — a fixture, not a per-test incantation ([R6](research.md)). Two zones are
worth pinning: one with a fixed offset and one that observes DST, so a transition can be
tested from both sides. The existing suite is regression coverage for "the record did not
move" and is expected to pass unmodified.

**Target Platform**: the same single Linux machine. The daemon under systemd, an interactive
shell, and the web process all read the same `/etc/localtime`, so all three agree without
being told to ([R5](research.md)).

**Project Type**: single Python package (`src/robot_army/`) with a CLI and a web front end.
This feature adds one module and edits two CLI-facing files and two web files.

**Performance Goals**: unchanged. One `astimezone()` call per displayed stamp — microseconds,
against views that render tens to hundreds of rows. `log` is the largest consumer at one call
per record; the page size is already bounded.

**Constraints**: `Result.data` (the `--json` payload) and the JSON body assembled from
`view.data` and `chrome` MUST stay UTC (FR-012). Audit files and their UTC-day names MUST stay
byte-identical (FR-011). No comparison, ordering, age, staleness threshold, backoff window, or
capacity decision may read a converted value (FR-013). A stamp that does not parse must reach
the screen unchanged rather than raising (FR-015).

**Scale/Scope**: one new module of roughly 40 lines. Ten one-line substitutions in
`operations.py`, one changed function in `web/pages.py` plus one line, two lines in
`web/html.py`, and two documentation paragraphs. One new test file, plus a shared fixture in
`tests/conftest.py`.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. Simplicity First (YAGNI & KISS) — **PASS**

- **No new dependency.** `astimezone()` with no argument is the standard library's answer to
  exactly this question. `zoneinfo`, `tzdata`, `pytz`, and `dateutil` were all considered and
  all rejected as machinery for a problem this project does not have ([R2](research.md)).
- **No configuration key.** FR-008 forbids one, and the reasoning is this principle's: a
  display-timezone setting has exactly one correct value on a single-user machine, which is
  the value the operating system already holds.
- **One new module is the fewest moving parts, not the most.** `timefmt.py` has callers in two
  layers that must not import each other — `operations.py` (terminal) and `web/pages.py` plus
  `web/html.py` (web). FR-005 requires that every surface agree, and a rule with one
  implementation is the only way to hold that. The alternatives — duplicating the function in
  both layers, or hanging it off `states.py` where the state machine lives — were rejected in
  [R4](research.md). The module absorbs `pages._parse`, which already exists, so the count of
  parse implementations goes from two to one rather than from one to two.
- **No abstraction.** No formatter class, no strategy, no registry. One module-level constant
  and two functions.

### II. Single-User, Local-First — **PASS**

The host's own zone is the single source, read from the operating system. No per-user
preference, no browser timezone negotiation, no `Accept-Language` or `Intl` round trip — all of
which are multi-user machinery, and two of which would additionally make the terminal and the
web interface disagree about the same instant. Conversion runs on the machine, which is what
the spec asked for and what SC-008 pins.

### III. Total Accountability — **PASS, with one enumerated gap**

- **The record does not move.** FR-010 through FR-012 are the requirements this principle
  writes: audit records keep their UTC `ts`, audit files keep their UTC-day names, and every
  machine-readable payload keeps its UTC values. The reconstruction standard is therefore
  unaffected — a log read a year from now in another zone means what it meant when written.
- **Enumerated gap:** this feature logs nothing, because it does nothing to log. Converting a
  string for display changes no state outside the process, writes no file, makes no network
  call, and invokes no model. Under Principle III's exception path this is the naming of it:
  *rendering a stored timestamp into local time is not an audited action.* Auditing it would
  produce one record per line of output and drown the log that exists to be read.
- **No silent failure.** The one failure mode — a stamp that will not parse — is surfaced
  rather than swallowed: the raw value is displayed as stored (FR-015), so a corrupt row is
  visible to the reader instead of being hidden behind a dash or an exception.

### IV. Interruption Tolerance — **PASS**

**What happens if it is killed halfway through?** Nothing. This feature writes no persistent
state, opens no file for writing, holds no lock, and starts no long-running work. A process
killed mid-render leaves a truncated line on a terminal or a truncated HTTP response, exactly
as it does today, and the next run reads the same unchanged database. There is no partial
state to observe because there is no state.

### V. Public Code, Unsupported Project — **PASS**

- No credentials, personal data, hostnames, or network addresses are introduced. A timezone
  name is machine configuration, not personal data, and it is read at runtime rather than
  committed.
- **No compatibility shim.** The displayed format changes and nothing is kept for the benefit
  of an outside consumer, because there is no outside consumer and this principle forbids
  maintaining one. Anything parsing this project's *output* was already wrong to; the
  machine-readable surfaces it should have been parsing are unchanged.
- Documentation is updated for the author's future self (FR-018), so the next reader finds the
  store-UTC/display-local split written down rather than inferring it — and does not "fix" the
  half of it that looks like an oversight.

### Operating Constraints — **PASS**

Every capability stays terminal-reachable; the terminal is in fact the primary surface this
feature fixes. Exit codes are untouched. No graphical interface becomes a prerequisite for
anything. Persistent data stays SQLite and JSONL in a human-inspectable format — and stays
*more* inspectable for keeping one unambiguous timestamp format on disk.

### Development Workflow — **PASS**

Unit tests ship with the change. The conversion function parses external-ish input (a string
from the database) and therefore carries failure-path tests as well as success-path ones:
unparseable, empty, absent, and the two DST edges.

## Project Structure

### Documentation (this feature)

```text
specs/010-local-timezone-display/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── time-display.md  # Phase 1 output — the display contract
├── checklists/
│   └── requirements.md  # From /speckit-specify
├── spec.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/robot_army/
├── timefmt.py           # NEW — parse_stamp() and local(); the one conversion in the system
├── operations.py        # 10 display lines converted; Result.data untouched
└── web/
    ├── pages.py         # when() converts; the audit ts in the log view converts;
    │                    # _parse is deleted in favour of timefmt.parse_stamp
    └── html.py          # the paused-since pill and the rendered-at footer convert

tests/
├── conftest.py          # NEW fixture: pin TZ for a test and restore it
├── unit/
│   ├── test_timefmt.py  # NEW — the conversion, its edges, and its refusals
│   ├── test_cli_local_time.py   # NEW — every terminal display site, under a non-UTC zone
│   └── test_web_local_time.py   # NEW — every web display site, and the JSON/HTML split
└── (existing suite unchanged — it is the regression proof that the record did not move)

docs/
└── logging.md           # the store-UTC / display-local split, stated where `ts` is defined
```

**Structure Decision**: The existing single-package layout is kept exactly. The one addition is
`src/robot_army/timefmt.py` at the top level of the package rather than inside `web/`, because
the terminal must reach it and `operations.py` importing from `web/` would invert the
dependency this package deliberately runs one way — `web/` is a front end onto `operations.py`,
never the reverse.

## Complexity Tracking

> No Constitution Check violations. The one addition that could be read as complexity — a new
> module — is argued under Principle I above rather than deferred here, and it reduces the
> number of stamp-parsing implementations in the codebase from two to one.

---

## Constitution Re-Check (post-Phase 1)

*Required by the Governance section: the gate is applied again once the design exists.*

**All five principles still PASS.** The design produced during Phase 0 and Phase 1 did not add
anything the pre-design check did not anticipate, and two findings made it simpler than
planned:

- **[R2](research.md) removed code rather than adding it.** FR-009's fallback — render UTC and
  say so when the host zone is unresolvable — needs no branch, no `try`, and no default: the C
  library already resolves an unknown zone to UTC and `astimezone()` labels it `+00:00`. A
  requirement satisfied by doing nothing is the best outcome Principle I can produce.
- **[R5](research.md) closed an edge case with no code at all.** Neither systemd unit sets `TZ`,
  so the daemon, an interactive shell, and the web process already resolve the same
  `/etc/localtime`. The spec listed this as a case to handle; it turns out to be a case to
  verify and document.

**The one thing the design added beyond the pre-check** is the enumeration in
[contracts/time-display.md](contracts/time-display.md) — sixteen display sites named
individually. That is not complexity in the shipped system; it is a list, and it exists because
[R7](research.md) established that no existing test would catch a site left behind. Without it,
FR-005 and SC-001 would be claims rather than checks.

**Principle III's enumerated gap is unchanged and remains the only one**: rendering a stored
timestamp into local time is not an audited action, because it changes no state outside the
process. Phase 1 introduced no other unlogged action, because it introduced no action at all.

**Principle IV's question is answered the same way after design as before it**: there is no
persistent write, no lock, and no long-running work in this feature, so being killed halfway
through leaves nothing partial to observe.
