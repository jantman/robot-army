# Quickstart: validating the connection bounds

**Feature**: `specs/20260904-125206-web-socket-timeout-thread-cap`

Five checks. The first four are the ones a person can run by hand against a live interface;
the fifth is the suite. Behaviour under test is defined in
[`contracts/connection-limits.md`](contracts/connection-limits.md).

## Prerequisites

```bash
cd /home/jantman/GIT/robot-army
uv sync
```

A configured `robot-army` (the interface refuses to start otherwise — that is unchanged). The
commands below assume the shipped defaults, `127.0.0.1:8420`.

## 1. A silent connection is given up on (FR-001, SC-001)

Start the interface in one terminal:

```bash
uv run robot-army serve
```

In another, open a connection and say nothing:

```bash
time python3 -c "
import socket
s = socket.create_connection(('127.0.0.1', 8420))
print('connected; waiting for the server to give up')
print('server closed, read:', s.recv(1)) "
```

**Expected**: returns in about 15 seconds, printing `read: b''` — the server closed the
connection. Before this feature it never returns.

**Expected on the server's terminal**: nothing at all. No traceback, no line (FR-003).

Repeat with a partial request line to check the same bound mid-request:

```bash
python3 -c "
import socket, time
s = socket.create_connection(('127.0.0.1', 8420))
s.sendall(b'GET / HTTP/1.1\r\n')
start = time.monotonic()
s.recv(1)
print('closed after', round(time.monotonic() - start), 'seconds') "
```

And with a body that is promised and not sent (FR-002):

```bash
python3 -c "
import socket, time
s = socket.create_connection(('127.0.0.1', 8420))
s.sendall(b'POST /dispatch/pause HTTP/1.1\r\nHost: 127.0.0.1:8420\r\n'
          b'Content-Length: 4096\r\n\r\nx')
start = time.monotonic()
s.recv(1)
print('closed after', round(time.monotonic() - start), 'seconds') "
```

## 2. The interface still serves slow work (SC-004)

With the interface running, load a view that forks `git` for every interrupted item:

```bash
time curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8420/interrupted
```

**Expected**: `200`, however long it takes. The bound is on waiting for the client, never on
the server's own computation.

## 3. A flood is refused, and the interface stays usable (FR-004, SC-002)

Open more connections than the cap and hold them silent:

```bash
python3 - <<'PY'
import socket
held = []
for _ in range(40):
    s = socket.create_connection(('127.0.0.1', 8420))
    s.sendall(b'GET / HTTP/1.1\r\n')      # partial: never completes
    held.append(s)
print('holding', len(held), 'connections; press Enter to release')
input()
PY
```

While that is running, in a third terminal:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8420/
```

**Expected**: either `200` (a slot was free) or `503` — never a hang. Before this feature, all
40 connections are held forever and the descriptors are never returned.

**Expected on the server's terminal**: exactly one line,
`robot-army: at capacity (32 connections); refusing new connections`, no matter how many
connections were refused (FR-009).

Confirm the descriptors really are bounded while the flood is held:

```bash
ls -1 /proc/$(pgrep -f 'robot-army serve')/fd | wc -l
```

**Expected**: a number well under 200, and stable while the flood continues.

## 4. The refusal is in the durable record (FR-010, SC-006)

Release the held connections, then stop the interface with Ctrl-C and read the last record:

```bash
tail -n 5 "$(ls -t ~/.local/state/robot-army/logs/audit-*.jsonl | head -1)" \
  | python3 -c "
import json, sys
for line in sys.stdin:
    record = json.loads(line)
    if record.get('action') == 'web.stop':
        print(json.dumps(record['detail'], indent=2))"
```

**Expected**: a `detail` containing `refused_over_capacity` with the number of connections
turned away — non-zero after step 3, `0` after a run where the cap never engaged.

*(The audit directory is `<state_dir>/logs`; the path above is the XDG default.)*

## 5. The suite

```bash
uv run pytest -q
```

**Expected**: everything passes, including the two new modules —
`tests/unit/test_web_connection_limits.py` and
`tests/integration/test_web_connection_limits.py` — and every pre-existing web test unchanged
(SC-008). The integration module overrides the two module constants to a fraction of a second
and a cap of two, so it costs about a second, not a minute.

```bash
uv run pytest -q tests/unit/test_web_connection_limits.py \
                 tests/integration/test_web_connection_limits.py
```

## What failure looks like

| Symptom | Meaning |
|---|---|
| Step 1 never returns | `Handler.timeout` is not set, or is being shadowed by the dynamically built `BoundedHandler` subclass. |
| Step 1 prints a traceback on the server | Something is escaping `handle_one_request`'s `TimeoutError` clause — check the exception type actually raised. |
| Step 3 hangs instead of answering | The refusal is blocking the accept loop; the socket is not in non-blocking mode. |
| Step 3 refuses at far fewer than 32 | A slot is not being released — check that the decrement runs in a `finally` covering every way a connection ends (FR-008). |
| Step 4 shows no `refused_over_capacity` | The count is read from the wrong object, or `web.stop` is written before the server is closed. |
