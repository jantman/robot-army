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
> What you produce should be code and file changes in this git repository, arriving as commits
> and pull requests. Do not satisfy the issue by changing the state of this machine or any other
> system — do not deploy, restart, reconfigure, or edit something in place where the change
> belongs in this repository instead. Pushing your branch and opening the pull request are the
> exceptions. Running tests, running builds, and installing dependencies inside this worktree
> are ordinary parts of doing the work and are not what this restricts.
>
> If the issue below explicitly asks for something else — no pull request, a commit straight to
> the default branch, or an action on a system — the issue wins. Nothing here is checked.

Four paragraphs, one job each: the override framing, delivery (FR-002, FR-003), containment
with its carve-outs (FR-004 – FR-007), and the override rule stated outright (FR-008).

## Rules

| Rule | Requirement |
|---|---|
| Present on every dispatch, in every repository, with no per-repository file or setting | FR-001, FR-011 |
| Says work happens on the non-default branch, not the default one | FR-002 |
| Says the branch is pushed to `origin` and a pull request opened at the end | FR-003 |
| Says the work product is repository changes delivered as commits and pull requests | FR-004 |
| Says not to satisfy the issue by changing this or another system | FR-005 |
| Names the push and the pull request as permitted, so it cannot forbid its own delivery rule | FR-006 |
| Leaves tests, builds, and dependency installation inside the worktree explicitly permitted | FR-007 |
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

## The determinism tests

Two, both cheap, both guarding a requirement that an innocent edit could break:

- The constant contains no `{` and no `}` — a string with no format placeholders cannot vary.
- Composing the same fixture issue twice yields identical bytes.

Plus the golden-string test in `tests/unit/test_speckit_prompt.py`, whose expected value this
milestone deliberately changes: see [research.md D5](../research.md).
