# Feature Specification: Docs overhaul and example config

**Feature Branch**: `robot-army/issue-136-docs-overhaul-and-example-config`

**Created**: 2026-09-05

**Status**: Draft

**Input**: jantman/robot-army issue #136 — "Docs overhaul and example config". The single
1,180-line README is split into a published guide under `docs/guide/`; a subcommand grows
that renders a fully commented example `config.toml`, whose rendered output is committed
and embedded in the guide; and an agent guidance file at the repository root records how
both are kept current.

Two choices the issue left open were settled with the author before this spec was written,
and are recorded here as decided rather than as open questions:

* The guide is broken down **by pipeline** — one issue followed end to end, in the order
  the system touches it — over the four layouts proposed (by task, by subsystem, minimal,
  by pipeline).
* The example config is delivered in **both** forms the issue offered, from one source of
  truth: a subcommand renders it, the rendered output is committed, and a test fails when
  the two disagree.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Get a working config without reading the README (Priority: P1)

The author is setting this up on a new machine, or has forgotten which section a setting
lives in. They run one command, get a complete, commented `config.toml` on stdout or at a
path they name, edit the handful of values that are actually theirs, and start the daemon.

**Why this priority**: It is the one thing the issue says has no quick path today, it is
independently useful with no documentation change at all, and every other story is easier
to write once a single generated artefact is the thing the documentation points at.

**Independent Test**: Run the subcommand, redirect its output to a file, point the loader
at that file, and confirm it loads without error. No guide page needs to exist.

**Acceptance Scenarios**:

1. **Given** a machine with no `config.toml`, **When** the author runs the example-config
   subcommand and writes its output to the default config path, **Then** the file loads
   through the existing loader with no problems reported, and the values in it are the
   documented defaults.
2. **Given** the generated example, **When** the author reads any section, **Then** every
   key carries a short comment saying what it does, and every key the loader accepts is
   present.
3. **Given** the generated example, **When** the author greps it for a credential,
   **Then** it contains none — the credential keys name environment variables and file
   paths, and are commented out or given placeholder names rather than real values.
4. **Given** an existing file at the requested output path, **When** the author runs the
   subcommand targeting that path, **Then** the existing file is not overwritten unless
   the author explicitly asks for that, and the refusal exits non-zero.

---

### User Story 2 - Find one answer without scrolling a 1,180-line README (Priority: P2)

The author wants to know why nothing dispatched, or what a session is told, or where the
logs are. They open the published site, pick the pipeline stage the question belongs to,
and read one page of a few hundred lines rather than searching one of twelve hundred.

**Why this priority**: It is the issue's headline complaint, but it depends on nothing —
it can ship after story 1 and is worth having even if the example config never changed.

**Independent Test**: Browse the guide from its landing page and confirm every subject the
old README covered is reachable in two clicks and appears exactly once.

**Acceptance Scenarios**:

1. **Given** the published site, **When** the author lands on it, **Then** they see what
   the project is and a link into the guide, and the guide's own index shows the pipeline
   stages in order with a one-line description of each.
2. **Given** any subject the old README documented, **When** the author looks for it,
   **Then** it appears on exactly one guide page — not duplicated across two — and the
   page it is on is the pipeline stage where that subject takes effect.
3. **Given** the repository on GitHub, **When** the author opens `README.md`, **Then** it
   is a short overview with a link to the published guide, and does not restate the
   guide's content.
4. **Given** the published site, **When** the author navigates it, **Then** the project's
   history — the roadmap, incident write-ups, verification notes, and initial planning —
   is not part of the guide's navigation, though those files remain in the repository
   where they are.

---

### User Story 3 - Documentation that does not rot (Priority: P3)

A later change adds a config key or changes a behaviour. The author, or an agent session
working on this repository, is told at the point of making that change that the guide page
and the example config are part of it — and the test suite refuses the change if the
committed example no longer matches what the generator produces.

**Why this priority**: It protects the value of the first two stories rather than
delivering value itself, and it is worth nothing until they exist. The current
`share/config.example.toml` — referenced from nowhere, already missing three sections — is
the evidence that instructions alone would not have been enough.

**Independent Test**: Add a key to the loader's accepted-key set without touching the
generator, run the test suite, and confirm it fails with a message naming the missing key.

**Acceptance Scenarios**:

1. **Given** a new key accepted by the loader, **When** the suite runs without the
   generator having been updated, **Then** a test fails and names the key that is absent
   from the example.
