# Implementation Plan: Docs overhaul and example config

**Branch**: `robot-army/issue-136-docs-overhaul-and-example-config` | **Date**: 2026-09-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260905-124257-docs-overhaul-example-config/spec.md`

## Summary

Three deliverables, one of which is code.

1. **The guide.** `README.md`'s 1,180 lines become eight pages under `docs/guide/`, ordered
   as the system touches an issue: setup → intake → selection → session → outcome, plus
   operating and configuration. `docs/index.md` is the landing page; `docs/_config.yml`
   configures GitHub Pages' built-in branch build and excludes the project history from the
   site without removing it from git. `README.md` shrinks to an overview and a link.
2. **The example config.** A new module renders a fully commented `config.toml` by
   iterating `config.py`'s existing `_KNOWN_KEYS` and `_REPO_KEYS` and demanding an
   annotation for every key it finds. A new `robot-army example-config` subcommand writes
   it to stdout or, with `--output`, atomically to a file. The rendered output is committed
   at `share/config.example.toml` and included in the configuration guide page.
3. **`CLAUDE.md`.** The agent guidance file the constitution refers to but which does not
   yet exist, carrying the rule that a behaviour change updates its guide page and a
   configuration change regenerates the example.

The mechanism that stops all three rotting is three tests: the committed example must equal
a fresh render, the rendered example must cover every key the loader accepts and must load
clean, and every internal documentation link must resolve.

## Technical Context

**Language/Version**: Python 3.11+ (`tomllib` is stdlib from 3.11, and `config.py` already
imports it).

**Primary Dependencies**: None added. The generator is `str` assembly over the standard
library; the site is GitHub's built-in Jekyll build, configured by a four-line YAML file
and requiring no local build, no plugin, and nothing in `pyproject.toml`.

**Storage**: Files. One generated TOML document at `share/config.example.toml`; Markdown
under `docs/`. No schema change, no database touched.

**Testing**: pytest, following `tests/unit/` conventions. Three new test modules.

**Target Platform**: Single Linux machine with a shell (Operating Constraints).

**Project Type**: Single project — CLI plus a small web surface. `src/robot_army/`,
`tests/unit/`, `tests/integration/`.

**Performance Goals**: Not applicable. Rendering is a few hundred lines of string
concatenation, run by hand.

**Constraints**: The rendered example must be byte-identical across machines (FR-016) and
must load through the existing loader unmodified (FR-013). Both are asserted by tests.

**Scale/Scope**: ~70 configuration keys across 14 sections plus the per-repository section;
22 README sections redistributed across 8 guide pages.

## Constitution Check

*GATE: passed before Phase 0. Re-checked after Phase 1 design — result at the end of this
section.*

### I. Simplicity First (YAGNI & KISS)

**Pass.**

- **No new dependency.** No templating engine, no TOML writer, no static-site generator, no
  link-checking library. The generator emits strings; the link check walks Markdown with a
  regex and asks the filesystem. Justified by the principle's own default: the standard
  library and what is already present.
- **No configuration knob for the generator.** It takes a destination and a force flag,
  because those are the two things the author actually does. No `--format`, no `--minimal`,
  no section filter — each would have exactly one caller and no second use in hand.
- **The annotation table is one table, not an abstraction.** No plugin registry of
  "renderers", no per-type dispatch hierarchy: a section is a name plus an ordered list of
  annotated keys, and rendering is a loop.
- **Two designs were available for FR-019** (research R1) and the one with fewer moving
  parts won: iterate the loader's existing tables rather than introduce a third
  registry or restructure `_KNOWN_KEYS` to carry prose.

### II. Single-User, Local-First

**Pass.** Nothing here is multi-user, hosted, or networked. The generated example contains
no credentials — it names environment variables and file paths, which is the principle's own
prescription, and the loader's `_TOKEN_PATTERNS` guard would refuse the file if it did.
Publishing the guide reads a repository GitHub already hosts; it adds no runtime service and
no always-on dependency. `robot-army example-config` works with the network down.

### III. Total Accountability

**Pass, with one documented exception.**

- **What this logs**: `example-config --output PATH` writes an audit record —
  `action: "example_config.write"`, `target` the resolved path, `detail: {"force": …}`,
  `outcome` success or the failure with its error text. The refusal to overwrite an
  existing file is recorded as a failure, deliberately: "I ran it and nothing changed" is
  precisely the question a log has to answer.
- **The documented exception**: rendering to standard output writes no record. Nothing
  outside the process changes — no file created, no request made, nothing mutated — so
  there is no action to reconstruct. Principle III's exception path requires this to be
  named and justified in the plan rather than left as a silent gap; it is named here.
- **A second, smaller exception**: the audit record for `--output` is written to the
  *default* layout's log directory, because this command runs before any config is loaded
  and therefore cannot honour a non-default `[paths] state_dir`. A failure to open that log
  does not fail the write. Stated rather than hidden.
- No silent failure: every refusal exits non-zero with a message on stderr, following the
  existing exit-code contract (0 ok, 1 failed, 2 usage, 3 precondition).

### IV. Interruption Tolerance

**Pass.**

- **What happens if it is killed halfway through**: the document is rendered completely in
  memory before any byte is written; the write is temp-file → `fsync` → `rename` within the
  destination directory. A kill leaves either the old file or the new one, never a
  truncated `config.toml` that the loader would read as broken TOML. This matters more than
  it looks: the usual destination is the file the daemon refuses to start without.
- Re-running is idempotent — the output is deterministic, so a second run reproduces the
  same bytes.
- No network call is made, so the timeout and bounded-retry rules have nothing to bind to.
- The documentation half is files in git; interruption leaves an unstaged working tree.

### V. Public Code, Unsupported Project

**Pass**, and this principle actively shapes the guide.

- The guide is "what it does, how to run it, where the logs are, and what they mean" — which
  is the pipeline breakdown almost exactly, with `operating.md` carrying the last two.
- **Out of scope, and enforced by review rather than assumed**: contribution guides, issue
  templates, support channels, end-user tutorials, a "getting help" page, badges, or a
  product voice. The existing README's second-person voice about the author's own machine is
  kept.
- No packaging or release pipeline is built. Publication is a repository setting plus a
  four-line YAML file, not a pipeline the author maintains.
- The example config contains no credentials, no private hostnames, and no personal paths:
  the one environment-derived default is rendered commented rather than resolved (research
  R2), which is a Principle V consequence as much as an FR-016 one.

### Development Workflow

**Pass.** Spec → plan → tasks → implement. Unit tests ship with the new behaviour: the
generator's completeness, its loadability, the committed copy's freshness, and the link
check. `config.py` parses external input and already carries failure-path tests; the new
tests exercise the generator's failure path too — an un-annotated key must raise, not be
skipped.

### Post-Phase-1 re-check

**Pass, unchanged.** Phase 1 added no dependency, no service, and no configuration knob. The
one design element that grew was the annotation table, which is data rather than structure.
The `contracts/` directory documents the CLI surface and the generated document's shape;
neither introduces an abstraction.

**Complexity Tracking is empty** — there is no violation to justify.

## Project Structure

### Documentation (this feature)

```text
specs/20260905-124257-docs-overhaul-example-config/
├── plan.md              # This file
├── spec.md              # Phase -1
├── research.md          # Phase 0 — nine decisions
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   ├── cli.md           # the example-config subcommand's surface
│   └── example-config.md # the generated document's shape and rules
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source code and content (repository root)

