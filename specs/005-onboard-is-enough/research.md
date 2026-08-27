# Research: Onboarding Is Enough

Phase 0 for [plan.md](plan.md). Every decision the design needed that the spec left to
implementation, with what was rejected and why. Findings that came from reading the existing code
rather than from choosing between options are marked as such — they are the ones most worth keeping.

---

## R1 — Where the resolved view lives

**Decision**: A new module `src/robot_army/repos.py` exposing two questions —
`known(conn)` returning the onboarded repository keys, and `resolve(conn, config, key)` returning a
`RepoConfig` built from the onboarding record, the `[repos.*]` section if one exists, and the
existing global defaults. Pure functions over `(conn, config)`. No writes, no audit calls.

**Rationale**: The twenty-six `config.repos` call sites divide cleanly into "which repositories are
known" and "settings for this one". Both now have two sources. Answering them anywhere other than one
place means every call site performs the join, and the join has an ordering rule (record over
section over default) that must not be re-derived by hand twelve times.

**Alternatives considered**:

- *Methods on `Config`*, alongside the existing `permission_mode_for()` and friends. Rejected on a
  hard constraint rather than taste: `Config` is constructed with no database. `doctor`'s
  configuration check, the validation path that runs before any database is opened, and a large
  number of tests all build a `Config` and would break. Threading an optional connection through it
  would make every existing accessor conditional on whether one was supplied.
- *Resolving at parse time*, producing a fully-populated `Config` that already includes derived
  repositories. Rejected because the derived set depends on the database, which reintroduces the
  same problem one layer earlier, and because it would make configuration validation depend on
  onboarding state.
- *No seam — let each call site check both.* Rejected: twelve independent implementations of a
  precedence rule is the shape that produces a bug where one call site disagrees with another, which
  is precisely what issue #8 turned out to be.

---

## R2 — Comparing a clone's origin against a repository key

**Decision**: Normalise a remote URL to `(host, owner, name)`. Strip any `userinfo@` component,
strip a trailing `.git`, lowercase all three parts for comparison. Accept the three forms git
actually produces on this machine — `git@host:owner/name.git`, `https://host/owner/name(.git)`, and
`ssh://git@host/owner/name(.git)`. A URL that does not parse into exactly that shape is a refusal,
not a pass.

**Rationale**: The five known wrong-location repositories are only detectable by comparing identity,
and the author's own clones use at least three URL spellings for the same repository — a comparison
on raw strings would refuse correct clones far more often than it caught wrong ones. Case folding is
required because the source system treats repository names case-insensitively while a Linux
filesystem does not.

The host is compared too, against the host of the configured API base. A clone of
`owner/name` on a different forge sitting at the derived path is exactly as wrong as a clone of a
different repository, and the check costs nothing extra.

**Alternatives considered**:

- *Compare only `owner/name`, ignore the host.* Rejected: it would accept a same-named repository on
  a different forge, and the failure mode is identical to the one this check exists to prevent.
- *Ask git to canonicalise via `git ls-remote`.* Rejected outright — it makes a network call per
  onboarding, to a host that may not answer, to compare two strings.

**Security note**: a remote URL may embed credentials. Stripping userinfo is not only for comparison
— FR-032 requires it before the URL reaches an audit record, an error message, or a terminal. This is
the first time this codebase reads a git remote URL, so it is the first time this exposure exists.

---

## R3 — Which remote is "the" remote

**Decision**: Verify against `origin` when it exists. When it does not but exactly one remote does,
verify against that one and say in the record which one was used. When there are several and none is
`origin`, refuse as ambiguous.

**Rationale**: This mirrors the existing `default_remote()`, which prefers `origin` and falls back to
the first remote — with one deliberate difference. `default_remote()` picks arbitrarily among several
because it is choosing where to fetch from and any answer is serviceable. Identity is not
serviceable-with-any-answer: picking arbitrarily among several remotes to decide *what repository
this is* would make the check's verdict depend on git's ordering.

