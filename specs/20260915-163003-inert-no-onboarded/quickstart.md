# Quickstart: Say so when nothing is onboarded

Validation scenarios. The automated ones are the unit tests in
`tests/unit/test_nothing_onboarded.py`; the manual one re-runs issue #83's reproduction.

## Automated

```bash
uv run pytest tests/unit/test_nothing_onboarded.py
uv run pytest            # the full suite, which is the guard that no other reason's text moved
```

## Manual — the issue's reproduction, at a safe effect level

```bash
mv ~/.local/state/robot-army/state.db /tmp/state.db.bak
robot-army run --once --effect-level no-remote
robot-army repos                   # everything NOT ONBOARDED
robot-army doctor; echo $?         # expected: [FAIL] onboarded repositories …; exit 4
robot-army cards --state needs_info  # expected: every reason names `robot-army onboard`
mv /tmp/state.db.bak ~/.local/state/robot-army/state.db
robot-army doctor                  # expected: [ok] onboarded repositories  N onboarded
```

**Expected**, against [the contract](contracts/inert-installation.md): `doctor` fails on the new
check and exits 4; no held card's reason tells its author to edit the card.
