# ① Where work comes from

Two routes in. Both end at the same gate.

## The label is the gate

The ordinary route: I write an issue in an onboarded repository and put the label on it —
`robot-army` by default, `[github] label` to change it. The next poll sees it, checks the
issue is mine, and queues it.

Two things are checked and neither can be turned off:

- **The author.** Only issues written by `[github] author` are ever dispatched. There is
  deliberately no "any author" value. The check lives in one place, `poll.evaluate`, and
  every path that can put an item in the queue goes through it — including `retry`, which
  re-reads the issue from GitHub rather than trusting the body it stored.
- **The repository is onboarded.** A repository nobody approved is not watched, whatever
  its issues say.

Being on a project board's ready column is *not* an admission — see
[what runs next](3-selection.md#ordering-work-from-a-project-board). The board narrows and
orders; it never admits.

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

## Parking a card

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

## When a card doesn't say enough

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

## One card, one issue

Enforced by two unique indexes rather than by code that has to remember, so a path that
skipped its check fails loudly instead of quietly duplicating. Creation is four steps with the
intent written first, and every seam between them is separately resumable — including the
dangerous one, where the issue exists and nothing local knows it yet.

If the database is lost entirely, each card's own comment names its issue, and the next poll
rebuilds the mapping from it rather than filing a second one. The one gap left open is a crash
between creating the issue and recording it *combined with* losing the database, and it is
written down in [state](state.md) rather than pretended away.

---

Next: [what runs next](3-selection.md).
