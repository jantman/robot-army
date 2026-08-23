# M0 Spike — Execution Plan & Results Log

Working document for the M0 spike defined in `robot-army-planning.md` §15.

**How to use this:** each experiment has a **Question**, a **Method**, **Record**, and a
**Design consequence**. Fill in the `> **RESULT:**` block as we go. When every result block is
filled, M0 is done and the answers feed directly into the spec.

**Status:** ✅ **M0 COMPLETE.** Phases 0, 2, 3, 4, 5, 6 done, including one full real-claude
dispatch end to end. **No reboot test needed** — see E4.3/F3.
**Not done, and deliberately so:** E1.1–E1.3 (TTY) and E1.4 (`--bg`) — the settled constraint
(kitty session is the product) reduced these to "is a display-less fallback possible", which is
nice-to-know and blocks nothing. Revisit only if a headless mode is ever wanted.

### Findings that change the design

| # | Finding |
|---|---|
| F2 | *Corrected* — no PATH work needed; systemd resolves the native `claude` binary |
| F9 | **Security:** trusting a repo also accepts its **committed** tool-permission pre-approvals (5 repos affected, not 30) |
| F10 | **Bug in §9:** the documented launch chain is broken — `dtach` rejects `--` |
| F11 | `--hold` matters more than assumed — without it a failed launch returns rc=0 and no diagnostic |
| F12 | Trust **and settings** resolve to the main clone, not the worktree — so no `.claude/` plumbing needed |
| F13 | Untracked-file burden is ~15 repos, not 294 — dispatch builds and tests, it doesn't run the app |
| F14 | Worktrees are **not** cheap once a venv exists: **499 MB** measured |
| F15 | **Post-create hooks must have timeouts** — a hook can hang forever and wedge `dispatching` |
| E1.5 | Trust is keyed on the main clone; check it at dispatch or sessions hang on an invisible modal |
| E2.4 | `launch --var` gives an exact reconciliation key; cmdline parsing is fragile |
| F16 | **`kitty @ launch` returning 0 is not evidence a session started** — confirm independently |
| F17 | Killing the wrapper **orphans a live claude session** — `interrupted` ≠ "nothing running" |
| F18 | Each session gets an addressable `kitty-*.scope` — a clean `terminate()`, and **disjoint from the daemon's cgroup**, so a daemon restart cannot kill sessions |
| F19 | **Sessions inherit the *kitty daemon's* environment, not the caller's** — and a stray `CLAUDE_CODE_CHILD_SESSION` silently disables transcript saving, making the session unresumable |

### F19 — environment inheritance, and a silent unresumable-session failure

**How it surfaced.** The first real-claude dispatch worked perfectly by every visible measure —
window appeared, claude ran, answered the prompt, Remote Control active, exit 0. But **no registry
entry was ever written and no transcript was saved**, so `--resume` would have been impossible. The
only evidence was one line in the status bar:

> ⚠ Transcript saving is off — inherited `CLAUDE_CODE_CHILD_SESSION` marker · restart with
> `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1` to keep future transcripts

**Root cause — the mechanism matters more than this instance.** The spike kitty had been launched
from inside a Claude Code session, so it carried `CLAUDE_CODE_CHILD_SESSION=1` (and 9 other
`CLAUDE_*` vars). `kitty @ launch` forks the child from **the kitty daemon process**, so the
session inherited kitty's environment — *not* the environment of the daemon that issued the RPC.
Relaunching kitty with those variables scrubbed fixed it completely: registry entry in ~2s,
transcript written, `--resume` working.

**The two general lessons:**

1. **The daemon's own environment does not reach dispatched sessions.** Whatever a session needs
   must be passed explicitly with `kitty @ launch --env` (verified working for
   `ROBOT_ARMY_ITEM`). Conversely, sessions inherit whatever the *user's login kitty* happens to
   carry — which the daemon does not control and cannot see without asking.
2. **This failure is silent.** Exit 0, healthy-looking session, no error, nothing in the wrapper's
   records. An orchestrator would have marked it `awaiting_review` and offered a resume button that
   could never work.

**Mitigations, in order of value:**
- **Verify the dispatch, don't assume it.** After launch, confirm `~/.claude/sessions/<pid>.json`
  exists and its `sessionId` equals the one we generated. This is the same check F16 already
  demands for "did the session actually start", and it now catches *silently degraded* sessions
  too — which makes it load-bearing rather than belt-and-braces.
- Pass `--env CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1` defensively at dispatch.
- Consider scrubbing `CLAUDE_CODE_*` from the launch environment explicitly.
- Note for §14: "session ran but produced no transcript" deserves to be a surfaced anomaly, not a
  silent state.
| E3.3 | Exit-code classification settled: 1/126/127 → `failed`; 128+N → `interrupted`; 0 → `awaiting_review` |
| E5.2 | **`~/.claude/sessions/<pid>.json` is an exact live-session registry** — §10's "best-effort" is too pessimistic |
| E6.5 | Git refuses to remove a dirty worktree without `--force` — §6's guard is free |

---

## Settled constraint — read before proposing simplifications

**A normal, interactive Claude Code session in a kitty window on this machine is the default and
primary experience. Remote Control is the away-from-desk case, not the main one.**

This is a product requirement, not an implementation detail, and it is **not open for
optimization**. The consequences, which every experiment below must respect:

- The host/display stack does **not** exist merely to solve "something must own the PTY". It
  exists because the session must be a real terminal session the human can sit down at. Even if
  a headless option works perfectly, it does not satisfy the requirement.
- Sessions appear as a window/tab **in the already-running kitty instance** — not a standalone
  kitty process, not a browser tab, not a background agent.
- Therefore `kitty @ launch` is on the critical path, and E2.1/E2.2 (socket discovery and
  reachability from systemd) are **blocking**, not optional.
- "Does claude need a TTY?" (E1.1) is therefore a question about whether a *degraded, secondary*
  host is possible — not a chance to delete the kitty path. Its leverage is real but bounded.

*(Recorded here because an earlier draft of this plan mis-ranked `--bg` and TTY-less operation as
stack-collapsing simplifications. They are not. This paragraph exists to prevent that from being
rediscovered. Worth promoting into `robot-army-planning.md` §2 "Settled decisions".)*

## Ground rules for this spike

These exist because the spike tests things that can kill the terminal we're working in.

1. **Never target the kitty instance hosting this session.** At time of writing that is
   PID `5300`, socket `/tmp/mykitty-5300`. All kitty-kill experiments target a *dedicated
   spike kitty instance* launched in its own instance group (§ Phase 0).
2. **Replicate daemon conditions, don't approximate them.** We are sitting in an interactive
   kitty session with `KITTY_LISTEN_ON`, `DISPLAY`/`WAYLAND_DISPLAY`, a controlling TTY, and a
   full shell environment. A systemd user service has *none* of that guaranteed. Every
   experiment that the daemon will eventually perform is run through
   `spike/as-daemon.sh` (a real transient systemd user unit), never straight from this shell.
   A test that passes from this prompt but fails from systemd is a test that lied to us.
3. **Benign prompts in kill tests.** Sessions we intend to SIGKILL get a prompt that does
   nothing destructive, in a throwaway worktree.
4. **The reboot experiment goes last** (Phase 4) — it ends this session. Results must be
   written to this file before rebooting.
5. **Record raw output, not summaries.** Paste actual exit codes, actual JSON, actual stderr.
   The value of M0 is the specifics.

### Scratch layout

```
~/GIT/robot-army/spike/          # throwaway scripts; committed — they seed the real wrapper
~/GIT-worktrees/<repo>/<slug>/   # worktrees under test
~/.local/state/robot-army-spike/ # dtach sockets, wrapper logs, exit-code reports
```

`~/.local/state/...` rather than `/tmp` deliberately — see E4.3, we need to know what survives
a reboot and what doesn't.

---

## Phase 0 — Prerequisites

Nothing here is an experiment; it's the bench setup. Fast.

### P0.1 — Install a persistence layer
`sudo pacman -S dtach`. (`abduco` is AUR-only; `dtach` is in `extra` and is the doc's preferred
choice anyway.) Verify: `dtach -h`.

> **RESULT:** ✅ Done. `dtach` installed at `/usr/bin/dtach`.

### P0.2 — Confirm Claude Code's actual flag surface
`claude --help` on 2.1.239. We are specifically looking for:
- `--remote-control` — exact spelling and whether it takes a name argument
- `--permission-mode` — accepted values (is `auto` real?)
- `--session-id` — **if this exists, E3.4 is answered before we start**: the orchestrator
  *generates* the session ID rather than scraping for it, which is strictly better than
  either `--continue` or capturing an ID after the fact.
- `--continue` / `--resume` semantics

> **RESULT:** ✅ Done, claude 2.1.239. Key answers:
> - **`--session-id <uuid>` EXISTS.** The orchestrator generates the UUID and passes it in.
>   **E3.4 is answered without an experiment** — downgraded to a verification.
> - `--remote-control [name]` — name is optional. Also `--remote-control-session-name-prefix
>   <prefix>` (defaults to hostname), which is directly relevant to the §9 naming convention.
> - `--permission-mode` choices confirmed: `acceptEdits`, `auto`, `bypassPermissions`, `manual`,
>   `dontAsk`, `plan`. `auto` is real.
> - `-n, --name <name>` — display name shown in the prompt box, `/resume` picker, and terminal
>   title. Distinct from the Remote Control name; we probably want to set both.
> - `-c, --continue` (cwd-scoped), `-r, --resume [id]`, plus `--fork-session` to get a new
>   session ID when resuming — relevant to §8 resume policy.
> - `--add-dir`, `--allowedTools`, `--disallowedTools`, `--tools` — the §9 guardrail surface.
> - **`--bg, --background`** and **`-w, --worktree [name]`** and `--tmux` — see findings F5–F7.
> - `--from-pr`, `--teleport`, `claude agents` subcommand — noted for later milestones.

#### Findings from P0.2

**F5 — `--bg/--background` exists, but does *not* replace the host/display stack.**
*(Corrected — an earlier draft overstated this.)* Help text: *"Start the session as a background
agent and return immediately (manage with `claude agents`)"*. A background agent has no terminal
window, so it cannot satisfy the settled constraint above: the default experience is sitting down
at a real kitty session. Even if `--bg` gives a perfectly persistent, phone-drivable session, the
kitty path still has to exist and still has to work.

What `--bg` is *actually* worth investigating for — a narrow, secondary question, not a
redesign:
- Whether it's a sane **degraded mode** for F3's no-graphical-session case (daemon running with
  no kitty to launch into), so work can still be dispatched and picked up later.
- Whether `claude agents` exposes a status/exit surface worth reusing in §14 observability.

Demoted accordingly: E1.4 is optional and low-priority. Do it if the earlier phases go fast.

