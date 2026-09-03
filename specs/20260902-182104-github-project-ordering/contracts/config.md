# Contract: Configuration

**Feature**: [../spec.md](../spec.md) | Four keys, one global and three per repository.

## Keys

```toml
[dispatch]
project_ordering = true             # default; false disables board ordering machine-wide

[repos."jantman/robot-army"]
project_ordering = false            # optional; overrides the global value for this repo
project = 3                         # optional; a number, or a board URL
project_column = "Ready"            # optional; the column to dispatch from
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `[dispatch] project_ordering` | bool | `true` | Whether a resolvable board governs order at all |
| `[repos.*] project_ordering` | bool \| absent | inherit | Overrides the above for one repository |
| `[repos.*] project` | int \| str \| absent | discover | Project number, or a `github.com/{users,orgs}/…/projects/N` URL |
| `[repos.*] project_column` | str \| absent | discover | Exact column name; matched case- and space-insensitively |

`true` is the default for `[dispatch] project_ordering` because FR-019 requires a cleanly
resolvable board to take effect without being switched on. That is the one setting here whose
default changes behaviour on upgrade — a repository with a linked project starts ordering by
it, and gains holds for cards parked in other columns — which is why the off switch exists and
why `robot-army status` reports the state on the first pass after the upgrade rather than
leaving it to be noticed.

`project` accepting both a number and a URL is not indulgence: discovery reads
`repository.projectsV2`, which sees only *linked* projects (R6), so an unlinked board can be
named only by a URL that carries its owner type and number. A bare number is resolved against
the repository's owner.

## Resolution

`Config.effective_project_ordering(key) -> tuple[bool, bool]`, shaped exactly like
`effective_wait_for_merge`: the value, and whether the author chose it rather than inherited
it. The second element is what lets a surface say which file to edit.

```python
def effective_project_ordering(self, key: str) -> tuple[bool, bool]:
    repo = self.repos.get(key)
    if repo is not None and repo.project_ordering is not None:
        return repo.project_ordering, True
    return self.dispatch.project_ordering, False
```

`project` and `project_column` are read straight off `RepoConfig` with no global counterpart,
because there is nothing sensible to inherit: a project number means nothing outside the
repository it belongs to.

`RepoConfig` gains `project_ordering: bool | None = None`, `project: str | None = None`,
`project_column: str | None = None`, following the established convention that `None` means
*inherit* and the distinction survives parse time so a surface can report which setting
decided. `repos.resolve` carries all three through — the onboarding record wins `path` only;
the section wins every policy field.

## Validation

`[dispatch]` is already in `_STRICT_KEY_SECTIONS`, so a misspelled `project_ordering` there is
a hard error for free. `_REPO_KEYS` gains the three per-repository names, and `[repos.*]`
already treats an unknown key as a problem rather than a warning — the rule the file states
as *a typo in a section that exists is a setting that quietly does nothing, which is worse
than a setting that is missing, because it looks applied*.

| Input | Outcome |
|---|---|
| `[dispatch] project_ordering` not a bool | problem: refuses to load |
| `[repos.*] project_ordering` not a bool | problem; the value is treated as `None` on the failure path so the loader does not silently pick a side in a config it is about to refuse |
| `[repos.*] project` neither an int nor a parseable project URL | problem, naming both accepted forms |
| `[repos.*] project_column` not a string, or empty | problem |
| `[repos.*] project_column` naming a column the board does not have | **not** a config error — the board is not readable at parse time. Reported at resolution as `unresolved_reason`, naming the configured value and the columns the board offers (FR-015) |

## Recognised column names

```python
RECOGNISED_DISPATCH_COLUMNS: tuple[str, ...] = ("ready", "todo", "to do")
```

Compared after lowercasing and collapsing internal whitespace. A board offering exactly one of
them resolves without configuration; a board offering two or more, or none, is ambiguous and
is reported with the columns it does offer rather than guessed at (FR-018). GitHub's Kanban
template offers exactly `Ready` and the simpler template exactly `Todo`, which is what makes
the common case configuration-free.

## What `doctor` checks

Appended as `project: <name>` rows, for **every onboarded repository**.

This is not what this contract originally said, and the difference is recorded rather than
quietly reconciled. The first version mirrored the Trello board block — checks only when a
board exists, on the reasoning that inventing a passing check says something about a board
that does not exist. Implementation disagreed on two counts and the implementation won:

- **A repository with no board gets one passing row**, naming that fact. `doctor` is the
  command that says everything it knows, and silence here reads identically to "this build
  has no board checks" — which is the wrong answer for the person running it to find out
  whether boards work at all. `status` still stays quiet about such repositories, because
  that command answers "what is happening now" rather than "what is configured".
- **It is a *passing* row, not a failing one.** `doctor` exits non-zero on any failed check,
  so reporting absence as a failure would have made the command fail on every installation
  without a project board — most of them — and it is the command the README tells the author
  to run first, every time.

A repository with board ordering switched off likewise gets one passing row saying so, naming
whether that came from its own setting or the global one.

| Check | Fails when |
|---|---|
| `project` (absent) | **Never.** A repository with no linked project passes, with `no project is linked to <repo> — board ordering has no effect here` |
| `token` | The credentials cannot read projects. Distinguishes the three shapes R7 measured: `INSUFFICIENT_SCOPES` (classic token missing `read:project`), `FORBIDDEN` (permission), and an empty `x-oauth-scopes` (a **fine-grained** token, which cannot read user-owned projects at all — reported with that explanation, not as a generic failure) |
| `project` | No project resolved: none linked, more than one linked, or a configured project not found |
| `column` | No dispatch column resolved: none recognised, more than one recognised, or a configured column absent from the board |
| `view sort` | A board view carries a sort **and** at least one card in the dispatch column has a value for that sort field — the precise condition under which the order on screen and the order dispatched can disagree (R2). A sort that changes nothing does not warn |
| `freshness` | The last board read failed, reporting the error and how long the snapshot has been stale |