2. **Given** an updated generator, **When** the suite runs without the committed example
   having been regenerated, **Then** a test fails and says how to regenerate it.
3. **Given** an agent session starting work in this repository, **When** it reads the
   agent guidance file at the repository root, **Then** it is told to update the affected
   guide page and to regenerate the committed example as part of any change that alters
   behaviour or configuration.

---

### Edge Cases

- **A key exists in the loader but has no sensible example value.** Credential keys are
  the case: `token_env` names an environment variable and `token_file` names a path, and
  only one of each pair is used at a time. The example must show both forms without
  producing a config that fails validation — so at most one of each mutually exclusive
  pair is active and the other is present as a commented alternative, and the coverage
  test must count a commented key as covered.
- **Sections that must stay absent to stay inert.** `[trello]` and `[pushover]` are
  `None` when absent, which is what makes an unconfigured install make no outbound request
  at all. The example cannot ship them active, or copying it turns on a board poll against
  an empty board id. They appear commented out, and the generated file must load clean
  with them in that state.
- **A `[repos.*]` section in the example.** Unknown keys inside `[repos.*]` are an error,
  not a warning, so the example's repository section must be exactly right or copying it
  fails to load. It also names a repository that does not exist, so it must be commented
  out or use a key the author will obviously replace.
- **The published site would otherwise include the whole `docs/` tree.** `docs/roadmap.md`,
  the incident and verification notes, and `docs/initial-planning/` all live there. They
  must not appear in the guide's navigation, and the build must not fail on them.
- **A guide page links to a page that was renamed or never written.** Every internal link
  must resolve to a file that exists, and that must be checked mechanically rather than by
  reading.
- **The old README's anchors are linked from elsewhere.** Issue comments and commit
  messages in this repository's history link to README sections. Those links break; the
  README's pointer to the guide is the mitigation, and no redirect mechanism is built.
- **The generator runs on a machine whose environment changes a default.** One default is
  computed from the environment (the terminal socket glob is rooted under the runtime
  directory). A generated example that embeds this machine's value would differ from the
  committed copy on another machine and fail the drift test, so environment-derived
  defaults must be rendered in a stable form rather than as the resolved local value.

## Requirements *(mandatory)*

### Functional Requirements

#### The guide

- **FR-001**: The documentation MUST be published from the repository's `docs/` directory
  by GitHub Pages' built-in branch-deployment mechanism. No hand-authored workflow file
  may build or publish it.
- **FR-002**: The guide MUST consist of these pages, under `docs/guide/`, following one
  issue through the system in the order the system touches it: an index (what it does and
  the pipeline at a glance), setup, intake, selection, session, outcome, operating, and
  configuration.
- **FR-003**: `docs/index.md` MUST be the published site's landing page, stating what the
  project is and linking into the guide.
- **FR-004**: Every subject covered by the README as it stands MUST appear on exactly one
  guide page. Nothing is dropped, and nothing is documented twice.
- **FR-005**: `README.md` MUST be reduced to a high-level overview — what the project is,
  what it does, roughly how it works — plus a pointer to the published guide, and MUST NOT
  restate the guide's content.
- **FR-006**: The content of `docs/logging.md` and `docs/state.md` MUST be folded into the
  guide's operating page, and those two files MUST NOT remain as separate copies of the
  same material.
- **FR-007**: `docs/roadmap.md`, `docs/incident-*.md`, `docs/verification-*.md`, and
  `docs/initial-planning/` MUST remain in the repository at their present paths and MUST
  NOT appear in the guide's navigation.
- **FR-008**: Every internal link between guide pages, and every link from `README.md` and
  `docs/index.md` into the guide, MUST resolve to a file that exists in the repository.
- **FR-009**: The guide MUST be written for the author's future self — what it does, how
  to run it, where the logs are and what they mean. It MUST NOT acquire contribution
  guides, issue templates, support channels, or end-user tutorials.

#### The example config

- **FR-010**: A `robot-army` subcommand MUST render a complete example `config.toml`.
- **FR-011**: The rendered example MUST include every key the configuration loader
  accepts, in every section it accepts, including the per-repository section's keys.
- **FR-012**: Every key in the rendered example MUST carry a brief comment saying what it
  does — on the same line or on the line above it.
- **FR-013**: The rendered example MUST load through the existing configuration loader
  with no problems reported, exactly as generated, with no edits.
