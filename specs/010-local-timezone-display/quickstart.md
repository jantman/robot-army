# Quickstart: Validating Local-Time Display

**Feature**: `010-local-timezone-display`

Eight scenarios, runnable on this machine in a few minutes against a throwaway state directory
— no daemon, no live session, no network, no GitHub token, and nothing touching the real
database. Each maps to a success criterion in [spec.md](spec.md); the exact behaviour each
must produce is fixed in [contracts/time-display.md](contracts/time-display.md).

The point of this feature is what a *person* reads, so scenarios 1 and 2 are read with eyes as
well as with `grep`. Scenarios 4 and 5 are the ones that matter most if you are short on time:
they are the proof that the record did not move, which is the only way this change can do harm.

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

# Every command below runs in a pinned zone, so the output is the same on any machine.
ra() { TZ=America/New_York uv run robot-army --config "$RA_TMP/config.toml" "$@"; }
```

Copy any remaining `[…]` sections from `share/config.example.toml` if a command complains that
a required key is missing.

### Seeding instants whose local rendering is known

Every stamp below is chosen so the local rendering lands on a **different calendar day** from
the stored one — the single clearest way to see that conversion happened.

```bash
uv run python - <<'PY'
import os, pathlib
from robot_army import db
from tests.conftest import seed_item

state = pathlib.Path(os.environ["RA_TMP"]) / "state" / "state.db"
conn, _ = db.open_database(state)

# 2026-08-30T01:31:07Z is 2026-08-29 21:31:07 -04:00 — the day before, in New York.
item = seed_item(conn, repo_key="jantman/demo", issue_number=42, dry_run=True, state="ready")
with db.transaction(conn):
    conn.execute(
        "UPDATE work_items SET discovered_at=?, ready_at=?, updated_at=? WHERE id=?",
        ("2026-08-30T01:31:07Z", "2026-08-30T01:31:07Z", "2026-08-30T01:31:07Z", item),
    )
    db.raise_anomaly(conn, kind="orphan_worktree", detail={},
                     entity_type="work_item", entity_id=str(item))
    conn.execute("UPDATE anomalies SET detected_at = ?", ("2026-08-30T01:31:07Z",))
conn.commit()
print("seeded item", item)
PY
```

`db.raise_anomaly` stamps `detected_at` with the real clock, so the `UPDATE` immediately after
it is what pins the anomaly to the same known instant as the work item. If `seed_item` takes
different arguments than shown, follow `tests/conftest.py` — it is the reference, and the point
here is only to get rows with a known stamp on them.

---

## 1. The terminal reads local

```bash
ra pause
ra status
ra show 1
ra anomalies
```

**Expected**: every timestamp reads `2026-08-29 21:31:07 -04:00`, not `2026-08-30T01:31:07Z`.
Specifically — the `PAUSED since …` line (C1), the anomaly line in `status` (C2), `show`'s
history rows (C5), and `anomalies`' `detected …` (C9). Note that the date shown is **the 29th**
while the stored value says the 30th: that difference is the feature.

Then the log, which is the tenth site and the one with its own formatter:

```bash
ra log --since 1h
```

**Expected**: each record begins with a local stamp carrying `-04:00`. *(SC-001, SC-002,
SC-003)*

## 2. The web reads local

```bash
TZ=America/New_York uv run robot-army --config "$RA_TMP/config.toml" serve \
  --bind 127.0.0.1 --port 8420 &

for p in /active /queue /interrupted /anomalies /log; do
  echo "== $p"; curl -s "http://127.0.0.1:8420$p" | grep -o '[0-9-]\{10\} [0-9:]\{8\} [+-][0-9:]\{5\}' | head -3
done
curl -s "http://127.0.0.1:8420/queue" | grep -c 'T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
```

**Expected**: the first loop prints local stamps with offsets on every view. The second
command prints `0` — no raw UTC stamp survives anywhere in the rendered HTML.

Open `/queue` in a browser and confirm the footer reads `rendered 2026-… -04:00` (W4) and the
`DISPATCH PAUSED since …` pill reads local (W3). Confirm the relative age is still beside each
absolute time — `2026-08-29 21:31:07 -04:00 (…​ ago)` — because FR-006 keeps the pair.
*(SC-001, SC-002)*

## 3. The two interfaces agree

```bash
ra status | grep "PAUSED since"
curl -s "http://127.0.0.1:8420/queue" | grep -o "DISPATCH PAUSED since [^<]*"
```

**Expected**: the same wall-clock value in both. *(SC-008)*

## 4. Machine-readable output did not move

The load-bearing scenario. Run the same commands in two very different zones and diff:

```bash
for tz in America/New_York Asia/Kolkata UTC; do
  TZ=$tz uv run robot-army --config "$RA_TMP/config.toml" status --json > "$RA_TMP/status.$tz.json"
