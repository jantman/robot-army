# robot-army

A single-user daemon that turns labelled GitHub issues into real, interactive Claude Code
sessions in the terminal I already have open.

⚠️☠️☢️🚨 **DANGER: Entering Vibe Land!** This is entirely vibe coded by Claude and reviewed
by Claude. I've barely looked at a single line of the code. You probably don't want to ever
run this if you're not me. 🚨☢️☠️⚠️

This is written for my future self. It is public so it can be read, not so it can be
adopted — there is no support, no stable API, and no packaging beyond a local
`pyproject.toml`.

## The guide

Start at **[the guide](guide/)**, which follows one issue through the system in the order the
system touches it.

- [Setup](guide/1-setup.md) — install, the token, the config file, onboarding, effect levels
- [① Where work comes from](guide/2-intake.md) — the label, the Trello board, cards
- [② What runs next](guide/3-selection.md) — concurrency, serial working, board order, holds
- [③ What a session is told](guide/4-session.md) — the prompt, Spec Kit, previewing
- [④ What happens after](guide/5-outcome.md) — issue comments, notifications, cleanup
- [Operating it](guide/operating.md) — the web interface, logs, health, recovery
- [Configuration](guide/configuration.md) — every section and key

Two deeper references:

- [The audit log](guide/audit-log.md) — record shape, every action, reconstructing history
- [State](guide/state.md) — every path and table, and what survives a reboot

## The source

[github.com/jantman/robot-army](https://github.com/jantman/robot-army). MIT licensed.
