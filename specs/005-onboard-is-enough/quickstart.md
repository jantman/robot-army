# Quickstart: Validating Onboarding Is Enough

Runnable scenarios that demonstrate the milestone end to end. Two of them need things no fake can
supply — a real clone of the wrong repository sitting at a derived path, and a clone that moves after
approval — and those two are the ones worth the session.

Referenced detail lives in [contracts/config.md](contracts/config.md),
[contracts/onboarding.md](contracts/onboarding.md), and [data-model.md](data-model.md) rather than
being repeated here.

## Prerequisites

- Milestones 001–004 working: `robot-army doctor` passes, a daemon starts, an issue can be
  dispatched.
- **The verification round in [issue #1](https://github.com/jantman/robot-army/issues/1) should be
  complete before this milestone is implemented**, so that round verifies the system as built rather
  than one changing underneath it.
- At least one repository the author owns with a clone at `<repo_root>/<name>` and **no**
  `[repos.*]` section.
- The five known wrong-location repositories still in the state that makes them wrong. On the
  author's machine that is `jantman/zoneminder`, `jantman/troposphere`,
  `jantman/Trello-Desktop-MCP`, `jantman/ford-f150-gen14-can-bus-interface`, and
  `jantman/docker-zoneminder-OLD` — verify with `git -C ~/GIT/<name> remote get-url origin` before
  relying on them.

```bash
robot-army doctor
robot-army repos          # note what is listed BEFORE the change; this is the comparison
```

---

## 1 — Onboard, and nothing else (User Story 1, FR-015, SC-001)

The headline. Pick a repository with a clone at the conventional location and no section.

```bash
grep -c 'repos."owner/name"' ~/.config/robot-army/config.toml     # expect 0
robot-army onboard owner/name
robot-army repos
```

**Expected**: the approval screen shows the clone path with `(derived from [paths] repo_root)` and a
`verified:` line naming the origin it checked. After approval the repository appears in
`robot-army repos`. **No file was edited.**

Then label an issue in it.

**Expected**: it becomes a work item and dispatches on the ordinary path, into a worktree cut from
the derived clone, running the shared `[hooks] post_create` steps.

---

## 2 — Onboarding while the daemon is running (research R7)

The behaviour change nothing asked for and everything gets. With the daemon already running, onboard
a second repository from another terminal.

```bash
# daemon already up, started before this repository existed to it
robot-army onboard owner/second
robot-army log --since 2m | grep poll
```

**Expected**: the repository is polled on the next cycle. **No daemon restart.** Today the polled set
comes from a config loaded at process start, so this is impossible; after this milestone it comes
from the onboarding record.

---

## 3 — The wrong repository is refused (User Story 2, FR-009, SC-003)

**The scenario that justifies the milestone's size**, and the one no fake establishes. Run all five.

```bash
for r in zoneminder troposphere Trello-Desktop-MCP \
         ford-f150-gen14-can-bus-interface docker-zoneminder-OLD; do
  robot-army onboard "jantman/$r"; echo "  -> exit $?"
done
robot-army repos
```

**Expected**: five refusals, each exiting `3`, each naming **both** the repository asked about and
the repository actually found — `ZoneMinder/zoneminder`, `coxmediagroup/troposphere`,
`agrath/Trello-Desktop-MCP`, `jantman/ford-f150-can-experiments`, `jantman/docker-zoneminder`. None
of the five appears in `robot-army repos`.

**Why a fake cannot do this**: the failure is not a missing directory. Each path exists, is a valid
git repository, and has a working tree. A fake can reproduce the *state*; only these five prove the
comparison reads a real remote correctly at the moment it matters. If any one of them onboards
successfully, FR-009 is not implemented and nothing else in this milestone is worth believing.

Then fix one and confirm the override works:

```toml
[repos."jantman/zoneminder"]
path = "~/GIT/jantman-zoneminder"
```

**Expected**: it onboards, the screen says `(configured in [repos."jantman/zoneminder"])`, and the
origin check runs against the configured path too.

---

## 4 — Every other refusal says which one it is (FR-009)

Five causes, five distinct messages. None may degrade to a generic failure.

| Setup | Expected refusal |
|---|---|
| A repository with no clone at the derived path | names the path, the setting it came from, and the override |
| A path pointing at a linked worktree | names it as a linked worktree, not a primary clone |
| A path inside `worktree_root` | names the collision |
| A clone with no remote at all | names the missing remote |
| A clone with several remotes, none named `origin` | names the ambiguity rather than picking one |

```bash
robot-army onboard owner/name; echo "exit=$?"      # after each setup
robot-army log --since 5m | grep repo.onboard
```

**Expected**: exit `3` every time, and — the part that is new — **an audit record for every one of
them**. Refusals write nothing today (research R11); a refusal with no record is the failure this
scenario exists to catch.

---

## 5 — The clone moved (User Story 5, FR-028, SC-005)

The reason resolution happens at onboarding rather than at dispatch.

```bash
robot-army onboard owner/name            # approve
mv ~/GIT/name ~/GIT/name-moved           # after approval
# label an issue, let it dispatch
robot-army show <item-id>
robot-army anomalies
```

**Expected**: the item lands in `failed` naming the **recorded** path, an anomaly is raised, and
**no worktree is created anywhere**. Specifically it must not re-derive, and it must not find some
other directory that happens to match the name.

Then the nastier half:

```bash
mv ~/GIT/name-moved ~/GIT/name-real
git clone https://github.com/someone/name ~/GIT/name    # a DIFFERENT repository, same name
robot-army retry <item-id>
```

**Expected**: refused again, naming both identities. The path exists and is a valid clone; it is the
wrong one. This is scenario 3's failure arriving months later, and it is the case that a
re-derivation design would silently get wrong.

```bash
robot-army onboard owner/name --reapprove     # points at the real clone
```

**Expected**: re-resolves, re-verifies, and dispatch resumes.

---

## 6 — Exceptions keep working (User Story 3, SC-008)

```bash
robot-army repos --json > /tmp/before.json
# ... implement ...
robot-army repos --json > /tmp/after.json
```

**Expected**: for every repository that had an explicit `path` and its own `post_create`, nothing
about what runs or where it runs changed. This is the scenario that makes the change adoptable rather
than a migration.

Then change a configured `path` on an already-onboarded repository.

**Expected**: dispatch is **blocked** pending `onboard --reapprove`, which shows the recorded path
and the configured one. It does not silently take effect, and it does not silently lose.

---

## 7 — Shared preparation steps (User Story 4, FR-019 through FR-022)

Set `[hooks] post_create`, onboard two repositories, and give one its own steps.

```bash
robot-army run --effect-level local --once
robot-army show <item-a>    # inherited the shared steps
robot-army show <item-b>    # ran its own, and NOT the shared ones
```

**Expected**: the override **replaces** rather than appends. Then remove `[hooks] post_create`
entirely and confirm a repository with no section runs no preparation steps at all — today's
behaviour, preserved.

Also confirm the budget warning:

```bash
robot-army doctor    # with shared steps whose timeouts exceed the startup budget
```

**Expected**: the warning counts the inherited steps for **every** repository that inherits them.
Counting them once under-reports for the majority of repositories after this milestone.

---

## 8 — A section is no longer evidence (FR-016, FR-017)

Write a `[repos.*]` section for a repository and do **not** onboard it.

```bash
robot-army repos
robot-army run --once
robot-army log --since 5m | grep <that-repo>
```

**Expected**: it is **not** polled, **not** dispatchable, and `robot-army repos` says it is not
onboarded rather than listing it as known. This is the one intentional breaking change in the
milestone: such a repository was never dispatchable, and the system stops pretending to watch it.

---

## 9 — The allowlist refuses, and is not a security boundary (User Story 6, FR-023 through FR-027)

```bash
robot-army onboard someoneelse/theirs      # not owned, not listed
robot-army onboard jantman/typoed-nmae     # owned? no such repository
```

**Expected**: refused, naming `extra_repos` in the first case and the missing repository in the
second. Then set `include_owned = false` and confirm an owned repository is refused naming
`include_owned`.

Then the case that must **not** fail: remove an already-onboarded repository from `extra_repos`.

**Expected**: it keeps working. The allowlist governs onboarding, not continued operation (FR-027).

Confirm the request count while doing all of this:

```bash
robot-army log --since 10m | grep -c 'github.*"/repos/'
```

**Expected**: one lookup per onboarding attempt. Not 252, not three pages. If enumeration appears
here, FR-025 is not implemented and SC-009 fails.

---

## 10 — Trello cards still resolve (research R8)

The consumer the spec did not mention. With a repository onboarded and **no** section for it, put a
card on the board naming it by its GitHub URL.

```bash
robot-army run --once
robot-army cards
```

**Expected**: the card resolves to that repository and files an issue. Repeat naming it by a
filesystem path *inside* its clone.

**Expected**: also resolves — `_key_for_path` compares against resolved paths, not configured ones.
If this fails, the card is held as `needs_info` with a reason listing only the repositories that have
sections, which is the quiet failure R8 exists to prevent.

---

## 11 — No credential reaches the record (FR-032)

The first time this codebase reads a git remote URL is the first time this exposure exists.

```bash
git clone https://user:fake-token@github.com/jantman/name ~/GIT/cred-test
# point a repo's path at it, onboard, then:
grep -r 'fake-token' ~/.local/state/robot-army/ ; echo "exit $?"    # expect 1
robot-army repos --json | grep -c 'fake-token'                      # expect 0
```

**Expected**: no match anywhere — not in the audit log, not in `verified_origin`, not in any
terminal output, and not in the refusal message if the comparison fails. The record stores the
normalised `host/owner/name`, never the raw URL.

---

## 12 — Migration 005 (data-model.md)

```bash
cp ~/.local/state/robot-army/state.db /tmp/pre-005.db
robot-army doctor
python3 -c "import sqlite3;print(sqlite3.connect('$HOME/.local/state/robot-army/state.db').execute('pragma user_version').fetchone())"
```

**Expected**: `user_version` 5, `doctor` passes its schema check, and every pre-existing row still
reads back with its fingerprint intact.

Then the pre-005 row case: with a row whose `clone_path` is `NULL`, attempt a dispatch.

**Expected**: blocked, naming `onboard --reapprove`. **Not** backfilled, and **not** guessed at —
writing a path nobody approved into an approval record is the one thing the table exists not to do.

---

## 13 — Discovery (User Story 7, droppable)

```bash
robot-army repos --onboardable
```

**Expected**: repositories the author owns that are not yet onboarded, each showing whether a clone
was found at its derived location and whether the origin matches. Already-onboarded repositories are
marked, not offered.

**If this story was dropped**, confirm instead that `list_owned_repos()` and its `Boundaries`
protocol declaration are **gone**:

```bash
grep -rn 'list_owned_repos' src/ ; echo "exit $?"    # expect 1
```

An implemented method with no caller is the state issue #8 reports. Resolving that issue while
leaving one behind would be a poor joke.

---

## What CI cannot check

Scenarios 3 and 5 need real clones of the wrong repositories at real paths — 3 needs the five that
exist on this machine today, and 5 needs a clone to move out from under an approval that already
happened. Scenario 9's request count needs a real account with 252 repositories to be meaningful; a
fake with three would pass an implementation that enumerates.

Everything else has mechanical coverage. These three are the ones that would catch the failure this
milestone is built to prevent: a worktree and a branch created in a repository the author never
named.
