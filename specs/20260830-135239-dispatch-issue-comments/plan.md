# Implementation Plan: Say on the issue which machine and which session picked it up

**Branch**: `issues/38` | **Date**: 2026-08-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/20260830-135239-dispatch-issue-comments/spec.md`

## Summary

The dispatch comment already exists and is already posted from the one place it should be. It is
missing three facts and one variant.

1. **Two pure body builders in `dispatch.py`.** `dispatch_comment_body(...)` and
   `failure_comment_body(...)` take facts and return a string. Every rule this feature is about —
   the three variants, the three ways a predecessor can be identified, the unknown host — is a
   rule about a string, so it is tested as one, without git, a worktree or a stub host.

2. **Three more facts at the existing call site.** The host (new `host_name()`), the session name
   (`plan.title`, the same string the launch used), and the attempt number, which is already a
   local variable at that point. `_comment_failure` gains the host line and nothing else.

3. **One new read, `db.previous_session_for_item(conn, item_id, attempt)`.** A restart carries no
   `resume_session_id`, so the session it supersedes has to be looked up — and the existing
   `db.latest_session_for_item` would return *our own row*, because the session is inserted
   before it is launched. The `attempt < ?` bound is the whole point of the new function.

4. **The same facts on `dispatch.confirmed`.** `host`, `session_name`, `attempt` and `supersedes`
   join `resumed_from` in that record's detail, so the log can answer "which machine" without the
   reader already knowing whose log they are reading — which is the correlation problem the issue
   opens with.

**No schema change, no migration, no new dependency, no new configuration knob, no new command,
no new boundary, no new audit action, and no change to either state machine.** The posting moment
does not move: it stays after confirmation, which is what keeps FR-006 structural.

Everything above was measured against this checkout; see [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`).

**Primary Dependencies**: none added. `httpx` remains the only runtime dependency and is reached
only through the existing `GitHubWriter.comment`.

**Storage**: SQLite (`work_items`, `sessions`) and the JSONL audit log — **both unchanged**.
`SCHEMA_VERSION` does not move; every column read already exists. See
[data-model.md](./data-model.md).

**Testing**: pytest. A new unit module `tests/unit/test_issue_comments.py` for the body rules —
no `requires_git` marker, no worktree, no network. Cases added to the existing
`tests/integration/test_dispatch.py` for the wiring, reusing its `ready_item` / `trust_file`
helpers and `conftest`'s `RecordingWriter`, `StubSessionHost` and `StubDisplay`.

**Target Platform**: a single Linux machine with a shell — and, increasingly, more than one of
them, which is the entire reason this feature exists.

**Project Type**: single Python package (`src/robot_army`) — CLI plus daemon plus a small web
interface.

**Performance Goals**: none. One `os.uname()` and at most one indexed single-row query per
dispatch, against a path that already spends up to `confirm_timeout_seconds` waiting.

**Constraints**: the comment must never be able to fail a session (`_safe_comment`, unchanged),
must never reach GitHub below `live` (the effects wiring, unchanged), and must never be written
before confirmation (the call site, unchanged). This feature is obliged to leave all three
properties exactly where they are rather than re-implement them.

**Scale/Scope**: three source files touched (`dispatch.py`, `db.py`, and documentation), roughly
seventy lines of production code, one new test module, cases added to one existing test module.

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 — see below.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | PASS. Two pure functions, one four-line query, three extra fields on one log record. No new module, no dataclass, no template engine, no configuration knob. The five tempting elaborations are named and rejected below. |
| **II. Single-User, Local-First** | PASS. No new state, no new service. The one outward call is a comment on the maintainer's own issue, through the boundary that already makes it. The host name is read from the kernel, not resolved over the network. |
| **III. Total Accountability** | PASS, and the record is **improved**: `dispatch.confirmed` gains the host and session name, which is what lets the log stand alone on a machine you are not sitting at. Nothing is swallowed — `_safe_comment` logs every failure it declines to propagate, and that is a documented exception under Principle III's exception clause, restated below. |
| **IV. Interruption Tolerance** | PASS. Nothing here writes persistent state, opens a transaction, or retries. See "What happens if it is killed halfway" below. |
| **V. Public Code, Unsupported** | PASS with one thing stated rather than assumed: these comments are **public**. They publish this machine's name and an absolute path under `$HOME` onto a world-readable issue. See "What this publishes" below. |

### What this logs

| Action | When | Detail |
|---|---|---|
| `dispatch.confirmed` | Unchanged moment | **Gains** `host`, `session_name`, `attempt`, and `supersedes` when one was found without a resume. `resumed_from` keeps its meaning. |
| `github.comment` | Every comment attempt | Unchanged. Real writer: intent → outcome pair with the comment URL. Simulated writer: one `simulated` record carrying the **whole body**, which is what makes step 2 of [quickstart.md](./quickstart.md) a real verification rather than a gesture. |
| `github.comment` (error) | A post that failed | Unchanged. Written by `_safe_comment` with the exception and a note that the item's state is unaffected. |

**The documented Principle III exception**: a failed comment is logged and then *not* propagated.
That is a swallowed exception by the letter of the principle, and it is deliberate — GitHub's
availability and a running session's fate are unrelated facts, and letting the first decide the
second would fail live sessions for a cosmetic reason. It is already the shipped behaviour; this
plan restates it rather than introducing it, and it stays logged, which is the condition the
principle attaches.

