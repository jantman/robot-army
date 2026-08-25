# Quickstart: Validating the Trello Source

Runnable scenarios that demonstrate the milestone end to end. Use a **disposable board** — the
planning document and FR-042 both insist the loop-prevention invariant be proven against a real board
rather than inferred from a dry run, and proving it involves deliberately killing the process
mid-creation.

## Prerequisites

- Milestone 002 working: `robot-army doctor` passes, a daemon can start.
- A private Trello board with only you on it, an `AI-task` label, and `In Progress` and `Done` lists.
- A Trello API key and token exported as `TRELLO_API_KEY` and `TRELLO_API_TOKEN`.
- A `[trello]` section per [`contracts/config.md`](contracts/config.md).
- At least one onboarded repository you are willing to receive throwaway issues in.

```bash
robot-army doctor        # includes the five board checks; must report all five green
```

---

## 1 — An unconfigured installation touches no board (FR-001)

Comment out `[trello]`, run one daemon tick, and confirm no board request was made.

```bash
robot-army run --once
robot-army log --since 5m | grep -c trello     # expect 0
```

**Expected**: zero `trello.*` records, and everything else behaves exactly as in milestone 002.

---

## 2 — Card to issue, with the human gate intact (User Story 1)

Add a card titled anything, described with the repository's GitHub URL, and tag it `AI-task`. Then:

```bash
robot-army run --once
robot-army cards
```

**Expected**: the card is `linked`; an issue exists in that repository containing the card's text and
a link back to the card; the card carries a comment with the issue URL; **the issue is not labelled**
and `robot-army status` shows no new work item. Nothing dispatched.

Now label the issue `robot-army` by hand and tick again. **Expected**: exactly one work item appears,
created by the ordinary issue path, and `robot-army show <id>` displays the card URL beside the issue
URL.

---

## 3 — An ambiguous card is held, not guessed (User Story 2)

Add a tagged card with no repository reference. Tick.

**Expected**: `robot-army cards` shows it `needs_info` with a reason naming what is missing; **no
issue exists in any repository**; the card has exactly one comment saying what is needed.

Tick four more times. **Expected**: still exactly one comment (FR-022), and — the specific trap R9
exists for — the card is not re-evaluated on every pass merely because our own comment changed its
last-activity timestamp.

Now edit the card to name the repository, and tick once.

**Expected**: it resolves and follows scenario 2's path, with no human action beyond the edit
(FR-023). Repeat with a card naming *two* different configured repositories: it must be held with an
ambiguity reason, not resolved to either.

---

## 4 — The board tells the truth (User Story 3)

Take the scenario 2 card's issue through dispatch to close.

**Expected**: the card is in `In Progress` exactly while the session runs, and in `Done` once the
issue closes, with an outcome comment.

Then, with a second card mid-dispatch, abandon its work item. **Expected**: the card returns to the
list it came from with a comment naming the reason — it does not sit in `In Progress` claiming to be
busy (FR-029).

Finally, move a card out of `In Progress` by hand and then close its issue. **Expected**: the card is
**not** moved; a comment records what would have been done (FR-030).

---

## 5 — One card, one issue, under a knife (User Story 4, FR-042)

This is the scenario that cannot be replaced by a dry run.

```bash
# Repeat polling: must never produce a second issue
for i in $(seq 1 10); do robot-army run --once; done
robot-army cards --json | jq '[.cards[] | select(.state=="linked")] | length'
```

Then the interruption matrix. For each row of data-model.md's interruption table, kill the daemon at
that point (`kill -9`), restart, tick, and confirm no duplicate:

- Killed after the issue was created but before the mapping was written: **expected** the existing
  issue is adopted by the listing recovery, `robot-army log` shows a `trello.recovered` record, and
  the repository holds exactly one issue for that card.
- Killed after the mapping but before the card comment: **expected** the comment appears on the next
  tick, once.

Then the database-loss case:

```bash
mv ~/.local/state/robot-army/state.db /tmp/state.db.bak
robot-army run --once
```

**Expected**: the card's marker comment restores the mapping, `robot-army log` shows the recovery, and
**no second issue is created** (FR-034). Note that work items are also gone — that is expected, and
unrelated to this invariant.

---

## 6 — Nothing is written below the live effect level (FR-039, SC-009)

```bash
robot-army run --once --effect-level no-remote
```

**Expected**: real cards were read and evaluated; the board is untouched; no issue was created; the
log contains a record for every write that would have been made, with full arguments; `robot-army
cards` shows nothing by default and the simulated rows only with `--include-simulated`, marked.

Then run once at `live` against the same card. **Expected**: the real creation happens — the
simulated row did not consume the card's identity (FR-041).

---

## 7 — A shared board stops ingestion (FR-004)

Set the board to public rather than private, restart the daemon.

**Expected**: ingestion refuses with an anomaly naming the actual permission level; **dispatch of
ordinary labelled issues keeps working**; `robot-army doctor` exits `4` and says which check failed.
Set it back to private and restart to confirm it recovers.

Repeat with the `AI-task` label renamed — a renamed label must fail loudly rather than look like an
empty board (FR-005).

Then the case that must **not** fail: add a second member to the private board and restart.
**Expected**: ingestion continues normally, and the member list appears in the audit log. Who else is
on the author's own board is the author's decision, and the system does not get a vote (FR-004a).

---

## 8 — No credential ever reaches the record (FR-003, SC-011)

Break the token deliberately, tick, then:

```bash
grep -R "$TRELLO_API_TOKEN" ~/.local/state/robot-army/log/  ; echo "exit $?"   # expect exit 1
grep -R "$TRELLO_API_KEY"   ~/.local/state/robot-army/log/  ; echo "exit $?"   # expect exit 1
curl -s localhost:8420/cards | grep -c "$TRELLO_API_TOKEN"                     # expect 0
```

**Expected**: no match anywhere, including in the authentication-failure records, and the failure
itself is recorded with a usable cause. This is the scenario R3 exists for.

---

## 9 — An unreachable board is not an empty board (FR-009)

Point `api_base` at a black hole and tick three times.

**Expected**: each failure is recorded with its cause, backoff grows, an anomaly is raised after the
threshold, the heartbeat reports the board as degraded, and at no point does anything report "no
cards found". GitHub polling and dispatch continue untouched.
