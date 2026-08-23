# robot-army — Planning Document

A local daemon for delegating work to Claude Code, sourced from GitHub issues and Trello cards.

**Status:** Consolidated planning doc. Supersedes `github-claude-orchestrator-spec-skeleton.md` and the initial `README.md` brainstorm. Still a skeleton — bullets to edit, expand, and cut before writing the real spec.

---

## 1. Purpose & Scope

- A long-running local daemon, running as my user, that watches GitHub and Trello for work I've flagged, and starts Claude Code sessions in the right local directory.
- Claude Code runs with Remote Control enabled. The orchestrator's job ends once the session is up — a human drives from there.
- This handles software, homelab, and infrastructure work only. It is explicitly **not** a general personal assistant: no email, no calendar, no notes, no home automation. Those are handled elsewhere (Claude mobile + connectors).
- Built with the possibility that it becomes a general-purpose AI orchestrator, but not built *as* one today.

## 2. Design Principles

- **Local execution.** Sessions run on my machine, in my clones, with my environment.
- **No inbound network exposure.** Poll GitHub and Trello. No webhooks, no tunnels, no open ports (except the local web UI).
- **Human gates on dispatch.** Card → issue is automatic. Issue → dispatch requires me to apply a label. See §4.
- **Fail visibly.** A dead or stalled daemon must be obvious. Silent death is the known failure mode of these systems.
- **Idempotent.** Restart, reboot, or API hiccup must not duplicate work items, issues, cards, or sessions.
- **Boring and inspectable.** I should be able to query the DB and read the logs at 2am and understand what happened.
- **Thin abstractions.** Generic enough to add a second source or worker later; not so generic that it costs anything now.

### Dry-run mode (requirement)

The daemon must have a **dry-run mode**: it reports what it *would* do without updating GitHub issues, updating Trello cards, or launching Claude sessions.

**Implement it at the abstraction boundary, not at call sites.** The scattered-`if dry_run:` approach drifts (new code forgets the check), can't be tested, and lies — the simulated path diverges from the real one. §3 already defines exactly the interfaces that touch the outside world: Work Item Source (`update`, `create`), AI Worker (`dispatch`, `terminate`), and Session Host (`spawn`, `terminate`). Dry-run is a decorator over those, which logs the intended call and returns a plausible fake handle. Any new side-effecting operation *must* go through an interface, so it is structurally impossible to forget.

Note this implies **git operations need an interface too** (worktree add, fetch, branch create). Today they'd be inline calls; dry-run is the reason to give them a seam.

**Effects are not one boolean.** These are independently useful and a single flag can't express them:

| Level | Polls & evaluates | Git worktree + hooks | Session launch | GitHub/Trello writes |
|---|---|---|---|---|
| `plan` | ✅ real | ❌ simulated | ❌ | ❌ |
| `local` | ✅ real | ✅ real | ❌ | ❌ |
| `no-remote` | ✅ real | ✅ real | ✅ real | ❌ |
| `live` | ✅ | ✅ | ✅ | ✅ |

`local` is how you debug per-repo post-create hooks (§6) without burning subscription usage. `no-remote` is how you test dispatch end-to-end without commenting on real issues. Expose `--dry-run` as an alias for `plan` for ergonomics, but model the levels underneath.

**Polling always stays real.** A dry-run that fakes its reads tells you nothing about eligibility, which is the main thing you want to check.

**Persistence: real writes, with `dry_run` on the row.** Work items and sessions are written to the live database with `dry_run = true`, so the state machine can be observed advancing through `dispatching → active → interrupted` for real. One database, one code path.

**The `dry_run` flag governs reporting and remote writes — never resource accounting.** This is the subtle part, and getting it backwards causes real bugs. At the `no-remote` level a dry-run item has a *real* worktree and a *real* Claude session burning *real* subscription quota. So:

| Concern | Treatment of `dry_run` rows |
|---|---|
| GitHub/Trello writes | **Skipped** — this is the point of the mode |
| Global concurrency cap (§10) | **Counted.** A real session consumes real quota regardless of the flag |
| Per-repo cap (§10) | **Counted.** Otherwise a live dispatch collides with the dry-run's worktree |
| Reconciliation of sessions (§8) | **Performed.** A real session that dies still needs its state fixed |
| Reconciliation "is the issue closed?" | **Skipped** — there is no real source item to check |
| Web UI | **Shown, visually distinct.** Never silently filtered out |

