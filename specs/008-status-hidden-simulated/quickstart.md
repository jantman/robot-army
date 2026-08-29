# Quickstart: Validating the Status Withheld-Rows Disclosure

Seven scenarios. All seven are runnable on this machine in a couple of minutes against a
throwaway state directory — no live session, no network, no GitHub token, and nothing touching
the real database. Each maps to a success criterion in [spec.md](spec.md); the exact output each
one must produce is fixed in [contracts/status-output.md](contracts/status-output.md).

## Prerequisites

```bash
cd ~/GIT/robot-army
uv sync
uv run pytest              # the suite must pass before any of this means anything
```

### A throwaway environment

Everything below runs against a scratch state directory so the real database is never involved.

```bash
export RA_TMP=$(mktemp -d)
cat > "$RA_TMP/config.toml" <<'TOML'
[daemon]
effect_level = "plan"

[paths]
state_dir = "REPLACE_ME/state"
repo_root = "REPLACE_ME/repos"
worktree_root = "REPLACE_ME/worktrees"
TOML
sed -i "s|REPLACE_ME|$RA_TMP|g" "$RA_TMP/config.toml"
mkdir -p "$RA_TMP/state" "$RA_TMP/repos" "$RA_TMP/worktrees"

# A convenience alias for the rest of this document.
ra() { uv run robot-army --config "$RA_TMP/config.toml" "$@"; }
```

Copy the remaining `[…]` sections from `share/config.example.toml` if any command complains that
a required key is missing — the example file is the reference for the full shape.

### Seeding the rows the bug needs

The reported failure needs simulated `ready` work items and nothing else. Seed them directly
with the same helper the test suite uses:

```bash
uv run python - <<'PY'
import os, pathlib
from robot_army import db
from tests.conftest import seed_item

conn, _ = db.open_database(pathlib.Path(os.environ["RA_TMP"]) / "state" / "state.db")
for n in (26, 27, 28, 30):
    seed_item(conn, repo_key="jantman/privatepuppet", issue_number=n,
              dry_run=True, state="ready")
conn.commit()
PY
```

To exercise the mixed case later, add two real rows with `dry_run=False` and different issue
numbers.

---

## 1. The reported contradiction is gone

```bash
ra status
```

**Expected**: the queue table lists the four items in dispatch order, each with a `*` on its item
number and a `* = simulated (dry-run) row` footnote beneath the table. Where the output used to
read `no work items yet` it now reads:

```
no work items (4 simulated rows withheld — pass --include-simulated to show them)
```

and where it read `no matching work items`:

```
no matching work items (4 simulated rows withheld — pass --include-simulated to show them)
```

Read the whole output and confirm no two statements in it can both be false. *(SC-001, SC-002,
SC-003, FR-001, FR-002, FR-007)*

## 2. The stated number is the number the flag reveals

```bash
ra status --include-simulated
```

**Expected**: all four rows appear in the counts and in the listing, marked simulated. The four
rows that appear here are exactly the four the previous scenario said were withheld — count them.
No withheld line appears anywhere, and no `0` is printed in its place. *(SC-005, SC-006, FR-005,
data-model.md invariant 2)*

## 3. The disclosure is not limited to the empty case

Seed two real `ready` items, then:

```bash
ra status
```

**Expected**: the counts and the listing show the two real items, the queue shows all six, and a
standalone line beneath the listing reads `4 simulated rows withheld — pass --include-simulated
to show them`. The old behaviour would have shown a six-row queue over a two-row listing with
nothing said about the gap — quieter than the reported bug, and the same defect. *(SC-001,
FR-003)*

## 4. Filters scope the number honestly

```bash
ra status --repo jantman/privatepuppet
ra status --repo demo                      # a repository with only real rows
ra status --state done
```

**Expected**: the first reports 4 withheld. The second reports none and prints no withheld line
at all, because `--include-simulated` would reveal nothing under that filter. The third reports
none, because no simulated item is in `done`. In every case, re-running the same command with
`--include-simulated` must add exactly the number of listing rows that was claimed. *(SC-002,
FR-004)*

## 5. An empty database still says so plainly

```bash
export RA_TMP=$(mktemp -d)   # a fresh one, with no rows seeded at all
```

Repeat the config setup, then:

```bash
ra status
```

**Expected**: `no work items yet`, with no parenthetical, no zero, and no queue section. The
everyday empty case is untouched. *(SC-004, SC-006, FR-006)*

## 6. The machine-readable view agrees with the text

```bash
ra status --json | python -m json.tool | grep -A 3 withheld_simulated
ra status --include-simulated --json | python -m json.tool | grep -A 3 withheld_simulated
```

**Expected**: `{"counts": 4, "items": 4}` from the first, `{"counts": 0, "items": 0}` from the
second. The key is present in both — a consumer never has to tell "nothing withheld" apart from
"field absent". Confirm the numbers match what scenario 1 printed. *(SC-005, FR-010, FR-011)*

## 7. Withholding is not an error

```bash
ra status >/dev/null; echo "exit: $?"
```

**Expected**: `exit: 0`. Rows being withheld says nothing about whether the command succeeded.
*(FR-012)*

---

## P3: the sibling listings

Only if User Story 3 was implemented. Seed simulated cards (requires a `[trello]` section in the
scratch config) and a simulated item with a `worktree_path`, then:

```bash
ra cards
ra worktree list
```

**Expected**: neither claims nothing is tracked or recorded while rows exist and were withheld;
each names the count and the flag. With nothing withheld, both print their original messages
verbatim. *(FR-013)*

---

## Cleanup

```bash
rm -rf "$RA_TMP"
```

## What this quickstart deliberately does not cover

The web interface. Below `live` it renders as an empty system with a neutral pill, which is a
larger problem than a contradictory line of text and is tracked as issue #14. This feature places
the withheld count into the payload `web/pages.py` already consumes and stops there; verifying
the interface belongs to that issue's quickstart, not this one.