**F6 — `-w, --worktree [name]` means claude can create the worktree itself.** §6 assumes the
orchestrator does `git worktree add`. Probably we still want to own it (branch naming, base
branch, post-create hooks), but worth knowing the built-in exists and what it does — especially
whether it interacts with `--session-id` and `--continue`. Low priority; note it in §6.

**F7 — `--tmux` exists** (requires `--worktree`, prefers iTerm2 native panes). Not what we want —
§3 explicitly rejects tmux's multiplexing — but it confirms the "persistent host" idea is one the
tool itself has opinions about. Ignore unless dtach disappoints.

**F8 — RISK: the workspace trust dialog.** The `--print` help says the trust dialog *"is skipped
when Claude is run in non-interactive mode"*, which means **in interactive mode it is not
skipped**. Every dispatch creates a brand-new worktree directory claude has never seen. If a
first-run trust prompt appears, every dispatched session will sit at a modal prompt doing nothing
until a human answers it from the phone — silently defeating the entire orchestrator for any new
worktree. This is not mentioned anywhere in the planning doc and it is a **dispatch-blocking
risk**. Added as E1.5: confirm whether it fires for a new worktree, and find how to pre-trust a
directory (settings file? `~/.claude.json` entry? a flag?).

### P0.3 — Scratch dirs
`mkdir -p ~/GIT-worktrees ~/.local/state/robot-army-spike/{sockets,logs,reports}`

> **RESULT:** ✅ Done. `~/GIT-worktrees/` and
> `~/.local/state/robot-army-spike/{sockets,logs,reports}` created. One worktree left in place
> for Phase 2: `~/GIT-worktrees/electronics-projects/spike-1` on branch `robot-army/spike-1`.

### P0.4 — What does the systemd user manager actually see?
```
systemctl --user show-environment
systemctl --user --failed          # it currently reports "degraded" — find out why, it may bite us later
```
This is the precondition for E2.2. If the graphical session does `import-environment`, the
service may inherit `DISPLAY`/`WAYLAND_DISPLAY` and possibly `KITTY_LISTEN_ON` — which would
make the daemon accidentally work in a way we can't rely on.

> **RESULT:** ✅ Done. KDE Plasma 6 imports a large environment into the systemd user manager.
> **Present:** `WAYLAND_DISPLAY=wayland-0`, `DISPLAY=:1`, `XAUTHORITY=/run/user/1000/xauth_DdHSmg`,
> `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`, `XDG_RUNTIME_DIR=/run/user/1000`,
> `SSH_AUTH_SOCK`, `XDG_CURRENT_DESKTOP=KDE`, `XDG_SESSION_TYPE=wayland`.
> **Absent:** `KITTY_LISTEN_ON` (expected — per-terminal, not session-wide).
> **PATH is `/home/jantman/scratch/tfenv/bin:/home/jantman/scratch/pkenv/bin:/home/jantman/bin:/home/jantman/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/bin:/usr/lib/jvm/default/bin:/usr/bin/site_perl:/usr/bin/vendor_perl:/usr/bin/core_perl:/home/jantman/go/bin`
> — note `~/.asdf/shims` is NOT on it.** See findings F1–F3 below.
> `degraded` state is unrelated: two failed autostart units (`google-chrome-service`,
> `pulseaudio`). Not a concern for us.

#### Findings from P0.4 that change the plan

**F1 — The daemon will have a display connection.** Because Plasma imports the environment, a
systemd user service can talk to Wayland/X11 and therefore spawn a GUI kitty directly. This makes
**E2.0 likely to succeed**, which in turn makes the kitty control socket (E2.1/E2.2) optional
rather than blocking. Confirm rather than assume, but plan for it.

**F2 — Non-issue, and the daemon actually gets a *better* claude than the shell does.**
*(Corrected after investigation.)* `~/.local/bin` **is** on the systemd user manager's PATH, and
`~/.local/bin/claude` is the puppet-managed native install
(`-> ~/.local/share/claude/versions/2.1.239/claude`). Verified: under a systemd-like PATH,
`claude` resolves to `/home/jantman/.local/bin/claude`, 2.1.239. No `Environment=PATH=` needed.

The asdf shim is **vestigial** — a leftover from a `npm -g` install under asdf nodejs 22.22.0
(shim dated Feb 22). It only shadows the native binary in *interactive* shells, where
`~/.asdf/shims` sorts first. It currently resolves to the same binary by accident: `asdf current
nodejs` is `system`, so `asdf exec` falls through to PATH and lands on the native install.

That accident is one `asdf local nodejs <version>` away from breaking, and it would break the
interactive shell while the daemon kept working — a confusing failure mode. **Recommend deleting
`~/.asdf/shims/claude`** so both paths resolve identically and by the same mechanism. Your call;
it's cleanup, not a blocker.

Use the `~/.local/bin/claude` symlink, **not** the versioned path — the installer updates in
place and a pinned version path would break on upgrade.

**F3 — Daemon start ordering.** ✅ **RESOLVED — JA's call: manual start after login is
acceptable.** The imported environment exists only *after graphical login*, so a service started
at boot under `loginctl enable-linger` would have no `WAYLAND_DISPLAY`/`DISPLAY` and no kitty to
launch into. Rather than solve that with unit ordering, the daemon is simply **started by hand
once the desktop is up**, which makes the failure mode structurally impossible.

Consequence: **M0 needs no reboot test** (see E4.3), and `PartOf=graphical-session.target` /
`After=` MariaDB become optional M1+ polish if auto-start is ever wanted. Note the daemon should
still *check* its preconditions at startup (kitty socket reachable, DB reachable) and fail loudly
if started too early — cheap, and it converts a confusing silent failure into a clear one.

**F4 — `--scrub` changes meaning.** Since the real daemon *will* inherit the imported
environment, the scrubbed run is no longer "what the daemon will have". Reinterpret it as a
dependency probe: what does the launch chain *minimally* require? Useful for F3 (predicting the
no-graphical-session case), but the un-scrubbed run is now the realistic baseline.

### P0.5 — Build `spike/as-daemon.sh`
A wrapper that runs a command the way the eventual systemd unit will:

```sh
systemd-run --user --pipe --wait --collect --service-type=exec \
    --unit="ra-spike-$$" --working-directory=/ -- "$@"
```

`--pipe` gives us stdout/stderr and the exit code; `--service-type=exec` plus no TTY is the
honest reproduction. Add a `--scrub` mode that additionally uses `env -i` to strip the
inherited environment down to `HOME`/`PATH`/`XDG_RUNTIME_DIR`, so we can tell the difference
between "works" and "works only because it inherited something".

> **RESULT:** ✅ Done — `spike/as-daemon.sh`. Verified against a real transient unit:
>
> | Probe | Result |
> |---|---|
> | Controlling TTY | **none** — `tty: not a tty`, stdin/stdout both non-TTY. Faithful. |
> | `WAYLAND_DISPLAY` / `DISPLAY` (un-scrubbed) | `wayland-0` / `:1` — **present**, confirms F1 |
> | `KITTY_LISTEN_ON` | **unset** — confirms the socket path must come from config/discovery |
> | `claude` on PATH | `/home/jantman/.local/bin/claude` — the **native** binary, confirms F2 |
> | `kitty`, `dtach` on PATH | `/usr/bin/kitty`, `/usr/bin/dtach` |
> | Same under `--scrub` | `claude` and `kitty` still resolve; `WAYLAND_DISPLAY` unset |
> | Exit code propagation | `exit 42` → `as-daemon: exit=42` → caller sees 42. **Faithful for E3.3.** |
>
> Note `--scrub` still finds `claude`/`kitty` because the minimal PATH includes `~/.local/bin`
> and `/usr/bin`. What `--scrub` actually removes is the *display* environment, which is the
> useful part: it models the F3 no-graphical-session case.

