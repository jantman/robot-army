# Contract: The two delivery forms

The delivery block is the one piece of prose robot-army puts in front of every session. Until
now there was one of it. There are now two, and which one a session receives is decided by the
effect level, at the same moment and in the same place as every other real-or-simulated
selection.

This contract supersedes
[`specs/012-prompt-branch-pr-safety/contracts/delivery-block.md`](../../012-prompt-branch-pr-safety/contracts/delivery-block.md)
in one respect only: the block is no longer fixed text. Everything that document says about
position, precedence and determinism still holds, for both forms.

## Selection

| Effect level | Form | `name` |
|---|---|---|
| `plan` | local-only | `local` |
| `local` | local-only | `local` |
| `no-remote` | local-only | `local` |
| `live` | push-and-pull-request | `push` |

- The selection happens in `effects.wire()` and nowhere else. No other module may name
  `EffectLevel`; `tests/unit/test_effects.py` enforces this for the whole package.
- Nothing else selects: not a configuration key, not a per-repository setting, not the issue's
  text, not a caller's argument. `prompt.compose` takes the form it is handed and does not
  choose one.
- `robot-army prompt` accepts `--effect-level`, the same override `run` and `serve` accept, so
  the preview can be asked about a level other than the configured one. It selects a form the
  same way a daemon at that level would: through `build_context` into `wire()`.
- The argument is **required**. A default would be the pre-issue-#32 behaviour, handed for free
  to the next call site that forgets.

## Position

Unchanged from milestone 012 — the form occupies the same slot, whichever it is:

```text
[.claude/robot-army.md, if present]
---
[the Spec Kit block, if detected and enabled]
---
[the delivery form for this effect level]
---
You are working on <repo> issue #<n> … <the fenced issue>
```

## What both forms must say

Every rule below holds for `push` and `local` alike. Each is a test in
`tests/unit/test_delivery_prompt.py`, parametrised over both forms.

| Rule | Requirement |
|---|---|
| Present on every dispatch, in every repository, with no file or setting to enable it | FR-001, FR-007 |
| Names whose rules these are — the person who dispatched the session | RA-06 |
| Work happens on the feature branch the session was started on, never the default branch | FR-006 |
| Says "when there is work to deliver", so an investigation is not forced to produce commits | FR-006 |
| Never points *upwards* at the branch name, which is composed below the block | 012 D3 |
| Says the repository is the mechanism for changing what the repository manages, and why a hand-made change is worse than none | FR-006 |
| Puts the ordinary working loop outside the limit rather than excepting it from one | FR-006 |
| Keeps reading — including of live systems — outside the limit, at every level | FR-006 |
| Asserts its own precedence over the issue text however that text is worded | FR-006 |
| Grants the issue no override, and does not re-list the three overrides even as refusals | FR-006 |
| Disclaims enforcement: nothing here is checked by the system | FR-006 |
| Contains no format placeholder, so the composed prompt stays deterministic | 012 FR-010 |

## What only the `push` form says

> …commit it, push that branch to `origin`, and open a pull request. Commits sitting on an
> unpushed branch are not a finished job…
>
> …arriving as commits and a pull request…
>
> …It is a limit on one thing — reaching past the repository to change a live system, where a
> change to the repository is what was asked for.

Its text is **unchanged** from what ships today, byte for byte (SC-003). A `live` dispatch's
prompt after this feature is identical to a `live` dispatch's prompt before it.

## What only the `local` form says

| Rule | Requirement |
|---|---|
| Says the work is committed on the feature branch and stops there | FR-005 |
| Names the prohibited outward actions explicitly: no push, no pull request, no comment on the issue, nothing written to any remote | FR-004 |
| Says where the work does end up — the commits in this worktree, which is where they will be read | FR-005 |
| Says an instruction composed above it that asks for a push or a pull request describes a `live` dispatch and does not apply to this run, and changes nothing else about those instructions | FR-006a |
| Scopes its limit to two named things — sending anything out from this machine, and bypassing the repository — rather than to system state in general | FR-006 |
| Says reading is real at every effect level, so the reading permission is not read as an oversight | FR-006 |

Naming the prohibited actions is a deliberate departure from the `push` form's rule against
naming the three overrides. That rule exists because naming them *while permitting them* hands
an injected paragraph its vocabulary; here they are named while being refused, which is the
opposite move. The closing precedence paragraph is identical in both forms and still covers
anything not named.

### The sentence about instructions above

The `local` form is the only text in this system that narrows an instruction composed above it.
The carve-out is deliberate, and it is bounded three ways:

1. It applies to outward writes only — a push, a pull request, any other write off this
   machine. Every other thing a repository's `.claude/robot-army.md` or a configured Spec Kit
   instruction asks for is untouched.
2. It exists only in the form that a below-`live` run composes. At `live` there is nothing to
   reconcile and the sentence is absent.
3. It is not reachable from the issue's text. The effect level is chosen by the operator at
   startup; nothing inside the fence can select a form, so RA-06's reason for putting
   `.claude/robot-army.md` above everything — an exception channel the issue's author does not
   control — is untouched.

Without it, a `no-remote` dispatch of a Spec Kit issue in *this* repository would still be told
to push, by this repository's own `[speckit] implement` string, and the feature would fail on
the repository the bug was found in ([research.md R8](../research.md)).

## Size

| Form | Budget | Why |
|---|---|---|
| `push` | < 1,800 characters | Unchanged from RA-06. |
| `local` | < 2,200 characters | One paragraph longer by construction: it has to say what not to do, where the work goes instead, and which instruction above it narrows. Still small against the 60,000-character body allowance, and the budget moves with its reason written beside it, which is the honest version of a budget. |

## What is recorded

| Record | Field | Value |
|---|---|---|
| `daemon.start` | `boundaries.delivery` | the form's `name` |
| `prompt.preview` | `delivery` | the form's `name` |

Neither carries the text. The form is a pure function of the effect level, which
`daemon.start` records already; the two fields exist so a reader does not have to know that
function to answer "what was this session told?".
