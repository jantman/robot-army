# Phase 0 research

Nine questions, all answerable from the tree. No unknowns remain.

---

## R1 — Where does the item number come from at the posting site?

**Decision**: `item.id`, already a local at every call site.

`_comment_failure(boundaries, audit, item, reason)` is called from five places in
`dispatch.py` (lines 1077, 1125, 1172, 1208, 1293) and every one of them already holds the
work item. `_safe_comment` reads `item.id` for its own error record. Nothing has to be
queried, and no call site changes shape.

**Alternatives**: passing `item_id` separately was considered and rejected — the item is
already the parameter, and two ways to name the same row is the kind of second path that
goes stale.

---

## R2 — Does `reason` stay a parameter of `failure_comment_body`?

**Decision**: no. The parameter is removed, and `_comment_failure` drops it too.

A parameter the body ignores is worse than no parameter: it reads as though the reason is
still considered, and the next person to touch the function has to prove it is not used. The
signature becomes `failure_comment_body(*, host: str, item_id: int)`, and
`_comment_failure(boundaries, audit, item)`. Each of the five call sites keeps its `reason`
local — `_fail` still takes it — and stops passing it onward.

This is the enforcement mechanism for FR-001 and FR-003: the function cannot leak what it is
not given. A test asserting "the reason is absent" can be deleted tomorrow; a signature that
never receives the reason cannot regress quietly.

---

## R3 — How long can a failure reason actually be?

**Decision**: bounded and short. No truncation, no disclosure control, no expander.

Every producer was read:

| Producer | Shape |
|---|---|
| `check_gates` (`dispatch.py:357,372,386,417,426,447`) | one constructed sentence |
| author backstop (`dispatch.py:1030`) | one constructed sentence |
| `worktree.prepare` (`worktree.py:158,213,245`) | one sentence, with a git exception interpolated |
| pre-launch validation (`dispatch.py:1168`) | `"pre-launch validation failed: " + "; ".join(problems)` |
| launch failure (`dispatch.py:1196`) | `f"launch failed: {exc}"` |

Hook output — the one genuinely unbounded thing in this system — goes to
`PreparationResult.output` and is stored in the item's `prepare_output` column. It is **not**
what lands in `failure_reason`; the reason for a failed hook is
`"preparation step N (description) failed"`. So the longest realistic reason is the
fingerprint sentence that started this feature, at roughly 220 characters.

FR-009 is therefore satisfied by showing the whole thing. Adding a truncation rule with a
"show more" affordance would be a moving part built for a case that does not occur —
Principle I.

**Alternatives**: a `<details>` expander, and truncation at N characters with a link to the
item page. Both rejected on the measurement above; either can be added the day a reason gets
long, and the spec's FR-009 already states the rule such a change would have to obey.

---

## R4 — Can a reason contain markup?

**Decision**: yes, and it is already handled. A test pins it anyway.

Reasons interpolate git exception text and filesystem paths, so `<`, `>` and `&` are all
reachable. `html.tag` runs every child through `escape` unless it is a `Markup`, and
`Markup` is a distinct type rather than a convention (`html.py:31`). Passing a plain `str`
reason to `div(...)` is escaped by construction.

FR-008 needs no new code. It needs a test, because the thing that would break it is somebody
later wrapping the reason in `Markup` to get a line break, which is exactly the mistake the
type exists to make visible.

---

## R5 — Is the reason already shown anywhere in the web interface?

**Decision**: yes, in two places, and neither is the one the operator was sent to.

- **The item's own page** (`pages.py:1931`) shows `failure` and, separately, `blocked` — the
  stored reason and the live re-check, kept apart since issue #63.
- **`/queue`'s blocked table** (`pages.py:933,1156`) shows
  `failure_reason or blocked_reason` for any failed item.

So the fact is not missing from the system; it is missing from the card. The chrome pill
added by issue #182 counts failed items and points at **needs me**, and that page is the one
that does not show it. This is the whole shape of the defect: the destination of the count
is the one listing that cannot explain what it counted.

