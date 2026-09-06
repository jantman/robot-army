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

### What the approval screen puts in front of you

Between the resolution lines and the prompt, `onboard` prints the **full text** of any
`.claude/settings*.json` committed at the base branch tip:

```
committed tool-permission settings at the base ref:
  These are applied to a dispatched session WITHOUT asking. Read them.

  --- .claude/settings.json ---
  {"hooks": {"SessionStart": [{"hooks": [{"type": "command",
     "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.bmad-loop/hook.py SessionStart"}]}]}}
```

That is the control. A session dispatched into the repository honours those settings without
asking, so a committed `SessionStart` hook runs a script from the repository the moment the
session opens. The hash of what you approved is recorded, and dispatch blocks if it changes
later, naming the files and pointing at `onboard --reapprove`.

It is read from **git, at the base branch tip** — not from the working tree — because that is
what a freshly created worktree will contain.

**This screen is real at every effect level, including `plan`.** It did not used to be: the
read behind it was simulated below `local` and answered "no such file" for every path, so the
screen said `no committed .claude/settings*.json at the base ref` for every repository and an
empty set of hashes was recorded as approved. Rehearsing onboarding at `plan` first — the
natural instinct — was the one way to see nothing at all (issue #20).

If you onboarded anything while that was true, the recorded approval claims the repository
commits no settings. Nothing backfills it, deliberately: an approval means a human read the
file and said yes. The next dispatch blocks instead, names the files as `added:`, and
`onboard --reapprove` shows you the real review.

### The one thing onboarding warns about rather than refuses

If the repository uses Spec Kit and numbers its feature directories by scanning, the approval
screen says so before it asks:

```
spec kit: this repository numbers feature directories by scanning
  feature_numbering is "sequential" in .specify/init-options.json.
  Two sessions running at once scan the same specs/ and cannot see each other's
  worktrees, so both can claim the same number. Nothing here prevents that.
  Set "feature_numbering": "timestamp" in that file to number by time instead.
```

`/speckit-specify` picks a number by scanning `specs/` for the highest one already used — a
scan of **one worktree**. With one worktree per issue, that scan cannot see a number a sibling
worktree claimed ten minutes ago, so two concurrent sessions take the same one. It happened
twice here: `012` and `014` each name two different features.

**Nothing in this daemon can catch that, which is why this is a warning and not a check.** The
losing session's claim exists only as untracked files in another worktree — not on a branch,
not in a ref, not in anything git can be asked about. Widening a search from "this worktree" to
"every ref in the repository" finds nothing and picks the same number. The only thing that
closes the race is `"feature_numbering": "timestamp"` in the repository's own
`.specify/init-options.json`, which names directories by the second and cannot collide.

So it is said once, at the moment I am already reading a screen about a repository and
deciding whether to trust it, and then never again. Onboarding is not blocked, the exit code
does not change, and nothing is written into the repository. Ignoring it is a real choice: a
duplicate prefix breaks no tooling, because Spec Kit resolves a feature by full path. What is
lost is that "spec 012" stops being an unambiguous name for anything.

Two things it deliberately does **not** do. It does not warn about a repository that is not a
Spec Kit project, whatever files are lying around in it — detection has to say yes first. And
it does not report a `.specify/init-options.json` it could not parse as unsafe; that gets its
own wording, because "this is set to scanning" and "I could not read this file" are different
things to be told:

```
spec kit: the feature numbering could not be determined
  .specify/init-options.json: not a JSON object.
  If it does not say "timestamp", two sessions running at once can claim the
  same feature number. Set "feature_numbering": "timestamp" to be sure.
```

This is checked at onboarding only. A repository that installs Spec Kit afterwards is not
warned until the next `onboard --reapprove`, which is a deliberate limit rather than an
oversight — the alternative is a line about numbering on every dispatch, forever, for a
problem this size.

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

Reads are always real — a dry run that fakes its reads tells you nothing about the main
thing you want to check. That covers polling and eligibility, and it covers the onboarding
settings review above: **nothing about approving a repository is reduced below `live`.**

The one nuance is what "read" means at a boundary that also invents things. Asking what is
committed in your clone has a true answer at every level, so it is answered truthfully.
Asking whether the worktree a `plan` run pretended to create is dirty does not, so that is
answered as-if. The rule is *the subject of the question decides, not the verb*, and it is
written down at `SimulatedVersionControl` because the two times it was left implicit, it was
got wrong.

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
