# Contract: what decides a repository's base ref

Consumers are onboarding, the dispatch gate, worktree creation, cleanup, three listings, and
the tests that pin all of them.

## The read

```python
VersionControl.default_branch(clone_path: str, remote: str) -> str | None
```

- Returns a **branch name** (`"master"`), never a ref path and never `origin/master`.
- Returns `None` when the clone does not know: `refs/remotes/<remote>/HEAD` is absent, the path
  is not a git repository, or git failed. The caller falls back and says so; nothing
  irreversible hangs off the distinction, so the three answers `remote_branch_head` insists on
  are two here.
- **MUST NOT contact the network** and MUST NOT write to the clone — no ref, no object, no
  config. It reads what the clone already holds.
- Implemented as `git symbolic-ref --quiet refs/remotes/<remote>/HEAD`, with
  `refs/remotes/<remote>/` stripped from the answer. Timeout-bounded like every other read on
  this boundary, and recorded as `git.subprocess`.
- `SimulatedVersionControl` answers **really**, at every effect level. The subject is the
  operator's clone, which exists whatever is being simulated — the same rule that governs
  `default_remote`, `remote_url` and `show_file_at_ref`.

## The resolution

```python
repos.base_ref(config, key, vcs, clone_path, *, remote=None) -> BaseRef
```

| Order | Condition | `ref` | `source` |
|---|---|---|---|
| 1 | `[repos."<key>"] base_branch` is stated | that value | `repo_config` |
| 2 | the clone answers | the detected branch | `detected` |
| 3 | `[worker] base_branch` is stated | that value | `worker_config` |
| 4 | otherwise | `"main"` | `default` |

- `ref` is never empty.
- Detection is attempted **only** at step 2 — a stated per-repository value means no git
  command runs at all.
- `remote` defaults to `vcs.default_remote(clone_path)`. No remote means step 2 is skipped.
- Resolution never raises for an operational condition. A `BoundaryError` from either git call
  means "the clone did not answer", which is step 2 declining.

## Surfaces

**The onboarding screen** prints the ref and its provenance, in the shape the `clone path`
line above it already uses:

```
base ref     : master   (detected from origin/HEAD)
base ref     : develop   ([repos."owner/name"] base_branch)
base ref     : main   ([worker] base_branch; origin/HEAD is not set)
base ref     : main   (the default; origin/HEAD is not set)
```

**The `--json` document** carries `base_ref` (unchanged), `base_ref_source` and
`base_ref_detail`.

**The `repo.onboard` audit record** carries `base_ref` (unchanged) and `base_ref_source`. What
was approved, and what decided it.

**The committed settings shown and fingerprinted at onboarding**, and the fingerprint the
dispatch gate re-computes, are read at the resolved ref. Both sides resolve by this contract,
so an unchanged repository cannot report a changed fingerprint because the two disagreed about
which branch to look at.

**The queue** does not resolve. `ordering.plan` is pure by contract — no I/O beyond the
database, because the web interface recomputes it on every page render — so its wait-for-merge
hold message says "has not landed yet" and names no branch.

## Configuration

Both keys keep their names.

- `[repos."<key>"] base_branch` — the override. Beats everything, including detection.
- `[worker] base_branch` — the fallback for clones that cannot answer. **Loses to detection**,
  and is therefore rendered commented out in the generated example, under a fifth reason in
  [`example-config.md`](../../20260905-124257-docs-overhaul-example-config/contracts/example-config.md):
  *derived from the repository*.

A section that omits `base_branch` no longer has the worker value copied into it at parse time.
`""` means *not stated*, which is what makes rule 1 distinguishable from rule 3.
