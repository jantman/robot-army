# Phase 0 research: where a default branch is written down, and who is allowed to ask

Every question here was settled against the code in this repository or against `git` itself
(2.55.0, the version on this machine), not from memory.

## R1 — What does a clone actually know about its remote's default branch?

**Decision**: read `refs/remotes/<remote>/HEAD` with
`git symbolic-ref --quiet refs/remotes/<remote>/HEAD`, and strip the leading
`refs/remotes/<remote>/`.

**Measured**:

| Clone | `git symbolic-ref --quiet refs/remotes/origin/HEAD` |
|---|---|
| `git clone` of a `master` repository | `refs/remotes/origin/master`, exit 0 |
| `git clone --single-branch` of the same | `refs/remotes/origin/master`, exit 0 |
| `git init` + `git remote add` + `git fetch` | `refs/remotes/origin/master`, exit 0 |
| `git init` + `git remote add`, never fetched | no output, exit 1 |

So every clone that has ever spoken to its remote carries the answer, and the one shape that
does not is the shape that has never fetched — which is also the shape the test fixtures
build, so the fallback path is exercised by the existing suite rather than only by a new test.

**Rationale**: it is local, it costs one process, it needs no credential, and it is the same
ref `git` itself uses to resolve a bare `origin` to a branch. The full ref name is read rather
than `--short`, because `--short` returns `origin/master` and un-prefixing that means trusting
that the remote's name contains no `/`. Stripping a known `refs/remotes/<remote>/` prefix is
exact.

**Alternatives considered**:

- `git ls-remote --symref <remote> HEAD` — authoritative and never stale, but it is a network
  call, and `remote_branch_head`'s docstring already records what this codebase thinks about
  network calls in a read path: they get `FETCH_TIMEOUT`, they can fail for reasons unrelated
  to the question, and a caller then has to decide what "could not ask" means. Onboarding
  would block on a slow remote to answer a question the disk already answers.
- The GitHub API's `default_branch` — accurate, but it requires a token for a private
  repository, and it makes onboarding a network operation. FR-002 rules both of these out.
- `git remote show <remote>` — contacts the remote *and* parses prose.
- The checked-out branch (`_symbolic_head`) — that is where the maintainer happens to be
  standing, not what the repository's default is. It would have answered `master` for the
  reported case by luck and `robot-army/issue-150-…` for this very worktree.

## R2 — Where does the resolved answer live?

**Decision**: a new `repos.base_ref(config, key, vcs, clone_path, remote=None)` returning a
small frozen result carrying the ref and its provenance. Not persisted.

**Rationale**: `repos.py` is already the module that answers "what is true of this repository",
already takes a `VersionControl` for `select_remote` and `verify`, and already holds the
record-over-section-over-default join. The alternative — a new column on the `repos` table
written at onboarding — was rejected for four reasons:

1. Every other fact of this kind is re-read rather than stored. Trust is re-read at every
   dispatch; the settings fingerprint is recomputed at every dispatch and compared with the
   recorded one. A stored default branch would be the first cached copy of a property of the
   clone.
2. A stored copy can disagree with the clone it was copied from, and the disagreement is
   silent. A renamed default branch would then need `onboard --reapprove` to *become visible*,
   rather than merely to be re-approved.
3. It would need a migration, and every repository onboarded before it would answer `NULL` —
   so the reported bug would go on happening for the reporter's existing repositories until
   each was re-onboarded.
4. It is one `git symbolic-ref` per resolution, on paths that already shell out to git several
   times.

The recorded `path` is not a counter-example. Its docstring says why it is frozen: it decides
*which repository is acted upon*, and re-deriving it after approval is the guess the record
exists to avoid. A base ref decides *which branch within an already-decided repository*, and
getting it wrong fails loudly at `git worktree add` rather than quietly acting on the wrong
repository.

## R3 — Precedence, and the awkward fact about `[worker] base_branch`

**Decision**: `[repos."<key>"] base_branch` → detected → `[worker] base_branch` → `"main"`.

