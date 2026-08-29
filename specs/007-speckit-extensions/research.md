# Phase 0 Research: Spec Kit Awareness

Nine questions. Each was settled against the worktrees and installations actually on this machine,
not against Spec Kit's documentation, because what the daemon reads is what is on disk.

---

## R1 — What counts as evidence that a worktree is a Spec Kit project?

**Decision.** Two independent halves, both required (FR-002):

1. **Scaffolding** — `.specify/` is a directory *and* `.specify/templates/spec-template.md` is a
   file. The template is the file `/speckit-specify` copies to produce a spec; without it the
   lifecycle cannot start regardless of what else is present.
2. **Commands** — all four lifecycle commands are present, each in either of the two forms the
   Claude integration installs:
   - `.claude/skills/speckit-<name>/SKILL.md` (skills mode — what this repository has)
   - `.claude/commands/speckit.<name>.md` (commands mode)

   for `<name>` in `specify`, `plan`, `tasks`, `implement`. Mixed forms are accepted; the form found
   is recorded.

**Rationale.** The failure this guards against is concrete: a repository that carries `.specify/`
because it was initialised once, in a checkout with no agent commands, would get a prompt naming four
commands that do not exist — and the session's only sensible response is to ignore the instruction it
was just given, which looks exactly like the instruction not working. Requiring the commands makes
"detected" mean "the flow the prompt names can actually run".

**Alternatives considered.**

- *`.specify/` alone.* Rejected: the case above, and it is the first thing a partially removed
  installation leaves behind.
- *`.specify/integrations/claude.manifest.json`.* It is the most precise statement of what the Claude
  integration installed — a file list with hashes. Rejected as the primary test because it is a
  Spec Kit implementation detail of a specific version, absent from older installations, and reading
  it means trusting a manifest over the filesystem it describes. It stays useful as *evidence* in the
  record.
- *Running `specify check`.* Rejected outright: FR-003 forbids executing anything from the
  repository, and shelling out to a tool that may not be installed is a failure mode where a read
  suffices.

---

## R2 — How is the lifecycle phase derived from files?

**Decision.** A four-rung ladder over the artifacts inside one feature directory, highest rung wins:

| Rung | Evidence |
|---|---|
| `specify` | `spec.md` exists |
| `plan` | `plan.md` exists |
| `tasks` | `tasks.md` exists |
| `implement` | `tasks.md` contains at least one completed task marker (`- [X]` or `- [x]`) |

**Rationale.** The first three are exactly what the three commands produce, at documented paths, and
each is written by the command that names the stage. The fourth needs something else because
`/speckit-implement` writes no new file of its own — what it does is tick tasks off. A ticked task is
therefore the only file-visible proof that implementation started, and it is proof of the thing
itself rather than of a side effect.

**Alternatives considered.**

- *Commits on the branch.* Rejected: a session commits for many reasons, and committing the spec
  itself would register as implementation. It also cannot distinguish a session that committed
  nothing yet from one that never started.
- *Source files modified outside `specs/`.* Rejected for the same reason plus a worse one — it would
  make the phase depend on a diff of the whole worktree, which is expensive and noisy where a
  checkbox is neither.
- *`.specify/feature.json` as the source of the current stage.* It names the *directory*, not the
  stage, so it cannot answer this question. See R3 for the other thing it cannot do.

---

## R3 — How is progress attributed to *this* work item?

**Decision.** A **baseline** — the set of feature directory names present in the worktree the moment
it was prepared — is recorded on the work item. A phase is derived only from a feature directory that
is **not** in that baseline.

**Rationale.** This is the edge case with teeth. A fresh worktree of this repository contains six
finished features, each with `spec.md`, `plan.md` and a `tasks.md` full of ticked boxes. Without a
baseline, every item would report `implement` the instant its worktree existed, and the phase column
would be worse than useless — it would be confidently wrong on every row.

`/speckit-specify` always creates a *new* feature directory. So "a directory that was not here when
the worktree was made" is precisely this session's feature, with no heuristics and no timestamps.

One measurement settled a tempting alternative: **`.specify/feature.json` is gitignored** — it is
listed in `.specify/.gitignore` as machine-local state. A fresh worktree therefore has no pointer at
all, so nothing can be read from it until the session writes one, and it cannot be the baseline, the
attribution mechanism, or a required input. It may exist and it may be stale; nothing here depends on
it either way.

**Alternatives considered.**

- *Modification times against the worktree's creation time.* Rejected: `git worktree add` stamps
  every checked-out file with the creation time, so the boundary between "was already here" and
  "written a second later" is exactly where the resolution runs out.
- *`git status --porcelain` for untracked artifacts.* It answers correctly right up until the session
  commits its spec, at which point the phase would vanish. Covering both cases needs `status` **and**
  a diff against the merge base — two mechanisms where one column does.
- *Deriving the baseline lazily on first observation.* Rejected: for an item whose baseline is
  missing, the session may already have created its directory, which would then be classified as
  pre-existing. The result is the same silence with none of the honesty; a NULL baseline reports no
  phase and says why.

