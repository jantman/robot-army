# Contract: Simulated-Row Visibility and the Non-Live Announcement

Amends [002's HTTP contract](../../002-web-ui/contracts/http-api.md). Two of its universal rules
are superseded here; everything else in that document stands unchanged.

**This is not a stable public API** (002 FR-009). It is versioned by the commit that produced
it. The only consumer is the author's own browser and `curl`.

---

## Superseded rules

### Replaces: "Simulated rows are excluded unless `?include_simulated=1`"

> **Simulated rows are shown by default when the effective effect level is below `live`, and
> excluded by default at `live`. `?include_simulated=` overrides the default in both
> directions.** When shown, every simulated row carries a visible marker in HTML and
> `"simulated": true` (or `"dry_run": true`) in JSON, unchanged.

### Replaces: "Every response carries chrome: effect level, daemon liveness…"

> Unchanged, plus: **every response below `live` carries a banner naming the level and what the
> page's values are not**, and the effect-level pill is styled as an alarm below `live`.

---

## `?include_simulated=` — accepted values

Applies to every `GET` view and, as a form field, to every `POST` action.

| Value (case-insensitive) | Meaning |
|---|---|
| `1`, `true`, `yes`, `on` | show simulated rows |
| `0`, `false`, `no`, `off` | withhold simulated rows |
| absent, empty, anything else | unstated — the effective level decides |

An unrecognised value is **not** an error. `?include_simulated=treu` behaves as if the
parameter were absent (`200`, not `400`), because the parameter is typed by hand on a phone.

### Resolution

| Stated | Effective level | Rows shown |
|---|---|---|
| `1` | any | simulated included |
| `0` | any | simulated excluded |
| — | `plan`, `local`, `no-remote`, unknown | simulated included |
| — | `live` | simulated excluded |

### Propagation

Every link and form the interface generates states the resolved value **explicitly, in both
directions**: `?include_simulated=1` or `?include_simulated=0`. It is never omitted from a
generated URL.

This is a visible change from 002, where the parameter was omitted when false. A stated
preference therefore survives navigation, action submission, the `303` after a `POST`, and the
ten-second auto-refresh — which omission can no longer achieve, because omission now means
"use the default" rather than "false".

---

## Effective level

One value per request, used by the banner, the pill, and the visibility default. Defined as the
more simulated of:

1. the interface's own configured level (`[daemon] effect_level`, or `serve --effect-level`), and
2. the level of the daemon currently holding the lock, read from its heartbeat,

ordered `plan` < `local` < `no-remote` < `live`. When no daemon holds the lock, (1) stands
alone. When a daemon holds the lock and no heartbeat can be read, the value is `unknown` and is
treated as most simulated.

Consumers reading `effect_level` from the payload get the interface's configured level as
before; `effective_level` is the new key and is the one the page's own styling reflects.

---

## The non-live banner

Rendered on **every** view — including `/log`, `/anomalies` and refusal pages that carry chrome
— whenever the effective level is below `live` or unknown. Absent entirely at `live`.

- Same slot, weight and `banner error` styling as the existing `DAEMON NOT RUNNING` and
  `EFFECT LEVEL MISMATCH` banners.
- Names the effective level.
- States, in operator terms, what did not really happen at that level. **The whole banner is
  derived from `effects.REAL_AT`, not only its list** — the sentences around it turn on whether
  *every* simulatable boundary is simulated, which is true only at `plan`:

| Effective level | Framing |
|---|---|
| `plan` | "nothing on this page really happened", closing "Nothing here reached your repositories, GitHub, Trello, or a terminal." |
| `local`, `no-remote` | "parts of what these rows describe did not really happen", closing "Everything listed above was skipped; anything not listed was really carried out." |

  Fixed prose would be false wherever the level performs some effects for real: at `local`
  branches and commits are genuinely created, and at `no-remote` a real session runs in a real
  terminal. A banner that overstates is a banner that stops being read.

  The list itself:

| Effective level | Stated consequences |
|---|---|
| `plan` | no session is really launched; no branch, commit or worktree is really created; no hook really runs; no terminal window really opens; no issue or comment is really written and the issue numbers shown are invented; no card really moves on the board; no notification is really sent |
| `local` | no session is really launched; no terminal window really opens; no issue or comment is really written and the issue numbers shown are invented; no card really moves on the board; no notification is really sent |
| `no-remote` | no issue or comment is really written and the issue numbers shown are invented; no card really moves on the board; no notification is really sent |
| `live` | *(no banner)* |

- Not dismissible. Does not suppress, and is not suppressed by, the daemon-not-running banner,
  the mismatch banner, or a `?msg=` action banner — all render together.
- When the effective level is `unknown`, the existing `EFFECT LEVEL UNKNOWN` banner carries the
  explanation and **no second banner is emitted**; only the pill styling changes.

## The level pill

| Effective level | Rendered as |
|---|---|
| below `live` | `effect level: <level> — simulated`, class `pill level simulated`, error colour, bold |
| `live` | `effect level: live`, class `pill level live`, muted |
| unknown | `effect level: unknown — simulated`, class `pill level simulated` |

The word `simulated` is in the text as well as in the styling, so a monochrome screenshot or a
colour-blind reader still carries the signal.

## The visibility toggle

The chrome pill that read `simulated rows included` only when rows were included now renders in
both states and is a link:

| Resolved | Text | Links to |
|---|---|---|
| included | `simulated rows included` | the same path with `include_simulated=0` |
| excluded | `simulated rows hidden` | the same path with `include_simulated=1` |

`pill quiet` in both states — it is a control, not a warning.

---

## Withheld-row disclosure

Any section withholding rows it matched states the count and links to the same URL with the
preference flipped. **Per section, not per view**: `/queue` renders ready, dispatching and
blocked, and `/interrupted` renders two states, so a view-wide number would name rows that
section's own empty text is denying — and a view-wide *disclosure* leaves a section free to
claim absence about rows that exist, one notch quieter than the defect this milestone removes.

| Situation | HTML |
|---|---|
| nothing withheld, rows present | table only, no note |
| nothing withheld, no rows | the existing empty text, e.g. `Nothing is ready.` |
| rows withheld, rows present | table, then `N simulated rows hidden — show them` at the foot of the view |
| rows withheld, no rows | `Nothing to show here. N simulated rows are hidden — show them.` in place of the empty text |

Each withheld row is disclosed **exactly once**: an empty section carries its own count in
place of its empty text, and the note at the foot of the view carries the rows withheld from
the sections that did render. The two sets are disjoint and together they are the whole.

Every count is the number the link would actually reveal *on that page*. A count scoped more
widely than the page renders — every simulated work item, say, on a view that shows three
states — replaces an obvious contradiction with a subtler one, which is 008's own standard for
the number it introduced. No section claims absence while withholding (FR-008), and no count
appears when it is zero (FR-009).

## Pages with no context

The dead-end responses — `404`, `405`, the `Host` refusal, a schema mismatch — are rendered
without a database context, so they can resolve neither the effective level nor the default it
implies. They therefore **state no preference at all**: their links carry no
`include_simulated`, and they render no visibility toggle. The destination applies its own
default.

Treating that absence as a stated `0` would put `?include_simulated=0` on every link of every
error page, and a stated `0` beats the level default — so on an instance below `live`, one tap
from a `404` would pin "hide everything".

## Paths the toggle may point at

The visibility toggle links to the view being rendered. A refused `POST` renders its chrome on
a refusal page, so the path carried there is the **referring view**, or `/active`, never the
action route — which has no `GET` handler and would answer `405`.

---

## JSON payload

Three keys, on every view that carries chrome.

```json
{
  "effect_level": "live",
  "effective_level": "plan",
  "include_simulated": true,
  "simulated_preference": null,
  "withheld_simulated": 0
}
```

| Key | Type | Meaning | Status |
|---|---|---|---|
| `effect_level` | `str` | the interface's configured level | unchanged |
| `effective_level` | `str` | the level driving the banner and pill; `"unknown"` if unreadable | **new** |
| `include_simulated` | `bool` | the **resolved** value the rows were selected with | unchanged in name; its default now varies by level |
| `simulated_preference` | `bool \| null` | what the operator stated; `null` if nothing | **new** |
| `withheld_simulated` | `int` | matching rows this response is not showing; always present, never absent | **new on the cards payload**; already present, nested, on status-derived views |

`withheld_simulated` is always present so a consumer never has to distinguish "nothing was
withheld" from "this build does not report it" — the absent-versus-zero ambiguity milestone 008
removed from `status`.

FR-021 requires the JSON representation to hold the same rows the page holds for the same
request. It does, because both come from the one assembly `View` already performs.

---

## Terminal equivalence

`robot-army status`, `cards` and `worktree list` keep excluding simulated rows by default at
every level, and keep disclosing what they withheld (milestone 008). The default is **not**
made level-dependent there: a flag is typed deliberately and is visible in the scrollback,
whereas the web reader has no other way to ask. 002 FR-006 requires every web capability to
have a terminal equivalent, and it does — `--include-simulated` reaches the same rows.

One CLI payload changes: `robot-army cards --json` gains `withheld_simulated`, matching the
key `status --json` already carries. The text output of every command is unchanged.
