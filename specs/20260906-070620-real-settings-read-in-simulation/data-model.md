# Data Model

**Feature**: the onboarding security review reads real committed settings at every effect level
**Date**: 2026-09-06

**No schema change. No migration.** This feature changes which *values* an existing column
receives, not the shape of anything. It is recorded here because the meaning of one existing
column silently changed while the bug was live, and that is the fact a future reader needs.

---

## `repos.settings_fingerprint`

**Shape (unchanged)**: a JSON object mapping each committed settings path to the SHA-256 of its
content at the base branch tip, or `NULL` when the repository commits none.

**Meaning (unchanged)**: *a human read exactly these files and approved this repository for
dispatch.*

**What was wrong**: below effect level `local` the mapping was computed from a boundary that
answered "no such file" for every path, so an empty mapping was written for every repository —
including repositories with committed settings that were never shown to the human. The column's
meaning and the value in it disagreed.

**What changes**: nothing about the column; the value written is now the one the meaning claims.

### Rows already written

Rows approved while the read was blank hold an empty mapping. Nothing in this feature rewrites
them (research R6), because writing hashes into an approval row on the strength of a code change
would forge the row's only assertion.

Instead they are caught by the gate that already exists. At dispatch, `check_gates` compares
the repository's real fingerprint against the recorded one:

| Recorded | Real | Before this fix | After this fix |
|---|---|---|---|
| `{}` | `{}` (genuinely no settings) | dispatch proceeds | dispatch proceeds — correct |
| `{}` | `{settings.json: …}` | **dispatch proceeds** — the bug | **blocked**, `added: ['.claude/settings.json']`, `onboard --reapprove` |
| `{settings.json: h}` | same `h` | proceeds | proceeds |
| `{settings.json: h}` | different hash | blocked | blocked |

The second row is the one that changes, and it is the point of the feature: the wrong approvals
already recorded become visible instead of standing.

---

## Committed settings review (in-memory, not persisted)

The mapping `path → full text` that the approval screen prints, and the mapping `path → SHA-256`
that is recorded. Both are derived from the same read of the same two paths at the same ref, so
they cannot disagree about what was found — which is why the screen the human read and the hashes
the record holds are the same evidence.

| Field | Source | Note |
|---|---|---|
| path | the fixed pair of reviewed settings paths | unchanged by this feature |
| content | the git object store at the base ref | **now real at every effect level** |
| hash | SHA-256 of that content | absent path ⇒ absent key, so a later *appearance* is a difference |

The base ref is the base branch tip, not the working tree, because that is what a freshly created
worktree will contain. Unchanged; restated because the fix must not be "read the working tree,
which is easier to get at".

---

## State transitions

None. No state machine is touched. The approval row is still written only after the human answers,
and still only on approval.
