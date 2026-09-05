# robot-army

A single-user daemon that turns labelled GitHub issues into real, interactive Claude Code
sessions in the terminal I already have open.

This is written for my future self. It is public so it can be read, not so it can be
adopted — there is no support, no stable API, and no packaging beyond a local
`pyproject.toml`. See [the constitution](https://github.com/jantman/robot-army/blob/main/.specify/memory/constitution.md).

## What it does

I label an issue I wrote. Within a couple of minutes, without touching a terminal:

1. The daemon polls GitHub, sees the label, and checks the issue is mine.
2. It creates an isolated git worktree on a new branch and runs that repository's
   preparation steps.
3. It launches a real interactive session into the running kitty instance, hosted by
   `dtach` so it survives the terminal dying.
4. It waits for proof the session actually started — a launch call returning success is
   not proof — and only then records the item as `active`.
5. When the session ends, a wrapper writes its exit status to a spool file the daemon
   drains. That file survives the daemon being down.

Issues can also start life as a card on a private Trello board, so I can capture a task from
my phone. That path files an issue and stops: labelling it is still mine to do.

## The pipeline, and this guide

These pages follow one issue through the system, in the order the system touches it. When
something is not happening, the stage it is stuck at is where to look.

| Stage | Page | What it covers |
|---|---|---|
| — | [Setup](1-setup.md) | Install, the token, the config file, onboarding a repository, and how to try it all without consequences |
| ① Intake | [Where work comes from](2-intake.md) | The label, the Trello board, cards that do not say enough |
| ② Selection | [What runs next](3-selection.md) | Concurrency, serial working, project-board order, holds, pausing |
| ③ Session | [What a session is told](4-session.md) | The prompt, Spec Kit, previewing, attaching |
| ④ Outcome | [What happens after](5-outcome.md) | Issue comments, notifications, cleanup |

And two pages that are not a stage:

| Page | What it covers |
|---|---|
| [Operating it](operating.md) | The web interface, the logs, where state lives, health, recovery, what to do when something looks wrong |
| [Configuration](configuration.md) | Every section and key, and the generated example config |

Two deeper references, linked from the pages above where they matter:

- [The audit log](audit-log.md) — record shape, every action name, and how to answer
  "what happened?" from the log alone.
- [State](state.md) — every path, every table, what survives a reboot, and the
  interrupted-at-X table.

## Design notes

The reasoning lives in `specs/001-minimum-daemon/`:
[research.md](https://github.com/jantman/robot-army/blob/main/specs/001-minimum-daemon/research.md)
records twenty decisions with their rejected alternatives,
[plan.md](https://github.com/jantman/robot-army/blob/main/specs/001-minimum-daemon/plan.md)
carries the constitution check, and
[data-model.md](https://github.com/jantman/robot-army/blob/main/specs/001-minimum-daemon/data-model.md)
has the state machines and the "interrupted at X → result on next start" table.

Three implementation details are counter-intuitive enough to be worth naming here, because
each looks like a bug to a reader who does not know why:

- **`dtach` takes no `--` separator.** It rejects one outright. The wrapper needs its own.
- **The wrapper does not `exec` the worker.** `exec` would replace the shell and the exit
  code could never be captured, which is the wrapper's entire reason to exist.
- **`git worktree remove` is never given `--force` by default.** Git's refusal to remove a
  dirty worktree is the guard, not an obstacle.
