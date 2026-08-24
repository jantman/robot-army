# Contract: Terminal and Web Surfaces

Every capability this milestone adds is reachable from the terminal, and the web interface is not a
prerequisite for any of it (FR-049, and the constitution's Operating Constraints). Exit codes are the
existing table: `0` success, `1` operation failed, `2` usage error, `3` precondition not met, `4`
check failed.

## New verbs

### `robot-army cards [--state STATE] [--include-simulated] [--json]`

Lists tracked cards: card id and title, state, resolved repository and issue where one exists, the
reason when the state is `needs_info` or a creation is failing, and how long it has been in that
state. Read-only; a member of `READ_COMMANDS`.

Exits `0` even when the list is empty — an empty board is not a failure. Exits `3` when `[trello]` is
not configured, with a message saying so rather than printing an empty table, because an empty table
would misrepresent "not configured" as "nothing to do".

### `robot-army rescan <card-id> [--all-needs-info]`

Forces immediate re-evaluation of a card awaiting clarification (FR-024). Implemented as a forced job
request through the existing `control.py` marker mechanism, exactly as `poll` and `reconcile` already
are — the daemon drains the marker on its next tick and re-evaluates.

Exits `1` if the card is not tracked, `3` if no daemon is running to service the request, `0`
otherwise. A card that is not in `needs_info` is a usage error (`2`): rescanning a linked card is
meaningless and silently doing nothing would be worse than refusing.

### `robot-army show <item>` — extended

Where a work item's issue came from a card, the card's URL is displayed alongside the issue URL
(FR-048). Derived by join; no new column (R16).

### `robot-army doctor` — extended

Performs the five startup board checks from [`config.md`](config.md) and reports each individually,
so the board can be verified without starting the daemon. Exits `4` if any fails, consistent with its
existing contract.

## New routes

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/cards` | Card listing, mirroring `robot-army cards`. `.json` suffix supported as on every other view |
| `POST` | `/card/{id}/rescan` | Force re-evaluation. Confirm-then-post, like every other mutating route |

`/cards` joins the existing view chrome: effect level, heartbeat age, pause state, and the
unacknowledged-anomaly count appear on it as on every other page, and simulated rows are excluded by
default and visibly marked when included.

Both routes go through `operations.*` rather than reimplementing anything, which is milestone 002's
FR-047 rule and the reason the two front ends cannot drift.

## What is deliberately not offered

- **No control to create an issue from a card on demand.** Creation is automatic for a resolvable
  card and refused for an unresolvable one; a button that overrode the resolution check would be a
  way to file an issue in a repository the author did not name, which is the failure R8 exists to
  prevent.
- **No control to edit a card.** The board is the author's surface. The system comments on it and
  moves it within its own lifecycle, and nothing else.
- **No control to unlink a card from its issue.** Deleting a mapping is how a duplicate issue gets
  created (data-model.md). If this is ever genuinely needed it will be a deliberate terminal-only
  operation with its own justification, not a button.
- **No board onboarding flow.** One board, named in configuration, verified at startup.
