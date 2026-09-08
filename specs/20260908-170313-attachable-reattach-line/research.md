# Phase 0 Research: A Reattach Line Only Where Reattaching Would Reach That Session

**Feature**: [spec.md](./spec.md) · **Issue**: jantman/robot-army#51 · **Tree**: `main` at 047f8cd

Everything below was measured in this checkout, with throwaway modules under `tests/unit/`
deleted after the run. Output is quoted verbatim; the ids and paths in it are synthetic or
this machine's own runtime directory.

## R1 — The line is printed in every session state, without exception

`operations.py:1040` guards on one thing:

```python
if session.host_socket:
    result.say(f"       reattach: dtach -a {session.host_socket}")
```

Seeding one item and one session, once per state, and rendering `show`:

```
R1 state='starting'     -> ['       reattach: dtach -a /run/user/1000/robot-army/12.sock']
R1 state='running'      -> ['       reattach: dtach -a /run/user/1000/robot-army/12.sock']
R1 state='exited_clean' -> ['       reattach: dtach -a /run/user/1000/robot-army/12.sock']
R1 state='exited_error' -> ['       reattach: dtach -a /run/user/1000/robot-army/12.sock']
R1 state='lost'         -> ['       reattach: dtach -a /run/user/1000/robot-army/12.sock']
```

**Decision**: the reported defect reproduces exactly as filed, in all three terminal states
rather than only in `lost`.

## R2 — A socket is named after the item, so an ended attempt points at the next one's session

`Layout.socket_for` (`paths.py:150`) is `socket_dir / f"{item_id}.sock"`. There is no session
component. Two attempts on one item therefore record byte-identical paths, which the render
confirms:

```
R2 socket_for(12) = /run/user/1000/robot-army/12.sock
R2 output:
sessions (2 attempt(s)):
  #1 lost id=sess-1-1 pid=4321 exit=—
       started 2026-09-08 17:06:13 -04:00 ended —
       reattach: dtach -a /run/user/1000/robot-army/1.sock
  #2 running id=sess-1-2 pid=4321 exit=—
       started 2026-09-08 17:06:13 -04:00 ended —
       reattach: dtach -a /run/user/1000/robot-army/1.sock
```

(The probe printed `socket_for` for the issue's item 12 and then rendered a seeded item 1,
which is why two numbers appear.)

**Decision**: this is the finding that shapes the whole design. The issue describes a command
that *cannot work*; the more damaging case is the one where it **can** — the maintainer types
the line under the row marked `lost`, and lands in attempt #2's live session. So the guard
cannot be "does this path answer". It has to be "could this row be what is answering", and
only then "is anything answering".

**Alternative considered and rejected**: making sockets per session, so a path identifies the
attempt that recorded it. That is a change to the dispatch path, the reboot story, and the
socket-pruning story, to fix an output line. Out of proportion, and out of scope.

## R3 — What decides "could this row be what is answering"

`states.TERMINAL_SESSION_STATES` is `{exited_clean, exited_error, lost}` and already exists. A
row in one of those states is not what is listening on a shared path, whatever is: nothing
reopens a session row — `SESSION_TRANSITIONS` has no edge out of a terminal state.

**Alternative considered**: `procinfo.is_alive(session.pid, session.proc_start)`, the identity
check `_describe_live_session` (`operations.py:2412`) uses for the same family of question. It
is strictly more precise — it identifies the row's own process — but it carries a documented
degradation when `proc_start` is missing, which is a legitimate row shape, and that middle
"cannot identify it" case would need its own wording. Two guards where three would do the same
work: the state check is free, exact for the attribution question, and already the rule the
web uses to decide whether to offer its attach button (`pages.py:461`, `session_states=(RUNNING,)`).

**Decision**: session state answers attribution; the socket probe answers reachability. Both,
in that order, and the state check first so a finished attempt costs nothing.

## R4 — The reachability check already exists, at the boundary, and is the one `attach` uses

`SessionHost.is_alive(handle)` — `boundaries/dtach.py:144`. It probes rather than trusting the
file's existence, which is exactly the distinction this feature needs, and its docstring
records why. Measured here:

