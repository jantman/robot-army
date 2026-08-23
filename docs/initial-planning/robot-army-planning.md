# robot-army — Planning Document

A local daemon for delegating work to Claude Code, sourced from GitHub issues and Trello cards.

**Status:** Consolidated planning doc. Supersedes `github-claude-orchestrator-spec-skeleton.md` and the initial `README.md` brainstorm.

**M0 spike is complete** and its results are folded into §§3–10 below. Full method, raw output, and 19 numbered findings live in [`m0-spike-plan.md`](m0-spike-plan.md); the spike scripts are in [`spike/`](../../spike/). Claims that were *measured* are marked as such — everything else is still design intent awaiting contact with reality.

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
- **The session ID is orchestrator-generated, not worker-reported.** `claude --session-id <uuid>` accepts a UUID we choose, so `dispatch()` takes the ID as an input rather than returning it. It is known and persisted *before* the process starts, which survives every failure mode including a process that dies before writing anything. (M0 E3.4, verified end to end.)
- **`terminate(handle)` has a clean implementation:** each session lands in its own systemd scope (`kitty-<pid>-<n>.scope`), so `systemctl --user stop <scope>` kills exactly that session's process tree. Record the scope by reading `/proc/<pid>/cgroup` at dispatch — treat the name as an opaque handle, don't compute it. (M0 F18.)

### Session Host
Owns the process and its PTY (if any). This is the axis along which *work survival* varies, which is why it's abstracted separately from display.

- Interface: `spawn(cwd, argv) -> handle`, `is_alive(handle)`, `terminate(handle)`, `exit_code(handle)`, `attach_command(handle)`.
- Capability flags — the orchestrator branches on these, they're not decoration:
  - `survives_display_death` — does the work outlive the terminal showing it?
  - `reattachable` — can a human get a terminal back onto a running session?
  - `multi_viewer` — can more than one terminal attach at once?
- Implementations:
  - `direct` — plain child process, no PTY, stdout to a log file. Only viable if Claude Code doesn't require a TTY. **Untested and deliberately so** — the §2 settled decision makes a kitty session the product, so a headless host answers a question nobody is asking. Revisit only if a headless mode is ever wanted.
  - `dtach` — holds the PTY master, independent of any viewer. Preferred persistence layer: single-purpose, tiny, no config, no collision with existing tmux usage. (`abduco` is an equivalent alternative.)
  - `tmux` / `screen` — possible, heavier, brings multiplexing nobody asked for.

**`dtach` capability flags — all three measured in M0, not assumed:**

| Flag | Value | Evidence |
|---|---|---|
| `survives_display_death` | **true** | Killed kitty; the dtach master and its child survived, socket persisted, no false exit reported |
| `reattachable` | **true** | `dtach -a` from a *fresh* kitty instance restored a live claude session, fully repainted |
| `multi_viewer` | **true** | Two kitty windows attached at once; both mirrored output and either could drive input |

Reattach repaints because `dtach -a` defaults to a `ctrl_l` redraw and Claude Code honours it. If a future worker ever doesn't, `dtach -a -r winch` is the documented alternative. This makes the §13 "attach" button straightforwardly feasible.

**`dtach` argument form (this bit is easy to get wrong):** `dtach -A <socket> <cmd> [args...]` — **no `--` separator**. dtach rejects `--` outright (`Invalid option '--'`). See §9.

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

### Second security boundary: repo-committed tool permissions

The author check above protects the *issue* path. It does nothing about the *repo contents* path, and M0 turned up a real hole there (F9).

Claude Code's workspace-trust dialog also accepts whatever tool permissions a repo has **committed** to `.claude/settings.json` (or a committed `.claude/settings.local.json`). Observed verbatim on a real repo:

> ⚠ This folder pre-approves 3 tool permissions in `.claude/settings.local.json`: WebSearch, WebFetch(domain:…), … These will apply without asking.

For a whitelisted repo I don't control, anyone with commit access can add pre-approved permissions that a dispatched session will honour silently.

