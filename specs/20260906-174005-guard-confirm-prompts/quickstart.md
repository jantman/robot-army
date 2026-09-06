# Quickstart: proving a given-up prompt is recorded, not crashed

## Prerequisites

```bash
uv sync
```

A configured `robot-army` with at least one work item that has a worktree, one running
session, and some simulated rows. `robot-army status` will say.

## The whole suite

```bash
uv run pytest
```

Must pass before this feature is complete. The tests that matter here cover, for each of
the four commands and both causes: that nothing happened, the exit code, the printed line,
and the audit record.

## By hand, at a terminal

Each command below is run twice: once with input closed, and once by pressing Ctrl-C at the
prompt.

```bash
# 1. purge-simulated — the one the issue reproduced
robot-army purge-simulated < /dev/null ; echo "  → exit=$?"
# expect: the prompt, then
#   no answer available: input ended before the prompt was answered
#   → exit=4
# and NOT a traceback

robot-army purge-simulated        # then press Ctrl-C
# expect: interrupted, exit=1

# 2. worktree remove --force — the destructive one
robot-army worktree remove <item-id> --force < /dev/null ; echo "  → exit=$?"
# expect: exit=4, the worktree still on disk, the branch still there

# 3. cancel — the other destructive one
robot-army cancel <item-id> < /dev/null ; echo "  → exit=$?"
# expect: exit=4, `robot-army show <item-id>` unchanged, the session still running

# 4. onboard — the one that already worked; it must still behave identically
robot-army onboard <owner/repo> --reapprove < /dev/null ; echo "  → exit=$?"
# expect: exit=4, the same line as before this change
```

Confirm nothing moved:

```bash
robot-army status          # same counts
ls <worktree-path>         # still there
```

## Reading the record back

The point of the feature. From the log alone, without re-running anything:

```bash
# every prompt anyone gave up on, and why
robot-army log --since 1h
jq -r 'select(.detail.cause == "interrupted_at_prompt" or .detail.cause == "no_answer_available")
       | "\(.ts) \(.action) \(.entity_id) \(.detail.cause)"' \
  ~/.local/state/robot-army/logs/audit-*.jsonl
```

Expect one line per abandonment above. For the force-removal, the intent record that
precedes it names the path and carries `force: true`:

```bash
jq -r 'select(.action == "worktree.remove")
       | "\(.ts) \(.kind) \(.outcome) \(.target) force=\(.detail.force) \(.detail.cause // "")"' \
  ~/.local/state/robot-army/logs/audit-*.jsonl
```

Expect the `intent`/`pending` line naming the worktree, then an `outcome`/`error` line with
`abandoned: true` and the cause — and no removal in between.

## Machine-readable runs still parse

```bash
robot-army purge-simulated --json < /dev/null > /tmp/doc.json ; echo "  → exit=$?"
jq . /tmp/doc.json
```

Expect exit 4, the prompt and the explanation on stderr, and `/tmp/doc.json` parsing
cleanly. (Before this change the prompt landed on stdout and it did not.)

## Answered prompts are untouched

```bash
echo n | robot-army purge-simulated ; echo "  → exit=$?"     # aborted, exit=1
echo y | robot-army purge-simulated ; echo "  → exit=$?"     # purged, exit=0
echo wrong | robot-army worktree remove <item-id> --force    # aborted, exit=1, nothing removed
```

The audit-log page's own description of these commands is in
[docs/guide/audit-log.md](../../docs/guide/audit-log.md); the operator-facing description is
in [docs/guide/operating.md](../../docs/guide/operating.md). Both are updated by this
feature.
