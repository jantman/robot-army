# Contract: Boundary Interfaces

The five seams that touch the outside world. FR-053 requires effect levels to be enforced *here*
rather than at call sites, and gives the reason: scattered `if dry_run:` checks drift, cannot be
tested, and let the simulated path diverge from the real one.

**Wiring happens once at startup.** The daemon selects real or simulated per boundary from the table
below, and code downstream of that wiring has no access to the effect level. This is what makes the
guarantee structural rather than a rule someone has to remember.

| Boundary | `plan` | `local` | `no-remote` | `live` |
|---|---|---|---|---|
| `IssueSource` reads | real | real | real | real |
| `IssueSource` writes | simulated | simulated | simulated | real |
| `VersionControl` | simulated | real | real | real |
| `HookRunner` | simulated | real | real | real |
| `SessionHost` | simulated | simulated | real | real |
| `Display` | simulated | simulated | real | real |

Reads are always real (FR-052) — a dry run that fakes its reads tells you nothing about eligibility,
which is the main thing you want to check.

**Every simulated implementation must**: emit an audit record naming the call and its full
arguments, and return a **structurally valid** fake handle. Returning `None` or raising would let the
simulated path diverge from the real one at exactly the point the requirement exists to prevent.

**An outward-facing call's exit status is not evidence of its effect.** This project has now been
caught by it twice, in both directions:

- `kitty @ launch` returns `0` and a valid window id for a session that **never started** (M0 F16).
  FR-025 exists because of it: an item does not reach `active` on the strength of the launch call
  returning success, only on an independent observation that a session carrying the id we generated
  exists.