- Scope, measured across all 294 repos in `~/GIT`: 28 have *untracked* `settings.local.json` (mine, globally gitignored — no risk), **1 committed `settings.local.json`, and 4 committed `.claude/settings.json`**. The exposure is specifically committed settings, which is a small and checkable target.
- **Trusting a repo must be a deliberate per-repo onboarding step**, never automatic — the same philosophy as the human gate above.
- **Record the committed settings file's hash at onboarding and re-check it at dispatch**, so a *change* to a repo's pre-approved permissions re-triggers human review. Cheap, and it closes the hole.
- For non-owned repos, consider `--strict-mcp-config` plus an explicit `--disallowedTools`.
- Note that guardrails cannot be imposed by *omitting* files from the worktree — settings resolve from the main clone (§6), so tighter limits must come from the command line.

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

- Shares the object store — cheap and instant *for git objects*. Zero migration of existing repos. My interactive clone is never touched by the orchestrator.
- Branch naming: _TBD (e.g. `robot-army/issue-142-short-slug`)_
- Created from a configurable base branch (default `main`), fetched fresh before creation.
- **Dispatch precondition: the main clone must be trusted.** Claude Code keys workspace trust on the *main clone*, not the worktree, so a worktree of a trusted repo never prompts — but a worktree of an untrusted one blocks on a modal dialog forever. Check `~/.claude.json` → `projects["<main clone path>"].hasTrustDialogAccepted` at dispatch and fail the item visibly rather than launching a session that hangs invisibly. One entry per repo, so nothing accumulates. (M0 E1.5.)

### The actual pain: untracked and ignored files — smaller than it looks

Measured across all 294 repos in `~/GIT` (M0 E6.1, F13). The original assumption here was *"every repo will need something"*; that turns out not to be true, because **a dispatched session builds, tests, and lints — it does not run the production app.**

| Marker | Repos | What's actually needed |
|---|---|---|
| `venv/` / `.venv/` | 39 | **The dominant case.** One shared default hook (`make setup`, `python -m venv && pip install -r`) |
| `tox.ini` | 47 | Nothing — rebuilds its own envs |
| `.claude/` | 30 | **Nothing.** Settings resolve from the main clone, see below |
| `.gitmodules` | 11 | Real work — see below |
| `docker-compose.yml` | 10 | Ports only, and they're env-configurable |
| `.env` | 4 | Symlink or copy — **and only if the session must run the app** |
| `node_modules/` | 2 | Rarer than expected |

Evidence for the "tests don't need `.env`" claim: a fresh worktree of `equipment-status-board` (venv + `.env` + compose) ran **2029 tests passing in 134s** after nothing but `make setup`, with no `.env` and no `instance/`. So roughly **15 repos need bespoke config, not 294** — the hook config is an override list, not a registry.

- **`.claude/` needs no plumbing.** Both trust *and* project settings resolve through git to the main clone. A worktree with no `.claude/` directory at all still picked up the main clone's (gitignored) `settings.local.json`. Dispatched sessions therefore inherit my per-repo allowlists automatically. (M0 F12.)
- **Submodules are the real cost.** They come up empty in a fresh worktree and **are not shared with the main clone** — the worktree gets its own `.git/worktrees/<n>/modules/`, so each worktree re-clones them. "Cheap and instant" does not hold for these 11 repos.
- **Worktrees are not cheap once a hook runs.** The finished `equipment-status-board` worktree measured **499 MB** — the venv, not the git objects. Several active repos means multiple GB of ephemeral disk. (M0 F14.)
- **Ports are a config problem, not a patch-every-repo problem.** `equipment-status-board` parameterises every port via its tracked `.env.example` (`ESB_HOST_PORT`, `FLASK_RUN_PORT`, …), so per-worktree port assignment is just env injection — the same mechanism the `.env` repos already need. Spot-check each compose repo at onboarding rather than assuming.

### Post-create hooks are a failure domain, not a convenience

**Every hook step needs a timeout.** In M0, `git submodule update --init --recursive` on a real repo **hung indefinitely** and had to be killed — its `.gitmodules` uses `git://` URLs, and GitHub disabled that protocol in 2021, so port 9418 is now *dropped* rather than refused. It doesn't error; it hangs. (M0 F15.)

A hung hook wedges the work item in `dispatching` forever with no session, no error, and nothing for reconciliation to observe. So:

