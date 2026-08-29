# Implementation Plan: Spec Kit Awareness

**Branch**: `007-speckit-extensions` | **Date**: 2026-08-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/007-speckit-extensions/spec.md`

## Summary

Two reads and one paragraph of text. Before launching a session, dispatch asks whether the prepared
worktree is a Spec Kit project — the scaffolding *and* the four lifecycle commands, both halves — and
if it is, the composed prompt gains a fixed block naming the lifecycle, the convention for when it
applies, and the fact that the repository's own instructions outrank it. While the session runs,
reconciliation reads the worktree's `specs/` directory and derives which stage the run has reached
from the artifacts that are there, comparing against a baseline recorded when the worktree was
created so that six finished features already in the checkout are not mistaken for this item's
progress. The phase appears on `robot-army status`, `show`, and the web item and active views; the
repositories listing gains a column saying which clones this changes.

Nothing is written into a worktree. No extension file is read or produced. One new module, one
migration adding four columns, one config key with a per-repository override, and no new dependency.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: none added. Detection and observation use `pathlib`, `json`, and `re` from
the standard library. `httpx` remains the only runtime dependency and is not touched here.

**Storage**: the existing SQLite database. Migration 007 adds four columns to `work_items`:
`speckit_baseline`, `speckit_phase`, `speckit_feature_dir`, `speckit_phase_at`.

**Testing**: pytest. Unit tests over fixture worktrees for detection, the phase ladder, attribution,
prompt composition, and config parsing; an integration test that dispatches into a Spec Kit fixture
repository and advances the phase through a reconciliation; and one test that hashes the entire
worktree before and after both reads to prove FR-018.

**Target Platform**: the same single Linux machine. No new external surface.

**Project Type**: single Python package (`src/robot_army/`) with a CLI and a web front end.

**Performance Goals**: observation reads at most a handful of small files per active item per
reconciliation cycle. Active items are bounded by the concurrency cap (2–4 in practice), so the
whole pass is a few dozen `stat` calls — below the noise floor of the cycle it runs inside.

**Constraints**: detection MUST NOT fail a dispatch (FR-005), MUST NOT touch the network (FR-003),
and MUST NOT write inside a worktree (FR-018). Everything here is a local read.

**Scale/Scope**: ~250 onboarded-eligible repositories, of which more than half use Spec Kit; four
lifecycle stages; one new module of roughly 200 lines plus wiring.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. Simplicity First — **PASS**

- **No new dependency.** Reading four file paths and one markdown file needs no library. No YAML
  parser is added, which is a direct consequence of the spec's decision not to touch
  `.specify/extensions.yml`.
- **No new abstraction.** Detection is a function returning a frozen dataclass; there is no
  "project type" plugin interface with one implementation, and no strategy object. If a second
  framework ever wants the same treatment, the second caller is what will justify the shape — not
  this one.
- **One new module.** `src/robot_army/speckit.py` holds the predicate, the ladder, and the prompt
  block. It is called from three places that already exist (`dispatch.build_launch_plan`,
  `reconcile.reconcile`, `operations.repos`) and adds no new command, no new state, and no new
  daemon loop.
- **The deliberate omission is the biggest simplicity win.** The spec's Out of Scope section rejects
  extension hooks, and with them the whole machinery of writing into worktrees, excluding those
  writes from git, honouring effect levels on them, standing down on a tracked registration, and
  carrying a second, weaker class of evidence in the record.
- **One config key.** `[speckit] enabled`, plus a per-repository override, which FR-011 requires. It
  follows the existing `permission_mode_for` / `model_for` pattern exactly rather than inventing a
  second override mechanism.

### II. Single-User, Local-First — **PASS**

Every operation is a local filesystem read of a path the daemon already owns or already reads. No
network request, no new credential, no new persistent location. Detection reads the worktree for
dispatch and the primary clone for the repositories listing — both paths the system already opens.

### III. Total Accountability — **PASS, with one documented omission**

**What this logs:**

| Action | When | Detail |
|---|---|---|
| `speckit.detect` | once per dispatch, before the prompt is composed | outcome (detected / not), which half was missing, which command form was found, the path read, and whether the behaviour was suppressed and by which setting |
| `speckit.phase` | on a phase *transition* only | from, to, the feature directory it was derived from, and whether the directory changed |
| `speckit.baseline` | folded into the existing `worktree.prepare` outcome rather than a separate record | the feature directories present at creation |

**What this does not log, and why.** A reconciliation pass that observes no change writes nothing.
FR-014 requires this and the constitution permits it under the exception path: the observation is a
read that changes no state outside the process, and with a 60-second cycle and long-running sessions
the alternative is a log where the overwhelming majority of lines say a phase did not change. The
reconstruction standard is still met — every transition is recorded with its time, so the log answers
"when did this reach plan?" exactly. This is the one omission and it is named here as Principle III
requires.

Reads themselves are not logged individually. Detection produces a decision and the decision is
logged; logging each `Path.exists()` would bury the decision it supports.

### IV. Interruption Tolerance — **PASS**

**What happens if it is killed halfway:**

- *Between worktree preparation and the baseline write.* They commit in the same transaction as
  `worktree_path` and `branch`, so this cannot produce a half-state. If the process dies before the
  transaction, the whole preparation is redone.
- *A baseline that is NULL* — an item from before this migration, or one whose preparation predates
  it — means no phase is ever reported for that item, and one record says so. Conservative by
  construction: the alternative (deriving a baseline late) would silently classify the session's own
  new feature directory as pre-existing and then report nothing anyway, without saying why.
- *Mid-observation.* Nothing is written until the derivation completes; a kill leaves the previous
  phase and the next pass re-derives from the same files.
- *The phase write itself.* The audit line is flushed before the transaction commits, following the
  existing ordering, so a crash can leave a logged transition with the column unchanged. The next
  pass re-derives the same phase from the same files and rewrites it — the record shows the
  transition twice at worst, never a change that happened with no line for it.
- *Daemon down for hours.* Phase is derived from files that are still on disk, so a restart reports
  the current stage without having watched any intermediate step (FR-017).
- *Worktree removed by cleanup.* Observation never clears a recorded phase. A missing worktree, a
  missing `specs/` directory, and a missing artifact are all "nothing new to say", never "the phase
  is gone".

No network call is added, so the timeout and retry rules have nothing to bind to here.

### V. Public Code, Unsupported Project — **PASS**

No credential, hostname, or personal data is read or written. The prompt block is fixed text
committed in the repository. README gains a section; nothing is packaged or published.

### Development Workflow — **PASS**

Unit tests accompany every unit of behaviour, with the failure and interruption paths covered as the
constitution requires for state machines and code parsing external input: the phase ladder parses
someone else's markdown, so malformed, empty, and absent files are tested alongside the happy path.

## Project Structure

### Documentation (this feature)

```text
specs/007-speckit-extensions/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── detection.md     # the predicate, the ladder, the attribution rule
│   ├── prompt.md        # the exact guidance text and where it sits
│   └── config.md        # the [speckit] section and the per-repo override
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/robot_army/
├── speckit.py           # NEW — detection, the phase ladder, the prompt block
├── prompt.py            # compose() gains an optional speckit block between the
│                        #   repository's instructions and the issue
├── dispatch.py          # build_launch_plan() detects, records, and passes the block
├── worktree.py          # prepare() returns the baseline it observed
├── reconcile.py         # one new pass over active items; one new summary counter
├── migrations.py        # migration 007
├── models.py            # WorkItem gains four fields
├── config.py            # [speckit] enabled; RepoConfig.speckit; speckit_enabled_for()
├── operations.py        # repos listing gains a column; show() gains a line
└── web/pages.py         # item and active views show the phase

