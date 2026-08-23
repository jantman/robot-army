# Contract: Session Exit Record

The one contract that crosses a process boundary, between the session wrapper (bash, running in a
bare launch environment) and the daemon (Python). Per [R5](../research.md#r5--session-exit-reporting-an-atomic-spool-directory-not-an-http-post),
this is a **spool file, not an HTTP POST** — a POST to a daemon that is down loses the record
permanently, and the daemon is legitimately down during restarts and upgrades.

## Transport

The wrapper writes to `~/.local/state/robot-army/spool/exits/` (honours `XDG_STATE_HOME`), using the
atomic pattern the constitution's Interruption Tolerance principle prescribes:

1. Write the complete JSON document to `<session-id>.<event>.json.tmp`
2. `fsync` it
3. `rename` to `<session-id>.<event>.json`

`rename` within a directory is atomic on Linux, so the daemon never observes a partial record. One
file per event; the wrapper never appends to a shared file, which avoids interleaving between
concurrent sessions.

The daemon drains the directory at the top of every tick: read, apply in a transaction, and unlink
**only after commit**. A crash between apply and unlink causes reapplication, so application is
idempotent on `(session_id, event)`.

## Events

### `start`

Written immediately before the worker is executed.

```json
{
  "schema": 1,
  "event": "start",
  "item": "42",
  "session_id": "3f2a1b8c-...-9d4e",
  "ts": "2026-08-23T14:07:11Z",
  "pid": 1996056,
  "ppid": 1996044,
  "cwd": "/home/jantman/GIT-worktrees/privatepuppet/issue-142",
  "argv": ["claude", "--session-id", "3f2a...", "--permission-mode", "auto", "..."]
}
```

The `pid` here is the **wrapper's**, not the worker's. It is useful for diagnosis but is never used
as session identity — that comes from the registry join on `session_id` (R8).

### `exit`

Written after the worker exits, before the wrapper itself exits.

```json
{
  "schema": 1,
  "event": "exit",
  "item": "42",
  "session_id": "3f2a1b8c-...-9d4e",
  "ts": "2026-08-23T16:31:02Z",
  "started": "2026-08-23T14:07:11Z",
  "ended": "2026-08-23T16:31:02Z",
  "exit": 0,
  "signal": null
}
```

## Field rules

| Field | Type | Rules |
|---|---|---|
| `schema` | integer | Currently `1`. The daemon rejects unknown versions into an anomaly rather than guessing |
| `event` | string | `start` or `exit` |
| `item` | string | Work item id as a string |
| `session_id` | string | The UUID the daemon generated and passed via `--session-id`. **The join key** |
| `ts` | string | UTC ISO 8601, `Z` suffix |
| `exit` | integer | Raw exit status as the shell reported it |
| `signal` | integer or null | Set to `N` when `exit` is in `[129, 191]`, i.e. `128+N`. Otherwise null |

**Why `signal` is a separate field rather than derived.** FR-032 requires distinguishing "crashed"
from "a human killed it", and both arrive as a single number from the shell. Recording the decoded
signal at the point where the information is unambiguous means the daemon never has to guess whether
`137` meant SIGKILL or a program that genuinely returned 137.

## Daemon-side interpretation

Per [data-model.md](../data-model.md), exit code maps to state:

| `exit` | Session state | Work item state |
|---|---|---|
| `0` | `exited_clean` | `awaiting_review` |
| `1`, `126`, `127` | `exited_error` | `failed` |
| `128+N` | `exited_error`, `signal` set | `interrupted` |
| other non-zero | `exited_error` | `failed` |

## Malformed records

Parsing external input, so the constitution requires failure-path tests. Behaviour:

| Condition | Behaviour |
|---|---|
| Unparseable JSON | Move to `spool/exits/rejected/`, raise an anomaly, never silently delete |
| Unknown `schema` | Same |
| `session_id` matches no row | Anomaly `orphan_exit_record`; keep the file. This is evidence of a session the daemon lost track of, and discarding it would destroy the evidence |
| `exit` record with no prior `start` | Apply it anyway; a missing `start` is itself worth an audit line but the outcome is the valuable part |
| Applied twice | Second application is a no-op by design |

## Wrapper requirements

The wrapper is bash, seeded from `docs/initial-planning/spike/ra-session-wrapper.sh`, installed as
`robot-army-session-wrapper`. Two properties are load-bearing and must survive future editing:

1. **It must not `exec` the worker.** `exec` replaces the shell, and the exit code could then never
   be captured — which is the wrapper's entire reason to exist. The spike script carries this as a
   comment; keep it.
2. **It must run with no virtualenv and a minimal PATH**, because it runs in whatever environment
   the terminal daemon happens to provide (M0 F19). It may use only `bash`, `printf`, `date`, `mv`,
   and `mkdir`. It must not require `jq`, `curl`, or Python.
