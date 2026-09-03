# Implementation Plan: GitHub Project Board Ordering

**Branch**: `robot-army/issue-48-github-project-ordering` | **Date**: 2026-09-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260902-182104-github-project-ordering/spec.md`

## Summary

One GraphQL request per repository per poll, two columns on `work_items`, one new table, one
new hold reason, and a permutation inside `ordering.plan`.

The board is read at poll time and stored, because `ordering.plan` is pure and runs on every
web page render — a board read on that path would break the invariant that makes the queue and
the dispatcher agree by being the same function. What gets stored is deliberately shaped so
four states stay distinguishable: no board knowledge, on the board at rank *n*, on the board
parked elsewhere, and not on the board at all. Collapsing any two of those is the bug this
design is most careful about.

Ordering does not gain a new sort key. `plan` sorts exactly as it does today and then permutes
each governed repository's items **within the slots that sort already gave them**, which is
what makes FR-002 true by construction: `oldest-first` and `repo-priority` keep their meanings,
and only which of a repository's items sits at each of its positions changes.

The split rule the author chose is the shape of the gate: a card parked in another column is a
deliberate *not yet* and is held with a new `OFF_COLUMN` reason naming the column it is in; an
issue absent from the board is no signal at all and still dispatches, ordered after everything
the board ranked.

**Two findings from Phase 0 changed what gets built.** GitHub exposes exactly one manual
ordering per project — global across columns, with no per-view and no per-column order, and
`ProjectV2View` has no `items` connection at all — so "board order" is that one list filtered
to the column, and there is no view concept in the configuration. And a **fine-grained token
cannot read a user-owned project**, because GitHub has no account-level Projects permission;
a classic token with `read:project` is required, and `doctor` reports which kind the author is
holding rather than letting it fail obscurely at poll time.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: none added. `httpx` remains the sole runtime dependency; the GraphQL
call is a `POST` through the same client, the same `base_url`, and the same bounded-retry
`_request` the REST calls already use.

**Storage**: the existing SQLite database. **Migration 009** — two nullable columns on
`work_items` and a `repo_projects` table. No backfill.

**Testing**: `pytest`, `tests/unit/` and `tests/integration/`. `uv run pytest`,
`uv run ruff check`.

**Target Platform**: one Linux machine with a shell, talking to github.com. GitHub Enterprise
is out of scope and fails loudly rather than quietly (R8).

**Project Type**: single-process CLI daemon with a read-only web view.

**Performance Goals**: `ordering.plan` runs on every dispatch tick *and* every web page render
and must stay free of network I/O — the board adds one bounded scan per plan, not one query per
queued item. The board read itself is **1 rate-limit point per repository per poll**, verified,
against a 5,000/hour budget.

**Constraints**: `ordering.plan` is pure and stays pure. `boundaries/github.py` remains the
only module that knows the GitHub API exists. Reads are real at every effect level, so nothing
here is simulated.

**Scale/Scope**: a handful of repositories, boards of tens of items, one author.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 — see the re-check at the end.*

### I. Simplicity First (YAGNI & KISS)

**Pass.** No new dependency, no new process, no new module. The feature is four config keys,
two boundary methods, one migration, one enum member, and one permutation. Four abstractions
were available and all four were declined:

- **A view-faithful sort engine** — replicating `sortByFields` for every field type, multi-field
  sorts, and each view's `filter`. Rejected in R2: it is a sort engine with one caller, built to
  resolve a divergence that measurement showed does not currently exist, and it would have to be
  maintained against GitHub's view semantics forever. The condition under which it *would*
  matter is detected and reported instead.
- **A new `ProjectSource` boundary** — rejected in R12 against the rule `boundaries/__init__.py`
  already states. Same host, same token, same client, answering a question about the very issues
  `poll` already returns; two implementations never used polymorphically is the strategy
  interface with one caller Principle I forbids.
- **A `project_items` side table** — rejected in R9. `work_items` already holds exactly one row
  per issue under a unique index, and a side table would buy a join on the render path to carry
  two values.
- **A separate board poll interval** — rejected in R5. The Trello reader has one because its
  poll is expensive; this one costs a single point, and a knob whose only effect is to make
  SC-002 slower is a knob that should not exist.

The one thing that *is* new machinery — the `repo_projects` table — is justified in R9 against
both alternatives: `repos` is an approval record that deliberately never re-derives, and
`poll_state` has fixed columns with nowhere to put a resolution.

### II. Single-User, Local-First

**Pass.** No accounts, no service, no hosted state. The resolution and the board snapshot live
in the existing local SQLite database, and every surface reads them from disk — `status`,
`capacity` and `/queue` all answer correctly with the network unplugged, which is the concrete
form local-first takes here. The token is read through the existing `read_token()`, never
stored, and never logged.

### III. Total Accountability

**Pass, with one gap enumerated below.**

**What this logs**:

| Action | Record |
|---|---|
| A project resolved, or not | `github.project.discover` — candidates seen, project and column chosen, whether each was discovered or configured, or the reason none resolved |
| A board read | `github.project.read` — project, column, items ranked, items parked elsewhere, pages fetched, remaining rate limit |
| A GraphQL response carrying both data and errors | `github.project.partial`, then a raise — a partly-believed board is worse than no board |
| The per-repository board pass | `poll.board` — read, skipped for backoff, or failed with the reason |
| A snapshot going stale | `poll.board.fallback` — which repository, how old, and that its order remains in force |
| An item held off-column | through the existing hold recorder, which this feature does not modify |
| The order actually dispatched | already logged: `dispatch` records the item it selected, and the queue's order is reconstructable from the stored board facts at that time |

**The enumerated gap** (Principle III's documented-exception path): **the board's item sequence
is not written to the log on every read.** What is recorded is the read, its counts, and the
resulting stored state; the sequence itself is recoverable from `work_items.board_position` at
any instant, but a reader reconstructing history from the log alone cannot say what order the
board was in three days ago. The justification is proportionality: logging the full sequence
every 60 seconds per repository would write the same list 1,440 times a day and drown the
records that matter, while the question it answers — *what order did the board have?* — is
answerable now from the database and is not a question about an action the system took. The
actions themselves — every read, every failure, every fallback, every dispatch — are all
recorded. This is the same trade `capacity` already makes in not logging its snapshot on every
render. **This gap is not justified here alone**: it is written into `docs/logging.md`'s "What
is deliberately not logged" section as part of the work, because that is the list the author
consults, and a justification that lives only in a plan is not a documented exception.

**What happens if it is killed halfway through**: the read is a read, so a kill during it
changes nothing and the previous snapshot stands. The write is one transaction per repository
covering both the `work_items` updates and the `repo_projects` upsert, so a kill mid-write
rolls back to the previous snapshot whole — never half of one board and half of another. Killed
between read and write, the snapshot is lost and the next poll re-reads it for one point.
Migration 009 advances `user_version` last, so an interrupted upgrade re-runs whole.

### IV. Interruption Tolerance

**Pass.** The one new network call goes through `_request`, which already carries an explicit
timeout, bounded retries with jitter, and `Retry-After`/rate-limit-aware backoff. Per-repository
failures accumulate into `repo_projects.consecutive_failures` with the same
`min(2 ** failures, 900)` bound `poll_repo` already uses, so a broken board backs off rather
than hammering. Pagination is bounded at 20 pages and **raises rather than truncating**,
because a truncated board is a wrong order rather than a partial one. All writes are inside
`db.transaction`, which is `BEGIN IMMEDIATE` on a WAL database with `synchronous=FULL`.

### V. Public Code, Unsupported Project

**Pass.** No credentials, no personal data, no hostnames committed. Board titles and column
names are read at runtime and stored locally, not in the repository.

The documentation obligation this creates is real, is part of the work, and is listed as three
concrete files under Project Structure rather than left as an intention:

- **`README.md`** must say that a **classic** token with `read:project` is required and that a
  fine-grained token cannot read a user-owned board at all. Today the README shows
  `export ROBOT_ARMY_GITHUB_TOKEN=ghp_...` with no scope guidance whatsoever, so without this
  the author hits a wall with a confusing error and no pointer. It also documents the four new
  keys and how board ordering behaves.
- **`docs/state.md`** must gain the new table and columns. That file exists so the author can
  read the database at 2am without the source, and a schema it does not describe is a schema
  they will re-derive by hand.
- **`docs/logging.md`** must gain the new action names and, in its "What is deliberately not
  logged" section, the Principle III gap enumerated above. Constitution: *an undocumented gap
  in the record is a violation; a documented, justified one is not* — and the place the author
  actually looks for that list is `logging.md`, not this plan.

### Operating Constraints

**Pass.** Everything is reachable from the terminal: `status` reports which board governs each
repository and why, `capacity` reports the setting and its source, `doctor` checks the token,
the project, the column, the view sort, and freshness before the daemon needs any of them.
Every command still exits non-zero on failure. **Nothing here mutates anything outward** — the
feature only reads GitHub — so the rule for irreversible or outward-facing actions has nothing
to apply to. The behaviour that *is* new by default is dispatch order and the off-column hold,
which is why FR-020's per-repository off switch exists and why the first pass after upgrade
reports the new state rather than leaving it to be noticed.

### Development Workflow

**Pass.** Unit tests ship with every changed behaviour, and the failure paths are the ones that
matter most here: a GraphQL 200-with-errors must raise rather than yield an empty board; an
unread board must gate nothing; a stale snapshot must stay in force and be visibly stale; a
truncated page must raise. The constitution's requirement that parsing code and persistence
logic carry failure-path tests applies squarely to `_graphql` and to migration 009.

## Project Structure

### Documentation (this feature)

```text
specs/20260902-182104-github-project-ordering/
├── plan.md                      # This file
├── spec.md
├── research.md                  # Phase 0 — R1..R12
├── data-model.md                # Phase 1 — migration 009, entities, states
├── quickstart.md                # Phase 1 — validation scenarios
├── contracts/
│   ├── config.md                # the four keys, resolution, doctor checks
│   ├── project-source.md        # the two boundary methods, the document, _graphql
│   └── dispatch-policy.md       # the permutation, the hold, where the board is read
├── checklists/
└── tasks.md                     # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
src/robot_army/
├── migrations.py        # + SCHEMA_009_SQL, _migration_009
├── models.py            # + WorkItem.board_column/board_position, RepoProject, ROW_TYPES
├── db.py                # + get/save/list_repo_project(s), board-fact update
├── config.py            # + the four keys, effective_project_ordering,
│                        #   RECOGNISED_DISPATCH_COLUMNS, key tables
├── repos.py             # carry the three per-repo fields through resolve()
├── poll.py              # + the per-repo board pass, its backoff and its records
├── ordering.py          # + HoldReason.OFF_COLUMN, board_key, the permutation
├── operations.py        # status: the `projects` list; capacity: the setting;
│                        #   doctor: the project checks
├── web/pages.py         # queue: the new reason and the off-column count
└── boundaries/
    ├── __init__.py      # + BoardSnapshot, BoardEntry, ProjectResolution,
    │                    #   two IssueSourceReader methods, __all__
    └── github.py        # + _graphql, resolve_project, read_board

