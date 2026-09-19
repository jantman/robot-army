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

**Where the logic lives:** both workflows are thin callers of [jantman/github-actions-
workflows](https://github.com/jantman/github-actions-workflows), pinned at `@v1`. This file
keeps only what a composite action cannot reach, because by the time one runs the runner is up
and the token is minted: the triggers, `concurrency`, `permissions`, the fork guard, the
trigger guard, and `actions/checkout`. Change the behaviour there, not here — and note that a
change *there* is testable on its own PR, whereas a change to these files is not.

**What a run posts:** the review deliberately does *not* pass `--comment` to the review plugin.
In that mode the plugin posts each finding as a standalone inline comment and gives the run no
top-level body to hang a summary or a cost figure off. Instead the agent writes its findings to
a JSON file and a later step turns them into exactly **one** GitHub review (`POST
/pulls/N/reviews`): the findings as batched inline comments, and the summary plus the run's
cost footer in the body. Every run posts one review, including a run that decided to skip. The
review is authored by `github-actions[bot]`, not `claude[bot]` — the action revokes its own App
token before that step runs — so the body opens with an explicit attribution line and carries a
hidden `<!-- claude-code-review -->` marker that later runs use to find it.

**A denied tool call now fails the check.** Both workflows parse the run's result block and put
duration, turn count, cost and `permission_denials_count` in the job summary; the review also
puts them in its footer. A non-zero denial count, or `is_error`, fails the job. A denied
`Skill` or `Task` is exactly how these runs used to finish green having done nothing.
Relatedly, if the agent ends its turn without writing the findings file, the run posts its raw
final message with a warning banner and fails — it does not pass quietly.

**Who can trigger the mention workflow:** only comment/issue/review authors whose
`author_association` is `OWNER`, `MEMBER` or `COLLABORATOR`. This repository is public and that
job has `contents: write` and blanket `Bash`, so without the check any passer-by who types the
mention would get an agent run billed to the maintainer's account. The field has to be read
per-event: on `issue_comment`, `github.event.issue.author_association` is the issue's *opener*,
not the commenter.

**Re-review on new commits:** the upstream `/code-review` plugin stops without posting if
Claude has already commented on the PR, which would make every push after the first review a
silent no-op. `claude-pr-review.yml` overrides that in its `prompt` and scopes re-reviews to
the commits since Claude's last review.

### Setup

Both need a `CLAUDE_CODE_OAUTH_TOKEN` repository secret and the Claude GitHub App installed
on the repo:

```bash
claude            # then: /install-github-app
```

Without the secret the action fails immediately on every PR.
