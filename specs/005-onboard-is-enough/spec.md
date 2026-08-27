# Feature Specification: Onboarding Is Enough

**Feature Branch**: `005-onboard-is-enough`

**Created**: 2026-08-26

**Status**: Draft

**Input**: User description: "I would really like it if, for a repo that follows this pattern, all I
need to do is run the onboard command and not add it to the config file."

**Scope note**: This is milestone 005. It claims a slot [`docs/roadmap.md`](../../docs/roadmap.md)
currently reserves for "whatever survives contact with reality" — the parking lot of still-open §16
items. That parking lot moves to 006, because it has no shape yet and this does: one user story, one
command, one file that stops needing to be edited. The verification round in
[issue #1](https://github.com/jantman/robot-army/issues/1) will generate its own candidates for 006,
which is a further argument for not committing that slot before the round runs.

**This milestone adds no new capability.** It does not poll a new source, launch a different kind of
session, or add an interface. It changes *where the daemon learns which repositories exist* — from
the configuration file to the onboarding record — and in doing so removes a manual step that has to
be performed correctly, by hand, once per repository, before anything works.

**It is also the resolution of [issue #8](https://github.com/jantman/robot-army/issues/8).**
`include_owned` and `extra_repos` are parsed, validated, stored, and never read; `list_owned_repos()`
is implemented and has no caller. That issue framed the choice as "delete them or implement them",
and could not resolve it because a discovered repository had no `path` and no preparation steps.
Both of those gaps close here, so the answer is implement.

### Why this is possible now, and was not before

`Config` already resolves most per-repository settings with a global fallback:
`permission_mode_for()`, `model_for()`, `base_branch_for()`, and `effective_repo_cap()` each return
the repository's own value if it set one and the global default otherwise. Four of the seven
per-repository settings already behave the way this milestone wants all of them to behave.

Exactly two do not. `path` has no fallback at all, and `post_create` has no fallback despite
[001's spec](../001-minimum-daemon/spec.md) recording a decision that it should
("a single shared default preparation step covers the common dependency-environment case, with
per-repository overrides"). This milestone finishes that ladder rather than building a new mechanism
beside it.

### What the author's machine actually looks like

Measured, not assumed. These numbers are the reason the derivation rule is a single flat rule and
the reason the origin check is a refusal:

| | |
|---|---|
| Non-archived repositories the author owns | 252 |
| With a clone at exactly `<repo_root>/<name>` | 227 |
| Of those, whose clone really is that repository | **222** |
| Whose derived path resolves to a **different repository** | **5** |
| With no clone at the derived path | 25 |

The five:

| Repository | Derived path holds |
|---|---|
| `jantman/zoneminder` | `ZoneMinder/zoneminder` |
| `jantman/troposphere` | `coxmediagroup/troposphere` |
| `jantman/Trello-Desktop-MCP` | `agrath/Trello-Desktop-MCP` |
| `jantman/ford-f150-gen14-can-bus-interface` | `jantman/ford-f150-can-experiments` |
| `jantman/docker-zoneminder-OLD` | `jantman/docker-zoneminder` |

These are not missing directories. Each one exists, is a valid git repository, and has a working
tree — it is simply the wrong one. A derivation rule without verification would cut a worktree and a
branch in a repository the author never named, and the first sign of it would be a branch appearing
in someone else's clone. That is why FR-009 is a refusal and not a warning.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Onboard, and nothing else (Priority: P1)

The author labels an issue in a repository that robot-army has never heard of. Today that does
nothing: the daemon polls only repositories with a `[repos.*]` section, so the issue sits there
looking like an issue nobody labelled. To fix it the author must open the configuration file, write
a section, get the path right, restart the daemon, *and then* onboard.

After this story, the author runs `robot-army onboard jantman/some-repo`. The command works out
where the clone is, confirms it really is that repository, shows both facts on the approval screen
it already prints, and records what it resolved. From the next poll onward the repository is polled
and dispatchable. The configuration file is never opened.

**Why this priority**: This is the whole milestone. Every other story in this list exists to make
this one safe or to preserve something it would otherwise break. With only this story shipped — and
its safety story, US2, which is not separable from it in practice — the system already does the
thing the author asked for.

**Independent Test**: Onboard a repository that has no `[repos.*]` section, label an issue in it, and
watch it dispatch. Fully tested by observing a session in a repository the configuration file does
not mention.

**Acceptance Scenarios**:

1. **Given** a repository the author owns with a clone at the conventional location and no
   `[repos.*]` section, **When** the author runs `onboard` for it, **Then** the approval screen shows
   the resolved path, states that it was derived rather than configured, and shows the origin it
   verified, and approving it records the repository as onboarded.
2. **Given** that repository is onboarded, **When** the daemon next polls, **Then** the repository is
   in the polled set and a labelled issue in it becomes a work item, with no restart and no
   configuration change.
3. **Given** that work item is dispatched, **When** the worktree is created, **Then** it is cut from
   the recorded path, and the session's preparation steps are the shared default ones.
4. **Given** a repository with a `[repos.*]` section but which has never been onboarded, **When** the
   daemon polls, **Then** it is **not** polled and not dispatchable — onboarding remains the gate it
   is today ([001 FR-001](../001-minimum-daemon/spec.md)), and this milestone moves that gate rather
   than removing it.

---

### User Story 2 - A path that resolves to the wrong repository is refused (Priority: P2)

The author onboards `jantman/zoneminder`. The conventional location holds a clone of
`ZoneMinder/zoneminder` — upstream's, not theirs. The command refuses, names both the repository it
was asked about and the repository it actually found, and suggests the override that would fix it.
Nothing is recorded, and nothing is dispatchable, until the author resolves it.

**Why this priority**: US1 is not shippable without this. A derivation rule that is right 98% of the
time and silently wrong 2% of the time is worse than no derivation rule, because the 2% fails by
doing real work in the wrong place rather than by doing nothing. Five known cases exist on this
machine today.

**Independent Test**: Onboard each of the five known mismatches and confirm all five are refused with
the actual origin named. Requires no dispatch and no session.

**Acceptance Scenarios**:

1. **Given** the derived path holds a clone whose origin is a different repository, **When** the
   author runs `onboard`, **Then** it exits non-zero, names the expected and the found repository,
   and records nothing.
2. **Given** the derived path does not exist, **When** the author runs `onboard`, **Then** it exits
   non-zero saying the clone was not found at that path, and names both the setting that controls
   the root and the per-repository override.
3. **Given** the derived path exists but is not a git repository, or is a git repository with no
   `origin` remote, **When** the author runs `onboard`, **Then** each case is refused with a message
   naming that specific cause rather than a generic failure.
4. **Given** two clone URLs that differ only in transport or suffix — `git@github.com:owner/name.git`
   against `https://github.com/owner/name` — **When** they are compared, **Then** they are treated as
   the same repository.

---

### User Story 3 - The exceptions keep working (Priority: P3)

The author has repositories the convention does not fit: five whose derived path is another
repository, a nested grouping layout for some upstream projects, and roughly fifteen that need
bespoke preparation steps. For each of these the author writes a `[repos.*]` section exactly as
today, and it wins over anything derived.

**Why this priority**: This is what makes the change safe to adopt rather than a migration. Every
existing configuration keeps working unchanged, and the file stops being a registry of every
repository and becomes a list of exceptions.

**Independent Test**: Onboard one repository with an explicit `path` pointing somewhere
unconventional and confirm the derived location is never consulted; confirm an existing full
configuration behaves identically to before.

**Acceptance Scenarios**:

1. **Given** a `[repos.*]` section with an explicit `path`, **When** the author onboards it,
   **Then** that path is used, derivation is not attempted, and the approval screen says the path was
   configured rather than derived.
2. **Given** a configured path, **When** it is verified, **Then** the origin check runs against it
   too — a configured path can be wrong as easily as a derived one.
3. **Given** a repository is already onboarded with a recorded path, **When** a `[repos.*] path` is
   later added or changed that disagrees with the record, **Then** dispatch is blocked pending
   `onboard --reapprove`, which shows both paths — mirroring how a changed settings fingerprint is
   already handled.
4. **Given** a repository whose clone lives in a nested grouping directory, **When** the author
   writes a `path` override for it, **Then** it behaves exactly as any other configured repository.

---

### User Story 4 - Preparation steps have a default (Priority: P4)

The author sets one `post_create` block that covers the common case — the dependency-environment
step nearly every repository needs. Repositories that need something else say so in their own
section. A repository that has been onboarded but never configured gets the default, not nothing.

**Why this priority**: Without this, US1 delivers a repository that dispatches into an unprepared
worktree. That is not broken, but it is not useful either, and it would push the author straight back
into the configuration file — which is the thing this milestone exists to stop.

**Independent Test**: Onboard two repositories, give one an override, and confirm the shared default
runs in the other and the override runs in the first.

**Acceptance Scenarios**:

1. **Given** `[hooks] post_create` is set and a repository has no section, **When** a worktree is
   prepared for it, **Then** the shared steps run.
2. **Given** a repository whose section sets its own `post_create`, **When** a worktree is prepared,
   **Then** its own steps run and the shared ones do not — an override replaces, it does not append.
3. **Given** neither is set, **When** a worktree is prepared, **Then** no preparation steps run,
   which is exactly today's behaviour.
4. **Given** the shared steps are set, **When** the configuration is validated, **Then** their
   timeouts are summed into the same startup budget warning per-repository steps already feed.

---

### User Story 5 - The clone moved (Priority: P5)

Months later the author reorganises `~/GIT`. A repository onboarded long ago now has its clone
somewhere else. The next dispatch for it refuses, names the path it recorded, and raises an anomaly.
It does not re-derive, and it does not quietly find a different directory that happens to match the
name.

**Why this priority**: This is the failure mode that resolving at onboard rather than at runtime
exists to produce. Re-deriving on every dispatch would make a directory reshuffle silently repoint a
live work item, which is the same class of error as US2 and harder to notice because nothing about
the command the author ran changed.

**Independent Test**: Onboard a repository, rename its clone directory, and attempt a dispatch.
Requires no second repository.

**Acceptance Scenarios**:

1. **Given** an onboarded repository whose recorded path no longer exists, **When** an item for it is
   dispatched, **Then** the item lands in `failed` naming the recorded path, an anomaly is raised,
   and no worktree is created anywhere.
2. **Given** an onboarded repository whose recorded path now holds a different repository, **When**
   an item for it is dispatched, **Then** it is refused the same way — the origin is re-checked at
   dispatch, not only at onboarding.
3. **Given** either refusal, **When** the author re-runs `onboard --reapprove`, **Then** the path is
   re-resolved and re-verified, and dispatch resumes.

---

### User Story 6 - What may be onboarded at all (Priority: P6)

The author mistypes a repository name, or names a repository belonging to someone else. Onboarding
refuses, saying which setting would have permitted it. `include_owned` governs repositories the
author owns; `extra_repos` names specific repositories they do not.

**Why this priority**: It closes issue #8 and gives both settings a meaning that matches their names.
It is last because it is the least valuable of the six on any ordinary day — it catches typos, and
the author is the only person who runs this command.

**Independent Test**: Attempt to onboard a repository the author neither owns nor listed; confirm the
refusal names the setting. Requires no session and no dispatch.

**Acceptance Scenarios**:

1. **Given** `include_owned = true` and a repository the author owns, **When** they onboard it,
   **Then** it is permitted.
2. **Given** a repository the author does not own and which is not in `extra_repos`, **When** they
   onboard it, **Then** it is refused, naming `extra_repos` as the setting that would permit it.
3. **Given** `include_owned = false` and a repository the author owns but did not list, **When** they
   onboard it, **Then** it is refused, naming `include_owned`.
4. **Given** any of these checks, **When** it runs, **Then** it consults only the repository being
   named — enumerating every repository the author owns is not required to answer a question about
   one of them.

---

### User Story 7 - See what could be onboarded (Priority: P7)

The author asks which repositories they own that are not yet onboarded and have a clone where one is
expected, so they can pick from a list rather than remember names.

**Why this priority**: Genuinely droppable, and named as such. It is the only story here that adds a
surface rather than removing a step, and the author can already get the same answer from a shell
one-liner. It is included because it is the only remaining candidate caller for
`list_owned_repos()`, which exists today with none.

**Independent Test**: Run the listing against a live account and confirm already-onboarded
repositories are excluded.

**Acceptance Scenarios**:

1. **Given** the author owns repositories, **When** they ask what is onboardable, **Then** each is
   listed with whether a clone was found at its conventional location and whether that clone's origin
   matches.
2. **Given** a repository is already onboarded, **When** the listing runs, **Then** it is marked as
   such rather than offered again.

**If this story is dropped**, `list_owned_repos()` and its protocol declaration MUST be deleted
rather than left in place, and issue #8's "dead code" half is resolved by removal. Leaving an
implemented method with no caller is the state that produced issue #8 in the first place.

---

### Edge Cases

- **The derived path is inside the worktree root.** A repository whose conventional location happens
  to fall under the directory robot-army cuts worktrees into must be refused, not onboarded — the two
  would fight over the same tree.
- **The derived path is itself a git worktree** of another repository rather than a primary clone.
  Worktrees are cut from a primary clone (M0), so this is refused with that named as the cause.
- **Two repository keys derive to the same path**, because the owner differs and the name does not.
  The origin check refuses at most one of them; the other is legitimately that clone.
- **Repository name case.** GitHub treats `jantman/ZoneMinder` and `jantman/zoneminder` as the same
  repository; a case-sensitive filesystem does not. The comparison is case-insensitive and the path
  is taken from the filesystem as it actually exists.
- **The recorded path is a symlink**, or becomes one later. It is resolved to a real path when
  recorded, so the record does not depend on a link that may be repointed.
- **`repo_root` itself does not exist**, or is not a directory. This is a configuration problem and
  is reported at load, not discovered per repository at onboarding time.
- **A repository is onboarded, then removed from `extra_repos`.** The allowlist governs onboarding,
  not continued operation; an already-onboarded repository keeps working. Revoking it is what
  removing the onboarding record is for.
- **The onboarding record exists but the configuration now sets a different `base_branch`, `model`,
  or `permission_mode`.** These already resolve through the existing fallback ladder at read time and
  continue to do so — only `path` is frozen at onboarding, because only `path` decides *which
  repository* is acted upon.
- **A `[repos.*]` section names a repository that was never onboarded.** It is not polled and not
  dispatchable, and `robot-army repos` says so — a section is no longer evidence that a repository is
  in use.

## Requirements *(mandatory)*

### Path Resolution

- **FR-001**: The system MUST provide a configurable root directory under which repository clones are
  expected, defaulting to the author's conventional location, and MUST report at configuration load
  if it is absent or not a directory.
- **FR-002**: For a repository with no explicitly configured path, the system MUST derive its clone
  location as a single candidate: the repository's own name directly beneath that root. Exactly one
  candidate is derived; the system MUST NOT search, walk, or try alternatives.
- **FR-003**: An explicitly configured path MUST suppress derivation entirely for that repository.
- **FR-004**: Path resolution MUST occur only during onboarding. No later operation may derive a
  path.

### Verification

- **FR-005**: Before recording anything, onboarding MUST confirm the resolved path exists, is a
  primary git clone, and has an origin remote.
- **FR-006**: Onboarding MUST compare that origin against the repository it was asked about,
  normalising transport, host form, and any trailing suffix so that equivalent URLs compare equal,
  and comparing repository identity case-insensitively.
- **FR-007**: Verification MUST apply equally to configured and derived paths.
- **FR-008**: The system MUST refuse to onboard a repository whose resolved path falls inside the
  worktree root, or which is a linked worktree rather than a primary clone, naming the cause.
- **FR-009**: A verification failure MUST be a refusal with a non-zero exit, never a warning that
  proceeds. Each distinct cause — absent, not a repository, no origin, wrong repository — MUST
  produce a message naming that cause and, where one exists, the override that would resolve it.

### The Onboarding Record

- **FR-010**: The onboarding record MUST persist the resolved path, whether it was derived or
  configured, and the origin that was verified.
- **FR-011**: The approval screen MUST show all three before the author approves, alongside what it
  already shows.
- **FR-012**: All later operations MUST read the recorded path. No operation may substitute a
  freshly derived or freshly configured path for it.
- **FR-013**: Where a configured path later disagrees with the recorded one, dispatch for that
  repository MUST be blocked pending explicit re-approval, which MUST show both paths.
- **FR-014**: A schema change MUST carry existing onboarding records forward. Records predating this
  milestone carry no path and MUST be treated as requiring re-approval rather than being guessed at
  or silently dropped.

### Which Repositories Are Known

- **FR-015**: The set of repositories polled MUST be the set of onboarded repositories.
- **FR-016**: A repository that has a configuration section but has not been onboarded MUST NOT be
  polled and MUST NOT be dispatchable.
- **FR-017**: Every operation that today answers "which repositories are known" from the
  configuration file MUST answer it from the onboarding record instead, and MUST NOT report a
  repository as known merely because a section describes it.
- **FR-018**: Requesting a repository's settings MUST return a resolved result for any onboarded
  repository, combining its record with its section where one exists and the existing global defaults
  where it does not — rather than failing because no section exists.

### Preparation Steps

- **FR-019**: The system MUST support a shared default set of preparation steps applying to any
  repository that does not define its own.
- **FR-020**: A repository's own steps MUST replace the shared default rather than extending it.
- **FR-021**: A repository with neither MUST run no preparation steps, preserving current behaviour.
- **FR-022**: Shared steps MUST be validated and budgeted exactly as per-repository steps are today,
  including their contribution to the startup timeout warning.

### Onboarding Eligibility

- **FR-023**: The system MUST permit onboarding a repository the author owns when the
  own-repositories setting is enabled, and MUST permit onboarding any repository explicitly listed.
- **FR-024**: A repository permitted by neither MUST be refused at onboarding, naming which setting
  would have permitted it.
- **FR-025**: Eligibility MUST be determined by consulting only the repository named. Enumerating
  every repository the author owns MUST NOT be required to onboard one of them.
- **FR-026**: This check is a mistake guard, not a security boundary, and MUST be documented as such.
  The security boundary remains the issue-author check, which is unchanged and cannot be disabled.
- **FR-027**: Eligibility governs onboarding only. An already-onboarded repository MUST continue to
  operate if the setting that permitted it later changes.

### Runtime Safety

- **FR-028**: Before creating a worktree, the system MUST confirm the recorded path still exists and
  still holds the repository it was verified as. A failure MUST fail the item, raise an anomaly, and
  create nothing.
- **FR-029**: The system MUST NOT create a worktree or a branch in any repository other than the one
  named by the work item.

### Accountability

- **FR-030**: Onboarding MUST record, before the outcome, what it resolved: the path, how, and the
  origin comparison — so a later reader can reconstruct why a repository points where it does without
  re-running anything.
- **FR-031**: Every refusal MUST be recorded with its cause.
- **FR-032**: Nothing recorded may contain a credential, including where a clone URL embeds one. A
  URL carrying credentials MUST be redacted in the record while still permitting the comparison.

### Key Entities

- **Repository root**: the single directory beneath which clones are expected. One value, not a
  search path.
- **Resolved location**: the outcome of onboarding a repository — an absolute path, whether it was
  derived or configured, and the repository identity confirmed at that path. Recorded once, read
  thereafter.
- **Onboarding record**: the existing per-repository trust record, extended with the resolved
  location. It becomes the answer to "which repositories does this system know about", a question the
  configuration file answers today.
- **Repository settings**: the per-repository values a dispatch needs. Today an entry that must exist;
  after this milestone, a set of overrides layered over defaults, present or not.
- **Shared preparation steps**: the default steps for a repository that names none.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A repository following the convention becomes pollable and dispatchable with exactly
  one command and zero edits to any file.
- **SC-002**: At least 220 of the author's 252 non-archived owned repositories can be onboarded
  without a configuration edit. Measured today: 222 qualify.
- **SC-003**: All five known wrong-location repositories are refused at onboarding, and none of them
  ever receives a worktree or a branch.
- **SC-004**: Across the whole milestone, zero worktrees and zero branches are created in a
  repository other than the one named by the work item.
- **SC-005**: A clone moved or replaced after onboarding produces a refusal naming the recorded path
  on the first dispatch attempt, and never a worktree in a different repository.
- **SC-006**: The configuration file needed to run the system contains only exceptions. On the
  author's machine that is at most twenty-five sections, against one per dispatchable repository
  today.
- **SC-007**: For any onboarded repository, the log alone answers where its clone is, whether that
  was derived or configured, what was found there, and when it was approved — without re-running
  anything.
- **SC-008**: Every existing configuration continues to behave identically. A repository with an
  explicit path and its own preparation steps sees no change in what runs or where.
- **SC-009**: Onboarding a repository requires at most one request to the source system beyond those
  onboarding already makes, regardless of how many repositories the author owns.

## Assumptions

- **One rule, one candidate.** The nested `<root>/<owner>/<name>` grouping directories on the
  author's machine hold repositories they do not own and would not dispatch into. They are served by
  an explicit override rather than a second derivation rule, per Principle I — a search path with one
  known user is a configuration knob with no second use in hand.
- **An override replaces.** Per-repository preparation steps replace the shared default rather than
  appending to it, because the repositories that need their own steps need *different* steps, not
  extra ones. Appending would make the shared default undroppable.
- **A changed path needs re-approval.** Only `path` is frozen at onboarding, because only `path`
  decides which repository is acted upon. The other per-repository settings continue to resolve at
  read time through the existing fallback ladder, and changing them needs no ceremony.
- **A missing clone is not a defect.** The 25 owned repositories with no clone at the conventional
  location are simply not onboardable until they are cloned. Cloning them is the author's business.
- **Onboarding stays deliberate and human.** Nothing here makes onboarding automatic, batched, or
  implied by anything else. It remains the one-per-repository decision it is today; this milestone
  only removes the file edit that had to accompany it.
- **The effect ladder does not apply.** Onboarding makes no outward-facing write — it reads a local
  clone, reads one repository's metadata, and writes locally. It is unaffected by effect level today
  and remains so.
- **Ownership is answered per repository.** A single lookup of the named repository determines
  ownership, rather than enumerating the author's 252.

## Out of Scope

- **Cloning anything.** If no clone exists at the resolved path, onboarding refuses. It does not
  clone, offer to clone, or clone on first dispatch.
- **Multiple roots or a search path.** One root, one candidate. If a second root is ever genuinely
  needed, that is a later change with a demonstrated need behind it.
- **Re-deriving at runtime**, under any circumstance, including as a fallback when the recorded path
  is missing. That failure is a refusal, by design.
- **Automatic or bulk onboarding.** Onboarding remains one repository at a time, with the author
  looking at the approval screen.
- **Removing per-repository configuration sections.** They stay, as overrides. Nothing about an
  existing configuration file becomes invalid.
- **Any change to the issue-author security boundary**, which is untouched and remains
  non-disableable.
- **Reconciling repositories that were onboarded and whose clone has since been deleted.** They fail
  at dispatch, loudly, which is sufficient; a background sweep for absent clones is not built.

## Dependencies & Follow-on

- Resolves [issue #8](https://github.com/jantman/robot-army/issues/8), which should be closed with a
  pointer here rather than separately fixed.
- **[Issue #1](https://github.com/jantman/robot-army/issues/1)'s milestone 001 scenario 6 must be
  re-run** after this lands. It is the onboarding-refusals scenario, and this milestone changes what
  onboarding refuses and what it prints. Every other scenario in that round is unaffected.
- The verification round in issue #1 should complete **before** this is implemented, so that round
  verifies the system as built rather than a system changing underneath it.
- [`docs/roadmap.md`](../../docs/roadmap.md) needs its 005 entry replaced with this and the
  "whatever survives contact with reality" parking lot renumbered to 006.
- The shipped example configuration and the configuration contract both document `include_owned` as
  "poll every repo you own", which is wrong in two ways and must be corrected as part of this work.
