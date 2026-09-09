# Contract: what `show` prints under a session attempt about attaching to it

**Feature**: [../spec.md](../spec.md) · **Surface**: `robot-army show <id>`, terminal output only

`show` renders each session attempt as two lines — the identity line and the times line — and
may then print **nought, one, or two further lines** about attaching to that attempt. This
contract fixes which, and their exact text.

## The two questions, in order

1. **Could this row be what is listening?** Answered by the attempt's state alone. A socket
   path is named after the work item, not the session, so every attempt on an item records the
   same path and a live socket proves nothing about *which* attempt is behind it. An attempt in
   `exited_clean`, `exited_error` or `lost` is not what is listening, whatever is.
2. **Is anything listening?** Asked only of the rows the first question lets through, and asked
   of the session host boundary — the same question `attach` will ask when the maintainer acts
   on the line.

## The four outcomes

| # | Condition | What is printed |
|---|---|---|
| 1 | No socket path recorded | nothing |
| 2 | The attempt is in a terminal state (`exited_clean`, `exited_error`, `lost`) | nothing |
| 3 | Open attempt (`starting`, `running`), socket answers | the command |
| 4 | Open attempt, socket does not answer | the refusal |
| 5 | Open attempt, the check could not answer | the command **and** the caveat |

Outcomes 1 and 2 both print nothing, and they are listed apart because they are different
facts: one row never had a socket, the other has one that is no longer its own.

## The exact text

Seven leading spaces, aligning with the times line above it, unchanged from today.

**The command** (outcome 3) — byte-for-byte what is printed today:

```
       reattach: dtach -a /run/user/1000/robot-army/12.sock
```

The command itself is composed by the session host (`attach_command`), not spelled out at
the render site, so it cannot drift from the invocation `attach` actually runs.

**The refusal** (outcome 4):

```
       reattach: not available — nothing is listening on /run/user/1000/robot-army/12.sock
```

It names the path because the reader's next question is which one, and it says what was
observed — nothing is listening *now* — rather than diagnosing why. Nothing here asserts that
the socket ever existed: a `starting` attempt whose host has not been spawned yet, and a
rehearsal's row naming a socket no process ever bound, both print this and both are described
correctly by it.

**The command with a caveat** (outcome 5), two lines, the second aligned under the first's
command:

```
       reattach: dtach -a /run/user/1000/robot-army/12.sock
                 unverified: dtach probe on /run/user/1000/robot-army/12.sock timed out
```

The command is still offered, because hiding a session that may well be alive is the worse of
the two errors and the maintainer can simply run it and see. The caveat is what stops the offer
being a claim. The text after `unverified: ` is the boundary's own error message, not a
rewording of it.

## Invariants

- **I1**: Every `dtach` command `show` prints either reaches the session on the row it is
  printed under, or is followed by the `unverified:` line. There is no third case.
- **I2**: A finished attempt costs no syscall. The state check short-circuits first, so `show`
  on an item with ten dead attempts asks the operating system nothing.
- **I3**: `show` remains read-only and cannot fail because of this. A probe that raises
  produces outcome 5; the exit code and every other section are unaffected.
- **I4**: The `--json` payload is unchanged. `host_socket` there records what was configured
  and makes no claim about reachability; a consumer wanting outcome 2's distinction reads
  `state`, which is in the payload already.
