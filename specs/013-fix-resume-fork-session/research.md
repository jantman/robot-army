# Phase 0 Research: `resume` That Actually Resumes

**Feature**: [spec.md](./spec.md) | **Date**: 2026-08-30

Every finding below was measured on this machine against the installed worker binary or read
out of this repository's own source. Nothing here is inferred from documentation alone — the
defect this feature fixes was itself caused by trusting an intent that was never executed.

---

## R1 — The accepted flag combination

**Decision**: append `--fork-session` to the worker argv whenever `--resume` is present.

**Rationale**: measured directly. The combination the code composes today is rejected before
anything runs:

```
$ claude --session-id 1111…-1111 --resume 2222…-2222 -p 'hi'
Error: --session-id can only be used with --continue or --resume if --fork-session is also specified.
```

Adding the flag the error names removes the rejection. Argument validation now passes and the
binary proceeds to the next real question — whether the conversation exists:

```
$ claude --session-id 1111…-1111 --resume 2222…-2222 --fork-session -p 'hi'
No conversation found with session ID: 2222…-2222        → exit 1
```

`claude --help` describes the flag as "When resuming, create a new session ID (only works with
`--resume` or `--continue`)", which is word for word what `dispatch.py:478`'s existing comment
already claims the launch is doing: "A resume is a *new attempt* restoring the prior session's
context". The intent was correct and complete; only the token was missing.

**Alternatives considered**:

- *Drop `--session-id` on a restoring launch and let the worker pick its own id.* Rejected: the
  chosen id is how confirmation, attach, terminate, and exit correlation all address the
  session. Surrendering it would trade a one-token fix for an unsolved identity problem, and
  would make the FR-065 mismatch detector fire on every resume.
- *Use `--continue` instead of `--resume`.* Rejected: `--continue` is scoped to "the most recent
  conversation in this directory", which is a different and weaker guarantee than naming the
  session we recorded. We know exactly which session we mean.

---

## R2 — Does the forked session honour the id we ask for?

**Decision**: yes. FR-003 is satisfied by R1 alone; no additional identity handling is needed.

**Rationale**: measured end to end, because "creates a new session ID" in the help text could
plausibly have meant the binary *generates* one and ignores ours — which would have broken
tracking silently, the same class of defect all over again.

A session was created under a known id `aaaa…0001`, replying with a marker string. It was then
resumed with a different requested id and `--fork-session`:

```
$ claude --session-id bbbb…0002 --resume aaaa…0001 --fork-session -p 'What marker did you reply with before?'
MARKER-ONE                                                → exit 0
```

Three things were confirmed afterwards:

| Question | Finding |
|---|---|
| Was prior context restored? | Yes — the marker exists only in the first session's transcript. |
| Did it run under the id we asked for? | Yes — `bbbb…0002.jsonl` was created; every record inside carries `sessionId: bbbb…0002`. |
| Was the original preserved? | Yes — `aaaa…0001.jsonl` still exists, 12 lines; the fork is 20, its history copied forward. |

So a resume is a genuinely new, separately trackable attempt that carries the old conversation.
That is exactly the model `sessions.attempt` and FR-002 already assume.

**Alternatives considered**: none. The measurement answered the question.

---

## R3 — Why the failure wedges the item

**Decision**: the confirmation-elapsed branch must consult the session's own recorded state
before declaring it lost, and must use a recorded exit as the outcome when one is present.

**Rationale**: reading the two paths shows the ownership split is already correct and only one
branch violates it.

`spool.apply_record` (`spool.py:219-233`) applies an exit record to the *session* row, and then
touches the work item **only if the item is `active`**. During a launch the item is
`dispatching`, so the daemon deliberately leaves it alone: dispatch is still in flight and owns
settling it. That is right.

`dispatch.dispatch_item` then reaches `entry is None` (`dispatch.py:775-785`) and transitions the
session to `LOST` — assuming the session is still pre-exit. By then it is not:

```
STARTING → RUNNING → EXITED_ERROR      (daemon, from the exit record)
EXITED_ERROR → LOST                    (dispatch, ~45s later)  → IllegalTransition
```

`SESSION_TRANSITIONS` has no edge out of a terminal state, by design. The exception escapes
`dispatch_item`, `_fail` is never reached, and the item is left in `dispatching` — the observed
`web.resume.result [error] … IllegalTransition` line. The 15-minute `dispatching_max_age_seconds`
reaper is then the only thing that resolves a failure detected in under three seconds.

Two processes are involved — the daemon drains the spool, the web worker thread runs
`operations.resume` — so this is a real cross-process race, not a logic slip in one flow.

**The pattern to copy already exists in this repository.** `reconcile.py:157-159` faced the same
question and answered it correctly:

