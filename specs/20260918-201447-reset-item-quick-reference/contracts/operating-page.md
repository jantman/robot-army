# Contract: `docs/guide/operating.md` as a quick reference

**Feature**: [../spec.md](../spec.md) · **Date**: 2026-09-18

The page's job changes from "explain why the system is like this" to "answer what do I type".
This contract fixes its shape, because a shape that is only described in prose is a shape that
rots — which is the failure being fixed.

## Required structure, in this order

1. **One paragraph** saying what the page is and where the reasoning went.
2. **Recipes** — the situations, first, because that is why the page is open. A few lines each,
   commands in order, a link to the narrative page for the why. At minimum:
   an item is stuck · I want to start over · something is running that should not be ·
   the daemon looks dead · the disk is full.
3. **The state table** — every `WorkItemState`: what it means, what is legal from it, what it
   becomes next.
4. **The command table** — every subcommand: what it does, when to reach for it, what it
   refuses, and **Web** or **Terminal** for where it is reachable.
5. **Where things live** — the existing path table, kept.
6. **The web interface** — how to start it, how to reach it from the phone, and the
   no-authentication warning, which stays in full. A safety statement is not "reasoning".
7. **Reading the logs** — the commands, and a pointer to `audit-log.md`.
8. **Where the why lives** — links to the narrative pages.

## The two tables

**State table** — one row per `WorkItemState` member, no omissions:

| Column | Content |
|---|---|
| State | the value as it appears in `status`, `show` and the web |
| Means | one sentence |
| You can | the commands legal from it, by name |
| Becomes | the states it can move to |

**Command table** — one row per subcommand the argument parser defines:

| Column | Content |
|---|---|
| Command | as typed, including subcommands like `worktree remove` |
| Does | one sentence |
| Reach for it when | the situation |
| Refuses | the guard that stops it, in a few words |
| Where | `Web`, `Terminal`, or `Both` |

## Enforced by test (FR-024, SC-008)

`tests/unit/test_operating_reference.py` parses both tables and asserts:

- the set of states in the state table equals the set of `WorkItemState` members;
- the set of commands in the command table equals the set of subcommands the CLI parser
  defines, compared against the parser rather than a hand-kept list;
- the `Where` column holds only `Web`, `Terminal` or `Both`;
- the recipes section names each of the five required situations.

A verb added without a row fails the test on the day it is added. That is the whole point.

## Where the displaced reasoning goes

| Currently on `operating.md` | Moves to |
|---|---|
| why blockers are re-checked live, and how to read a `blocked` line | `3-selection.md` |
| why cleanup keeps what it keeps, and what a retained branch means | `5-outcome.md` |
| what survives a reboot, and what an interrupted-at-X leaves behind | `state.md` |
| why the web interface is a separate process, and how it uses the window | condensed in place — `operating.md` owns the web interface per `CLAUDE.md` |
| the audit record's format and every action name | already on `audit-log.md`; the page links |

Nothing true is deleted outright. Reasoning is moved where a narrative page already owns the
stage, condensed where this page owns it, and dropped only where a table now says the same
thing in fewer words.

## Size

Materially shorter than the 593 lines it replaces, and majority table and recipe. No line
ceiling is asserted in a test — the README's ceiling exists because the README had become the
documentation, which is not this page's failure mode. This page's failure mode is drift, and
drift is what the table test catches.
