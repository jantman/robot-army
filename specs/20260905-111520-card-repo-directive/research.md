# Research: Naming the repository outright on a card

The feature is small and lives inside one function, so the research is mostly about *where
the seams already are* — `resolve_repository` in `src/robot_army/intake.py` is the only
place in the system that decides which repository a card is for, and everything below is an
argument about what to change inside it and what to leave alone.

## R1 — Where the recognition belongs

**Decision**: inside `resolve_repository` in `src/robot_army/intake.py`, as a pass over the
same `f"{title}\n{body}"` text that the three existing reference scanners read, running
*before* them and short-circuiting when it finds anything.

**Rationale**: the module docstring states the rule — "**This is the only module that knows
what a card means.**" A declaration on a card is a statement about what the card means, so
it belongs here and nowhere else. Putting it here also means every existing caller gets it
for free: `evaluate_card` is the only caller, `poll_board`'s per-card path routes through
it, and `robot-army rescan` re-enters the same function. Nothing else has to change to make
the feature reachable.

**Alternatives considered**:

- *A new module, `carddirectives.py`.* Rejected: one function, one caller, and it would put
  half the knowledge of what a card means outside the module that claims all of it.
- *A pre-filter in `poll_board` that rewrites the card text.* Rejected outright — it would
  make the text stored in `cards.body` differ from the text on the board, and FR-015 needs
  the card's description to reach the filed issue verbatim.

## R2 — The line's grammar

**Decision**:

```
^[ \t]*robot-army[ \t]*:[ \t]*(\S+)[ \t]*$
```

matched with `re.IGNORECASE | re.MULTILINE` against the card text after every backtick in it
has been removed.

**Rationale**, clause by clause:

- **Anchored at both ends.** This is the whole of "and nothing else on the line", and it is
  what makes `see robot-army: owner/name for context` prose rather than an instruction.
  Without the anchors the feature would fire on any sentence that mentioned it.
- **`\S+` for the reference.** One run of non-space characters covers all three accepted
  spellings — a URL, an `owner/name`, and a path — without the grammar needing to know which
  is which. Deciding *what the reference is* is R3's job, and keeping the two apart is what
  lets the reference forms stay exactly the ones the rest of the card already accepts.
- **`[ \t]` rather than `\s` for the padding.** `\s` matches `\n`, which under `MULTILINE`
  would let the pattern straddle a line break and defeat the anchors.
- **`IGNORECASE`.** The prefix is a project name, not a token; `Robot-Army:` is the same
  instruction and refusing it teaches nothing.
- **Backticks stripped first.** The issue writes the line as `` `robot-army: <ref>` `` and so
  will the guide page, because that is how a literal is rendered in prose. An author copying
  it out of the documentation will paste the backticks with it, and Trello renders markdown,
  so the backticks are invisible on the card afterwards — a failure with no visible cause.
  A backtick cannot appear in any of the three reference forms, so removing every one of them
  before matching is safe and costs a single `str.replace`.
- **A bare `robot-army:` with nothing after it does not match**, which is exactly FR's
  "treat it as if it were not there" with no special case written for it.

**Alternatives considered**:

- *Tolerating a leading `-` or `*` bullet.* Rejected: a bullet is content, the line then is
  not "nothing else", and unlike the backticks nothing in the documentation will lead an
  author to type one.
- *Stripping trailing punctuation from the reference.* Rejected: `owner/name.` fails the
  onboarding filter and the author gets a held card quoting `owner/name.` back at them,
  which shows the problem. Guessing which trailing characters were meant is how a parser
  starts selecting repositories nobody named.

## R3 — Turning one reference into one repository

**Decision**: reuse the three existing recognisers unchanged — `_URL_REF`, `_BARE_REF`,
`_PATH_REF` and `_key_for_path` — by running them against the declaration's reference text
alone rather than against the whole card, and accepting a key only through the existing
`_offer`, which admits a candidate only if it is exactly an onboarded key.

**Rationale**: FR-004 asks for the same three spellings, and "the same" is only true if it
is the same code. It also preserves the security property the whole design rests on
(R8 of milestone 003): **an unonboarded reference cannot select anything.** A declaration
raises the author's intent above the text scan; it does not raise it above onboarding, and
nothing in this feature lets a card file an issue in a repository nobody approved.

**Alternative considered**: a stricter grammar for the declaration only — `owner/name`
exactly, no URL, no path. Rejected against FR-004: a line that silently ignores a spelling
used everywhere else on the same card is a trap, and a trap in the escape hatch is worse
than no escape hatch.

## R4 — Precedence, and what a declaration that fails does

**Decision**: if the card carries **one or more** declarations, the declarations decide the
outcome and the ordinary text scan is not consulted at all. Within that:

