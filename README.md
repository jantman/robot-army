# robot-army

⚠️☠️☢️🚨 **DANGER: Entering Vibe Land!** This is entirely vibe coded by Claude and reviewed by Claude. I've barely looked at a single line of the code. You probably don't want to ever run this if you're not me. 🚨☢️☠️⚠️

A single-user daemon that turns labelled GitHub issues into real, interactive Claude Code
sessions in the terminal I already have open.

**📖 [Read the guide](https://jantman.github.io/robot-army/)** — everything below is the
one-paragraph version.

This is written for my future self. It is public so it can be read, not so it can be
adopted — there is no support, no stable API, and no packaging beyond a local
`pyproject.toml`. See [the constitution](.specify/memory/constitution.md).

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

There is a web interface (`robot-army serve`) for deciding an interrupted item from a phone,
a Trello intake path, project-board ordering, per-repository concurrency and serial working,
notifications, and guarded cleanup of finished worktrees. All of it is in the guide.

## Getting started

```bash
uv sync
uv run pytest
uv run robot-army example-config --output ~/.config/robot-army/config.toml
uv run robot-army doctor
uv run robot-army onboard jantman/some-repo
uv run robot-army run
```

`example-config` writes a fully commented `config.toml` with every option in it; the same
file is committed at [`share/config.example.toml`](share/config.example.toml). Then read
[Setup](docs/guide/1-setup.md) — the token has to be a classic PAT, and kitty needs a
control socket.

## Documentation

The guide is published at **<https://jantman.github.io/robot-army/>** and its source is in
[`docs/guide/`](docs/guide/):

| Page | Covers |
|---|---|
| [Setup](docs/guide/1-setup.md) | Install, the token, the config file, onboarding, effect levels |
| [① Where work comes from](docs/guide/2-intake.md) | The label, the Trello board, cards that need info |
| [② What runs next](docs/guide/3-selection.md) | Concurrency, serial working, board order, holds, pausing |
| [③ What a session is told](docs/guide/4-session.md) | The prompt, Spec Kit, previewing, attaching |
| [④ What happens after](docs/guide/5-outcome.md) | Issue comments, notifications, cleanup |
| [Operating it](docs/guide/operating.md) | The web interface, logs, health, recovery, anomalies |
| [Configuration](docs/guide/configuration.md) | Every section and key |
| [The audit log](docs/guide/audit-log.md) | Record shape, every action, reconstructing history |
| [State](docs/guide/state.md) | Every path and table, and what survives a reboot |

Design reasoning lives in `specs/`, and [`CLAUDE.md`](CLAUDE.md) carries the working rules
for changes to this repository.

## Licence

MIT.