### P0.6 — Build `spike/ra-session-wrapper.sh`
The §9 session wrapper, spike edition. Takes an item ID and a command; runs the command,
captures `$?`, and instead of POSTing to a daemon API (which doesn't exist yet) appends a JSON
line to `~/.local/state/robot-army-spike/reports/exits.jsonl`:

```json
{"item":"spike-1","pid":1234,"started":"...","ended":"...","exit":0,"signal":null}
```

Also records: the PID of claude, the cwd, and any session ID it can find. This is the artifact
that makes E3.1–E3.4 measurable, and it becomes the real wrapper in M1.

> **RESULT:** ✅ Done — `spike/ra-session-wrapper.sh`. Emits `start` and `exit` records to
> `~/.local/state/robot-army-spike/reports/exits.jsonl`, plus a per-item log. Verified:
>
> | Smoke test | Result |
> |---|---|
> | `/bin/true` | `"exit":0,"signal":null` |
> | `/bin/false` | `"exit":1,"signal":null` |
> | SIGTERM'd child | `"exit":143,"signal":15` — **signal decode works**, which is what E3.3 needs to separate "crashed" from "a human killed it" |
> | Dry-run stand-in, `fail` | `"exit":42,"dry_run":true` |
> | Inside a systemd unit, stdin piped | works, exit propagates |
>
> `--session-id` is parsed out of argv and recorded, so every report is self-describing.
> No `exec` for the payload — documented inline, because §9 suggests `exec` and doing so would
> destroy the wrapper's only reason to exist.
>
> **Dry-run support added per the new requirement (see planning doc §2).** `RA_DRY_RUN=1` runs an
> interactive stand-in instead of claude, accepting `exit` (→0), `fail` (→42), and `hang`
> (sleep forever, for E4.2's kill tests). **This directly improves Phase 2:** the
> `kitty @ launch → dtach → wrapper → claude` chain has four moving parts, and testing it with a
> real claude session makes every failure ambiguous, costs subscription usage, and throws away a
> real session on each kill test. Prove the plumbing with the stand-in first, then swap in claude.

---

## Phase 1 — Process model & TTY

**Scoping note.** §15 calls the TTY question "highest-leverage" on the theory that a `no` answer
collapses the host/display stack. Under the settled constraint above, it doesn't — the kitty path
is required regardless. What E1.1 actually buys us is narrower but still worth having:

- whether a **degraded, display-less host** is possible at all (F3: daemon up, no graphical
  session), and
- whether `dtach` is *mandatory* (claude needs a PTY owner) or merely *preferred*.

**E1.5 (workspace trust) is the item in this phase that can actually block dispatch**, and is
the highest-priority thing in the whole spike alongside Phase 2. Do E1.5 first.

### E1.1 — Does `claude --remote-control` require a TTY?

**Method.** Three runs, each via `spike/as-daemon.sh`, in a throwaway worktree:

| Run | stdin | Expectation to test |
|---|---|---|
| a | `-p "what is 2+2"` | baseline — print mode is known-headless, proves the harness works |
| b | `--remote-control ... --permission-mode auto "<prompt>"`, stdin `/dev/null` | does it start? |
| c | same as (b) but stdin held open (`sleep infinity \| claude ...`) | does it *stay* running? |

**The distinction that matters** and which §15 doesn't spell out: "runs without a TTY" and
"stays alive without a TTY awaiting Remote Control input" are different questions. A
non-interactive claude may read EOF on stdin and exit immediately, which would look like
success in a log and be useless in practice. Run (c) isolates that.

**Record.** Exit code, stderr verbatim, whether the process persists, whether the session
appears in the Claude app.

**Design consequence.** Yes → `direct` host is viable; kitty becomes optional display;
`survives_display_death` is trivially true. No → the `dtach` host is mandatory, not preferred.

> **RESULT:**

### E1.2 — Is a TTY-less session fully drivable from Remote Control?
If E1.1 (b) or (c) started a session, drive it from the phone: send a prompt, approve a tool
use, observe output. A session that starts but can't render approval prompts without a TTY is
a failed result even though the process is alive.

**Record.** Does tool-approval work? Does output render? Any degradation vs the kitty case?

> **RESULT:**

### E1.4 — `--bg/--background` as a degraded-mode option *(optional, low priority)*

**Does not affect the main design** — see F5 and the settled constraint. Run only if time
allows. The question is narrow: is this a usable fallback when no kitty is available, and does
`claude agents` expose anything worth reusing for observability?

**Method.**
1. `claude --bg --remote-control "ra-bg-1" --session-id <uuid> --permission-mode auto "<prompt>"`
   in a worktree, launched via `spike/as-daemon.sh`. Does it return immediately? What's the exit
   code of the launching command, and what's left running?
2. `claude agents` — what does it report? Is there a stable ID, a status, an exit state?
3. **The decisive question: is a background agent drivable from the phone via Remote Control?**
   Open the Claude app, find the session, send a follow-up prompt, approve a tool use.
4. If drivable: what happens on reboot? Is there any reattach concept? Does `claude agents`
   survive a logout?

**Record.** Whether it returns immediately; what `claude agents` shows; whether Remote Control
can drive it; whether it's interactive at all or fire-and-forget autonomous.

**Design consequence.** If a background agent is a *persistent, human-drivable* session:
`dtach` is unnecessary, kitty becomes purely optional decoration, the §9 session wrapper loses
its main reason to exist (`claude agents` reports status), and §3's Session Host abstraction
shrinks to almost nothing. If it's autonomous fire-and-forget, it's irrelevant to this design and
we proceed with the dtach plan unchanged. Either answer is worth having in the first hour.

> **RESULT:**

### E1.5 — Does the workspace trust dialog fire on a fresh worktree?

**This is a dispatch-blocking risk.** See finding F8.

#### What's already known (researched, before running the experiment)

**The mechanism is `~/.claude.json` → `projects["<key>"].hasTrustDialogAccepted` (boolean).**
Confirmed present: 68 project entries, e.g. `/home/jantman/GIT` = `true`,
`/home/jantman/GIT/it-committee` = `false`.

**There is no supported bypass.** Searched the 2.1.239 binary for `CLAUDE_CODE_*` env vars
matching TRUST/WORKSPACE/SKIP/BYPASS/DANGER and for settings keys like `trustedDirectories` /
`skipTrustDialog` / `autoTrust`: **nothing.** `--dangerously-skip-permissions` governs tool
permissions, which is a different gate. So pre-seeding `~/.claude.json` is the only lever.

**The trust key is not necessarily the literal cwd.** The binary contains
`getPersistedTrustKeyForPath`, `getWorkspacePersistedTrustKey`, `isTrustKeyPersistedTrusted`,
`isWorkspacePersistedTrusted`, `cascadeTrusted`, and `hasWorkspaceTrust`. There is a
path → trust-key mapping and some notion of cascading. **This is the crux:** if the key for a
worktree resolves to the git common dir (i.e. the main clone at `~/GIT/<repo>`), then worktrees
of an already-trusted repo inherit trust and F8 evaporates. If it's the literal worktree path,
every dispatch needs a pre-seed step. Do not guess — writing the wrong key silently does nothing
and the session hangs at a prompt, which is the exact failure we're trying to prevent.

Also present: `isUntrustedAutomountPath`, `isUntrustedUncPath` (path-class blocks — irrelevant
here but note if worktrees ever live on a network mount), and telemetry events
`tengu_trust_dialog_shown` / `tengu_trust_dialog_accept`.

**Method.**
1. `git worktree add` in a repo whose main clone is **already trusted** (e.g. `biweeklybudget`,
   `privatepuppet`). Snapshot `~/.claude.json` first.
2. Launch an interactive session there under a PTY we can observe and kill:
   `script -q -c 'claude --permission-mode manual' /dev/null`, with a timeout. **Do not answer
   the dialog** — we want to see whether it appears, then kill it.
3. Diff `~/.claude.json`. What key, if any, was created? Literal worktree path, or the main
   clone's path?
4. Repeat in a worktree of an **untrusted** repo, to separate "inherited trust" from "never
   prompts for worktrees".
5. If a pre-seed is needed: write the key, relaunch, confirm **no prompt** — the verification
   that matters.

**Record.** Does it fire? The exact key that gets written. Whether trust cascades from the main
clone, from `~/GIT`, or not at all.

**Design consequence.** If cascading works: nothing to build. If not, dispatch needs a
"trust the worktree" step in the §6 worktree-creation path, plus two things the planning doc
doesn't account for:
- **Write-safety.** `~/.claude.json` is read-modify-written by every running claude process. The
  daemon editing it concurrently with live sessions risks clobbering unrelated state (68 projects'
  worth). Needs a safe write strategy — and note `~/.claude/backups/` exists, suggesting Anthropic
  already treats this file as corruption-prone.
- **Growth and cleanup.** One entry per ephemeral worktree, forever. Worktree cleanup (§16) must
  also delete the trust entry, or this file grows without bound.

Security note: pre-trusting directories the orchestrator itself created from a trusted repo is
defensible. Blanket-trusting `$HOME` is not — note that `/home/jantman` is currently `false`,
and it should stay that way.

> **RESULT:** ✅ **ANSWERED. Trust is keyed on the main clone, not the worktree — and the dialog
> does block.** Two tests, both launched under a scrubbed `env -i` (no inherited `CLAUDE*` vars),
> stdin on a PTY via `script`, dialog deliberately unanswered:
>
> | Repo | Main clone trust | Worktree | Trust dialog? |
> |---|---|---|---|
> | `electronics-projects` | `true` | `~/GIT-worktrees/electronics-projects/spike-1` | **No** — went straight to the prompt |
> | `it-committee` | `false` | `~/GIT-worktrees/it-committee/spike-1` | **Yes** — blocked on a modal, timed out at 25s |
>
> **No `projects` entry was ever created for a worktree path** — `.claude.json` project keys came
> back byte-identical to the pre-test snapshot in both runs. So the trust key resolves through git
> to the main clone (`getPersistedTrustKeyForPath` / `cascadeTrusted` confirmed by behavior).
>
> **Consequences, all favourable:**
> - **One trust entry per repo, not per worktree.** The unbounded-growth and cleanup concerns
>   above are void. Worktree cleanup has nothing to undo.
> - The `~/.claude.json` concurrent-write hazard shrinks to a rare, per-repo, one-time operation
>   at repo-onboarding time — not something that happens on every dispatch.
> - **Dispatch precondition:** `projects["<main clone path>"].hasTrustDialogAccepted == true`.
>   The daemon should *check* this at dispatch and fail the item to `failed` with a clear message
>   rather than launching a session that will silently hang on a modal. Cheap, and it converts the
>   worst failure mode (invisible hang) into a visible error.
> - Auto-mode notices are global and already dismissed
>   (`hasSeenAutoModeEntryWarning: true`, `hasSeenAutoDefaultNotice: true`), so
>   `--permission-mode auto` adds no additional first-run dialog. Verified by running the second
>   test with `--permission-mode auto` specifically.

#### F9 — SECURITY: the trust gate is also the gate on repo-committed permission pre-approvals

The `it-committee` dialog said, verbatim:

> ⚠ This folder pre-approves 3 tool permissions in `.claude/settings.local.json`:
> WebSearch, WebFetch(domain:developer.neoncrm.com), and WebFetch(domain:raw.githubusercontent.com)
> These will apply without asking. Only proceed if you trust this configuration.

**Trusting a repo silently accepts whatever tool permissions that repo has committed to
`.claude/settings.local.json`.** This is not a detail — it interacts directly with §4, which
allows *"a whitelist of repos that aren't mine"*. For a repo you don't control, a commit can add
pre-approved permissions, and a robot-army dispatch would honour them without ever asking. The
§4 author-check protects the *issue* path; it does nothing about the *repo contents* path.

**Scope, measured across all 294 repos in `~/GIT` (narrows this considerably):**

| Settings file | Count | Reaches a worktree? |
|---|---|---|
| `.claude/settings.local.json`, **untracked** | 28 | Yes — via the main clone, see F12 |
| `.claude/settings.local.json`, **committed** | 1 (`workshop-inventory-tracking`, your own) | Yes |
| `.claude/settings.json` **committed** (shared settings) | 4 | Yes |

`.claude/settings.local.json` is globally gitignored for you (`~/.config/git/ignore:139`), so in
practice local settings stay local — **the exposure is via *committed* settings files**, which is
a much smaller and more precise target than "any repo with a `.claude/` dir".

Implications to decide in the spec:
- Trusting a repo for robot-army must be a **deliberate per-repo onboarding step**, never
  automatic — same philosophy as the §4 human gate on dispatch.
- The thing to watch is **committed** settings (`.claude/settings.json`, and any committed
  `settings.local.json`) in repos you don't control. Untracked local files are your own and pose
  no supply-chain risk.
- Record the committed settings file's hash at onboarding and re-check at dispatch, so a *change*
  to a repo's pre-approved permissions re-triggers human review. Cheap; closes the hole.
- For non-owned repos, consider `--strict-mcp-config` plus an explicit `--disallowedTools`.

#### F12 — Settings resolve to the main clone too, not the worktree

Tested directly: a fresh worktree of `it-committee` contained **no `.claude/` directory at all**
(the file is gitignored, so it cannot transfer), yet the trust dialog listed *exactly* the three
permissions from the **main clone's** `~/GIT/it-committee/.claude/settings.local.json`. The
worktree's `--git-common-dir` is `/home/jantman/GIT/it-committee/.git`.

So **both** trust *and* project settings resolve through git to the main clone. Two consequences:

- **Good news for §6.** A dispatched session in a worktree automatically inherits your existing
  per-repo permission allowlists and CLAUDE.md-adjacent config. The post-create hook does **not**
  need to copy or symlink `.claude/` — one fewer item on the untracked-files pain list, and it
  means dispatched sessions behave like your interactive ones without extra work.
- It also means a worktree cannot be given *narrower* permissions than the main clone by simply
  omitting files. If robot-army wants tighter guardrails for a dispatched session than you use
  interactively, that has to come from the command line (`--disallowedTools`, `--settings`), not
  from worktree contents.

### E1.3 — What does `direct` cost us?
If E1.1/E1.2 pass: with stdout redirected to a log file, is that log usable, or is it ANSI
soup? Determines whether `direct` needs `script(1)`-style PTY allocation anyway (which would
put us right back at needing a PTY owner).

> **RESULT:**

---

## Phase 2 — Kitty plumbing under daemon conditions

### P2.0 — Launch the dedicated spike kitty instance
Do this before any Phase 2 experiment. It must be a *separate process* from the one running
this session so we can kill it freely.

```
kitty --instance-group ra-spike \
      -o allow_remote_control=yes \
      -o listen_on=unix:/tmp/ra-spike \
      --detach
```

Verify it's a distinct PID from `5300`, and note the socket path it actually created
(`ls -l /tmp/ra-spike*`).

> **RESULT:** ✅ Works. `kitty --instance-group ra-spike -o allow_remote_control=yes
> -o listen_on=unix:/tmp/ra-spike --title "..."` under `setsid` gave a separate instance.
> **The PID was appended: `/tmp/ra-spike-2306121`.** Killing it never affected the main
> instance (5300) in any test. Two spike instances were used over the session; both cleaned up.

### E2.0 — Fallback only: launching a standalone kitty

**Decided, not open:** the session must appear as a window/tab **in the existing kitty
instance**, not as a separate standalone kitty process. That requires `kitty @ launch`, so
**E2.1 and E2.2 are blocking work, not optional.** Keep this experiment only as a documented
fallback for the degraded case where no kitty is running (see F3 — daemon started without a
graphical session).

Note the silver lining of this decision: with `kitty @ launch`, the daemon never needs a display
connection of its own. It writes to a unix socket and the already-running kitty does all the
display work. So the daemon's environmental requirement is just *"the socket path"*, not the
whole Wayland/X11 environment — which makes F3's no-graphical-session case fail cleanly
("no kitty socket, can't dispatch") rather than mysteriously.

The fallback shape, if ever needed:

```
kitty --title "ra-spike-1" -e dtach -A <sock> -- <wrapper> claude ...
```

No control socket, no `allow_remote_control`, and the §9 security tradeoff ("an always-listening
kitty control socket lets anything that can reach it run arbitrary commands in my terminal",
which §16 flags as needing a deliberate answer) simply doesn't arise.

**Method (only if we ever need the degraded path).** From `spike/as-daemon.sh`, spawn the above
and confirm a window appears — P0.4 showed the imported environment includes `WAYLAND_DISPLAY`
and `DISPLAY`, so this should work while a graphical session exists.

> **RESULT:** _Deferred — not needed under the "window in existing kitty" decision._

### E2.1 — Can we get a stable, daemon-predictable `listen_on` path?

**Why this is a real problem, confirmed:** the current config says
`listen_on unix:/tmp/mykitty` but the live socket is `/tmp/mykitty-5300`. Kitty appended the
PID. A daemon with no knowledge of that PID cannot address the socket.

**Scoping note (measured):** there is currently exactly **one** kitty instance (PID 5300,
launched from `~/.kitty-session.kitty` at login), one socket, and `/tmp` is tmpfs. So in the
normal case a glob returns exactly one match and prediction isn't needed — discovery is trivial.
The ambiguity that actually bites is **stale sockets**, not concurrent instances: a kitty killed
with `-9` leaves its socket file behind until the next reboot clears tmpfs. Verify that
connecting to a stale socket fails fast (expected `ECONNREFUSED`, not a hang) — if so, option 4
below is ~5 lines and this whole item is closed.

**Method.** Test, in the spike instance, in order of preference:
1. `listen_on=unix:/tmp/ra-spike-fixed` — does it still append the PID? (expect yes)
2. Explicit placeholder: `listen_on=unix:/tmp/ra-spike-{kitty_pid}` — does including the
   placeholder suppress the automatic suffix? (kitty documents the substitution; the
   interaction with auto-appending is what we're checking)
3. Abstract socket: `listen_on=unix:@ra-spike` — Linux abstract namespace, does the suffix
   still apply?
4. Fallback: **discovery instead of prediction** — daemon globs `/tmp/mykitty-*`, runs
   `kitty @ --to <sock> ls` against each, picks the live one. Test that a stale socket file
   from a dead kitty fails fast rather than hanging.
5. Fallback: kitty `startup_session` / launch wrapper writes the real path to
   `~/.local/state/robot-army/kitty-socket` at kitty start; daemon reads that file.

**Record.** Which options yield a fixed path; the exact config syntax that worked (§15 asks us
to confirm this while we're here); and if none do, which fallback we're adopting.

**Design consequence.** Feeds §9 "Kitty specifics" and the config schema. Also: option 4/5
means the daemon needs a "no kitty available" degraded mode — worth knowing now.

> **RESULT:** ✅ **Resolved — use discovery (option 4), not prediction.** The PID suffix is
> unavoidable in practice (confirmed twice: `/tmp/ra-spike-2306121`, `/tmp/ra-spike2-2323485`),
> but it doesn't matter, because **probing a dead socket fails immediately**:
>
> | Case | Result | Time |
> |---|---|---|
> | Nonexistent path | `connect: no such file or directory` | **14 ms** |
> | Stale socket file, no listener | `connect: connection refused` | **25 ms** |
>
> So the daemon globs the configured pattern (e.g. `/tmp/mykitty-*`), runs `kitty @ --to <s> ls`
> against each candidate, and takes the one that answers. No hang risk, no stale-socket
> ambiguity, and it tolerates the user restarting kitty. Config stores the *pattern*, not a path.
> Options 1–3 (fixed path via `{kitty_pid}` / abstract socket) were not needed and remain untested.

### E2.2 — Can a systemd user service reach the kitty control socket?

**Method.**
```
spike/as-daemon.sh          kitty @ --to unix:/tmp/ra-spike-<pid> ls
spike/as-daemon.sh --scrub  kitty @ --to unix:/tmp/ra-spike-<pid> ls
```
The `--scrub` run is the one that matters — it proves reachability doesn't depend on inherited
graphical-session environment.

**Record.** Exit code and output of both. Any error about `allow_remote_control`, socket
permissions, or missing `KITTY_LISTEN_ON`.

**Design consequence.** §15 asks this directly. Also determines whether the daemon needs
`--to` passed explicitly in every invocation (almost certainly yes) and whether `kitty` needs
to be on the service's `PATH`.

> **RESULT:** ✅ **YES — and it needs less than expected.** `kitty @ --to unix:/tmp/ra-spike-<pid> ls`
> from a real transient unit returned **rc=0 with full JSON**, both un-scrubbed **and under
> `--scrub`** (no `WAYLAND_DISPLAY`, no `DISPLAY`, minimal PATH).
>
> `kitty @` is just a unix-socket client, so the daemon needs **only the socket path** — not a
> display connection; the already-running kitty does all the display work. `kitty` resolves at
> `/usr/bin/kitty` under `--scrub`, and `claude` at `~/.local/bin/claude` (F2), so no PATH work is
> needed. `--to` must be passed explicitly every time, since `KITTY_LISTEN_ON` is absent (P0.4).

### E2.3 — Full launch chain, driven as the daemon
```
spike/as-daemon.sh kitty @ --to <sock> launch \
    --type=tab --cwd <worktree> --title "ra-spike-1" --var ra_item=spike-1 -- \
    dtach -A ~/.local/state/robot-army-spike/sockets/spike-1.sock -- \
    ~/GIT/robot-army/spike/ra-session-wrapper.sh spike-1 \
    claude --remote-control "ra-spike-1" --permission-mode auto "<benign prompt>"
```

**Record.** Does the window appear in the spike kitty? Does claude start? Does the `kitty @`
client exit immediately (§9 claims it does — verify)? Is the session reachable from the phone?
Note the full process tree: `ps -ef --forest` around the kitty PID.

> **RESULT:** ✅ **Chain works end to end** (with the dry-run stand-in, not yet real claude).
> `kitty @ launch` from a systemd unit created the tab, dtach created its socket
> (mode `srwx------`, user-only — good), and the wrapper wrote its `start` record with the correct
> `cwd`, `session_id`, and `dry_run: true`.
>
> **✅ §9's "the `kitty @` client exits immediately" claim is CONFIRMED** — the transient unit went
> `inactive` while the session kept running.
>
> #### 🐛 F10 — BUG IN THE PLANNING DOC: the §9 launch chain is wrong as written
>
> §9 documents:
> ```
> dtach -A <sockdir>/<item-id>.sock -- robot-army-session-wrapper ...
> ```
> **dtach does not accept a `--` separator** and rejects it outright:
> ```
> /usr/bin/dtach: Invalid option '--'
> ```
> Correct form — no separator:
> ```
> dtach -A <sockdir>/<item-id>.sock <wrapper> <item-id> -- <cmd...>
> ```
> Worth fixing in §9 before anyone copies it into code. (Our wrapper keeps its own `--` because
> *it* parses one; dtach must not see it.)
>
> #### F11 — `--hold` is worth more than §9 suggests
>
> Without `--hold`, the failed launch above closed its window instantly, leaving **rc=0, a valid
> window id, and no diagnostic anywhere** — indistinguishable from success at the API level. With
> `--hold` the error was right there in the window. §9 calls it "useful for debugging, probably not
> on by default"; the stronger recommendation is: **turn it on whenever a dispatch fails, and
> during early operation.** A dispatch that silently half-succeeds is the worst failure mode.

### E2.4 — Does `kitty @ ls` report enough to identify a session through `dtach`?

**Method.** `kitty @ --to <sock> ls` while E2.3's session runs. Inspect the JSON for the
window: `foreground_processes` (PIDs + cmdlines), `title`, and `user_vars`.

**The thing to check that §15 doesn't mention:** `launch --var ra_item=spike-1` sets a window
user variable that `kitty @ ls` reports back. If that works, correlation is an exact key lookup
rather than cmdline parsing through two layers of `dtach`/wrapper indirection. That is a much
better reconciliation primitive — confirm it.

**Record.** The raw JSON for the window. Specifically: does `foreground_processes` show
`dtach`, the wrapper, or `node .../cli.js`? Can we walk from the reported PID to the claude
process via `/proc/<pid>/task/*/children` or `pgrep -P`?

**Design consequence.** §8 reconciliation cross-check; §16 "attach button" feasibility.

> **RESULT:** ✅ **`kitty @ ls` reports far more than §8 assumes — three independent correlation
> keys, none requiring cmdline parsing.** Window object keys include: `id`, `pid`, `cwd`, `env`,
> `user_vars`, `title`, `session_name`, `cmdline`, `foreground_processes`, `at_prompt`,
> `in_alternate_screen`, `last_cmd_exit_status`, `created_at`.
>
> Observed for our session:
> ```json
> { "title": "ra-spike-1", "pid": 2321364,
>   "cwd": "/home/jantman/GIT-worktrees/electronics-projects/spike-1",
>   "user_vars": { "ra_item": "spike-1" }, "last_cmd_exit_status": 0 }
> ```
> | Key | Set by | Verdict |
> |---|---|---|
> | `user_vars.ra_item` | `launch --var ra_item=spike-1` | **Best.** Exact key lookup, survives the dtach/wrapper layers, no parsing |
> | `env.ROBOT_ARMY_ITEM` | `launch --env ROBOT_ARMY_ITEM=spike-1` | Also works; useful as a cross-check and readable from `/proc/<pid>/environ` |
> | `cwd` | the worktree | Works, and is the natural join key to the work item |
> | `foreground_processes[].cmdline` | — | Works but **fragile, don't rely on it** — see below |
>
> **Use `--var`.** §8's plan to walk `foreground_processes` is viable but brittle: with `--hold`,
> kitty inserts a `kitten run-shell --shell=/bin/bash ...` layer that carries the *entire* command
> in its own argv, so the tree is deeper than expected and the same string appears at multiple
> depths. I hit exactly this: a `pgrep -f 'ra-session-wrapper.sh spike-1'` matched the
> `kitten run-shell` layer instead of the wrapper, and briefly produced a wrong conclusion.
> Depth-dependent parsing would encode that fragility permanently.

### E2.5 — Kitty death *without* `dtach` → SIGHUP
Launch claude directly under the spike kitty (no dtach in the chain), then kill the spike kitty
instance. Confirm claude dies. Confirm no exit is reported by the wrapper (this is the §8
"nothing pushes, reconciliation must sweep" case — we need to *see* it happen).

**Record.** Did claude die? What did the wrapper write, if anything? Was there a
`exits.jsonl` line at all?

> **RESULT:** ✅ **Confirmed exactly as §8 predicts.** Wrapper launched directly under kitty with
> no dtach; killed the spike kitty. The wrapper **died via SIGHUP**, and `exits.jsonl` contains a
> `start` record and **no `exit` record**.
>
> This is the §8 "nothing pushes, so a periodic sweep is mandatory" case, now demonstrated rather
> than assumed. The observable signature of this state is precisely: *a `start` with no matching
> `exit`, and no live process.* That's what the reconciler must key on.

### E2.6 — Kitty death *with* `dtach` → survival + reattach
Kill the spike kitty while E2.3's session runs.

**Record.**
- Is the claude process still alive? Its new parent (reparented to init/systemd?).
- Does the dtach socket remain?
- `dtach -a <sock>` from a **fresh** kitty window — does it reattach with usable rendering?
  (Watch for a garbled screen — dtach doesn't replay scrollback; check whether claude redraws.)
- Does Remote Control from the phone still work while nothing is attached?

**Design consequence.** This is the single decision behind `KittyDisplay(DtachHost(...))`. If
reattach renders badly, the "attach" button in §16 is worth less than it looks.

> **RESULT:** ✅ **dtach does its job. `KittyDisplay(DtachHost(...))` is validated.**
>
> Killed the spike kitty while the session ran:
> - **Wrapper survived** (PID 2321380, state `Ss+`), still running its payload.
> - **dtach master survived** (PID 2321379), reparented to PID 4047 (the session/user manager).
> - Only the *attached client* died with kitty — the master holds the PTY, exactly as designed.
> - **dtach socket persisted**, and **no exit was reported** (correct — the session didn't end).
> - Reattach from a **freshly launched kitty instance** via `dtach -a <sock>` worked first try.
>
> **✅ Repaint on reattach: RESOLVED — it works.** *(This corrects an earlier caveat in this
> document.)*
>
> The first run of this test showed a blank window on reattach, and I recorded it as a risk to the
> §16 attach button. **That was an artifact of the dry-run stand-in, not a dtach limitation.**
> `dtach -a`'s default redraw method is `ctrl_l` (per `dtach --help`) — it sends Ctrl-L to the
> program on attach. The stand-in is a dumb `read` loop that ignores Ctrl-L, so nothing repainted.
>
> Re-tested with a **real claude session**: killed kitty, launched a fresh kitty instance, attached
> with `dtach -a`, and the full TUI came back immediately — prompt, prior response, status bar,
> Remote Control line. No keystroke or resize needed.
>
> **The §16 attach button is straightforwardly feasible.** (If a future TUI ever *doesn't*
> repaint, `dtach -a -r winch` is the documented alternative.)
>
> Also observed: the registry `status` field moved `busy → idle` when the display died, so
> §13 can show display-less-but-alive sessions accurately.

### E2.7 — `multi_viewer` capability
Attach two kitty windows to the same dtach socket simultaneously. Does dtach allow it, refuse
it, or steal?

**Record.** Behavior. Sets the `multi_viewer` flag value in §3.

> **RESULT:** ✅ **`multi_viewer = true`.** Two kitty windows attached to the same dtach socket
> simultaneously (1 master + 2 clients). Both mirrored identical content, and text sent to
> viewer 1 appeared in viewer 2 and was acted on by the payload. No stealing, no refusal.
>
> Also incidentally confirms the redraw behavior above: content became visible in both viewers
> only once input caused output.

---

## Phase 3 — Exit & session identity

### E3.1 — Exit code propagation through the full chain
With the E2.3 chain running, exit claude normally. Confirm the wrapper's `exits.jsonl` line
appears with the right code, i.e. that `dtach` and `kitty @ launch` don't swallow it.

**Record.** The JSONL line. Also: does dtach exit when its child exits, and does its socket get
cleaned up automatically or leak?

> **RESULT:** ✅ **Exit code propagates cleanly through the whole chain.** Drove the payload to a
> clean exit from an attached viewer:
> ```json
> {"event":"exit","item":"spike-1","pid":2321380,
>  "cwd":"/home/jantman/GIT-worktrees/electronics-projects/spike-1",
>  "session_id":"deadbeef-0000-4000-8000-000000000001","dry_run":true,
>  "started":"2026-08-23T15:48:04Z","ended":"2026-08-23T15:49:41Z",
>  "exit":0,"signal":null}
> ```
> - **dtach master exited** when its payload exited — no orphan.
> - **Socket was removed automatically.** No leak, nothing to clean up in the happy path.
>   (Reboot-orphaned sockets are still an open question — E4.3.)
> - Neither `kitty @ launch` nor `dtach` swallowed or altered the exit code.
>
> Note this was the stand-in, not claude. E3.2 (`/exit` via Remote Control → 0) still needs the
> real thing, but the *transport* is now proven, so a failure there would be claude's behavior,
> not the chain's — which is exactly the isolation the dry-run stand-in was built to give us.

### E3.2 — `/exit` via Remote Control → exit 0
§7 makes exit 0 *the discriminator* between `awaiting_review` and `interrupted`, so this needs
to be verified through the whole stack, not just a bare claude.

**Method.** From the phone, `/exit`. Watch `exits.jsonl`.

**Record.** Exit code. Time between `/exit` and the report. Whether the kitty window closes or
lingers.

> **RESULT:** ✅ **VERIFIED end-to-end with real claude** — `/exit` through the full
> `kitty → dtach → wrapper` chain produced `{"event":"exit","exit":0,"signal":null}`, claude gone,
> dtach socket auto-removed, registry entry removed. Done twice (once through a *reattached*
> window after kitty death, which also exercises E2.6). The §7 discriminator is sound.
>
> Practical detail for any "cancel session" control that drives input: `kitty @ send-text` needs
> **`\r`** to submit. Sending `\n` typed `/exit` into the prompt without submitting it, and the
> session sat there — a silent no-op that looked like `/exit` had failed.
>
> *(Original reasoning, now superseded by direct measurement, retained for the record:)*
> - *Confirmed by JA from prior usage:* `/exit` via Remote Control causes claude to finish up and
>   exit **0**. (Also already asserted in planning §7.)
> - *Measured here (E3.1):* the `kitty @ launch → dtach → wrapper` chain propagates exit codes
>   faithfully — verified with both `0` and `42`, plus signal deaths decoded as `128+N`. Neither
>   dtach nor kitty altered or swallowed the code.
>
> Since claude exits 0 and the transport preserves exit codes exactly, exit 0 reaches the daemon.
> The §7 discriminator between `awaiting_review` and `interrupted` is sound.
>
> *(Stated as a composition of two verified facts rather than an end-to-end observation, because
> that is what it is. The one thing genuinely untested is the latency between `/exit` and the
> wrapper's report — expected to be negligible, and nothing depends on it.)*

### E3.3 — What do non-zero exits actually look like?
Produce every failure we can and tabulate. §16 can't classify `awaiting_review` vs `failed`
until we know what the codes are.

| Failure | Method | Exit code | stderr |
|---|---|---|---|
| Nonexistent cwd | `--cwd /nope` in `kitty @ launch` | | |
| Invalid flag | `--permission-mode bogus` | | |
| Not a git repo / no worktree | run in a bare temp dir | | |
| SIGTERM mid-run | `kill <pid>` | | |
| SIGKILL mid-run | `kill -9 <pid>` | | |
| SIGKILL the *wrapper* too | | | |
| Auth/rate-limit failure | if reproducible; otherwise note as unknown | | |

**Record.** Fill the table. Note especially whether signal deaths surface as `128+N` through
the wrapper or as something else, since that's the distinction between "crashed" and "human
killed it".

> **RESULT:** ✅ **Measured.** claude 2.1.239.
>
> **Argument-level failures — all exit fast, before any session:**
>
> | Failure | rc | Message |
> |---|---|---|
> | `--permission-mode bogus` | **1** | `error: option '--permission-mode <mode>' argument 'bogus' is invalid.` |
> | unknown flag | **1** | `error: unknown option '--nonexistent-flag'` |
> | `--session-id not-a-uuid` | **1** | `Error: Invalid session ID. Must be a valid UUID.` |
> | `--model no-such-model-xyz` | **1** | fails fast |
> | `--add-dir /does/not/exist` | **0** | ⚠️ **silently tolerated** |
> | `--settings <malformed json>` | **0** | ⚠️ **silently ignored** |
> | `--effort nonsense` | **0** | warns, ignores, uses default |
>
> **Process-level failures via the wrapper — standard shell semantics, correctly captured:**
>
> | Failure | rc | signal |
> |---|---|---|
> | payload binary missing | **127** | — |
> | payload not executable | **126** | — |
> | payload SIGTERM'd | **143** | 15 |
> | payload SIGKILL'd | **137** | 9 |
> | **wrapper itself SIGKILL'd** | — | **no record at all** |
>
> **Classification guidance for §16** (`awaiting_review` vs `failed`):
> - **1, 126, 127** → `failed`. These are config/dispatch errors — claude never ran. Retrying
>   without a config change is pointless; surface the stderr.
> - **143 / 137 (128+N)** → `interrupted`, not `failed`. A signal death means something external
>   killed it; the work may be perfectly resumable via `--resume <id>`.
> - **0** → `awaiting_review` (the §7 rule stands).
> - **no record** → `interrupted` via reconciliation (E2.5).
>
> ⚠️ **Two silent-tolerance cases to guard at dispatch.** `--add-dir` pointing nowhere and a
> malformed `--settings` both exit **0** and proceed. Note the `--print` help says settings that
> fail validation are *"silently ignored in this mode"* — implying **interactive mode may instead
> show an error dialog, which would block the session on a modal** exactly like the trust dialog
> (E1.5). The daemon should validate its own generated paths and settings before launch rather
> than discover this at runtime.
>
> #### F16 — `kitty @ launch` returning 0 is NOT a dispatch success signal
>
> Third demonstration of this, and now conclusive. `kitty @ --to ... launch --cwd /nope/nowhere`
> returned **rc=0 and a valid window id (85)** while the session never started. Combined with F10
> (dtach arg error → rc=0, valid window id) and F11, the rule is:
>
> **The daemon must never treat `kitty @ launch`'s exit code as evidence that a session started.**
> Confirm dispatch by an independent observation — the wrapper's `start` record arriving, or the
> window appearing in `kitty @ ls` with the expected `user_vars.ra_item` (E2.4). Until one of those
> is seen, the item is not `active`. This is what F15's `dispatching` max-age is for.
>
> #### F17 — killing the wrapper ORPHANS the payload (a live session with no bookkeeping)
>
> `kill -9` on the wrapper produced **a `start` record, no `exit` record, and a still-running
> payload** — reparented and leaked. In the real chain the same holds for claude: dtach sees its
> child (the wrapper) exit and tears down its socket, so the daemon observes *no socket, no exit
> report* and reconciles the item to `interrupted` — **while a real claude session is still
> running**, editing files and consuming subscription quota.
>
> `interrupted` therefore does **not** imply "nothing is running". Consequences:
> - §10's concurrency cap must count orphans, or the cap silently over-admits.
> - Reconciliation should sweep for claude processes whose cwd is under `~/GIT-worktrees/` and
>   reconcile them against known items — which is exactly the E5.1 `/proc/<pid>/cwd` discriminator,
>   now doing double duty.
> - **The obvious in-wrapper fix is a trap — and it is a trap in both senses. Do not ship it
>   without testing.** To trap signals *while* the payload runs, the payload must go into the
>   background so the shell can `wait` on it. But bash, with job control disabled (the default in a
>   non-interactive script), **redirects a background command's stdin from `/dev/null`** unless an
>   explicit redirection overrides it — which would silently break claude's interactivity, the one
>   property the whole design exists to preserve (settled constraint, top of this doc). A wrapper
>   that cleans up orphans perfectly but breaks the interactive session is a net loss.
> - **Worse, it wouldn't have helped here anyway:** the case we actually observed was `kill -9`,
>   and **SIGKILL cannot be trapped.** An in-wrapper trap addresses only the catchable signals,
>   which are the cases least likely to leak.
> - **Therefore the daemon-side sweep is the primary mitigation, not a fallback.** Reconciliation
>   scans for claude processes with cwd under `~/GIT-worktrees/` (E5.1) and reconciles them against
>   known items. If a trap is added later, it is defence in depth and must be verified against a
>   real interactive session first.

### E3.4 — Session ID — **ANSWERED by P0.2, downgraded to a verification**

`--session-id <uuid>` exists. The orchestrator **generates** the UUID at dispatch and passes it
in, so the ID is known and recorded in the DB *before the process starts*. No scraping, no race,
no dependence on the `~/.claude/projects/` layout, and it survives every failure mode including
a process that dies before writing anything.

**Remaining verification (small).**
1. Launch with an orchestrator-generated `--session-id`; confirm it's accepted (valid UUID form).
2. After exit, `claude --resume <that-uuid>` in the worktree — does it resume that exact session?
3. What happens if the same `--session-id` is reused for a second launch — error, resume, or
   silent overwrite? (Matters for the §11 idempotency guarantee on retry-after-failure.)
4. Does `--session-id` compose with `--bg` (E1.4) and with `--fork-session`?

**Design consequence.** §8 resume policy resolves to `--resume <id>`; positional `--continue`
becomes a fallback we don't need. §7's session table gets the ID at `dispatching`, not `active`.

> **RESULT:** ⚠️ **Partially answered — the flag exists, but end-to-end verification is NOT done.**
> - ✅ `--session-id <uuid>` exists (P0.2, `--help` on 2.1.239), and rejects a malformed value with
>   rc=1 / `Error: Invalid session ID. Must be a valid UUID.` (E3.3) — so it is genuinely parsed.
> - ✅ `~/.claude/sessions/<pid>.json` exposes `sessionId` for live sessions (E5.2), giving the
>   daemon an exact join key.
> - ✅ **VERIFIED end-to-end with a real claude session** through the full
>   `kitty @ launch → dtach → wrapper → claude` chain, dispatched from a systemd unit:
>   - Orchestrator generated `853c3354-3974-43ba-bda5-9381c47e9652` and passed it to `--session-id`.
>   - `~/.claude/sessions/<pid>.json` showed **exactly that `sessionId`**, appearing ~2s after
>     launch, with `cwd` = the worktree and `status` = `busy`.
>   - The transcript was written to
>     `~/.claude/projects/-home-jantman-GIT-worktrees-electronics-projects-real-1/853c3354-….jsonl`
>     — **the filename is the session id**, so the daemon can locate a transcript from data it
>     already holds.
>   - After `/exit`, `claude --resume 853c3354-…` in the worktree answered *"I replied with the
>     word READY"* — **context fully restored**.
>   - On clean exit the registry entry was **removed** and the dtach socket **auto-cleaned**;
>     `status` had moved `busy → idle` when the display died.
>
> **§8's resume policy is fully settled: generate the UUID at dispatch, store it before launch,
> resume with `--resume <id>`.**
>
> ❌ Still unverified (minor, deferrable to M1): reusing the *same* id for a second launch
> (§11 idempotency on retry), and composition with `--fork-session`.

### E3.5 — Does `--continue` / `--resume` actually resume *usefully*?
Not just "does it start" — resume a session that had done real work (read files, made an edit)
and check that the context is there.

**Record.** Was the prior context present? Did it re-read files? Any prompt asking to confirm?

> **RESULT:** ✅ **Answered — confirmed by JA from extensive prior usage: `--resume` restores the
> entire session.** No test needed.
>
> Combined with E3.4 (`--session-id` lets the orchestrator *generate* the ID at dispatch), §8's
> resume story is fully settled: the daemon knows the session ID before the process starts, and
> `--resume <id>` brings the whole thing back. Positional `--continue` is unnecessary.

---

## Phase 4 — Reconciliation & reboot recovery

### E4.1 — Daemon restart does not kill sessions
§16 lists this as resolved-by-reasoning; confirm empirically since it's cheap. Kill the
`as-daemon.sh` transient unit (`systemctl --user stop ra-spike-*`) while a session runs.

**Record.** Session survives? Did stopping the unit propagate a signal to the launched session
(systemd's default `KillMode=control-group` will kill the whole cgroup — **check whether the
kitty-launched child ended up in the service's cgroup or kitty's**). This is a real risk the
planning doc doesn't cover: the §9 "kitty is the parent" argument is about process parentage,
but systemd kills by *cgroup*, not by parentage. If the child lands in the service cgroup, the
daemon stopping will kill sessions regardless of who forked them.

> **RESULT:** ✅ **Risk resolved — and §9's conclusion holds for a better reason than parentage.**
> No reboot needed; measured directly from `/proc/<pid>/cgroup`:
>
> | Process | cgroup |
> |---|---|
> | main kitty (5300) | `…/app.slice/app-kitty\x2drestore@….service` |
> | **a live claude session (646827)** | **`…/app.slice/kitty-5300-67.scope`** |
> | a transient `as-daemon.sh` unit | `…/app.slice/ra-cg-probe.service` |
>
> **kitty places each launched window's processes into its own systemd scope**, not into the
> caller's cgroup and not even into kitty's own. So a session launched by `kitty @ launch` from the
> daemon's unit lands in a kitty-created scope that is **outside the daemon's service cgroup
> entirely**.
>
> Therefore stopping or restarting the daemon **cannot** kill live sessions — not merely because
> kitty is the parent process (§9's argument, which would *not* have saved us, since systemd kills
> by cgroup), but because the cgroups are disjoint. The right conclusion, reached for the right
> reason.
>
> #### F18 — each session gets an addressable systemd scope, which is a clean `terminate()`
>
> The scope is a real unit: `systemctl --user show kitty-5300-67.scope` reports
> `Id=kitty-5300-67.scope`, `ActiveState=active`. That gives the §3 AI Worker / Session Host
> `terminate(handle)` interface a precise, well-behaved implementation —
> `systemctl --user stop <scope>` kills exactly that session's process tree and nothing else,
> which is far better than signalling a PID we scraped.
>
> ⚠️ Treat the scope name as an **opaque handle**, not a derivable key. It is
> `kitty-<kitty_pid>-<n>.scope`, and `n` is *close to* but not obviously equal to the kitty window
> id (this session: scope `…-67`, while `KITTY_WINDOW_ID` was 68). The relationship wasn't pinned
> down. Record the scope by reading `/proc/<pid>/cgroup` at dispatch; don't compute it.

### E4.2 — The "lost" path
`kill -9` claude *and* the wrapper, so nothing reports. Then run a hand-written reconciliation
check: is the dtach socket stale? does `kitty @ ls` still show a window? Does the item look
distinguishable from a live session?

**Record.** What the reconciler can actually observe. Whether a stale dtach socket is
detectable without hanging.

> **RESULT:** ✅ **Stale dtach sockets are detected cleanly and fast.** Simulated an unclean death
> by `kill -9`-ing a dtach master (the same state a reboot produces):
>
> - Socket file **remains** on disk — as expected under `~/.local/state`.
> - `dtach -a <stale socket>` → **rc=1 in 7ms**. No hang. (rc=124 would have been a problem.)
> - The failed attach **does not** clean up the stale socket — so **stale sockets accumulate and
>   the daemon must prune them itself**. Pruning is safe precisely because liveness is a 7ms probe.
>
> Combined with E2.1 (stale *kitty* sockets → `ECONNREFUSED` in 25ms), the rule holds for both
> socket types: **probe, don't trust the file's existence.**

### E4.3 — Reboot
**Do this last.** Write all prior results to this file first.

**Before rebooting, record:** contents of `~/.local/state/robot-army-spike/sockets/`, the
worktree's `git status`, the session ID(s), the `exits.jsonl` contents.

**After reboot, check:**
- Did the dtach sockets survive? (They're under `~/.local/state`, so they should — which means
  **stale socket files will accumulate and must be distinguishable from live ones.** Note how.)
- Would they have survived under `/tmp` or `$XDG_RUNTIME_DIR`? Note the tradeoff: `/tmp`
  self-cleans but tells you nothing; `~/.local/state` persists but lies.
- `dtach -a` against a stale socket — clean failure or hang?
- `claude --continue` (or `--resume <id>`) in the worktree — does it resume usefully?
- Did the systemd user manager come up in a usable state? (It's currently `degraded`.)

**Record.** All of the above.

**Design consequence.** §8 reconciliation, socket directory choice, and the systemd
ordering/grace-period question.

> **RESULT:** ⏭️ **DEFERRED OUT OF M0 — deliberately, not skipped.** Most of what this test would
> have shown is now either answered or substituted for. What remains needs a real daemon and unit
> file, so it belongs in M1.
>
> **Already answered elsewhere:**
>
> | Reboot question | Answered by |
> |---|---|
> | Does `--resume` still work? | E3.5 — session state is on disk in `~/.claude/projects/`; a reboot doesn't touch it |
> | Is everything `interrupted`? | E2.5/F17 — the signature is a `start` with no `exit` and no live process; that *is* the post-reboot state |
> | Do dtach sockets survive? | E3.1 — dtach only removes its socket on clean exit, and they live on a persistent fs |
> | Is a stale socket detectable, or does it hang? | **E4.2 substitute — `kill -9` the master: `dtach -a` fails in 7 ms, socket persists, daemon must prune** |
> | Do sessions die when the daemon restarts? | **E4.1 — no; disjoint cgroups (`kitty-*.scope`)** |
> | Does the daemon have its environment at boot? | F3 — Plasma imports at *graphical login*, so before login it does not. A design decision (`PartOf=graphical-session.target`), not an experiment |
>
> **What was left is now moot.** The only genuinely reboot-only question was whether the unit's
> ordering (`After=` MariaDB, `PartOf=graphical-session.target`) behaves at boot — and **JA has
> ruled that out of scope: starting the daemon manually after login is acceptable.**
>
> That decision also **resolves F3** rather than deferring it: a daemon started by hand after
> graphical login is guaranteed to have the imported environment (`WAYLAND_DISPLAY`, `DISPLAY`,
> `DBUS_SESSION_BUS_ADDRESS`) and a running kitty to launch into. The "daemon came up before the
> graphical session" failure mode simply cannot occur. If auto-start is ever wanted later,
> `PartOf=graphical-session.target` is the known answer — it just needs testing at that point.
>
> One residual, and it does not affect the design: whether `~/.claude/sessions/<pid>.json` entries
> persist as **stale** across a reboot. Strongly expected (no clean exit ⇒ no cleanup), and the
> E5.3 predicate already guards for it via the `procStart` PID-reuse check — so a reboot would
> *validate* the guard, not inform it. Substitutable any time by `kill -9`-ing a throwaway session.
>
> **Conclusion: no reboot test is needed. M0 is complete without it.**

---

## Phase 5 — Out-of-band session detection

Feeds the §10 concurrency cap. Run with *this* interactive session plus at least one spike
session alive, so there's something to discriminate.

### E5.1 — Process scan
```
pgrep -af claude
ps -eo pid,ppid,user,lstart,args | grep -i claude
```

**Complication already identified:** `claude` is an asdf shim (`~/.asdf/shims/claude`), so the
real process is almost certainly `node .../cli.js`. `pgrep -f claude` may match the shim, the
node process, both, or neither reliably.

**Better discriminators to test:**
- `/proc/<pid>/cwd` — orchestrator sessions live under `~/GIT-worktrees/`, interactive ones
  under `~/GIT/`. This is probably the cleanest signal and doesn't depend on cmdline shape.
- Process ancestry — orchestrator sessions have `dtach` → wrapper in the chain.
- Environment marker — have the wrapper export `ROBOT_ARMY_ITEM=<id>` and read
  `/proc/<pid>/environ`. Explicit beats inferred.

**Record.** Actual cmdlines observed. Which discriminator is unambiguous.

> **RESULT:** ✅ **Cmdline matching is unusable. Use `/proc/<pid>/exe` + `cwd`.**
>
> `pgrep -f claude` returned **18 matches**, of which:
> - **12** were the `claude-desktop-bin` Electron app (renderers, GPU process, crashpad handler)
> - several were `bash -c` processes matching only because the command line contained
>   `/home/jantman/.claude/shell-snapshots/...`
> - **2** were actual Claude Code sessions
>
> **Two self-inflicted incidents this session prove the point better than the numbers do:**
> 1. `pkill -f 'dtach -n /tmp/ra-dtach-probe.sock'` **killed my own shell**, because the shell's
>    own command line contained the pattern being matched.
> 2. `pgrep -f 'ra-session-wrapper.sh spike-1'` matched kitty's `kitten run-shell` layer instead of
>    the wrapper, and briefly produced a wrong conclusion about whether the wrapper survived (F11).
>
> A daemon doing this on a timer would eventually kill something it shouldn't. **Never match on
> command lines.**
>
> **What works:**
>
> | Discriminator | Result |
> |---|---|
> | `pgrep -x claude` + `readlink /proc/<pid>/exe` | **Definitive.** Claude Code is `~/.local/share/claude/versions/<v>/claude`; the desktop app is `/usr/lib/claude-desktop-bin/claude` |
> | `readlink /proc/<pid>/cwd` | Cleanly separates `~/GIT/<repo>` (interactive) from `~/GIT-worktrees/<repo>/<item>` (orchestrator) |
> | `/proc/<pid>/environ` → `ROBOT_ARMY_ITEM` | Works; present only on orchestrator sessions, so it is a positive identifier, not a classifier |
>
> Observed live: pid 646827 cwd `~/GIT/robot-army`, pid 1996056 cwd
> `~/GIT/workshop-inventory-tracking` — both correctly classified as interactive.

### E5.2 — `~/.claude` state inspection
Look for anything that lists live sessions: `~/.claude/projects/*/`,
`~/.claude/ide/*.lock`, any pidfile or lockfile. Determine whether a session's liveness is
inferable, and whether stale entries are distinguishable.

**Record.** What's there. Whether it's usable or whether it's an undocumented internal format
we'd be foolish to depend on.

> **RESULT:** ✅ **There is a live session registry, and it is far better than §10 assumed.**
>
> **`~/.claude/sessions/<pid>.json`** — one file per running Claude Code session. Verified an
> **exact 1:1 correspondence** with live processes and **zero stale entries**: two live sessions,
> two files, both PIDs alive.
>
> Fields (from the two live sessions):
>
> | Field | Example | Use |
> |---|---|---|
> | `pid` | `646827` | liveness |
> | `sessionId` | *(uuid)* | **exact join key** — the daemon *generated* this via `--session-id` (E3.4) |
> | `cwd` | `/home/jantman/GIT/robot-army` | orchestrator vs interactive |
> | `procStart` | `69148376` | **PID-reuse guard** (kernel start-time ticks) |
> | `status` | `busy` / `shell` | **live activity** — useful for the §13 UI |
> | `kind` / `entrypoint` | `interactive` / `cli` | session class |
> | `name` | `robot-army-91` | auto-derived from dir; `-n/--name` overrides (§9 naming) |
> | `version` | `2.1.239` | **lets us version-guard our own parsing** |
> | `messagingSocketPath` | *(path)* | socket **verified present** — independent liveness check |
>
> **This resolves §10's "both are somewhat fragile — accept best-effort" as too pessimistic.**
> `sessionId` + `procStart` gives an exact, PID-reuse-safe join between a DB row and a live
> process. That is not best-effort; it is exact.
>
> ⚠️ **Caveats, and they matter:**
> - **Undocumented internal format.** It can change or vanish in any release. The file records its
>   own `version`, so guard on it and degrade to the E5.1 `/proc` method rather than crashing.
> - **`<pid>.<hash>.key` files sit alongside, mode `0600`.** These look like session credentials.
>   **Not read during this spike, and the daemon must never read or copy them.**
> - `~/.claude/session-env/` holds **285** UUID-keyed directories against 2 live sessions — that
>   one *does* accumulate stale entries, so it is not a liveness source. (Unrelated to us, but
>   note it before anyone mistakes it for one.)

### E5.3 — Decision
Write down the chosen predicate for "count this claude against the cap", and its known failure
modes.

> **RESULT:** ✅ **Chosen predicate — registry first, `/proc` as fallback.**
>
> ```
> live_sessions():
>   if ~/.claude/sessions/ exists and entries report a version we understand:
>       for each <pid>.json:
>           skip if pid not alive, or /proc/<pid> start-time != procStart   # PID-reuse guard
>           classify: cwd under ~/GIT-worktrees/  -> orchestrator (join on sessionId)
>                     otherwise                   -> out-of-band interactive
>   else:                                                                   # fallback
>       for pid in pgrep -x claude:
>           skip unless /proc/<pid>/exe is under ~/.local/share/claude/
>           classify by /proc/<pid>/cwd as above
> ```
>
> **Every session found counts against the §10 global cap**, orchestrator or not — they contend for
> the same subscription. Orchestrator sessions additionally join to a DB row by `sessionId`.
>
> **This predicate does double duty:** an orchestrator-cwd process with *no* matching `active` DB
> row is precisely the **F17 orphan** — a live session the daemon has lost track of. Reconciliation
> should flag those loudly rather than let them run unaccounted.
>
> **Known failure modes:**
> - Registry format changes → caught by the `version` guard, degrades to `/proc`.
> - A session started in a worktree *by hand* is misclassified as orchestrator; the `sessionId`
>   join corrects it (no DB row ⇒ treat as out-of-band, not as an orphan to kill).
> - Neither method sees sessions belonging to another user — out of scope, single-user machine.
> - **Never** fall back to cmdline matching (E5.1).

---

## Phase 6 — Worktree reality check

Independent of Phases 1–5; can be done in parallel / on a different evening.

### E6.1 — Inventory `~/GIT` (300 repos)
Scan for the things §6 says will hurt:
```
.gitmodules  .env  .envrc  venv/ .venv/  node_modules/  .python-version
docker-compose.yml  Makefile  tox.ini  package.json
```
Produce a count and pick guinea pigs: **one plain repo, one with submodules, one with heavy
untracked state** (`.env` + `node_modules` or a venv), ideally one you actually get work done in.

**Record.** The counts, and the three repos chosen.

> **RESULT:** ✅ **294 git repos in `~/GIT`.** Marker counts:
>
> | Marker | Count | Relevance |
> |---|---|---|
> | `venv/` or `.venv/` | **39** | **The dominant post-create-hook need.** Not carried into worktrees |
> | `tox.ini` | 47 | Test runner; usually rebuilds its own envs |
> | `.claude/` | 30 | Resolves from the main clone — **no hook needed**, see F12 |
> | `.gitmodules` | **11** | Worktree risk, see E6.3 |
> | `docker-compose.yml` | 10 | Port-collision risk (§6) |
> | `.python-version` | 6 | pyenv; worktree should inherit via the file (tracked) |
> | `.env` / `.env.local` | **4** | Untracked secrets — **must** be handled by a hook |
> | `package.json` | 4 | |
> | `node_modules/` | 2 | Surprisingly rare; less of a problem than §6 assumes |
> | `.tool-versions` | 1 | asdf; relevant to the F2 shim note |
>
> **The `.env` repos are the acute case, and they overlap almost exactly with the compose repos:**
> `equipment-status-board`, `kiosk-show-replacement`, `machine-access-control`,
> `workshop-inventory-tracking`. These are the real applications — untracked secrets *and* a dev
> server *and* ports. Any one of them is the right guinea pig for the hard case.
>
> **Repos with submodules (11):** `3d-printed-things`, `GitPython`, `activitywatch`,
> `pelican-plugins`, `pelican-themes`, `puppet-docs`, `python-mcollective`, `sedutil`,
> `specfiles`, `willie`, `zoneminder`.
>
> **Proposed guinea pigs for E6.2:**
> 1. `electronics-projects` — plain, worktree already created and proven, no build step
> 2. `biweeklybudget` or `equipment-status-board` — venv + (for the latter) `.env` + compose
> 3. one of the 11 submodule repos — `activitywatch` or `zoneminder`
>
> **Scale note for §6:** only ~15 of 294 repos (`.env` + submodules) need real per-repo hook work.
> The venv 39 are likely one shared default hook. That is a much smaller configuration burden than
> "every repo will need something here" implies.

### E6.2 — Add worktrees and see what breaks
For each guinea pig:
```
git -C ~/GIT/<repo> fetch origin
git -C ~/GIT/<repo> worktree add ~/GIT-worktrees/<repo>/spike-1 -b robot-army/spike-1 origin/main
```
Then try to actually *do work* in it: run the test suite, start the dev server, run the linter —
whatever you'd want a dispatched session to do.

**Record.** For each repo: exactly what was missing, and the command that fixed it. This is the
raw material for the per-repo post-create hook config in §6.

> **RESULT:** ✅ Tested `equipment-status-board` (the hard case: venv + `.env` + compose + tracked
> `.claude/`).
>
> **What the worktree lacked vs the main clone:**
>
> | Path | In worktree? | Consequence |
> |---|---|---|
> | `venv/` | no | `ModuleNotFoundError: No module named 'flask_sqlalchemy'` — nothing runs |
> | `.env` | no | *No consequence for tests* — see below |
> | `instance/` | no | none observed |
> | `.pytest_cache/` | no | none (rebuilt) |
> | `.claude/` | **yes** | this repo *tracks* `.claude/commands/**` |
>
> **The fix was the repo's own setup target** — `make setup` (`python -m venv venv &&
> venv/bin/pip install -r requirements-dev.txt`). Ran clean in the worktree in **47s**.
>
> **Then: `2029 tests passed in 134s`, with no `.env` and no `instance/`.**
>
> #### F13 — the untracked-file problem is much smaller than §6 assumes, because dispatch ≠ running the app
>
> §6 says *"Every repo will need something here."* Measured, that's not so. A dispatched session
> builds, tests, and lints — it does not run the production app. `.env` is needed for `make
> dev`/`make run`/compose, none of which a robot-army session normally does. Combined with E6.1's
> counts, the realistic hook burden is:
>
> - **39 venv repos** → one shared default hook (`make setup` or `python -m venv && pip install -r`)
> - **4 `.env` repos** → symlink or copy, and *only* if the session must run the app
> - **30 `.claude/` repos** → **nothing**, settings resolve from the main clone (F12)
> - **11 submodule repos** → real work, see E6.3
>
> So roughly 15 repos need bespoke config, not 294.
>
> #### F14 — worktrees with venvs are not cheap: **499 MB** for this one
>
> §6 says worktrees are *"cheap and instant"* because they share the object store. True for git
> objects; false once a post-create hook builds a venv. The finished
> `equipment-status-board` worktree measured **499 MB**. With a per-repo concurrency cap of 1 and
> several repos active, that's multiple GB of ephemeral disk. Worth a disk-usage note in the
> cleanup policy (§16) — and an argument for cleaning up worktrees on issue close rather than
> keeping them around indefinitely.

### E6.3 — Submodules
On the submodule repo: does `git worktree add` leave submodules in a sane state? Does
`git submodule update --init --recursive` work from the worktree? Does it share the object
store or re-clone?

**Record.** Behavior, and whether the repo is usable at all under worktrees.

> **RESULT:** ⚠️ **Submodules are the real worktree cost, and they introduced a hang.** Tested
> `specfiles` (4 submodules, 1.5 MB).
>
> 1. **Submodules are EMPTY in a fresh worktree.** `git submodule status` shows all four with the
>    `-` (uninitialized) prefix. Expected, but it means any submodule repo *requires* a
>    post-create hook — it is never optional.
> 2. **Submodule gitdirs are NOT shared with the main clone.** The worktree got its own
>    `.git/worktrees/spike-1/modules/externals`, separate from the main clone's
>    `.git/modules/externals`. So **§6's "shares the object store — cheap and instant" does not
>    hold for submodule repos** — each worktree re-clones them, costing time, network, and disk.
> 3. **🚨 `git submodule update --init --recursive` HUNG and had to be killed at 180s.**
>
> #### F15 — post-create hooks MUST have timeouts (a hang is worse than a failure)
>
> The cause here is repo-specific: `specfiles`' `.gitmodules` uses `git://` URLs, and **GitHub
> disabled the git:// protocol in 2021**. Port 9418 is now dropped rather than refused, so the
> fetch doesn't error — it *hangs*. Confirmed independently: `git ls-remote git://github.com/...`
> had to be killed at 8s.
>
> The generalizable lesson matters more than the specific repo: **a post-create hook can hang
> indefinitely, and a hung hook hangs the dispatch.** The work item sits in `dispatching` forever
> with no session, no error, and nothing for reconciliation to observe. §6 discusses hooks purely
> as a convenience; they need to be treated as a failure domain:
> - Every hook gets a **timeout**, configurable per repo, with a sane default.
> - Hook timeout → work item to `failed` with the captured output, not a silent stall.
> - `dispatching` needs a **maximum age** in reconciliation, precisely so this state can't wedge.
>
> Note this also means the §7 `dispatching` state — described as "transient" — can in fact persist,
> which is worth saying explicitly in the state table.

### E6.4 — Draft the post-create hook shape
From what actually broke in E6.2/E6.3, write the config schema for the per-repo hook: is it a
shell command? a list? does it need the worktree path and the source clone path? Does it need
to run before or after the branch is created?

**Record.** Proposed schema, with the three real examples filled in.

> **RESULT:** ✅ Proposed schema, driven by what actually broke in E6.2/E6.3:
>
> ```yaml
> repos:
>   equipment-status-board:
>     post_create:
>       - run: make setup          # 47s measured
>         timeout: 300             # F15: hooks MUST have timeouts
>       - link: .env               # from the main clone; only if the app must run
>     ports:                       # E6.6: env-var injection, no source changes
>       ESB_HOST_PORT: auto
>       ESB_DEV_HOST_PORT: auto
>
>   specfiles:
>     post_create:
>       - run: git submodule update --init --recursive
>         timeout: 120             # WILL hang on this repo (git:// is dead) -> fail, don't stall
>
>   electronics-projects: {}       # nothing needed
> ```
>
> Design points the experiments forced:
> - **`timeout` is mandatory per step, not optional** (F15). A hook that hangs wedges the dispatch
>   in `dispatching` with nothing observable.
> - Hooks need **both** `run` (shell) and `link`/`copy` (untracked-file plumbing) forms; expressing
>   a symlink as a shell command works but reads badly and is harder to make idempotent.
> - Hooks run **after** branch creation and **in the worktree**, with the main clone's path
>   available (the `.env` case needs to reference it).
> - **No `.claude/` handling needed** — F12.
> - A **default hook** covering the 39 venv repos means most repos need no entry at all; the config
>   is an override list, not a registry.
> - Non-zero hook exit → work item `failed`, with the captured output surfaced. Don't launch a
>   session into a half-built worktree.

### E6.5 — Cleanup semantics
- `git worktree remove` with uncommitted changes — does it refuse? (§6 says never auto-remove
  in that case; confirm git gives us the guard for free.)
- `git worktree remove --force` — what does it destroy?
- `git worktree prune` after deleting a directory by hand.
- Does removing the worktree leave the branch behind?

**Record.** Behavior. Feeds the §16 cleanup-policy decision.

> **RESULT:** ✅ **Fully answered. Git provides the §6 safety guard for free.**
>
> | Operation | Behavior |
> |---|---|
> | `worktree remove`, **clean** tree | succeeds |
> | `worktree remove`, **dirty** tree (even just an *untracked* file) | **refuses**: `fatal: '...' contains modified or untracked files, use --force to delete it` |
> | `worktree remove --force` | removes it, discarding changes |
> | branch afterwards | **always left behind** — `git branch -D` is a separate step |
> | directory deleted by hand | `worktree list` marks it **`prunable`**; `worktree prune` clears it |
>
> **§6's "never auto-remove a worktree with uncommitted changes" is enforced by git itself** — the
> daemon gets it by simply never passing `--force` without a human decision. Note the guard is
> conservative: an untracked file alone is enough to block removal, which is the right default for
> us.
>
> Two things for the cleanup policy (§16):
> - Cleanup is inherently **two steps** (worktree, then branch). Doing only the first leaves
>   `robot-army/*` branches accumulating in every repo.
> - **`prunable` is a detectable state**, useful to reconciliation: it means the worktree
>   directory vanished out from under us.

### E6.6 — Port collisions
For any guinea pig with a dev server: what port does it bind, and is it configurable by env
var? Just enough to know whether per-worktree port assignment (§6) is a config problem or a
patch-every-repo problem.

> **RESULT:** ✅ **It's a config problem, not a patch-every-repo problem.**
> `equipment-status-board`'s tracked `.env.example` declares every port as an environment
> variable: `ESB_HOST_PORT`, `ESB_DB_HOST_PORT`, `ESB_DEV_HOST_PORT`, `FLASK_RUN_PORT`.
>
> So per-worktree port assignment is just "inject a different `.env` (or env overrides) at
> dispatch" — which the post-create hook already has to do for the 4 `.env` repos anyway. No
> source changes needed. §6 can downgrade this from "future problem" to "solved by the same
> mechanism as `.env` handling", at least for repos that already parameterise their ports.
>
> Caveat: this is one repo. The claim is that the *mechanism* is sufficient, not that all 10
> compose repos are already parameterised. Worth a spot-check when onboarding each one.

---

## Wrap-up

When the result blocks are filled:

1. **Update `robot-army-planning.md`** — move answered items from §15/§16 into Resolved, and
   fold the concrete findings (exit codes, socket path strategy, host capability flags) into
   §3/§8/§9.
2. **Decide the four judgment calls** M0 informs but doesn't answer (§16): non-zero exit
   classification, kitty socket security posture, attach button, worktree cleanup policy.
3. **Keep `spike/ra-session-wrapper.sh`** — it's the M1 wrapper's first draft, now with
   evidence behind every line.