| Declarations present | Every one selects an onboarded repo | Distinct repos selected | Outcome |
|---|---|---|---|
| none | — | — | unchanged: the ordinary scan |
| ≥1 | yes | 1 | resolved to it |
| ≥1 | yes | ≥2 | held — they disagree |
| ≥1 | no | any | held — naming the reference that failed |

**Rationale**: two properties are worth more than the flexibility any softer rule would buy.

- *A declaration overrides rather than tie-breaks.* An override that only applied when the
  system was already confused would be untestable by the author: they cannot see whether the
  system is confused until it holds the card, so they could not tell a working line from an
  ignored one. Overriding always means the line's effect is the same on every card.
- *A failing declaration holds the card rather than falling back.* The fallback is the
  dangerous direction: a typo'd `robot-army: jantmna/demo` on a card that also mentions two
  other repositories would silently become "resolve from the text", and the issue lands
  somewhere the author did not ask for. Holding is the safe direction, and it is the
  direction the whole of `resolve_repository` already leans.
- *One good line plus one bad one holds too.* The author wrote both. Acting on one and
  discarding the other is the same silent-typo failure wearing a different hat.

**Alternative considered**: treat declarations as an extra source of candidates merged into
`found`, so a good line plus stray text mentions would still be ambiguous. Rejected — it
does not solve the reported problem at all, which is precisely a card whose text mentions
several repositories.

## R5 — Carrying "how it was decided" out to the record

**Decision**: add `source: str` to the existing frozen `Resolution` dataclass, taking
`"declaration"` or `"scan"`, defaulting to `"scan"`; add it to the `detail` of the existing
`trello.evaluated` audit record.

**Rationale**: FR-014 and SC-005 need the record to distinguish the two, and Principle III's
standard is reconstruction from the log alone — "which repository" without "why that one" is
half an answer when the card names three. `Resolution` is already the value that carries a
verdict plus its reason out to the caller, so the field goes where the rest of the verdict
already is. No new audit action is introduced: this is a shape change to one record, which
is why `docs/guide/audit-log.md` is in the task list.

**Alternative considered**: a separate `trello.declared` record. Rejected: one record per
card evaluation already exists and already fires on exactly the occasion this fact is known.
A second record would double the log's volume to carry one string.

## R6 — What the held card is told

**Decision**: two new reasons, and one edit to `_needs_info_comment`.

- Nothing matched: ``the `robot-army:` line on this card names 'jantmna/demo', which is not
  an onboarded repository — onboarded: jantman/demo, jantman/other``
- Disagreement: ``this card gives more than one `robot-army:` line and they name different
  repositories (jantman/demo, jantman/other); it must name exactly one``

and the comment's closing sentence gains the shape of the line, so the instruction on the
card is enough on its own to fix the card.

**Rationale**: SC-003. The specific failure this feature introduces is one where the author
*has* already done the thing the generic message asks for. Being told "name a repository"
when you have named one, in the way the documentation told you to, is the kind of unhelpful
that costs an afternoon. Quoting the reference back is what makes a typo visible.

The existing hold machinery is reused with no change: `_hold_for_info` already writes one
comment per *distinct* reason and re-comments when the reason changes, so a card that goes
from "no repository" to "your line names nothing" gets told about the change, and a card
held on the same reason for a month is still commented on once.

## R7 — Accountability and interruption (constitution, Development Workflow)

**What this logs**: nothing new is done, so nothing new is logged. The one existing record
for this decision, `trello.evaluated`, gains a `source` key. No outward-facing action, no
network call, no file write, and no state change is added by this feature: recognition is a
pure function of text the system already has in hand.

**What happens if it is killed halfway**: nothing that was not already true. Resolution is
recomputed from the card's stored text on every evaluation and nothing about it is
persisted, so a process killed mid-evaluation loses a computation and redoes it. The
four-step, separately-resumable creation that follows a *successful* resolution is untouched
by this feature.

## R8 — Documentation and configuration surface

**Decision**: `docs/guide/2-intake.md` — the page for "the label gate, Trello intake, card
handling" — gains the declaration in its "When a card doesn't say enough" section, and
`docs/guide/audit-log.md`'s `trello.evaluated` row is amended to say the record now records
how the repository was chosen.

**No configuration key is added**, so `exampleconfig.py` and `share/config.example.toml` are
untouched and `tests/unit/test_example_config_drift.py` stays green without any action. The
prefix is the literal `robot-army`; a knob selecting a different one would have exactly one
caller, which is the speculative generality Principle I names outright.

`README.md` is not touched. It is a pointer to the guide and a test fails if it grows past
150 lines.
