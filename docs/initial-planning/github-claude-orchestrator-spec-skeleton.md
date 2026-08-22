# GitHub Issue → Claude Code Orchestrator

**Status:** Draft skeleton — bullets to edit, expand, and cut before writing the real spec.

---

## 1. Purpose

- A long-running local daemon that watches my GitHub repos for issues I've flagged as agent work, and starts a Claude Code session in the correct local clone.
- The GitHub issue is the durable interface: it carries the request, the audit trail, the discussion thread, and the outcome.
- Explicit non-goal: this is not a general assistant. It does not do email, calendar, or home automation.

## 2. Design Principles

- **Local execution.** Sessions run on my machine, in my clones, with my environment. Nothing about the repo leaves except what Claude Code already sends.
- **No inbound network exposure.** Poll GitHub; do not accept webhooks. No ports opened, no tunnel, no reverse proxy.
- **Human in the loop at the end, not the middle.** The agent works autonomously but never merges. I review.
- **Fail visibly.** A crashed or stalled orchestrator must be obvious, not silent.
- **Idempotent.** Restarting the daemon must not re-dispatch work already in flight or already done.
- **Boring and inspectable.** State on disk in a format I can read and hand-edit when something goes sideways.

## 3. Trigger & Eligibility

- Poll interval: _TBD (30s? 2m? adaptive backoff?)_
- An issue is eligible only if **all** of:
  - `author == me` (hard requirement — this is the security boundary)
  - carries the dispatch label (e.g. `claude`) _[or: @-mention in body? decide one, not both]_
  - repo is on the orchestrator's allowlist
  - not already dispatched (see state tracking)
- Consider a second label for mode selection: e.g. `claude:plan` vs `claude:implement` vs `claude:interactive`
- Consider re-dispatch on new comment from me on an already-processed issue (continuation flow)
- **Open question:** how do I cancel / abort a running session from GitHub? Remove label? Close issue? New comment with a keyword?

## 4. Repo → Local Path Mapping

- Explicit config file mapping `owner/repo` → local clone path. No inference, no searching the filesystem.
- Config also holds per-repo overrides: allowed tools, model, permission mode, whether interactive is allowed.
- Behavior when the mapping is missing: comment on the issue and skip. Do not guess.
- **Open question:** auto-clone repos not present locally, or refuse? (Leaning refuse — keeps the allowlist meaningful.)

## 5. Workspace Isolation

- One git worktree per issue, named from the issue number (e.g. `../worktrees/repo-issue-142`).
- Branch naming convention: _TBD (e.g. `claude/issue-142-short-slug`)_
- Worktree is created from a configurable base branch (default `main`, pulled fresh).
- Cleanup policy: when? On merge? On issue close? Manual `--prune` command? Age-based?
- **Open question:** what happens if a worktree for that issue already exists — resume, error, or recreate?

## 6. Session Modes

### 6a. Interactive (Remote Control)
- Start a session with Remote Control enabled so I can pick it up from my phone or browser.
- Post the session URL back to the issue as a comment.
- Note: Remote Control sessions are tied to the machine and the user; they end when the machine stops running the session. Plan for that.
- Best for: exploratory work, anything I want to steer.

### 6b. Headless (`claude -p`)
- Fire-and-forget. Capture `--output-format json`, extract result and `session_id`.
- Post the result summary back to the issue as a comment; store `session_id` for later `--resume`.
- Best for: triage, plan generation, small well-scoped fixes.

- **Open question:** does the label pick the mode, or does the daemon pick based on issue size/content? (Leaning: label. Explicit beats clever.)

## 7. Context Loading

- **Do NOT use `--bare`** by default — it skips CLAUDE.md, hooks, skills, plugins, and MCP server auto-discovery, which is exactly the accumulated context that makes my repos work well.
- If a reproducible/hermetic run is ever wanted, use `--bare` plus explicit `--append-system-prompt-file`, `--settings`, `--mcp-config`.
- Inject issue context into the prompt: title, body, labels, and the comment thread.
- Consider a repo-level `.claude/orchestrator.md` for dispatch-specific instructions distinct from CLAUDE.md.