The rule: *anything that consumed a real resource is accounted for; only the outward-facing effects are suppressed.*

**Risk of this approach, and the mitigation.** The failure mode is a query that forgets `WHERE dry_run = false` and quietly treats simulated work as live. Don't rely on remembering — make it structural: put the filter in the persistence layer's default scope (or a `live_work_items` view) so *including* dry-run rows is the explicit act, not excluding them.

**Dry-run rows accumulate.** Needs a purge command, and a decision on whether they're purged on startup or retained for inspection.

**Mode must be loudly visible.** Log it at startup, show it persistently in the web UI, and include it in the health signal (§14). Both failure directions are bad: believing you're in dry-run when you're live produces surprise GitHub comments; believing you're live when you're in dry-run is exactly the silent no-op §2 warns about.

**Known limitation:** dry-run doesn't write the mapping table, so repeated dry-runs will keep reporting "would create issue X" for the same card. That's correct behavior, but it means **dry-run cannot validate the §11 loop-prevention invariant** — that needs a real run against a test board, or unit tests.

### Settled decisions (not open for reconsideration)
- **Subscription auth only.** Sessions run under my Claude subscription. No API key, no pay-per-request. Consequence: orchestrator usage shares limits with my interactive sessions, which is why the concurrency cap in §10 matters.
- **Local execution only.** No GitHub Actions, no hosted runners, no cloud dispatch. Work happens in clones on my machine.
- **A normal interactive Claude Code session in a kitty window is the default experience.** Remote Control is the away-from-desk case, not the primary one. The host/display stack does not exist merely to solve PTY ownership — it exists because the session must be a real terminal session I can sit down at. Headless or background-agent modes do not satisfy this, however well they work. Sessions appear in the *already-running* kitty instance, which puts `kitty @ launch` on the critical path.

## 3. Core Abstractions

Keep these deliberately thin. The risk with a sample size of one is designing an interface shaped exactly like Claude Code.

### Work Item
- The unit of work. Has: source, source ID, canonical URL, title, prompt body, target repo, workdir, state, priority, timestamps.
- Concrete implementations: GitHub Issue, Trello Card.

### Work Item Source
- Interface: `poll() -> [work items]`, `update(item, status)`, plus `create(item)` for sources that can be written to.
- Everything about labels, columns, comment formats, and API pagination is implementation detail behind this.
- Concrete implementations: GitHub, Trello.

### AI Worker
- Interface: `dispatch(workdir, prompt, config) -> session handle`, `status(handle)`, `terminate(handle)`.
- Resist putting Claude-specific concepts (permission modes, MCP config, session IDs, `--continue` semantics) in the interface. Those live in an opaque per-worker config blob the implementation unpacks.
- Concrete implementation: Claude Code. Possible future: Bedrock direct, local inference.

### Session Host
Owns the process and its PTY (if any). This is the axis along which *work survival* varies, which is why it's abstracted separately from display.

- Interface: `spawn(cwd, argv) -> handle`, `is_alive(handle)`, `terminate(handle)`, `exit_code(handle)`, `attach_command(handle)`.
- Capability flags — the orchestrator branches on these, they're not decoration:
  - `survives_display_death` — does the work outlive the terminal showing it?
  - `reattachable` — can a human get a terminal back onto a running session?
  - `multi_viewer` — can more than one terminal attach at once?
- Implementations:
  - `direct` — plain child process, no PTY, stdout to a log file. Only viable if Claude Code doesn't require a TTY (see §17).
  - `dtach` — holds the PTY master, independent of any viewer. Preferred persistence layer: single-purpose, tiny, no config, no collision with existing tmux usage. (`abduco` is an equivalent alternative.)
  - `tmux` / `screen` — possible, heavier, brings multiplexing nobody asked for.

### Display
Optional viewer onto a hosted session. **Composed with a host, not substituted for one.**

- Interface: `open(host_handle)`, `is_open(handle)`, `close(handle)`.
- Implementations: `kitty`, `none`.
- Kitty and tmux are *not* peers — kitty renders a PTY someone else may own; tmux owns one. Modelling them as interchangeable would force a lowest-common-denominator interface. Today's setup is `KittyDisplay(DtachHost(...))`.

## 4. Sources, Triggers & Eligibility

### GitHub
- Poll repos: all of mine, plus a whitelist of repos that aren't mine.
- An issue is eligible only if **all** of:
  - `author == me` (security boundary — non-negotiable)
  - carries the `robot-army` label
  - repo is in the configured set
  - not already dispatched (see §7 state)
