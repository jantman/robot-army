# Contract: the comment for a failed attempt

Supersedes §3 of
[`contracts/issue-comment.md`](../../20260830-135239-dispatch-issue-comments/contracts/issue-comment.md).
Variants 1 and 2 of that contract — the dispatch and reassignment comments — are unchanged.

## The body

**Posted when**: a dispatch fails before a session is confirmed — a blocked gate, an author
refusal, a hook failure, a pre-launch validation failure, a launch error, or an unconfirmed
launch. Five call sites, one body.

```markdown
🤖 robot-army could not start a session for this issue.

- Host: `orion`
- Work item: `126`
```

| Field | Source | Notes |
|---|---|---|
| Host | `host_name()` | `os.uname().nodename`, or `unknown`. Trust is granted per machine, so which machine failed is a real fact and is not sensitive. |
| Work item | `item.id` | The local item id — the number `robot-army show` takes. An address on the operator's machine, meaningless to anyone else, which is the point. |

## What it must not contain

**The failure reason, in any form.** Not fenced, not summarised, not categorised, not
abbreviated. The comment is identical for every cause.

This is enforced by the signature, not by review: `failure_comment_body(*, host, item_id)`
is never given the reason. A test asserts the absence; the signature is what makes the
absence structural.

Consequently the body also carries no filesystem path, no repository-local filename, no
command invocation, and no machine-generated output — because the reason was the only thing
that could have carried them.

**Why.** The repository being worked on may be public and is frequently not the operator's.
A failure reason interpolates git exception text, paths under the operator's home directory,
settings filenames, and the exact local command that would clear the condition. Principle V
forbids committing that to a world-readable repository; posting it to a world-readable issue
is the same disclosure with less control, because a comment on somebody else's repository is
not the operator's to take back.

## Where the reason goes instead

Three places, none public:

| Surface | Carries |
|---|---|
| `state.work_item` audit record | `detail.reason`, in full |
| `work_items.failure_reason` / `.blocked_reason` | in full, readable by `robot-army show <id>` and the web interface |
| Notification, if configured | `detail`, in full — the operator's own device |

## Rules inherited unchanged

| Rule | Behaviour |
|---|---|
| Host cannot be determined | The line still appears, reading `` `unknown` ``. Never omitted, never empty. |
| Posting fails | Logged as an `error` record under `github.comment` naming the work item; the item's state is unaffected. Nothing retries. |
| Effect level below `live` | Nothing reaches GitHub; `SimulatedIssueWriter` records the intended body under `github.comment` with `simulated: true`. The documented `jq` recipe on the guide's outcome page now prints this body. |
| Nothing is edited or deleted | One comment per attempt, in order. Comments posted before this change stay as they are. |
| Every line is `- Label: value` | Two lines now instead of one plus a fence. |
