# Quickstart: Trello Column Ignore List

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

How to prove this works, in the order that fails fastest. The first three sections need no board and
no credentials; the last needs a real one, and says why.

## Prerequisites

```bash
cd ~/GIT/robot-army
uv sync
uv run pytest                  # must pass before anything below means anything
```

For the live sections: a private Trello board with at least four columns — one to capture into, one
to park into, plus the configured in-progress and done columns — and `TRELLO_API_KEY` /
`TRELLO_API_TOKEN` exported. **Use a throwaway board.** These steps file real GitHub issues.

## 1. The default is inert

The property FR-002 states, and the one most worth checking first because everything else is built
on top of it.

```bash
uv run pytest tests/unit -k "ignore or ignored"
uv run robot-army doctor        # with no ignore_lists key in the config
```

**Expect**: the board section reports exactly the checks milestone 003 reported — reachable, private,
members, tag, in-progress list, done list. No `ignored list exists` row, because zero are configured.
The daemon polls, tracks and evaluates identically.

## 2. Configuration errors are caught at load

```toml
[trello]
ignore_lists = "Icebox"          # a string, not a list
```

```bash
uv run robot-army doctor
```

**Expect**: exit non-zero with `[trello] ignore_lists must be a list of strings`. Then try
`ignore_lists = ["Icebox", ""]` and expect `contains an empty column name`; then
`ignore_lists = ["Icebox", "Icebox"]` and expect it to load cleanly and behave as one entry.

## 3. A missing column is refused, by name

```toml
ignore_lists = ["Icebox", "Blocked"]     # with no "Blocked" column on the board
```

```bash
uv run robot-army doctor
```

**Expect**:

```
[ok]   ignored list exists    'Icebox' found
[FAIL] ignored list exists    'Blocked' not found — the board has: Doing, Done, Icebox, In Progress
```

`doctor` exits non-zero. Now start the daemon and confirm the blast radius is right:

```bash
uv run robot-army run
```

**Expect**: board **ingestion** refused with the failure named in the log, and GitHub polling and
dispatch of the author's own labelled issues continuing normally. That split is FR-018, and it is the
half most likely to regress unnoticed.

## 4. Park a card — the primary flow

Configure `ignore_lists = ["Icebox"]` against a board where that column exists.

1. Create a card in **Icebox**, tag it, and name one onboarded repository in its description.
2. Wait one poll interval (300s by default; `poll_seconds = 20` while testing).

**Expect**: no issue in the repository. No comment on the card. The card has not moved.
`uv run robot-army cards` lists nothing for it — it was never tracked, which is FR-006 holding
structurally. The poll record shows it:

```bash
uv run robot-army log --limit 200 | grep trello.poll | tail -1
# ... "detail": {"tagged": 1, "ignored": 1, "newly_tracked": 0}
```

3. Drag the card from **Icebox** to an ordinary column. Wait one interval.

**Expect**: the issue appears, unlabelled, with the card's title and body and a link back to the
card; the card carries the marker comment. No re-tag, no rescan, no restart — that is SC-002.

## 5. Park and un-park a card that is already tracked

The path that would be a silent trap if the exclusion were done in the wrong place.

1. Create a tagged card in an ordinary column naming **no** repository. Wait one interval.

**Expect**: `robot-army cards` shows it as `needs_info`, and a single comment on the card says what
is missing.

2. Drag it to **Icebox**. Wait one interval.

**Expect** — and check all four:
- `robot-army cards` still lists it, still `needs_info`, now reading `parked in 'Icebox'`.
- Its state is **not** `dropped`. This is the whole point: `dropped` is terminal and nothing returns
  from it.
- The web cards page does not count it among outstanding `needs_info` work.
- Exactly one `trello.parked` record — and, after several more intervals, still exactly one:

```bash
uv run robot-army log --since 1h | grep -c trello.parked     # 1, and stays 1
```

3. Drag it back out. Wait one interval.

**Expect**: one `trello.released` record; the card is re-evaluated; it is `needs_info` again and
outstanding again; and **no second clarification comment** is added, because the reason has not
changed (FR-010).

4. Edit the card to name a repository, still outside Icebox. Wait one interval.

**Expect**: the issue is created normally.

## 6. Work already in flight is immune

The largest blast radius, and the one CI covers least well.

1. Take a card through to a created issue, label the issue, and let a session start.
2. With the session running, add that card's **current** column to `ignore_lists` and restart the
   daemon. Then also try dragging the card into Icebox.

**Expect**, in both cases: the session keeps running, the issue is untouched, the mapping is intact,
`robot-army cards` still shows the card as `linked`, and when the issue closes the card still moves
to the done column. That is FR-013 and FR-014, and SC-007 is this test with *every* column ignored.

3. Set `ignore_lists = ["In Progress", "Done"]` — the lifecycle columns themselves.

**Expect**: the configuration loads without error and changes nothing observable, because the ignore
list applies only to cards with no recorded issue (FR-015).

## 7. Migration

```bash
cp ~/.local/state/robot-army/state.db /tmp/state-v5.db      # a database from before this change
uv run robot-army doctor
sqlite3 ~/.local/state/robot-army/state.db 'PRAGMA user_version'   # 6
sqlite3 ~/.local/state/robot-army/state.db \
  'SELECT card_id, state, current_list_id FROM cards'
```

**Expect**: `user_version` is 6; pre-existing rows have `current_list_id` NULL and are treated as not
parked; the first poll after the migration fills it in. Run `doctor` twice and confirm the second run
applies nothing.

## What the test suite covers, and what it cannot

The suite should establish: the two guards and the `evaluate_card` gate ordering (one test per row of
[contracts/surfaces.md](contracts/surfaces.md)'s table); config parsing and its three rejections; the
per-column existence check and its ingestion-only refusal; the park/un-park round trip preserving
state and reason; a `linked` card's immunity in both directions; duplicate board columns of the same
name both being excluded; one record per transition rather than per cycle; and the inert default.

**CI cannot establish three things**, for the reason milestone 001's note already gives — no live
credentials, no real board:

1. That a real Trello board with **two columns of the same name** behaves as FR-019b says. The
   fixture asserts it against a constructed `BoardInfo`; only a real board proves the API returns
   what the fixture assumes.
2. That the `dateLastActivity` stamp behaves across a park and un-park with no edit — specifically
   that dragging a card between columns *does* move the stamp, which determines whether step 5's
   un-parked card is re-evaluated on the first cycle or waits for an edit. If it does not move, the
   release path must force one re-evaluation, and that is the first thing to check on the live round.
3. That the ergonomics are right at all — whether one ignore list is enough, or whether the author
   reaches for a second column and finds the answer is a per-card override the spec explicitly
   declined to build.

Record what the live round finds in `docs/roadmap.md` under this milestone's *What running it taught*
heading, as 004 and 005 do.
