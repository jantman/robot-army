<!--
SYNC IMPACT REPORT
Version change: 1.0.0 → 1.1.0
Rationale: Principle III's enforcement status was relaxed from non-negotiable to
exception-permitted-with-written-justification, and the Development Workflow testing
requirement was materially expanded to require unit tests. No principle was removed and no
previously compliant work is invalidated, so this is a MINOR bump.

Modified principles:
  III. Total Accountability (NON-NEGOTIABLE) → III. Total Accountability
    - NON-NEGOTIABLE designation removed
    - Documented-exception path added for actions that cannot practically be logged

Modified sections:
  Development Workflow — unit tests are now required for all new or changed behavior
  Governance — compliance review updated to match Principle III's exception path

Added sections: none

Removed sections: none

Deferred TODOs: none
-->

# Robot Army Constitution

## Core Principles

### I. Simplicity First (YAGNI & KISS)

Build only what a present, concrete need requires.

- Speculative generality MUST NOT be added: no plugin systems, abstraction layers, strategy
  interfaces, or configuration knobs that have exactly one caller and no second use in hand.
- New third-party dependencies MUST be justified in the feature plan by the work they remove.
  The standard library and already-present dependencies are the default.
- A single process, plain files, and obvious top-to-bottom control flow are the default shape.
  Services, brokers, queues, daemons, and concurrency MUST be justified against a demonstrated
  need, not an anticipated one.
- When two designs satisfy the requirement, the one with fewer moving parts wins.

Rationale: There is one maintainer and no team to absorb accidental complexity. Every
abstraction is a permanent tax paid by one person.

### II. Single-User, Local-First

The target is one user, on one Linux personal computer.

- Multi-tenancy, user accounts, authentication, authorization, and role systems MUST NOT be
  built. The operating-system user is the trust boundary.
- All persistent state MUST live on the local filesystem at a documented path. Core function
  MUST NOT require a hosted database, cloud service, or always-on network connection.
- Secrets MUST be read from environment variables or local, git-ignored configuration files.
  Secrets MUST NOT be committed to the repository or written to logs.
- Features MUST NOT assume a public IP, reverse proxy, container orchestrator, or any
  deployment infrastructure beyond a shell on this machine.

Rationale: Scope discipline. Every capability aimed at a hypothetical second user is
complexity with no beneficiary.

### III. Total Accountability

Every action taken MUST leave a record.

- Every action that changes state outside the running process — file writes, command
  execution, network requests, model/API invocations, notifications, scheduled triggers —
  MUST be written to a durable, append-only log at the time it occurs.
- Each record MUST contain: UTC timestamp, originating component, action, target, relevant
  parameters with secrets redacted, and outcome (success or failure with error detail).
- Logs MUST be structured and machine-parseable, one record per line, and MUST remain
  human-readable. Any rotation or retention policy MUST be documented and MUST NOT discard
  records silently.
- Silent failure is forbidden. Swallowed exceptions, bare catch-all handlers that continue,
  and unlogged retries or fallbacks are violations of this constitution.
- The standard of correctness is reconstruction: from the log alone, without re-running
  anything, it MUST be possible to answer what the system did, when, to what, and with what
  result.
- Exceptions MUST be explicit. Where logging an action is genuinely impractical, or where the
  cost of logging is disproportionate to the action's risk, the feature plan MUST name which
  actions go unlogged and why. An undocumented gap in the record is a violation; a
  documented, justified one is not.

Rationale: Software that acts autonomously on the user's behalf is trustworthy only to the
degree its actions are auditable after the fact. Logging everything remains the default and
the burden of argument sits with the omission — but a principle with no exception path
invites either dishonest compliance or paralysis, and neither serves accountability.

### IV. Interruption Tolerance

Assume the process is killed mid-operation, the network disappears mid-request, and the
machine loses power without warning.

- Writes to persistent state MUST be atomic: write to a temporary file, fsync, then rename;
  or use a transactional store such as SQLite. A partially written file MUST NOT be
  observable to a later run.
- Long-running work MUST be safely restartable. Record progress checkpoints, make repeated
  operations idempotent where the underlying action allows it, and detect and report
  incomplete work on startup rather than assuming a clean slate.