```text
src/robot_army/
├── exampleconfig.py     # NEW — annotations, rendering, atomic write
├── cli.py               # CHANGED — `example-config` parser + early route in main()
└── config.py            # UNCHANGED — remains the key-surface source of truth

tests/unit/
├── test_example_config.py       # NEW — coverage, loadability, determinism, failure path
├── test_example_config_drift.py # NEW — committed copy == fresh render
└── test_docs_links.py           # NEW — every internal doc link resolves

share/
└── config.example.toml  # REGENERATED — replaces the stale 231-line hand-written file

docs/
├── _config.yml          # NEW — Jekyll config; excludes project history from the site
├── index.md             # NEW — published landing page
├── guide/
│   ├── index.md         # NEW — overview + the pipeline at a glance
│   ├── 1-setup.md       # NEW
│   ├── 2-intake.md      # NEW
│   ├── 3-selection.md   # NEW
│   ├── 4-session.md     # NEW
│   ├── 5-outcome.md     # NEW
│   ├── operating.md     # NEW — absorbs logging.md and state.md
│   └── configuration.md # NEW — key reference + the example
├── logging.md           # REMOVED — content folded into guide/operating.md (FR-006)
├── state.md             # REMOVED — content folded into guide/operating.md (FR-006)
├── roadmap.md           # UNCHANGED — history, excluded from the site (FR-007)
├── incident-2026-08-31-desktop-session-killed.md   # UNCHANGED, excluded
├── verification-2026-09-01-cleanup-guards.md       # UNCHANGED, excluded
└── initial-planning/    # UNCHANGED, excluded

README.md                # REWRITTEN — overview + pointer to the guide (FR-005)
CLAUDE.md                # NEW — agent guidance (FR-021, FR-022)
```

**Structure Decision**: The existing single-project layout is kept exactly as it is. One new
module in `src/robot_army/`, three new test modules in `tests/unit/`, and content under
`docs/`. Nothing is reorganised: `docs/` is already the directory GitHub Pages serves from
in branch mode, which is why the guide goes beneath it rather than at the repository root.

## The README → guide mapping (FR-004)

Every top-level section of the current README, and where its content lands. This table is
the checkable form of "nothing is lost and nothing is duplicated" (research R9); the
implementation follows it, and review checks the tree against it.

