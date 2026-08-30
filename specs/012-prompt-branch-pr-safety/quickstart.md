# Quickstart: Proving A Session Is Told How To Deliver Its Work

Four checks, cheapest first. The last one is the only one that involves the daemon, and it is
the only one that proves the text reaches a real dispatch rather than a function call.

## Prerequisites

```bash
cd ~/GIT/robot-army      # or the worktree this feature is being built in
uv sync
```

## 1. The suite

```bash
uv run pytest
```

Everything must pass, including the golden-string test in
`tests/unit/test_speckit_prompt.py`, whose expected value this milestone changes on purpose
([research.md D5](research.md)). A failure there after implementation means the golden was not
re-captured; a failure there *later*, unexpectedly, means somebody reshaped the prompt sections
without meaning to — which is what the test is for.

## 2. Read the block, and check its size

```bash
uv run python -c "
from robot_army import prompt
print(prompt.DELIVERY)
print()
print(len(prompt.DELIVERY), 'characters')
"
```

Expected: the four paragraphs from
[contracts/delivery-block.md](contracts/delivery-block.md), and a count under 1,500 (SC-004).

Read it as the session will. It should be possible to answer, from the text alone: which branch
the work goes on, what happens at the end, what is not an acceptable way to satisfy the issue,
that pushing and opening a pull request are allowed anyway, that running the tests is allowed,
and what happens when the issue says otherwise (SC-003).

## 3. Compose a prompt and look at the ordering

```bash
uv run python -c "
from robot_army import prompt, speckit
from robot_army.boundaries import Issue

issue = Issue(
    number=29,
    title='Ensure that prompts include PR creation',
    body='The issue body goes here.',
    url='https://github.com/jantman/robot-army/issues/29',
    labels=('robot-army',),
    author='jantman',
    state='open',
)
print(prompt.compose(
    issue,
    repo_key='jantman/robot-army',
    branch='robot-army/issue-29-example',
    instructions='Always run make check.',
    speckit_block=speckit.GUIDANCE,
))
"
```

Expected, top to bottom: `Always run make check.`, the Spec Kit paragraph, the delivery block,
then `You are working on jantman/robot-army issue #29 …` and the body. That is FR-009 (the
repository's own instructions outrank) and the D2 position, both visible in one read.

Then drop `instructions=` and `speckit_block=` and run it again: the delivery block is still
there. That is FR-001 and FR-011 — no repository file, no detection, no setting.

## 4. A real dispatch, simulated, with the prompt read back out of the database

The end-to-end check. Runs against the daemon's own state, changes nothing outward, and proves
the composed text is what was actually launched with rather than what a function returned.

```bash
uv run robot-army doctor                       # first, every time
uv run robot-army run --dry-run                # simulated dispatch; leave it long enough to pick up a labelled issue
```

With an item dispatched, read the prompt back out of the session row — `launch_argv` is the
whole argv chain and the prompt is its final element:

```bash
sqlite3 ~/.local/state/robot-army/state.db \
  "select launch_argv from sessions order by id desc limit 1;" \
  | python -c "import json,sys; print(json.loads(sys.stdin.read())[-1])"
```

Expected: the delivery block is present in the launched prompt, and — the point of doing it this
way — it was already recorded without this feature adding any logging of its own (FR-014,
[research.md D4](research.md)).

Clean up the simulated rows when finished:

```bash
uv run robot-army purge-simulated
```

## What none of this proves

That a session obeys any of it. Nothing checks, by design — the spec puts enforcement out of
scope and the block says so itself. The observable outcome of a real run is answered by the tools
that already answer it:

```bash
uv run robot-army show <id>      # uncommitted changes? commits on the branch? PR open?
```
