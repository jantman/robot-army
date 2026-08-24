# Contract: Terminal Commands Added and Changed

The Operating Constraints require every capability to be reachable from the terminal, and FR-006
requires every web control to have a terminal equivalent. This milestone adds four verbs and changes
two, extending [001's CLI contract](../../001-minimum-daemon/contracts/cli.md); its universal rules —
exit codes, `--json`, `--include-simulated`, lock behaviour — apply unchanged.

---

## `robot-army serve`

Runs the web interface in the foreground. Independent of the daemon: it starts, stops, and survives
separately, which is what FR-005 requires.

| Option | Default | Meaning |
|---|---|---|
| `--bind ADDR` | `[web] bind`, else `127.0.0.1` | Listening address |
| `--port N` | `[web] port`, else `8420` | Listening port |
| `--config PATH` | `~/.config/robot-army/config.toml` | |

Startup sequence, all before the socket accepts anything:

1. Load and validate config; on failure exit `3` listing **every** problem.
2. Open the database and verify the schema version. The web never migrates (R11); a mismatch exits
   `3` saying which version is on disk and which the code expects.
3. Refuse a globally routable bind address, exit `3` (FR-004).
4. Bind, or exit `3` naming the address and the reason.
5. Write `web.start` to the audit log and print the effective address and port; warn if it is not
   loopback.

Exits `0` on `SIGTERM`/`SIGINT` after in-flight requests finish. Never touches running sessions.

This command takes **no lock**. It reads and writes the same database as the daemon, relying on WAL
mode exactly as `robot-army status` already does.

## `robot-army pause` / `robot-army unpause`

Suspends and resumes dispatch. While paused the daemon still polls, evaluates eligibility, reconciles,
and heartbeats; it starts no new session, and eligible items accumulate in `ready` (FR-033, FR-034).

Durable across daemon restart and reboot (FR-035). Cleared only by `unpause` or by the web control —
never by time, never by a restart.

Both print the resulting state and when it was set. `pause` on an already-paused system is not an
error: it reports the existing pause and its timestamp, and still records the attempt.

Works whether or not the daemon is running: it writes to the database, which the daemon reads before
each dispatch decision. Pausing a stopped daemon is meaningful — it takes effect when it starts.

## `robot-army attach <item-id>`

Opens a terminal tab attached to that item's running session (R10). Refuses, exit `3`, if the item has
no session in `running`. Refuses, exit `1`, if no terminal control socket answers, naming that as the
reason.

Changes no state and consumes no session: reattachment repaints fully and more than one viewer is
allowed, both measured in M0.

`robot-army show <id>` continues to print the attach command for pasting; this verb saves the paste.

---

## Changed: `robot-army poll` and `robot-army reconcile`

**These verbs previously over-promised.** 001's contract says that with a daemon running, `poll`
"signals it to poll on its next tick"; in fact it only printed how often the daemon polls, because
`Daemon.request()` had no caller outside the process. FR-023 needs a real force, so the mechanism now
exists (R5) and the verbs use it.

New behaviour with a daemon running: write the request marker, report that the job was requested and
that the daemon will run it within one tick, exit `0`. Behaviour with no daemon running is unchanged
— the work is done directly and reported in full.

The response is necessarily *"requested"* rather than *"here is what it found"*. The daemon reports
the result into the audit log, which `robot-army log` and the web audit view then show.

---

## Changed: every listing command

The pause state, and when it was set, is shown wherever work items are listed (FR-036) — so `status`
gains a line, and it appears in `--json` output and in `heartbeat.json`. A system that is healthy and
deliberately doing nothing must not read as a system that is healthy and doing nothing for no reason.

---

## Configuration added

```toml
[web]
bind = "127.0.0.1"      # set to the LAN address, or 0.0.0.0, to reach it from the phone
port = 8420
refresh_seconds = 10
```

All three are optional with the defaults shown. `bind` is refused if it parses as a globally routable
address; `0.0.0.0` is permitted with a warning, because it cannot be classified and refusing it would
push toward pinning an address a DHCP lease can change (R13).