tests/unit/
├── test_migrations.py           # 009 applies, is idempotent, backfills nothing
├── test_config.py               # the four keys, defaults, resolution, unknown-key refusal
├── test_repos.py                # resolve carries the fields rather than dropping them
├── test_ordering.py             # the permutation preserves slots; board_key totality;
│                                #   OFF_COLUMN precedence; no gate without a read
├── test_github_project.py       # _graphql raises on 200-with-errors; filtering of drafts,
│                                #   PRs, REDACTED, foreign repos, null Status; pagination
├── test_poll_board.py           # the board pass: write, skip, backoff, stale retention
├── test_web_views.py            # the queue renders the reason and the off-column count
└── test_status_projects.py      # status/capacity report the resolution and its source

tests/integration/
├── test_dispatch_capacity.py    # board order end to end through a real dispatch pass
└── test_web_end_to_end.py       # /queue renders with a reader that raises on any call
```

### Documentation (repository root)

Not an afterthought and not optional — Principle V's requirement is that the docs are written
for the author's future self, and two of the three below are files this repository already
keeps current for exactly this class of change.

```text
README.md            # the token requirement (a CLASSIC PAT with read:project, and that a
                     #   fine-grained token cannot read a user-owned board at all), the four
                     #   config keys beside the existing [dispatch] block, and a section on
                     #   board ordering in the shape of "Working a repository serially" —
                     #   what governs the order, what a parked card does, how to turn it off
