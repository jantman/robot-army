# Quickstart: Validating Concurrency & Polish

Runnable scenarios that demonstrate the milestone end to end. Three of them need things a dry run
cannot produce — a real Claude session you started yourself, a real dirty worktree, a real unpushed
branch — so use a **disposable repository** for the cleanup scenarios. Cleanup deletes worktrees and
branches; that is the point of it, and it is why scenarios 8–11 come with their own guard rails.

## Prerequisites

- Milestone 003 working: `robot-army doctor` passes, a daemon starts, an issue can be dispatched.
- At least two onboarded repositories, one of which you are willing to have branches deleted in.
- A terminal you can start your own `claude` sessions in, outside the daemon.
- For scenario 12 only: a webhook URL you can watch, in `[health] webhook_url`.

```bash
robot-army doctor
robot-army capacity          # new in this milestone; must report before anything else is trusted
```

---

## 1 — Nothing changed until you change the configuration (FR-046, SC-013)

With no `[dispatch]`, `[cleanup]`, or `[notifications]` sections, run a full cycle against one
eligible item on an idle machine.

```bash
robot-army run --once
robot-army log --since 5m | grep -cE 'cleanup\.|notify\.'    # expect 0
```

**Expected**: the item dispatches exactly as it did in milestone 003. Zero cleanup records, zero
notification records, no outbound request beyond the ones 003 already made.

---

## 2 — Your own sessions count (User Story 1, FR-001, SC-001)

Set `[daemon] max_concurrent_sessions = 2`. Start **two** `claude` sessions by hand, anywhere outside
`~/GIT-worktrees`. Make one issue eligible.

```bash
robot-army capacity
robot-army run --once
robot-army status
```

**Expected**: `capacity` reports `total 2 / cap 2`, with `ours 0` and `others 2`. The item stays
`ready` with hold reason `global_cap`. Exactly one `dispatch.at_capacity` record is written — not one
per tick (R16).

Now close one of your sessions and run another tick.

**Expected**: the item dispatches with no human action beyond closing the session (SC-002), and a
`dispatch.hold_ended` record names the duration and how many passes the hold spanned.

---

## 3 — The daemon never touches what it did not start (FR-006, SC-003)

With your own sessions running, exercise everything that could plausibly reach for one: run several
ticks, force a reconciliation, and cancel an unrelated item.

```bash
robot-army run --once; robot-army reconcile; robot-army cancel <some-item>
ps -o pid,etime,cmd -p <your-session-pids>
```

**Expected**: your sessions are untouched — same PIDs, same start times. No `session.terminate` record
names a PID the daemon did not launch. `CapacitySnapshot.others` is an integer, so there is no handle
to misuse (R5).

---

## 4 — The launch window does not open a second slot (FR-009, R3)

The trap this scenario exists for: a `starting` session has no registry file yet. With
`max_concurrent_sessions = 1` and **two** items eligible, run one tick.

```bash
robot-army run --once
robot-army status
```

**Expected**: exactly one dispatch. The second item is held at `global_cap` even though the registry
listed zero entries at the moment it was consulted, because the in-flight row was counted.

---

## 5 — A degraded registry holds rather than guesses (FR-007, SC-001)

Move the registry directory aside while a session of your own is running.

```bash
mv ~/.claude/sessions ~/.claude/sessions.bak
robot-army capacity
robot-army run --once
mv ~/.claude/sessions.bak ~/.claude/sessions
```

**Expected**: `capacity` reports `degraded true` and still counts your session, because the `/proc`
fallback ran (R4). Nothing is dispatched on an assumption of free capacity. If both paths fail,
`observable false` is reported, a `capacity_unobservable` anomaly is raised, and dispatch is withheld
entirely.

---

## 6 — One repository, one session (User Story 2, FR-011, SC-004)

`[dispatch] default_repo_max_sessions = 1`, `[daemon] max_concurrent_sessions = 2`. Label **two**
issues in the same repository and **one** in a different repository.

```bash
robot-army run --once
robot-army status
```

**Expected**: one session in each repository. The second item of the busy repository is held with hold
reason `repo_cap`, not `global_cap` — and critically, it did **not** block the other repository's item
from dispatching (FR-012, FR-020).

---

## 7 — The queue's "next" is the dispatcher's "next" (User Story 3, SC-006)

Fill the machine to capacity with three or more items eligible.

```bash
robot-army status | head -20        # note the item at position 1
robot-army run --once               # after freeing exactly one slot
```

**Expected**: the item dispatched is the one listed at position 1. Repeat with `[dispatch] order =
"repo-priority"` and a `priority` set on one repository: every eligible item in the higher-priority
repository is listed and dispatched before any item in the lower-priority one (FR-016, SC-007).