- Per-step `timeout`, configurable, with a sane default.
- Hook timeout or non-zero exit → work item `failed` with captured output. **Never launch a session into a half-built worktree.**
- `dispatching` needs a **maximum age** in reconciliation (§7, §8).

Sketch, driven by what actually broke:

```yaml
repos:
  equipment-status-board:
    post_create:
      - run: make setup        # 47s measured
        timeout: 300
      - link: .env             # from the main clone; only if the app must run
    ports: { ESB_HOST_PORT: auto, ESB_DEV_HOST_PORT: auto }
  specfiles:
    post_create:
      - run: git submodule update --init --recursive
        timeout: 120           # WILL hang on this repo -> fail, don't stall
  electronics-projects: {}     # nothing needed
```

Hooks run after branch creation, in the worktree, with the main clone's path available. Both `run` (shell) and `link`/`copy` forms are needed — expressing a symlink as a shell command works but reads badly and is harder to make idempotent.

### Cleanup
- When? On issue close? On PR merge? Age-based? Manual prune command? _Still to decide_ — but the 499 MB measurement argues for cleaning up on issue close rather than retaining indefinitely.
- Never auto-remove a worktree with uncommitted changes. **Git enforces this for free:** `git worktree remove` refuses on a dirty tree — *including merely untracked files* — with `use --force to delete it`. The daemon gets the guard by simply never passing `--force` without a human decision.
- **Cleanup is two steps.** `worktree remove` always leaves the branch behind; `git branch -D` is separate. Doing only the first accumulates `robot-army/*` branches in every repo.
- A worktree directory deleted out from under git shows as **`prunable`** in `git worktree list` — a detectable state worth surfacing to reconciliation.

## 7. State Model

**Key insight: work state and session state are independent axes.** Conflating them is what makes "how do we know when a session finished?" unanswerable. Separating them makes both questions cheap.

### Work item state
| State | Meaning |
|---|---|
| `discovered` | Seen at source, not yet evaluated |
| `needs_info` | (Trello) insufficient information, awaiting human clarification |
| `ready` | Eligible, queued for dispatch |
| `dispatching` | Worktree being prepared, session starting. **Not reliably transient** — a hung post-create hook (§6) can wedge an item here indefinitely, so this state needs a **maximum age** enforced by reconciliation |
| `active` | A session is running for this item |
| `interrupted` | Session gone, work not complete (reboot, crash, kill). **Does not imply "nothing is running"** — see the orphan case in §8 |
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

Mapping — **the non-zero case is now decided**, based on the exit codes M0 actually produced (E3.3):

| Observation | State | Reasoning |
|---|---|---|
| exit **0** | `awaiting_review` | Verified end to end: `/exit` → 0 through kitty → dtach → wrapper |
| exit **1, 126, 127** | `failed` | Config/dispatch errors — bad flag, bad `--permission-mode`, bad `--model`, missing or non-executable binary. Claude never ran; retrying without a config change is pointless. Surface the stderr |
| exit **128+N** (137 = SIGKILL, 143 = SIGTERM) | `interrupted` | Something external killed it. The work may be perfectly resumable via `--resume <id>` — this is *not* a failure of the item |
| no exit ever reported | `interrupted` | Via reconciliation (§8) |
| issue closed | `done` | Regardless of session state |

The wrapper decodes signals as `128+N` and records the signal number explicitly, so "crashed" and "a human killed it" stay distinguishable.

⚠️ **Two silent-tolerance cases to guard at dispatch rather than discover at runtime:** `--add-dir` pointing at a nonexistent path and a malformed `--settings` file both exit **0** and proceed. Claude Code's own docs note invalid settings are *"silently ignored"* in print mode — implying interactive mode may show a blocking dialog instead, the same hazard as the trust dialog. Validate generated paths and settings before launch.

**Exit 0 with the issue still open is a normal resting state, not an anomaly** — I might `/exit` because I'm going to lunch. Clean exit says the session ended deliberately; it says nothing about whether the work is finished.

## 8. Reconciliation & Reboot Recovery

