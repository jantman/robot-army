# Data Model: Read Before You Approve

**Feature**: 011-onboard-review-before-prompt | **Date**: 2026-08-29

**No schema change. No migration.** This feature moves when something is displayed and adds two
values to a field that already exists. The `repos` table, the `work_items` table, the session
tables and every column on them are untouched, and nothing here needs `migrations.py` opened.

## Entities

### Approval screen

Not persisted. Composed in memory during an onboarding run and written to the maintainer's
terminal; it exists only for the duration of the run.

| Part | Source | Present when |
|---|---|---|
| repository key | the argument | always |
| clone path, and `derived` / `configured` | `repos.verify()` | always |
| verified origin and the remote consulted | `repos.Verification.verified_line()` | always |
| recorded path, with a change marker | the existing `repos` row | `--reapprove` on a known repository |
| base ref | the `[repos.*]` section, else `[worker] base_branch` | always |
| trust verdict and explanation | `dispatch.is_trusted()` | always |
| committed settings, in full, or the line saying there are none | `dispatch.read_committed_settings()` | always |
| fingerprint diff against the approved version | the existing `repos` row | `--reapprove` on a known repository |

**Lifecycle**: composed only after resolution and verification both succeed — a refused run has
no approval screen. Written out exactly once, at the boundary between the screen and the
outcome. After that write the run holds no copy of it, which is the mechanism that makes
"printed once" structural rather than a thing to remember.

**Invariant**: the screen is complete before it is written. There is no partial flush and no
second flush; the maintainer never sees half a screen, and never sees a screen grow after the
prompt has appeared below it.

### Onboarding outcome record

Persisted, in the existing append-only audit log, as the existing `repo.onboard` action. This
feature adds no field and no action — only two values to the `cause` field on the refusal
outcome that milestone 005 introduced.

| `cause` | Meaning | Status |
|---|---|---|
| `not_permitted`, `no_such_repository`, `malformed_key`, `no_clone`, `linked_worktree`, `inside_worktree_root`, `no_remote`, `ambiguous_remote`, `unparseable_url`, `wrong_repository`, `source_unreachable` | refused during resolution or verification | unchanged |
| `unapproved_committed_settings` | `--yes` refused to skip an unreviewed settings change | unchanged |
| `aborted_at_prompt` | the maintainer answered no | unchanged |
| `interrupted_at_prompt` | the maintainer interrupted the run at the prompt | **new** |
| `no_answer_available` | input ended before an answer was given | **new** |

**Invariant after this feature**: every terminating path through `onboard` leaves exactly one
`repo.onboard` outcome record — an approval or a refusal with a cause. Before it, two paths
(interruption, and end of input) left none.

**Detail payload**: the two new causes carry the same detail as the existing prompt-stage
refusals — the repository key as `entity_id`, plus the resolved clone path and its source. They
are written by the same helper, so there is nothing new to keep in step.

## What is deliberately not modelled

- **The stream a line was written to.** It is decided at the moment of writing from the exit
  code and the output mode, and is not stored, inspected, or reachable afterwards.
- **Whether the maintainer read the screen.** Unknowable and not worth approximating. The
  feature guarantees the screen was delivered before the question; what happened next is the
  maintainer's.
