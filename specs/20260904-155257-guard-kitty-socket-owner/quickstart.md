# Quickstart: proving the socket guard by hand

**Feature**: `specs/20260904-155257-guard-kitty-socket-owner` · **Date**: 2026-09-04

Prerequisites: the repository checked out, `uv` available, and — for the last two sections — a
running kitty with `allow_remote_control yes`.

## The automated proof

```bash
uv run pytest tests/unit/test_kitty_socket_trust.py -v
uv run pytest tests/unit/test_config.py -k socket_glob -v
uv run pytest                       # the whole suite must pass (constitution)
uv run ruff check src/ tests/       # the scope CI lints
```

## The negative test that matters most

This is the finding, reproduced and then refuted. It needs no second user account: a socket
belonging to *you* in a directory that lets anyone rearrange it is refused for the same reason
the stranger's socket is.

```bash
d=$(mktemp -d) && chmod 0777 "$d"          # world-writable, no sticky bit
python3 - "$d" <<'PY'
import socket, sys
s = socket.socket(socket.AF_UNIX); s.bind(f"{sys.argv[1]}/mykitty-zzz"); s.listen()
input("listening; press enter to stop")
PY
```

In another shell, point the daemon at it and ask the diagnostic:

```bash
uv run robot-army doctor            # with [terminal] socket_glob = "<d>/mykitty-*"
```

Expected: the terminal-socket check **fails**, and its detail names the candidate and says the
directory is writable by others without the sticky bit. Before this change it reported the socket
as healthy.

Now remove the hazard and repeat:

```bash
chmod 1777 "$d"                     # same directory, sticky bit set
uv run robot-army doctor
```

Expected: the check passes — the socket is yours and nobody else can swap it. That contrast is
the rule in one pair of commands.

## The impostor that sorts first

With the real kitty running and `socket_glob` at its default:

```bash
touch "${XDG_RUNTIME_DIR}/mykitty-zzzzzz"     # sorts ahead of the real socket
uv run robot-army doctor
```

Expected: the diagnostic still reports the **real** socket. The plain file is refused for not
being a socket, and discovery continues past it. Check the audit log for the refusal:

```bash
grep kitty.probe ~/.local/state/robot-army/logs/*.jsonl | tail -1 | python3 -m json.tool
```

Expected: one record carrying both `tried` (the real socket, exit 0) and `refused` (the planted
name, `not a socket`). Delete the file afterwards.

## The default, and the existing setup

```bash
uv run python -c "from robot_army.config import TerminalConfig; print(TerminalConfig().socket_glob)"
```

Expected: `/run/user/<uid>/mykitty-*` — and with `XDG_RUNTIME_DIR` unset, a path under
`~/.local/state`, never `/tmp`.

With `socket_glob = "/tmp/mykitty-*"` still in the configuration file, expected: the daemon
starts, dispatch works exactly as before, and one warning is printed at load naming the
recommended location. A security fix that demanded an edit before the daemon would run again
would be a fix that gets reverted, so this case is part of the proof, not an afterthought.

## The exposure that remains

```bash
grep -n "process table\|cmdline" README.md share/config.example.toml
```

Expected: both say that the composed prompt and every `[repos.*] env` value are passed as command
arguments and are therefore readable by any local process while a session launches. This feature
documents that; it does not close it.
