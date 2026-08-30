# Phase 0 Research: Standing Delivery Instructions In The Dispatch Prompt

Six decisions. Five needed the existing prompt code read carefully, because each is about where
a paragraph goes and what that position already means in a file where **position is the
precedence mechanism**. The sixth, D6, is different in kind and was added after the first
implementation was reviewed: it is about what the prohibition is a prohibition *of*, and it
reverses what this milestone originally shipped.

---

## D1. The block is unconditional and takes no parameter

**Decision**: `prompt.compose()` inserts the text itself, from a module constant. No keyword
argument, no caller opt-in, nothing for `dispatch.py` to pass.

**Rationale**: FR-011 says inclusion depends on nothing — not detection, not configuration, not
per-repository state. A parameter exists to let a caller vary a value; there is exactly one
caller and nothing to vary. Under Principle I that is the definition of a knob with one caller
and no second use in hand.

This is the opposite choice from milestone 007's `speckit_block`, and the difference is real
rather than stylistic: that block is *wrong* for a repository without Spec Kit, so somebody has
to decide per dispatch, and `dispatch.speckit_block()` is where the deciding and its logging
live. This block is right for every repository the daemon dispatches into. Nothing decides, so
nothing needs a seam to decide at.

**Alternatives considered**:

- *A `delivery_block` keyword like `speckit_block`.* Symmetric with the neighbouring parameter
  and therefore tempting. Rejected: it would mean a call site that always passes the same
  constant, plus a `None` branch that no production path ever takes and no test could justify
  beyond exercising itself.
- *A `[dispatch] delivery_guidance = true/false` configuration key.* Rejected on the spec's
  recorded assumption. Two override paths already exist and are documented in the text itself —
  a repository's `.claude/robot-army.md` outranks by position, and an explicit instruction in
  the issue body overrides by the block's own last sentence. A third path with no known user
  would be configuration surface that has to be parsed, validated, documented, and tested
  forever.

---

## D2. It sits below the Spec Kit block and immediately above the issue

**Decision**: The assembled order becomes:

```text
[.claude/robot-army.md, if present]
---
[the Spec Kit block, if detected and enabled]
---
[the standing delivery instructions]        ← new, always
---
You are working on <repo> issue #<n> …
<the issue body>
```

**Rationale**: Three things pushed it to this slot rather than above the Spec Kit block.

1. **It leaves existing text true.** The Spec Kit block ends with "Where any instruction above
   this paragraph conflicts with this one, the instruction above wins." Inserting the new block
   *above* it would silently extend that sentence to cover the new text, declaring a precedence
   between two blocks that do not conflict and that nobody decided. Inserting below leaves the
   sentence meaning exactly what it meant when it was written.
2. **The block talks about the issue below it.** Its override rule (FR-008) and its "unless the
   issue says otherwise" framing both point downward. Adjacency makes the reference short and
   unambiguous.
3. **It is read last among the guidance.** The delivery rule matters at the *end* of the work,
   which is the far end of a long session; being the last thing before the task itself is the
   best position available for that.

**Alternatives considered**:

- *Above the Spec Kit block.* Rejected for reason 1.
- *After the issue body.* Rejected outright: the body can be 60,000 characters and is truncated
  at that limit, so anything after it is guidance that may be read last or not at all — the same
  argument milestone 007 already made for its own block, and it applies with more force here
  because this text has a consequence (unpushed work) that the truncation notice cannot repair.
- *Merged into the Spec Kit block.* Rejected: it would make the behaviour conditional on Spec
  Kit detection, contradicting FR-011.

---

## D3. The branch is referred to without a direction word

**Decision**: The text says "the branch this session was started on" rather than "the branch
named above".

**Rationale**: A near-miss worth writing down. The sentence naming the branch —
`You are working on <repo> issue #<n> in a dedicated git worktree on branch \`…\`` — is the
first line of the *issue section*, which by D2 sits **below** this block, not above it. "Above"
would have been wrong, and wrong in a way that reads perfectly well and would have survived
review. Direction-neutral phrasing is correct wherever the block ends up, which also means a
later reordering cannot quietly falsify it.

The block does not restate the branch name. Interpolating it would make the text vary per
dispatch and cost FR-010's one-line determinism test for nothing — the name is already on the
next line.

---

## D4. Nothing new is logged, and nothing new needs to be

**Decision**: No audit call is added.

**Rationale**: Principle III asks what a feature logs. This one changes the value of a string
that is *already* recorded in full. `dispatch.build_launch_plan()` puts the composed prompt into
`worker_argv`, which is wrapped into `plan.argv`, which `db.insert_session()` persists as JSON in
the session row's `launch_argv` column — and which `dispatch.unconfirmed` records verbatim in its
detail when a launch cannot be confirmed. What a session was told is therefore reconstructable
from stored state without re-running anything, before this change and after it, and FR-014 is
satisfied by not breaking something rather than by adding something.

