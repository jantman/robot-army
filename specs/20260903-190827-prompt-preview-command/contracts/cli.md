# Contract: `robot-army prompt`

An addition to milestone 001's [CLI contract](../../001-minimum-daemon/contracts/cli.md).
Every universal rule there still applies; this command adds no flag of its own.

## Usage

```text
robot-army prompt <owner/repo> <issue-number>
```

| Argument | Meaning |
|---|---|
| `<owner/repo>` | An onboarded repository's key, exactly as `robot-army repos` prints it |
| `<issue-number>` | An issue number in that repository. Need not be labelled, eligible, open, or known to this system |

Global `--config PATH` applies, as it does to every command. There is **no `--json`**
([R7](../research.md)) and no `--include-simulated`.

## What it prints

**stdout**: the composed prompt, and nothing else. No banner, no header, no trailing
summary, no blank framing line. `robot-army prompt owner/repo 42 > prompt.txt` produces a
file whose whole content is the text a session would be handed.

**stderr**: one note naming the directory the repository's instructions and the Spec Kit
detection were read from, in one of three forms:

```text
context read from the worktree at /home/…/worktrees/repo/issue-42
context read from the clone at /home/…/git/repo (no worktree for this issue)
no readable directory at /home/…/git/repo — repository instructions and spec kit guidance are omitted
```

plus, on any non-zero exit, the reason for the failure.

## Exit codes

| Code | Condition | stdout |
|---|---|---|
| `0` | The prompt was composed and printed | the prompt |
| `1` | The issue could not be obtained — 404, authentication, rate limit, transport failure | empty |
| `2` | `<owner/repo>` is not `owner/name`, or `<issue-number>` is not a positive integer | empty |
| `3` | The repository is not onboarded | empty |

The three failure conditions are distinguishable by code alone, without parsing a message
(SC-007). Code `2` for a non-numeric issue number comes from argparse's own `type=int`
([R11](../research.md)).

## Guarantees

1. **Fidelity.** For the same issue, the same work-item row and the same directory contents,
   the printed text is byte-for-byte what `dispatch.build_launch_plan` would put in the
   worker's argv. Both call `prompt.compose`; neither reimplements it.
2. **The delivery block is always present.** There is no argument that suppresses it, mirroring
   its unconditional presence in a dispatch.
3. **The Spec Kit block obeys the same gates.** Detection, per-repository suppression and the
   configured command list are `dispatch.speckit_block`'s, unchanged.
4. **Read-only.** No row is created or modified, no branch or worktree is made, no session is
   started, nothing is written to GitHub. The only durable effect is the audit record.
5. **Not gated by dispatch controls.** A paused daemon, a held item and a held repository make
   no difference: this command does not dispatch.
6. **Live read.** The issue is fetched at invocation. The output is what a dispatch *now*
   would send, not a record of what a past dispatch did send.

## Behaviour on unusual issues

| Case | Behaviour |
|---|---|
| Issue is closed | Printed. Eligibility is a different question, answered elsewhere |
| Number names a pull request | Printed. GitHub's issues endpoint serves it and `compose` has no opinion |
| Issue body is empty | The prompt carries `_(the issue has no body)_`, as a dispatch would |
| Body exceeds `prompt.MAX_BODY_CHARS` | Truncated with the pointer to the issue URL, as a dispatch would |
| Title yields no slug | The derived branch omits the slug, as `prompt.branch_name` does |
| Repository onboarded, clone missing | Exit `0`, prompt printed without the instructions and Spec Kit sections, warned on stderr |
