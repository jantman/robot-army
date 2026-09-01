# Feature Specification: What Each Spec Kit Command Is Invoked With Is Configuration, Not Compiled-In Prose

**Feature Branch**: `issues/39`

**Created**: 2026-09-01

**Status**: Draft

**Input**: User description: "issue #39 on this repo" — *"For repos using spec-kit: 1. Each phase
(`/speckit-specify`, `/speckit-plan`, and `/speckit-tasks`) should commit its work to the branch.
2. `/speckit-implement` should be called with a specific prompt: `/speckit-implement when finished
with implementation, commit, push the branch to origin, and open a PR. Once that's done, monitor the
CI jobs on the PR. Once all are complete, use /answer-reviews to respond to any reviews. Repeat this
until claude reviews with a comment of "No issues found. Checked for bugs and CLAUDE.md
compliance.".`"*

**Clarified**: 2026-09-01. A first draft of this specification read the issue as a request for two
particular sentences and set about specifying their wording — whether to name `/answer-reviews`,
what bounds the review loop, whether the early phases push as well as commit. All three questions
were the wrong questions. **The maintainer configures what the commands are invoked with; robot-army
carries it.** What the text says is out of scope here, permanently and by design.

**Amended**: 2026-09-01, on the maintainer's decision. The instructions are configured globally and
**a repository may override them**, on the same value-plus-provenance pattern every other
per-repository setting already uses. The first draft put this in Out of Scope pending a second
repository that wanted different text; it is in scope now.

