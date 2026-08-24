# GitHub Actions Workflows

## 🧪 `tests.yml`

**Triggers:** every push; PRs to `main`
**Purpose:** run `ruff check` and `pytest`. Nothing else — see the comments at the top of
the file for why this is deliberately not a release pipeline.

## 🤖 `claude-pr-review.yml` and `claude-mention.yml`

These two look redundant and are not. **Do not delete one as a duplicate of the other.**

| | `claude-pr-review.yml` | `claude-mention.yml` |
|---|---|---|
| **Triggers** | `pull_request` | `issue_comment`, `issues`, `pull_request_review*` + `@claude` |
| **Asked for?** | No — reviews every PR unprompted | Yes — only when you write `@claude` |
| **Action mode** | agent (a `prompt` is supplied) | tag (no `prompt`) |
| **`contents:`** | `read` | `write` — it can push fixes |

The modes are why they cannot be one job. Supplying a `prompt` puts the action in agent mode
for *every* event it sees, so a single job with both triggers would answer `@claude` comments
in agent mode: no PR context, no tracking comment, and nothing posted back. They could share
one file as two guarded jobs; they are kept apart so each file's permissions and tools say
what they mean.

The 👀 reaction on an `@claude` comment comes from the Claude GitHub App acknowledging the
mention. It is **not** evidence that anything ran — the work happens in `claude-mention.yml`,
and if that workflow is missing or not yet on `main`, the eyes are all you ever get.

**Artifacts:** both upload `claude-<workflow>-logs-*`, containing `execution-output.json`
(the action's own transcript) and `sessions/` (the raw Claude Code session JSONL). The action
otherwise discards these with the runner, and the job log alone shows only `init` and
`result`. Reach for these first when a run is green but posted nothing. Note this repo is
public, so these artifacts and the `show_full_output` job logs are world-readable — they
carry full tool output, so don't put anything into CI you wouldn't publish.

**Re-review on new commits:** the upstream `/code-review` plugin stops without posting if
Claude has already commented on the PR, which would make every push after the first review a
silent no-op. `claude-pr-review.yml` overrides that in its `prompt` and scopes re-reviews to
the commits since Claude's last comment.

### Setup

Both need a `CLAUDE_CODE_OAUTH_TOKEN` repository secret and the Claude GitHub App installed
on the repo:

```bash
claude            # then: /install-github-app
```

Without the secret the action fails immediately on every PR.