- Poll interval: _TBD (30s? 2m? adaptive backoff on rate limits?)_

### Trello
- **Explicit assumption: the board is private and nobody else can access it.** This is the security boundary for the Trello path — there is no author check, so board access *is* authorization. Revisit if the board is ever shared.
- Poll for cards with the `AI-task` label.
  - If the card has a single clear repo URL or local path (`~/GIT/<repo name>`), create a matching GitHub issue and comment on the card linking to it.
  - Otherwise mark the work item `needs_info`, surface in the web UI, and don't touch it again until it changes.
- **Re-scan:** poll `dateLastActivity` and auto-rescan `needs_info` cards when it changes, in addition to a manual "re-scan" button in the UI. I will forget to press the button.

### The human gate (deliberate invariant)
- Card → issue is automatic. Issue → dispatch requires me to add the `robot-army` label by hand.
- This is not an accident of the design; it is the safety property. A card description is semi-untrusted text (possibly pasted from a log, an email, a webpage) that would otherwise flow unreviewed into a prompt that drives shell access.
- **Do not optimize this away later for convenience.**

## 5. Priority & Ordering

- Configurable per repo: work all issues from a higher-priority repo before moving on, or strict oldest-to-newest globally.
- Known limitation: strict repo priority will starve low-priority repos. Accepted for now; add aging (bump priority by time in queue) if it becomes annoying.
- Revisit later if needed. Not worth engineering up front.

## 6. Workspace Isolation

**Decision: git worktrees, added to existing clones. No migration.**

```
~/GIT/privatepuppet/                     # untouched, my interactive clone
~/GIT-worktrees/privatepuppet/issue-142/ # git worktree add, from the above
```

- Shares the object store — cheap and instant. Zero migration of existing repos. My interactive clone is never touched by the orchestrator.
- Branch naming: _TBD (e.g. `robot-army/issue-142-short-slug`)_
- Created from a configurable base branch (default `main`), fetched fresh before creation.

### The actual pain: untracked and ignored files
- Worktrees do **not** bring `.env`, `node_modules`, `venv`, build caches, or local config.
- Every repo will need something here. Plan a **per-repo post-create hook** in config (symlink, copy, `uv sync`, `npm ci`, whatever) rather than discovering this one repo at a time.
- Submodules and worktrees interact awkwardly — check any repo with submodules before adding it.
- Two sessions running dev servers will fight over ports. Per-worktree port assignment is a future problem; note it now.

### Cleanup
- When? On issue close? On PR merge? Age-based? Manual prune command?
- Never auto-remove a worktree with uncommitted changes.

## 7. State Model

**Key insight: work state and session state are independent axes.** Conflating them is what makes "how do we know when a session finished?" unanswerable. Separating them makes both questions cheap.

### Work item state
| State | Meaning |
|---|---|
| `discovered` | Seen at source, not yet evaluated |
| `needs_info` | (Trello) insufficient information, awaiting human clarification |
| `ready` | Eligible, queued for dispatch |
| `dispatching` | Transient — worktree being prepared, session starting |
| `active` | A session is running for this item |
| `interrupted` | Session gone, work not complete (reboot, crash, kill) |
| `awaiting_review` | Session ended, source item not yet closed |
| `done` | Source item closed |
| `failed` | Dispatch error — couldn't create worktree, couldn't start session |
| `abandoned` | Human cancelled |

### Session state (separate table, FK to work item)
| State | Meaning |
|---|---|
| `starting` | Process spawned, not yet confirmed |
| `running` | PID alive |
| `exited_clean` | Observed exit, code 0 — human issued `/exit` |
| `exited_error` | Observed exit, non-zero |
| `lost` | No exit ever reported (reboot, host death, OOM, kill -9) |

### Resolving "is it finished?"
Three independent, cheaply observable facts. The orchestrator never *infers* completion from session behavior.

1. **Is the session alive?** Check the PID / ask the host / `kitty @ ls`.
2. **Did it exit cleanly?** `/exit` via Remote Control causes claude to finish up and exit 0 (verified). The wrapper reports this. **Exit 0 is the discriminator between `awaiting_review` and `interrupted`** — it means a human deliberately ended the session, rather than the session being killed out from under it.
3. **Is the work done?** The GitHub issue closing is the signal. Already in the design (Trello card moves to Done on issue close).

