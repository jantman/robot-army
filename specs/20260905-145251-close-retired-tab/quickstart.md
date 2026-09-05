# Quickstart — validating window closing

Prerequisite for everything below:

```bash
uv sync
uv run pytest            # must pass before anything here is meaningful
```

---

## Scenario 0 — The mechanism, already verified against real kitty

Run during planning, before a line of the feature was written, because the previous feature in this
area shipped a claim about kitty that had never been checked. Reproduce it in one command if you
want to see it yourself; it creates a window and closes it again.

```bash
K="kitty @ --to unix:$(ls /tmp/mykitty-* | head -1)"
WID=$($K launch --type=tab --hold --var ra_item=999999 --title ra-marker-probe -- true)
$K ls | jq -r '.[].tabs[].windows[] | select(.user_vars.ra_item) | "\(.id) \(.user_vars.ra_item) \(.title)"'
$K close-window --match id:$WID
$K ls | jq '[.[].tabs[].windows[] | select(.user_vars.ra_item)] | length'
```

Measured on kitty 0.48.2:

| Claim | Result |
|---|---|
| `--var ra_item=…` is reported by `kitty @ ls` as `user_vars` | **yes** — `52 999999 ra-marker-probe` |
| `--hold` keeps the window after its command exits | **yes** — the window survived `-- true` returning immediately |
| `close-window --match id:<n>` closes a held window | **yes** — `0` marked windows remaining |
| Window ids are monotonic within one kitty | **yes** — the probe got 52, above the 49 and 50 recorded for items 54 and 45 |

That last row is also the evidence for the identity rule: ids climb within one kitty process and
restart from 1 when kitty restarts, so a **stored** id is not identity and the marker is
([research R3](./research.md)).

---

## Scenario 1 — A finished item leaves no tabs (US1, SC-001, SC-002)

Run as a unit test against the simulated display, which keeps its own window map, so the whole
decision path is exercised without a terminal.

| Setup | Expectation |
|---|---|
| `done` item, one session `lost`, one window marked with its id | window closed, `windows_closed == 1` |
| the same item resumed once — two sessions, both ended, two windows | **both** windows closed (FR-002) |
| the same item, but one session still `running` | **no** window closed (FR-004) |
| `done` item with **no** session rows at all — a rebuilt database | no window closed (W2) |

Then the whole pass, not the sweep alone: seed the first case, call `reconcile()`, and assert the
window is gone and `reconcile.pass` reports `windows_closed: 1`.

---

## Scenario 2 — Failed and abandoned work keeps its window (US2, SC-004, SC-005)

The half that must not regress. `--hold` exists because a vanishing window destroyed the only
evidence of a failed launch, so each of these runs **ten passes**, not one — a build that closes the
window on the second pass would satisfy a single-pass assertion.

| Setup | Expectation across ten passes |
|---|---|
| `failed` item with a marked window | never closed |
| `abandoned` item with a marked window | never closed |
| a window with **no** `ra_item` at all | never closed, never even considered |
| a window whose `ra_item` names an item id that does not exist | never closed |
| a window whose `ra_item` is not an integer | never closed, and nothing raises |

The third row is the one to keep honest: it stands in for every window the maintainer opened
themselves, and the assertion is that the sweep does not so much as look at it.

---

## Scenario 3 — Stopping a finished item's session by hand (US3)

Proves the rule is about the work rather than the route, and that `operations.cancel` needed no
change:

1. seed a `done` item with a `running` session and a marked window;
2. stop it with `operations.cancel`;
3. run a pass;
4. assert the window is closed — and confirm by `git diff` that `operations.py` is untouched.

Repeat with a `failed` item and assert the window survives.

---

## Scenario 4 — The failure paths (FR-013, FR-014)

| Case | Expectation |
|---|---|
| The display raises when listing windows | recorded once for the pass; the pass completes; `windows_closed == 0` |
| One close raises, a second window also qualifies | the second is still closed; the failure is recorded; the failed one is not counted |
| A window vanished between the listing and the close | **success** — not recorded as a failure, not counted |
| No candidate items at all | the display is **never called** — assert on the stub, since this is what keeps a machine with no kitty from logging 1,440 failures a day (research R6) |

The last row is a test about a call that must *not* happen, which is easy to omit and is the whole
of the cost argument.

---

## Scenario 5 — On the machine, after merging

There is no live evidence left to reproduce against: the two windows that prompted this feature were
closed by hand before implementation began. So the real-machine check is forward-looking — take the
next item through to a merged pull request and confirm the tab goes on its own.

```bash
uv run robot-army reconcile
jq -r 'select(.action == "kitty.close_window") | [.ts, .target, .outcome] | @tsv' \
  ~/.local/state/robot-army/logs/audit-$(date -u +%F).jsonl
```

Then count marked windows and expect none:

```bash
kitty @ --to unix:$(ls /tmp/mykitty-* | head -1) ls \
  | jq '[.[].tabs[].windows[] | select(.user_vars.ra_item)] | length'
```

---

## Regression guards

```bash
uv run pytest
uv run ruff check src/ tests/
```

Three existing guarantees to re-assert, because this feature moves close to each:

- **`reconcile.py` never names the effect level.** The simulated display is chosen by the wiring, so
  no branch in the sweep may mention it — the grep-the-source test covers the new code.
- **`--hold` is still passed on every launch.** FR-017, checked by reading `KittyDisplay.open`.
- **`cleanup.live_sessions` still has one definition**, now with three callers rather than two.
