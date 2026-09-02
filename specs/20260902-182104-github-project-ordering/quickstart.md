# Quickstart: validating GitHub Project board ordering

**Feature**: [spec.md](spec.md) | Contracts: [config](contracts/config.md) ·
[project-source](contracts/project-source.md) · [dispatch-policy](contracts/dispatch-policy.md)

## Prerequisites

- A **classic** personal access token with `read:project` (or `project`). A fine-grained token
  cannot read user-owned projects at all — see [research.md](research.md) R7 — and `doctor`
  will say so rather than failing obscurely.
- A repository onboarded to robot-army, with a GitHub project linked to it, whose Status field
  offers exactly one of `Ready` / `Todo`.
- `uv` available; the suite and lint are `uv run pytest` and `uv run ruff check`.

## 0. Before anything else: does the token work?

```bash
uv run robot-army doctor
```

Expect `project: token`, `project: project`, and `project: column` rows. This is the fastest
way to separate a credential problem from an ordering problem, and it makes no changes.

## 1. The order follows the board (US1, SC-001)

```bash
uv run robot-army poll --once          # read issues and the board
uv run robot-army status               # the queue, in dispatch order
```

**Expected**: the repository's ready items appear in the same top-to-bottom order as the
dispatch column on the board. `status --json | jq '.projects'` names the project, the column,
whether each was discovered or configured, and when the board was last read.

Now drag a card from the bottom of the column to the top in the browser, then:

```bash
uv run robot-army poll --once && uv run robot-army status
```

**Expected**: the queue order follows within that one pass (SC-002). No restart.

## 2. A parked card is held, and says so (US1 AS7, FR-012)

Move one labelled issue's card from the dispatch column to `Backlog`, then poll and check the
queue.

**Expected**: that item is held with `off_column` and the sentence names `Backlog`, the
dispatch column, and both ways out. Move it back, poll once, and it is dispatchable again at
its board position (SC-009).

## 3. An issue not on the board still dispatches (FR-008)

Label an issue and do not add it to the project.

**Expected**: it appears in the queue, unheld, ordered **after** every item the board ranked.
This is the half of the split rule that is easy to get wrong in the other direction.

## 4. The global order mode is untouched (FR-002, SC-003)

With two repositories queued and `[dispatch] order = "repo-priority"`, confirm the
higher-priority repository still occupies the same queue positions it did before the board was
involved — only *which* of its items sits at each position changed.

```bash
uv run pytest tests/unit/test_ordering.py
```

## 5. Nothing changes for a repository with no board (SC-003, SC-010)

Set `project_ordering = false` for the repository and poll again.

**Expected**: no project is contacted for it, nothing is held off-column, and the order is
byte-identical to what `[dispatch] order` alone produces. The existing ordering tests are the
real assertion here and must pass unchanged.

## 6. A broken board never stalls the queue (FR-023, FR-025, SC-008)

Revoke the token's project scope, or point `project` at a number that does not exist, then
poll.

**Expected**: dispatch continues. The previously read order stays in force and the queue
reports it as stale with its age; if no board was ever read, the repository falls back to
`[dispatch] order` and nothing is held. `doctor` reports the specific failure. Nothing about
this is silent — `grep project ~/.local/state/robot-army/log/*.jsonl` shows the discovery
attempt, the failure, and the fallback.

## 7. The queue is still free to render (FR-005, SC-004)

```bash
uv run robot-army web &      # then load /queue repeatedly
```

**Expected**: no GitHub request is made while rendering. The assertion belongs in a test —
render the queue view with a reader that raises on any call — rather than in an eyeball.

## Suite

```bash
uv run pytest && uv run ruff check
```

Everything above must hold with the board unreachable, because every surface reads the stored
snapshot rather than the API. That is the property worth protecting: pull the network cable and
`status`, `capacity`, and `/queue` all still answer.
