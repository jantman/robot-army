# Quickstart: validating the read guard and the read costs

**Feature**: `specs/20260904-143822-guard-cross-origin-gets`

Six checks. The first five can be run by hand against a live interface; the sixth is the suite.
Behaviour under test is defined in [`contracts/read-cost.md`](contracts/read-cost.md).

## Prerequisites

```bash
cd /home/jantman/GIT/robot-army
uv sync
```

A configured `robot-army` — the interface refuses to start otherwise, which is unchanged — and
at least one item in `interrupted` or `awaiting_review` for checks 2 and 3 to have anything to
count. The commands below assume the shipped defaults, `127.0.0.1:8420`.

Start the interface in one terminal and leave it running:

```bash
uv run robot-army serve
```

---

## 1. A cross-site read is refused; every honest read still works (C1, FR-001–FR-005, SC-001–SC-003)

The attack, as a browser would send it:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H 'Sec-Fetch-Site: cross-site' \
  -H 'Origin: https://evil.example' \
  http://127.0.0.1:8420/interrupted
```

**Expected**: `403`. Before this feature: `200`, after several `git` subprocesses per item.

Read the refusal itself:

```bash
curl -sS -H 'Accept: application/json' -H 'Sec-Fetch-Site: cross-site' \
  http://127.0.0.1:8420/log | python3 -m json.tool
```

**Expected**: `{"ok": false, "reason": "...", "code": 3}` — the interface's standard refusal
shape, not a crash and not a blank page.

Now every path that must keep working:

```bash
# The documented terminal path: no origin headers at all.
curl -sS -o /dev/null -w 'no headers      %{http_code}\n' http://127.0.0.1:8420/queue

# The address bar or a bookmark.
curl -sS -o /dev/null -w 'sec-fetch none  %{http_code}\n' \
  -H 'Sec-Fetch-Site: none' http://127.0.0.1:8420/queue

# A link on a page this server rendered.
curl -sS -o /dev/null -w 'same-origin     %{http_code}\n' \
  -H 'Sec-Fetch-Site: same-origin' -H 'Origin: http://127.0.0.1:8420' \
  http://127.0.0.1:8420/queue
```

**Expected**: `200` for all three.

Check that a cross-site POST is still refused *and still recorded* — the property this feature
must not break:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'Sec-Fetch-Site: cross-site' http://127.0.0.1:8420/dispatch/pause
uv run robot-army log --limit 2
```

**Expected**: `403`, and the log shows a `web.dispatch.pause` pair whose outcome is `error`.
A refused *read* leaves no such record, by design (C2) — the count appears in `web.stop`.

Stop the interface with `Ctrl-C` and look at the closing record:

```bash
uv run robot-army log --limit 1
```

**Expected**: a `web.stop` record whose detail carries `refused_cross_site` with the number of
reads this run turned away.

---

## 2. `/interrupted` stops forking git per card (C4, FR-006–FR-009, SC-004)

Watch the subprocesses while loading the page twice in quick succession. In a third terminal:

```bash
# Count git processes started against the worktree root over the next 20 seconds.
timeout 20 strace -f -e trace=execve -p "$(pgrep -f 'robot-army serve')" 2>&1 \
  | grep -c '"/usr/bin/git"' &
sleep 1
curl -sS -o /dev/null http://127.0.0.1:8420/interrupted
curl -sS -o /dev/null http://127.0.0.1:8420/interrupted
wait
```

**Expected**: roughly the count of one render, not two. Before this feature the two renders cost
the same as each other.

If `strace` is not available, the page itself says it:

```bash
curl -sS http://127.0.0.1:8420/interrupted | grep -o 'checkout signals[^<]*'
curl -sS http://127.0.0.1:8420/interrupted | grep -o 'checkout signals[^<]*'
```

**Expected**: the first render says `checkout signals read just now`; the second, issued within
five seconds, says `checkout signals 0s old (cached)` or similar with a small age. Wait six
seconds and repeat: it says `read just now` again.

Then confirm acting on an item clears it — hold and release an item, and reload:

```bash
uv run robot-army hold <id> --reason quickstart
uv run robot-army release <id>
curl -sS http://127.0.0.1:8420/interrupted | grep -o 'checkout signals[^<]*'
```

**Expected**: `read just now` — the cache was dropped for that item.

---

## 3. A log filter matching nothing stays cheap (C5, FR-013–FR-015, SC-005, SC-006)

```bash
time curl -sS -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:8420/log?item=999999'
```

**Expected**: well under two seconds even against a large log directory. Before this feature it
reads every `audit-*.jsonl` file whole.

Read what the response says about itself:

```bash
curl -sS -H 'Accept: application/json' 'http://127.0.0.1:8420/log?item=999999' \
  | python3 -m json.tool | grep -E 'truncated|bytes_scanned|has_more|next_cursor'
```

**Expected**: `bytes_scanned` no greater than 8388608. If the log directory is larger than the
budget, `truncated` is `true`, `has_more` is `true`, and `next_cursor` is set — follow it and the
scan continues from where it stopped rather than starting over.

Confirm the ordinary page is unchanged:

```bash
uv run robot-army log --limit 5
curl -sS -H 'Accept: application/json' http://127.0.0.1:8420/log \
  | python3 -m json.tool | grep -m5 '"ts"'
```

**Expected**: the same newest-first records, in the same order.

---

## 4. One machine observation per rendered page (C3, FR-016, SC-007)

`capacity.snapshot` enumerates `/proc`, so count the `openat` calls against it:

```bash
timeout 10 strace -f -e trace=openat -p "$(pgrep -f 'robot-army serve')" 2>&1 \
  | grep -c '"/proc/' &
sleep 1
curl -sS -o /dev/null http://127.0.0.1:8420/queue
wait
```

**Expected**: roughly half what the same command reports before this feature.

Cheaper and just as conclusive — the two capacity readings on one page must agree:

```bash
curl -sS -H 'Accept: application/json' http://127.0.0.1:8420/queue \
  | python3 -m json.tool | grep -A6 '"capacity"'
```

**Expected**: the chrome's capacity block and the queue's own capacity block report identical
numbers, every time. Before this feature they were two observations moments apart.

---

## 5. Nothing about the interface's other guards moved

```bash
# DNS rebinding is still refused, on reads and writes.
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: evil.test:8420' http://127.0.0.1:8420/active

# The security headers are still on every response.
curl -sSI http://127.0.0.1:8420/active | grep -iE 'frame-options|content-security|nosniff|referrer'
```

**Expected**: `403` for the first; all four headers present on the second.

---

## 6. The suite

```bash
uv run pytest
```

**Expected**: everything passes. The cases this feature adds live in
`tests/unit/test_web_read_cost.py` (the counting tests), and in
`tests/unit/test_web_routing.py`, `tests/unit/test_resume_signals.py` and
`tests/unit/test_web_log.py` (the behaviour of each change), plus one round-trip in
`tests/integration/test_web_end_to_end.py` proving a cross-site GET is refused over a real
socket.

To run only this feature's cases:

```bash
uv run pytest tests/unit/test_web_read_cost.py tests/unit/test_resume_signals.py \
  tests/unit/test_web_log.py tests/unit/test_web_routing.py -q
```
