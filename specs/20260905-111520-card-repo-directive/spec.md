# Feature Specification: Naming the repository outright on a card

**Feature Branch**: `robot-army/issue-116-method-of-handling-cards-with-multiple`

**Created**: 2026-09-05

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#116 — "Method of handling cards with multiple repo names in them" (label: robot-army). Some Trello cards can have the names of 2, 3, or more onboarded repos in the card description/body. Allow specifying the intended repo via a line containing `robot-army: <repo URL / path / slug>` and nothing else.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A card that names several repositories can still say which one it means (Priority: P1)

I write a card about a change that touches two of my repositories, or I paste in a log that
mentions three of them. Today that card is held: the system sees more than one onboarded
repository named and refuses to guess. I add one line to the card that says
`robot-army: owner/name`, and on the next pass the issue is filed in that repository. I did
not have to delete the other mentions, reword the description, or move anything out of the
card to get there.

**Why this priority**: This is the whole of the reported problem. Without it the only way to
resolve a multi-repository card is to mutilate the card's text until exactly one reference
survives — which destroys the context that made the card worth writing. Every other part of
this feature is refinement of the message around this one behaviour.

**Independent Test**: Write a card naming two onboarded repositories, confirm it is held, add
the line, and confirm the next evaluation files the issue in the named repository. Delivers
the entire value of the feature with nothing else built.

**Acceptance Scenarios**:

1. **Given** a card whose text names three onboarded repositories, **When** it also carries a
   line reading `robot-army: <one of them>` and nothing else, **Then** the issue is filed in
   that repository and the card is linked as it would be for an unambiguous card.
2. **Given** a card that is held awaiting clarification because it names two repositories,
   **When** I edit it to add the line and the card is evaluated again, **Then** it resolves
   and the issue is filed without any further action from me.
3. **Given** a card that names exactly one onboarded repository **and** carries a line naming
   a *different* onboarded repository, **When** it is evaluated, **Then** the issue is filed
   in the repository the line names — the line decides, and the other mentions do not.
4. **Given** a card carrying the line, **When** the issue is filed, **Then** the card's
   description appears in the issue exactly as written, including the line: nothing is
   stripped or rewritten on the way through.

---

### User Story 2 - The line accepts the same three ways of naming a repository as the rest of the card (Priority: P2)

Sometimes the thing I have to hand is a GitHub URL from the browser; sometimes it is the local
clone path from a shell; sometimes it is just `owner/name`. Whichever I paste after
`robot-army:`, it selects the repository — I do not have to remember a second, stricter
spelling that only this line accepts.

**Why this priority**: It ranks below P1 because the feature works with a single accepted
form. It ranks above P3 because a line that silently ignores a spelling used everywhere else
on the card is a trap, and a trap in the escape hatch is worse than no escape hatch.

**Independent Test**: Write the same multi-repository card three times, once with each
spelling after `robot-army:`, and confirm all three file the issue in the same repository.

**Acceptance Scenarios**:

1. **Given** a card whose line reads `robot-army: https://github.com/owner/name`, **When** it
   is evaluated, **Then** it resolves to that onboarded repository.
2. **Given** a card whose line reads `robot-army: owner/name`, **When** it is evaluated,
   **Then** it resolves to that onboarded repository.
3. **Given** a card whose line names the local clone path of an onboarded repository, **When**
   it is evaluated, **Then** it resolves to that repository.
4. **Given** a card whose line names something that is **not** an onboarded repository — a
   repository I never onboarded, a path outside every clone, a typo — **When** it is
   evaluated, **Then** no issue is filed anywhere, and the card is held.

---

### User Story 3 - When the line does not work, the card says so specifically (Priority: P3)

I mistype the repository name, or I name a repository I forgot to onboard, or I put two of
these lines on one card naming different repositories. The card is held, and the comment on it
tells me that it found my line and what was wrong with what the line said — not the generic
"name a repository", which would send me looking for a problem I have already fixed.

**Why this priority**: The system is correct without it — the card is held either way, which
is the safe direction. It matters because the failure this feature introduces is one where I
*have* done the thing being asked for, and being told to do it again is the specific kind of
unhelpful that costs an afternoon.

**Independent Test**: Write a card with a line naming a non-onboarded repository, and confirm
the held reason and the card comment name the line's own text as the problem rather than
saying no repository was identified.

**Acceptance Scenarios**:

1. **Given** a card whose line names something that is not an onboarded repository, **When**
   it is held, **Then** the reason quotes what the line said, states that it matched no
   onboarded repository, and lists the onboarded repositories.
2. **Given** a card carrying two such lines naming two different onboarded repositories,
   **When** it is evaluated, **Then** it is held with a reason saying the card gives more than
   one such line and they disagree.
3. **Given** a card carrying two such lines that name the *same* repository by different
   spellings, **When** it is evaluated, **Then** it resolves normally: two ways of saying one
   thing are one instruction.
4. **Given** a card held for any of these reasons, **When** I correct the line and it is
   evaluated again, **Then** it resolves with no further action, exactly as a card corrected
   any other way does today.
5. **Given** any evaluation of a card, **When** the durable record of that evaluation is read
   back, **Then** it says whether the repository was chosen by such a line or by the ordinary
   scan of the card's text.

---

### Edge Cases

- **Trailing whitespace and surrounding blank lines.** A line the author typed with a trailing
  space, or with the prefix in different letter case, MUST still count. "Nothing else on the
  line" is about content, not about invisible characters.
