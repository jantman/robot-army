# Quickstart: seeing the waiting count work

**Feature**: [spec.md](spec.md) | **Contract**: [contracts/chrome-bar.md](contracts/chrome-bar.md)

## Prerequisites

```bash
uv sync
```

Nothing else. No daemon is needed to see any of this — the interface renders from the database
and says loudly when the daemon is absent, which is the page state every step below is read in.

## The whole gate

```bash
uv run pytest
```

The suite must pass. It is the only completion criterion the constitution names.

## The cases, and where each is asserted

| What to see | Contract | Test |
|---|---|---|
| the pill counts three states, scoped both ways | C7–C10 | `tests/unit/test_web_chrome_waiting.py` |
| the count and its page agree | **C12** | `tests/unit/test_web_chrome_waiting.py` |
| zero renders quiet, one and many render warn | C2–C4 | `tests/unit/test_web_chrome_waiting.py` |
| no pill on a 404 | C1 | `tests/unit/test_web_chrome_waiting.py` |
| the quiet bar is five pills | C21 | `tests/unit/test_web_chrome_waiting.py` |
| `failed` is listed, with its controls | C22–C24, C28 | `tests/unit/test_web_views.py` |
| the failed section discloses withheld rows | C25, C26 | `tests/unit/test_web_views.py` (`WITHHELD_VIEWS`) |
| no level pill at `live`, pill at `unknown` | C13–C16 | `tests/unit/test_web_non_live_banner.py` |
| visibility pill only when rows are included | C17–C19 | `tests/unit/test_web_simulated_default.py` |
| every withholding view discloses it itself | **C20** | `tests/unit/test_web_views.py`, and R6's table |

C12 and C20 are bolded because they are the two properties that cannot be inferred from the
others. C12 ties the number to the page; C20 is the precondition without which C18 must not
ship.

## Seeing it by hand

Run the interface against a scratch database:

```bash
export ROBOT_ARMY_STATE_DIR=$(mktemp -d)
uv run robot-army serve &
```

Open `http://127.0.0.1:8420/active`. With an empty database the bar reads:

```
[DAEMON NOT RUNNING] [0/N sessions (…)] [order: oldest-first] [0 need me] [0 anomalies]
```

Two things to notice, both of them the point of this feature:

- **No `effect level: live` pill and no `simulated rows hidden` pill.** Their absence is the
  statement. Everything on the bar is now either a fact about the machine or a count.
- **`0 need me` is present, quiet, and is a link.** A count at zero is an answer; a level pill
  at `live` is not.

Now put something in a parked state — any of the three will do — and reload. The pill turns
`warn` and states the number. Click it: the page lists exactly that many items, across its
three sections, and the nav entry it came from reads `needs me`.

### Seeing the scoping hold

Append `?include_simulated=0` and `?include_simulated=1` to the same view with a simulated item
parked. The pill's number changes with the setting, and the page it links to lists exactly that
number under the same setting. **If those two ever differ, the feature is wrong** — that
disagreement, one surface printing two numbers, is the defect this scoping exists to prevent
and is what C12 asserts.

### Seeing the two silent pills speak when they should

```bash
uv run robot-army serve --effect-level plan
```

On a `plan` instance both pills return: `effect level: plan — simulated`, because the level is
not `live`, and `simulated rows included`, because below `live` that is the default. The
asymmetry is intended — in both cases the pill marks the surprising state, and below `live`
both states are surprising.

With rows explicitly hidden on that instance (`?include_simulated=0`), the visibility pill goes
and the route back is the per-table note — `N simulated rows hidden — show them` — beneath the
table that withheld them. That note is R9's discoverability, offered where there is something
to discover. Verify it is there before trusting the pill's absence.

## What is not here

No new command, no config key, no migration, no audit action. `robot-army status` already
printed these counts; this is the web catching up.
