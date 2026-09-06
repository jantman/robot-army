# Working in this repository

Runtime guidance for coding agents. **[The constitution](.specify/memory/constitution.md)
governs**; where this file and the constitution differ, the constitution wins and this file
is the thing that is wrong.

## What this is

A single-user daemon that turns labelled GitHub issues into interactive Claude Code
sessions. One Linux machine, one user, no deployment infrastructure. Python 3.11+, standard
library first, `uv` for everything.

```bash
uv sync
uv run pytest            # the whole suite; it must pass before anything is complete
uv run robot-army --help
```

Features follow the Spec Kit flow — specify, plan, tasks, implement — and the plan must
carry a Constitution Check. Use it for work that adds or changes behaviour; a typo, a
one-line fix, a dependency bump or a documentation edit does not need four phases, and that
judgement is yours.

## Two things every change has to keep current

These are here because both have rotted before, and both rot silently.

### 1. A change to behaviour updates its guide page

The documentation is [`docs/guide/`](docs/guide/), published by GitHub Pages from the
`/docs` folder. It follows one issue through the system in the order the system touches it,
so **the page to update is the one for the pipeline stage the change affects**:

| Change to… | Update |
|---|---|
| install, tokens, onboarding, effect levels | [`1-setup.md`](docs/guide/1-setup.md) |
| the label gate, Trello intake, card handling | [`2-intake.md`](docs/guide/2-intake.md) |
| ordering, capacity, `wait_for_merge`, board ordering, holds, pause | [`3-selection.md`](docs/guide/3-selection.md) |
| the composed prompt, Spec Kit detection, preview, attach | [`4-session.md`](docs/guide/4-session.md) |
| issue comments, notifications, cleanup | [`5-outcome.md`](docs/guide/5-outcome.md) |
| the web interface, health, recovery, anomalies, paths | [`operating.md`](docs/guide/operating.md) |
| any config key | [`configuration.md`](docs/guide/configuration.md) **and section 2 below** |
| a new audit action, or a record's shape | [`audit-log.md`](docs/guide/audit-log.md) |
| a database table, a state file, or reboot behaviour | [`state.md`](docs/guide/state.md) |

`README.md` is a high-level overview and a pointer to the published guide. **Do not grow it
back.** It was 1,180 lines and being the only documentation there was, which is the problem
`docs/guide/` exists to solve. A test fails if it passes 150 lines.

`docs/roadmap.md`, `docs/incident-*.md`, `docs/verification-*.md` and
`docs/initial-planning/` are project history. Leave them where they are; they are excluded
from the published site by `docs/_config.yml`.

### 2. A change to configuration regenerates the example

Adding, removing or renaming a key in `config.py`'s `_KNOWN_KEYS` or `_REPO_KEYS` means:

1. Add or update its entry in `SECTIONS` in
   [`src/robot_army/exampleconfig.py`](src/robot_army/exampleconfig.py) — a rendered value
   and a one-line comment saying what the key does.
2. Regenerate the committed copy:

   ```bash
   uv run robot-army example-config --output share/config.example.toml --force
   ```

3. Explain it on [`configuration.md`](docs/guide/configuration.md) if it is a key anyone has
   to think about, rather than a timeout with an obvious default.

You will not be able to skip step 1: `render()` refuses to produce a document when a key in
the loader's tables has no annotation, and says which key. You will not be able to skip step
2 either: `tests/unit/test_example_config_drift.py` compares the committed file against a
fresh render and fails naming the command above.

The five rules that decide whether a key renders live or commented out are in
[`contracts/example-config.md`](specs/20260905-124257-docs-overhaul-example-config/contracts/example-config.md).
The short version: a key stays commented when it is one of a mutually exclusive pair, when
it lives in a section whose *absence* is the behaviour (`[trello]`, `[pushover]`), when the
loader validates it against the filesystem, when its default is derived from the
environment, or when it is derived from the repository being acted on (`[worker]
base_branch`, which the clone's own `<remote>/HEAD` outranks). **The generated file must keep
loading clean and configuring nothing outward-facing** — that is what makes it safe to copy,
and it is tested.

## Conventions worth matching

- **Docstrings explain why, not what.** This codebase's modules carry the reasoning for
  decisions that look wrong without it — read a couple before writing one.
- **Commit messages explain why the change was made.** Atomic commits.
- **Unit tests are required** for every new or changed unit of behaviour, and persistence,
  state machines and parsers additionally need failure- and interruption-path tests.
- **Every action that changes state outside the process is logged**, before it happens for
  anything irreversible or outward-facing. An action deliberately left unlogged must be
  named and justified in the feature plan.
- **Writes to persistent state are atomic** — temp file, `fsync`, rename — and every network
  call has a timeout and bounded retries.
- **Nothing outward-facing is on by default.** Notifications, cleanup, and the Trello board
  are all off until configured, and new ones should be too.
- **No credential ever reaches the config file, the audit log, or a `[repos.*] env` value.**
  The repository is public, and `env` values are visible in `/proc/<pid>/cmdline`.

## What not to build

From the constitution, and worth repeating because each is tempting:

- Multi-user anything: accounts, auth, roles. The OS user is the trust boundary.
- Backward compatibility for outside consumers, deprecation cycles, migration shims.
- Contribution guides, issue templates, support channels, end-user tutorials. The
  documentation is written for the author's future self.
- Speculative generality: a configuration knob with one caller, an abstraction with one
  implementation, a dependency that removes less than it costs.
