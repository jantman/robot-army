# Quickstart: Validating the Reattach Line

**Feature**: `specs/20260908-170313-attachable-reattach-line/`

Everything here runs offline against seeded database rows and a stub session host. Nothing
needs GitHub, a running daemon, a real `dtach`, or a real socket.

```bash
uv sync
uv run pytest                     # the whole suite must pass
```

---

## The one-line proof

The defect and the fix are both visible in one question: how many `dtach` commands `show`
prints for an item whose only attempt is over.

```bash
uv run pytest tests/unit/test_show_reattach_line.py -q
```

Before this feature that number is 1, in each of the three terminal states (research.md R1).
After it, 0 — and it is still 1 for an open attempt whose socket answers, which is the half of
the property that is easy to lose.

---

## Scenario 1 — A session that is over offers nowhere to go (US1)

**Setup**: one `active` item; one attempt with a recorded socket path; the stub host reporting
that path as **live**, so the outcome cannot be attributed to the socket being absent.

**Run**: `operations.show(ctx, item_id)`.

**Expect**, once for each terminal state:

| Attempt state | Rendered |
|---|---|
| `exited_clean` | no line containing `dtach` |
| `exited_error` | no line containing `dtach` |
| `lost` | no line containing `dtach` |

The live socket in the setup is the point. It is what the item's *next* dispatch will be
listening on, and the reason the guard is the row's state rather than the path's liveness
(research.md R2).

## Scenario 2 — Two attempts, one socket (US1, FR-001)

**Setup**: one item with two attempts recording the same path — attempt #1 `lost`, attempt #2
`running` — and the stub host reporting that path as live.

**Expect**: exactly one `dtach` command in the output, under attempt #2. This is the case the
issue did not report and the one that misleads rather than merely fails: before the fix, both
rows carry it.

## Scenario 3 — A live session is still offered (US2, FR-002)

**Setup**: one `running` attempt whose recorded path the stub host reports as live.

**Expect**: `       reattach: dtach -a <path>`, byte-for-byte what is printed today.

## Scenario 4 — An open row with nothing at the other end says so (US2, FR-003)

**Setup**: one `running` attempt whose path the stub host does **not** report as live. Repeat
with `starting`.

**Expect**: `       reattach: not available — nothing is listening on <path>`, and no `dtach`
command anywhere in the output.

## Scenario 5 — A check that could not answer (US3, FR-004, FR-009)

**Setup**: one `running` attempt, and a session host whose `is_alive` raises `BoundaryError`.

**Expect**:

- the `dtach` command is printed
- the next line reads `unverified: ` followed by the boundary's own message
- `result.code` is `EXIT_OK` and every other section of the item renders normally

## Scenario 6 — Nothing recorded, nothing invented (FR-005)

**Setup**: an attempt with `host_socket` NULL, in each of the five states.

**Expect**: no attach line of any kind, and no path in the output. Unchanged from today.

## Scenario 7 — The payload and the web are untouched (FR-008)

```bash
uv run pytest tests/unit/test_web_views.py tests/unit/test_web_render.py -q
```

**Expect**: green, with no change to those files. `show(...).data["sessions"][0]` carries the
same thirteen keys it carries today (research.md R7); the item view renders from that payload
and never reads the lines this feature changes.

---

## By hand, on a real machine

With a session actually running:

```bash
uv run robot-army show <id>        # the open attempt carries the dtach command
uv run robot-army cancel <id>      # stop it
uv run robot-army show <id>        # the same attempt now carries nothing
ls /run/user/$(id -u)/robot-army/  # and its socket is gone
```

Then dispatch the same item again and show it once more: the finished attempt stays silent
while the new one carries the command, even though both rows name the same path.