- **FR-014**: The rendered example MUST contain no credential values. Keys that name
  environment variables or credential files MUST show a placeholder name or path, and MUST
  NOT contain anything the loader's own credential detector would refuse.
- **FR-015**: Sections that are inert when absent MUST NOT be active in the rendered
  example, so that copying it verbatim makes no outbound request that was not configured.
- **FR-016**: The rendered example MUST be byte-for-byte reproducible: two runs on
  different machines, at different times, MUST produce identical output.
- **FR-017**: The subcommand MUST write to standard output by default and MUST accept a
  destination path. When given a path that already exists, it MUST refuse and exit
  non-zero unless the author explicitly asks for the file to be replaced.
- **FR-018**: The rendered output MUST be committed to the repository at a documented
  path, and the guide's configuration page MUST present it.
- **FR-019**: The example, the generator, and the loader's own accepted-key definitions
  MUST have one source of truth for what keys exist. The example MUST NOT be a second
  hand-maintained list of key names.
- **FR-020**: Generating the example MUST NOT read the author's own configuration,
  environment, or state. It renders defaults and nothing personal.

#### Keeping it current

- **FR-021**: An agent guidance file MUST exist at the repository root, and MUST instruct
  that a change altering behaviour also updates the guide page for the pipeline stage it
  affects, and that a change altering configuration also regenerates the committed
  example.
- **FR-022**: That file MUST NOT contradict `.specify/memory/constitution.md`, and MUST
  state that the constitution governs where they differ.
- **FR-023**: The test suite MUST fail when the committed example differs from what the
  generator produces.
- **FR-024**: The test suite MUST fail when a key the loader accepts is absent from the
  generated example, naming the key.
- **FR-025**: The test suite MUST fail when a guide page links to a file that does not
  exist.
- **FR-026**: Generating the example MUST be recorded in the audit log if and only if it
  changes state outside the process — writing to a file does, writing to standard output
  does not. Any action deliberately left unrecorded MUST be named and justified in the
  plan.

### Key Entities

- **Guide page**: One Markdown file under `docs/guide/`, covering one pipeline stage,
  reachable from the guide index, linking only to files that exist.
- **Config key definition**: The single record of a configuration key — its section, its
  name, its default, and the one-line explanation that becomes its comment. Consumed both
  by the generator and by the coverage test.
- **Committed example**: The generator's output, stored in the repository, presented in
  the guide, and compared against a fresh render by the drift test.
- **Agent guidance file**: `CLAUDE.md` at the repository root, subordinate to the
  constitution, telling a future session what a change to behaviour or configuration also
  obliges it to update.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A working, complete `config.toml` is obtainable with one command and no
  reading — from an empty machine to a file the daemon accepts, in under a minute.
- **SC-002**: Every configuration key the software accepts appears in the example with an
  explanation; the count of undocumented keys is zero, and stays zero automatically.
- **SC-003**: No single documentation page exceeds roughly 350 lines, down from one file
  of 1,180.
- **SC-004**: Any subject the old README covered is reachable from the published site's
  landing page in at most two clicks.
- **SC-005**: A configuration change that omits the example, or a generator change that
  omits the committed copy, fails the test suite rather than shipping.
- **SC-006**: Publishing the documentation requires no workflow the author has to
  maintain: the count of hand-authored CI jobs that build or deploy the docs is zero.
- **SC-007**: The full test suite passes.

## Assumptions

- GitHub Pages is, or will be, configured for this repository to deploy from the default
  branch's `/docs` folder. That setting lives in repository settings, not in the
  repository, so this feature produces a tree that works when it is set rather than
  setting it. The plan records this as the one step the author performs by hand.
- The built-in branch deployment renders Markdown through GitHub's default static-site
  build. No third-party plugin, no local build step, and no dependency added to
  `pyproject.toml` for the documentation.
- The guide documents the software as it behaves today. This feature restructures and
  completes documentation; it does not change dispatch, session, or configuration
  behaviour, with the single exception of adding the example-config subcommand.
- `share/config.example.toml` is the committed example's home, since it already exists at
  that path. Its current contents are stale and are replaced wholesale by generated
  output.
- The audience is the single author. Where the old README addressed a reader in the second
  person about their own machine, the guide keeps that voice.
- Old links to README anchors break, and that is accepted. No redirect or anchor-alias
  mechanism is built for a single-user, unsupported project.