| README section (line) | Destination | Note |
|---|---|---|
| `# robot-army` intro (1) | `README.md` + `docs/index.md` + `guide/index.md` | The one deliberate three-way reuse: an overview sentence is supposed to appear where a reader arrives. |
| `## What it does` (12) | `guide/index.md` | Becomes the overview, with the pipeline diagram added. |
| `## Running it` (30) | **splits**: install/token/first run → `guide/1-setup.md`; the `[repos.*]` exceptions discussion → `guide/configuration.md` | The section currently mixes first-run steps with config reference. Split named in advance. |
| `### The token has to be a classic one` (46) | `guide/1-setup.md` | |
| `### Adding a repository` (68) | `guide/1-setup.md` | Onboarding is setup, not intake: it is the trust step, done once. |
| `### `[repos.*]` is for exceptions` (104) | `guide/configuration.md` | |
| `## The web interface` (164) | `guide/operating.md` | Including `### Read this part` (192) and `### What it can do` (272). |
| `## The intake board` (304) | `guide/2-intake.md` | With `### Parking a card` (353), `### When a card doesn't say enough` (376), `### One card, one issue` (393). |
| `## What every session is told` (405) | `guide/4-session.md` | With `### Reading a prompt before it is sent` (465). |
| `## When a repository uses Spec Kit` (497) | `guide/4-session.md` | With `### Telling it how *I* run the lifecycle` (523), `### Seeing how far it has got` (575), `### What it deliberately does not do` (602). |
| `## How many sessions run at once` (617) | `guide/3-selection.md` | |
| `## Working a repository serially` (673) | `guide/3-selection.md` | With `### The clone fast-forward` (707). |
| `## Ordering work from a project board` (721) | `guide/3-selection.md` | |
| `## What it writes on the issue` (780) | `guide/5-outcome.md` | |
| `## Being told when something happens` (839) | `guide/5-outcome.md` | Notifications fire on outcomes; this is where a reader looks for them. |
| `## Pausing dispatch` (891) | `guide/3-selection.md` | Pausing decides what runs next — the selection stage. |
| `## Holding specific work` (907) | `guide/3-selection.md` | |
| `## Trying it without consequences` (951) | `guide/1-setup.md` | Effect levels are how a first run is made safe. |
| `## Where things live` (982) | **splits**: state paths → `guide/operating.md`; `[paths]` keys → `guide/configuration.md` | Second deliberate split; the paths *reference* belongs with the other keys. |
| `## Reading the logs` (999) | `guide/operating.md` | Merged with `docs/logging.md` (FR-006). |
| `## When something looks wrong` (1015) | `guide/operating.md` | |
| `## Recovering` (1043) | `guide/operating.md` | |
| `## Noticing it has died` (1082) | `guide/operating.md` | |
| `## Cleaning up` (1093) | `guide/5-outcome.md` | Cleanup is what happens after the issue closes — the end of the pipeline. |
| `## Design notes` (1161) | `guide/index.md` | |
| `## Licence` (1178) | `README.md` | Stays where a reader looks for it, next to `LICENSE`. |
| `docs/logging.md` (whole file) | `guide/operating.md` | FR-006. |
| `docs/state.md` (whole file) | `guide/operating.md` | FR-006. |

Two sections split and one is intentionally reused in three places. Every other section
lands on exactly one page, satisfying FR-004.

## Phase 1 design summary

Detail lives in the artefacts; this is the shape.

- **`data-model.md`** defines the three entities the generator works in: `SectionSpec`,
  `KeySpec` (name, rendered value, comment, active-or-commented), and the `RepoSectionSpec`
  that covers `_REPO_KEYS`. It also states the completeness invariant — the specified key
  set equals the loader's key set, in both directions — and what happens when it is
  violated.
- **`contracts/cli.md`** fixes the subcommand's surface: arguments, streams, exit codes, and
  the fact that it is routed before `load_config` because it must work with no config
  present.
- **`contracts/example-config.md`** fixes the generated document: section order, the
  active-versus-commented rules from research R3, the comment format, and the guarantees a
  consumer may rely on (byte-reproducible, loads clean, no credentials).
- **`quickstart.md`** is the end-to-end validation: generate, load, diff against the
  committed copy, run the three new test modules, and the single manual step of enabling
  GitHub Pages.

## Risks and how they are handled

| Risk | Handling |
|---|---|
| A 1,180-line file redistributed by hand loses content. | The mapping table above, written before anything moves. |
| The committed example silently goes stale — the failure this repository already has. | FR-023's drift test; it fails the suite rather than being noticed a year later. |
| A key is added to the loader and never documented. | The generator raises on an un-annotated key (research R1); the test asserts it. |
| Generated output differs per machine, so the drift test fails spuriously. | The one environment-derived default is rendered commented (research R2). |
| The example is copied verbatim and starts polling an unconfigured Trello board. | `[trello]` and `[pushover]` render fully commented, header included (research R3). |
| Guide pages accumulate broken links as they are edited. | FR-025's link test. |
| Old links to README anchors break. | Accepted in the spec's assumptions; the README's pointer is the mitigation. Single-user, unsupported project — no redirect machinery. |
| Pages is never actually enabled, so the guide is committed but unpublished. | Named as the one manual step, in `quickstart.md` and in the PR description. |

## Complexity Tracking

No Constitution Check violations. Table intentionally empty.