Also confirm the pause takes precedence over capacity:

```bash
robot-army pause && robot-army status
```

**Expected**: held items show `paused`, not `global_cap` — you are not sent to free capacity that
would change nothing (US3 AS4).

---

## 8 — Cleanup is off until you turn it on (FR-022, SC-013)

Take an item to a closed issue with `[cleanup]` absent.

**Expected**: the worktree and branch both survive. `cleanup_state` is `NULL`. Nothing was removed and
nothing was attempted.

---

## 9 — The happy path reclaims the disk (User Story 5, SC-008)

Set `[cleanup] on_issue_close = true`. Take one item through dispatch, push its branch, merge it, and
close the issue.

```bash
du -sh ~/GIT-worktrees/<repo>/<item>     # note the size — M0 measured 499 MB for a prepared worktree
robot-army reconcile
robot-army show <item-id>
git -C ~/GIT/<repo> branch --list 'robot-army/*'
```

**Expected**: `cleanup_state done`; the worktree directory is gone; the branch is gone. Two audit
pairs — `git.remove_worktree` then `git.delete_branch` — each written before its attempt, with
`git.delete_branch` carrying the containment evidence that authorised `force` (R12).

---

## 10 — Every guard refuses, and says why (SC-009)

The scenario that matters most, because it is the one where getting it wrong is unrecoverable. Set up
four items and close all four issues:

| Item | Setup | Expected |
|---|---|---|
| a | `touch` an untracked file in its worktree | `retained` — git refuses a dirty tree, including merely untracked files |
| b | Commit to its branch and do **not** push | `branch_retained` — worktree removed, branch kept, containment unproven |
| c | Leave its session running | `skipped` — reconsidered on a later pass, unlike `retained` |
| d | `rm -rf` its worktree directory by hand | `prunable_worktree` anomaly, not a cleanup failure |

```bash
robot-army reconcile
robot-army status
robot-army cleanup            # the explicit path, same guards
```

**Expected**: **zero** removals that should have been kept. Item b's commits still exist. Each
retention is explained in `cleanup_reason` and visible without reading the log. Item c is cleaned once
its session ends; item a stays retained until you deal with the file and ask explicitly.

Verify the branch guard's dangerous case directly:

```bash
git -C ~/GIT/<repo> log --oneline robot-army/<item-b-branch>   # the commits are still there
```

---

## 11 — Cleanup below `live` removes nothing it should not (FR-039, SC-011)

Run a cleanup-eligible cycle at `--effect-level plan`.

**Expected**: `git.remove_worktree` and `git.delete_branch` records marked simulated, carrying full
arguments; nothing removed from disk. At `local` and above the removals are real, because cleanup
follows worktree *creation*'s effect rule, not the board's.

---

## 12 — Notifications say enough and no more (User Story 6, SC-010)

Set `[notifications] events = ["failure"]` with a webhook you can watch. Force one failure and one
success.

```bash
robot-army run --once
robot-army log --since 10m | grep notify
```

**Expected**: exactly one message, for the failure. Zero for the success. The message identifies the
item, its repository, and where to look. **No credential appears anywhere** — check the log and the
delivered message, including across an authentication failure.

Then set `events` to all four kinds and work through a backlog of six or more items in one cycle.

**Expected**: at most `max_per_cycle` messages plus one summary naming how many were suppressed and of
which kinds (R15). Nothing is dropped silently.

Finally, point the webhook at something that hangs.

**Expected**: the send fails, is recorded, and dispatch and reconciliation complete normally on the
same tick (FR-035).

---

## 13 — Configuration that cannot be resolved refuses to start (FR-014, R17)

```bash
# each of these in turn, then:  robot-army doctor
[dispatch] order = "whatever"           # expect: refuses to start, names the key
[repos.x] priority = "high"             # expect: refuses to start
[repos.x] max_sessions = 0              # expect: refuses to start
[repos.x] max_sesions = 2               # typo — expect: refuses to start
[repos.x] max_sessions = 9              # with global cap 2 — expect: warns, effective cap 2
```

**Expected**: the first four exit non-zero with the offending key named. The fifth starts, warns, and
`robot-army capacity` reports the effective cap as 2 — and distinguishes "you chose 1" from "1 is what
you get by default" (US2 AS4).

---

## What CI cannot check

The same ceiling the roadmap already records. Scenarios 2, 3, 4, and 5 need a live session registry
and real `claude` processes; 9 and 10 need a real git remote and a real worktree. These are the
scenarios that would catch the worst bugs in this milestone — an under-counted cap and a wrongly
deleted branch — so they are the ones to run by hand before calling it done.
