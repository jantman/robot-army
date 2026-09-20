# Quickstart — validating this feature

```bash
uv sync
uv run pytest            # must pass before the feature is complete
```

## 1. The comment, without spending a real issue

Below `live` nothing reaches GitHub and the body that *would* have been posted is recorded
in full. This is the documented way to check comment wording, and it is what
[`5-outcome.md`](../../docs/guide/5-outcome.md) already tells the reader to run:

```bash
uv run robot-army run --effect-level local --once
jq -r 'select(.action == "github.comment" and .simulated == true) | .detail.body' \
  ~/.local/state/robot-army/logs/audit-$(date -u +%F).jsonl
```

**Expect** a failed attempt's body to be exactly:

```markdown
🤖 robot-army could not start a session for this issue.

- Host: `<hostname>`
- Work item: `<id>`
```

and **no** fenced block, no path, no repository key, no command.

## 2. The reason is still on record

For the same item, all three must still hold:

```bash
uv run robot-army show <id>                    # prints `failure    : …` in full
jq -r 'select(.action == "state.work_item" and .entity_id == <id>) | .detail.reason' \
  ~/.local/state/robot-army/logs/audit-$(date -u +%F).jsonl
```

The notification body, if a channel is configured, carries the same text.

## 3. The needs-me card

```bash
uv run robot-army serve
```

Open `/interrupted`. For a failed item:

- its reason is on the card, without clicking through to the item page;
- no missing-checkout banner appears **unless** the item has a recorded checkout path whose
  directory is gone.

The JSON form must agree:

```bash
curl -s -H 'Accept: application/json' localhost:<port>/interrupted \
  | jq '.failed[] | {id, failure_reason, worktree_missing}'
```

## 4. Reproducing item 126 end to end

The scenario that produced this feature, as an integration test rather than by hand: onboard
a repository, commit a `.claude/settings.json` to its base ref afterwards, and dispatch. The
fingerprint gate refuses; the item fails with no worktree and no branch.

**Expect**: an issue comment naming only a host and an item number, and a needs-me card
naming the settings-fingerprint reason with no missing-checkout banner. That pairing is
SC-005 and is the one test that covers both halves of the feature at once.

## What to check by hand, once

The comment already posted on `DecaturMakers/kiosk-show-replacement#26` is not touched by
this change — nothing in this system edits or deletes a posted comment. Delete it manually.
