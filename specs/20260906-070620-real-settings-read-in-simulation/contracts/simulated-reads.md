# Contract: what `SimulatedVersionControl` may invent

Companion to [001's boundaries contract](../../001-minimum-daemon/contracts/boundaries.md), which
states the rule this refines: *reads are always real (FR-052) — a dry run that fakes its reads
tells you nothing about eligibility, which is the main thing you want to check.*

Read literally, that rule is wrong for this one class, and the gap is how issue #20 survived. A
simulated version-control boundary invents artifacts — a worktree it did not create, a branch it
did not cut — and then has to answer questions *about them*. Those answers cannot be real; there is
nothing there to read. So "read ⇒ real" fails, and in failing it stopped being a rule anyone could
apply, which left every method a case-by-case judgement. Two of the sixteen were judged wrong.

## The rule

> **The subject of the question decides, not the verb.**
>
> A method whose subject exists **independently of the simulation** — the operator's primary clone,
> its configuration, its object store — MUST answer truthfully, at every effect level, by
> delegating to the real implementation.
>
> A method whose subject is an artifact the simulation **only pretended to create** MUST answer as
> if the pretence held, and MUST carry a written reason for the value it chooses.
>
> A method that **writes** is inert regardless of subject.

The second clause is not a licence to fake: an as-if answer must be the answer that leads the
caller to the decision the real path would reach. `commits_ahead` returns `0` rather than `None`
for exactly this reason — `None` means "could not determine", which would make every simulated
cleanup retain its branch and stop the simulation describing the product.

## The table

Every member of the `VersionControl` protocol. `tests/unit/test_git_boundary.py` asserts this table
covers `VersionControl.__protocol_attrs__` exactly, so a member added without a decision fails the
suite by name.

| Member | Subject | Answer | Why |
|---|---|---|---|
| `show_file_at_ref` | the clone's object store | **real** | the file is committed in the operator's clone whatever level we simulate; this is the read the onboarding security review depends on (issue #20) |
| `list_remotes` | the clone's remote config | **real** | the identity check must reach the same verdict at `plan` as at `live` (005) |
| `remote_url` | the clone's remote config | **real** | "what repository is at this path" has one true answer (005) |
| `default_remote` | the clone's remote config | **real** | derived from `list_remotes`, which is already real; inventing `"origin"` made a local-only clone look remote-backed and suppressed the `fetch_skipped` record the real path writes (issue #20) |
| `default_branch` | the clone's `refs/remotes/<remote>/HEAD` | **real** | which branch a remote calls its default is written in the operator's clone at every effect level; an invented `"main"` would make a `plan`-level onboarding of a `master` repository print the wrong base ref and review the wrong settings (issue #150) |
| `fetch` | the network; writes refs | inert | a write |
| `add_worktree` | creates the artifact | as-if handle | a write; returns a structurally valid handle |
| `remove_worktree` | the artifact | as-if | a write |
| `delete_branch` | the artifact | as-if | a write |
| `prune_worktrees` | the clone | inert | a write |
| `fast_forward` | the operator's own clone | as-if `skipped` | the one verb that writes to the author's working clone; a dry run claiming it moved a branch it did not move is the lie effect levels exist to prevent |
| `worktree_exists` | a worktree never created | as-if `True` | answering `False` would fail every simulated item at pre-launch validation |
| `status_porcelain` | that same worktree | as-if `""` | nothing was checked out to be dirty |
| `commits_ahead` | a branch never cut | as-if `0` | `None` means "could not determine" and would retain every branch |
| `remote_branch_head` | a branch never pushed | as-if forty zeroes | the same answer `rev_parse` gives, so cleanup reaches the real path's decision |
| `rev_parse` | **mixed** — see below | as-if forty zeroes | one caller asks about a real ref, another about the pretended branch |
| `list_worktrees` | **mixed** — see below | as-if `[]` | asked about the real clone, but used to judge a worktree the simulation did not create |

## The two mixed subjects

`rev_parse` and `list_worktrees` are asked by callers on both sides of the rule, which is why the
rule is written about subjects rather than about methods, and why neither is changed.

- `rev_parse` is asked `<remote>/<base_ref>` during worktree preparation — a real subject — and
  `refs/heads/<branch>` during cleanup, which is the branch the simulation pretended to cut.
  Answering the second honestly ("no such branch") would send simulated cleanup down a different
  path from the real one.
- `list_worktrees` is asked about the real clone, but both callers use the answer to judge a
  worktree the simulation did not create, and both already fall back to a real filesystem check
  when the list is empty — so the honest answer reaches the decision by another route.

**Do not "finish the job" by making these real.** The reason is recorded at each method.

## Audit consequence

A member answering truthfully emits the **real** implementation's records — it genuinely happened,
so recording it as `simulated=True` would be a lie. This is existing behaviour for `list_remotes`
and `remote_url` and now also holds for `show_file_at_ref` and `default_remote`. No action is left
unlogged by this contract, so it claims no Principle III exception.