**Finding from the code**: `default_remote()` returns a remote *name*, and nothing in the codebase
has ever needed a URL. `VersionControl` therefore gains `remote_url(clone_path, remote)`, with the
matching method on `SimulatedVersionControl`. Following that class's existing rule — cheap,
side-effect-free reads answer honestly rather than returning a fake — the simulated implementation
performs the real read, because at every effect level the question "what repository is at this path"
has one true answer.

---

## R4 — Detecting a primary clone rather than a linked worktree

**Decision**: The resolved path is a primary clone when its `.git` is a **directory**. In a linked
worktree `.git` is a file containing a `gitdir:` pointer.

**Rationale**: Worktrees are cut from a primary clone, as milestone 001 established. Onboarding a
linked worktree would produce worktrees-of-worktrees and a branch namespace shared with whatever owns
the primary. The `.git`-is-a-file distinction is a documented git property, needs no subprocess, and
is the cheapest check available.

**Alternatives considered**: comparing `git rev-parse --git-dir` against `--git-common-dir`, which is
the more "correct" interrogation. Rejected as a subprocess where a `stat` answers, and it gives no
better answer for the case at hand.

**Related check**: the resolved path must not sit inside `worktree_root` (FR-008). Two directories
that both believe they own a tree is a class of confusion worth one comparison to avoid.

---

## R5 — Determining ownership without enumerating 252 repositories

**Decision**: One `GET /repos/{owner}/{name}`. Ownership is `owner.login` equal to the configured
author, case-insensitively. The same response supplies the canonical repository name and the default
branch.

**Rationale**: SC-009 requires onboarding to cost at most one additional request regardless of how
many repositories the author owns. Enumerating would cost three pages today and grow. One lookup
answers three questions at once — does this repository exist, does the author own it, and what is its
canonical name — and the third matters because a case-mismatched name is otherwise diagnosed as a
missing directory.

**Consequence for issue #8**: this leaves `list_owned_repos()` with no caller unless US7 ships. That
is stated in the spec rather than resolved here, because it is a scope decision and not a technical
one. What research can say is that implementing the allowlist does **not** require the enumeration,
so "we need it for `include_owned`" is not available as a reason to keep it.

---

## R6 — What a pre-005 onboarding record means

**Decision**: Migration 005 adds four nullable columns. A row with a `NULL` clone path is treated as
requiring re-approval: dispatch refuses for that repository with a message saying to re-run
`onboard`. No backfill is attempted.

**Rationale**: Backfilling would mean writing the configured path into the record without anyone
having verified it — recording an approval nobody gave, which is the one thing the onboarding record
exists not to do. Re-onboarding is one command per repository, and on the author's machine the
`repos` table currently holds **zero rows**, so the practical cost of the strict choice is nil.

**Interruption**: the migration is a sequence of `ALTER TABLE` statements inside the existing
transactional ladder, which advances `user_version` as its last statement. Killed mid-migration, the
version does not advance and the whole thing re-runs.

---

## R7 — The polled set moving to the database changes when a repository takes effect

**Finding, not a decision.** `poll_all()` reads `sorted(config.repos)` from a `Config` loaded once at
process start, so today a newly configured repository requires a daemon restart before it is polled.
Reading the onboarding record instead means a repository onboarded **while the daemon is running** is
polled on the next cycle, with no restart.

This is strictly better and it is what US1 AS2 asks for. It is recorded here because it is an
observable behaviour change that nothing in the spec asked for directly, and because it creates a
new question the tests must answer: what a poll cycle does when a repository appears between cycles.
The answer is nothing special — `poll_state` is keyed by repository and a new key simply has no prior
state — but that is a claim to verify, not to assume.

---

## R8 — Trello card matching is a fourth consumer, and it reads paths

**Finding from the code, and the reason this section exists.** The spec frames the change as "which
repositories are known", which suggested the key set was the only thing to move. Reading
`intake.py` shows otherwise: `_key_for_path()` decides that a filesystem path pasted into a card
names a repository by comparing that path against **every configured clone path**, including parent
directories. `_offer()` gates candidates on `candidate in config.repos`, and the "no repository could
be identified" message lists `sorted(config.repos)` back to the author.

All three must consult the resolved view. If they are missed, the failure is quiet and specific: a
card naming a repository that has no `[repos.*]` section is held as `needs_info` with a reason
listing only the configured repositories, and the author is told to name a repository they already
named. Milestone 003's scenario 3 would catch it, but only against a repository onboarded without a
section — which does not exist today.