tests/
├── unit/
│   ├── test_speckit_detect.py       # both halves, both command forms, unreadable
│   ├── test_speckit_phase.py        # the ladder, malformed and absent files
│   ├── test_speckit_attribution.py  # baseline, stale artifacts, two features, no regress
│   ├── test_speckit_prompt.py       # composition order, determinism, byte-identity
│   └── test_speckit_config.py       # default, global off, per-repo off, suppression record
└── integration/
    └── test_speckit_dispatch.py     # dispatch → prompt → reconcile → phase advances,
                                     #   and the worktree is byte-identical afterwards
```

**Structure Decision**: the existing single-package layout. The new module sits beside the other
policy modules (`ordering.py`, `capacity.py`, `cleanup.py`) rather than under `boundaries/`, because
it is not a boundary: it reads the local filesystem directly, the way `prompt.read_instructions`
already does, and inventing a boundary for `Path.exists()` would be exactly the speculative
generality Principle I forbids.

## Complexity Tracking

> No Constitution Check violations. This section is empty by design.

## Constitution Re-Check (post-design)

Re-evaluated against the Phase 1 artifacts. Still **PASS**, with three things the design added that
the pre-research gate could not have judged:

- **Principle I.** The design got *smaller* during Phase 0, not larger. Two candidate mechanisms were
  removed by measurement rather than by preference: `.specify/feature.json` is gitignored, so nothing
  depends on it (research R3), and no `git` subprocess is invoked anywhere, so the argument about
  whether `git status` refreshing its index counts as a write never has to be had (research R9).
  What remains is `Path.exists()` in four places.
- **Principle III.** The design added one thing to log that the gate did not anticipate: a `NULL`
  baseline is recorded once per item with its reason, rather than producing silence that looks
  identical to "no progress yet". The single omission is unchanged and remains named: no record for a
  cycle in which nothing changed.
- **Principle IV.** `data-model.md` rule 4 — absence never clears a recorded phase — was added
  because cleanup removes worktrees under items that still exist. Without it, the ordinary operation
  of milestone 004 would have silently erased history that the log has no way to restore.

No violations to justify, so **Complexity Tracking stays empty**.
