# Contract: `[terminal]` configuration

**Feature**: `specs/20260904-155257-guard-kitty-socket-owner` · **Date**: 2026-09-04

## Keys

```toml
[terminal]
socket_glob           = "/run/user/1000/mykitty-*"  # default: $XDG_RUNTIME_DIR/mykitty-*
probe_timeout_seconds = 2                           # unchanged
binary                = "kitty"                     # unchanged
```

No key is added, removed, or renamed. An existing configuration file parses identically.

## The default

Computed at load, not frozen at import:

```
default = f"{paths.runtime_dir()}/mykitty-*"
```

`paths.runtime_dir()` is `$XDG_RUNTIME_DIR` when set, otherwise `paths.state_home()`
(`~/.local/state`). Both are owned by the user and not writable by anyone else, which is what
FR-008 and FR-009 require. The same function already decides where the daemon's own sockets live,
and `docs/state.md` already records why.

`TerminalConfig.socket_glob` uses `field(default_factory=...)` so that a `TerminalConfig()`
built in a test sees the same value the loader would produce for the same environment.

## Warnings

Both are warnings, never errors: the configuration still loads and the daemon still starts
(research R4).

| Condition | Message |
|-----------|---------|
| pattern contains no `*` or `?` | unchanged from today, word for word |
| the pattern's fixed leading directory is writable by others without the sticky bit, or is owned by a third party | states that another local user could place a socket there, names the recommended `$XDG_RUNTIME_DIR/mykitty-*`, and says the daemon will refuse any candidate it does not own |

The fixed leading directory is the longest prefix of the pattern containing no wildcard
character — for `/tmp/mykitty-*` that is `/tmp`, which is world-writable *with* the sticky bit
and therefore does **not** warn on its own; the warning is aimed at the genuinely unsafe shapes
(`/var/tmp/shared/*` with mode `0777`, a directory owned by somebody else). A pattern naming a
directory that does not exist yet does not warn, because there is nothing to judge.

## Documentation that must agree

- `share/config.example.toml` — the `socket_glob` line shows the runtime-directory form, and the
  `[repos.*] env` block carries a comment that its values are visible to other local processes.
- `README.md` — the `listen_on` line becomes `unix:${XDG_RUNTIME_DIR}/mykitty` (kitty expands
  environment variables and appends the PID with a hyphen, verified against kitty 0.48.2), with a
  sentence on why not `/tmp`, a warning against the abstract form `unix:@mykitty` (no filesystem
  permissions at all), and the note about launch arguments being readable through the process
  table.
- `docs/security-analysis.md` — RA-15 marked resolved, describing what refuses the impostor and
  what remains true about the argument exposure.
