# Contract: what a needs-me card says about a failure

The **needs me** page (`/interrupted`) renders one card per item in `interrupted`,
`awaiting_review` and `failed`, all three through `_interrupted_card`. This contract covers
the two elements that distinguish a failed card; everything else on the card is unchanged.

## The reason

Rendered for items in `failed`, from `failure_reason or blocked_reason` — the same
expression `/queue`'s blocked table uses.

```html
<div class="reason">committed tool-permission settings at main differ from what was
approved at onboarding (added: ['.claude/settings.json']; …)</div>
```

| Case | Rendered |
|---|---|
| A reason is recorded | The reason, in full, as literal text |
| Both columns empty or absent | `no reason was recorded` — stated, not left blank |
| The item is `interrupted` or `awaiting_review` | Nothing. No empty element. |

**In full, not truncated.** Every producer of a failure reason constructs one bounded
sentence; unbounded hook output goes to `prepare_output`, not here. If that ever stops being
true, a card showing less than the whole must say so and the whole must stay reachable.

**As literal text.** The reason is passed to the markup helpers as a plain `str`, so
`html.escape` runs on it. It must never be wrapped in `Markup` — that type exists so this
question has a visible answer.

**Stored, not re-checked.** This is the sentence recorded when the item failed. The item's
own page is where the stored reason and the live re-check are shown side by side, and that
distinction (issue #63) is not restated here.

## The missing-checkout banner

```html
<div class="banner error">The isolated checkout is missing. Resuming will fail until it is
restored; abandoning is the usual answer.</div>
```

| `worktree_path` | Checkout on disk | Banner |
|---|---|---|
| recorded | absent | **shown** — unchanged from today |
| recorded | present | not shown |
| empty | n/a | **not shown** — nothing was created, so nothing is missing |

The third row is the change. A gate refusal fails the item before `worktree.prepare` runs,
so a blocked item has no path, and the old rule described it as having lost something it
never had — while advising the one remedy (abandon) that is wrong for it.

## The JSON form

`/interrupted` in its JSON representation carries, on the same rows and under the same
simulated-visibility scoping:

| Key | Value |
|---|---|
| `failure_reason` | already present via `operations._item_dict`; unchanged |
| `blocked_reason` | already present; unchanged |
| `worktree_missing` | **redefined**: a path is recorded and it is not present |

The HTML and the JSON make the same claim about the same item. That is the property worth
testing, not either one alone.