## 8. Permissions & Blast Radius

- Default to `--permission-mode dontAsk` with a narrow `--allowedTools` allowlist. Never `--dangerously-skip-permissions`.
- Per-repo tool allowlists in config; start restrictive, widen deliberately.
- Remember the prefix-matching gotcha: `Bash(git diff *)` (with the space) — `Bash(git diff*)` also matches `git diff-index`.
- Hard rules:
  - Never push to the default branch
  - Never merge
  - Never force-push
  - Opening a draft PR is OK; marking ready for review is not _[decide]_
- Credentials: what does the session actually need? Scope the GitHub token to the minimum. Nothing else in the environment.
- **Threat model note:** an issue body is semi-untrusted text that will drive tool calls. Author filtering is the primary control. Assume prompt injection is possible via linked content, pasted logs, and fetched URLs even when I wrote the issue.

## 9. Result Reporting

- Comment back on the issue with: outcome, branch name, worktree path, cost, session ID, link to draft PR if created.
- Cost tracking: `--output-format json` returns `total_cost_usd` and a per-model breakdown (client-side estimate — differs from actual bill).
- Apply a status label: `claude:running` → `claude:done` / `claude:failed` / `claude:needs-input`
- On failure: post the error, leave the worktree in place for inspection, do not retry automatically.

## 10. Concurrency & Resource Limits

- Max concurrent sessions: _TBD_
- Per-repo concurrency limit (probably 1, to avoid worktree/dev-server collisions)
- Daily/weekly spend ceiling with a hard stop
- Queue behavior when at capacity: hold, or comment "queued"?
- **Open question:** MCP tool state bleeding between parallel sessions in different worktrees — does this actually bite me? Test before running >1.

## 11. State & Persistence

- Where does dispatch state live? (SQLite? JSON on disk? Labels on the issue itself as the source of truth?)
- Using GitHub labels as state is appealing — survives daemon restarts, visible in the UI, no local DB to corrupt.
- Must survive: daemon restart, machine reboot, GitHub API outage, session crash.
- Reconciliation loop on startup: what's labeled running but has no live process?

## 12. Observability

- Structured log to disk; one line per state transition.
- Health signal — something that goes red when the daemon dies. _(Ntfy? Trello card? Email? Existing homelab monitoring?)_
- This was the specific failure mode of the OpenClaw-based pipelines people have written about: silent death with no signal.

## 13. Deployment & Lifecycle

- Runs as: systemd user unit? Docker? tmux?
- Restart policy, log rotation, config reload without restart
- Auth: subscription login vs `ANTHROPIC_API_KEY`. Note that programmatic usage draws on subscription limits; API key is the unambiguous path for wrapped/automated use.
- Machine must be awake — how does that interact with how the workstation is actually used?

## 14. Open Questions / Parking Lot

- Should this also watch a Trello list, or is GitHub the single dispatch surface? (Currently: GitHub only, keep it simple.)
- Bridge from Gemini/voice: "open an issue on X about Y" → issue → orchestrator picks it up. Worth it?
- GitHub Actions with a self-hosted runner as an alternative or complement — better audit trail and per-repo config, but no interactive Remote Control handoff.
- Anthropic's self-hosted environments feature exists but is Team/Enterprise-only, so not applicable on an individual plan.
- Scheduled/proactive runs (e.g. nightly dependency review) — same daemon, or separate concern?
- Multi-machine: does this ever need to dispatch to more than one host?

## 15. Milestones

- **M0:** Poll one repo, detect eligible issue, log it. No execution.
- **M1:** Headless dispatch, worktree creation, result comment back to the issue.
- **M2:** Interactive mode with Remote Control link posted to the issue.
- **M3:** Continuation via issue comments (`--resume` with stored session ID).
- **M4:** Concurrency, spend caps, health monitoring.
- **M5:** _[whatever survives contact with reality]_