### What happens if it is killed halfway

Nothing to recover. The comment is the last statement of a dispatch that has already been
confirmed, recorded and committed:

* **Killed before the comment** — the session is running and recorded; the issue simply has no
  comment. There is no retry and no repair pass, deliberately: a missing comment costs one
  `robot-army show`, and a retry ledger for it would be more machinery than the fact is worth.
  This is stated so the gap is documented rather than discovered.
* **Killed mid-POST** — GitHub either accepted the comment or did not; the `github.comment`
  intent with no outcome is exactly the crash signature `docs/logging.md` already documents. At
  worst the issue carries one comment nobody wrote a matching outcome for.
* **Killed after the comment** — nothing follows it but `return True`.

No transaction is opened, no file is written, no state column is touched. The feature is
read-only with respect to persistent state.

### What this publishes

The comment names the host and an absolute worktree path on a public issue. Two things make that
acceptable rather than an oversight:

* The existing comment **already publishes the worktree path**, so `$HOME` and the maintainer's
  username are already on these issues. This adds the machine's short name beside it.
* Principle V's rule is about *committed content*, and it is aimed at credentials, personal data
  and private network addresses. A personal machine's short name is none of those, and the whole
  value of the feature is that the name is readable by the person reading the issue.

It is named here so the decision is on the record. Nothing else new is published: no token, no
environment, no prompt body, no issue content that was not already there.

### Rejected elaborations (Principle I)

| Tempting | Why it was rejected |
|---|---|
| A `sessions.host` column | A migration and a schema-version bump to store a value that is identical for every row a given machine writes. The log detail carries it for free (R2, R5). |
| A shared `hostname()` helper, refactoring `health.py:187` and `notifier.py:41` onto it | Two call sites with no requirement to change, whose payloads are wire formats. Unrelated risk inside an issue that asked for a comment (R2). |
| `socket.getfqdn()` instead of `os.uname().nodename` | A blocking network lookup for a fact the kernel holds, and it would disagree with the two host values the project already publishes (R2). |
| Editing the first comment instead of adding one per attempt | FR-004 wants the history. An edited comment loses the ordering the issue exists to preserve, and an edit can fail halfway in a way an append cannot. |
| A comment when a session *ends*, or when a PR opens | Not asked for, and the branch already ties the PR to the session. Out of scope, and named as such in the spec's assumptions. |
| A `DispatchFacts` dataclass passed to the builders | One caller, unpacked immediately (R6). |

## Project Structure

### Documentation (this feature)

```text
specs/20260830-135239-dispatch-issue-comments/
├── plan.md                      # This file
├── research.md                  # Phase 0 — eight findings, measured against this checkout
├── data-model.md                # Phase 1 — no schema change; where each published fact comes from
├── quickstart.md                # Phase 1 — five verification steps, four of them offline
├── contracts/
│   └── issue-comment.md         # Phase 1 — the three comment variants, exactly
├── checklists/
│   └── requirements.md          # From /speckit-specify
├── spec.md
└── tasks.md                     # Not created by /speckit-plan
```

### Source code

```text
src/robot_army/
├── dispatch.py     # host_name(); dispatch_comment_body(); failure_comment_body();
│                   # _comment_dispatch / _comment_failure become thin; three fields
│                   # added to the dispatch.confirmed detail
└── db.py           # previous_session_for_item(conn, item_id, attempt)

tests/
├── unit/test_issue_comments.py        # NEW — the body rules, no git, no worktree
└── integration/test_dispatch.py       # cases added beside the existing comment tests

README.md           # NEW section "What it writes on the issue", before "Being told when
                    # something happens"
docs/logging.md     # one paragraph: dispatch.confirmed now carries what the comment publishes
```

**Structure Decision**: the existing single-package layout, unchanged. Every edit lands in a file
that already owns the behaviour being changed: the comment bodies where the comments are already
built, the query where every other query lives, the tests beside the ones already asserting on
`writer.comments`.

## Complexity Tracking

No Constitution Check violation to justify. The two judgement calls that could have become
violations — a schema column for the host, and a cross-module hostname refactor — were both
rejected in research and are recorded in the table above.

## Post-Design Constitution Re-check

Re-evaluated after Phase 1, against the artifacts as written:

| Principle | Verdict after design |
|---|---|
| I | PASS. The design got *smaller* during Phase 0, not larger: the dataclass and the shared helper were both cut. What remains is two pure functions, one query, and four log fields. |
| II | PASS. Nothing added is machine-specific configuration, and nothing new leaves the machine except the comment that was already leaving it. |
| III | PASS. Every new fact published is also recorded locally ([contracts/issue-comment.md](./contracts/issue-comment.md) §5), the one un-propagated failure is logged and justified above, and the one accepted gap — no retry for a comment lost to a crash — is stated rather than left to be found. |
| IV | PASS. Read-only with respect to persistent state; no transaction, no retry, no file write. |
| V | PASS, with the publication of the host name named and reasoned about above rather than assumed. |

Ready for `/speckit-tasks`.
