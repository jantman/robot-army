# Research: reads that must be real in `SimulatedVersionControl`

**Feature**: the onboarding security review reads real committed settings at every effect level
**Date**: 2026-09-06

Phase 0 for issue #20. Three questions had to be settled before writing the plan: what rule
decides whether a simulated boundary method fakes its answer, which methods that rule reclassifies
today, and what stops the next method being decided by coin toss.

---

## R1 — What rule decides whether a simulated read may fake its answer?

**Decision**: **the subject of the question decides, not the verb.** A method whose subject exists
independently of the simulation — the operator's primary clone, its config, its object store —
answers truthfully at every effect level. A method whose subject is an artifact the simulation only
pretended to create — the worktree it did not make, the branch it did not cut, the commit it did
not push — answers as if the pretence held.

**Rationale**: this is the rule the codebase was already following without having written it down.
`remote_url`'s docstring says it in the specific case: *"what repository is at this path" has one
true answer no matter what level we are simulating*. `commits_ahead`'s says the other half: `0`
rather than `None`, because *the simulation answers the question it was asked* about a branch it
pretended to create, and a divergence there would make every simulated cleanup retain its branch.
Both are the same rule seen from opposite sides. Stating it once turns two case-by-case judgements
into one test.

The naive rule — "reads are real, writes are simulated" (FR-052 read literally) — does not survive
contact with this class: `worktree_exists` and `commits_ahead` are reads, and making them real
would make every simulated dispatch fail pre-launch validation or retain every branch. FR-052 is
about reads that inform a *decision the operator asked the dry run to rehearse*; the subject rule
is the precise form of it for a boundary that also invents artifacts.

**Alternatives considered**:

- *Split the protocol into a real-reads half and a simulated half.* Two classes, one of them wired
  at every level, plus a composition step. More moving parts for the same behaviour — Principle I
  rejects it, and the existing `self._real` delegation already achieves it in one line per method.
- *Make the whole boundary real at `plan` and simulate only the writes.* This is a bigger claim
  than the bug supports: `plan` exists so that nothing is created, and `add_worktree` /
  `delete_branch` / `fetch` must stay inert. The wiring table stays as it is.

---

## R2 — Which methods does the rule reclassify?

Walked all sixteen protocol members against the rule. Two change; the rest are confirmed as they
stand and the reason is now written at each.

| Method | Subject | Verdict |
|---|---|---|
| `show_file_at_ref` | the primary clone's object store | **change to real** — this is the bug |
| `default_remote` | the primary clone's remote config | **change to real** — same shape, see below |
| `list_remotes` | the primary clone's remote config | already real (005) |
| `remote_url` | the primary clone's remote config | already real (005) |
| `fetch` | the network, and it writes refs | stays inert |
| `add_worktree`, `remove_worktree`, `delete_branch`, `prune_worktrees` | artifacts, and they write | stay as-if |
| `fast_forward` | the operator's own clone, and it writes to it | stays as-if — its docstring already argues this |
| `worktree_exists` | a worktree the simulation did not create | stays as-if (`True`) |
| `status_porcelain` | that same worktree | stays as-if (`""`) |
| `commits_ahead` | a branch the simulation did not cut | stays as-if (`0`) |
| `remote_branch_head` | a branch the simulation did not push | stays as-if (forty zeroes) |
| `rev_parse` | **both** — see R3 | stays as-if (forty zeroes) |
| `list_worktrees` | **both** — see R3 | stays as-if (`[]`) |

`default_remote` is included because it is the reported bug with a different name. It answers
`"origin"` unconditionally, so at `plan` a local-only clone is described as having a remote and a
clone whose only remote is `gh` is described as having `origin`. `worktree.prepare` uses the answer
to decide whether to record `fetch_skipped: the repository has no configured remote`; today that
record can never appear below `local`, so the dry run misreports what the real run would do. The
real implementation derives it from `list_remotes`, which is already real, so delegating costs one
line and one subprocess that was already being run beside it.

