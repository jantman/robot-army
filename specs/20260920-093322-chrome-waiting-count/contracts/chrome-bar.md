# Contract: what the chrome bar renders

**Feature**: [../spec.md](../spec.md) | **Date**: 2026-09-20

The chrome bar is the strip of pills on every view. This contract states, for each pill this
feature touches, exactly when it appears and what it says. The cases are numbered so tasks and
tests can name them.

## The waiting pill

Rendered from `chrome["waiting_count"]`.

| # | Given | Then |
|---|---|---|
| C1 | `waiting_count` absent from the chrome | no pill at all |
| C2 | `waiting_count == 0` | `<a href="/interrupted…" class="pill quiet">0 need me</a>` |
| C3 | `waiting_count == 1` | `…class="pill warn">1 needs me` |
| C4 | `waiting_count == 3` | `…class="pill warn">3 need me` |
| C5 | any count | the link carries the current visibility setting, via the same `_visibility_suffix` every other chrome link uses |
| C6 | any count | the pill is rendered immediately before the anomaly pill |

C1 is the `server._bare` case — 404, 405, schema refusals. C2 is deliberately *present*: a
count at zero answers a question the operator is asking, unlike the two pills below.

## The waiting count itself

Set by `pages.chrome`.

| # | Given | Then |
|---|---|---|
| C7 | items in `awaiting_review`, `interrupted`, `failed` | `waiting_count` is their total |
| C8 | items in any other state only | `waiting_count` is `0` |
| C9 | a simulated item in a counted state, `include_simulated=False` | it is not counted |
| C10 | a simulated item in a counted state, `include_simulated=True` | it is counted |
| C11 | any request | the JSON body carries `waiting_count` with the same value the pill shows |
| C12 | any count and visibility setting | following the pill's link with that setting lists exactly `waiting_count` items |

C12 is the agreement requirement. It is not a restatement of C7–C10: it is the property that
ties the count to the page, and it is what a test must assert end to end rather than on the
two halves separately.

## The effect-level pill

| # | Given | Then |
|---|---|---|
| C13 | effective level is exactly `live` | no pill |
| C14 | effective level is `plan`, `prepare`, or any level below `live` | `class="pill level simulated">effect level: {level} — simulated` |
| C15 | effective level is `unknown` (running daemon whose level could not be read) | the pill renders |
| C16 | bare chrome, where `effect_level` is `unknown` | the pill renders |

C15 and C16 are the whole reason the condition is `!= "live"` and not `in BELOW_LIVE`.
"We could not tell" is news; `live` is not.

C14 is unchanged behaviour, restated here so a change to it is visibly a change.

## The simulated-visibility pill

| # | Given | Then |
|---|---|---|
| C17 | `include_simulated` is `True` | `<a href="{path}?include_simulated=0" class="pill quiet">simulated rows included</a>` |
| C18 | `include_simulated` is `False` | no pill |
| C19 | `include_simulated` absent (bare chrome) | no pill — unchanged |

C17 is the normal state on an instance below `live`, where rows are included by default. The
asymmetry with C13 is intended: below `live` is the surprising state in both cases.

## The precondition on C18

| # | Given | Then |
|---|---|---|
| C20 | any view that can withhold simulated rows, rendering with at least one withheld | that view itself names the number withheld and offers the link that reveals them |

C20 must hold for `/active`, `/queue`, `/interrupted` (each of its three sections), `/cards`,
`/anomalies` and `/log`. It is verified in [research.md](../research.md) R6 and asserted in
tests. **If C20 fails anywhere, C18 must not be implemented** — on that view the pill would be
the only route back.

## The whole bar, quiet

| # | Given | Then |
|---|---|---|
| C21 | level `live`, daemon running and healthy, dispatch not paused, no anomalies, `waiting_count == 0`, simulated rows hidden | the bar is exactly: the daemon pill, the capacity pill, the order pill, the waiting pill at zero, the anomaly pill at zero — and no effect-level pill, no pause pill, no visibility pill |

C21 is the readability claim the whole feature rests on: every pill on a quiet bar is either a
fact about the machine or a count answering a question. Six pills become five, and the two that
went were the two that never said anything.

## The destination page

| # | Given | Then |
|---|---|---|
| C22 | an item in `failed` | it is listed on `/interrupted`, in its own section |
| C23 | nothing in `failed` | the failed section renders its own empty text rather than vanishing |
| C24 | a failed item | the controls its state permits (`retry`, `reset`, `abandon` as `legal_actions` determines) are offered, from the one existing source of legal actions |
| C25 | the failed section withholding every row it matched | its empty text states the number withheld and offers the reveal link |
| C26 | the failed section rendering rows while withholding others | the page's withheld note includes them, and the section's empty text does not |
| C27 | any view | the nav entry for `/interrupted` reads `needs me` |
| C28 | the JSON body of `/interrupted` | it carries `failed` rows and a `failed` key under `counts` |

C25 and C26 together are milestone 009's invariant — each withheld row disclosed exactly once —
extended to the new section.