**Scope note**: This is the resolution of
[issue #39](https://github.com/jantman/robot-army/issues/39). It extends the `[speckit]`
configuration section and the guidance block that milestone
[007](../007-speckit-extensions/spec.md) introduced. **Nothing in this milestone writes anything
into a worktree** — 007's central property, preserved for the same reason it was true then.

### The block says what the lifecycle is, and nothing about how it is run here

The paragraph a dispatched session gets in a Spec Kit repository is fixed text compiled into the
daemon. It names the four commands, says the issue is the input to `/speckit-specify`, leaves the
judgement about whether the lifecycle is warranted to the session, and defers to the repository's
constitution. All of that is true of Spec Kit in general, which is exactly why it can be a constant.

What it cannot say is how the maintainer runs the lifecycle. That is not general knowledge about
Spec Kit; it is a working practice, it changes as the practice changes, and today the only ways to
express it are both wrong:

- **Edit the daemon.** A code change, a test change and a release to alter a sentence about how the
  maintainer likes to work. Milestone 007 accepted this for text that describes Spec Kit itself. It
  is the wrong bargain for text that describes one person's habits.
- **Write it into every repository's `.claude/robot-army.md`.** One file edit per repository, kept in
  step by hand, for something that is the same everywhere. That is precisely the cost milestone 005
  was written to remove and that 007 cited as its own motivation.

Issue #39 arrives with two examples of such practice — commit at the end of each of the first three
phases, and invoke `/speckit-implement` with a paragraph about pushing, opening a pull request,
watching CI and answering reviews. Both are opinions about how this maintainer works. Neither is a
fact about Spec Kit. Compiling either into the daemon would put a working practice in the one place
it is most expensive to change, and would guarantee a third issue the next time the practice moves.

### One mechanism, and the issue is its own proof that one is enough

The two items in the issue look like two features. They are one, seen twice: **for each lifecycle
command, the maintainer may specify the instruction that command is invoked with.**

Item 2 is that mechanism applied to `implement`. Item 1 is the same mechanism applied to `specify`,
`plan` and `tasks` — "commit your work to the branch when this phase is done" is an instruction
attached to a command in exactly the way the implement paragraph is. Two independent requests
landing on one mechanism, on the day it is first proposed, is the second caller that Principle I
asks for before a knob is built.

So this milestone adds configuration for four strings and the rendering that puts them into the
block. It does not add a commit instruction, a CI instruction, a review instruction, or any opinion
about how work should be delivered. Those are values in a file the maintainer owns.

The strings are global, and a repository may override any of them. That shape is not a design
decision this milestone gets to make freshly: `base_branch`, `permission_mode`, `model`,
`max_sessions` and the Spec Kit gate itself are all "a global default, overridden per repository
where the exception is real, and the answer carries its own provenance". Milestone 005 fixed what
the `[repos.*]` section is for — a place for exceptions, never a registration — and an instruction
that differs in one repository is exactly such an exception. The alternative, global-only, was the
first draft's position and it made "this one repository needs a different implement paragraph" cost
either a `.claude/robot-army.md` edit or a change of practice everywhere.

### What stays exactly as it was

Detection is untouched: the same two halves, the same reads, the same never-raises promise. The gate
is untouched: `[speckit] enabled` and the per-repository override decide whether a session gets the
block at all, and configured text lives entirely inside that gate rather than beside it. Observation
is untouched: the phase ladder is still derived from files in the worktree and still needs no
cooperation from the session. Enforcement remains absent — the block says what to invoke and never
implies that anything checks.

## User Scenarios & Testing *(mandatory)*

<!--
  One mechanism, its two uses from the issue, the property that keeps it from being a
  liability on an installation that never touches it, and the exception path.

  Story 1 is the motivating case and the smallest useful slice: one command, one string.
  Story 2 is the same mechanism reaching the other three commands, which is what turns it
  from "a place to put the implement paragraph" into a general answer. Story 3 is the
  unconfigured installation and the malformed one — the first must be byte-for-byte what it
  is today, and the second must be refused out loud rather than dropped in silence. Story 4
  is the per-repository override, last because it is the exception to a practice that has to
  exist before it can have exceptions.
-->

### User Story 1 - The maintainer decides what `/speckit-implement` is invoked with (Priority: P1)

The maintainer writes, in `config.toml`, the instruction that `/speckit-implement` should carry in
their repositories. Every subsequent dispatch into a detected Spec Kit repository tells the session
to invoke `/speckit-implement` with that instruction, verbatim.

Some weeks later the practice changes — a different review command, a different stopping condition,
a step added or dropped. The maintainer edits the string. No code change, no test change, no
`.claude/robot-army.md` in any repository, and nothing in the daemon knows or cares what the
instruction says.

**Why this priority**: It is the issue's own example, the smallest complete slice of the mechanism,
and the one that removes a per-repository file edit today. Delivered alone, the maintainer can
express the entire implement-phase practice once and have it reach every Spec Kit repository.

**Independent Test**: Set the implement instruction in a configuration file, compose a prompt for a
fixture issue against a detected Spec Kit worktree, and read the result. The configured text appears,
attached to `/speckit-implement`, unaltered. Change the string, compose again, and only that part of
the prompt changes.

**Acceptance Scenarios**:

1. **Given** a configured instruction for `implement`, **When** a prompt is composed for a detected
   Spec Kit worktree, **Then** the guidance block tells the session to invoke `/speckit-implement`
   with that instruction, and the instruction appears exactly as written.
2. **Given** a configured instruction containing Markdown, quotation marks, backticks or multiple
   paragraphs, **When** the prompt is composed, **Then** the text is carried through unchanged — the
   daemon does not reformat, wrap, escape, summarise or otherwise interpret it.
3. **Given** the same configuration and the same issue, **When** the prompt is composed twice,
   **Then** both compositions are byte-identical.
4. **Given** a repository for which the guidance block is suppressed — by `[speckit] enabled` or by
   the per-repository override — **When** a prompt is composed, **Then** no configured instruction
   appears either, because the configuration lives inside the gate rather than beside it.

---

### User Story 2 - The same mechanism reaches the other three commands (Priority: P2)

The maintainer wants each of the first three phases to commit its work before the next begins. That
is not a robot-army feature: it is one sentence, configured against `specify`, `plan` and `tasks`,
by the same mechanism Story 1 built for `implement`.

Any command may be configured or left alone, independently. Configuring only `implement` is the
Story 1 case and the block mentions only `implement`. Configuring all four means all four are
mentioned, in lifecycle order, so the block reads as the sequence the session will actually run.

**Why this priority**: It is the issue's first item and it needs no new mechanism — only that the
mechanism be per-command rather than implement-only. Second because Story 1 is where the practice
that could not be expressed at all lives, and because a commit instruction can be written into a
repository's `.claude/robot-army.md` in the meantime while an implement invocation cannot be
expressed anywhere.

**Independent Test**: Configure instructions for two of the four commands, compose a prompt, and
confirm both appear, in lifecycle order, and the unconfigured two are not mentioned at all.

**Acceptance Scenarios**:

1. **Given** instructions configured for a subset of the lifecycle commands, **When** the prompt is
   composed, **Then** exactly those commands are named with their instructions, in the order
   `specify`, `plan`, `tasks`, `implement`, whatever order the configuration file used.
2. **Given** no instruction configured for a command, **When** the prompt is composed, **Then**
   nothing is said about invoking that command with anything, and no placeholder, empty heading or
   "none configured" line appears.
3. **Given** an instruction configured for `specify`, **When** the prompt is composed, **Then** the
   block's existing sentence about the issue being the input to `/speckit-specify` still stands and
   the two read as one instruction — the session hands it the issue *and* the configured text.
4. **Given** any combination of configured commands, **When** the block is read, **Then** it still
   ends with its precedence sentence, so a repository's own `.claude/robot-army.md` outranks the
   configured text exactly as it outranks everything else in the block.

---

### User Story 3 - An unconfigured installation is unchanged, and a broken one says so (Priority: P3)

An installation that configures nothing gets today's block, byte for byte. Nobody has to opt out of
a feature they did not ask for, and the change is invisible until it is used.

An installation that configures something malformed — a number where a string belongs, an empty
string, a command name that is not one of the four, text long enough to suggest a whole document was
pasted in by accident — is told, in the same way every other configuration mistake in this project is
reported. It is not silently ignored, and it does not reach a dispatched session in a mangled form.

**Why this priority**: It is worth nothing on its own and it is what makes the other two safe to
ship. A silently dropped instruction is the worst outcome available here: the maintainer believes
every session is being told something that no session has ever been told, and the sessions look
exactly the same either way.

**Independent Test**: Compose a prompt with no `[speckit]` customization at all and compare it to a
stored expected string from before this milestone — byte-identical. Then load configurations with
each malformed shape and confirm each is reported.

**Acceptance Scenarios**:

1. **Given** a configuration with no Spec Kit customization, **When** a prompt is composed for a
   detected Spec Kit worktree, **Then** the result is byte-identical to what the same inputs produce
   today.
2. **Given** a customization whose value is not a string, **When** the configuration is loaded,
   **Then** it is reported as a configuration problem naming the offending key, alongside every
   other problem in the file rather than aborting at the first.
3. **Given** a customization keyed on a name that is not one of the four lifecycle commands, **When**
   the configuration is loaded, **Then** it is reported as an unknown key, consistent with how every
   other section treats unknown keys.
4. **Given** a customization whose value is empty or only whitespace, **When** the configuration is
   loaded, **Then** it is reported rather than treated as absent — an empty instruction is a mistake,
   and the two states are indistinguishable in a composed prompt.
5. **Given** a customization longer than the documented per-command limit, **When** the configuration
   is loaded, **Then** it is reported, naming the limit and the length found.

---

### User Story 4 - One repository needs different instructions (Priority: P4)

The maintainer's practice is written once, globally, and it is right nearly everywhere. In one
repository it is not: that repository has no CI worth waiting for, or a review flow of its own, or a
lifecycle the maintainer runs differently there. Its section in the configuration file names the
commands whose instructions differ and gives their text. Every other command in that repository, and
every command in every other repository, still comes from the global setting.

The same section can also say that a command carries *no* instruction here, without disturbing the
global text and without switching off the whole guidance block — which is the only tool available
today and is far too blunt for "just not this paragraph, just here".

Whichever setting produced the answer is recorded, so a prompt sent months ago can be accounted for
without guessing whether the global text or an override was in force at the time.

**Why this priority**: It is the exception path, and an exception path is worth nothing until there
is a practice to except. It is also the cheapest of the four stories, because the resolution shape
it needs is the one four other settings in this project already use.

**Independent Test**: Configure global instructions and a repository section overriding one of them,
then compose prompts for two repositories — the overridden one and any other. The first shows the
override for that command and the global text for the rest; the second shows the global text
throughout.

**Acceptance Scenarios**:

1. **Given** a global instruction for a command and no override in a repository's section, **When** a
   prompt is composed for that repository, **Then** the global instruction is used.
2. **Given** a repository whose section sets an instruction for a command, **When** a prompt is
   composed for that repository, **Then** the repository's text is used for that command and the
   global text is used for every other command.
3. **Given** a repository whose section sets a command's instruction to nothing, **When** a prompt is
   composed for that repository, **Then** that command is not mentioned as taking an instruction, and
   the global instruction for it is unaffected everywhere else.
4. **Given** a repository with no section at all, **When** a prompt is composed for it, **Then** it
   receives the global instructions — a repository needs no section to get the behaviour, exactly as
   milestone 005 requires.
5. **Given** any repository, **When** the effective instructions for it are asked for, **Then** the
   answer names which setting produced each one, and that provenance is what both the dispatch record
   and any listing report rather than each deriving it separately.
6. **Given** a repository section containing a malformed override, **When** the configuration is
   loaded, **Then** it is reported exactly as the equivalent global mistake is, naming the repository
   and the command.

---

### Edge Cases

- **A configured command the session never runs.** The block already leaves the session free to judge
  that the lifecycle is not warranted. A configured `implement` instruction on a one-line fix is
  simply never acted on, which is correct and is not recorded as anything.
- **Text that argues with the rest of the prompt.** A maintainer can configure an instruction that
  contradicts the delivery block, the repository's own instructions, or the issue. Nothing here
  adjudicates that: the block's existing precedence sentence and the delivery block's own override
  paragraph already say who wins, and the configured text sits inside the block rather than above it.
- **Text containing the block's own separators or Markdown structure.** The prompt is assembled from
  sections joined by `---`. Configured text is prose supplied by the one person who reads the result;
  it is carried through unchanged, and a maintainer who pastes a horizontal rule into it gets a
  horizontal rule.
- **Configuration changed while the daemon runs.** Configuration is read at start-up, so an edit
  reaches dispatches after the next start. Items already dispatched carry the prompt they were
  dispatched with; a prompt is composed once.
- **Two dispatches under different configurations.** The block is no longer identical across all
  time, nor across all repositories — only across a given effective configuration. That is a
  deliberate amendment to milestone 007's determinism contract and is stated as one, not discovered
  later by a failing test.
- **Empty means different things in the two places.** Globally, an empty instruction and an absent
  one are the same state, so an empty one is a mistake and is reported. In a repository's section
  they are different states — absent inherits, empty overrides with nothing — so empty is meaningful
  there and is the only way to remove one instruction without removing the whole block. That
  asymmetry is not an inconsistency; it is what "override" means when there is something to inherit.
- **An override in a repository whose block is switched off.** `speckit = false` suppresses the block
  entirely, so the override is never rendered. It is not a configuration error: turning the block off
  and leaving the text in place is how the maintainer parks an exception without deleting it.
- **An override for a repository that has no Spec Kit installed.** Detection fails and nothing is
  rendered. Also not an error — a repository can adopt Spec Kit later, and 007 requires that to work
  with no re-onboarding.
- **Very long configured text.** The composed prompt is a single argument to a process. The issue
  body already has a documented cap for this reason and configured instructions need one too.
- **Dry-run items.** A dry-run item composes a prompt and dispatches nothing; configured text is part
  of the composed prompt like everything else and changes no dry-run behaviour.
- **A repository that is not a Spec Kit project.** Detection fails, no block, no configured text. The
  configuration is not a general-purpose way to add prose to every prompt, and must not become one.

## Requirements *(mandatory)*

### Functional Requirements

**The configuration**

- **FR-001**: The `[speckit]` configuration section MUST accept, for each of the four lifecycle
  commands (`specify`, `plan`, `tasks`, `implement`), an optional instruction: free text the
  maintainer supplies and the daemon does not interpret.
- **FR-002**: Each command's instruction MUST be independently settable and independently omissible.
  Configuring one MUST NOT require configuring any other.
- **FR-003**: The configuration MUST be readable and writable by hand in the existing configuration
  file, with no new file, no new format, and no other place to look.
- **FR-004**: An absent customization MUST be exactly equivalent to the section not existing.
- **FR-005**: No new enable/disable switch MUST be introduced. The existing `[speckit] enabled` key
  and per-repository `speckit` override govern configured instructions exactly as they govern the
  block.
- **FR-006**: A value that is not text, an unrecognised command name, and a value longer than the
  documented limit MUST each be reported as a configuration problem, in the same manner and at the
  same time as every other configuration problem, naming the offending key. An empty or
  whitespace-only value MUST be reported in the global form, where it says nothing that omission does
  not already say, and MUST be accepted in a repository's section, where it means "no instruction for
  this command here" (FR-025).
- **FR-007**: The per-command length limit MUST be documented, and the configured instructions
  together MUST NOT be able to grow the composed prompt beyond what the dispatch mechanism can carry.

**What the session is told**

- **FR-008**: When an instruction is configured for a command and the guidance block is being sent,
  the block MUST tell the session to invoke that command with that instruction.
- **FR-009**: The instruction MUST reach the session exactly as written — unmodified, unreformatted,
  unwrapped, unescaped and unsummarised.
- **FR-010**: Commands with no configured instruction MUST NOT be mentioned as taking one, and no
  placeholder, empty heading or absence marker MUST appear for them.
- **FR-011**: When more than one command is configured, they MUST appear in lifecycle order —
  `specify`, `plan`, `tasks`, `implement` — regardless of the order in the configuration file.
- **FR-012**: A configured `specify` instruction MUST coexist with the block's existing statement
  that the issue is that command's input, such that the session is told to supply both.
- **FR-013**: With nothing configured, the composed prompt MUST be byte-identical to what the same
  inputs produce before this milestone.
- **FR-014**: With a given effective configuration, the block MUST be identical across issues and
  repeated compositions, and MUST differ between two repositories only where their effective
  instructions differ. This amends milestone 007's FR-009 — which required identical text everywhere
  — to require identical text per effective configuration, and that amendment MUST be recorded in
  007's contract rather than left implicit.
- **FR-015**: The block MUST retain its closing precedence sentence, and configured text MUST sit
  under it — a repository's own `.claude/robot-army.md` still outranks everything in the block.
- **FR-016**: The block MUST NOT state or imply that following a configured instruction is checked,
  recorded, or enforced.

**Scope of the change**

- **FR-017**: Detection MUST be unchanged: the same evidence, the same reads, the same promise never
  to raise.
- **FR-018**: Phase observation MUST be unchanged, and MUST continue to derive the recorded phase
  from files in the worktree.
- **FR-019**: Nothing in this milestone MUST write into a worktree, run a subprocess, or make a
  network request.
- **FR-020**: The dispatch record for a Spec Kit worktree MUST say which commands carried a
  configured instruction **and which setting produced each one**, so a prompt can be accounted for
  after the fact from the log plus the configuration file, without guessing whether an override was
  in force.
- **FR-021**: The configuration MUST be documented where the rest of the Spec Kit behaviour is
  documented, with the issue's own two examples shown as examples of use rather than as defaults.
- **FR-022**: No configured instruction MUST ship as a default. An installation that configures
  nothing is told nothing extra.

**Per-repository override**

- **FR-023**: A repository's existing `[repos.*]` section MUST accept an override for any of the four
  lifecycle command instructions, and the section MUST remain optional — a repository with no section
  receives the global instructions.
- **FR-024**: An override MUST replace the global instruction for that command in that repository,
  and MUST NOT affect any other command, any other repository, or the global setting. Overriding one
  command MUST NOT require restating the others.
- **FR-025**: An override MUST be able to state that a command carries no instruction in this
  repository, without altering the global instruction and without suppressing the guidance block.
- **FR-026**: Resolving a repository's instructions MUST yield, for each command, both the effective
  text and the setting that produced it — global, override, or nothing configured — computed in one
  place, so that the dispatch record and any listing report the same answer rather than deriving it
  separately.
- **FR-027**: The effective instructions for a repository MUST be answerable offline, before an issue
  is labelled, without dispatching anything — the expectation milestone 007 set when it made "which
  repositories does this change?" answerable from the existing repositories listing.
- **FR-028**: Malformed overrides MUST be reported exactly as the equivalent global mistakes are,
  naming the repository and the command, and MUST be collected alongside every other configuration
  problem rather than aborting at the first.

## Key Entities

- **Lifecycle command instruction**: free text the maintainer associates with one of the four Spec
  Kit lifecycle commands. Owned entirely by the maintainer; read, bounded and carried by the daemon;
  never interpreted by it. Absent by default. Meaningful only inside a guidance block that is being
  sent at all.
- **Effective instructions for a repository**: the four instructions that repository's sessions
  actually receive, each resolved from its override if it has one and the global setting otherwise,
  and each carrying the provenance of that decision. Derived, never stored — a repository's answer
  must follow the configuration file, and a cached copy is what would stop it doing so.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The maintainer can change what dispatched sessions are told to invoke any lifecycle
  command with by editing one file in one place — no code change, no test change, and no file edited
  inside any repository.
- **SC-002**: A practice expressed once reaches every Spec Kit repository the daemon dispatches into,
  including ones onboarded afterwards, with no per-repository step — and an exception in one
  repository costs one section in the same file, changing nothing anywhere else.
- **SC-003**: An installation that configures nothing sees no change whatsoever in what its sessions
  are told.
- **SC-004**: Every malformed customization is reported before any session is dispatched; none is
  silently ignored, and none reaches a session altered from what was written.
- **SC-005**: For a given effective configuration, the guidance block is identical across issues and
  repeated compositions, and two repositories differ only where their configuration differs.
- **SC-006**: Given the log and the configuration file, the guidance a past dispatch carried can be
  reconstructed without re-running anything, including which setting supplied each instruction.
- **SC-007**: The two practices named in issue #39 are expressible entirely as configuration values,
  with no part of either appearing in the daemon's own text.
- **SC-008**: What any repository's sessions will be told is answerable offline, before an issue is
  labelled and without dispatching anything.

## Assumptions

- **The text is the maintainer's business.** Whether an instruction is wise, self-consistent,
  achievable, or names a command that exists on the machine is not checked and is not this
  project's concern. The only properties enforced are structural — it is text, it is not empty, it
  is not absurdly long, and it is keyed on a real lifecycle command.
- **The global setting is the practice; an override is an exception to it.** The `[repos.*]` section
  stays what milestone 005 made it — a place for exceptions, never a registration — so the expected
  state of nearly every repository is no section at all, or a section that says nothing about Spec
  Kit instructions.
- **Override replaces, it does not append.** A repository's instruction for a command is that
  command's instruction there. Appending to the global text would be a novel semantic in a project
  where four other settings already resolve by replacement, and it would leave the reader guessing
  which half comes first.
- **Configuration takes effect on the next daemon start**, consistent with every other setting.
- **The four lifecycle commands are the whole surface.** These are the commands detection already
  requires and the block already names; a customization for a command the block never mentions
  would be text with nowhere to go.
- **Both installation forms remain irrelevant.** Skills and commands are both invoked as
  `/speckit-<name>`, so the block still needs no variant and the configuration is keyed on the bare
  command name.
- **The examples in issue #39 are examples.** Neither the commit instruction nor the implement
  paragraph is a default, a fallback, or a suggestion the daemon makes.

## Dependencies

- The Spec Kit guidance block, detection, and the `[speckit] enabled` / per-repository gate from
  milestone [007](../007-speckit-extensions/spec.md). This milestone extends all three and replaces
  none.
- The existing configuration loading, validation and problem-reporting behaviour, which FR-006 and
  FR-028 ask to be reused rather than paralleled.
- The existing per-repository override and resolution pattern — `base_branch_for`,
  `permission_mode_for`, `model_for`, `speckit_enabled_for` — which FR-026 extends to four strings
  rather than reinventing, including the value-plus-provenance return that keeps two callers from
  disagreeing about the same answer.
- The existing repositories listing, which FR-027 and SC-008 hang the offline answer on rather than
  introducing a new surface.

## Out of Scope

- **What the instructions say.** Committing per phase, pushing, opening pull requests, monitoring CI,
  answering reviews, and any stopping condition for any of it are configuration values, not
  requirements of this milestone. This is the correction that produced this draft and it is the
  central boundary of the feature.
- **Free-form guidance not attached to a command.** A general "append this to every Spec Kit block"
  string would overlap the per-command mechanism and duplicate what `.claude/robot-army.md` already
  does per repository. If the per-command shape proves too narrow in use, that is the evidence to
  add it on.
- **Customizing anything outside the Spec Kit block.** The delivery block applies to every repository
  and is deliberately not configurable here; the rest of the prompt is issue text and facts about
  the worktree.
- **Commands beyond the four lifecycle rungs**, such as `/speckit-clarify` or `/speckit-analyze`.
  Detection does not require them, the block does not name them, and a session free to run them is
  already free to do so.
- **Verifying that a configured instruction was followed.** Milestone 007 settled this for the whole
  surface: an instruction the session chose not to follow is indistinguishable from one whose moment
  has not arrived, and a daemon-side check would be a second, worse source of truth.
- **Hot-reloading configuration**, and **previewing a composed prompt from the command line.** Both
  are plausible conveniences for editing prose that is only visible once dispatched, and neither is
  required to express a practice in a file. Each wants its own issue.
