# Contract: Onboarding, resolution, and verification

What `robot-army onboard` does after milestone 005, what it refuses and with what message, and what
the rest of the system reads afterwards.

## The command

```
robot-army onboard <owner>/<name> [--reapprove] [--yes]
```

Unchanged in shape. Unchanged in that it prompts, prints committed settings in full before asking,
and refuses `--yes` when unapproved committed settings are present. It gains a resolution and
verification step ahead of all of that, and records what it resolved.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | approved and recorded, or already onboarded with nothing changed |
| 3 | refused — any resolution or verification failure, or not permitted by the allowlist |
| 4 | aborted at the prompt |

Every non-zero exit prints a message naming the cause and, where one exists, the override that
resolves it. Every non-zero exit is written to the audit log — including the ones that happen before
the prompt, which today write nothing (research R11).

## The approval screen

Three lines are added ahead of what is printed today. They come first because they answer *which
repository is about to be trusted*, which must be settled before anything about trust is read.

```
repository   : jantman/some-repo
clone path   : /home/jantman/GIT/some-repo   (derived from [paths] repo_root)
verified     : github.com/jantman/some-repo via origin
base ref     : main
trust        : accepted — ...

committed tool-permission settings at the base ref:
  ...
```

`(derived from [paths] repo_root)` reads `(configured in [repos."jantman/some-repo"])` when a section
supplied the path. FR-011 requires the distinction to be visible: the author needs to know which file
to edit when it is wrong.

> **This ordering was specified here and not delivered until milestone 011.** `onboard` composed
> the screen above the prompt exactly as written above, but `Result.say()` only appended to a list
> that the CLI printed *after* the command returned — so the process blocked for input with the
> whole screen still in memory, and the maintainer was asked to approve a repository slug they had
> just typed (issue #17). Nothing in this section was wrong; the output layer discarded it. What
> the command writes, on which stream, and what each way out of the prompt leaves behind is now
> governed by
> [`specs/011-onboard-review-before-prompt/contracts/onboard-output.md`](../../011-onboard-review-before-prompt/contracts/onboard-output.md).
> Resolution, verification, the refusal taxonomy, and what is recorded on approval remain this
> contract's.

## Resolution order

Stops at the first refusal. Everything here is a read; nothing is written until the author approves.

| # | Step | Refusal |
|---|---|---|
| 1 | Allowlist — owned, or listed in `extra_repos` | not permitted; names the setting that would permit it |
| 2 | Repository lookup — one request; yields ownership and canonical name | no such repository, or the source system is unreachable |
| 3 | Path — the section's `path`, else `<repo_root>/<name>` | — |
| 4 | Exists — the directory is present | no clone at that path |
| 5 | Primary clone — `.git` is a directory | that is a linked worktree, not a primary clone |
| 6 | Outside `worktree_root` | that path is inside the worktree root |
| 7 | Remote — `origin`, else the sole remote | no remote configured / several remotes and none named origin |
| 8 | Normalise — strip userinfo, strip `.git`, lowercase | could not read that remote URL as a repository |
| 9 | Compare — host, owner, name | **wrong repository**; names both identities |

## Refusal messages

The wording matters more here than usual, because refusal 9 is the one that fires on the five known
collisions and its message is the only thing standing between the author and an override they will
write incorrectly.

```
$ robot-army onboard jantman/zoneminder
refusing: the clone at /home/jantman/GIT/zoneminder is ZoneMinder/zoneminder,
          not jantman/zoneminder.
          The path was derived from [paths] repo_root. If your clone of
          jantman/zoneminder is elsewhere, set it explicitly:

              [repos."jantman/zoneminder"]
              path = "/where/it/actually/is"
```

```
$ robot-army onboard jantman/never-cloned
refusing: no clone at /home/jantman/GIT/never-cloned (derived from [paths] repo_root).
          Clone it there, or set [repos."jantman/never-cloned"] path.
```

```
$ robot-army onboard someoneelse/theirs
refusing: someoneelse/theirs is not owned by jantman and is not in [github] extra_repos.
          Add it to extra_repos to permit onboarding it.
```

```
$ robot-army onboard jantman/some-repo
refusing: /home/jantman/GIT/some-repo is a linked worktree, not a primary clone.
          Worktrees are cut from a primary clone; onboarding this would nest them.
```

Each names the path, how it was arrived at, and the edit that fixes it. None of them says "invalid
configuration" and stops.

## What is recorded

On approval, one row, one transaction, in addition to what onboarding writes today:

| Column | Value |
|---|---|
| `clone_path` | absolute, symlinks resolved |
| `path_source` | `derived` or `configured` |
| `verified_origin` | normalised `host/owner/name` — **never** a raw URL, which may embed credentials |
| `origin_verified_at` | now |

The audit detail on `repo.onboard` gains the same four, plus which remote was consulted and the
ownership verdict.

## What the rest of the system reads

| Consumer | Today | After |
|---|---|---|
| `poll_all()` | `sorted(config.repos)` | the onboarded set — so a repository onboarded while the daemon runs is polled next cycle, with no restart |
| `dispatch_item()` | `config.repos.get(key)` | the resolved view; a missing record fails the item as it does today |
| `check_gates()` | onboarded, trusted, fingerprint | plus: recorded path still exists, still a primary clone, still the same repository |
| `intake._key_for_path()` | every configured `RepoConfig.path` | every resolved clone path (research R8) |
| `intake._offer()` | `candidate in config.repos` | `candidate in known(conn)` |
| `robot-army repos` | one row per configured section | one row per onboarded repository, with its path and whether it was derived |
| `ordering`, `cleanup`, `reconcile`, `capacity` | `config.repos.get(key)` | the resolved view; no behaviour change |

## Re-verification at dispatch

`check_gates()` gains a fourth precondition, alongside the three it already enforces. It raises the
same `DispatchBlocked` the other three raise, which the caller already turns into a `failed` item
with a reason — so the failure path is existing code.

| Condition | Result |
|---|---|
| `clone_path` is `NULL` (onboarded before 005) | blocked: re-run `onboard --reapprove` |
| The recorded path no longer exists | blocked, naming the path; anomaly raised |
| It is no longer a primary clone | blocked, naming the path |
| Its remote no longer normalises to the same repository | blocked, naming both identities; anomaly raised |
| A `[repos.*] path` disagrees with the record | blocked: re-run `onboard --reapprove`, showing both |

Three local reads. No fetch, no network. It runs before anything is created, so a failure creates
nothing anywhere — which is the entire point (FR-029, SC-004).

## Discovery (User Story 7, droppable)

```
robot-army repos --onboardable
```

Lists repositories the author owns that are not yet onboarded, each with whether a clone was found at
its derived location and whether that clone's origin matches. Read-only; onboards nothing.

This is the only remaining candidate caller for `GitHubReader.list_owned_repos()`, which is
implemented today with none. **If this story is dropped, that method and its `Boundaries` protocol
declaration must be deleted**, not left in place — an implemented method with no caller is the exact
state issue #8 reports, and resolving the issue by reproducing it would be poor form.
