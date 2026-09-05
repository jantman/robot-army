# Setup

Install it, give it a token, point it at a repository, and — if you want to watch it work
before it can do anything — run it at an effect level where it cannot.

## Running it

```bash
cd ~/GIT/robot-army
uv sync
uv run pytest                  # the suite must pass

mkdir -p ~/.config/robot-army
uv run robot-army example-config --output ~/.config/robot-army/config.toml
$EDITOR ~/.config/robot-army/config.toml     # see the configuration page
export ROBOT_ARMY_GITHUB_TOKEN=ghp_...       # a CLASSIC PAT; see below

uv run robot-army doctor       # run this first, every time
uv run robot-army onboard jantman/some-repo
uv run robot-army run
```

`example-config` writes a fully commented file with every option in it, at their defaults.
Two values are actually yours: `[github] author` and `[paths] repo_root`. Everything else
has a sensible default, and everything outward-facing is off. The
[configuration page](configuration.md) explains each key.

## The token has to be a classic one

**Use a classic personal access token with `repo` and `read:project`.** Not a fine-grained
token, and this is not a preference.

GitHub has an *organisation*-level Projects permission for fine-grained tokens and **no
account-level one**. A user-owned board — anything at `github.com/users/<you>/projects/N`,
which is what you get by clicking "New project" on your own account — therefore cannot be
read by a fine-grained token however it is configured. There is no setting that fixes it;
the permission does not exist. See [community
#156512](https://github.com/orgs/community/discussions/156512).

Without `read:project` everything still works except [board
ordering](3-selection.md#ordering-work-from-a-project-board), which is skipped with the
reason recorded. `doctor` tells you which kind of token you are holding and what it is
missing, so this is one line of output rather than an afternoon:

```
[ok]   project: demo token             classic token can read projects
[FAIL] project: demo token             this looks like a fine-grained token or GitHub App …
```

## Adding a repository

**`onboard` is the whole job.** No file edit, no restart:

```bash
uv run robot-army onboard jantman/some-repo
```

It works out where the clone is — `<repo_root>/<name>`, one candidate, no searching — checks
that the clone actually *is* that repository by reading its `origin`, shows me what it found,
and asks. Onboard a repository while the daemon is running and it is polled on the next cycle.

```toml
[paths]
repo_root = "~/GIT"        # where clones live; a repo's default location is <repo_root>/<name>

[hooks]
post_create = [ { run = "uv sync", timeout = 120 } ]   # the steps every repo gets
```

The origin check is the part that earns its keep. Five of my 252 repositories have a clone of
a *different* repository sitting at the derived path — `~/GIT/zoneminder` is upstream's clone,
not my fork's — and without the check those five would get a branch cut in someone else's
repository. They are refused, by name:

```
$ robot-army onboard jantman/zoneminder
refusing: the clone at /home/jantman/GIT/zoneminder is zoneminder/zoneminder,
          not jantman/zoneminder.
          The path was derived from [paths] repo_root. If your clone of
          jantman/zoneminder is elsewhere, set it explicitly:

              [repos."jantman/zoneminder"]
              path = "/where/it/actually/is"
```

A repository that is genuinely unusual gets a `[repos.*]` section of **overrides** — not a
registration, and nothing needs one. See
[`[repos.*]` is for exceptions](configuration.md#repos-is-for-exceptions).

`[github] include_owned` and `extra_repos` decide what may be **onboarded** — any repository
I own, plus any I list. They are a guard against a mistyped name, not a security boundary;
the issue-author check is that one, and it cannot be disabled.

That check is enforced in one place, `poll.evaluate`, and every path that can put an item in
the dispatch queue goes through it. `retry` is the one that used to not: it re-checked the
repository's conditions and returned the item to the queue carrying its stored,
someone-else-authored body, while the confirmation in front of the button promised the
opposite. It now re-reads the issue from GitHub and re-runs the same verdict, so an item
blocked because somebody else wrote it stays blocked. A second comparison at dispatch, against
the author recorded on the item, is a backstop rather than the gate.

## Two things that are easy to overlook

- **kitty must be running with a control socket.** `kitty.conf` needs
  `listen_on unix:${XDG_RUNTIME_DIR}/mykitty` and `allow_remote_control yes`. Kitty expands
  environment variables there and appends its PID, so configure the **glob**, never a fixed
  path. Under the runtime directory and not `/tmp`: `/tmp` is world-writable, so any local
  user can create a socket matching the glob and — because the daemon takes whichever answers
  first, and keeps it — receive every launch instead of kitty. The daemon refuses a socket it
  does not own, so an existing `/tmp` setup keeps working and is not even flagged: `/tmp` is
  sticky, which stops a stranger swapping an entry. What sticky does *not* stop is them
  claiming the name after kitty exits and frees it, so the daemon re-checks the socket it is
  using and refuses it if it changed hands — a loud failure rather than a silent redirection.
  The runtime directory avoids the whole question, because nobody else can write there at all.
  Not `unix:@mykitty` either: an abstract socket carries no filesystem permissions, so any
  local process can connect to it.
- **Start the daemon by hand after graphical login**, not at boot. A daemon started before
  login has no display environment and no kitty to launch into.
- **A launch is visible in the process table.** The composed prompt and every `[repos.*] env`
  value are passed to kitty as command arguments, so any local process can read them from
  `/proc/<pid>/cmdline` while the session starts. Do not put a credential in `env`.

`doctor` catches the failure that cost the most time during the spike: a kitty instance
carrying `CLAUDE_CODE_CHILD_SESSION` in its environment silently disables transcript
saving, producing sessions that look perfect, exit 0, and can never be resumed.

## Trying it without consequences

Four graduated effect levels, enforced at the boundaries rather than at call sites:

| Level | Polls | Worktrees | Sessions | GitHub writes | Notifications |
|---|---|---|---|---|---|
| `plan` | real | no | no | no | no |
| `local` | real | **real** | no | no | no |
| `no-remote` | real | real | **real** | no | no |
| `live` | real | real | real | **real** | **real** |

Cleanup follows the *worktree* row rather than the GitHub one: simulated at `plan`, real at
`local` and above, because removing a worktree is a local effect. A notification leaves the
machine, so it follows the GitHub row.

Polling and eligibility are always real — a dry run that fakes its reads tells you nothing
about the main thing you want to check.

```bash
uv run robot-army run --dry-run --once          # plan
uv run robot-army run --effect-level local      # debug a repo's preparation steps
uv run robot-army status --include-simulated
uv run robot-army purge-simulated
```

Simulated rows are excluded from every listing unless you ask for them, and are visibly
marked when shown. Every listing that excludes them also says how many it withheld and how
to see them — so `status`, `cards` and `worktree list` never report an empty system while
holding rows back, and `status` in particular never prints a populated queue above a claim
that there is no work.

---

Next: [where work comes from](2-intake.md).