- `systemctl --user stop` returns `0` in about four milliseconds for a unit that is **already
  inactive**, killing nothing, while a live process remains in its cgroup (issue #34). Termination
  had no equivalent confirmation, so `cancel` reported a stopped session that was still running,
  the work item was marked `interrupted`, and — because reconciliation's session sweep visits only
  `active` items — nothing ever looked at it again.

So: **any boundary operation whose effect is observable MUST confirm that effect independently
before reporting it.** Not "check the return code and log it": observe the world and report what was
observed. Three corollaries, each of which was a real bug before it was a rule:

1. **Identity, not just existence.** Confirm against `pid` *and* `proc_start` together
   (`procinfo.is_alive`, FR-038). A recycled pid is not a live session, and must never be signalled
   as one. Never identify a process by its command line (FR-039).
2. **Bounded.** A confirmation that does not complete within its bound reports "not confirmed",
   never success. An unbounded confirmation is a hang with better intentions.
3. **A contradicted success is recorded as such.** When a call reports success and the observation
   disagrees, the record carries both, and the operation escalates rather than returning. Recording
   only the success is how the first four milliseconds of issue #34 looked entirely healthy in the
   log.

The confirmed shape is not free, and it is not required of operations whose effect cannot be
observed — a comment posted to an issue, say, where the API response *is* the observation. The test
is whether the world can be asked a second, independent question. Where it can, ask it.

---

## `IssueSource`

Reads and writes at the work-item source. Split into two protocols so the effect table can treat
them differently — that split is what makes "polling is always real" structural.

```
IssueSourceReader:
    poll(repo_key, etag) -> PollResult(items, etag, status, rate_limit)
    get_issue(repo_key, number) -> Issue | None
    is_closed(repo_key, number) -> bool
    open_pr_for_branch(repo_key, branch) -> PullRequest | None
    list_owned_repos() -> [repo_ref]

IssueSourceWriter:
    comment(repo_key, number, body) -> comment_url
```

**Implementations**: `GitHubReader` / `GitHubWriter`, `SimulatedIssueWriter`.

There is no `SimulatedIssueReader` — deliberately. It is impossible to construct one by accident
because no effect level ever selects it, and its absence means a bug that tries to fake reads fails
to import rather than quietly returning fixtures.

**Contract notes**
- `poll` passes `etag` as `If-None-Match`; a `304` returns `items=[]` with `status=304` and is the
  healthy steady state, costing nothing against the rate limit.
- Every call sets explicit connect and read timeouts and retries with bounded exponential backoff
  and jitter, honouring `Retry-After` and `X-RateLimit-Reset` (FR-008).
- A transport failure raises. It **must not** be caught and turned into an empty result — "no
  eligible work" and "I could not ask" are different facts, and conflating them is exactly the silent
  failure Principle III forbids.
- `comment` is the only write in this milestone: a dispatch comment and a dispatch-failure comment.

## `VersionControl`

```
VersionControl:
    fetch(clone_path, remote, ref)
    add_worktree(clone_path, worktree_path, branch, base_ref) -> WorktreeHandle
    remove_worktree(worktree_path, force=False, clone_path=None) -> RemovalResult
    delete_branch(clone_path, branch)
    list_worktrees(clone_path) -> [WorktreeInfo]     # includes `prunable`
    prune_worktrees(clone_path)
    status_porcelain(worktree_path) -> str
    commits_ahead(clone_path, base_ref, branch) -> int
    show_file_at_ref(clone_path, ref, path) -> bytes | None
```

**Implementations**: `GitVersionControl` (subprocess), `SimulatedVersionControl`.

Planning §2 identified this boundary specifically: git operations would otherwise be inline calls,
and dry-run is the reason to give them a seam.

**Contract notes**
- `remove_worktree` takes the clone path because `git worktree remove` resolves the
  repository from its working directory; run from anywhere else it reports "is not a
  working tree" and removes nothing. Found during implementation, not during design.
- `remove_worktree` **never passes `--force` on its own**. Git refuses to remove a dirty worktree —
  including one with merely untracked files — and that refusal is the free guard FR-016 relies on
  (M0 E6.5).
- `remove_worktree` returning success does **not** mean the branch is gone. Removal is two steps;
  `delete_branch` is separate, and callers that skip it accumulate `robot-army/*` branches forever.
- `show_file_at_ref` exists for the committed-permission fingerprint (R12). It reads from the git
  object, not the filesystem, because what matters is what a freshly created worktree will contain.
- Every subprocess call sets a timeout. `git fetch` against an unreachable remote hangs otherwise.

## `HookRunner`

```
HookRunner:
    run(steps, worktree_path, clone_path, env) -> HookResult(ok, step_index, output, timed_out)
```

**Implementations**: `SubprocessHookRunner`, `SimulatedHookRunner`.

**Contract notes**
- **Every step is bounded by a timeout** (FR-013). M0 F15: `git submodule update --init --recursive`
  on a real repository hung *indefinitely* because its `.gitmodules` uses `git://` URLs and port 9418
  is now dropped rather than refused. It does not error; it hangs. A hung hook wedges a work item in
  `dispatching` forever with no session, no error, and nothing for reconciliation to observe.
- On timeout, the process **group** is killed, not just the direct child. A shell command that
  spawned `git` leaves the grandchild running otherwise.
- Output is captured and returned on failure — a failure with no output is unactionable.
- `HookResult(ok=False)` means the work item fails. **A session is never launched into a partially
  prepared worktree** (FR-014).

## `SessionHost`

Owns the process and its PTY. This is the axis along which *work survival* varies, which is why it
is separate from `Display`.

```
SessionHost:
    spawn(cwd, argv, socket_path) -> HostHandle
    is_alive(handle) -> bool
    terminate(handle, scope=None, *, expected_start=None, proc_root=None)
        -> TerminationOutcome(confirmed, method, escalated, detail)
    attach_command(handle) -> [str]

    capabilities: survives_display_death, reattachable, multi_viewer
```

**Implementations**: `DtachHost`, `SimulatedSessionHost`.

`DtachHost` capability values, all three **measured** in M0 rather than assumed: `survives_display_death`
true, `reattachable` true, `multi_viewer` true. The orchestrator branches on these; they are not
decoration.

**Contract notes**
- **`dtach` takes no `--` separator.** It rejects one outright with `Invalid option '--'`. The form
  is `dtach -A <socket> <cmd> [args...]`. The wrapper needs its own `--` after it. This broke the
  planning document's documented launch chain (M0 F10) and is the single easiest thing here to get
  wrong.
- `terminate` uses the recorded systemd scope (`systemctl --user stop <scope>`), read from
  `/proc/<pid>/cgroup` at confirmation and treated as an **opaque handle** — never recomputed
  (M0 F18). Falls back to signalling the process group, logging that the degraded path was taken.
- **`terminate` confirms; it does not trust the stop command's exit status.** `systemctl --user
  stop` exits 0 for a unit that is already inactive, killing nothing, which is how issue #34
  produced a session reported stopped and still running twenty-six minutes later. Every rung is
  followed by an independent `procinfo.is_alive(pid, proc_start)` observation under a bound, and
  a rung that reports success while the process survives escalates to the next rung rather than
  returning. `TerminationOutcome.confirmed` is the only thing a caller may change state on; a
  `BoundaryError` is raised only when there is neither a recorded scope nor a recorded pid.
  Full cases and caller obligations:
  [014 contracts/termination-outcome.md](../../014-confirm-session-termination/contracts/termination-outcome.md).
- `is_alive` **probes the socket**; it never trusts the file's existence. A dead dtach socket fails
  in ~7 ms, so there is no hang risk, and stale sockets do not clean themselves up.

## `Display`

An optional viewer onto a hosted session, **composed with a host, not substituted for one**.

```
Display:
    open(cwd, argv, title, user_vars, env) -> DisplayHandle
    is_open(handle) -> bool
    close(handle) -> None
    find_by_var(key, value) -> DisplayHandle | None
    send_text(handle, text) -> None
```

**Implementations**: `KittyDisplay`, `SimulatedDisplay`. Today's stack is
`KittyDisplay(DtachHost(...))`.

Kitty and dtach are *not* peers: kitty renders a PTY someone else may own; dtach owns one. Modelling
them as interchangeable would force a lowest-common-denominator interface.

**Contract notes**
- **Socket discovery, not prediction.** Kitty appends its PID to `listen_on`, so no fixed path
  exists. Glob the configured pattern, probe each candidate with `kitty @ --to <s> ls`, take
  whichever answers. A dead socket refuses in 14–25 ms. `--to` is mandatory — there is no
  `KITTY_LISTEN_ON` in a service environment.
- **`open` returning a window id is not evidence a session started.** It returns `0` and a valid id
  even when nothing ran — demonstrated three times in M0 (F16). Callers **must** confirm
  independently (FR-025).
- `find_by_var` uses `--var` / `user_vars` for an exact key lookup. Walking `foreground_processes` is
  fragile: `--hold` inserts a `kitten run-shell` layer that repeats the whole command in its own
  argv, so the same string appears at several depths.
- `--hold` is always passed, so a failed launch leaves a readable window instead of one that
  vanishes instantly (M0 F11).
- `send_text` must terminate with `\r`, not `\n`. `\n` types the text without submitting it, which
  looks exactly like the command silently failing.
- **Sessions inherit the terminal daemon's environment, not the caller's.** Anything a session needs
  must be passed explicitly via `--env` (M0 F19).

---

## Constitutional note

Principle I forbids "strategy interfaces that have exactly one caller and no second use in hand".
Each boundary here has exactly **two** implementations in this milestone, both required by
FR-051 through FR-058. The simulated implementations are not scaffolding for a hypothetical future;
they are the dry-run feature the spec mandates. See the plan's Complexity Tracking table.

What is deliberately **not** built: no registry, no plugin discovery, no configuration-driven
implementation selection, no abstract base classes beyond what type checking needs. Selection is a
literal table in one function.