- On daemon startup: **reconcile first, poll second.** Never dispatch new work before existing state is reconciled.
- **Also run reconciliation on a timer, not only at startup.** Under a non-persistent host, kitty death kills claude via SIGHUP with no clean exit and no wrapper report — nothing pushes. Without a periodic sweep, items sit in `active` with nothing behind them until the next daemon restart. Kitty death is far more likely than a reboot, so this is the common case, not the edge case.
- Reconciliation pass:
  - For every work item in `active`: ask the host `is_alive()`. Cross-check `kitty @ ls` for the window.
  - If not alive and no exit was reported → session `lost`, work item → `interrupted`.
  - For every item in `awaiting_review`: has the source item closed? → `done`.
  - For every item in `dispatching` older than the max age → `failed` (a hung post-create hook, §6).
  - **Sweep for orphans** — see below.
  - Prune stale dtach sockets and `prunable` worktrees.
- **After a reboot, everything is `interrupted`.** Expected, not an error condition.
- With a persistent host (`dtach`), kitty death leaves sessions `active` and merely display-less — **confirmed in M0**, and the session registry's `status` field even flips `busy → idle` when the display dies.

### What reconciliation can actually observe (all measured in M0)

- **`~/.claude/sessions/<pid>.json` is a live session registry.** One file per running Claude Code session, exact 1:1 correspondence with live processes, no stale entries in the happy path (the file is removed on clean exit). Each carries `sessionId`, `cwd`, `status` (`busy`/`idle`/`shell`), `version`, and `procStart`.
  - Because the daemon *generates* the `sessionId` (§3), the DB↔process join is **exact**, not best-effort — this supersedes §10's original "accept best-effort" note.
  - `procStart` guards against PID reuse. Use PID **and** `procStart`, never PID alone.
  - It is an **undocumented internal format**. It records its own `version` — guard on it and degrade to `/proc/<pid>/exe` + `cwd` rather than crashing.
  - The adjacent `<pid>.<hash>.key` files (mode 0600) look like session credentials. **Never read or copy them.**
- **Transcripts are at `~/.claude/projects/<encoded-worktree-path>/<session-id>.jsonl`** — the filename *is* the session ID, so a transcript is locatable from data already in the DB.
- **Stale sockets are cheap to detect, and must be pruned by us.** A dead kitty socket gives `ECONNREFUSED` in ~25 ms; a dead dtach socket makes `dtach -a` fail in ~7 ms. Neither hangs, and neither cleans itself up. **Probe; never trust the file's existence.**
- **Never identify processes by command line.** `pgrep -f claude` returned 18 matches of which 12 were the Claude *desktop* app, plus unrelated shells whose argv merely contained the string. Two real incidents in M0: a `pkill -f` killed the invoking shell, and a `pgrep -f` matched kitty's `run-shell` wrapper instead of the intended process, producing a wrong conclusion. Match on `/proc/<pid>/exe` and `cwd` instead.

### The orphan case: `interrupted` does not mean "nothing is running"

If the wrapper dies without reporting (`kill -9`, OOM), **claude keeps running, reparented**. dtach then sees its child exit and tears down its socket, so the daemon observes *no socket and no exit report* and marks the item `interrupted` — **while a real session is still alive, editing files and consuming subscription quota.**

So reconciliation must sweep for claude processes whose `cwd` is under `~/GIT-worktrees/` and reconcile them against known items. An orchestrator-cwd process with no matching `active` row **is** an orphan; flag it loudly rather than letting it run unaccounted. This is the same scan §10 needs for the concurrency cap, doing double duty.

