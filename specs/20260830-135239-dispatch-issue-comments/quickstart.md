# Quickstart: verifying the issue comments

Prerequisites: `uv sync`, and a checkout of this branch. Steps 1–3 need nothing else. Step 4
needs a live GitHub token and is the only step that writes to a real issue.

## 1. The suite

```bash
uv run pytest
```

The cases this feature adds:

```bash
uv run pytest tests/unit/test_issue_comments.py -v
uv run pytest tests/integration/test_dispatch.py -k "comment or reassign or supersede" -v
```

`tests/unit/test_issue_comments.py` covers the body rules with no git and no worktree: the three
variants, the predecessor's three cases, and the unknown host. The integration cases cover the
wiring — that a restart finds its predecessor, that a resume names the session it restored, and
that a comment failure leaves a confirmed session alone.

## 2. Rehearse a dispatch without touching GitHub

```bash
uv run robot-army run --effect-level local --once
```

Nothing is posted. The body that *would* have been posted is in the log, in full:

```bash
jq -r 'select(.action == "github.comment" and .simulated == true) | .detail.body' \
  ~/.local/state/robot-army/logs/audit-$(date -u +%F).jsonl
```

Expect a `Host:` line naming this machine, a `Session:` line naming `ra-<repo>-<number>`, a
`Session id:` line, and the branch and worktree. This is the whole of
[contracts/issue-comment.md](./contracts/issue-comment.md) §1, verifiable with no network and no
consequences.

## 3. Check the log agrees with the comment

```bash
jq -r 'select(.action == "dispatch.confirmed")
       | [.detail.host, .detail.session_name, .detail.session_id, .detail.attempt] | @tsv' \
  ~/.local/state/robot-army/logs/audit-*.jsonl
```

Every value here must appear verbatim in the body from step 2. That is FR-002, checked rather
than assumed.

## 4. The real thing, once

With `effect_level = "live"`, label an issue and let it dispatch. Then:

```bash
uv run robot-army show <id>          # the session id the comment should carry
gh issue view <n> --comments | tail -20
```

Then exercise the reassignment path on the same item:

```bash
uv run robot-army cancel <id>
uv run robot-army resume <id>        # expect a "reassigned … (attempt 2)" comment with Continues:
uv run robot-army cancel <id>
uv run robot-army restart <id>       # expect attempt 3, with Supersedes: and "without that session's context"
```

The issue now holds three comments in order, each naming its own host and session id, none of
them edited (FR-004). Reading them top to bottom is SC-003.

## 5. The failure variant

Break a precondition deliberately — the cheapest is an unonboarded or untrusted clone:

```bash
uv run robot-army restart <id>       # against a repo whose trust was revoked
gh issue view <n> --comments | tail -5
```

Expect "could not start a session", a `Host:` line, and the reason fenced beneath it. The item is
`failed` with the same reason:

```bash
uv run robot-army show <id>
```

## What "done" looks like

- `uv run pytest` passes.
- Step 2 prints a full body containing host, session name and session id, with nothing posted.
- Step 3's four values appear verbatim in step 2's body.
- Step 4 leaves three ordered comments; the second says what it continues, the third says what it
  supersedes and that it starts fresh.
- Step 5's comment names the host and the reason, and no comment anywhere claims a session that
  was never confirmed.
