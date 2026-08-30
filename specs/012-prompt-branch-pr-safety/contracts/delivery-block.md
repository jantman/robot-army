# Contract: The Standing Delivery Block

The one piece of user-visible prose this milestone ships. Fixed text: it does not vary with the
repository, the issue, the branch, the dispatch mode, or anything the daemon detected. That is
what makes FR-010's determinism a one-line test rather than an argument.

## Position

`prompt.compose()` assembles, in order, after this milestone:

```text
[.claude/robot-army.md, if present]
---
[the Spec Kit block, if detected and enabled]
---
[the standing delivery block]               ← new, present on every dispatch
---
You are working on <repo> issue #<n> in a dedicated git
worktree on branch `<branch>`.

**Title**: …
**URL**: …
**Labels**: …

---

<the issue body>
```

Below the Spec Kit block so that block's closing sentence ("the instruction above wins") keeps
the meaning it had when it was written; above the issue so the block cannot be pushed past a
60,000-character body or cut by its truncation. The reasoning in full is
[research.md D2](../research.md).

## The text

> Unless the issue below explicitly says otherwise, this is how the work is expected to be
> delivered.
>
> Do the work on the feature branch this session was started on, never on the repository's
> default branch. When the work is done, commit it, push that branch to `origin`, and open a
> pull request. Commits sitting on an unpushed branch are not a finished job: the worktree can
> be reclaimed, and unpushed work is the one thing that cannot be recovered from it.
>
> Deliver the work as code and file changes in this repository, arriving as commits and a pull
> request. Where this repository is the mechanism for changing something — configuration
> management, infrastructure as code, deployment or schedule definitions — an issue asking for
> that thing is asking you to write the code that produces it, not to go and do it directly. A
> change made by hand is invisible to review and gone the next time the real tool runs.
>
> This is not a limit on how you work: build, run, test, install dependencies, start things
> locally, read whatever you need to read including live systems, and push your branch and open
> the pull request at the end. It is a limit on one thing — reaching past the repository to
> change a live system, where a change to the repository is what was asked for.
>
> If the issue below explicitly asks for something else — no pull request, a commit straight to
> the default branch, or an action on a system — the issue wins. Nothing here is checked.

1,445 characters. Five paragraphs, one job each: the override framing, delivery (FR-002,
FR-003), the mechanism rule with its reason (FR-004, FR-005), the scope line (FR-006, FR-007),
and the override rule stated outright (FR-008).

The third and fourth paragraphs are one idea split deliberately. The rule needs its boundary
stated in its own sentence rather than as a subordinate clause, because the boundary is the part
a session is most likely to get wrong in the cautious direction — and a session that will not run
the test suite is as broken as one that reconfigures a host.

## Rules

| Rule | Requirement |
|---|---|
| Present on every dispatch, in every repository, with no per-repository file or setting | FR-001, FR-011 |
| Says work happens on the non-default branch, not the default one | FR-002 |
| Says the branch is pushed to `origin` and a pull request opened at the end | FR-003 |
| Says the work is delivered as repository changes arriving as commits and a pull request | FR-004 |
| Says the repository is the mechanism, names the repository kinds, and gives the reason | FR-005 |
| Scopes the limit to bypassing the repository, so it cannot forbid its own delivery rule | FR-006 |
| Puts the ordinary working loop outside the limit, not inside it as an exception | FR-007, SC-007 |
| States that an explicit instruction in the issue body overrides it | FR-008 |
| Sits below `.claude/robot-army.md`, which continues to outrank it | FR-009 |
| Identical bytes on every dispatch; no interpolation, no placeholders | FR-010 |
| Under 1,500 characters | SC-004 |
| Never states or implies that compliance is checked | spec Edge Cases; matches the Spec Kit block's stance |

## What it must not say

- **Not "above".** The branch name appears in the section *below* this block, so a direction
  word pointing up would be false. See [research.md D3](../research.md).
- **Not the branch name itself.** Interpolating it would make the text vary per dispatch and
  cost the determinism test its one-line proof, to restate something on the next line.
- **Not a claim of enforcement.** Nothing checks any of this, and text implying otherwise would
  be the kind of false boundary the spec's Out of Scope rules out.
- **Not "do not change the state of any system", and not an exceptions list.** This is where the
  first draft went wrong, and the failure is instructive enough to record: phrased as a ban on
  side effects, the rule forbids the push and the pull request the block demands two paragraphs
  earlier, forbids running the test suite, and *still* does not explain the case it exists for,
  because it names the wrong thing as the fault. The fault is bypassing the repository, not
  touching a system. A rule drawn there needs no exceptions — which is why the reappearance of an
  exception list should be read as evidence the rule has drifted back to the wrong place. See
  [research.md D6](../research.md).

## The determinism tests

Two, both cheap, both guarding a requirement that an innocent edit could break:

- The constant contains no `{` and no `}` — a string with no format placeholders cannot vary.
- Composing the same fixture issue twice yields identical bytes.

Plus the golden-string test in `tests/unit/test_speckit_prompt.py`, whose expected value this
milestone deliberately changes: see [research.md D5](../research.md).