Mapping:
- exit 0 → `awaiting_review`
- non-zero exit → `awaiting_review` (with the error surfaced) or `failed`, _decide_
- no exit ever reported → `interrupted`
- issue closed → `done`, regardless of session state

**Exit 0 with the issue still open is a normal resting state, not an anomaly** — I might `/exit` because I'm going to lunch. Clean exit says the session ended deliberately; it says nothing about whether the work is finished.

## 8. Reconciliation & Reboot Recovery

- On daemon startup: **reconcile first, poll second.** Never dispatch new work before existing state is reconciled.
- **Also run reconciliation on a timer, not only at startup.** Under a non-persistent host, kitty death kills claude via SIGHUP with no clean exit and no wrapper report — nothing pushes. Without a periodic sweep, items sit in `active` with nothing behind them until the next daemon restart. Kitty death is far more likely than a reboot, so this is the common case, not the edge case.
- Reconciliation pass:
  - For every work item in `active`: ask the host `is_alive()`. Cross-check `kitty @ ls` for the window.
  - If not alive and no exit was reported → session `lost`, work item → `interrupted`.
  - For every item in `awaiting_review`: has the source item closed? → `done`.
- **After a reboot, everything is `interrupted`.** Expected, not an error condition.
- With a persistent host (`dtach`), kitty death should leave sessions `active` and merely display-less — reconciliation should confirm this rather than assume it.

### Resume policy
- **Never auto-resume.** Surface interrupted items in the web UI with a resume button. Auto-resuming an agent into unknown post-reboot state is exactly the class of surprising action this whole architecture exists to avoid.
- Resume mechanism: `claude --continue` in the worktree. Since it's one worktree per work item, `--continue` is unambiguous there.
- Also capture and store the session ID at dispatch if available — `--resume <id>` is more robust than positional `--continue`, and having the ID recorded costs nothing.
- Useful signals to show alongside the resume button, so I can decide whether resuming is even worth it:
  - Uncommitted changes in the worktree?
  - Any commits on the branch?
  - Is the issue already closed?
  - Is there an open PR?
- Alternative to resume: abandon the worktree, or start fresh. Both should be one click.

### systemd
- If the daemon starts on boot, ensure it starts *after* MariaDB (`After=` / `Requires=`).
- Consider a startup grace period — don't reconcile against a half-booted system.

## 9. Dispatch & Session Launch

Baseline invocation (confirmed working):

```
claude --remote-control "<session name>" --permission-mode auto "<prompt>"
```

Full launch chain (display → host → wrapper → worker):

```
kitty @ launch --cwd <worktree> -- \
  dtach -A <sockdir>/<item-id>.sock -- \
  robot-army-session-wrapper <item-id> claude --remote-control "<name>" --permission-mode auto "<prompt>"
```

### Process ownership
- `kitty @ launch` is an RPC to a **running kitty instance**; kitty forks the child itself. The claude process's parent is kitty, not the daemon. The `kitty @` client exits immediately.
- **Therefore the daemon can restart without killing sessions.** No `setsid` needed.
- **But kitty becomes the lifecycle owner.** Kitty allocates the PTY pair and holds the master; if kitty dies, the master closes, the slave hangs up, and the child gets SIGHUP and dies. Remote Control does **not** save you here — it's a channel to a process, and the process is gone. Recovery means `--continue` in the worktree, with whatever context loss that implies.
- Kitty death (closed last window, graphical session restart, compositor crash) is **much more likely than a reboot**. This is the main argument for a persistent host.
- `dtach` between kitty and the wrapper holds the PTY master independently, demoting kitty to a pure viewer. Kitty dying becomes cosmetic. Costs one word in the command line.

### Kitty specifics
- **Terminology collision:** kitty's own remote control (`kitty @`) is distinct from Claude Code's Remote Control. Use unambiguous names in code and config (`kitty_rc` vs `claude_rc`).
- Requires a **stable, predictable `listen_on` socket** in `kitty.conf` — not the default PID-derived path, which the daemon can't discover. A systemd user service won't inherit the graphical session's environment, so pass the socket path explicitly in config rather than relying on `KITTY_LISTEN_ON`. _Verify exact config syntax against current kitty docs._
- **Security note:** an always-listening kitty control socket lets anything that can reach it run arbitrary commands in my terminal. User-only unix socket is acceptable, but this is now load-bearing rather than a convenience — worth a deliberate decision, not a default.
- `kitty @ ls` reports `foreground_processes` with PIDs and command lines — recover both window ID and PID from kitty during reconciliation rather than trusting a PID recorded at launch.
- `--hold` keeps a window open after the command exits; useful for debugging failed launches, probably not on by default.

