# Contract: what robot-army writes on an issue

Three variants, one posting rule each. Nothing else is ever written to an issue by dispatch,
and no comment is ever edited or deleted after it is posted (FR-004).

All values appear in backticks. Every line is `- Label: value`, so the whole comment stays
scannable in GitHub's rendering at any width (FR-011).

---

## 1. First dispatch — `attempt == 1`

**Posted when**: a session has been confirmed running, from the single call site at the end of
`_dispatch_item`. Never before confirmation (FR-006).

```markdown
🤖 robot-army dispatched a session for this issue.

- Host: `orion`
- Session: `ra-robot-army-38`
- Session id: `2f1c9c3e-6a54-4a0b-9f0d-4c2a1d8e77b1`
- Branch: `robot-army/issue-38-issue-comments-on-dispatch`
- Worktree: `/home/jantman/worktrees/robot-army/issue-38`
```

| Field | Source | Notes |
|---|---|---|
| Host | `host_name()` | `os.uname().nodename`, or `unknown` (§4) |
| Session | `plan.title` | `prompt.session_name(repo_key, issue_number)` — the same string the launch used, not a re-derivation |
| Session id | `session_id` | The UUID the system generated; `sessions.session_id`, the transcript name, and the exit-spool key |
| Branch | `branch` | The branch the worktree is on, and the branch a PR for this work is opened from — this is the whole of the PR correlation (FR-001, spec assumption 4) |
| Worktree | `worktree_path` | Absolute path *on `Host`* |

## 2. Reassignment — `attempt > 1`

**Posted when**: as above, for any dispatch that is not the item's first — `robot-army resume`,
`robot-army restart`, or the same via the web interface.

Resumed (context restored):

```markdown
🤖 robot-army reassigned this issue to a new session (attempt 2).

- Host: `orion`
- Session: `ra-robot-army-38`
- Session id: `9d4e0b77-2c31-4f5a-8e77-b1a0c2d31f45`
- Continues: `2f1c9c3e-6a54-4a0b-9f0d-4c2a1d8e77b1` (that session's context was restored)
- Branch: `robot-army/issue-38-issue-comments-on-dispatch`
- Worktree: `/home/jantman/worktrees/robot-army/issue-38`
```

Restarted (fresh context):

```markdown
- Supersedes: `2f1c9c3e-…` (this session starts without that session's context)
```

No predecessor could be identified (FR-010):

```markdown
- Supersedes: no earlier session is on record
```

**Which line appears** is decided in this order, and no other:

1. A resume passed the id of the session whose context was restored → `Continues:`.
2. Otherwise the highest-attempt session row below this one → `Supersedes:`.
3. Otherwise → `Supersedes: no earlier session is on record`.

The attempt number is stated in the opening line so that a reader scrolling an issue with
several of these gets the ordering without comparing UUIDs.

## 3. Failed attempt

**Posted when**: a dispatch fails before a session is confirmed — a blocked gate, a hook
failure, a pre-launch validation failure, a launch error, or an unconfirmed launch. This is the
existing failure comment with a host line added (FR-005).

````markdown
🤖 robot-army could not start a session for this issue.

- Host: `orion`

```
kitty could not open a window: …
```
````

The reason is fenced because it is machine text of unbounded shape.

---

## 4. Rules that hold for all three

| Rule | Behaviour |
|---|---|
| Host cannot be determined | The line still appears, reading `` `unknown` ``. Never omitted, never empty (FR-009). |
| Posting fails | Logged as an `error` record under `github.comment` naming the work item; the item's state and the session's fate are unchanged (FR-007). |
| Effect level below `live` | Nothing reaches GitHub. The `SimulatedIssueWriter` records the full intended body under `github.comment` with `simulated: true` (FR-008). |
| Issue closed, locked, transferred, deleted | Indistinguishable from any other posting failure, and handled as one. |
| Same item dispatched twice | Two comments. Nothing is edited in place (FR-004). |

## 5. The matching log record

`dispatch.confirmed` carries the same facts, so a comment and a log line can be matched without
inference:

```json
{"ts":"…","component":"dispatch","kind":"event","action":"dispatch.confirmed","outcome":"ok",
 "entity_type":"work_item","entity_id":41,
 "detail":{"session_id":"9d4e…","session_name":"ra-robot-army-38","host":"orion","attempt":2,
           "resumed_from":"2f1c…","pid":31415,"scope":"…","window_id":7,"socket":"…",
           "worktree":"…","branch":"…"}}
```

`resumed_from` keeps its existing meaning (present only for a resume). `supersedes` appears only
when a predecessor was identified without a resume. `host` and `session_name` are new and always
present.
