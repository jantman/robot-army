# Configuration

One file, `~/.config/robot-army/config.toml`, never written by the daemon.

## Getting a complete one

```bash
uv run robot-army example-config --output ~/.config/robot-army/config.toml
```

That writes a fully commented file with **every** option in it, at its default, with a
one-line explanation on each key. Copied verbatim it polls no board, notifies nobody, and
deletes nothing — every outward-facing behaviour is off until you turn it on.

Two values are actually yours:

- `[github] author` — the login whose issues may be dispatched. Blank is a hard error; there
  is no "any author" value and no way to disable the check.
- `[paths] repo_root` — where your clones live. It must exist before the daemon will start.

The same file is committed at
[`share/config.example.toml`](https://github.com/jantman/robot-army/blob/main/share/config.example.toml),
so you can read it without running anything. It is generated, not hand-written: a test fails
if the committed copy stops matching what the command produces, and the command itself
refuses to run if a key exists in the loader that nobody has documented. That is the
mechanism, and it exists because the previous hand-maintained example silently fell three
sections behind.

Writing to a file is refused if something is already there:

```bash
uv run robot-army example-config                     # to stdout
uv run robot-army example-config --output PATH       # exit 3 if PATH exists
uv run robot-army example-config --output PATH --force
```

## How it is validated

Everything at once. A load reports **every** problem it found, not the first — fixing one
typo per restart is a poor experience at 2am.

**A literal credential in this file is an error, not a warning.** The repository is public,
and a config that "works" with a token in it is a config that will eventually be pasted
somewhere. Credentials come from an environment variable named by a `*_env` key, or from a
mode-0600 file named by a `*_file` key.

**An unknown key is a warning at the top level and an error inside**
`[repos.*]`, `[trello]`, `[dispatch]`, `[cleanup]`, `[notifications]`, `[speckit]` and
`[pushover]`. The rule is the same in both places: a typo in a section that exists is a
setting that quietly does nothing, which is worse than a setting that is missing, because it
looks applied. In those seven the consequence is bad enough to refuse the file.

## The sections

| Section | What it decides | Covered on |
|---|---|---|
| `[paths]` | where clones, worktrees, state and sockets live | below, and [operating](operating.md#where-things-live) |
| `[github]` | the issue source, the label, the author gate, the token | [setup](1-setup.md) |
| `[worker]` | how a session is launched: permission mode, model, branch naming | below |
| `[dispatch]` | order, per-repository session default, `wait_for_merge`, board ordering | [what runs next](3-selection.md) |
| `[daemon]` | loop timings, effect level, the global session cap | [what runs next](3-selection.md#how-many-sessions-run-at-once) |
| `[speckit]` | whether sessions are told about the lifecycle, and what each command carries | [what a session is told](4-session.md#when-a-repository-uses-spec-kit) |
| `[trello]` | the optional intake board | [where work comes from](2-intake.md#the-intake-board) |
| `[notifications]` | which events are worth saying out loud, and the per-burst bound | [what happens after](5-outcome.md#being-told-when-something-happens) |
| `[pushover]` | the second notification channel's two credential files | [what happens after](5-outcome.md#being-told-when-something-happens) |
| `[cleanup]` | whether closing an issue reclaims its worktree and branch | [what happens after](5-outcome.md#cleaning-up) |
| `[hooks]` | preparation steps run in a new worktree | below |
| `[terminal]` | how kitty is found and driven | [setup](1-setup.md#two-things-that-are-easy-to-overlook) |
| `[web]` | `robot-army serve`'s address, port and refresh | [operating](operating.md#the-web-interface) |
| `[health]` | when the heartbeat counts as stale, and the webhook URL | [operating](operating.md#noticing-it-has-died) |
| `[repos.*]` | per-repository exceptions | below |

### `[paths]`

```toml
[paths]
repo_root     = "~/GIT"        # must exist; a repo's default clone is <repo_root>/<name>
worktree_root = "~/worktrees"  # where per-issue checkouts are created
# state_dir   = …              # defaults under $XDG_STATE_HOME
# socket_dir  = …              # defaults under $XDG_RUNTIME_DIR
```

`repo_root` is validated at load rather than at onboarding time, so a missing clone root is
one message alongside every other problem — not 227 identical refusals discovered one
repository at a time.

### `[worker]`

```toml
[worker]
permission_mode = "auto"          # acceptEdits, auto, bypassPermissions, manual, dontAsk, plan
model           = ""              # empty means the worker's own default
# base_branch   = "main"          # only for a clone that cannot say; see below
branch_prefix   = "robot-army"
binary          = "claude"
```

`permission_mode` is the one to think about. `auto` is what lets a session work unattended,
which is the point of the whole system, and it is also the setting that decides how much it
can do without asking.

### The base ref

The branch new work is cut from, that the committed `.claude/settings*.json` are read at,
and that "has this landed?" is asked against. **It comes from the repository**, not from
this file. Four rungs, first one that answers:

| Rung | Where | When it decides |
|---|---|---|
| 1 | `[repos."owner/name"] base_branch` | I said so about this repository |
| 2 | the clone's `refs/remotes/<remote>/HEAD` | almost always — every clone that has ever fetched has it |
| 3 | `[worker] base_branch` | the clone could not say |
| 4 | `main` | nothing said anything |

Detection is a local ref read: no network, no token, nothing written. `onboard` prints which
rung answered, and so does the `repo.onboard` record.

**Rung 2 outranks rung 3 deliberately**, and that is the one surprising line here. Until
issue #150 the base ref was `[worker] base_branch`, whose default is `"main"` — so
onboarding `jantman/biweeklybudget`, whose default branch is `master`, printed
`base ref : main`, reviewed the settings at a ref that does not exist, and recorded that
nothing as approved. The obvious fix — let an explicitly written value win — fixes it for
nobody: `base_branch = "main"` shipped **live** in the example configuration, so my own
`config.toml` says `"main"` because I copied it, not because I chose it. A value copied is
indistinguishable from a value chosen, so the global key steps aside for the repository's
own answer and the example no longer writes it out.

Set `[repos."owner/name"] base_branch` for the repository that really does branch off
something else. That one wins over everything, because it is a statement about the
repository in front of you.

### `[hooks]`

Preparation steps run in a fresh worktree before the session starts. A step is one of `run`,
`link` or `copy` — `link` and `copy` are first-class rather than shell commands so they stay
idempotent and readable.

```toml
[hooks]
default_timeout_seconds = 300
post_create = [ { run = "uv sync", timeout = 120 } ]
```

A repository's own `post_create` **replaces** these rather than extending them. There is
deliberately no way to ask for both: the repositories that need their own steps need
*different* steps, and appending would make the shared default impossible to opt out of.

### `[repos.*]` is for exceptions

A section is a set of **overrides**, not a registration. Nothing needs one — `onboard` is
what makes a repository known. Write one when a repository is genuinely unusual:

```toml
# The derived path holds upstream's clone, not mine.
[repos."jantman/zoneminder"]
path = "~/GIT/jantman-zoneminder"

# This one needs something other than the shared step.
[repos."jantman/some-node-thing"]
post_create = [ { run = "npm ci", timeout = 300 } ]

# Everything else: nothing here at all. Onboarded, and that is enough.
```

A section with no onboarding record is not a repository this system watches, and
`robot-army repos` says so rather than listing it as known. Changing a `path` after
onboarding does not silently take effect either — dispatch blocks and names
`onboard --reapprove`, because which repository is acted upon is not a setting to change
behind my back.

Every key: `path`, `base_branch`, `permission_mode`, `model`, `max_sessions`, `priority`,
`wait_for_merge`, `project_ordering`, `project`, `project_column`, `speckit`,
`speckit_commands`, `post_create`, `env`. Each overrides the matching global setting for
that repository alone, and each is explained on the page for the stage it affects.

**Do not put a credential in `env`.** Those values are passed to kitty as command
arguments, so any local process can read them from `/proc/<pid>/cmdline` while the session
starts.

## The example, in full

Everything above is a tour of what each section decides. The file itself — every key, at its
default, with its own one-line comment — is one command away, and is committed so it can be
read without running anything:

```bash
uv run robot-army example-config | less
```

[`share/config.example.toml`](https://github.com/jantman/robot-army/blob/main/share/config.example.toml)

It is deliberately **not** pasted into this page. A copy here would be a second thing to
regenerate, and a second thing to regenerate is how the last example config ended up three
sections behind the loader. There is one copy, one generator, and a test that fails when they
disagree.
