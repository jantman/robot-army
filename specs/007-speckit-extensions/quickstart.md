# Quickstart: Validating Spec Kit Awareness

Eight scenarios. The first five are runnable on this machine with no live session; the last three
need a real dispatch and belong to the human verification round. Every one of them maps to a success
criterion in [spec.md](spec.md).

## Prerequisites

```bash
cd ~/GIT/robot-army
uv sync
uv run pytest              # the suite must pass before any of this means anything
uv run robot-army doctor   # unchanged by this milestone; still run it first
```

Two throwaway checkouts make the scenarios concrete:

- a **Spec Kit repository** — anything with `.specify/templates/spec-template.md` and
  `.claude/skills/speckit-*/SKILL.md`. This repository is one.
- a **plain repository** — anything without them.

---

## 1. Detection says the right thing about both kinds of repository

```bash
uv run robot-army repos
```

**Expected**: a `spec-kit` column. `yes` for the Spec Kit checkouts, `no` for the others, `off` for
any repository with `speckit = false`, and `?` for a clone that could not be read. No network access
occurs — verify by running it with the machine offline. *(SC-008, FR-021, FR-022)*

## 2. Detection needs both halves

```bash
mkdir -p /tmp/half/.specify/templates && touch /tmp/half/.specify/templates/spec-template.md
uv run python -c "from robot_army import speckit; print(speckit.detect('/tmp/half'))"
```

**Expected**: `detected=False`, and a reason naming the four missing lifecycle commands rather than a
generic failure. Add `.claude/skills/speckit-{specify,plan,tasks,implement}/SKILL.md` and it flips to
`detected=True` with `form='skills'`. *(FR-002)*

## 3. The prompt gains the block, and only when it should

```bash
uv run pytest tests/unit/test_speckit_prompt.py -v
```

**Expected**: the composed prompt for a detected worktree contains the block between the repository's
own instructions and the issue; the composed prompt for an undetected one is byte-identical to the
golden string captured before this milestone; the same issue composed twice produces the same bytes.
*(SC-002, FR-007, FR-009, FR-010)*

## 4. The stale-artifact trap

This is the scenario that would have shipped a wrong column, so run it by hand at least once.

```bash
# a worktree of this repository already contains six finished features
uv run python - <<'PY'
from robot_army import speckit
root = "/path/to/a/fresh/worktree"
base = speckit.baseline(root)
print("baseline:", base)
print("phase:", speckit.observe(root, baseline=frozenset(base)))
PY
```

**Expected**: the baseline lists `001-…` through `007-…`, and the phase is `None` — *not* `implement`,
which is what every one of those directories would otherwise report. Then create
`specs/999-scratch/spec.md` inside the worktree and re-run: the phase becomes `specify` on
`specs/999-scratch`. Delete it afterwards. *(SC-005, FR-013)*

## 5. Nothing is written

```bash
uv run pytest tests/integration/test_speckit_dispatch.py -k writes_nothing -v
```

**Expected**: the test hashes every path under the worktree, runs a full dispatch and a
reconciliation pass, and asserts the snapshot is unchanged — including ignored files, which a
`git status` check would miss. *(SC-004, FR-018)*

---

## 6. A real dispatch into a Spec Kit repository

```bash
uv run robot-army run &
# label an issue in a Spec Kit repository, then:
uv run robot-army show <id>
```

**Expected**: `show` reports that Spec Kit was detected; the audit log carries one `speckit.detect`
record with its evidence; the session, when it appears, starts with the lifecycle rather than editing
files. *(SC-001, SC-007)*

```bash
uv run robot-army log --action speckit.detect
```

## 7. The phase advances while you watch

Leave the session running through `/speckit-specify` and into `/speckit-plan`.

**Expected**: `robot-army status` and the web item view move from `specify` to `plan` within one
reconciliation interval, naming the feature directory. Exactly one `speckit.phase` record per
transition — not one per cycle:

```bash
uv run robot-army log --action speckit.phase --item <id>
```

Then open `http://<lan-address>:8420/item/<id>` on the phone and confirm the same stage is legible
there. *(SC-003, FR-014, FR-015)*

## 8. The judgement is really the session's

Dispatch a deliberately trivial issue — a typo fix — into a Spec Kit repository.

**Expected**: the session may reasonably skip the lifecycle. The item shows **no phase**, completes
normally, raises no anomaly, and appears nowhere as stalled or failed. This is a pass, not a
failure. *(FR-008, FR-016, and the honest half of SC-001)*

---

## What CI cannot settle

SC-001 is a rate over real dispatches, not an assertion: FR-008 hands the judgement to the session,
so the number is *measured* over the live round and recorded in the roadmap's "What running it
taught" section, the way milestones 004 and 005 record theirs. Scenarios 6 through 8 need a running
kitty, a real session, and real credentials, none of which CI has.
