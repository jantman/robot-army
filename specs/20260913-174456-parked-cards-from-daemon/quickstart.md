# Quickstart: Parked Is Judged by the Daemon's Ignore List

## Automated

```bash
uv run pytest tests/unit/test_health.py tests/unit/test_ignored_lists.py -q
uv run pytest            # the whole suite
```

The cases are listed in [contracts/parked-in-force.md](contracts/parked-in-force.md).

## By hand — the issue's own reproduction

This replays milestone 006's quickstart section 5 in the order that exposed the defect: the web
interface is started **before** the ignore list exists and is never restarted.

1. With no `ignore_lists` in `[trello]`, start the daemon and `robot-army web`.
2. Create a tagged card in an ordinary column naming no repository, and a second naming two. Wait one
   interval. `/cards` reads `awaiting clarification (2)`.
3. Add `ignore_lists = ["Icebox"]`, restart **only the daemon**, and drag the first card into
   `Icebox`. Wait one interval.

**Expect**, on `/cards` without restarting the web interface:

- `awaiting clarification (1)`; the `Icebox` card is only under `every tracked card`.
- Its reason reads `parked in 'Icebox' — no onboarded repository could be identified …`, matching
  `robot-army cards`.
- It has no `rescan` button; the other card does.
- An `IGNORE LIST MISMATCH` banner names `['Icebox']` and `[]`. Restart the web interface and it
  goes; the page is otherwise unchanged.

```bash
jq .ignore_lists ~/.local/state/robot-army/heartbeat.json      # ["Icebox"]
```

4. Stop the daemon. `/cards` now judges against the web interface's own list: after its restart in
   step 3 that is `["Icebox"]`, and the page is unchanged with no banner.
