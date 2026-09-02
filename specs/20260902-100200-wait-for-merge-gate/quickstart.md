# Quickstart: validating the wait-for-merge gate

Prerequisites: a checkout of this branch and `uv`. Nothing here needs a GitHub token, a
network connection, or a real repository — the gate makes no outward request, which is most of
why it is cheap to validate.

## 1. The suite

```bash
uv run pytest
uv run ruff check
```

Both must pass before the feature is complete (constitution, Development Workflow). The tests
that matter to this feature:

| File | What it proves |
|---|---|
| `tests/unit/test_config.py` | both keys parse, default off, resolve per the table in `contracts/config.md`, and a misspelling is refused with the key named |
| `tests/unit/test_repos.py` | `repos.resolve` carries the field through rather than dropping it |
| `tests/unit/test_ordering.py` | the gate holds, releases, isolates repositories, excludes `ready`, and ranks correctly against every other reason |
| `tests/unit/test_capacity_reporting.py` | `robot-army capacity` reports both limits and both sources for every onboarded repository |
| `tests/unit/test_web_views.py` | the queue view renders the new reason, on the surface that shares no rendering code with the terminal |
| `tests/unit/test_git_boundary.py` | `fast_forward`'s refusals and its one success, against real git (`requires_git`) |
| `tests/integration/test_worktree.py` | `prepare` calls it when the setting is in force and never when it is not, and a skip never fails the item |
| `tests/integration/test_dispatch_capacity.py` | the gate end to end, and a per-item hold recorded once rather than once per tick |

Two of those are not where the plan said they would be, and the reason is the same in both
cases: `tests/integration/test_dispatch_capacity.py` and `tests/integration/test_worktree.py`
already carry the fixtures — a registry, a `/proc`, two real repositories, a trust file —
that these behaviours need, and a new `tests/unit/` file would have had to reproduce all of
it to say less.

## 2. The gate, by hand

Against a scratch database and config, with no daemon running:

```bash
uv run robot-army --config /tmp/ra-quickstart/config.toml capacity   # per-repository block
uv run robot-army --config /tmp/ra-quickstart/config.toml status     # the queue, with holds
```

Config to exercise it:

```toml
[dispatch]
wait_for_merge = false          # off globally

[repos."jantman/example"]
wait_for_merge = true           # on for this one only
```

Expected, with one item in `jantman/example` in `awaiting_review` and another `ready`:

- `status` shows the ready item held, reason `awaiting_merge`, detail reading
  `repository <key>: #<n> is awaiting_review and has not landed on main yet`.
- A ready item in any other repository is **not** held and dispatches normally.
- Moving the unfinished item to `done` or `abandoned` — `uv run robot-army abandon <id>` is
  the quickest — clears the hold on the next `status`.
- Setting `wait_for_merge = false` for the repository clears it immediately, with no restart.
- `capacity` lists every onboarded repository, whether or not it has a live session, as
  `<key>  0 of 1 sessions (default)   wait-for-merge: on (configured)`.

## 3. The fast-forward, by hand

The refusals are the interesting half, and each should leave the clone untouched and say why:

```bash
uv run robot-army log --json | grep worktree.prepare | tail -2
```

Look for `fast_forward` in the `worktree.prepare` record's detail — and for the standalone
`git.fast_forward` record, which carries the same outcome with the shas that bracket it.
Reproduce each refusal in a scratch clone:

| Set up | Expected `fast_forward` |
|---|---|
| clean clone, default branch checked out, behind the remote | `updated`, with `before` and `after` shas |
| the same clone, run again | `already_current` |
| `touch newfile` in the clone | `skipped`, "uncommitted changes" |
| `git checkout -b elsewhere` | `skipped`, naming the branch actually checked out |
| `git checkout --detach HEAD` | `skipped`, "detached HEAD or an interrupted rebase" |
| a local commit the remote lacks | `skipped`, "this would be a merge or a rebase, not a fast-forward" |
| `git remote remove origin` | `skipped`, "the clone has no remote named 'origin'" |
| a stray `.git/MERGE_HEAD` on a clean tree | `skipped`, "an operation is in progress (merge)" |

In every refusal, `git status` and `git log` in the clone must be identical before and after,
and the dispatch must still succeed.

## 4. What to check by reading

- `ordering.plan` still performs no write and no network call — the gate must be one extra
  `list_work_items` scan per call, not one per queued item (`contracts/dispatch-policy.md`).
- `awaiting_merge` is **not** in `dispatch._GLOBAL_HOLDS`. If it were, one held repository
  would stop every other one in the same pass.
- No migration was added. `src/robot_army/migrations.py` should be untouched.
- `robot-army capacity`'s `per_repo` key still holds the live-session count and nothing else.
  The new per-repository facts arrived beside it under `repos`, so nothing reading the old
  key changed meaning.
