# Contract: Worker Launch Shapes

**Feature**: [../spec.md](../spec.md) | **Requirements**: FR-001 – FR-004, FR-013 – FR-016

This is the contract between `build_launch_plan` and the worker binary. It is authoritative only
because it was **measured** (see [research.md](../research.md) R1, R2, R5) — the defect it exists
to prevent was caused by a launch shape that was correct by the code's own definition and
rejected by the binary.

## The shapes

Let `S` be the session id this system chose and `P` the prior session being restored.

### Non-restoring (unchanged)

```
<binary> --session-id S -n <name> --remote-control <name> --permission-mode <mode> [--model <model>] <prompt>
```

### Restoring

```
<binary> --session-id S --resume P --fork-session -n <name> --remote-control <name> --permission-mode <mode> [--model <model>] <prompt>
```

`<mode>` ∈ `acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, `plan`.

`--bare` is never used, for the reasons already recorded at `dispatch.py`.

## Guarantees relied upon

| # | Guarantee | Evidence |
|---|---|---|
| G1 | `--session-id` with `--resume` is rejected unless `--fork-session` is also given. | `Error: --session-id can only be used with --continue or --resume if --fork-session is also specified.` |
| G2 | With `--fork-session`, the combination is accepted and argument validation proceeds. | Reaches `No conversation found with session ID: …` for an unknown id. |
| G3 | The forked session runs under the id **we** supplied, not one the worker invents. | A fork requested as `bbbb…0002` wrote `bbbb…0002.jsonl`; every record inside carries that `sessionId`. |
| G4 | The forked session carries the prior conversation. | It answered with a marker string present only in the prior session's transcript. |
| G5 | The prior session's transcript is preserved, not consumed. | Original remained at 12 lines; the fork was 20. |
| G6 | Arguments are validated before any model call. | `printf '' \| <binary> -p <shape>` returns in ~0.9s having complained only about missing input. |

G3 is the one that would have failed silently: had the worker invented its own id, tracking,
attach, terminate, and exit correlation would all have addressed the wrong process while the
launch *looked* successful.

## Verification probe (FR-013 – FR-016)

Each shape is checked by running it with `-p` and empty stdin, substituted for the prompt:

```
printf '' | <binary> -p <shape flags…>
```

A shape **passes** when the binary's output contains its expected sentinel, and **fails** on
anything else — in particular on an argument rejection:

| Shape | Expected sentinel | Meaning |
|---|---|---|
| Non-restoring | `Input must be provided either through stdin or as a prompt argument` | Every flag accepted; only the prompt was missing. |
| Restoring (unknown `P`) | `No conversation found with session ID:` | Every flag accepted; only the conversation was missing. |
| Any | `Error: --session-id can only be used with …` | **Failure.** The defect this contract exists to catch. |

Both passing outcomes exit non-zero, so exit status alone cannot discriminate — the sentinel is
the discriminator.

The probe MUST NOT dispatch work, create a worktree, or leave a session behind (FR-016). Using an
unknown `P` guarantees the restoring probe cannot resume anything real.

### When the binary is absent

The check reports itself as **skipped**, naming the missing binary. It never reports success, and
it never fails a suite on a machine where the worker simply is not installed (FR-015). This
follows the convention already in `tests/integration/test_spool_recovery.py`.

### When a future worker release changes the wording

The check fails, loudly, naming the shape and the binary's actual output. That is correct: the
sentinels encode measured behaviour, and behaviour that has changed must be re-measured rather
than assumed. Silently tolerating unrecognised output would restore exactly the blind spot that
produced this feature.