There is no new action here at all: no file is written, no subprocess runs, no network call is
made, nothing outside the process changes. A `prompt.compose` audit line would record that a pure
function returned a constant. No Principle III exception is claimed, because nothing goes
unlogged.

**Alternatives considered**: recording the guidance separately from the argv it is already part
of. Rejected — a second copy of the same bytes in the same log, which makes the record longer
without making it more complete.

---

## D5. Milestone 007's golden string is deliberately superseded

**Decision**: `tests/unit/test_speckit_prompt.py::test_without_a_block_the_prompt_is_byte_identical_to_before`
is updated, not deleted, and its docstring records why the golden changed.

**Rationale**: That test asserts 007's FR-010 — with no Spec Kit block, the prompt is
byte-identical to what it was before milestone 007. This feature makes that false on purpose:
every prompt now carries the delivery block. The requirement it protected ("adding the Spec Kit
parameter changed nothing for repositories without Spec Kit") was about *that* change and has
already been met; it is not a standing promise that the prompt never changes again.

Deleting the test would throw away what it is actually good for — it is the only assertion that
notices when the surrounding sections are reshaped by an innocent refactor. So the golden is
re-captured with the delivery block present and the docstring says which milestone changed it and
why, which is the difference between a superseded expectation and a quietly weakened one.

**Alternatives considered**: keeping the old golden by making the block opt-in. That is D1's
rejected parameter, arrived at from the other end — preserving a test by weakening the feature it
tests. Rejected.

---

## D6. The containment rule is drawn at the bypass, not at the side effect

**Decision**: The third paragraph says *where this repository is the mechanism for changing
something, write the code that produces it rather than doing it directly*, followed by a sentence
scoping the limit. It does **not** say "do not change the state of this machine or any other
system", and it carries no exception list.

**Rationale**: This reverses the first implementation, which shipped and was corrected on review.
The reversal is worth recording in full, because the wrong version was a faithful reading of the
issue text and passed every test written for it.

The issue asks that "nothing should be done to directly effect the state of this or any other
system". Read as a rule about side effects, that has two defects and they pull in opposite
directions:

- **It is too broad.** Pushing a branch and opening a pull request change the state of another
  system, and so does running a build, starting a container, or installing a dependency. The
  first draft noticed this and bolted on an exception list — the push and the pull request are
  permitted; tests, builds and dependency installation are not what this restricts. That patched
  the contradiction without fixing it, and an exception list is the reliable tell that a rule has
  been drawn in the wrong place. It also cannot be complete: a session wondering whether it may
  start a local dev server finds nothing in the list and has to guess.
- **It is too narrow, in the way that matters.** The case it exists for is a
  configuration-management repository and an issue reading "set up and run this service".
  Installing the service by hand is wrong — but *not because a machine was touched*. It is wrong
  because the repository was the thing that was supposed to touch it, so the change is invisible
  to review, absent from history, and erased by the next real run of the tool. A rule about side
  effects does not say any of that, and a session reasoning from it would have to arrive at the
  right answer by luck.

Naming the fault correctly fixes both at once. Bypassing the repository is the wrong thing;
touching a system is not. The push, the pull request, the test suite and the local dev loop stop
needing permission because they were never in scope, and the Puppet case is covered by the rule
itself rather than by hoping the session extrapolates.

The paragraph also gives its reason rather than only its rule. That is deliberate and is the
difference between a constraint a session can reason from and one it can only pattern-match
against. No list of repository kinds will ever be complete; "invisible to review and gone at the
next run" generalises to the ones the list omits.

**Alternatives considered**:

- *Keep the broad rule, extend the exception list.* Rejected. Each addition makes the list look
  more authoritative and its omissions more meaningful, and the underlying rule still names the
  wrong fault.
- *Keep both — a side-effect ban and the mechanism rule.* Rejected: a session that hits the two
  and finds them in tension will resolve it silently, which is the failure mode this whole block
  exists to remove.
- *Say nothing about side effects at all and rely on the permission mode.* Rejected. The
  permission mode is not a boundary the daemon sets per issue, and the spec's Out of Scope is
  explicit that this feature ships an instruction rather than a boundary — but an instruction
  that is absent is not an instruction that is safe.

**Cost of having got it wrong first**: one commit's worth of prose, five tests rewritten, and a
requirement (FR-006) that now states the property — the rule must not need exceptions — rather
than mandating the exception list. Cheap, and only because nothing had been pushed yet.

---

## Non-questions

Recorded so a later reader does not re-open them:

- **Does the daemon need to push or open pull requests itself?** No, and FR-013 forbids it. The
  session has the credentials, the working tree, and the judgement; the daemon has none of the
  three at that moment. This feature ships prose.
- **Should compliance be checked afterwards?** Out of scope by the spec. The observable facts
  already have a home: `robot-army show <id>` reports commits, push state, and pull request, and
  the cleanup branch guard already refuses to delete a branch whose commits are not provably on
  the remote.
- **Does anything need re-onboarding?** No. The text is composed at dispatch from a constant, so
  the next dispatch after the change carries it (SC-005).