```
R5 absent socket    -> False in 0.017 ms
R5 plain file       -> False in 0.051 ms
R5 live socket      -> True  in 0.022 ms
R5 abandoned socket -> False in 0.018 ms
```

The ~7 ms in the docstring is the pessimistic M0 figure; on this machine a refused connect is
tens of microseconds. Either way it is far below the cost of the render around it.

**Decision**: call `ctx.boundaries.session_host.is_alive`. Nothing is re-implemented, and what
`show` reports is the same question `attach` will ask when the maintainer acts on it.

**On the simulated host**: `SimulatedSessionHost.is_alive` reads an in-process set populated by
`spawn`, so in a short-lived `show` it is always `False`. That is not a reason to route to it —
`operations.py:2733` records that picking an implementation from stored session state happens in
exactly one place in this system, and a test holds it there. The real host asked about a
rehearsal's socket answers `False` because nothing is listening, which is the true answer to the
question actually being asked.

## R5 — Introducing this call introduces a new way for `show` to fail

`is_alive` raises `BoundaryError` on a probe timeout and on any other `OSError`. `show` calls
nothing that raises today, so an uncaught one would turn a read-only inspection command into a
traceback — and it would do so precisely when the machine is already misbehaving.

Reaching it is hard, which is worth recording so the branch is not mistaken for theatre:

```
R6 overlong path (>sun_path) exists -> False   # never reaches connect()
R6 socket in an untraversable dir   -> False   # Path.exists() swallows the OSError
R6 path that is a directory         -> False   # ECONNREFUSED
```

`Path.exists()` absorbs the permission cases and `connect()` refuses the malformed ones, so in
practice only a probe that hangs past `PROBE_TIMEOUT` (3.0 s, already set at the boundary)
raises. The branch exists for the day it does.

**Decision**: catch `BoundaryError` around the probe, print the command with the reason
appended, and leave the exit code alone. Not caught: anything else. A `TypeError` in this file
is a bug and should still crash.

## R6 — Nothing in the suite covers this line today

`grep -rn "reattach:" tests/` returns nothing. The two tests naming a reattach line
(`test_worktree_remove_guard.py:338,357`) cover the refusal message in `worktree remove`, which
is a different site with a different guard.

**Consequence**: the change cannot break an existing assertion, and every outcome it introduces
needs a test written for it. That is where the task list's weight goes.

## R7 — The `--json` payload and the web are unaffected

`show`'s `data["sessions"]` is `[_session_dict(s) for s in attempts]`, and `web/pages.py:1743`
renders the item view from that payload rather than from the lines. `_session_dict` is also
called directly at `pages.py:634`. Measured keys:

```
R3 session keys = ['attempt', 'confirmed_at', 'dry_run', 'ended_at', 'exit_code',
                   'host_socket', 'pid', 'scope', 'session_id', 'signal', 'started_at',
                   'state', 'window_id']
```

**Decision**: change no key. `host_socket` is a record of what was configured, not an offer to
act on it, so it carries none of the implication being removed. Adding an `attachable` key
would put a probe inside a helper two callers share, one of which renders a web page per
request, to serve a consumer that has not asked — and a JSON consumer that wants the answer can
read `state`, which is in the payload already.

**Alternative considered and rejected**: annotating the payload only in `show`, leaving
`pages.py:634` unannotated. Two shapes from one helper, differing by caller, is the kind of
divergence that is discovered much later and by accident.

## R8 — Where this is documented

`docs/guide/4-session.md` has "## Attaching to a running session" (line 273), which is the page
the CLAUDE.md table names for attach. `docs/guide/operating.md:400` prints the same `dtach -a`
line under recovery. The rule belongs on 4-session.md beside the command; operating.md's mention
is a bare "here is how to attach by hand" and says nothing this change contradicts.

**Decision**: one paragraph on `4-session.md`. `operating.md` unchanged, deliberately — a rule
stated twice drifts, and this is the page the table points at.
