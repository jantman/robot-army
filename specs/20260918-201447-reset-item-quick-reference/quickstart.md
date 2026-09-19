# Quickstart: validating reset and the quick reference

**Feature**: [spec.md](spec.md) · **Date**: 2026-09-18

## Prerequisites

```bash
uv sync
```

## The whole suite, which must pass

```bash
uv run pytest
```

## The new command exists and describes itself

```bash
uv run robot-army reset --help
```

Expect the description the web confirmation page also shows: the checkout and branch are
deleted, uncommitted work is refused without `--force`, the issue is re-read and re-checked
with the author included, and the item returns to the queue.

## Scenario 1 — start an interrupted item over

With a dry-run item in `interrupted` that has a checkout on disk:

```bash
uv run robot-army show <id>                 # note worktree_path, branch, and the stored title
uv run robot-army reset <id>                # answer y
uv run robot-army show <id>                 # state ready, no worktree, title from the live read
uv run robot-army log --item <id> --since 10m
```

Expect in the log, in order: a `reset` intent, `reset.evaluate` with `eligible: true` and the
four refreshed columns named, a `worktree.remove` intent/outcome pair, a `state.work_item`
record `interrupted → ready`, and the `reset` outcome.

## Scenario 2 — it refuses without destroying anything

```bash
# an issue that no longer passes the poller — closed, unlabelled, or authored by someone else
uv run robot-army reset <id>
echo $?                                     # non-zero
uv run robot-army show <id>                 # unchanged state, worktree still there
```

The stored title and body *will* have been refreshed, because the read happened. That is
`retry`'s existing behaviour and is deliberate.

```bash
# a checkout with uncommitted work
uv run robot-army reset <id>                # refused, naming --force as the override
# a session row still open
uv run robot-army reset <id>                # refused, naming robot-army cancel
# a done or abandoned item
uv run robot-army reset <id>                # refused, naming the state
```

## Scenario 3 — from the browser

```bash
uv run robot-army serve
```

- `/item/<id>` for an `interrupted` item offers a **reset** control, rendered as destructive.
- Following it reaches `/item/<id>/confirm/reset`, which names the item, says what will be
  destroyed, and links back to the item.
- Submitting performs it and the item page shows `ready`.
- `/item/<id>` for an `active`, `done` or `abandoned` item offers no reset control, and
  `curl -X POST` to `/item/<id>/reset` is refused `409`.
- An item with uncommitted work is refused on the page, naming the terminal command.

## Scenario 4 — the operating page answers the four questions

Open `docs/guide/operating.md` and answer, from that page alone:

1. What can I do from `interrupted`?
2. What does `abandon` refuse?
3. Can I start an item over from my phone?
4. What do I type when the disk is full?

Then:

```bash
uv run pytest tests/unit/test_operating_reference.py -v
```

which fails if any state or any subcommand is missing from its table, or named there without
existing.
