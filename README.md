# robot-army

⚠️☠️☢️🚨 **DANGER: Entering Vibe Land!** This is entirely vibe coded by Claude and reviewed by Claude. I've barely looked at a single line of the code. You probably don't want to ever run this if you're not me. 🚨☢️☠️⚠️

A single-user daemon that turns labelled GitHub issues into real, interactive Claude Code
sessions in the terminal I already have open.

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
my phone — see [the intake board](#the-intake-board). That path files an issue and stops:
labelling it is still mine to do.

## Running it

```bash
cd ~/GIT/robot-army
uv sync
uv run pytest                  # the suite must pass

mkdir -p ~/.config/robot-army
$EDITOR ~/.config/robot-army/config.toml     # see specs/001-minimum-daemon/contracts/config.md
export ROBOT_ARMY_GITHUB_TOKEN=ghp_...       # a CLASSIC PAT; see below

uv run robot-army doctor       # run this first, every time
uv run robot-army onboard jantman/some-repo
uv run robot-army run
```

### The token has to be a classic one

**Use a classic personal access token with `repo` and `read:project`.** Not a fine-grained
token, and this is not a preference.

GitHub has an *organisation*-level Projects permission for fine-grained tokens and **no
account-level one**. A user-owned board — anything at `github.com/users/<you>/projects/N`,
which is what you get by clicking "New project" on your own account — therefore cannot be
read by a fine-grained token however it is configured. There is no setting that fixes it;
the permission does not exist. See [community
#156512](https://github.com/orgs/community/discussions/156512).

Without `read:project` everything still works except [board
ordering](#ordering-work-from-a-project-board), which is skipped with the reason recorded.
`doctor` tells you which kind of token you are holding and what it is missing, so this is
one line of output rather than an afternoon:

```
[ok]   project: demo token             classic token can read projects
[FAIL] project: demo token             this looks like a fine-grained token or GitHub App …
```

### Adding a repository

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

### `[repos.*]` is for exceptions

A section is a set of **overrides**, not a registration. Nothing needs one. Write one when a
repository is genuinely unusual:

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

Two things are easy to overlook:

- **kitty must be running with a control socket.** `kitty.conf` needs
  `listen_on unix:/tmp/mykitty` and `allow_remote_control yes`. Kitty appends its PID, so
  configure the **glob**, never a fixed path.
- **Start the daemon by hand after graphical login**, not at boot. A daemon started before
  login has no display environment and no kitty to launch into.

`doctor` catches the failure that cost the most time during the spike: a kitty instance
carrying `CLAUDE_CODE_CHILD_SESSION` in its environment silently disables transcript
saving, producing sessions that look perfect, exit 0, and can never be resumed.

## The web interface

A second front end onto the same operations, so I can see what is running and decide an
interrupted item from my phone without opening a terminal.

```bash
uv run robot-army serve        # http://127.0.0.1:8420, the shipped default
```

**Two processes, started by hand after graphical login, in either order.** The interface is
deliberately separate from the daemon: it starts, stops, and survives on its own, so the
audit log and the interrupted list stay readable during exactly the incident that makes them
worth reading.

```bash
uv run robot-army run &        # the daemon
uv run robot-army serve        # the interface
```

To reach it from the phone, name the machine's LAN address:

```toml
[web]
bind = "127.0.0.1"      # the LAN address, or 0.0.0.0 for every interface
port = 8420
refresh_seconds = 10    # how often an open page re-fetches itself
```

### Read this part

**There is no authentication, and that is deliberate.** The operating-system user stops being
the trust boundary the moment this binds to anything but loopback — the network becomes the
boundary instead. **Anything that can reach that port has full control of robot-army**: it can
resume sessions, cancel them, abandon work, hold and release items and repositories, and
pause dispatch.

That is the accepted model, so the mitigations are the ones that matter:

- The default is loopback. Widening it is an explicit edit to the config.
- A **globally routable** bind address is refused outright, exit `3`. The interface will not
  start somewhere the internet can reach it.
- The effective address is printed at startup and written to the audit log as `web.start`, on
  every start, with a loud warning when it is not loopback. That is the one fact about this
  design that is never allowed to be silent.
- A state-changing request that a **browser** reports as coming from another site is refused
  with `403`. This is the one attack the model above does not already accept: a forged
  request needs no network path to the port at all, only my own browser — already inside the
  trust boundary — having some unrelated page open while the interface is running. It is not
  authentication; it identifies nobody, holds no state, and asks one question. Clients that
  send neither `Origin` nor `Sec-Fetch-Site`, `curl` included, are allowed through: they can
  reach the port directly anyway, which is the model above.
- **Reach it by address, not by name.** Any request whose `Host` is a hostname other than
  `localhost` is refused with `403`. Comparing `Origin` to `Host` is not enough on its own,
  because DNS rebinding lets an attacker control both: point `evil.test` at `127.0.0.1`, get
  my browser to load `http://evil.test:8420`, and every header agrees with every other while
  the request really lands here. Rebinding needs a *name*, so requiring an address closes it
  — and `[web] bind` already has to be an address for the same reason.
- **No page of it may be put in a frame.** Every response sends `X-Frame-Options: DENY` and a
  `Content-Security-Policy` beginning `frame-ancestors 'none'`, because framing walks straight
  past the same-origin check above: the form a baited click submits belongs to the framed page
  itself, so the browser reports `Sec-Fetch-Site: same-origin` and a matching `Origin`, and the
  check passes *honestly*. Nothing about the request distinguishes it, so the frame is refused
  rather than the click. The same policy adds `default-src 'self'`, `base-uri 'none'` and
  `form-action 'self'` — free here, because these pages load nothing external by design: no web
  font, no CDN, no icon set, no inline script or style. Every response also sends
  `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`, the latter so that
  following a `github.com` or `trello.com` link out of a view does not hand it this
  interface's address — `same-origin` and not `no-referrer`, because a refused control's
  page builds its "back to" link from the `Referer` of my own POST.

From outside the house I connect my existing VPN and use the same LAN address. Nothing is
published, no tunnel is configured, and no port is forwarded.

### What it can do

Six views — active, queue, interrupted, one item, anomalies, and the audit log — and the
controls for the decisions I actually make away from the desk: resume, restart, abandon,
cancel, retry, attach a terminal, acknowledge an anomaly, hold and release an item or a whole
repository, pause and resume dispatch, and force a poll or a reconciliation. Every one of them has a terminal equivalent, verified by a test
rather than by intention.

Resume and restart here obey the session cap, the pause and holds exactly as the terminal
does, and say so on the page rather than appearing to work and then quietly doing nothing.
There is no `--force` button: the answer to a refusal is the control that lifts the
condition, which is one press away on the same page and leaves the queue agreeing with the
button instead of overridden by it.

Deliberately **not** there: repository onboarding and permission re-approval, removing a
checkout or its branch, purging simulated rows, changing the concurrency limit, and anything
that starts or stops the daemon. Each stays a terminal command.

Add `.json` to any path, or send `Accept: application/json`, for the same facts as a payload:

```bash
curl -s localhost:8420/active.json  | jq '.items[] | {id, repo_key, state, title}'
curl -s localhost:8420/queue.json   | jq '.counts'
curl -s 'localhost:8420/log.json?item=42&outcome=error' | jq '.records'
```

It is not a stable API. It is versioned by the commit that produced it.

Nothing is fetched from a third-party host — no web font, no CDN, no icon set — so every view
works with the machine offline. Every page renders on a phone in a single column, and works
with scripting disabled, merely static until reloaded.

## The intake board

Optional, and **absent by default** — an installation with no `[trello]` section makes no
board request at all and behaves exactly as it did before this existed.

I put a card on a private Trello board from my phone, tag it, and it becomes a GitHub issue
in the repository the card names. That issue is **unlabelled**, so nothing runs: labelling it
is still the human gate, and the board cannot reach past it. The card then follows its issue —
into the in-progress list while a session runs, into the done list when the issue closes, and
back where it came from if the work is abandoned.

```toml
[trello]
board_id         = "5f3a..."        # required when the section is present
label            = "AI-task"        # the tag that marks a card as work
in_progress_list = "In Progress"
done_list        = "Done"
ignore_lists     = ["Icebox"]       # columns whose cards are NOT intake; empty by default
poll_seconds     = 300              # slower than GitHub's 60, deliberately

key_env          = "TRELLO_API_KEY"     # the NAME of a variable, never the value
token_env        = "TRELLO_API_TOKEN"   # or key_file / token_file, mode 0600
```

```bash
export TRELLO_API_KEY=...     # https://trello.com/power-ups/admin
export TRELLO_API_TOKEN=...
uv run robot-army doctor      # now checks the board too — every check must be green
uv run robot-army cards       # what is on the board and what became of it
```

`doctor` verifies at startup, and refuses to *ingest* — not to run — if any of these fail:

- The board is reachable and the credentials work.
- **The board is private.** A public board is not a person I chose, and board access is the
  only authorization this path has.
- The configured tag exists. A renamed label produces zero matching cards, which is
  indistinguishable from an empty board — the system would sit there looking healthy and
  doing nothing.
- Both lifecycle lists exist. A missing one is otherwise discovered halfway through a
  lifecycle, after the issue already exists.
- Every column named in `ignore_lists` exists. This one refuses for a reason worth stating:
  a warning would leave intake silently widened back to what it was, the excluded column
  would start filing issues, and nothing would look broken.

The board's **member list is recorded and never gated on**. Who else may see my own private
board is my decision, and a second member can at most cause an unlabelled issue to be filed —
only I can cause one to run.

### Parking a card

`ignore_lists` names columns whose cards are **not** intake. A tagged card sitting in one
produces no issue, no comment and no move. Drag it out and it is picked up on the next poll —
no re-tag, no rescan, no restart.

That reversibility is the whole point. Before this, the only way to stop a tagged card being
filed was to remove the tag: a one-way answer about what the card *is*, to a question that is
almost always about *when*. Columns are where I already say when. So an icebox column is a
parking space, and parking spaces have to work in both directions.

It gates **intake only**. A card that already has an issue is untouched in either direction —
its mapping, its session and its remaining board moves all continue — which is also why
listing `in_progress_list` or `done_list` here is harmless rather than contradictory: by the
time the daemon puts a card in either, that card is already linked.

A parked card shows as `parked in 'Icebox'` in `robot-army cards` and on `/cards`, *alongside*
whatever else it is rather than instead of it: a card can be awaiting clarification and parked
at once, which is exactly what writing a vague card and shelving it produces. The poll record
counts them (`{"tagged": 140, "ignored": 100, ...}`) and logs one line when a card is parked
and one when it is released — not one per card per cycle, which on a full icebox would be the
majority of the log saying nothing happened.

### When a card doesn't say enough

A card that names no onboarded repository, or names two, is **held** rather than guessed at.
It gets one comment saying what is missing, it appears in `robot-army cards` and on `/cards`
with its reason, and editing the card to name a repository resolves it on the next pass with
no further action.

```bash
uv run robot-army cards --state needs_info
uv run robot-army rescan <card-id>          # or --all-needs-info
```

A repository reference only counts if it is **already onboarded**. A card description is
often pasted from a log, and `src/robot_army` and `docs/roadmap.md` both look exactly like an
`owner/name`; filtering against the onboarded set means an unknown reference cannot select
anything, so the worst case is a held card rather than an issue filed somewhere I never named.

### One card, one issue

Enforced by two unique indexes rather than by code that has to remember, so a path that
skipped its check fails loudly instead of quietly duplicating. Creation is four steps with the
intent written first, and every seam between them is separately resumable — including the
dangerous one, where the issue exists and nothing local knows it yet.

If the database is lost entirely, each card's own comment names its issue, and the next poll
rebuilds the mapping from it rather than filing a second one. The one gap left open is a crash
between creating the issue and recording it *combined with* losing the database, and it is
written down in [docs/state.md](docs/state.md) rather than pretended away.

## What every session is told

Two things I was writing into issues by hand, or not writing and regretting. Every dispatched
prompt now carries them, in every repository, with nothing to configure and no file to add:

- **The work ends pushed, with a pull request open.** Stay on the feature branch the worktree
  was made on, and when the work is done, commit, push to `origin`, and open a PR. Commits on a
  branch nobody fetched are the one thing `worktree remove` can destroy, which is why the
  cleanup guards are as paranoid as they are — this is the same problem addressed a step
  earlier.
- **The repository is the mechanism, not the record.** Where a repo is how a thing gets changed
  — configuration management, infrastructure as code, deployment or schedule definitions — an
  issue asking for that thing is asking for the code that produces it. "Set up and run this
  service", filed against a Puppet repo, means write the manifest and open the PR. Doing it by
  hand also works, and is worse than not doing it: invisible to review, absent from the history,
  and gone the next time Puppet runs.

The second one is deliberately drawn at *bypassing the repository*, not at touching a system. A
rule against changing any system state would forbid the push and the PR the first instruction
demands, forbid running the test suite, and still not explain the Puppet case — so it would need
an exception list, and an exception list is how you know a rule is drawn in the wrong place. The
block says so in one sentence: build, run, test, install dependencies, start things locally,
read live systems, push, open the PR. The limit is on reaching past the repository to change a
live system where a change to the repository is what was asked for.

**The issue does not outrank them.** It used to: the block closed by saying that an explicit
instruction in the issue body wins, and named the three overrides it covered — no pull request,
a commit straight to the default branch, an action on a system. That was sound reasoning about
*my own* issues, and it stopped being sound the moment somebody else's text could occupy that
slot. It is also, read back, a list of exactly what an injected paragraph would ask for, granted
in advance, in a session running `--permission-mode auto`. The paragraph is gone.

What is left says the opposite: the issue says what to do, it does not decide how the work is
delivered, and the rules hold however the issue is worded. The rules bind the *manner* of
delivery rather than asserting there is something to deliver, so "investigate why the poller
stalls and report back" is unaffected — there is nothing to commit, so nothing to push.
"Delete the stale worktrees" is the case that really did lose something, and the answer is that
an instruction like that has to come from somewhere its author does not control: a repository's
own `.claude/robot-army.md`, which is prepended above everything and still outranks all of it,
or a session I start by hand.

**And the issue's own text is fenced.** Everything the issue's author wrote — title, labels,
body — is wrapped in a delimiter carrying sixteen random hex characters generated per dispatch,
under a paragraph saying the contents are untrusted data describing a task and not instructions
to follow. A body can emit `---`, or a `**Title**:` line, or a paragraph in the register of a
repository's standing instructions; none of it reaches outside the fence, and the closing
delimiter cannot be guessed by whoever wrote the text it closes. Control characters are
stripped from the title and body on the way in, so an escape sequence in an issue body cannot
rewrite the terminal of whoever is reading the session. The issue's URL is still in the prompt,
because I need it to find the thing — annotated as an identifier rather than as somewhere to
read from, since the page it points at renders comments from anyone who can reach the
repository.

Nothing checks any of it. Whether a session actually pushed and opened a PR is a question the
tools that already answer it still answer:

```bash
uv run robot-army show <id>       # uncommitted changes? commits on the branch? PR open?
```

### Reading a prompt before it is sent

Everything above describes what goes into a prompt. This prints one:

```bash
uv run robot-army prompt jantman/some-repo 42
```

It composes exactly what a dispatch of that issue would hand the session — the repository's
own `.claude/robot-army.md` if it has one, the Spec Kit block if it applies, the delivery
rules, and the fenced issue — and writes it to stdout and nothing else, so it redirects and
diffs cleanly. Everything explanatory, including which directory the repository's instructions
were read from, goes to stderr.

Two runs of it differ in exactly one thing: the fence delimiter, which is random per compose by
design. Diff two previews of the same issue and the four lines carrying it — the two markers,
and the two that name them — are the whole of the difference.

The issue does not have to be labelled, eligible, open, or known to the system: any issue
number in an onboarded repository works, which is the point — it answers "what would this
session be told?" before there is a session. Nothing is created by asking. No worktree, no
branch, no work item, no comment on the issue; the only trace is one line in the audit log.

For an issue that already has a worktree the prompt is read from *that worktree*, so it
answers what that session was told rather than what a fresh one would be. For everything else
it reads the onboarded clone, and says so, because a clone can sit on another branch or carry
uncommitted changes and a preview that hid the difference would be worse than no preview.

Exit codes distinguish the failures without reading the message: `2` for a malformed
`owner/repo` or issue number, `3` for a repository that was never onboarded, `1` if the issue
could not be fetched. In every one of those, stdout stays empty.

## When a repository uses Spec Kit

More than half of my work goes through [spec-kit](https://github.com/github/spec-kit), and
before this the only way to tell a dispatched session so was to write it by hand into that
repository's `.claude/robot-army.md` — one file edit per repository, for something the
repository's own contents already state.

Now the daemon notices. If the prepared worktree has `.specify/` **and** the four lifecycle
commands the session would actually run, the prompt gains a fixed paragraph: here is the
lifecycle, the issue is the input to `/speckit-specify`, and here is when the lifecycle is
worth using and when it is not. Nothing was edited in the repository to get that, and every
Spec Kit repository I own gets it from the moment it is installed there.

```toml
[speckit]
enabled = true          # the default; omit the section for the same effect

[repos."jantman/some-repo"]
speckit = false         # ...except here
```

**The judgement stays the session's.** The paragraph says which kinds of change warrant four
phases and which do not, and then says plainly that nothing checks. A typo fix that skips the
lifecycle is a correct outcome, not a stall — it produces an item with no phase, and nothing
anywhere treats that as a failure.

### Telling it how *I* run the lifecycle

That paragraph is true of Spec Kit in general, which is why it can be a constant. How I run
it is not: it changes as my habits change, and putting it in the daemon would mean a code
change and a release to alter a sentence about my own working practice.

So each lifecycle command can carry an instruction I write, and the daemon just carries it:

```toml
[speckit.commands]
specify = "When the specification is written, commit it to the branch before continuing."
plan = "When the plan is written, commit it to the branch before continuing."
tasks = "When the task list is written, commit it to the branch before continuing."
implement = """
when finished with implementation, commit, push the branch to origin, and open a PR. Once \
that's done, monitor the CI jobs on the PR. Once all are complete, use /answer-reviews to \
respond to any reviews. Repeat this until claude reviews with a comment of "No issues \
found. Checked for bugs and CLAUDE.md compliance.".\
"""
```

**Those are examples, not defaults.** Nothing ships configured; an installation that writes
none of this gets exactly the block it got before, byte for byte. What the text *says* is
entirely mine — the daemon never reads it, never checks whether the commands it names exist,
and never records whether a session did any of it.

One repository can differ, on the same override pattern as every other per-repository
setting:

```toml
[repos."jantman/some-repo".speckit_commands]
implement = "when finished, commit and push. Do not open a pull request here."
tasks = ""              # no instruction for /speckit-tasks in this repository
```

An empty string means *none here* — it drops one instruction in one repository without
`speckit = false` removing the whole block. Globally an empty string is a mistake and is
refused, because there it says nothing that omitting the key does not.

The instructions land inside the block, above its closing "the instruction above wins"
sentence, so a repository's own `.claude/robot-army.md` still outranks them. Which setting
supplied each one is recorded on dispatch and shown offline, before I label anything:

```bash
uv run robot-army repos --json | jq '.repos[] | {repo_key, speckit}'
```

The text itself is not written to the log — only the name of the setting that supplied it.
The log has never reconstructed a composed prompt (the issue body isn't in there either), and
recording pages of my own prose beside an omitted issue body would be an odd thing to start
doing.

### Seeing how far it has got

`/active` used to show a session five minutes into `/speckit-specify` and one three hours
into `/speckit-implement` as the same row. Now it shows the stage, and so do
`robot-army status` and `robot-army show`:

```bash
uv run robot-army show 42
#   spec-kit   : plan — specs/007-speckit-extensions (since 2026-08-28T14:02:11Z)
```

This is read from the files in the worktree — the feature directory Spec Kit writes, and the
ticked boxes in its `tasks.md` — so it needs no cooperation from the session and is equally
true of one that ignored every instruction it was given.

The part that took the design work: a fresh worktree of a repository like this one contains
**every feature it has ever shipped**, each with a `tasks.md` full of ticked boxes. So the
set of feature directories present when the worktree is created is recorded, and only a
directory that appears afterwards counts as this item's work. Without that, every item would
report `implement` the instant its worktree existed.

Which repositories this changes is answerable before I label anything, offline:

```bash
uv run robot-army repos        # a spec-kit column: yes / no / off / ?
```

### What it deliberately does not do

**Nothing is written into a worktree, and no `.specify/extensions.yml` is read or produced.**

Spec Kit's extension hooks look like the obvious mechanism here and are not one: a hook is an
instruction the agent chooses to follow, not a callback — nothing in Spec Kit calls out to
anything — so a report that never arrives is indistinguishable from a phase not yet reached.
A design whose failure mode is silence is the one this project has twice gone out of its way
to avoid. The files answer nearly the same question and cannot decline to be true. The full
argument, and the three things that would make hooks worth revisiting, are in
[the 007 spec](specs/007-speckit-extensions/spec.md#out-of-scope).

It also never installs, upgrades, or repairs Spec Kit in a repository. Detection reads; it
does not fix.

## How many sessions run at once

The cap counts **every** Claude session running as me, not just the ones the daemon
started. My own terminal sessions share the same subscription, so a cap that ignored them
protected nothing on the machine where I actually work.

```toml
[daemon]
max_concurrent_sessions = 2         # the whole machine, mine included

[dispatch]
order = "oldest-first"              # or "repo-priority"
default_repo_max_sessions = 1       # per repository, unless overridden below
wait_for_merge = false              # globally; see "Working a repository serially" below
project_ordering = true             # globally; see "Ordering work from a project board"

[repos."jantman/example"]           # an override, not a registration — see above
max_sessions = 2                    # optional; overrides the default above
priority = 10                       # optional; higher runs first under repo-priority
wait_for_merge = true               # optional; overrides the default above
project_ordering = false            # optional; overrides [dispatch] project_ordering
```

```bash
uv run robot-army capacity     # total, cap, mine vs. others, per repo, order in force
uv run robot-army status       # the same summary, plus the queue with positions
```

**Set the cap higher than feels right.** The first live `capacity` on this desktop reported
`2 of 2 sessions running, 0 ours, 2 other` — with the daemon having started nothing. Two
sessions of my own is my ordinary working state, so the default of 2 gives the daemon zero
slots and it never dispatches. The cap has to be my usual session count *plus* however many
robots I actually want; 3–4 is the realistic starting point.

Four things worth knowing:

- **One session per repository by default.** Two sessions in one clone share its ports, its
  dev server, and its submodule fetches. A repository that genuinely tolerates two says so.
- **A repository at its cap blocks its own work and nothing else.** A busy repository never
  stalls the queue; the next item in a different repository dispatches in the same pass.
- **When the count cannot be observed, nothing dispatches.** If the session registry is
  unreadable *and* `/proc` cannot be enumerated, the number of live sessions is unknown
  rather than zero — so work is held, an anomaly is raised, and `capacity` exits non-zero.
  A visible stall is a better failure than an invisible over-subscription. The middle case —
  registry unusable, `/proc` readable — counts via `/proc` and says `degraded`. Be aware that
  the `/proc` fallback matches on the binary *name*, so on this machine it also counts Claude
  Desktop: 11 processes where the registry says 2. The over-count is the safe direction, but
  in practice a degraded observation means "permanently at capacity". If the registry becomes
  unreliable, fix the registry.
- **Running work is never touched to reclaim capacity.** Lowering the cap under running
  sessions withholds new dispatch and leaves everything in flight alone.

`repo-priority` drains higher-priority repositories first and breaks ties oldest-first.
There is deliberately no aging: a low-priority repository can wait indefinitely while a
high-priority one keeps producing work, which is what choosing that mode means.

## Working a repository serially

`max_sessions` and `wait_for_merge` answer two different questions and I kept mixing them
up until I wrote this down.

**`max_sessions` counts live sessions.** The moment a session exits, the slot is free and
the next issue in that repository starts — from a default branch that does *not* yet
contain the work that just finished, because that work is sitting in a pull request I have
not looked at. For a repository where every issue touches the same files, that is the
collision, and no session cap can express the wait, because there is no session to count.

**`wait_for_merge` waits for the work to land.** With it on, a repository dispatches
nothing while it has an *unfinished* item — one that has been dispatched at least once and
has not reached `done` or `abandoned`. Merging the pull request closes the issue, a closed
issue makes the item `done`, and the next issue goes out on the following pass. Merging is
entirely mine to do: this never opens, approves or merges anything.

```bash
uv run robot-army status     # the held item, and "#41 is awaiting_review and has not landed"
uv run robot-army capacity   # both limits per repository, and where each one came from
```

Three consequences worth knowing before turning it on:

- **A `failed` item holds the repository too.** It is unfinished work, whatever condition it
  is in. `robot-army retry <id>` or `robot-army abandon <id>` says which — and the hold names
  the item, so I am not left guessing what stopped. `retry` re-reads the issue and re-checks
  its eligibility, author included, so it refuses rather than clearing a hold it should not.
- **It holds one repository, never the queue.** Every other repository dispatches in the
  same pass, exactly as it does under `max_sessions`.
- **It fast-forwards my own clone.** Before creating the worktree, the clone's local default
  branch is advanced to the freshly fetched remote head — see below. Sessions have always
  branched from `origin/<base>`, so this is for *my* clone rather than for theirs.

### The clone fast-forward

Only for repositories with `wait_for_merge` in force, and only ever a fast-forward. It is
skipped, with the reason recorded, whenever the clone has uncommitted changes (untracked
files included), is on another branch or a detached `HEAD`, is mid-rebase, mid-merge,
mid-cherry-pick or mid-bisect, has no such remote, or holds commits on its default branch
that the remote does not. There is no `--force` anywhere in it and nothing is ever reset or
rebased. A skip never fails the dispatch — the worktree comes off the remote-tracking ref
either way.

```bash
uv run robot-army log --json | grep git.fast_forward   # outcome, reason, and the shas
```

## Ordering work from a project board

If a GitHub project is linked to a repository, **it decides the order that repository's
issues run in** — the card at the top of the ready column goes first. Drag a card, and the
next poll follows. Nothing to configure: one linked project with an obvious ready column
is found and used.

```bash
uv run robot-army status       # which board governs each repository, and why not
uv run robot-army doctor       # the token, the project, the column, the freshness
```

**A card parked in another column is held.** Moving an issue to `Backlog` is you saying
"not yet", and it stops dispatching until you move it back — with the reason on screen
naming the column it is in:

```
demo: #48 is in 'Backlog', not the dispatch column 'Ready' — move it there, or set
project_ordering = false for this repository
```

**A labelled issue that is not on the board at all still runs.** That is not an
oversight — an issue nobody put on the board is no signal either way, so it dispatches
after everything the board actually ranked. The board says what to do *first*, not what is
allowed.

Six things worth knowing:

- **The label is still the gate.** Being in the ready column is not enough, and the author
  check is untouched. The board narrows and orders; it never admits.
- **Only within a repository.** Board order never changes how repositories interleave —
  `order` and `priority` keep doing exactly what they did. The set of queue positions a
  repository holds is unchanged; only which of its issues sits at each one moves.
- **`Ready` or `Todo`, found automatically.** GitHub's Kanban template offers `Ready` and
  the simpler board offers `Todo`, so most boards need no configuration. A board with both,
  or with neither, is reported rather than guessed at — set `project_column`.
- **Two linked projects is an error, not a coin flip.** `status` names both and the
  repository keeps dispatching in its configured order until you set `project`.
- **A board that cannot be read never stalls anything.** The order from the last
  successful read stays in force and the queue says how old it is. If no board was ever
  read, nothing is held and nothing is reordered — an unread board grants no authority.
- **A view with its own sort is a trap, and `doctor` watches for it.** GitHub exposes one
  manual ordering per project and no per-view order, so a view sorted by `Priority` shows
  you an order this cannot reproduce. The check fires only when that sort field actually
  has values on cards in your dispatch column — the condition under which what you see and
  what runs can genuinely differ.

To name a board that is not linked, or override the column:

```toml
[repos."jantman/example"]
project = "https://github.com/users/jantman/projects/3"   # or just: 3
project_column = "Ready"
project_ordering = false                                  # or turn it off entirely
```

Turning it off restores the previous behaviour exactly: no board is contacted for that
repository, nothing is held, and the order is whatever `[dispatch] order` says.

## What it writes on the issue

Two machines now run this, and a worktree path is not an address. So when a session is
confirmed, the issue is told where it is:

```markdown
🤖 robot-army dispatched a session for this issue.

- Host: `phoenix`
- Session: `ra-robot-army-38`
- Session id: `2f1c9c3e-6a54-4a0b-9f0d-4c2a1d8e77b1`
- Branch: `robot-army/issue-38-issue-comments-on-dispatch`
- Worktree: `/home/jantman/worktrees/robot-army/issue-38`
```

Both session handles are there because they are the two different things I search with: the
**name** is what appears in the tab title and the `/resume` picker, the **id** names the
transcript, the log records and the exit spool. The **branch** is the link to the pull
request — a PR for this work is opened from it — so an issue, its PR and its session logs
are one chain from either end.

Resume or restart an item and the next comment says so rather than repeating itself:

```markdown
🤖 robot-army reassigned this issue to a new session (attempt 2).
…
- Continues: `2f1c9c3e-…` (that session's context was restored)
```

A restart says `Supersedes:` instead, and says the new session starts without that
session's context — which is the difference between reading the earlier transcript for
context and reading it for facts that no longer apply. If no earlier session is on record
(a rebuilt database), it says that rather than naming one.

A failed attempt gets its own comment naming the host and the reason. Trust is granted per
machine, so "it works on the other one" is a real case and the host line is what makes it
visible.

Three rules hold throughout:

- **Nothing is posted before a session is confirmed running.** The comment is the last
  thing a dispatch does, after the check that a launch really started something — because
  `kitty @ launch` returns success either way.
- **Nothing is ever edited or deleted.** One comment per attempt, in order. That ordering
  is the record.
- **Below `live`, nothing reaches GitHub.** The body that *would* have been posted is in
  the log in full, which is how the wording gets checked without spending a real issue:

  ```bash
  uv run robot-army run --effect-level local --once
  jq -r 'select(.action == "github.comment" and .simulated == true) | .detail.body' \
    ~/.local/state/robot-army/logs/audit-$(date -u +%F).jsonl
  ```

A comment that fails to post is logged and otherwise ignored: GitHub being down is not a
reason to fail a session that is running. Nothing retries it, so a comment lost to a crash
between confirmation and the POST stays lost — `robot-army show <id>` still knows
everything the comment would have said.

## Being told when something happens

Off by default — nothing is sent until I ask. There are two channels, and either, both, or
neither may be configured.

```toml
[health]
webhook_url = "https://ntfy.sh/my-private-topic"   # a generic JSON POST

[pushover]                                          # a push notification on my phone
token_file    = "~/.config/robot-army/pushover-token"
user_key_file = "~/.config/robot-army/pushover-user"

[notifications]
events = ["failure", "needs_info"]  # dispatch | completion | failure | needs_info
max_per_cycle = 5
```

**Both channels get every message.** Adding Pushover does not replace the webhook, and one
channel failing never stops the other — each outcome is recorded separately, so the log can
say "the webhook took it and Pushover did not".

**Why Pushover needs its own channel rather than the webhook.** The webhook posts JSON.
ntfy accepts that; **Pushover does not** — it takes form-encoded parameters and rejects a
JSON body. `health.post_json`'s docstring claimed for two milestones that a generic webhook
covered both, and pointing `webhook_url` at Pushover produced a rejected request rather
than a notification. That is issue #106.

**Getting the two credentials.** The API token comes from an application registered at
<https://pushover.net/apps/build>; the user key is on the account dashboard. Each goes in
its own file, and each file must be mode 0600 — the same rule the GitHub and Trello
credential files follow. Both keys must be set or neither: a half-configured channel cannot
send, so it is a load error rather than a channel that silently never fires. A credential
written into `config.toml` instead of a file is also a load error, because this repository
is public.

At most `max_per_cycle` messages per daemon tick, then one summary naming how many were
held back and of which kinds. The bound is per *burst* rather than per event, because a
backlog produces different items and per-item de-duplication would not bound it at all. It
counts **messages, not deliveries**, so configuring a second channel does not halve how many
things I am told about. Every send is in the audit log whether or not it left the machine.

Messages carry identifiers and state names only. There is no field a credential could
reach, and a test asserts it across a run that includes an authentication failure.

**The stale-heartbeat alert goes to every configured channel too** — the one message that
matters most is the one saying the daemon itself has stopped, and a channel that could not
carry it would be the wrong half. Unlike the notifications above, that alert is *not* gated
by the effect level and never has been: `robot-army health --notify` takes no
`--effect-level` flag, so gating it would silently disable the dead-man's switch whenever
I am running the daemon at `local`.

## Pausing dispatch

```bash
uv run robot-army pause        # or the control on the queue view
uv run robot-army unpause
```

While paused the daemon still polls, evaluates eligibility, reconciles, and heartbeats. It
starts **no new session**, and eligible items accumulate in `ready` — nothing is rejected and
nothing is lost.

The pause is durable: it survives a daemon restart and a reboot, and is cleared only by
`unpause` or the web control. Never by time. It appears in `status`, in `heartbeat.json`, and
on every web view, because a system that is healthy and deliberately doing nothing must not
read as one that is healthy and doing nothing for no reason.

## Holding specific work

```bash
robot-army hold 42                    # one item
robot-army hold --repo owner/name     # every item in a repository, present and future
robot-army unhold 42                  # and the two releases
robot-army unhold --repo owner/name
robot-army holds                      # everything held, with ages
```

Pause stops the queue. A hold stops **named work** and leaves everything else moving — which
is the case I actually hit: four items from a repository I do not care about this week sitting
in front of one I do. Holding the repository takes all four out and lets the fifth dispatch in
the same pass.

The target is stated, never guessed from its shape. An item id is an integer and a repository
key contains a slash, so one argument *could* be classified by looking at it — but a mistyped
key that happened to parse as something else would silently hold the wrong thing. Giving both
or neither is a usage error.

A held item **stays in the queue**, in the position it would occupy anyway, reported with a
`held` reason. It ranks directly below `paused`, because every reason beneath it — a full
machine, an unmerged pull request, a parked card, stale failure residue — would name a fix that
cannot work while I am the one holding it. When an item and its repository are both held, the
one reason says so, and says that releasing one leaves the other in force.

Three things it does **not** do, each on purpose:

- **It never expires.** A hold that lapsed on its own would silently start work I stopped.
- **It never stops a running session.** A hold governs entry into dispatch; `cancel` is what
  stops a session, and the two must not be confusable.
- **It never reorders anything.** Releasing puts an item back exactly where it was.

Holds are durable — they survive a restart and a reboot, and a hold placed while the daemon is
down is honoured on its first pass. They are also *runtime state*, deliberately not
configuration: `[repos.*].priority` with `order = "repo-priority"` is the standing preference I
edit in a file, and a hold is the temporary statement I make from whichever surface is to hand.

The queue view carries a repositories section listing everything with queued work **and**
everything held, each with its own hold or release control. Both halves matter. Without the
first there is nothing on the page that can *place* a repository hold; without the second, a
hold that currently matches no queued item is invisible — and a hold holding nothing looks
exactly like no hold at all, right up until it silently suppresses the next issue I file.

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

## Where things live

| Path | Contents |
|---|---|
| `~/.config/robot-army/config.toml` | configuration (never written by the daemon) |
| `~/.local/state/robot-army/state.db` | SQLite database |
| `~/.local/state/robot-army/logs/audit-*.jsonl` | the audit log |
| `~/.local/state/robot-army/logs/sessions/<item>.log` | per-session wrapper log |
| `~/.local/state/robot-army/spool/exits/` | exit records awaiting the daemon |
| `~/.local/state/robot-army/heartbeat.json` | liveness evidence |
| `~/.local/state/robot-army/daemon.lock` | single-instance lock |
| `~/.local/state/robot-army/requests/` | markers asking the daemon to poll or reconcile now |
| `/run/user/<uid>/robot-army/<item>.sock` | session host sockets |
| `~/worktrees/<repo>/issue-<n>/` | isolated checkouts |

XDG variables are honoured when set. Full detail in [docs/state.md](docs/state.md).

## Reading the logs

```bash
uv run robot-army log --since 10m
uv run robot-army log --item 42
uv run robot-army log --follow
```

Every outward-facing action appears **twice**: an `intent` record before it and an
`outcome` record after, sharing an `action_id`. An intent with no outcome is the signature
of a process killed mid-action. Format and conventions in
[docs/logging.md](docs/logging.md).

Records carry which interface produced them — `daemon`, `cli`, or `web` — and the same log is
readable from the browser at `/log`, filtered, newest first, with GitHub links already made.

## When something looks wrong

```bash
uv run robot-army status               # counts, listings, outstanding anomalies
uv run robot-army show <item-id>       # one item's whole history and resume signals
uv run robot-army anomalies            # things detected but not resolvable
uv run robot-army anomalies --since 1h # …narrowed to a window: 30s, 10m, 2h, 1d
uv run robot-army repos                # why is nothing happening for this repo
uv run robot-army doctor               # environment and preconditions
```

Anomalies worth understanding rather than dismissing:

- **`orphan_session`** — a live worker under the worktree root that no item claims.
  `interrupted` does *not* mean nothing is running: if the wrapper dies uncleanly the
  worker keeps going, reparented, while dtach tears down its socket.
- **`no_transcript`** — the session ran and left nothing resumable. Raised by
  reconciliation five minutes after the session was confirmed, not at dispatch: the worker
  writes its transcript when it starts processing, so asking any earlier reports every
  healthy session. Two causes, and the check cannot tell them apart — the worker never
  saved one (`robot-army doctor` shows whether `CLAUDE_CODE_*` is set in the session host's
  environment), or the session died before writing one (its exit record shows that).
  Either way that session cannot be resumed: `restart` it, do not `resume` it. Raised at
  most once per session.
- **`registry_version_unknown`** — the worker's session-registry format changed. The
  daemon degraded to scanning `/proc` rather than crashing; identification is weaker until
  the version is reviewed.

## Recovering

Nothing resumes automatically — resume, abandon, and cancel are always mine to decide.

```bash
uv run robot-army show <id>       # uncommitted changes? commits on the branch? PR open?
uv run robot-army resume <id>     # new session, prior context restored
uv run robot-army restart <id>    # new session, no prior context
uv run robot-army cancel <id>     # stop that session's process tree and no other
uv run robot-army abandon <id>    # give up; the worktree is left alone
```

**`resume` and `restart` pass the same gate the dispatcher does**, and did not until issue
#120. If the machine is at `max_concurrent_sessions`, if the repository is at its own limit,
if dispatch is paused, or if the item or its repository is held, they refuse — exit `3`, the
reason on stderr in the same words the queue uses, and the item untouched. Lift the
condition and press again; there is nothing to repair in between. To go past it anyway:

```bash
uv run robot-army resume <id> --force   # past the cap, the pause, and the holds
```

`--force` covers my own policy and nothing else. It cannot bypass the issue author check,
workspace trust, the committed settings fingerprint, onboarding, or the state machine, and
it has no configuration equivalent. Every condition it goes past is named in the log as
`dispatch.forced` — written only when something actually applied, so forcing an already
dispatchable item overrides nothing and records nothing. Note that this is a different
`--force` from `cancel --force`, which only skips a confirmation prompt.

The claim on an item is atomic, so a tap on my phone and a terminal command arriving in the
same second cannot both start a session: one wins, the other is told the item was claimed by
another dispatcher. One worktree, one branch, one agent.

Reattach to a running session directly:

```bash
dtach -a /run/user/$(id -u)/robot-army/<item>.sock
```

## Noticing it has died

A dead daemon cannot report its own death, so the checker is a separate process and the
**timer**, not the daemon, is the dead-man's switch.

```bash
cp systemd/robot-army-health.* ~/.config/systemd/user/
systemctl --user enable --now robot-army-health.timer
uv run robot-army health          # exits 4 if the heartbeat is stale or absent
```

## Cleaning up

A prepared worktree was measured at up to 499 MB, so disk is a real constraint — but
deleting work is irreversible, so automatic cleanup is **off by default** and stays off
until I turn it on.

```toml
[cleanup]
on_issue_close = false      # true: reclaim a finished item's worktree and branch
```

```bash
uv run robot-army worktree list             # size, branch, condition, cleanup state
uv run robot-army worktree remove <id>      # refuses if a session is live, or if dirty
uv run robot-army worktree prune
uv run robot-army cleanup                   # every eligible item, under the same guards
uv run robot-army cleanup <id>              # one item, reconsidering a retained decision
```

With `on_issue_close = true`, an item whose issue has closed has its worktree and branch
reclaimed on the next reconciliation pass — provided nothing in either exists only there.
`robot-army cleanup` runs the identical function under the identical guards whether or not
the automatic path is enabled, so the manual route cannot drift from the automatic one.

**The guards are different guards, and that is the whole design.**

- **The session**: is anything still running in there? Asked of the session rows — and of
  nothing else. Not of the work item's state, because the reachable case is a *finished*
  item: the issue closes, the item goes `done`, the worker types on, and terminal is
  exactly the state I reclaim disk from. Not of the process table either, because a row
  whose process I cannot see is still a row nothing has closed, and refusing only on a
  *confirmed* live process would remove the worktree in every case where liveness could not
  be established. `worktree remove` gained this guard in #79, after it deleted a running
  worker's directory and its branch in one command and reported success; `cleanup` had it
  from the start. That was the wrong way round — `cleanup` runs unattended and is
  conservative by design, while `worktree remove` is what I reach for when `/home` is at
  93%, and it is the one that can override git. `--force` still overrides it, and says so
  in the prompt before I type anything.
- **The worktree**: git's own refusal, taken as-is. `git worktree remove` refuses on a dirty
  tree — *including merely untracked files* — and `--force` is never passed. A refused
  worktree is recorded as `retained` with git's own message, and the branch half is not
  attempted, because a dirty worktree means the branch may hold the only copy of something.
- **The branch**: my own containment check, because git's is the wrong one here. `git branch
  -d` accepts only a branch merged into the clone's current `HEAD`; the normal case is a PR
  merged on GitHub while my clone has a stale `main` checked out, so `-d` would refuse every
  time and `robot-army/*` branches would accumulate forever. Instead the branch is deleted
  only if every commit on it is provably on the remote — contained in the published base,
  which is fetched first, or on the remote under its own name, which the **remote is asked
  about during the check**. A remote-tracking ref is not taken as the remote's answer: it is
  a cache of what the remote said last time, a fetch scoped to the base branch neither
  refreshes nor prunes it, and #105 measured a branch deleted on the remote going on proving
  itself "pushed and up to date" from that leftover until a `gc` on the remote made the loss
  permanent. If the remote cannot be asked, or git cannot answer, that is "unproven", never
  "safe", and the branch is kept.

Four outcomes, all visible in `robot-army show <id>` and on the item's web page:

| `cleanup_state` | Worktree | Branch | Meaning |
|---|---|---|---|
| `done` | removed | removed | both guards passed |
| `branch_retained` | removed | kept | containment could not be proved |
| `retained` | kept | kept | git refused the worktree |
| `skipped` | kept | kept | a session was still live — reconsidered next pass |

`skipped` is the only one the automatic pass revisits: it means "not yet", where `retained`
means "we looked and decided no". `worktree_path` and `branch` are kept on the record even
after a successful removal, so "what was at this path?" stays answerable.

## Design notes

The reasoning lives in `specs/001-minimum-daemon/`: [research.md](specs/001-minimum-daemon/research.md)
records twenty decisions with their rejected alternatives,
[plan.md](specs/001-minimum-daemon/plan.md) carries the constitution check, and
[data-model.md](specs/001-minimum-daemon/data-model.md) has the state machines and the
"interrupted at X → result on next start" table.

Three implementation details are counter-intuitive enough to be worth naming here, because
each looks like a bug to a reader who does not know why:

- **`dtach` takes no `--` separator.** It rejects one outright. The wrapper needs its own.
- **The wrapper does not `exec` the worker.** `exec` would replace the shell and the exit
  code could never be captured, which is the wrapper's entire reason to exist.
- **`git worktree remove` is never given `--force` by default.** Git's refusal to remove a
  dirty worktree is the guard, not an obstacle.

## Licence

MIT.