docs/state.md        # a `repo_projects` section and a `work_items` board-columns section, in
                     #   the shape the `cards`, cleanup-columns and spec-kit-columns sections
                     #   already use; plus the new rows for "Interrupted at X → result"
docs/logging.md      # a "## The issue #48 actions" section for the five new action names, and
                     #   the enumerated Principle III gap added to "What is deliberately not
                     #   logged" — that section is where such gaps are recorded, and a gap
                     #   justified only in a plan nobody re-reads is an undocumented gap
```

`docs/roadmap.md` gains nothing. It maps the original planning milestones onto the numbered
specs; issue-driven features (#33, #47, #79) are not in it and this one does not belong there
either.

**Structure Decision**: unchanged from milestone 004's split, and the new code lands on the
right side of it. `capacity.py` observes the machine and gains nothing — a board is not a fact
about the machine. `ordering.py` is where configuration meets observation and takes the
permutation and the hold. The board *read* belongs to `poll.py` and `boundaries/github.py`
because it is I/O, and putting any of it behind `ordering.plan` would break the purity that
makes the queue and the dispatcher the same function.

The one genuinely new decision is that board facts live on `work_items` rather than in a side
table (R9): the render path already loads those rows, and two nullable columns cost nothing
there while a join would cost something on every page.

## Complexity Tracking

> No Constitution Check violations. This section is empty by design.

## Constitution Check — re-evaluated after Phase 1

Re-run against the design as it now stands in `research.md`, `data-model.md`, and
`contracts/`. **All five principles still pass.** Phase 0 changed the shape of the feature
twice, and both changes made it simpler.

1. **The view question dissolved into a check** (R2). The plan going in assumed board ordering
   might require replicating GitHub's view sorts. Measurement showed the author's own board
   sorts by a field with no values set, so manual position *is* what the screen shows. What
   would have been a sort engine became a `doctor` check that fires only when a sort field
   actually has values in the dispatch column — Principle I served, and Principle III's
   no-silent-wrongness requirement met more precisely than a general warning would have.

2. **One request replaced two** (R4). Discovery and the board read travel in one GraphQL
   document once a project id is known, so re-verifying the link costs nothing and the whole
   feature runs at one rate-limit point per repository per poll. That is what let R5 reject a
   separate, slower poll interval and keep SC-002's one-minute promise honest.

3. **No new boundary, no new seam, nothing simulated** (R12). Reads are real at every effect
   level, so the effect-level table, `Boundaries`, `describe()`, and
   `SIMULATED_CONSEQUENCES` are all untouched — and `test_effects.py`'s assertion that
   `github.py` holds exactly one `Simulated*` name stays true.

**The sharpest edge in this design, restated because it is the thing most likely to be got
wrong later**: a GraphQL failure is an **HTTP 200**. A missing scope, a forbidden project, and
a malformed field all arrive with a success status and an `errors` array, and `_request` — which
raises only on `>= 400` — cannot see them. Reading `payload["data"]` directly would turn every
one of those into an empty board, which is indistinguishable from a board with nothing in it:
a silent, plausible-looking wrong answer that would quietly stop ordering anything while
reporting success. `_graphql()` exists solely to make that impossible, it must not be bypassed,
and its 200-with-errors test is not optional.

No entry in Complexity Tracking. Nothing needed justifying.


## Post-implementation reconciliation (T045)

Re-read against the code as built. **The Constitution Check above still holds**, and the
enumerated Principle III gap is unchanged in substance and is now written into
`docs/logging.md` where the author will actually find it. Seven things differ from what this
document promised, and each is recorded rather than quietly absorbed.

**First, the validation that matters most.** The GraphQL documents were run against the live
API for `jantman/robot-army`, read-only, and every part of the design held:

```
project_access     ok, classic token, scopes include 'project'
resolve_project    #3 'robot-army', column 'Ready', discovered/discovered — no configuration
read_board         39 items; ranked [48, 1, 41, 20, 22, 21, 23, 30, 32, 44]
view_sort_conflicts ()
```

That ranked list is **exactly** the order the author reported seeing in the browser, which is
the end-to-end confirmation no mocked test can give: the documents are right, `POSITION`
really is what a drag writes to, and discovery resolves this board with an empty config
section. `view_sort_conflicts` returning empty is the check correctly staying quiet — view 1
does sort by `Priority`, and no card in `Ready` has one set.

### The deviations

1. **Two requests per repository per poll, not one.** R4 designed a single combined document
   carrying discovery and the board read together. The built code calls `resolve_project` then
   `read_board`, because combining them means the caller must already hold a project id, which
   turns two clean protocol methods into one method with a mode flag. The cost is real and
   small: ~120 rate-limit points per hour per repository against a 5,000 budget, measured at
   1 point per request. The benefit kept is that the *link* is re-verified on every pass, which
   is what FR-015 and FR-018 need in order to notice a project being unlinked or a second one
   being added. R4's arithmetic is therefore doubled and its conclusion unchanged.

2. **Four reader methods, not two.** `project_access` and `view_sort_conflicts` were added for
   `doctor` only. `project_access` cannot be derived from a failed `resolve_project`: the
   fine-grained-token diagnosis depends on the `x-oauth-scopes` header, which `_graphql` throws
   away, and that diagnosis is the single most useful line the check produces — no
   configuration fixes it, so an author who is not told will chase settings that cannot help.
   `view_sort_conflicts` is the R2 check, and it is what makes "manual position only" an
   honest choice rather than a silent one.

3. **`ProjectAccess.credential_kind`, not `token_kind`.** The lint rule that flags hardcoded
   passwords fires on any argument whose name contains `token`. Renaming the field was better
   than a `noqa`, which would have suppressed a real rule at the one boundary that handles
   credentials.

4. **`check_project` lives in `poll.py`**, mirroring `intake.check_board`'s placement rather
   than adding a module for one function. `doctor` wraps each repository's checks in a
   `try` — it exists to report problems, so one unreachable board must not stop it reporting
   on everything else.

5. **A resolution failure does not advance the backoff counter.** The plan did not distinguish
   the two, and the code does: a transport failure backs off, an ambiguous board does not.
   Backing off on ambiguity would delay recovery from a condition only the author can clear,
   and there is nothing to retry away.

6. **`resolve_project` was split into a recorder and a decision.** The decision has eight
   exits and every one of them must be logged; wrapping it was better than eight copies of the
   recording call, which is the shape a missed exit hides in.

7. **`status` prints only the board rows worth printing.** A repository with ordering enabled
   and no board is skipped, because most installations have none and a line on every one of
   them would bury the rows that matter. A repository the author switched **off** is shown —
   that is a choice they made and may have forgotten making.

### One test premise that was wrong, and what it taught

Two ordering tests failed on first run, and both were the test's fault rather than the code's.
The first asserted that a repository with no `[repos.*]` section and no recorded clone path
holds nothing; it holds `not_onboarded`, as every such repository in the suite already did.
The second set `clone_path = NULL` expecting `not_onboarded` and got `OFF_COLUMN`, because
`repos.resolve` falls back to the section's `path` — so nulling the record does not make a
repository unresolvable when a section still names it.

Worth recording because the second one is a real property of `resolve` that is easy to forget:
**the record wins `path` only when it has one**, and a configured section is a second source
for it. A test that wants an unresolvable repository has to use a key with neither.

### One thing not done

**T043's live walk-through is not complete.** The read-only half was run against the real API
and is quoted above. The half that needs a human — dragging a card and watching the order
follow, parking a card and watching it be held, and a live dispatch — has not been performed,
because it requires the author's running daemon and a browser. The quickstart is written to be
walked in that order and the failure scenarios in its steps 6 and 7 are covered by unit tests
in the meantime.

No entry in Complexity Tracking. Nothing built here needed justifying beyond the Constitution
Check above.
