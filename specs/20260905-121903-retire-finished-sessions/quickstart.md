# Quickstart — validating retirement

Four scenarios. **Scenario 1 runs against the real condition #138 reported**, because the machine
was left in it deliberately: items 45 and 54 are `done`, their workers are alive, and both have been
idle far longer than the threshold. It is both the acceptance test and the fix.

Prerequisite for all of them:

```bash
uv sync
uv run pytest            # must pass before anything here is meaningful
```

---

## Scenario 1 — The two real orphans retire themselves (US1, SC-001, SC-002, SC-005)

**Before**, as recorded while writing this spec:

```
$ uv run robot-army anomalies
[26] orphan_session  session:037460ea-…  pid 767308  work_item_id 45  work_item_state done
[25] orphan_session  session:c2673ac8-…  pid 404232  work_item_id 54  work_item_state done
[24] orphan_session  session:22222222-…  pid 498936   ← already dead

$ uv run robot-army capacity
capacity     : 3 of 3 sessions running
  ours       : 2
  others     : 1
```

Both workers report `"status": "idle"` in `~/.claude/sessions/<pid>.json`, idle 84 and 198 minutes.
Confirm that for yourself before running anything — the whole feature turns on this field:

```bash
for p in 767308 404232; do
  jq -r '"\(.pid) \(.status) \(.statusUpdatedAt)"' ~/.claude/sessions/$p.json
done
```

**Run one pass:**

```bash
uv run robot-army reconcile
```

**Expected after:**

| Check | Command | Expectation |
|---|---|---|
| Both workers gone | `ps -p 767308,404232` | no such processes |
| Both kitty tabs gone | look at the terminal | closed, with no action taken |
| Rows closed | `uv run robot-army show 45` | session `lost`, `ended` stamped, reason naming retirement |
| Slots released | `uv run robot-army capacity` | `1 of 3` — only the maintainer's own session |
| Anomalies empty | `uv run robot-army anomalies` | nothing listed. 25 and 26 never re-raise; 24 resolves under Scenario 3 |
| Counted | `uv run robot-army reconcile` output | `retired: 2` |
| Logged before the signal | see the `jq` below | one `session.retire` per session, timestamped **before** its `session.terminate` |

```bash
jq -r 'select(.action == "session.retire" or .action == "session.terminate")
       | [.ts, .action, .entity_id] | @tsv' \
  ~/.local/state/robot-army/logs/audit-$(date -u +%F).jsonl
```

**Then lift the pause and confirm the machine is unwedged** — this is SC-002, and the reason #138
mattered:

```bash
uv run robot-army unpause
uv run robot-army status        # the queue's 21 items should no longer report the cap
```

**Transcripts survive** (FR-016). Prove it rather than trusting it:

```bash
ls ~/.claude/projects/-home-jantman-worktrees-robot-army-issue-116/037460ea-*.jsonl
# and, if you want the full check:
claude --resume 037460ea-0969-4a44-adca-d79920557a33
```

---

## Scenario 2 — A worker in use is never killed (US1 scenario 5, SC-003)

The one failure mode that would matter. Run it as a unit test, not by hand.

Seed a `done` item with a live session whose registry entry reads `"status": "idle"` and a
`statusUpdatedAt` of *now*, then run passes:

| Step | Expectation |
|---|---|
| pass 1 | nothing terminated; **no audit record at all** for that session (C6) |
| advance the fake clock to `RETIRE_IDLE_SECONDS − 1` | still nothing |
| advance past `RETIRE_IDLE_SECONDS` | retired |
| same, but `"status": "busy"` at any age | never retired, at any age |
| same, but `statusUpdatedAt` absent / a string / in the future | never retired |
| same, but no registry entry for the session id | never retired |

The last four are the "unknown is safe" rules from C2 and each gets its own case. The absence of an
audit record in the first two rows is the assertion, not an omission.

---

## Scenario 3 — A resolved anomaly stops being reported (US3, SC-005)

Anomaly 24 on the machine is already this case: pid 498936 has not existed for hours.

```bash
uv run robot-army anomalies            # [24] listed
uv run robot-army reconcile
uv run robot-army anomalies            # [24] gone
uv run robot-army anomalies --all      # [24] present, marked resolved — NOT acknowledged
jq -r 'select(.action == "anomaly.resolved")' \
  ~/.local/state/robot-army/logs/audit-$(date -u +%F).jsonl
```

The recurrence case has to be a test rather than a command, and it is the one that catches a wrong
index (A5):

1. raise an `orphan_session` for a pid;
2. resolve it;
3. raise the same `(kind, entity_type, entity_id)` again;
4. assert **two** rows exist — the resolved one and a new open one.

---

## Scenario 4 — Stopping a terminal item's session by hand (US2, SC-006)

The path for `abandoned` and `failed` items, which retirement deliberately never touches.

```bash
uv run robot-army cancel <id>
```

| Check | Today | Required |
|---|---|---|
| Message | "session … is gone; it had already recorded its own ending" | says the session was stopped, by which method |
| Session row | left `running` | `lost`, with `ended_at` |
| Slot | still held | released |
| Work item | untouched | untouched — a terminal item is **not** moved to `interrupted` |

Three guards must still hold, each as its own case: an implausible pid is refused with **nothing
signalled**; a pid whose start time no longer matches is `already_gone` rather than signalled; a
process that survives leaves the row open and reports the failure.

---

## The interruption paths (Principle IV — run these as tests)

| Case | Expectation |
|---|---|
| Killed between the signal and the settle | dead process under an open row; the **next** pass's `_sweep_stale_sessions` reclaims it. No manual step |
| An exit record arrives after the row is `LOST` | settles quietly, is logged, and **the spool file is unlinked**. Today it raises, is logged as an error, and is retried on every tick forever (R7) |
| Killed mid-migration | schema stays 11; the migration re-runs cleanly |
| Two sessions retired in one pass, the first refusing | the second is still retired; the first is logged and not counted |

---

## Regression guards

```bash
uv run pytest
```

Beyond the new tests, three existing guarantees must be re-asserted because this feature moves
close to each:

- **Nothing reads a `.key` file.** The existing test stands; the new registry field comes from the
  same parsed payload.
- **`reconcile.py` never names the effect level.** The existing grep-the-source test covers the new
  code too — the host discriminator is derived from the session record, exactly as `cancel` does it.
- **`_resolve_closed_issues` is still the only writer of `WorkItemState.DONE`** (C8). This is a new
  test, and it is what keeps retirement's precondition meaning what the maintainer agreed to.