**Consequence for the design**: the card should render the same expression `/queue` already
renders — `failure_reason or blocked_reason` — rather than inventing a second rule for which
column wins. Two surfaces disagreeing about which reason is *the* reason is the next defect
in this family.

---

## R6 — `worktree_missing`: condition the banner, or redefine the field?

**Decision**: redefine the field.

`_signal_row` computes `worktree_missing = not signals.get("worktree_present", False)`
(`pages.py:1231`), and `local_resume_signals` returns `worktree_present: False` immediately
when `not item.worktree_path or not item.branch` (`operations.py:1356`). So an item that
never had a checkout reports its checkout as missing — in the HTML **and** in the JSON.

Conditioning only the banner would fix the sentence and leave `"worktree_missing": true` in
the JSON payload for an item that never had a worktree. That is the same false claim in the
representation the spec's FR-012 says must agree with the page.

The field becomes: **a checkout path is recorded and it is not present.** Its only consumers
are `pages.py:1231`, `pages.py:1317` and the tests, so the redefinition is contained.
`test_a_missing_checkout_is_surfaced_distinctly` sets a `worktree_path` before asserting, so
it stays green — which is itself evidence the new meaning is the one that test always
intended.

**Alternatives**: a second field (`worktree_lost` beside `worktree_missing`) was rejected —
two fields that differ only in a case nobody wants the old answer for is speculative
generality.

**A known edge, deliberately unaddressed**: `local_resume_signals` also short-circuits when
`item.branch` is empty, so an item with a path and no branch would report a missing checkout
under either definition. The two columns are written in one transaction
(`dispatch.py:1100`), so the state is not reachable from a completed preparation. Named here
rather than guarded against.

---

## R7 — What does this change about the record? (Principle III)

**Decision**: nothing is lost. The reason survives in three places, none of them public.

| Where | Written by | Contains the reason? |
|---|---|---|
| `state.work_item` audit record | `transition_work_item` (`states.py:362`), `detail.reason` | yes, in full |
| `work_items.failure_reason` / `.blocked_reason` | `_fail` (`dispatch.py:1546`) | yes, in full |
| Notification body | `notifications.emit` from `_fail`, `detail=reason` | yes, in full |
| `github.comment` audit record, `detail.body` | `_safe_comment` | **no longer** — it records the body, and the body no longer has it |

The fourth row is the only change, and it is not a gap: the same record's
`entity_id` names the work item whose `state.work_item` record, written moments earlier in
the same dispatch, carries the reason in full. Reconstruction from the log alone is intact.

Below `live`, `SimulatedIssueWriter` records the body that *would* have been posted — so the
documented way to check the wording without spending a real issue keeps working, and now
shows the quiet body.

---

## R8 — What existing tests and contracts assert the old behaviour?

**Decision**: three tests change, one contract section is superseded.

| Location | Assertion | Disposition |
|---|---|---|
| `tests/unit/test_issue_comments.py:161` | `"```\nkitty: no such window\n```" in text` | rewritten — it becomes the test that the reason is *absent* |
| `tests/integration/test_dispatch.py:1216` | `"trust check failed" in body` | becomes an assertion that it is **not** in the body, with the reason still asserted on `item.blocked_reason` two lines below (already there) |
| `tests/unit/test_web_views.py:203` | missing-checkout banner | unchanged; it sets a `worktree_path`, so it passes under the new meaning |
| `specs/20260830-135239-dispatch-issue-comments/contracts/issue-comment.md` §3 | "The reason is fenced because it is machine text of unbounded shape" | superseded by this feature's `contracts/failure-comment.md`; the old file is history and is left where it is |

`test_a_failure_comment_never_claims_a_session` needs no change.

---

## R9 — Should the comment say anything about where the operator should look?

**Decision**: no. Host and item number, nothing else.

Considered and declined: a line like ``run `robot-army show 126` `` would be useful to the
one reader who matters and harmless to everyone else. It was declined because it publishes
the tool's local interface on somebody else's issue for no benefit the item number does not
already deliver — the operator who recognises the host already knows what to type. The
operator's instruction was host and item number only, and the narrower reading is the one
that cannot leak.