*(The tempting in-wrapper fix — trap signals and kill the child — was evaluated and rejected: trapping requires backgrounding the payload, and bash with job control off redirects a background command's stdin from `/dev/null`, which would break the interactive session the whole design exists to protect. It also wouldn't help, since SIGKILL can't be trapped. The daemon-side sweep is the primary mitigation, not a fallback.)*

### Resume policy
- **Never auto-resume.** Surface interrupted items in the web UI with a resume button. Auto-resuming an agent into unknown post-reboot state is exactly the class of surprising action this whole architecture exists to avoid.
- **Resume mechanism: `claude --resume <id>` in the worktree — settled, and verified end to end.** The orchestrator generates the UUID at dispatch and passes it via `--session-id`, so the ID is persisted before the process starts. M0 confirmed the whole loop: generated UUID → adopted by the session → transcript written under that ID → after `/exit`, `--resume <id>` restored full context.
- Positional `--continue` is therefore **not needed**; it remains a fallback only.
- Still unverified (minor): reusing the *same* ID for a second launch (§11 idempotency on retry), and composition with `--fork-session`.
- Useful signals to show alongside the resume button, so I can decide whether resuming is even worth it:
  - Uncommitted changes in the worktree?
  - Any commits on the branch?
  - Is the issue already closed?
  - Is there an open PR?
- Alternative to resume: abandon the worktree, or start fresh. Both should be one click.

### systemd
- **Decided: the daemon is started manually after graphical login.** Auto-start on boot is not a requirement.
- This is not laziness — it removes a whole failure class. The graphical session imports `WAYLAND_DISPLAY`, `DISPLAY`, and `DBUS_SESSION_BUS_ADDRESS` into the systemd user manager *at login*, so a daemon started at boot under `loginctl enable-linger` would have none of it and no kitty to launch into. Starting by hand makes that state unreachable.
- The daemon should still **check its preconditions at startup** (kitty socket answers, DB reachable) and fail loudly if started too early. Cheap, and it turns a confusing silent failure into a clear one.
- If auto-start is ever wanted: `After=`/`Requires=` MariaDB plus `PartOf=graphical-session.target`, and test it then.
- **A daemon restart cannot kill running sessions** — and for a better reason than §9's "kitty is the parent" argument, which would not have saved us, since systemd kills by *cgroup*, not parentage. Measured: kitty places each launched window in its own `kitty-<pid>-<n>.scope`, disjoint from the daemon's service cgroup.

## 9. Dispatch & Session Launch

Baseline invocation (confirmed working):

```
claude --remote-control "<session name>" --permission-mode auto "<prompt>"
```

Full launch chain (display → host → wrapper → worker):

**Corrected and verified against a real dispatch in M0.** The earlier version of this block did not work — `dtach` rejects a `--` separator (`Invalid option '--'`), and the wrapper needs its own `--` before the worker command:

```
kitty @ --to unix:<discovered-socket> launch \
  --type=tab --cwd <worktree> \
  --title "ra-<item-id>" --var ra_item=<item-id> \
  --env ROBOT_ARMY_ITEM=<item-id> \
  -- dtach -A <sockdir>/<item-id>.sock \
     robot-army-session-wrapper <item-id> -- \
     claude --session-id <generated-uuid> \
            --remote-control "<name>" --permission-mode auto "<prompt>"
```

Note `--to` is mandatory (there's no `KITTY_LISTEN_ON` in a service environment), `--var` is what makes the window findable later, and `--env` is the *only* way to get anything into the session (see Environment below).

### Process ownership
- `kitty @ launch` is an RPC to a **running kitty instance**; kitty forks the child itself. The claude process's parent is kitty, not the daemon. **Verified: the `kitty @` client exits immediately** — the transient unit went `inactive` while the session kept running.
- **Therefore the daemon can restart without killing sessions.** No `setsid` needed. *(The real guarantee is cgroup separation, not parentage — see §8 systemd.)*
- **But kitty becomes the lifecycle owner.** Kitty allocates the PTY pair and holds the master; if kitty dies, the master closes, the slave hangs up, and the child gets SIGHUP and dies. **Confirmed in M0:** without dtach, killing kitty killed the session with no exit report at all — only a `start` record, which is exactly the reconciliation case.
- Kitty death (closed last window, graphical session restart, compositor crash) is **much more likely than a reboot**. This is the main argument for a persistent host.
- `dtach` between kitty and the wrapper holds the PTY master independently, demoting kitty to a pure viewer. **Confirmed:** with dtach in the chain, killing kitty left the session running and reattachable, with the socket intact and no false exit reported.

### ⚠️ Dispatch is not confirmed by the launch command's exit code

`kitty @ launch` returns **0 and a valid window id even when the session never started** — demonstrated three times in M0 (bad `dtach` args, a nonexistent `--cwd`, and a malformed command). There is no diagnostic anywhere; it is indistinguishable from success at the API level.

**Confirm every dispatch by an independent observation** before marking the item `active`:
1. the wrapper's `start` record arriving, and
2. `~/.claude/sessions/<pid>.json` existing with the `sessionId` we generated.

Check (2) is load-bearing for a second reason — see Environment below. Until one of these is seen, the item stays `dispatching` (which is why that state needs a max age).

**Use `--hold` on failure paths.** It keeps the window open after the command exits, turning a window that vanishes instantly into a readable error. §15's original note called it "probably not on by default"; M0 says it is the difference between a diagnosable failure and a silent one.

### Environment: sessions inherit *kitty's* environment, not the daemon's

`kitty @ launch` forks the child from the **kitty daemon process**, so the session inherits whatever the user's login kitty carries — *not* the environment of the daemon issuing the RPC. Anything the session needs must be passed explicitly with `--env`.

This caused a real, **silent** failure in M0: a kitty that had been started from inside a Claude Code session carried `CLAUDE_CODE_CHILD_SESSION=1`, which the dispatched session inherited. The session looked perfect — ran, answered, exited 0 — but **transcript saving was disabled**, so no registry entry and no transcript were written and `--resume` would have been impossible. The only evidence was one line in the status bar.

Mitigations:
- Pass `--env CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1` defensively, and/or scrub `CLAUDE_CODE_*` from the launch environment.
- **Verify after dispatch** that the session registry contains the expected `sessionId` (the same check as above). This catches silently-degraded sessions, not just failed launches.
- §14: "session ran but produced no transcript" deserves to be a surfaced anomaly, not a silent state.

### Kitty specifics
- **Terminology collision:** kitty's own remote control (`kitty @`) is distinct from Claude Code's Remote Control. Use unambiguous names in code and config (`kitty_rc` vs `claude_rc`).
- **Socket discovery, not prediction.** kitty appends its PID to `listen_on`, so `listen_on unix:/tmp/mykitty` becomes `/tmp/mykitty-5300` and no fixed path is available. That doesn't matter: **the daemon globs the configured pattern and probes each candidate with `kitty @ --to <s> ls`, taking whichever answers.** A dead socket fails in 14–25 ms (`ECONNREFUSED`), so there is no hang risk and stale sockets are harmless. **Config stores the pattern, not a path.** This also tolerates the user restarting kitty.
- **A systemd user service can reach the socket** — verified, including with the display environment stripped entirely. `kitty @` is just a unix-socket client, so the daemon needs *only* the socket path, never a display connection. `--to` must be passed explicitly every time.
- **Security note:** an always-listening kitty control socket lets anything that can reach it run arbitrary commands in my terminal. User-only unix socket is acceptable, but this is load-bearing rather than a convenience — worth a deliberate decision, not a default. *(Still open; M0 did not change this.)*
- **Correlate windows with `--var`, not command lines.** `kitty @ launch --var ra_item=<id>` comes back in `kitty @ ls` as `user_vars`, giving an exact key lookup. The window object also carries `cwd`, `pid`, `env`, `title`, and `last_cmd_exit_status`. Walking `foreground_processes` also works but is fragile — `--hold` inserts a `kitten run-shell` layer that repeats the entire command in its own argv, so the same string appears at several depths. (This bit me during M0.)
- `--hold` keeps a window open after the command exits — see the dispatch-confirmation note above; more valuable than originally assumed.
- Driving input into a session (e.g. a "cancel" control sending `/exit`) needs `kitty @ send-text` with **`\r`**, not `\n`. `\n` types the text without submitting it, which looks exactly like the command failing.

### The session wrapper
A small wrapper script sits between the host and claude. It exists because claude is not the daemon's child, so the daemon can't `waitpid()` on it.

A working spike implementation lives at `spike/ra-session-wrapper.sh`; M1's wrapper should start from it.

- Runs claude, captures `$?`, and **POSTs the exit code back to the daemon's API** — exit detection becomes a push, not a poll. Verified to propagate `0`, `1`, `42`, `126`, `127`, `137`, and `143` faithfully through both dtach and kitty.
- Decodes signal deaths as `128+N` and records the signal number separately, so §7 can tell "crashed" from "a human killed it".
- Also the natural home for: per-repo pre/post hooks and a per-session log file. **Not** for capturing the session ID — the orchestrator generates it (§3).
- **Do not use `exec` for the worker.** §9 originally suggested it to avoid an extra process layer, but `exec` replaces the shell and the exit code could never be captured — which is the wrapper's entire reason to exist. The extra process stays; document why so nobody "optimizes" it later.
- **Edge case:** if the host dies, the wrapper dies too and never reports. Reconciliation (§8) remains the exception path — and note the orphan case there, where the wrapper dies but claude does not.

### Misc
- Session naming: two distinct knobs — `--remote-control "<name>"` (shown in the Claude app) and `-n/--name` (shown in the prompt box, `/resume` picker, and terminal title). Set **both**, encoding source + item ID. Left alone, Claude Code auto-derives a name from the directory (observed: `real-1-9e`), which is not identifiable. `--remote-control-session-name-prefix` defaults to the hostname if a prefix is ever wanted.
- Per-repo config overrides: permission mode, allowed tools, model, base branch, post-create hook (+ timeout), host/display choice.
- `--permission-mode` accepts: `acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, `plan`. Intended default is `auto`.

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
  - **Implementation is settled, and it is exact rather than best-effort** (M0 revised this note). Read `~/.claude/sessions/<pid>.json`; skip entries whose PID is dead or whose `procStart` no longer matches; classify by `cwd` (`~/GIT-worktrees/…` ⇒ orchestrator, anything else ⇒ out-of-band). Orchestrator rows join to the DB on `sessionId`, which the daemon generated. Fall back to `pgrep -x claude` filtered by `/proc/<pid>/exe` only if the registry format changes — guard on its `version` field. **Never match command lines** (§8).
  - Every session found counts, orchestrator or not. A `dry_run` session counts too (§2) — it burns the same quota.
  - An orchestrator-cwd process with no matching `active` row is an **orphan** (§8), not a capacity number to ignore.
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
- **Anomalies worth surfacing rather than swallowing**, all of them observed during M0:
  - a session that ran but produced **no transcript** (§9 Environment) — looks healthy, cannot be resumed
  - an orchestrator-cwd claude process with **no matching `active` row** (§8) — an orphan
  - an item stuck in `dispatching` past its max age (§6 hung hook)
  - a dispatch whose registry entry never appeared, or appeared with the wrong `sessionId`
- Notifications on significant events (dispatch, completion, failure, needs-info): stretch, but cheap once the health channel exists.

## 15. Milestones

- **M0 — Spike (no daemon, no DB, no UI).** ✅ **COMPLETE.** Full method, raw results, and 19 numbered findings are in **[`m0-spike-plan.md`](m0-spike-plan.md)**; the spike scripts (`as-daemon.sh`, `ra-session-wrapper.sh`) are in [`spike/`](../../spike/) and seed M1's wrapper. The findings are folded into §§3–10 above. Checklist as originally written, with outcomes:

  **Process model & TTY**
  - ⏭️ **Does `claude --remote-control` require a TTY?** *Not tested, deliberately.* This was billed as highest-leverage on the premise that a "no" collapses the host/display stack — but the §2 settled decision makes a kitty session the product, so it wouldn't. Reduced to "is a headless fallback possible", which blocks nothing.
  - ✅ Worktree created, launched via `kitty @ launch` from a real systemd unit, Remote Control active.
  - ✅ Both confirmed. Without dtach: SIGHUP death, `start` record and no `exit`. With dtach: session survived, socket intact, reattached from a fresh kitty instance and **repainted fully**.
  - ⏭️ **Reboot: not needed.** Every sub-question was answered elsewhere or substituted for by `kill -9` (which produces the same "process gone, files left behind" state). The only genuinely reboot-only question was systemd unit ordering, which is now out of scope (§8 systemd).

  **Exit & session identity**
  - ✅ Verified end to end with real claude, twice — including through a *reattached* window after kitty death.
  - ✅ **Better than the question assumed: no capture needed.** `claude --session-id <uuid>` lets the *orchestrator generate* the ID, so it is known before the process starts. Verified: generated UUID adopted, transcript written under it, `--resume <id>` restored full context.
  - ✅ Full table measured (1 / 126 / 127 / 137 / 143 and two silent-zero cases). Classification now decided in §7.

  **Kitty plumbing**
  - ✅ Yes — even with the display environment stripped entirely. And no fixed `listen_on` path is needed: discovery by glob + probe, since dead sockets fail in ~25 ms (§9).
  - ✅ More than enough — and `--var`/`user_vars` is a better key than `foreground_processes`, which `--hold` makes ambiguous (§9).
  - ✅ Yes, and it repaints. Two viewers can attach at once. The attach button is feasible.

  **Out-of-band session detection**
  - ✅ `~/.claude/sessions/<pid>.json` is an exact live registry — the join is exact, not best-effort (§8, §10). Command-line matching is unusable and actively dangerous.

  **Worktree reality check**
  - ✅ All 294 repos inventoried; three exercised. Untracked-file burden is ~15 repos, not all of them; submodules do choke (and hung); worktrees cost ~500 MB once a venv exists (§6).
- **M1 — Minimum daemon.** GitHub polling, eligibility check, worktree creation, dispatch, state in MariaDB, reconciliation on startup, health signal. No Trello. Log-only observability.
- **M2 — Web UI.** Active/queue/interrupted views, resume and cancel controls, audit log.
- **M3 — Trello.** Card → issue creation, `needs_info` handling with auto-rescan, In Progress / Done card lifecycle, loop prevention.
- **M4 — Concurrency & polish.** Caps, out-of-band session awareness, per-repo config, priority modes, notifications.
- **M5 —** _whatever survives contact with reality._

## 16. Open Questions / Parking Lot

Empirical questions ("is this possible / what happens") have moved to the M0 spike. What's left here is **thought and decision** — things no experiment will answer.

### Decisions to make
- **Worktree cleanup policy** — when? _(M0 narrowed it: "what about uncommitted changes" is answered — git refuses without `--force`, so the guard is free. Remaining: the trigger. The 499 MB-per-worktree measurement argues for issue-close rather than indefinite retention. Remember cleanup is two steps — worktree, then branch.)_
- **Kitty control socket security** — acceptable as a user-only unix socket, or worth gating further? Still open; M0 didn't change it. Note it's now doubly load-bearing, since discovery-by-probe means the daemon will happily talk to whichever kitty answers.
- **Global concurrency cap value.** The *mechanism* is settled (§10); the number isn't.
- **Dry-run row retention** — purge on startup, or keep for inspection? (§2)
- Multi-machine dispatch — not now, but does the Session Host abstraction accidentally preclude it? Worth five minutes of thought before the interface is written, not after.
- Scheduled/proactive work (nightly dependency review, etc.) — same daemon, or a separate concern?
- Branch naming convention (§6 still says TBD).
- GitHub poll interval / backoff (§4 still says TBD).

### Resolved by M0
- ~~Non-zero wrapper exit → `awaiting_review` or `failed`?~~ **Decided in §7**, on measured exit codes: 1/126/127 → `failed` (config errors, claude never ran); 128+N → `interrupted` (killed externally, likely resumable); 0 → `awaiting_review`.
- ~~Session naming convention.~~ Set **both** `--remote-control "<name>"` and `-n/--name`, encoding source + item ID (§9 Misc). The auto-derived default is not identifiable.
- ~~Per-worktree port assignment scheme.~~ Env-var injection through the same mechanism the `.env` repos already need — not a per-repo patch (§6).
- ~~Should the web UI offer an "attach" button?~~ **Feasible and cheap** — `dtach -a` reattaches and repaints, and multiple viewers are allowed. Worth building.
- ~~Does resume need `--continue` or `--resume <id>`?~~ `--resume <id>`, with the ID generated by the orchestrator (§3, §8).

### Resolved earlier (pre-M0)
- ~~Do sessions survive a daemon restart?~~ Yes — **though the original reasoning was wrong.** "kitty is the parent, not the daemon" is true but wouldn't have saved us, because systemd kills by *cgroup*, not parentage. M0 measured the real guarantee: kitty puts each session in its own `kitty-<pid>-<n>.scope`, disjoint from the daemon's service cgroup. Right answer, better reason. No `setsid` needed.
- ~~API key vs subscription auth?~~ Subscription. See Settled Decisions in §2.
- ~~GitHub Actions as a complement?~~ No. Local execution only. See Settled Decisions in §2.
- ~~Usage/session limit awareness?~~ Deferred as a possible future enhancement; the §10 concurrency cap covers the practical need.