- Every network call MUST set an explicit timeout and MUST bound its retries with backoff.
  Unbounded retry loops and indefinite blocking are forbidden.
- Precautions MUST be reasonable, not extreme. Atomic renames, checkpoints, timeouts, and
  idempotency are the expected ceiling. Distributed consensus, replication, write-ahead
  journaling of our own design, and crash-recovery frameworks are out of scope.

Rationale: This runs on a home computer with consumer power and consumer internet.
Interruption is a normal operating condition, not an exceptional one — but recovering from
it must not cost more than the project is worth.

### V. Public Code, Unsupported Project

The repository is public and MIT-licensed; the project is not an open-source product.

- Committed content MUST be free of credentials, personal data, private hostnames, and
  internal network addresses, because the repository is world-readable.
- No support obligation exists. Stable public APIs, deprecation cycles, migration shims, and
  backward compatibility for outside consumers MUST NOT be maintained. Breaking changes are
  acceptable whenever they serve the single user.
- Documentation MUST be written for the author's future self: what it does, how to run it,
  where the logs are, and what they mean. Contribution guides, issue templates, support
  channels, and end-user tutorials are out of scope.
- Packaging, release pipelines, and published artifacts MUST NOT be built unless the author
  personally needs them.

Rationale: The repository is published so it can be read, not so it can be adopted. Treating
it as a supported product would import obligations the project never accepted.

## Operating Constraints

- Runtime target is a single Linux machine with a shell. Portability to other operating
  systems is not a requirement and MUST NOT constrain design.
- Every capability MUST be reachable and observable from the terminal. Commands MUST exit
  non-zero on failure. A graphical interface MUST NOT be a prerequisite for any function.
- Persistent data MUST use plain text, structured line formats, or SQLite. Human-inspectable
  formats are preferred over binary or opaque serializations.
- The log location and record format MUST be documented, and there MUST be a documented way
  to review recent activity.
- Irreversible or outward-facing actions — deleting or overwriting user data, sending
  external messages, spending money, mutating remote systems — MUST be logged before
  execution and MUST require explicit configuration or confirmation. They MUST NOT be
  reachable by default.

## Development Workflow

- Features follow the Spec Kit flow: specify, plan, tasks, implement. The plan MUST include a
  Constitution Check before implementation begins.
- Every feature plan MUST answer two questions explicitly: what does this log, and what
  happens if it is killed halfway through?
- Unit tests are required. Every new or changed unit of behavior MUST ship with unit tests,
  and the full suite MUST pass before a feature is considered complete.
- Persistence and recovery logic, state machines, and code parsing external input MUST
  additionally carry tests exercising their failure and interruption paths, not only their
  success paths.
- Coverage percentage targets MUST NOT be adopted and test-first development is not mandatory.
  The requirement is that the tests exist and are meaningful, not the order they were written
  in or the number they total.
- Commits MUST be atomic and their messages MUST explain why the change was made, not merely
  what changed.
- Review is self-review. The gate is this constitution, applied honestly.

## Governance

This constitution supersedes prior habits, conventions, and preferences wherever they
conflict. When a requested change conflicts with a principle, the conflict MUST be raised
before the work is done, not discovered afterward.

Amendment procedure: the author edits this file directly, updates the Sync Impact Report
comment at its top, bumps the version, and sets the Last Amended date. No external approval
applies. Dependent templates read this file at runtime and are not edited as part of an
amendment.

Versioning policy follows semantic versioning:

- MAJOR: a principle is removed or redefined in a way that invalidates prior compliant work.
- MINOR: a principle or section is added, or existing guidance is materially expanded.
- PATCH: clarification, wording, or typo fixes that do not change obligations.

Compliance review: every implementation plan MUST include a Constitution Check gate. Any
design that adds complexity in tension with Principle I or Principle II MUST be justified in
writing in the plan or removed. Any gap in the action record permitted under Principle III
MUST be enumerated and justified in the plan; an undocumented gap is a violation.
Implementation is not complete until the unit test suite passes. Runtime development guidance for coding
agents lives in the agent guidance file at the repository root when one is present; it
supplements this constitution and MUST NOT contradict it.

**Version**: 1.1.0 | **Ratified**: 2026-08-22 | **Last Amended**: 2026-08-22
