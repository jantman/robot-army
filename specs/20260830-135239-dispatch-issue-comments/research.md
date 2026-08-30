# Phase 0 Research: Say on the issue which machine and which session picked it up

Every finding below was read out of this checkout rather than remembered. File and line
references are to the tree at `issues/38`.

---

## R1 — What exists today

`_comment_dispatch` (`src/robot_army/dispatch.py:1158`) and `_comment_failure`
(`:1176`) already exist, both writing through `_safe_comment` (`:1183`). The dispatch
comment is posted from exactly one place, `src/robot_army/dispatch.py:1032`, which is the last
statement of `_dispatch_item` before `return True` — i.e. **after** confirmation, the board
update and the `dispatch.confirmed` record.

Today's body:

```text
🤖 robot-army dispatched a session for this issue.

- Branch: `robot-army/issue-38-…`
- Worktree: `/home/jantman/worktrees/robot-army/issue-38`
- Session: `<uuid>`
```

**Decision**: this feature extends the two existing comment bodies and adds a third variant. It
introduces no new call site, no new boundary, and no new posting moment.

**Rationale**: FR-006 ("nothing claims a session before it is confirmed") is already a property
of *where* the call sits. Moving it would put that property at risk for no gain.

**Alternatives rejected**: a separate "announcer" module or a notification-style event type —
one more indirection for one string, against Principle I.

**Consequence for the spec**: FR-006 needs no code. It needs a test that pins the call site's
position, which `test_a_confirmed_dispatch_reaches_active` and the unconfirmed-launch tests in
`tests/integration/test_dispatch.py` already half-cover.

---

## R2 — Where the host name comes from

`os.uname().nodename` is already the project's answer, in two places:
`src/robot_army/health.py:187` (the health webhook body) and
`src/robot_army/boundaries/notifier.py:41` (every notification body). Neither has a fallback for
an empty `nodename`.

**Decision**: add one module-level function `host_name()` to `dispatch.py`, returning
`os.uname().nodename` or the literal `"unknown"` when that is empty or the call raises.

**Rationale**: FR-009 requires the fallback, and only this feature requires it. A function
rather than an inline expression because the "unknown" branch has to be reachable from a test,
and `monkeypatch.setattr(os, "uname", …)` against a named function is the cheapest way to reach
it.

**Alternatives rejected**:

| Tempting | Why rejected |
|---|---|
| A shared `hostname()` helper in a new module, with `health.py` and `notifier.py` refactored onto it | Three call sites, two of which have no requirement to change, and their payloads are wire formats consumed by whatever receives the webhook. Rewriting them is unrelated risk inside an issue that asked for a comment (Principle I). |
| `socket.getfqdn()` | A network-dependent lookup for a fact the kernel already holds; it can block, and Principle IV forbids unbounded blocking. It also disagrees with the two host values the project already publishes, and FR-002 wants agreement. |
| Recording the host in a new `sessions.host` column | A migration and a schema-version bump to store a constant that is the same for every row a given machine writes. See R5 for what is done instead. |

---

## R3 — How a reassignment is recognised, and what it supersedes

The `sessions` table already carries `attempt INTEGER NOT NULL`
(`src/robot_army/migrations.py:64`), assigned from `db.next_attempt`
(`src/robot_army/db.py:417`, `MAX(attempt) + 1`) at `src/robot_army/dispatch.py:799` — inside
the transaction that writes the session row, **before** the launch. So at the comment's call
site `attempt` is an ordinary local variable.

**Decision**: `attempt == 1` is a first dispatch; `attempt > 1` is a reassignment. No new query
answers "is this a reassignment?".

**Decision**: the superseded session is identified in this order:

1. `resume_session_id` when one was passed — that is the session whose context was restored, and
   it is the fact FR-003 asks for. `operations.resume` supplies it from
   `db.latest_session_for_item`.
2. Otherwise (a restart: `operations.restart` passes no `resume_session_id`) the highest-attempt
   session row below ours, via a new `db.previous_session_for_item(conn, item_id, attempt)`.
3. Otherwise nothing — the comment names no predecessor (FR-010).

**The trap this avoids**: `db.latest_session_for_item(conn, item_id)`
(`src/robot_army/db.py:381`) orders by `attempt DESC` and would return **our own row**, because
the session row is inserted long before the comment is written. Calling it here would produce a
comment saying a session supersedes itself. The new helper takes `attempt` and asks for
`attempt < ?` for exactly this reason.

**Alternatives rejected**: filtering `db.list_sessions_for_item` in Python. It works, but every
SQL statement in this codebase lives in `db.py`, and one four-line function there is cheaper
than the first exception to that rule.

---

## R4 — Whether the comment can lie, and what stops it

Three ways it could, and what already prevents each:

