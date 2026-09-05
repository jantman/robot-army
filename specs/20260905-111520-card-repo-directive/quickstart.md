# Quickstart: proving the declaration works

The unit suite is the real proof; this is the end-to-end walk that shows the behaviour on a
real card, and the commands to reach for when it does not do what you expect.

## Prerequisites

- Two onboarded repositories, so a card can be genuinely ambiguous:
  `uv run robot-army repos` lists what is onboarded.
- A `[trello]` section pointing at the intake board, with the intake label applied to the
  card. Without `[trello]` the board source is inert and none of this runs.

## The whole suite

```bash
uv sync
uv run pytest
```

The cases that matter for this feature:

```bash
uv run pytest tests/unit/test_repo_resolution.py -q     # the grammar and the precedence rules
uv run pytest tests/unit/test_intake_poll.py -q         # the held reason and the audit record
```

## The walk

1. **Make an ambiguous card.** On the intake board, with the intake label, write a card whose
   description mentions two onboarded repositories — a pasted traceback from one and a link to
   the other is the realistic shape.

2. **Watch it be held.**

   ```bash
   uv run robot-army poll
   uv run robot-army cards --state needs_info
   ```

   The card is listed with the reason `this card names 2 onboarded repositories (…); it must
   name exactly one, or say which by a line reading \`robot-army: <repo>\` and nothing
   else`, and a comment saying so has appeared on the card itself.

3. **Add the line.** Edit the card's description and add, on its own line:

   ```
   robot-army: jantman/demo
   ```

4. **Watch it resolve.**

   ```bash
   uv run robot-army rescan <card-id>
   ```

   An issue is filed in `jantman/demo`. It is unlabelled — nothing runs until the label is
   applied by hand, which this feature does not change. The issue body quotes the card's
   description including the `robot-army:` line, which is intentional: it records the choice.

5. **Confirm the record says how it decided.**

   ```bash
   uv run robot-army log --limit 50 | grep trello.evaluated | tail -n 3
   ```

   The record for this card carries `"source": "declaration"` and `"repo_key":
   "jantman/demo"`. Compare with a card that had no line, whose record says `"source":
   "scan"`.

## The failure walks, which are the interesting ones

**A typo'd reference holds the card and says so.** Change the line to
`robot-army: jantmna/demo` and rescan. The card is held again, and both the listing and the
new comment quote `jantmna/demo` back and list what *is* onboarded. It does **not** silently
fall back to guessing from the rest of the card's text — that fallback is the failure the rule
exists to prevent.

**Two lines that disagree hold the card.** Add a second line naming the other repository. The
reason says more than one line was given and names both.

**A declaration inside pasted output cannot reach an unonboarded repository.** Paste a log
containing a line that looks exactly like a declaration and names a repository that is not
onboarded. The card is held. Nothing is filed anywhere. This is the property the adversarial
tests in `tests/unit/test_repo_resolution.py` defend, and it is unchanged by this feature: the
parser can be fooled by pasted text, and the onboarding filter still means nothing comes of
it.

**Prose is not a declaration.** A description containing `see robot-army: jantman/demo for
context` resolves by the ordinary text scan, exactly as it would have before — the mention of
`jantman/demo` counts as a mention, not as an instruction.

## Where to look when it is wrong

```bash
uv run robot-army cards --state needs_info           # the reason, per card
uv run robot-army log --limit 200 | grep trello.evaluated   # resolvable, repo_key, candidates, source
uv run robot-army rescan --all-needs-info            # re-evaluate everything held
```

The single most common cause of a line that appears to do nothing is that the repository it
names is not onboarded. The held reason says so and lists what is; `uv run robot-army onboard`
is the fix.
