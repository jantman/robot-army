# Feature Specification: Parked Is Judged by the Daemon's Ignore List

**Feature Branch**: `robot-army/issue-74-the-web-cards-page-counts-parked-cards`

**Created**: 2026-09-13

**Status**: Draft

**Input**: GitHub issue #74: "The web cards page counts parked cards as awaiting clarification and
never shows that they are parked".

## Background

During the issue #1 verification round, running milestone 006's quickstart section 5, a card the
author had dragged into `Icebox` (an ignored column) read correctly in the terminal —

```
6a955ee94919d282f04780ad  Verification: card with no repository na…  needs_info  —  —  1m
    parked in 'Icebox' — no onboarded repository could be identified from this card.
```

— while the web `/cards` page, at the same moment, counted it under `awaiting clarification (2)`,
showed its reason without the `parked in 'Icebox'` prefix, and offered it a `rescan` button.

The page already contains the logic the issue asks for: it leaves parked cards out of the
awaiting-clarification group and its count, prefixes their reason, and offers rescan only in that
group. All three symptoms come from one input: the page decided the card was **not parked**. The
terminal and the web ask the same question of the same data; what differs is the ignore list each
asks it against. The web interface is a separate, long-running process that reads its configuration
once when it starts. The quickstart edits `ignore_lists` in sections 2–4 and restarts the daemon, but
never the web interface, so a web interface started before `Icebox` was configured holds an empty
ignore list and sees nothing as parked.

This is issue #30's defect again in a different field. There, the web reported the session cap from
its own stale configuration; the fix was for the daemon — the process that actually enforces the cap
— to publish it on its heartbeat, and for every other surface to report against the published value.
The daemon is likewise the process that decides which cards are parked, so the same fix applies.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The web cards page does not count a parked card as outstanding (Priority: P1)

As the operator, glancing at `/cards` from a phone, the awaiting-clarification count includes only
cards that are actually waiting on me. A card I parked is shown as parked, with the column it is in,
and offers no rescan — even if the web interface was started before I configured that column as
ignored.

**Why this priority**: This is the reported defect, on the number read at a glance to decide whether
the board needs attention.

**Independent Test**: Start the web interface with no ignore list configured; configure
`ignore_lists = ["Icebox"]` and restart only the daemon; with a `needs_info` card in `Icebox`, load
`/cards`.

**Acceptance Scenarios**:

1. **Given** a running daemon whose ignore list contains `Icebox`, a web interface whose own
   configuration has no ignore list, and two `needs_info` cards of which one is in `Icebox`, **When**
   I load `/cards`, **Then** the page reads `awaiting clarification (1)`, the `Icebox` card appears
   only in `every tracked card`, its reason begins `parked in 'Icebox' — `, and it has no rescan
   control.
2. **Given** the same, **When** I request the page's machine-readable form, **Then** its
   awaiting-clarification count is 1 and its parked count is 1.
3. **Given** a web interface and daemon that agree on the ignore list, **When** I load `/cards`,
   **Then** the page is exactly what it is today.

---

### User Story 2 - The terminal and the web agree on what is parked (Priority: P1)

As the operator, `robot-army cards` and `/cards` give the same answer for the same card at the same
moment, because both judge parkedness against the same ignore list: the running daemon's.

**Why this priority**: The issue's second complaint is that the two surfaces reported different text
for the same field. Fixing only the web would leave the terminal able to disagree the other way —
after an edit to `ignore_lists` the daemon has not yet been restarted to read.

**Independent Test**: With a running daemon whose ignore list contains `Icebox`, set the terminal's
configuration file to a different ignore list without restarting the daemon, and run
`robot-army cards`.

**Acceptance Scenarios**:

1. **Given** a running daemon whose ignore list contains `Icebox` and a configuration file that no
   longer lists it, **When** I run `robot-army cards`, **Then** a card in `Icebox` still reads
   `parked in 'Icebox'`, matching what the daemon is doing with it.
2. **Given** no daemon running, **When** I run `robot-army cards` or load `/cards`, **Then**
   parkedness is judged against that process's own configuration, as today.

---

### User Story 3 - A disagreement is visible rather than silent (Priority: P3)

As the operator, when the ignore list a listing used differs from the one in that process's own
configuration, the listing says so in one line, so I know a restart is outstanding.

**Why this priority**: Not needed for correctness, but it is how the operator would have diagnosed
the original report in seconds, and it matches how the session cap reports the same disagreement.