**Decision**: `intake.py` takes the resolved view. The "configured:" wording in the ambiguity message
becomes "onboarded:", because after this change that is what the list is.

---

## R9 — Where the dispatch-time re-verification goes

**Decision**: Inside `check_gates()`, which already loads the `repos` record, already raises
`DispatchBlocked`, and already runs before anything is created.

**Rationale**: The alternative was a new guard in `dispatch_item()` before `worktree.prepare()`. The
gate function is the better home for three reasons that are all about not duplicating existing logic:
it holds the record the check needs, its exception type is already handled by the caller into a
`failed` item with a reason, and every existing precondition of the same kind — onboarded, trusted,
fingerprint unchanged — is already there. Adding a fourth to a list of three is smaller than adding a
first to a list of none.

**What it checks**: the recorded path still exists, its `.git` is still a directory, and its remote
still normalises to the same repository. Three reads, no fetch.

---

## R10 — Override semantics for the shared preparation steps

**Decision**: A repository's own `post_create` **replaces** the shared default. It does not extend
it, and there is no way to ask for both.

**Rationale**: The repositories that need their own steps need *different* steps — a different
dependency manager, a different bootstrap — not the common one plus extras. Appending would make the
shared default impossible to opt out of, which would force every exception repository to work around
it. Replacement keeps the default droppable per repository, and "override" is what every other
per-repository setting in this configuration already means.

**Validation consequence**: `config.py` sums per-repository step timeouts into a startup budget
warning. The shared steps must feed the same sum for any repository that inherits them, or the
warning silently under-reports for exactly the repositories that have no section — the majority,
after this change.

---

## R11 — Refusals currently leave no record

**Finding from the code.** `onboard()` returns `EXIT_USAGE` for a missing `[repos.*]` section before
opening any `audit.action`. The refusal is printed and forgotten. Under Principle III's
reconstruction standard — answering what the system did, when, to what, and with what result, from
the log alone — a refusal is a result, and today it is unrecoverable.

This is a **pre-existing violation**, not one this milestone introduces. It is fixed here because
that early return is being replaced anyway and because this milestone adds five more refusal paths
that would inherit the same shape. Recording it as a finding rather than folding it into the new work
keeps the distinction honest: one of these refusal paths is a bug fix and five are new behaviour.

---

## R12 — What this logs, and what happens if it is killed halfway

The two questions the constitution's Development Workflow requires every plan to answer explicitly.

**What it logs.**

| Moment | Record | Detail added |
|---|---|---|
| Onboarding, before the decision | `repo.onboard` intent | resolved path, whether derived or configured, the remote consulted, the normalised comparison result, ownership verdict |
| Onboarding, after | `repo.onboard` outcome | approved or refused, and on refusal the specific cause |
| Onboarding refused before any prompt | `repo.onboard` outcome | the cause — **new**, see R11 |
| Dispatch gate, on failure only | the existing `DispatchBlocked` path | recorded path, what was found there |
| Migration | the existing migration record | from and to version |

Nothing is summarised, sampled, or rate-limited, because nothing here fires per tick. The volume is
one record per onboarding and one per failed dispatch.

**What happens if it is killed halfway.**

| Killed | State left | Resolution on re-run |
|---|---|---|
| During migration | `user_version` unadvanced, some columns possibly added | The ladder re-runs the whole migration; `ALTER TABLE ... ADD COLUMN` on an existing column errors, so migration 005 must be written to tolerate re-entry or the whole statement sequence must be inside the one transaction the ladder already provides — it is |
| After resolution, before the prompt | Nothing written | Re-run resolves again; resolution is a pure read |
| After the prompt, before the transaction | Nothing written | Re-run prompts again |
| After the transaction commits, before output | The row exists and is correct | Re-run reports the fingerprint unchanged and does nothing |
| During dispatch re-verification | Nothing written; no worktree created | The item is picked up on a later pass and re-verified |

There is no window in which a repository is half-onboarded. The record is one row written in one
transaction, and every step before it is a read.