**The awkward fact**: `share/config.example.toml` ships `base_branch = "main"` **live** under
`[worker]`. The maintainer's real configuration is a copy of that file. So if an explicitly
written global value outranked detection, the fix would do nothing for the person who filed
the issue — and it would be impossible, from inside the loader, to tell their copied `"main"`
from a chosen one.

Two consequences:

- Detection outranks `[worker] base_branch`. The global key becomes what it can honestly be:
  the answer when the clone has none.
- The example stops rendering it live (FR-010). `KeySpec` already supports
  `active=False, why_commented=...`, and the contract's table of reasons gains a fifth row:
  *derived from the repository*. It sits beside *environment-derived* (`[terminal]
  socket_glob`) and means the same thing one layer out — the loader's default is not the
  answer, so shipping a value would override something better.

A per-repository `base_branch` still wins over everything, which is the whole point of the
override and the one case where the maintainer has demonstrably chosen a branch for *this*
repository.

**Required loader change**: today `config.parse` copies `worker.base_branch` into every
`[repos.*]` section that omits the key, so "inherited" and "explicitly set to the same value"
are indistinguishable. It becomes `""` — *not stated* — which every existing consumer already
reads correctly, because all four of them are spelled
`repo.base_branch or config.worker.base_branch`. `WorkerConfig.base_branch` likewise defaults
to `""`, with `"main"` applied at the one place that resolves.

## R4 — Who resolves, and who must not

Every call site was read. They fall into three groups.

**Resolve (has a clone and a `VersionControl`)**: `operations.onboard`, `dispatch.check_gates`,
`worktree.prepare`, `cleanup._remove_worktree`, `operations.repos` listing,
`operations.worktrees` listing, `operations._local_resume_signals`.

**Must not resolve**: `ordering.plan` and everything under it. Its docstring is explicit —
"Pure: no writes, and no I/O beyond reading the database" — because the web interface calls it
on every page render and the dispatcher calls it on every pass, and their agreement is
structural rather than maintained. Threading a `git` subprocess into it would break that
promise for one hold *message*.

The only thing it uses the base ref for is the sentence "#12 is active and has not landed on
`main` yet". **The branch name comes out of the sentence.** "has not landed yet" says exactly
as much, is true regardless of which branch it is, and cannot go stale. This is the one place
FR-006 is satisfied by removing a claim instead of by resolving it.

**Already correct**: `worktree.prepare` prefers `<remote>/<base_ref>` as the worktree's start
point when that ref exists, so a detected branch that is not checked out locally still
produces the right worktree. Nothing changes there.

## R5 — Cost and audit volume

One `git symbolic-ref` per resolution: a local ref read, no object access, no network. The
resolving paths are bounded — once per onboarding, once per dispatch, once per session, once
per cleanup, once per listed row.

It is recorded as `git.subprocess` with its argv, the same as `rev_parse`, `list_remotes`,
`default_remote` and `_symbolic_head`. No new audit action name is introduced, so
`docs/guide/audit-log.md` needs no new row — `repo.onboard`'s `detail` gains
`base_ref_source`, which that page documents as part of the record's shape.

## R6 — The simulated boundary

`SimulatedVersionControl` answers by the rule in its docstring: *the subject of the question
decides, not the verb*. The subject here is the operator's real clone and its real remote
configuration — the same subject as `default_remote`, `remote_url` and `show_file_at_ref`, all
three of which delegate to the real implementation, and two of which were bugs (#20) until
they did. `default_branch` delegates.

Getting this wrong has a precise cost, which is why it is written down rather than left to
judgement: an invented `"main"` at `plan` level would make a simulated onboarding of a
`master` repository print exactly the screen this issue is about, so the simulation would
reproduce the bug after the real path stopped having it.

## R7 — What breaks in the existing suite

`tests/conftest.py` writes `base_branch = "main"` into both the `[worker]` table and the
`[repos.*]` section of every fixture config, and the fixture clone has a remote that was never
fetched. So under the new precedence the per-repository value wins before detection is
consulted, and where it does not, detection finds nothing and the configured value is used.
Existing expectations are unchanged; the new behaviour needs fixtures that opt in by building
a clone with a real `origin/HEAD`.