- **The line inside a quoted log.** A pasted transcript that happens to contain a line of this
  exact shape is indistinguishable from the author writing one, and MUST be treated as the
  author writing one. The safety property this feature must not weaken is the other one: the
  named repository must still be onboarded before anything is filed in it, so the worst case
  for a card full of pasted output remains a held card.
- **The line as part of a sentence.** `see robot-army: owner/name for context` is prose, not
  an instruction, and MUST NOT select anything. Only a line that is nothing but the prefix and
  a reference counts.
- **A line with the prefix and nothing after it.** `robot-army:` alone says nothing about which
  repository is meant and MUST be treated as if it were not there, rather than as an error.
- **The line names an onboarded repository that has no `[repos.*]` section.** Onboarding, not
  configuration, is what makes a repository selectable everywhere else in the system, and this
  line MUST NOT be an exception.
- **A card that carries the line and is parked in an ignored column.** Parking still wins: the
  line says which repository, not whether to act.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST recognise, on any single line of a card's text, a declaration of
  the form `robot-army: <reference>` where the line contains nothing else, and treat it as the
  author naming the repository the card is for.
- **FR-002**: Recognition MUST be insensitive to letter case in the prefix and MUST tolerate
  leading and trailing whitespace on the line and around the reference.
- **FR-003**: A line that contains anything besides the prefix and a single reference MUST NOT
  be recognised, and MUST leave the card's resolution exactly as it is today.
- **FR-004**: A recognised declaration MUST accept the same three forms of reference the system
  already understands elsewhere on a card: a GitHub repository URL, a bare `owner/name`, and a
  filesystem path at or inside a repository's local clone.
- **FR-005**: A reference in a declaration MUST select a repository only if that repository is
  onboarded. An unonboarded reference MUST NOT cause an issue to be filed anywhere.
- **FR-006**: When exactly one repository is selected by the card's declarations, that
  repository MUST be used, and every other repository reference in the card's text MUST be
  disregarded.
- **FR-007**: When a card carries more than one declaration and they select more than one
  distinct repository, the card MUST be held rather than resolved.
- **FR-008**: When a card carries declarations that all select the same repository, the card
  MUST resolve to it.
- **FR-009**: When a card carries at least one declaration and no declaration selects an
  onboarded repository, the card MUST be held, and MUST NOT fall back to the ordinary scan of
  the rest of the card's text.
- **FR-010**: When a card carries no recognised declaration, resolution MUST behave exactly as
  it does today.
- **FR-011**: The held reason for a card whose declaration matched nothing MUST quote the
  reference the declaration gave, say that it matched no onboarded repository, and list the
  onboarded repositories.
- **FR-012**: The held reason for a card with disagreeing declarations MUST say that more than
  one was given and name the repositories they selected.
- **FR-013**: The comment left on a held card MUST carry the same reason as the held state and
  MUST tell the author how to name the repository using this line.
- **FR-014**: The durable record written for each card evaluation MUST distinguish a repository
  chosen by declaration from one chosen by the ordinary scan.
- **FR-015**: The card's text MUST be carried into the filed issue unchanged; the declaration
  MUST NOT be stripped, rewritten, or interpreted as anything other than a repository name.
- **FR-016**: A card parked in an ignored column MUST remain unacted-on regardless of any
  declaration it carries.
- **FR-017**: The published guide MUST document the declaration on the page covering card
  intake.

### Key Entities

- **Repository declaration**: A line on a card that names the repository the card is for. It
  has one reference, in one of three spellings, and it either selects an onboarded repository
  or selects nothing.
- **Resolution**: The existing verdict on which repository a card is for, extended to record
  whether a declaration or the ordinary text scan produced it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A card naming any number of onboarded repositories can be made to file its issue
  in a chosen one of them by adding a single line, with no other edit to the card.
- **SC-002**: Zero cards file an issue in a repository that is not onboarded, including cards
  whose declaration names an unonboarded repository or a path outside every clone.
- **SC-003**: Every held card whose author wrote a declaration receives a reason naming that
  declaration's own text, so that no author is told to name a repository they have named.
- **SC-004**: Cards that carry no declaration resolve exactly as they did before this feature:
  no card that resolves today becomes held, and no card that is held today becomes resolvable
  without an edit.
- **SC-005**: From the durable record alone, it can be determined for any evaluated card
  whether its repository was chosen by declaration or inferred from the card's text.

## Assumptions

- The prefix is the literal `robot-army`, matching the project's own name and the label that
  gates dispatch. No configuration knob selects a different prefix; one caller does not earn
  one.
- The declaration is recognised on any line of the card's text, which is the card's title and
  description together — the same text the ordinary scan already reads. The title is a single
  line, so a title consisting solely of a declaration counts; this is a consequence of reading
  one body of text rather than a separate feature.
- A declaration wins outright rather than acting as a tie-breaker. An override that only
  applied when the system was already confused would be untestable by the author, who cannot
  see whether the system is confused until it holds the card.
- Cards are the only place this is recognised. Issues written directly in a repository already
  know which repository they are in, so there is nothing for a declaration to say there.
- The existing held/`needs_info` machinery — the state, the single comment per distinct reason,
  the re-evaluation on card edit, `robot-army rescan` — is reused unchanged. This feature adds
  reasons for holding a card, not a new way of holding one.
