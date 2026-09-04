# Quickstart: proving the retry re-check by hand

The automated suite covers all of this. What follows is how to convince yourself, at a
terminal, that the control is real — which for a security fix is worth doing once.

## Prerequisites

```bash
cd /path/to/robot-army
uv sync
```

A GitHub token in the environment the daemon uses, an onboarded repository, and an issue in
it written by somebody other than the configured `github.author`. On a repository with no
outside contributors, a second account or a colleague's issue will do; failing that,
temporarily setting `github.author` to a login that is not yours produces the same verdict
from the other direction and is the cheaper rehearsal.

## The suite

```bash
uv run pytest -q
uv run ruff check src/ tests/
```

Both must pass before this is finished (Development Workflow). These are the two
commands CI runs; the project carries no type checker.

The tests that speak directly to the requirements:

```bash
uv run pytest tests/unit/test_operations_retry.py -v      # checks 5 and 6, every refusal
uv run pytest tests/unit/test_migrations.py -k 011 -v     # the column, and no backfill
uv run pytest tests/integration/test_dispatch.py -k author -v  # the dispatch backstop
uv run pytest tests/unit/test_web_actions.py -k retry -v  # the web path, and its text
```

## Scenario 1 — the bypass is closed (US1, SC-001)

1. Label the outside-authored issue with the dispatch label and let one poll run:

   ```bash
   uv run robot-army poll --repo <owner>/<repo>
   uv run robot-army status --state failed
   ```

   The item is listed, its reason naming the author condition. This is today's behaviour
   and it is correct. It appears in the web interface under `/queue`'s **blocked** section,
   which is where the button that used to bypass this lives.

2. Retry it from the command line:

   ```bash
   uv run robot-army retry <id>; echo "exit $?"
   ```

   **Expected:** exit `3`, and two lines — `refusing to retry item <id>: the issue is not
   eligible.` followed by the author reason verbatim. Before this change the item moved to
   `ready`.

3. Confirm nothing moved:

   ```bash
   uv run robot-army show <id>
   ```

   **Expected:** still `failed`, `blocked_reason` naming the author.

4. Do the same through the web interface — open `/queue`, press **retry** on that row,
   confirm.

   **Expected:** a `409` refusal page carrying the same sentence. The two front ends call
   the same function, which is what SC-001 turns on.

## Scenario 2 — content is refreshed, not replayed (US2, SC-004)

1. Edit the issue's title and body at GitHub.
2. Retry the item — refused again, as it must be.
3. Look at what the item now holds:

   ```bash
   uv run robot-army show <id>
   ```

   **Expected:** the *new* title and body. FR-009 refreshes on the refused path too, so the
   queue describes the issue as it currently is.

4. Repeat with an issue you wrote yourself, so the retry succeeds, and confirm the item
   reaches `ready` carrying the edited text.

## Scenario 3 — a read that fails refuses (SC-005)

With the daemon's token unset or an invalid `api_base`:

```bash
uv run robot-army retry <id>; echo "exit $?"
```

**Expected:** exit `1` and `could not read <owner>/<repo>#<n>: …`. The item stays `failed`.
Nothing falls back to the stored copy — that fallback is the original defect wearing a
network hiccup as a trigger ([R3](research.md)).

Against a deleted or invisible issue the message is instead `does not exist, or this token
cannot see it`, and the exit code is the same.

## Scenario 4 — the dispatch backstop (US4)

Drive an item into the queue with a foreign author recorded, bypassing retry entirely:

```bash
sqlite3 ~/.local/state/robot-army/state.db \
  "UPDATE work_items SET author = 'mallory', state = 'ready' WHERE id = <id>"
uv run robot-army run --once
```

**Expected:** the item is refused into `failed` with a reason naming the author, and no
worktree, branch or session is created — confirm with `git worktree list` in the clone.

Then the pre-migration shape:

```bash
sqlite3 ~/.local/state/robot-army/state.db \
  "UPDATE work_items SET author = NULL, state = 'ready' WHERE id = <id>"
uv run robot-army run --once
```

**Expected:** refused, with a reason saying the author was never recorded and naming
`retry` as the way to re-read and re-verify it (FR-015). Running `robot-army retry <id>`
then re-reads the issue and writes the column for the first time.

## Scenario 5 — the log tells the whole story (SC-006)

```bash
uv run robot-army log --item <id> --since 1h
```

**Expected:** one `retry.evaluate` per attempted retry carrying `eligible`, `reason`,
`author` and `refreshed`; one `retry.blocked` for a retry refused before the read; one
`dispatch.author` for each refused dispatch carrying both `recorded_author` and
`configured_author`. Before this change the refusals wrote nothing at all.

## Scenario 6 — the interface no longer lies (US3, SC-003)

```bash
uv run robot-army retry --help
```

**Expected:** the help text says the issue is re-read from GitHub and its eligibility
re-checked, author included. The web confirmation on `/queue`'s retry control says the same
sentence — [contracts/retry.md](contracts/retry.md) holds the exact wording, and the point
of the pair is that a maintainer can tell a fixed build from a broken one by reading it.

## Migration

```bash
sqlite3 ~/.local/state/robot-army/state.db 'PRAGMA user_version'   # 11 after first start
sqlite3 ~/.local/state/robot-army/state.db \
  'SELECT id, state, author FROM work_items ORDER BY id DESC LIMIT 10'
```

**Expected:** rows discovered before the upgrade show `author` as `NULL` — there is no
backfill, and that `NULL` is meaningful rather than missing ([data-model.md](data-model.md)).
Rows discovered after it carry the login GitHub reported.
