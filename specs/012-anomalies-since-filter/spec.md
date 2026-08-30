# Feature Specification: A `--since` Window on `anomalies`

**Feature Branch**: `robot-army/issue-24-verification-round-add-a-since-filter`

**Created**: 2026-08-30

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#24 — "`robot-army log` takes `--since`; `anomalies` does not, so reading 'what went wrong in the last hour' means eyeballing timestamps. Add `--since` to `anomalies` with the same duration parsing `log` already uses."

## User Scenarios & Testing *(mandatory)*

<!--
  One story, because this is one filter on one command. It is split from the boundary story
  below only because the second states what must NOT change: the anomaly list is a safety
  surface, and a filter that quietly hides outstanding anomalies is worse than no filter.
-->

### User Story 1 - Reading only what went wrong recently (Priority: P1)

Something misbehaved in the last hour and the maintainer wants to know what. `robot-army
anomalies` answers a wider question than that: it prints every unacknowledged anomaly the
system holds, oldest detections included, which on a machine that has been running for weeks
means scrolling past conditions already understood and long since triaged in the maintainer's
head, reading each detection timestamp to work out whether it belongs to the incident in front
of them.

`robot-army log` already answers the narrower question — `log --since 1h` — and the maintainer
reaches for the same shape on `anomalies` because the two commands are read side by side
during exactly this kind of investigation. It is not there. After this change it is, spelled
and parsed identically, so `anomalies --since 1h` shows the anomalies detected inside that
window and nothing older.

**Why this priority**: It is the whole feature. Delivered alone it removes the timestamp
arithmetic that motivated the request.

**Independent Test**: Against a database holding anomalies with known detection times spread
across several days, run `anomalies --since <duration>` for a range of durations and confirm
the printed set is exactly those detected within the window, and that the same set appears in
the machine-readable output.

**Acceptance Scenarios**:

1. **Given** three unacknowledged anomalies detected 10 minutes, 3 hours, and 2 days ago,
   **When** the maintainer runs `anomalies --since 1h`, **Then** only the 10-minute-old
   anomaly is listed.
2. **Given** the same three anomalies, **When** the maintainer runs `anomalies` with no
   `--since`, **Then** all three are listed, exactly as before this change.
3. **Given** anomalies exist but none were detected inside the requested window, **When** the
   maintainer runs `anomalies --since 5m`, **Then** the command reports that none were
   detected in that window — wording that does not claim there are no outstanding anomalies —
   and exits successfully.
4. **Given** the maintainer runs `anomalies --since 1h --all`, **Then** acknowledged and
   unacknowledged anomalies detected inside the window are listed, and nothing older.
5. **Given** the maintainer runs `anomalies --since 1h --json`, **Then** the machine-readable
   payload contains exactly the anomalies the human-readable output listed.
6. **Given** a duration the parser does not accept — `2 weeks`, `1.5h`, `-5m`,
   `10 fortnights`, `abc` — **When** the maintainer runs `anomalies --since <that>`, **Then**
   the command prints the same explanatory rejection `log --since` would print for that value
   and exits with the usage-error status, having listed nothing.
8. **Given** an empty `--since` value, **When** the maintainer runs `anomalies --since ""`,
   **Then** it behaves exactly as `log --since ""` does — as though no window were given —
   because FR-002's requirement is sameness, and inventing a difference at this edge would
   make the two commands disagree about their shared vocabulary.
7. **Given** the maintainer runs `anomalies --acknowledge <id> --since 1h`, **Then** the
   acknowledgement is applied and recorded as it is today, and the listing that follows is
   filtered to the window.

---

### User Story 2 - Trusting the unfiltered view (Priority: P1)

The anomaly list is how this system says "something happened that I could not resolve". A
filter added to it must not become a way to miss one. The maintainer who types `robot-army
anomalies` with no arguments — the reflex reading, the one in the documentation, the one the
web UI's anomaly count links to — must see precisely what they see today.

**Why this priority**: Same priority as Story 1 because it is the condition on Story 1 being
safe rather than a separate increment. A `--since` that changed the default view would trade a
small convenience for a class of silent omission the constitution's accountability principle
exists to prevent.

**Independent Test**: Run every existing `anomalies` invocation — bare, `--all`,
`--acknowledge`, `--json`, `--include-simulated` — against a fixed database before and after
the change and confirm byte-identical output.

**Acceptance Scenarios**:

1. **Given** any database state, **When** `anomalies` is run without `--since`, **Then** the
   output is identical to the output before this change, including the trailing list of kinds
   the system can raise.
2. **Given** any `--since` value, **When** the listing is empty because of it, **Then** the
   message distinguishes "none in this window" from "none outstanding", so a filtered empty
   result is never read as an all-clear.
3. **Given** the web UI's anomaly view and its anomaly count, **When** this change ships,
   **Then** both show the same unfiltered set they show today.

---

### Edge Cases

- **A window that predates every anomaly.** `--since 1000d` lists everything the command would
  have listed anyway. No special case, no error.