**Independent Test**: As User Story 1, and read the page.

**Acceptance Scenarios**:

1. **Given** the daemon's ignore list and the surface's own differ, **When** I read either listing,
   **Then** one line names the ignore list in force (the daemon's) and says this process's
   configuration differs.
2. **Given** they agree, or no daemon is running, **When** I read either listing, **Then** no such
   line appears.

### Edge Cases

- **No daemon running.** Nothing is deciding parkedness, so each surface uses its own configuration;
  deferring to a heartbeat left by a dead daemon would be the same surprise in reverse.
- **A daemon holds the lock but the heartbeat on disk is a previous daemon's** (the restart window
  issue #30 closed for the cap). The heartbeat is not believed; the surface uses its own
  configuration.
- **A stale heartbeat from the daemon that holds the lock.** Believed: a daemon's ignore list cannot
  change while it runs, exactly as its cap cannot.
- **A heartbeat from an older build with no ignore-list field**, or a field of the wrong shape.
  Not published; the surface uses its own configuration.
- **The daemon publishes an empty ignore list.** Believed: nothing is parked, because the daemon is
  parking nothing.
- **The daemon has no board configured.** It publishes no ignore list, and the surface uses its own
  configuration.
- **A card attached to a work item** (the item page's card link). Judged the same way; in practice
  such a card is `linked` and never parked.
- **A parked card reached by URL at `/card/<id>/confirm/rescan`.** Out of scope: the rescan it asks
  for already reports a parked card as ignored.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The daemon MUST publish, on each heartbeat, the ignore list it is applying — the column
  names from its loaded configuration — or no value when it has no board configured.
- **FR-002**: Every surface other than the daemon that reports whether a card is parked — the
  `cards` command, the web cards page, and the card shown on a work item — MUST judge parkedness
  against the ignore list the running daemon published, when a daemon holds the lock and the
  heartbeat was written by that daemon, and against its own configuration otherwise.
- **FR-003**: A published ignore list MUST be believed only when it is a list of non-empty strings;
  anything else is treated as not published.
- **FR-004**: The web cards page MUST NOT count a parked card among cards awaiting clarification,
  MUST NOT offer it a rescan control, and MUST show its reason prefixed `parked in '<column>'` —
  which, given FR-002, it does for a web interface started before the column was ignored.
- **FR-005**: The web page and the web request MUST take one reading of the daemon (lock and
  heartbeat) and use it for everything on the page, as the session cap already does, so the two
  halves of a page cannot answer differently across a daemon restart.
- **FR-006**: When the ignore list in force differs from the surface's own configuration, the cards
  listing on both surfaces MUST say so in one line, and its machine-readable form MUST carry both.
- **FR-007**: When a daemon and a surface agree on the ignore list, or no daemon is running, the
  output of both listings MUST be unchanged.
- **FR-008**: Unit tests MUST cover the web cards page with a parked row — its count, its reason
  prefix, and the absence of a rescan control — including the case where the web process's own
  configuration has no ignore list and the daemon's does.
- **FR-009**: The state guide MUST document the new heartbeat field, and the intake guide MUST say
  that parkedness is judged against the running daemon's ignore list.

### Key Entities

- **Heartbeat**: the daemon's liveness file. Gains the ignore list the daemon is applying, beside the
  session cap it already publishes.
- **Ignore list in force**: the column names parkedness is judged against — the running daemon's when
  it can be learned, the surface's own otherwise.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Replaying quickstart 006 section 5 without restarting the web interface, the web cards
  page's awaiting-clarification count excludes the parked card, and its reason reads
  `parked in 'Icebox'`.
- **SC-002**: For the same card at the same moment, `robot-army cards` and `/cards` show the same
  parked note.
- **SC-003**: With surfaces and daemon in agreement, every existing test of both listings passes
  unchanged.
- **SC-004**: The full test suite passes.

## Assumptions

- The web interface continues to read its configuration once at startup. Rereading the file would not
  fix the defect: the file is not the authority, the daemon is — the argument issue #30 made for the
  cap, and the same one applies here.
- Column names are compared exactly as today; publishing them changes where the list comes from, not
  how a card is matched against it.
- The rescan confirmation reached directly by URL for a parked card is left as is; the intake already
  reports a rescan of a parked card as ignored.
- No new audit action is needed: nothing new changes state outside a process, and the heartbeat is
  already written atomically.
