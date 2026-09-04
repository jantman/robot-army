# Contract: `retry`, before and after

One operation, reached two ways. `robot-army retry <id>` and `POST /item/<id>/retry` both
call `operations.retry(ctx, item_id, trust_file=…)`; the web front end adds a confirmation
and a CSRF check and nothing else. Everything below therefore holds identically for both,
which is what SC-001 asks for.

## Order of checks

| # | Check | Refusal | Exit | Costs a network call |
|---|---|---|---|---|
| 1 | the item exists | `no work item with id <id>` | `1` | no |
| 2 | the item is `failed` | `work item <id> is <state>; retry applies to failed items` | `3` | no |
| 3 | the repository resolves to a clone | `repository '<key>' does not resolve to a clone any more — …` | `3` | no |
| 4 | `dispatch.check_gates` | `refusing to retry item <id>: the blocking condition still holds.` + the condition | `3` | no |
| **5** | **the issue can be read** | `refusing to retry item <id>: <cause>` | `1` | **yes** |
| **6** | **`poll.evaluate` passes** | `refusing to retry item <id>: the issue is not eligible.` + the reason | `3` | — |

Checks 1–4 are today's behaviour, unchanged and in the same order. 5 and 6 are new. The
read is last among the checks that can be answered offline, deliberately
([R4](../research.md)): an item that cannot dispatch for a local reason spends no rate
limit finding out.

Between 5 and 6 the item's `title`, `body`, `labels` and `author` are rewritten from the
issue just read — on the refused path as well as the allowed one ([R5](../research.md)).

A refusal at check 6 then writes the new reason to **both** `blocked_reason` and
`failure_reason`. `/queue` renders `failure_reason or blocked_reason`, so writing only the
second would leave the page showing the old sentence beside a button that had just refused
for a different one. `poll._settle` writes the pair for the same reason; these two are the
only writers of an eligibility verdict and must not disagree.

## Refusal messages

Check 5, unreachable source (`BoundaryError`):

```
refusing to retry item 17: could not read owner/repo#42: <the transport error>
```

Check 5, absent or invisible issue (`get_issue` returned `None`):

```
refusing to retry item 17: owner/repo#42 does not exist, or this token cannot see it
```

The wording of both matches `robot-army prompt`, which faces the same two ambiguities on
the same call and already settled how to say them.

Check 6, any failing eligibility condition, the author one included:

```
refusing to retry item 17: the issue is not eligible.
  issue author 'mallory' is not the configured author 'jantman' (FR-007 security
  boundary; this cannot be disabled)
```

The second line is `Eligibility.reason` verbatim, unedited. It is written once, in
`poll.evaluate`, and both the poller and the retry quote it — so the sentence a maintainer
reads on the queue page and the sentence they read from the retry are the same sentence
([R1](../research.md)).

## Result payload

Success is unchanged: `item <id> is ready again`, exit `0`, `data={"item_id": …}`.

A refusal at check 6 carries the machine-readable verdict alongside the text:

```json
{"item_id": 17, "eligible": false, "reason": "issue author 'mallory' is not …"}
```

A refusal at check 5 carries the cause instead:

```json
{"item_id": 17, "cause": "issue_unreachable" | "issue_absent", "error": "…"}
```

Check 4's payload — `{"item_id": …, "blocked": "…"}` — is unchanged.

## HTTP status

`_status_for` maps every non-usage exit code to `409`, so both new refusals render as `409`
with the reason in the body, exactly as check 4 does today. Nothing in the web layer needs
to learn about them.

## Interface text

The web `ActionSpec` description and the CLI subparser's `description` — what
`robot-army retry --help` prints — both become:

> Move a failed item back to the queue. The issue is re-read from GitHub and its
> eligibility re-checked — author included — along with the repository's own conditions.
> Refused, with the reason, if any of them still blocks it.

The clause about the author is not decoration. It is the sentence whose absence made the
old text false in the one place it mattered ([R9](../research.md)), and naming the re-read
is what warns the maintainer that this operation now depends on the network.

The one-line `help` in `robot-army --help`'s subcommand listing stays terse — "re-read the
issue, re-check eligibility, and move a failed item back to ready" — because that listing is
scanned, not read. It still names the re-read, which is the part a maintainer needs before
they decide to look further.

## What does not change

- Which item states offer `retry` — still `failed` only.
- The confirmation requirement on the web control.
- The `retry` entry in `legal_actions`, and therefore which rows show the button. An
  author-rejected item still shows `retry`; pressing it now refuses, which is the honest
  behaviour. Hiding the button would leave the operator with a blocked item, a reason they
  cannot act on, and no way to discover that the block had since cleared.
