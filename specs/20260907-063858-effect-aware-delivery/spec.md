# Feature Specification: The delivery block follows the effect level

**Feature Branch**: `robot-army/issue-32-the-effect-ladder-constrains-robot-army`

**Created**: 2026-09-07

**Status**: Draft

**Input**: jantman/robot-army issue #32 — "The effect ladder constrains robot-army, not the
session it launches: a `no-remote` dispatch pushed a branch to GitHub"

## Context

The effect ladder is enforced at five seams. The session a dispatch launches is not one of
them: it is what `SessionHost` *launches*, and it runs as the same operating-system user, with
the same credentials and the same network. At `effect_level = "no-remote"` a dispatched session
committed and pushed a branch to GitHub. The daemon itself did nothing wrong — it posted no
comment and recorded the write as simulated — but the level's name, the contract, the quickstart
scenario heading and the guide all read as promises about the *system*, and they are not.

Since the standing delivery block was added, every dispatched session is told to push its branch
and open a pull request. That turns a one-off into the rule: at `no-remote`, every dispatch now
pushes and opens a pull request, and the level stops meaning anything close to its name.

Two things are therefore wrong at once — what the words claim, and what the prompt asks for — and
this feature fixes both. It deliberately does **not** try to make the constraint enforceable;
see Out of Scope.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A rehearsal at a reduced level does not reach GitHub (Priority: P1)

The maintainer runs the daemon at `no-remote` to watch a real session work an issue without the
run touching anything outside the machine. The session receives delivery instructions that match
the level it was dispatched at: commit the work on its branch, and do not push it, open a pull
request, or write anything back to the issue. At `live`, the instructions are what they are
today — push the branch, open the pull request.

**Why this priority**: it is the behaviour the issue was filed about, and the only part of this
feature that changes what a session does. Everything else is words about it.

**Independent Test**: compose the dispatch prompt at `no-remote` and at `live` for the same
issue and compare the delivery section; run a dispatch at `no-remote` and confirm no branch
appears on the remote.

**Acceptance Scenarios**:

1. **Given** the daemon is running at `live`, **When** an issue is dispatched, **Then** the
   session's prompt instructs it to commit, push the branch to `origin`, and open a pull
   request.
2. **Given** the daemon is running at `no-remote`, **When** an issue is dispatched, **Then** the
   session's prompt instructs it to commit its work on the branch and states that it must not
   push, must not open a pull request, and must not write to the issue or any other remote.
3. **Given** the daemon is running at `no-remote`, **When** a dispatched session finishes,
   **Then** the branch exists only in the local worktree and the remote has no branch for it.
4. **Given** either level, **When** the prompt is composed, **Then** the delivery section still
   holds regardless of how the issue's text is worded, including text that claims the rules no
   longer apply.

---

### User Story 2 - The words say what the ladder actually governs (Priority: P2)

A reader of the contract, the quickstart or the guide learns that the effect ladder constrains
robot-army's own boundaries, that the session it launches is outside those boundaries, and that
the reduced-level delivery instruction is an instruction to a session rather than a sandbox
around it.

**Why this priority**: the missing sentence is what let the surprise happen, and it stays
missing whatever the prompt says. A reader who believes `no-remote` is enforced against the
session will be wrong again the first time a session decides otherwise.

**Independent Test**: read `contracts/boundaries.md`, quickstart scenario 3 and the guide's
effect-level section, and check that each states the limit of what the level guarantees.

**Acceptance Scenarios**:

1. **Given** the boundaries contract, **When** it is read, **Then** it states that the effect
   level governs the seams it lists and does not govern what a launched session does.
2. **Given** quickstart scenario 3, **When** it is read, **Then** its heading does not promise
   "no GitHub writes", and its body says what is and is not checked.
3. **Given** the guide's effect-level page, **When** it is read, **Then** it says the ladder
   describes robot-army's own reach, that the session is told to match it below `live`, and that
   the telling is not enforcement.
4. **Given** the generated example configuration, **When** `effect_level` is read there,
   **Then** its comment does not frame the level as a limit on what the machine may touch.

---

### User Story 3 - The preview is still the prompt (Priority: P3)

The maintainer runs the prompt preview to see what a session would be told. The delivery section
it prints is the one that a dispatch at the level in effect would use.

**Why this priority**: the preview exists so the prompt can be inspected without dispatching. A
preview that always showed the `live` wording would be a new way to be misled about exactly this
feature.

**Independent Test**: run the preview at two levels for the same issue and confirm the delivery
section differs, and matches what a dispatch at each level composes.

**Acceptance Scenarios**:

1. **Given** the preview is run while the effect level is `live`, **When** the prompt is
   printed, **Then** its delivery section is the push-and-pull-request one.
2. **Given** the preview is run while the effect level is below `live`, **When** the prompt is
   printed, **Then** its delivery section is the local-only one.
3. **Given** a preview and a dispatch at the same level for the same issue, **When** both
   compose a prompt, **Then** their delivery sections are identical.
4. **Given** the preview is asked for a level other than the configured one, **When** the prompt
   is printed, **Then** it is the one a dispatch at the asked-for level would compose.

---

### Edge Cases

- **An instruction above the block says to push.** Not hypothetical: this repository's own
  configured Spec Kit instruction for `implement` says "commit, push the branch to origin, and
  open a PR", and it is composed *above* the delivery block, where position gives it precedence.
  A `.claude/robot-army.md` can say the same. The local-only form therefore says that such an
  instruction describes a `live` dispatch and does not apply to a contained run — and says it
  about outward writes only, changing nothing else those instructions ask for.
- **`plan` and `local` never launch a session.** The reduced wording is still what those levels
  compose, because the preview can be run at them and a prompt that varies by anything other
  than the level would be a second rule to remember.
