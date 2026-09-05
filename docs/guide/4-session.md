# ③ What a session is told

The prompt is composed at dispatch from four parts, in this order:

1. The repository's own `.claude/robot-army.md`, if it has one. This is prepended above
   everything and outranks all of it.
2. The Spec Kit block, if the worktree has Spec Kit installed and the guidance is on.
3. The delivery rules below.
4. The issue itself, fenced as untrusted data.

## The delivery rules

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

## Reading a prompt before it is sent

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
[the 007 spec](https://github.com/jantman/robot-army/blob/main/specs/007-speckit-extensions/spec.md#out-of-scope).

It also never installs, upgrades, or repairs Spec Kit in a repository. Detection reads; it
does not fix.

## Attaching to a running session

```bash
uv run robot-army attach <id>
dtach -a /run/user/$(id -u)/robot-army/<item>.sock    # the same thing, by hand
```

---

Next: [what happens after](5-outcome.md).