### The session wrapper
A small wrapper script sits between the host and claude. It exists because claude is not the daemon's child, so the daemon can't `waitpid()` on it.

- Runs claude, captures `$?`, and **POSTs the exit code back to the daemon's API** — exit detection becomes a push, not a poll.
- Also the natural home for: capturing the Claude session ID for later `--resume`, per-repo pre/post hooks, per-session log file.
- Use `exec` where possible to avoid a pointless extra process layer (note: not compatible with capturing `$?` in the same shell — structure accordingly).
- **Edge case:** if the host dies, the wrapper dies too and never reports. Reconciliation (§8) remains the exception path for exactly this case.

### Misc
- Session naming convention: _TBD — should encode source + item ID so it's identifiable in the Claude app._
- Per-repo config overrides: permission mode, allowed tools, model, base branch, post-create hook, host/display choice.

### Context
- **Do not use `--bare`.** It skips CLAUDE.md, hooks, skills, plugins, and MCP auto-discovery — exactly the accumulated per-repo context that makes these repos work well.
- Prompt seeding: issue title + body + label context. Consider a repo-level `.claude/robot-army.md` for dispatch-specific instructions distinct from CLAUDE.md.

### Guardrails
- Never push to the default branch. Never merge. Never force-push.
- Draft PR is fine; marking ready for review is a human action.
- Prefix-matching gotcha if using tool allowlists: `Bash(git diff *)` with the space — `Bash(git diff*)` also matches `git diff-index`.

## 10. Concurrency

- Global cap on concurrent orchestrator sessions: _TBD_
- Per-repo cap: probably 1, to avoid worktree and dev-server collisions.
- **Awareness of my own out-of-band sessions:** count `claude` processes not owned by the orchestrator against the global cap.
  - Implementation: process scan, or inspect `~/.claude` session state. Both are somewhat fragile — accept best-effort.
  - Orchestrator sessions run under my subscription plan, same as my interactive ones, so they genuinely do contend. This cap is doing real work, not guarding a hypothetical.
- Queue behavior at capacity: hold in `ready`, show queue position in UI.

## 11. Loop Prevention & Idempotency

**Invariant: one work item ⇒ at most one GitHub issue ⇒ at most one Trello card.**

- The DB mapping table is the source of truth for the card ↔ issue relationship.
- The `robot-army is working <issue URL>` comment is a **recovery marker** for rebuilding state after DB loss — not the primary key. Don't parse comments as the authoritative source in normal operation.
- Every create operation checks the mapping first.
- Guard against: card creates issue → issue creates card → repeat. The mapping check should make this structurally impossible, but write a test for it.

## 12. Storage

- **MariaDB** — already running, already backed up. Reasonable call.
- Tradeoffs to be aware of: external service dependency (startup ordering, DB outage takes down the daemon), and the daemon is no longer portable to a laptop without a DB.
- Keep the persistence layer thin enough that SQLite remains a drop-in option if that ever matters.
- Dedicated database and user for robot-army.

## 13. Web UI & API

- **Web UI first**, backed by an HTTP API. CLI/TUI clients later against the same API.
- Rationale: the web UI is reachable from my phone, which is the same ergonomic win as everything else in this architecture.
- Views needed:
  - Active sessions — what's running, in what worktree, links to issue/card
  - Queue — what's `ready`, in what order
  - `needs_info` cards — with re-scan button
  - `interrupted` items — with resume / abandon / restart buttons, and the resume-decision signals from §8
  - Audit log
- Controls: pause dispatch, cancel a session, adjust concurrency cap, force poll.

## 14. Observability & Health

- Structured log to disk, one line per state transition.
- **Audit log in the web UI**, with GitHub repos/issues, Trello cards, and PRs as clickable links.
- **Health signal is not a stretch goal.** A dead-man's-switch — heartbeat to ntfy/Pushover, or a Trello card that goes stale — belongs in M1. Silent death is the specific failure mode that kills these systems.
- Notifications on significant events (dispatch, completion, failure, needs-info): stretch, but cheap once the health channel exists.

## 15. Milestones