| Lie | Prevented by |
|---|---|
| "A session is running" when none is | The call site (R1): confirmation has already returned an entry carrying our session id. |
| A real comment on a rehearsal run | `effects.REAL_AT["issue_writer"] == {LIVE}` (`src/robot_army/effects.py:114`). Below `live` the wiring hands dispatch a `SimulatedIssueWriter` (`src/robot_army/boundaries/github.py:417`) which writes an audit record marked `simulated` carrying the **whole body** and returns a structurally valid fake URL. |
| A comment failure quietly turning a live session into a failed item | `_safe_comment` (`:1183`) catches, logs via `audit.error`, and does not propagate. |

An item's `dry_run` flag and the boundary's simulation cannot disagree: `dry_run` is set from
`self.effect_level.is_simulated` at `src/robot_army/daemon.py:373` and friends, i.e. from the
same level that chose the boundary.

**Decision**: nothing changes here. FR-007 and FR-008 are existing properties; this feature owes
them tests, not code.

---

## R5 — Making the log agree with the comment (FR-002)

The comment will publish three values that the local record does not currently hold in one
place:

* **session id** — recorded (`sessions.session_id`, and `dispatch.confirmed`'s detail).
* **session name** — `prompt.session_name(repo_key, issue_number)`
  (`src/robot_army/prompt.py:187`, `ra-<repo>-<number>`). Deterministic, but written nowhere. It
  reaches the comment as `plan.title`, which is the same string the launch used, not a
  re-derivation.
* **host** — nowhere at all. The log file is on the host, but no record says so.

**Decision**: add `host`, `session_name` and `attempt` to the `dispatch.confirmed` audit record's
detail, and add `supersedes` alongside the existing `resumed_from`.

**Rationale**: Principle III's standard is reconstruction *from the log alone*. Today the log
cannot answer "which machine" without knowing which machine's log you are reading, which is
precisely the correlation problem the issue is about. Four fields on one record per dispatch is
the cheapest way to make the log stand on its own, and it is what makes FR-002 checkable rather
than aspirational.

**Alternatives rejected**: a `sessions.host` column (R2's table — a migration for a per-machine
constant); a second audit record just for the comment (`github.comment` already writes an
intent/outcome pair at `src/robot_army/boundaries/github.py:357`).

---

## R6 — Shape of the body

**Decision**: short labelled lines, one fact per line, as the existing comment already does.
Values in backticks so a session id or a branch can be double-clicked and copied. Exact
templates for all three variants: [contracts/issue-comment.md](./contracts/issue-comment.md).

**Decision**: the body is built by two **pure** functions in `dispatch.py` —
`dispatch_comment_body(...)` and `failure_comment_body(...)` — which take facts and return a
string. `_comment_dispatch` / `_comment_failure` keep the I/O.

**Rationale**: the interesting rules (three variants, the predecessor's three cases, the unknown
host) are all rules about a string. Pure functions make them unit-testable without a worktree, a
stub host, git, or the `requires_git` marker — the integration module currently takes seconds per
case.

**Alternatives rejected**: a template file or a `DispatchFacts` dataclass. One caller each; a
dataclass whose only job is to be unpacked immediately is the speculative generality Principle I
names.

---

## R7 — Where the tests go

`tests/integration/test_dispatch.py` already exercises this path and already asserts
`writer.comments` is non-empty (line 81), covers a comment failure
(`test_a_comment_failure_does_not_change_the_items_state`, line 470), and covers a resume as a
new attempt (`test_a_resume_is_a_new_attempt_naming_what_it_restored`, line 750).
`tests/conftest.py`'s `RecordingWriter` (line 331) captures every `(repo_key, number, body)`.

**Decision**: the body rules get a new unit module `tests/unit/test_issue_comments.py`; the
wiring — that a restart names its predecessor, that a resume names the restored session, that a
simulated run posts nothing real — gets cases added to `tests/integration/test_dispatch.py`,
beside the ones already there.

**Alternatives rejected**: a whole new integration module. The fixtures, the git marker and the
`trust_file`/`ready_item` helpers are all in the existing one.

---

## R8 — Documentation (FR-012)

`README.md` documents what a session is *told* ("What every session is told", line 306) and what
the maintainer is *told* ("Being told when something happens", line 465). Nothing documents what
is written back onto the issue. `docs/logging.md` uses `github.comment` as its worked example of
an intent/outcome pair (line 139) but never says what those comments contain.

**Decision**: a new `## What it writes on the issue` section in `README.md`, placed immediately
before "Being told when something happens" — the two are the same subject seen from different
ends. One paragraph added to `docs/logging.md` saying that `dispatch.confirmed` now carries the
same host, session name and attempt the comment publishes, so the log and the issue can be
matched.

**Alternatives rejected**: a new document under `docs/`. Three paragraphs do not need a file, and
a fact filed away from the two documents a reader already opens is a fact nobody finds.