done
diff "$RA_TMP/status.America/New_York.json" "$RA_TMP/status.Asia/Kolkata.json" && echo "IDENTICAL"
```

*(Use `${tz//\//_}` in the filename if the slash is inconvenient.)*

**Expected**: `IDENTICAL`, and every timestamp in the file still ends in `Z`. Then the web's
JSON, including the two chrome keys that research [R3](research.md) singles out:

```bash
curl -s -H 'Accept: application/json' "http://127.0.0.1:8420/queue" \
  | grep -E '"(rendered_at|dispatch_paused_at|updated_at)"'
```

**Expected**: all three carry `…Z` values. A `-04:00` anywhere in this output is the feature's
central failure mode. *(SC-004)*

## 5. The audit files did not move

```bash
TZ=Asia/Kolkata uv run robot-army --config "$RA_TMP/config.toml" unpause
ls "$RA_TMP"/state/logs/
grep -o '"ts":"[^"]*"' "$RA_TMP"/state/logs/audit-*.jsonl | tail -3
```

**Expected**: the file is still named `audit-<UTC day>.jsonl`, and every `ts` ends in `Z`,
including the record written while the process was running in `Asia/Kolkata`. *(SC-005)*

## 6. A machine with no determinable zone

```bash
TZ=Bogus/Nowhere uv run robot-army --config "$RA_TMP/config.toml" status
TZ=Bogus/Nowhere curl -s "http://127.0.0.1:8420/queue" > /dev/null; echo "exit=$?"
```

**Expected**: no error, no traceback, no non-zero exit. Times render at `+00:00`, which is the
honest statement that the zone could not be determined rather than a silent pretence that the
machine is in UTC. *(SC-006, FR-009)*

## 7. The fold — two instants, one wall clock

The scenario that justifies putting the offset on every stamp rather than once per page:

```bash
uv run python - <<'PY'
import os, time
os.environ["TZ"] = "America/New_York"; time.tzset()
from robot_army import timefmt
for raw in ("2026-11-01T05:00:00Z", "2026-11-01T06:00:00Z"):
    print(raw, "->", timefmt.local(raw))
PY
```

**Expected**:

```text
2026-11-01T05:00:00Z -> 2026-11-01 01:00:00 -04:00
2026-11-01T06:00:00Z -> 2026-11-01 01:00:00 -05:00
```

Two distinct instants an hour apart, the same wall clock, told apart **only** by the offset.
Without the offset on every stamp these would be indistinguishable for one hour each autumn.
*(SC-006, SC-007)*

## 8. A corrupt stamp stays visible

```bash
uv run python - <<'PY'
import os, pathlib, sqlite3
state = pathlib.Path(os.environ["RA_TMP"]) / "state" / "state.db"
conn = sqlite3.connect(state)
conn.execute("UPDATE work_items SET ready_at = 'not a timestamp' WHERE id = 1")
conn.commit()
PY
ra show 1
```

**Expected**: `show` prints `not a timestamp` where the ready transition goes, and exits
normally. A rendering layer must never be the thing that hides a corrupt row, and must never be
the thing that crashes on one. *(FR-015)*

---

## Cleaning up

```bash
kill %1 2>/dev/null
rm -rf "$RA_TMP"
unset RA_TMP
```

## Coverage

| Scenario | Success criteria |
|---|---|
| 1 — terminal | SC-001, SC-002, SC-003 |
| 2 — web | SC-001, SC-002 |
| 3 — agreement | SC-008 |
| 4 — machine-readable unchanged | SC-004 |
| 5 — audit unchanged | SC-005 |
| 6 — no determinable zone | SC-006 |
| 7 — the DST fold | SC-006, SC-007 |
| 8 — corrupt stamp | FR-015 |