```python
# Not alive. Before concluding, check whether the session's own row already knows
# it exited — a spool record applied earlier in this same tick, say.
if session.state in (SessionState.EXITED_CLEAN, SessionState.EXITED_ERROR):
    continue
```

The fix is to apply that same guard at the second site. This is not a new idea being introduced;
it is an existing, proven one being applied where it was missed.

**Alternatives considered**:

- *Add `EXITED_* → LOST` to the legal transition table.* Rejected outright. It would make the
  contradiction legal instead of resolving it, overwrite a known exit status with "lost", and
  destroy the most useful fact in the record. The gate is behaving correctly here.
- *Catch `IllegalTransition` at the call site and carry on.* Rejected: Principle III forbids
  swallowing, and it treats a symptom. The session state is knowable; ask.
- *Have `confirm_session` also watch the database so a dead session is noticed in one second
  instead of forty-five.* Rejected under Principle I. It would push database knowledge across
  the host boundary that `effects.py` exists to keep clean, to save 44 seconds on a failure path.
  45 seconds against the current 900 already satisfies SC-002.

---

## R4 — When the exit record has not drained yet

**Decision**: accept it. If the session row is not yet terminal, `LOST` stands, and the exit
record that arrives afterwards is recorded as a duplicate.

**Rationale**: `spool._already_applied` treats an exit as applied once the session state is
terminal, and `LOST` is terminal (`states.py:88-90`). So a late record is a clean no-op, not a
crash or a second write. The item still fails; only the failure *reason* differs — "was not
confirmed" rather than "exited 1". Both are true and both are logged. Closing this last sliver
would mean reading the spool directory from inside dispatch, which is the daemon's job.

The daemon's tick is far shorter than the 45-second confirmation window, so in practice the
record has drained. This is the documented residual, not a common path.

---

## R5 — Verifying launch shapes against the real binary, cheaply

**Decision**: probe the real binary with the composed argv plus `-p` and empty stdin, and assert
the failure is *not* an argument rejection.

**Rationale**: the check must answer "would the binary accept this combination?" without running
a worker, spending model tokens, or needing a worktree. Measured:

```
$ printf '' | claude -p --session-id … -n probe --permission-mode auto --model sonnet
Error: Input must be provided either through stdin or as a prompt argument when using --print
                                                          → exit 1, 0.9s, no model call
```

Reaching the missing-input complaint proves every other argument was accepted. The two outcomes
are distinguishable, which is what makes the check meaningful rather than decorative:

| Shape | Sentinel that proves argument validation passed |
|---|---|
| Non-restoring | `Input must be provided either through stdin or as a prompt argument` |
| Restoring (unknown resume id) | `No conversation found with session ID:` |
| **Rejected combination** | `Error: --session-id can only be used with …` ← the failure to catch |

Both acceptable outcomes exit 1, so exit status alone cannot discriminate; the check keys on the
sentinel. Wording drift in a future worker release therefore breaks the check loudly, which is
the correct behaviour — it means "re-verify this against the binary", exactly what nobody did
the first time.

**Scope ceiling** (FR-013): the shapes are the ones `build_launch_plan` can actually compose —
6 permission modes × {restoring, not} × {model, no model} = 24 probes at ~0.9s. Roughly 20
seconds, in a test that skips when the binary is absent.

**Alternatives considered**:

- *Run a real worker per shape.* Rejected: needs network, auth, and tokens, and turns a
  20-second check into minutes.
- *Assert argv equals an expected list.* Rejected — that is precisely the test that already
  exists and that let this defect through. The list was "correct" by the code's own definition.
- *A general harness for exercising real workers.* Rejected under Principle I. The value is in
  the fixed, small set of shapes this system composes; anything broader has no second caller.

**Existing convention followed**: `tests/integration/test_spool_recovery.py` skips on
`not WRAPPER.exists()`, and `requires_git` marks tests that shell out to a real binary. The new
check is the same shape with a `requires_worker` marker — no new machinery.

---

## R6 — A resume whose stored context is gone

**Decision**: no special handling. It is the Story 2 path, and the fix already covers it.

**Rationale**: measured in R1 — a resume naming a session the worker no longer holds prints
`No conversation found with session ID: …` and exits 1 within a second. That is precisely the
fast-exit shape that wedges the item today, so it is fixed by the same change and needs no
branch of its own. The failure reason will name the exit status rather than the missing
conversation; the worker's own output is in the session log directory, which the failure reason
can point at.

---

## R7 — Which item states may be resumed

**Finding, not a decision**: `operations.resume` accepts `interrupted` **and**
`awaiting_review`, not `interrupted` alone. `AWAITING_REVIEW → DISPATCHING` is a legal work item
transition.

Recorded here because FR-011 defers to "the explanation it gives today", and a careless reading
of the spec's Story 1 framing could narrow the guard to `interrupted` and quietly break the
review-follow-up path. Nothing about that behaviour changes in this feature.
