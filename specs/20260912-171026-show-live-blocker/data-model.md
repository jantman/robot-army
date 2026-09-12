# Data Model: `show` Reports What Blocks an Item Now

No schema change, no new state file, no new audit action.

## Stored, unchanged

| Column | Written by | Meaning after this change |
|---|---|---|
| `work_items.failure_reason` | `dispatch._fail`, `poll._settle`, `retry` | Why the item failed. History. |
| `work_items.blocked_reason` | the same three, alongside `failure_reason` | What blocked it at that moment. History. No longer presented as present tense. |

## Computed, never stored

**`LocalBlocker`** (frozen dataclass in `operations`): the result of `retry`'s local checks.

| Field | Type | Meaning |
|---|---|---|
| `reason` | `str \| None` | The refusal message, word for word as `retry` prints it; `None` when nothing local blocks. |
| `unresolved` | `bool` | `True` when the repository does not resolve to a clone, so `check_gates` never ran. |

**`current_blocker`** (a key of `show`'s `result.data`, and so of `show --json` and the web item
payload):

| Key | Type | Meaning |
|---|---|---|
| `status` | `"blocked" \| "clear" \| "unknown" \| "not_checked"` | `not_checked` for any state other than `failed`. |
| `reason` | `str \| None` | Set when `status` is `blocked`. |
| `error` | `str \| None` | Set when `status` is `unknown`. |
| `differs_from_recorded` | `bool` | `status` is `blocked`, a `blocked_reason` is stored, and the two are different. |

`result.data["item"]["blocked_reason"]` keeps its stored value and meaning.