- **A window with a boundary exactly on a detection time.** An anomaly detected exactly at the
  cutoff instant is inside the window, matching the inclusive treatment `log --since` gives a
  record on its boundary.
- **A stored detection timestamp that cannot be read.** Detection times are written by this
  system in one format, so this is a corrupted-row case rather than an expected one. Such a
  row MUST NOT be silently dropped by the filter — silent omission from an anomaly listing is
  precisely what Principle III forbids.
- **`--since` combined with `--acknowledge`.** The acknowledgement is not filtered; only the
  listing printed afterwards is. Acknowledging an anomaly older than the window still works and
  still says so.
- **`--since 0s`.** A well-formed zero window. It lists anomalies detected at or after the
  present instant, which in practice is none — an empty listing, not an error.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The `anomalies` command MUST accept an optional `--since DURATION` argument.
- **FR-002**: `--since` MUST accept exactly the duration vocabulary `log --since` accepts, with
  the same meanings, and MUST reject everything it rejects with the same explanatory message.
  There MUST be one duration parser behind both commands, not two that agree by coincidence.
  Sameness is the requirement in both directions: an empty value, which `log` treats as no
  window rather than as an error, MUST be treated as no window here too.
- **FR-003**: When `--since` is supplied, the listing MUST include only anomalies whose
  detection time falls at or after the instant that far in the past, and MUST exclude the rest.
- **FR-004**: When `--since` is not supplied, the command's behaviour MUST be unchanged in
  every respect — the set listed, the order, the text, and the trailing enumeration of kinds.
- **FR-005**: `--since` MUST compose with the existing `--all` flag: `--all` selects whether
  acknowledged anomalies are eligible, `--since` narrows whatever `--all` selected.
- **FR-006**: `--since` MUST NOT change what `--acknowledge` does. The acknowledgement MUST be
  applied and audited as it is today, before and independently of any filtering.
- **FR-007**: A malformed `--since` value MUST cause the command to fail before listing
  anything, print the parser's explanation, and exit with the usage-error status the CLI
  already uses for a malformed argument — the same failure `log --since` produces.
- **FR-008**: The machine-readable (`--json`) payload MUST reflect the same filtered set as the
  human-readable output, so the two never disagree about what is in the window.
- **FR-009**: When the filter empties the listing, the message MUST state that none were
  detected in the requested window, distinguishably from the existing "no outstanding
  anomalies" message that means the list is genuinely empty.
- **FR-010**: An anomaly whose stored detection time cannot be interpreted MUST NOT be silently
  removed by the filter; it MUST remain visible or be reported as unjudgeable, never dropped
  without a trace.
- **FR-011**: The documented CLI reference MUST describe `--since` on `anomalies`, including
  the accepted duration forms.
- **FR-012**: The new filtering behaviour MUST ship with unit tests covering an inside-window
  anomaly, an outside-window anomaly, the boundary instant, a malformed duration, the absent
  `--since` default, and the interaction with `--all`.

### Key Entities

- **Anomaly**: a condition the system detected and could not resolve. Already carries a
  detection time and an acknowledgement time; this feature reads the detection time and adds
  nothing to the record.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A maintainer investigating a recent incident can list only the anomalies from the
  last hour with a single command and no manual timestamp comparison.
- **SC-002**: Every duration string accepted by the existing log filter is accepted here with
  the same meaning, and every string it rejects is rejected here with the same message —
  verified across the full set of accepted units and the known rejection cases.
- **SC-003**: The unfiltered `anomalies` output is unchanged: the existing anomaly test suite
  passes without modification to its expectations.
- **SC-004**: No anomaly disappears from a listing without the reader being able to tell why —
  a filtered-empty listing names the window, and no row is dropped unreported.
- **SC-005**: The full unit test suite passes, as the constitution's Development Workflow
  requires before the feature is complete.

## Assumptions

- **Scope is the CLI.** The issue names `robot-army anomalies`. The web anomaly view and the
  anomaly count in the web chrome are out of scope and keep showing the unfiltered set; adding
  a matching web filter would be speculative generality under Principle I, and can be a
  separate feature if the maintainer ever wants it.
- **`--since` filters on detection time**, not acknowledgement time. "What went wrong in the
  last hour" is a question about when the condition was detected. Under `--all`, an old anomaly
  acknowledged a minute ago is therefore outside a one-hour window; that is the intended
  reading.
- **The window is relative to now**, computed when the command runs. Absolute timestamps as
  `--since` values are not part of this feature, because `log --since` does not accept them
  either and FR-002 ties the two together.
- **No new anomaly data is stored and no schema changes.** The detection timestamp this filter
  reads is already recorded on every anomaly.
- **Nothing new needs auditing.** Reading the anomaly list is a read; the one state-changing
  path this command has, `--acknowledge`, already writes its audit record and is untouched by
  FR-006. The plan's Principle III answer is therefore "this adds no unlogged action", not a
  documented exception.
- **Origin.** Issue #24 is a throwaway created for a verification round and says so; it is to
  be closed and its branch deleted when that round ends. The feature it describes is
  nonetheless specified and built as written.
