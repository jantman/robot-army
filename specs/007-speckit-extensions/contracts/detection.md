# Contract: Detection, the Phase Ladder, and Attribution

The three predicates this milestone is made of. Each is stated as a table because each has a test per
row.

## 1. Detection (FR-001 – FR-005)

```text
speckit.detect(root: str | Path) -> Detection
```

Pure, local, read-only. Never raises: every failure becomes a `Detection` with `detected=False` and a
`reason`.

### Scaffolding half

| Evidence | Required |
|---|---|
| `<root>/.specify/` is a directory | yes |
| `<root>/.specify/templates/spec-template.md` is a file | yes |

### Commands half

All four of `specify`, `plan`, `tasks`, `implement`, each satisfied by **either** form:

| Form | Path |
|---|---|
| `skills` | `<root>/.claude/skills/speckit-<name>/SKILL.md` |
| `commands` | `<root>/.claude/commands/speckit.<name>.md` |

`form` is `skills` or `commands` when all four came from one form, `mixed` when they came from both.

### Outcomes

| Scaffolding | Commands | `detected` | `reason` |
|---|---|---|---|
| yes | all four | `True` | `"spec kit present (<form>)"` |
| yes | fewer than four | `False` | `"spec kit scaffolding present but lifecycle commands missing: <names>"` |
| no | any | `False` | `"no spec kit scaffolding at <root>/.specify"` |
| unreadable path | — | `False` | `"could not read <root>: <error>"` |

The last row is what FR-005 rests on: a permission error, a missing directory, or a path that is a
file are all detection misses. Nothing here propagates an exception into a dispatch.

## 2. The phase ladder (FR-012)

```text
speckit.observe(root, *, baseline: frozenset[str]) -> Phase | None
```

### Finding the feature directory

1. If `<root>/specs/` is not a directory → `None`.
2. Candidates are its immediate subdirectories whose names are **not** in `baseline`.
3. If there are none → `None`.
4. If there is more than one, the candidate with the highest rung wins; ties break on the most
   recently modified `specs/<dir>` mtime, and a remaining tie breaks on name, descending, so the
   answer is deterministic.

`.specify/feature.json` is **not** consulted. It is gitignored, absent from a fresh worktree, and
names a directory rather than a stage.

### Rungs, highest wins

| Rung | Evidence inside the chosen directory |
|---|---|
| `specify` | `spec.md` exists |
| `plan` | `plan.md` exists |
| `tasks` | `tasks.md` exists |
| `implement` | `tasks.md` contains a line matching `^\s*-\s*\[[Xx]\]` |

| Situation | Result |
|---|---|
| directory exists, none of the three files | `None` |
| `tasks.md` unreadable or not valid UTF-8 | rung `tasks` — the file's existence is still evidence; its contents merely fail to prove more |
| `tasks.md` present with only unticked boxes | rung `tasks` |
| every file present, one ticked box | rung `implement` |

## 3. Attribution (FR-013)

```text
speckit.baseline(root) -> tuple[str, ...]
```

The immediate subdirectory names of `<root>/specs/`, sorted, or `()` when `specs/` is absent.
Recorded once, at worktree preparation, as JSON on the work item.

| Case | Behaviour |
|---|---|
| worktree carries six finished features, session has done nothing | every directory is in the baseline → no candidates → no phase |
| session runs `/speckit-specify`, creating `specs/007-x/` | `007-x` is not in the baseline → rung `specify` |
| session works *inside* a baseline directory | no candidates → no phase. Conservative and correct: nothing distinguishes that from the author's own earlier work in the checkout |
| `speckit_baseline` is `NULL` | never observed at all; recorded once with the reason |
| `speckit_baseline` is `[]` | a Spec Kit worktree with no `specs/` yet; every directory that appears is this item's |

## 4. Where each predicate is called

| Call site | Path read | Question |
|---|---|---|
| `dispatch.build_launch_plan` | the worktree | does this session get the guidance block? |
| `worktree.prepare` | the worktree, at creation | what is the baseline? |
| `reconcile.reconcile` | the worktree, per active item | is this still a Spec Kit worktree, and has the rung changed? |
| `operations.repos` | the primary clone | does this repository use Spec Kit? |

**Observation is gated on detection.** `reconcile` calls `detect()` before `observe()` and skips
the item when it fails. Without the gate, a repository that merely happens to have a `specs/`
directory containing a `spec.md` would report a Spec Kit phase while having no Spec Kit at all — the
directory name is not rare enough to carry that meaning on its own. The cost is four `stat` calls per
active item per cycle, against a bound of two to four active items.

Detection is deliberately **not** cached on the repository row. FR-006 requires that a repository
which adopts Spec Kit after onboarding gets the behaviour with no re-onboarding, and a cache written
at onboarding is exactly the thing that would prevent it. The read is four `stat` calls.

## 5. What is guaranteed never to happen

| Guarantee | Requirement |
|---|---|
| No file inside the worktree is created, modified, or deleted | FR-018 |
| No subprocess is executed — no `git`, no `specify` | FR-003, FR-019 |
| No network request | FR-003 |
| `.specify/extensions.yml` is neither read nor written | FR-020 |
| No exception escapes into dispatch | FR-005 |
| No dispatch, resume, cleanup, capacity or state decision consults a phase | FR-016 |
