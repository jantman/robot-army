# Quickstart: Validating the Web Interface Below `live`

Eight scenarios, runnable on this machine in a few minutes against a throwaway state directory
— no live session, no network, no GitHub token, no Trello key, and nothing touching the real
database. Each maps to a success criterion in [spec.md](spec.md); the exact behaviour each one
must produce is fixed in [contracts/web-visibility.md](contracts/web-visibility.md).

The whole point of this feature is what a page *looks like*, so scenarios 3 through 6 are read
with eyes rather than with `grep`. Read them on a phone if one is to hand — that is the surface
the defect was found on.

## Prerequisites

```bash
cd ~/GIT/robot-army
uv sync
uv run pytest              # the suite must pass before any of this means anything
```

### A throwaway environment

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

ra() { uv run robot-army --config "$RA_TMP/config.toml" "$@"; }
```

Copy any remaining `[…]` sections from `share/config.example.toml` if a command complains that a
required key is missing — the example file is the reference for the full shape.

### Seeding the state the bug needs

Four simulated `ready` work items and a handful of simulated cards: the shape the issue reports
from `privatepuppet`.

```bash
uv run python - <<'PY'
import os, pathlib
from robot_army import db
from tests.conftest import seed_item

state = pathlib.Path(os.environ["RA_TMP"]) / "state" / "state.db"
conn, _ = db.open_database(state)
for n in (26, 27, 28, 30):
    seed_item(conn, repo_key="jantman/privatepuppet", issue_number=n,
              dry_run=True, state="ready")
with db.transaction(conn):
    for i in range(5):
        db.insert_card(conn, board_id="demo-board", card_id=f"card{i}",
                       card_url=f"https://trello.com/c/card{i}", title=f"Card {i}",
                       body="", dry_run=True)
conn.commit()
PY
```

The cards view needs a `[trello]` section to render at all — add one with `board_id =
"demo-board"` and any placeholder credentials, since nothing in this document polls the board.
Without it `/cards` correctly refuses with "no board is configured", which is a different
message from the one this feature is about.

### Serving it

```bash
ra serve --bind 127.0.0.1 --port 8420 &
```

Everything below is against `http://127.0.0.1:8420`. Stop it with `kill %1` when done, and
`rm -rf "$RA_TMP"` to clean up.

---

## 1. The four empty views are no longer empty

```bash
for p in /active /queue /interrupted /cards; do
  echo "== $p"; curl -s "http://127.0.0.1:8420$p" | grep -c "Nothing"
done
```

**Expected**: `/queue` and `/cards` report `0` matches for "Nothing" and their tables hold the
four items and five cards. `/active` and `/interrupted` legitimately hold no rows in this
seeding — their empty text is the truth, and scenario 7 checks that it stays the truth rather
than becoming a withheld-rows claim.

Open `/queue` in a browser and confirm the four items are listed without any URL editing.
*(SC-001, SC-002, FR-001)*

## 2. Hiding them is still one request away

```bash
curl -s "http://127.0.0.1:8420/queue?include_simulated=0" | grep -c "Nothing is ready"
```

**Expected**: `1`. The old behaviour is reachable by stating the preference, and the page now
also says how many rows it is withholding and offers a link back. *(SC-007, FR-002, FR-006)*

Then confirm the choice sticks: follow a nav link from that page and confirm the rows are still
hidden — every generated link carries `include_simulated=0`. *(FR-003)*

```bash
curl -s "http://127.0.0.1:8420/queue?include_simulated=0" | grep -o 'href="/active[^"]*"' | head -1
```

**Expected**: `href="/active?include_simulated=0"`.

## 3. Every page says it is not real

Open `/active`, `/queue`, `/cards`, `/anomalies` and `/log` in a browser.

**Expected**: each carries the same banner, in the same slot and weight as `DAEMON NOT RUNNING`,
naming `plan` and listing what did not really happen — no session launched, nothing committed,
no hook run, no terminal opened, no issue or comment written and the issue numbers invented, no
card moved, no notification sent.

Show one screenshot to someone with no context and ask whether the system is doing real work.
*(SC-003, SC-004, FR-010, FR-011, FR-012)*

## 4. The pill is unmistakable, and calm at `live`

Compare the level pill at `plan` with the same pill at `live`:

```bash
kill %1
ra serve --bind 127.0.0.1 --port 8420 --effect-level live &
```

**Expected**: at `plan` the pill reads `effect level: plan — simulated`, in the error colour and
bold, clearly louder than the capacity and order pills beside it. At `live` it reads
`effect level: live`, muted, and **no banner appears anywhere**. *(FR-014, FR-016, FR-017)*

Restore `plan` before continuing.

## 5. The consequences change with the level

```bash
for lvl in plan local no-remote; do
  kill %1 2>/dev/null; ra serve --bind 127.0.0.1 --port 8420 --effect-level "$lvl" & sleep 1
  echo "== $lvl"; curl -s http://127.0.0.1:8420/active | grep -o "no [a-z ]* is really [a-z]*"
done
```

**Expected**: three different lists, shrinking as the level rises. `no-remote` names only the
three outward-facing writers; `local` adds the session and the terminal window; `plan` adds
version control and hooks. Nothing is claimed at a level where it is not true. *(FR-013)*

## 6. A simulated row is legible as simulated

With `live` running and `?include_simulated=1`, seed one real work item alongside the four
simulated ones and open `/queue`.

**Expected**: the simulated rows carry the `simulated` badge and the real one does not; the
difference is visible without hovering or reading closely. *(SC-006, FR-019, FR-020)*

## 7. An empty database reads as empty

```bash
rm "$RA_TMP/state/state.db"* && ra status >/dev/null   # recreates an empty schema
curl -s "http://127.0.0.1:8420/queue" | grep -i "withheld\|hidden"
```

**Expected**: no output. The page says nothing is ready and reports no withheld rows, because
none were. The two claims never appear together. *(FR-009, spec edge case "Zero rows below
`live`")*

Re-seed before scenario 8.

## 8. The machine-readable view agrees

```bash
curl -s "http://127.0.0.1:8420/queue.json" | jq '{effect_level, effective_level, include_simulated, simulated_preference, items: (.items|length)}'
curl -s "http://127.0.0.1:8420/queue.json?include_simulated=0" | jq '{include_simulated, simulated_preference, withheld_simulated, items: (.items|length)}'
```

**Expected**: the first shows `include_simulated: true`, `simulated_preference: null`,
`effective_level: "plan"` and four items — the same four the page shows. The second shows
`include_simulated: false`, `simulated_preference: false`, `withheld_simulated: 4` and zero
items. No payload ever reports zero items without also reporting what it withheld.
*(FR-021, FR-022)*

Also confirm the terminal is unchanged, since this feature deliberately does not touch it:

```bash
ra status | head -20
```

**Expected**: simulated rows still excluded by default, still disclosed, exactly as milestone
008 left them. *(contracts/web-visibility.md, "Terminal equivalence")*

---

## The dispatch invariant

The one thing this feature must not have changed is which rows the daemon would act on. The
ordering already includes simulated rows unconditionally and is not touched, so:

```bash
uv run pytest tests/unit/test_ordering.py tests/unit/test_capacity.py
```

**Expected**: pass, unmodified. *(SC-008, FR-005)*

## Cleanup

```bash
kill %1; rm -rf "$RA_TMP"
```