---

## R4 — Where does observation run?

**Decision.** Inside `reconcile.reconcile()`, as one pass over items in `active` and
`awaiting_review` whose worktree still exists. It contributes a counter to the existing
`ReconcileResult` summary.

**Rationale.** Reconciliation is the module whose stated job is making recorded state match physical
reality, runs on startup *and* on a timer, and already walks worktrees for the sweep. Phase
observation is literally that job. Putting it in the poll loop instead would tie a local filesystem
read to the GitHub polling interval and to network health, for no reason.

`awaiting_review` is included so that the last stage a session reached is observed after it exits,
rather than frozen at whatever the final cycle happened to catch. Terminal states are not observed:
the stored phase is history at that point.

**Alternatives considered.** A dedicated watcher (a new loop, a new thread, an inotify dependency)
— rejected on Principle I; the answer changes on the scale of hours and a 60-second cycle is already
an order of magnitude finer than it needs to be.

---

## R5 — Where does the guidance sit in the prompt, and what does it say?

**Decision.** A fixed block between the repository's own `.claude/robot-army.md` instructions and the
issue, containing: the lifecycle in order, the statement that the issue's text is the feature input,
the convention for when the lifecycle applies, an explicit statement that the judgement belongs to
the session and nothing verifies it, and an explicit deference to the repository's own instructions
above. The exact text is [`contracts/prompt.md`](contracts/prompt.md).

**Rationale.** Position encodes precedence, which is how `prompt.py` already works: repository
instructions are *prepended* so they frame everything after them. Putting the generic block after
them preserves that, and the deference sentence makes the ordering explicit rather than implied.

The block is constant text — it does not vary with which command form was detected or which files
were found — which is what makes FR-009's determinism trivially true and testable. Both forms are
invoked as `/speckit-specify` regardless, so there is nothing to vary.

**Alternatives considered.**

- *Appending after the issue body.* Rejected: an issue body can be 60 000 characters, and guidance
  that arrives after it is guidance the session reads last.
- *Naming the detected files in the block.* Rejected: it makes the prompt vary with incidental facts,
  and the session can look at the repository itself.

---

## R6 — Every issue, or only some?

**Decision.** Settled in the specification (FR-008): the prompt states the convention and the session
judges. The daemon neither selects nor verifies.

**Rationale and consequences** are in the spec's Assumptions. What matters here is the design
consequence: because nothing is selected in advance, an item that shows no phase is a *correct*
outcome and must never be treated as a stall, an anomaly, or a failed instruction (FR-016). Several
places in the codebase would otherwise be tempted to read absence as a problem — the interrupted
view, the health signal, the anomaly table. None of them learn about phase at all.

---

## R7 — What is the configuration surface?

**Decision.** A `[speckit]` section with one key, `enabled` (default `true`), plus a per-repository
`speckit` override in the existing `[repos.*]` table, resolved by `Config.speckit_enabled_for(key)`
returning both the answer and which setting produced it. Full shape in
[`contracts/config.md`](contracts/config.md).

**Rationale.** FR-011 requires both scopes. The resolution helper returns the *reason* as well as the
answer because the suppression has to appear in the record (FR-011) and in the repositories listing
(FR-022), and reconstructing "which setting turned this off" at each call site is how the two end up
disagreeing.

**Alternatives considered.** A key on `[worker]` — rejected, since `[worker]` is about how the
session binary is invoked and this is not. Per-repository opt-in with no global default — rejected in
the spec, and for the reason recorded there.

---

## R8 — What does this store, and does storing a phase contradict "derived, not stored"?

**Decision.** Four columns on `work_items` (see [`data-model.md`](data-model.md)). The stored phase is
a **cache whose only job is transition detection**.

**Rationale.** FR-014 requires one record per transition rather than one per cycle, and "did this
change?" is unanswerable without the previous value. The worktree remains the source of truth: every
cycle re-derives from files and the column is overwritten with what was derived. The one place the
column outlives its source is after cleanup removes the worktree, where it becomes the item's last
known stage — which is history, correctly, and is why observation never clears it.

---

## R9 — How is "writes nothing" (FR-018) actually guaranteed?

**Decision.** By construction and by a test that hashes the worktree.

By construction: detection and observation use `Path.exists()`, `Path.is_dir()`, `iterdir()`, and
`read_text()`. No `git` subprocess is invoked by either — which matters, because `git status` can
refresh `.git/index` as a side effect and the honest way to avoid arguing about whether that counts
is not to run it. (`--no-optional-locks` exists for exactly that argument; not needing it is better.)

By test: an integration test snapshots every path under the worktree with its size and content hash,
runs a full dispatch and a reconciliation pass, and asserts the snapshot is identical. That is
SC-004 made mechanical rather than aspirational.

**Alternatives considered.** Asserting only that `git status` is clean — rejected: it would miss an
ignored file, and ignored files are exactly what an injection would most plausibly write.