- **An item dispatched at one level and resumed at another.** The prompt is composed afresh at
  every launch, so a resume carries the delivery section for the level in effect *now*, not the
  one it was first dispatched at.
- **An issue that wants an investigation and an answer, not a branch.** Both variants keep the
  "when there is work to deliver" framing; neither turns into a mandate to produce commits.
- **A session at a reduced level that pushes anyway.** Nothing prevents it. The documentation
  says so, and the level's guarantee is stated as covering robot-army's own writes only.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The composed prompt's delivery section MUST have two forms — one instructing the
  session to push its branch and open a pull request, one instructing it to keep its work local
  — and which one is used MUST be decided by the effect level in force.
- **FR-002**: The push-and-pull-request form MUST be used at `live` and at no other level.
- **FR-003**: The local-only form MUST be used at every level below `live`.
- **FR-004**: The local-only form MUST tell the session, in its own words rather than by
  omission, that it must not push a branch, must not open a pull request, and must not write to
  the issue or any other remote system.
- **FR-005**: The local-only form MUST say where the work does go: committed on the feature
  branch in the worktree, where the operator can inspect it.
- **FR-006**: Both forms MUST keep the properties the standing block already has: work happens
  on the session's feature branch and never on the default branch; the repository is the
  mechanism for changing the things the repository manages; building, running, testing,
  installing dependencies and reading live systems are outside the limit; and the section
  asserts its own precedence over the issue text rather than conceding it.
- **FR-006a**: The local-only form MUST reconcile itself with instructions composed above it:
  where a repository's standing instructions or a configured Spec Kit instruction ask for a push
  or a pull request, the form MUST say that those describe a `live` dispatch and do not apply to
  this run. It MUST scope that to outward writes and leave every other instruction above it
  intact, and it MUST NOT appear in the push-and-pull-request form.
- **FR-007**: Neither form may be selected, suppressed or overridden by anything the issue's
  author wrote, by a configuration key, or by a per-repository setting. The effect level is the
  only input.
- **FR-008**: The prompt preview MUST compose the same delivery section that a dispatch at the
  same effect level would compose.
- **FR-008a**: The prompt preview MUST accept an effect level for the run, overriding the
  configured one, so that "what would a session be told at this level?" can be asked without
  editing the configuration file. It is the same override the daemon and the web interface
  already accept.
- **FR-009**: The effect level MUST stay out of the code downstream of boundary wiring: the
  choice between the two forms is made where the boundaries are selected, not by a later caller
  asking what level it is running at.
- **FR-010**: The audit record already written for a prompt preview MUST name which delivery
  form was composed, so the log can answer what a session was told without recording the prompt
  itself.
- **FR-011**: `contracts/boundaries.md` MUST state that the effect level governs the boundaries
  it tabulates and does not constrain a session launched through `SessionHost`, and MUST name
  the delivery instruction as the mitigation and not as enforcement.
- **FR-012**: Quickstart scenario 3's heading MUST stop claiming "no GitHub writes", and its
  body MUST say that the check is on robot-army's own writes.
- **FR-013**: The guide's effect-level section MUST say the same thing in the operator's terms,
  including that a session below `live` is *asked* not to reach the network and that nothing
  stops one that ignores the request.
- **FR-014**: The example configuration's comment for `effect_level` MUST NOT describe the level
  as how much of the world the system may touch.

### Key Entities

- **Delivery section**: the fixed block of prose in every composed prompt that says how the work
  is expected to be delivered. Gains a second form; keeps its position, its precedence and its
  independence from the issue text.
- **Effect level**: the existing four-rung ladder. Gains one more thing it selects, at the same
  place it selects everything else.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A dispatch at `no-remote` of an issue whose work produces commits leaves zero new
  branches on the remote and zero new pull requests, where before this change it left one of
  each — including for a Spec Kit issue in a repository whose configured `implement` instruction
  says to push.
- **SC-002**: Composing the prompt for the same issue at `live` and at `no-remote` produces
  delivery sections that differ, and the two forms are the only two that can be produced.
- **SC-003**: The `live` prompt's delivery section is unchanged from what it is today, so no
  live dispatch behaves differently.
- **SC-004**: A reader of `contracts/boundaries.md`, quickstart scenario 3 and the guide's
  effect-level section can state, from those three sources alone, what the level does and does
  not guarantee about a launched session.
- **SC-005**: The prompt preview and a dispatch at the same level agree on the delivery section
  in every case, verified rather than asserted.
- **SC-006**: The full unit test suite passes.

## Out of Scope

- **Actually constraining the session** — a credential-less environment, a sandbox, a
  network-restricted launch. Option 3 in the issue, and much larger. The session is meant to be
  a real interactive session with the maintainer's own tooling; taking its credentials away
  changes what the product is. This feature makes the reduced levels honest by asking, and makes
  the asking's limits explicit.
- **A configuration key to choose the delivery form independently of the level.** One caller,
  one correct answer, and a second way to get this wrong.
- **Reading or reacting to what a session actually pushed.** Detecting a violation is a
  different feature and needs a different mechanism than the prompt.

## Assumptions

- The standing delivery block is already implemented and unconditional (issue #29, milestone
  012); this feature makes it conditional rather than introducing it.
- Reads stay real at every level, so the "read whatever you need, including live systems" line
  is correct in both forms and does not need to vary.
- The two levels that never launch a session (`plan`, `local`) still compose a prompt for the
  preview, and giving them the same local-only form as `no-remote` is simpler than a third case.
- The audit log continues not to record the composed prompt, the issue body, or the contents of
  any section; naming the delivery form is a flag, not the text.
- No published-site page other than the ones named needs to change, and no configuration key is
  added, removed or renamed.