- **M0 — Spike (no daemon, no DB, no UI).** By hand, in a script. Everything here is an *empirical* question — "is this possible / what actually happens" — not a design decision. Answers most remaining unknowns in an evening or two.

  **Process model & TTY**
  - **Does `claude --remote-control` require a TTY?** Run it with stdin/stdout redirected, no controlling terminal, from a non-interactive shell. Highest-leverage test in the list: if no TTY is needed, `direct` becomes a real host backend, kitty attachment becomes opt-in per session, and the whole host/display stack gets simpler than either current option.
  - Create a worktree, launch via `kitty @ launch`, confirm I can drive the session from my phone.
  - Kill kitty. Confirm claude dies via SIGHUP (expected). Repeat with `dtach` in the chain — confirm it survives and can be reattached from a fresh kitty window.
  - Reboot. Confirm reconciliation assumptions hold and that `--continue` in the worktree actually resumes usefully.

  **Exit & session identity**
  - `/exit` via Remote Control → confirm exit 0 propagates through `dtach` and the wrapper, not just from a bare claude process.
  - **Is a Claude session ID exposed at launch or during the session, and can the wrapper capture it?** Determines whether `--resume <id>` is available or whether resume is stuck with positional `--continue`. Affects §8 resume policy.
  - **What does a non-zero exit actually look like** in the cases I can produce (bad prompt, missing dir, killed mid-run)? Needed before deciding `awaiting_review` vs `failed` in §16.

  **Kitty plumbing**
  - **Can a systemd user service reach the kitty control socket** with an explicit `listen_on` path, given it won't inherit the graphical session's environment? Confirm the config syntax while I'm there.
  - **Does `kitty @ ls` report enough** to identify a specific session's window and PID through a `dtach` layer? The process tree is deeper than the simple case — verify `foreground_processes` still gives me something usable.
  - **Can I spawn a new kitty window onto an existing `dtach` session** (`dtach -a`)? Determines whether the web UI "attach" button in §16 is feasible.

  **Out-of-band session detection**
  - **Can I reliably distinguish my own interactive `claude` sessions from orchestrator ones?** Try both the process scan and `~/.claude` state inspection; find out which is less fragile. Feeds the §10 concurrency cap, which matters now that subscription auth is settled.

  **Worktree reality check**
  - Add a worktree to two or three real repos from `~/GIT/` and see what breaks. Specifically: what untracked/ignored files are actually needed, and does anything with submodules choke?
- **M1 — Minimum daemon.** GitHub polling, eligibility check, worktree creation, dispatch, state in MariaDB, reconciliation on startup, health signal. No Trello. Log-only observability.
- **M2 — Web UI.** Active/queue/interrupted views, resume and cancel controls, audit log.
- **M3 — Trello.** Card → issue creation, `needs_info` handling with auto-rescan, In Progress / Done card lifecycle, loop prevention.
- **M4 — Concurrency & polish.** Caps, out-of-band session awareness, per-repo config, priority modes, notifications.
- **M5 —** _whatever survives contact with reality._

## 16. Open Questions / Parking Lot

Empirical questions ("is this possible / what happens") have moved to the M0 spike. What's left here is **thought and decision** — things no experiment will answer.

### Decisions to make
- Worktree cleanup policy — when, and what about uncommitted changes?
- Session naming convention.
- Per-worktree port assignment scheme for repos with dev servers.
- Non-zero wrapper exit → `awaiting_review` with the error surfaced, or `failed`? _(M0 will show me what non-zero exits actually look like; the classification is still a judgment call.)_
- Kitty control socket security — acceptable as a user-only unix socket, or worth gating further? It's load-bearing now, so this deserves a deliberate answer rather than a default.
- Should the web UI offer an "attach" button for `reattachable` hosts? _(M0 establishes feasibility; whether it's worth building is the open part.)_
- Multi-machine dispatch — not now, but does the Session Host abstraction accidentally preclude it? Worth five minutes of thought before the interface is written, not after.
- Scheduled/proactive work (nightly dependency review, etc.) — same daemon, or a separate concern?

### Resolved
- ~~Do sessions survive a daemon restart?~~ Yes. `kitty @ launch` makes kitty the parent, not the daemon. No `setsid` needed.
- ~~API key vs subscription auth?~~ Subscription. See Settled Decisions in §2.
- ~~GitHub Actions as a complement?~~ No. Local execution only. See Settled Decisions in §2.
- ~~Usage/session limit awareness?~~ Deferred as a possible future enhancement; the §10 concurrency cap covers the practical need.