**Alternatives considered**: fixing only `show_file_at_ref`, as the issue's suggested fix says.
Rejected because the issue's own next sentence — *consider whether any read belongs in the
simulated class at all* — is the more useful half of the report, and because leaving a second
instance of the identical defect in the file the fix touches is how the first one survived 005.

---

## R3 — The two methods whose subject is mixed, and why they stay as-if

`rev_parse` and `list_worktrees` each serve callers on both sides of the rule. They are the reason
the rule is written about *subjects* rather than about *methods*, and the reason neither is changed
here.

- `rev_parse` is asked `remote/base_ref` by `worktree.prepare` (a real subject) and
  `refs/heads/<branch>` by `cleanup` (a branch the simulation pretended to create). Making it real
  would answer the second honestly with "no such branch", and simulated cleanup would stop
  reaching the decision the real one reaches — precisely the divergence `commits_ahead`'s docstring
  was written to prevent.
- `list_worktrees` is asked about the real clone, but its two callers use the answer to judge a
  worktree the simulation did not create. `worktree.condition` and `reconcile`'s prunable sweep
  both fall back to a real `Path.is_dir()` check when the list is empty, so the honest answer is
  already reaching the decision by another route.

**Decision**: leave both, and write the mixed subject into their docstrings so the next reader does
not "finish the job" by making them real. Splitting either into two protocol methods to separate
the subjects is speculative generality with one caller each (Principle I).

---

## R4 — What stops the next method being decided by coin toss?

**Decision**: a table in the test suite mapping **every** member of the `VersionControl` protocol to
either `real` or a one-line reason for answering as-if, asserted to cover
`VersionControl.__protocol_attrs__` exactly. Adding a method to the protocol without deciding fails
the suite, naming the method.

**Rationale**: this is the shape `effects.py` already uses for the wiring table — *written as data
rather than as branches so the test can assert the whole table* — and `test_effects.py` already
asserts that table covers exactly the nine boundaries. The same mechanism one level down. It adds
nothing to `src/`: the reasons live in the docstrings where a reader meets them, and the test holds
only the coverage assertion.

**Alternatives considered**: a `_REAL_READS` frozenset on `SimulatedVersionControl` itself.
Rejected — it would be production data with exactly one consumer, which is the shape Principle I
names.

Alongside the table, the stronger assertion is behavioural and follows the pattern
`test_the_simulated_implementation_answers_the_same_as_the_real_one` already established in
`tests/unit/test_git_boundary.py`: for each real-answering read, the simulated and real
implementations are asked the same question against the same fixture clone and must agree.

---

## R5 — What changes in the audit log, and is that acceptable?

Today a `plan`-level onboarding writes one `git.show_file_at_ref` record per settings path with
`simulated=True`. After the change it writes the real implementation's `git.subprocess` records
instead, exactly as `list_remotes` and `remote_url` already do.

**Decision**: accept it, and state it. The class docstring already gives the reason — *they
genuinely happened, so recording them as simulated would be a lie*. Under Principle III the
reconstruction standard is met either way, and it is now met more honestly: the log shows a read
that occurred rather than one that was intended. No action goes unlogged as a result of this
change, so there is no Principle III exception to declare.

---

## R6 — The approvals already recorded against a blank screen

Every repository onboarded on this installation while the read was blank has an approval row whose
fingerprint is `{}`. Nothing in this feature backfills them, deliberately.

**Decision**: rely on the gate that already exists. `check_launch_gate` compares the real
fingerprint against the recorded one and raises `DispatchBlocked` naming the added files and
pointing at `onboard --reapprove`. Before this change that comparison also read blank below `local`,
so the two blanks matched and the gate passed; after it, the real settings no longer match the empty
record and the dispatch blocks.

**Rationale**: an approval row means *a human read exactly this and said yes*. Writing hashes into
that row on the strength of a code change would forge the one assertion the table exists to make.
The block-then-reapprove path is the correction, and it is the path the product already documents.

**Alternatives considered**: a migration that clears or flags affected rows. Rejected — clearing is
indistinguishable from "never onboarded", which the gate already reports with a clearer message,
and flagging adds a column with one reader and no second use.
