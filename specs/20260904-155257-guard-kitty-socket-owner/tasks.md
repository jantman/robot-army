---

description: "Task list for refusing a terminal control socket the maintainer does not own"
---

# Tasks: Only the maintainer's own terminal socket may receive a dispatch

**Input**: Design documents from `/specs/20260904-155257-guard-kitty-socket-owner/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/discovery.md](contracts/discovery.md),
[contracts/config.md](contracts/config.md)

**Tests**: Required, not optional. The constitution's Development Workflow says "Every new or
changed unit of behavior MUST ship with unit tests", and adds that code parsing external input
must additionally carry tests exercising its failure paths. The candidate paths *are* external
input — filenames another local user can create — so every refusal branch is tested, not only
the accepting one. Tests come before the change they cover in each phase, so each is seen to
fail first.

**Organization**: one phase per user story, in the spec's priority order. US1 and US2 are both
P1 and are two clauses of the same rule; each is independently testable against the seam the
foundational phase creates. US3, US4 and US5 stand alone and can be delivered in any order
after it.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: the user story the task serves
- Every task names the exact file it touches

## Path Conventions

Single project. Source at `src/robot_army/`, tests at `tests/unit/`.

---

## Phase 1: Setup

**Purpose**: nothing to install or scaffold. The one task creates the file three later phases add
cases to, so it is not created three times.

- [X] T001 Create `tests/unit/test_kitty_socket_trust.py` with a module docstring naming its subject — which candidate paths `KittyDisplay.probe` may speak to, and which it must refuse without speaking to them — plus a `socket_at(path)` helper that binds and returns a real `AF_UNIX` socket (kept open for the test's duration so the inode stays a socket), and a `display(config, audit)` helper building a `KittyDisplay` against a `tmp_path` pattern. No test cases yet.

**Checkpoint**: `uv run pytest tests/unit/test_kitty_socket_trust.py` collects zero tests and passes.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the seam both P1 stories hang on — a per-candidate verdict, a refusal record, and a
`probe()` that consults them. Split out because US1 and US2 are two clauses of one rule and would
otherwise both rewrite the same loop.

- [X] T002 In `src/robot_army/boundaries/kitty.py`, add a module-level `_refuse(path: str) -> str | None` returning `None` when a candidate is acceptable and a reason string when it is not, with a docstring stating the rule from `contracts/discovery.md` and *why* it exists (a name in a shared directory is not evidence of who owns the listener). Body for now: strip any `unix:` prefix and return `None` — the clauses arrive in US1 and US2.
- [X] T003 In `src/robot_army/boundaries/kitty.py`, rewrite the `probe` loop to call `_refuse` before running anything against a candidate: on a refusal, append `{"socket": target, "reason": reason}` to a `refused` list and continue to the next candidate without invoking `run`; on acceptance, probe exactly as today. Store the list on `self._refusals` alongside the cached socket, and expose it as a read-only `refusals` property returning a tuple.
- [X] T004 In `src/robot_army/boundaries/kitty.py`, carry `refused` into the existing aggregate `kitty.probe` audit record on both the success and the failure path, beside `tried`, so one record still answers what was found, what was refused and why, and what was selected (Principle III).
- [X] T005 In `tests/unit/test_kitty_socket_trust.py`, add cases proving the seam independently of any clause: a candidate that `_refuse` rejects is never passed to `run` (assert on a `run` that fails the test if called with that target), discovery continues past a refusal to a later acceptable candidate, and the `kitty.probe` audit record carries both `tried` and `refused`.

**Checkpoint**: behaviour is unchanged for every existing test, because `_refuse` still accepts
everything. The new cases *fail* at this checkpoint rather than passing, and that was wrong in
the plan: a test that a refusal is never spoken to cannot pass while nothing is ever refused.
They are the right tests in the right file; they go green with T007, one phase later.

---

## Phase 3: User Story 1 — A socket the maintainer does not own never receives a dispatch (Priority: P1)

**Goal**: an unowned candidate, a plain file, a directory, a symbolic link, and a path that cannot
be inspected are each refused before any command runs against them.

**Independent test**: point the pattern at a directory holding a real socket plus one of each
refusable shape, and confirm only the socket is spoken to; then make every candidate unowned and
confirm the failure is the same clear one as when nothing matched.

- [X] T006 [US1] In `tests/unit/test_kitty_socket_trust.py`, add the failing cases for this story: a real socket is accepted; a plain file is refused `not a socket`; a directory is refused `not a socket`; a symbolic link pointing at the accepted socket is refused (proving `lstat`, not `stat` — this is the case that fails if the implementation follows links); a path deleted between globbing and inspection is refused `cannot be inspected`; and with `os.getuid` monkeypatched to a different id, every candidate is refused with a reason naming the owning uid.
- [X] T007 [US1] In `src/robot_army/boundaries/kitty.py`, implement the first three clauses of `_refuse`: `os.lstat` (returning `cannot be inspected: <strerror>` on `OSError`), `stat.S_ISSOCK`, and `st_uid == os.getuid()`, with a comment on the `lstat` choice naming the symlink case it defeats.
- [X] T008 [US1] In `tests/unit/test_kitty_socket_trust.py`, add the ordering case that is the finding itself: a refusable candidate whose name sorts *ahead* of the genuine socket under the existing reverse sort, asserted to receive nothing while the genuine socket is the one cached.

**Checkpoint**: the impostor is refused; `uv run pytest tests/unit/test_kitty_socket_trust.py`
passes and the full suite still passes.

---

## Phase 4: User Story 2 — A candidate in a directory another user can rearrange is refused (Priority: P1)

**Goal**: close the window between inspecting a candidate and speaking to it, by refusing any
candidate whose path passes through a directory a stranger can rearrange.

**Independent test**: the same owned socket, accepted in a private directory and in a
world-writable *sticky* one, refused in a world-writable non-sticky one, with the reason naming
the directory rather than the socket.

- [X] T009 [US2] In `tests/unit/test_kitty_socket_trust.py`, add the failing cases: an owned socket in a `0700` directory is accepted; the same socket in a `0777` directory is refused with a reason naming the directory; the same socket in a `1777` directory is accepted (the `/tmp` shape — this is the case that fails if the implementation refuses world-writable directories outright and breaks the maintainer's running setup); and a directory owned by another uid is refused, proved by monkeypatching `os.getuid`.
- [X] T010 [US2] In `src/robot_army/boundaries/kitty.py`, add the fourth clause of `_refuse`: walk the candidate's parent and every directory above it to the filesystem root, refusing unless each is owned by `os.getuid()` or uid 0 **and** is either not group/other-writable or carries the sticky bit; any `OSError` during the walk is a refusal. Comment why the sticky bit is the exemption and not an oversight.
- [X] T011 [US2] In `tests/unit/test_kitty_socket_trust.py`, add the regression case that the real default location passes the rule: a socket under a `0700` directory nested several levels deep is accepted, so the walk terminating at the root does not itself refuse everything.

**Checkpoint**: both P1 stories are complete; the rule is whole. Full suite passes.

---

## Phase 5: User Story 3 — The shipped setup no longer puts the socket where anyone can crowd it (Priority: P2)

**Goal**: the built-in default, the example configuration, and the README all name the per-user
runtime directory; an existing `/tmp` configuration still loads, with one warning.

**Independent test**: load a configuration setting no pattern and check the default; load one
rooted in a world-writable non-sticky directory and check the warning and that loading succeeds.

- [ ] T012 [P] [US3] In `tests/unit/test_config.py`, add the failing cases: with `XDG_RUNTIME_DIR` set, the default `socket_glob` is `<runtime dir>/mykitty-*`; with it unset, the default is under the state directory and is not under `/tmp`; a configuration whose pattern is rooted in a `0777` non-sticky directory loads successfully and produces a warning naming the recommended location; `/tmp/mykitty-*` produces no new warning, because `/tmp` is sticky; and the existing no-wildcard warning is unchanged.
- [ ] T013 [US3] In `src/robot_army/config.py`, make `TerminalConfig.socket_glob` a `field(default_factory=...)` computing `f"{runtime_dir()}/mykitty-*"`, import `runtime_dir` from `robot_army.paths`, use the same call as the loader's `_str` default, and delete the two `# noqa: S108` comments that existed only because the default was a `/tmp` literal.
- [ ] T014 [US3] In `src/robot_army/config.py`, add the load-time warning: take the pattern's longest wildcard-free leading directory, and if it exists and fails the directory half of the acceptance rule, warn that another local user could place a socket there, name `$XDG_RUNTIME_DIR/mykitty-*`, and state that the daemon refuses any candidate it does not own. A directory that does not exist is not warned about.
- [ ] T015 [P] [US3] In `share/config.example.toml`, change the `socket_glob` line to the runtime-directory form with a comment saying why it is not `/tmp`, keeping the existing note that kitty appends its PID.
- [ ] T016 [P] [US3] In `README.md`, change the documented `listen_on` line to `unix:${XDG_RUNTIME_DIR}/mykitty` (kitty expands environment variables and appends `-<pid>`, verified against kitty 0.48.2), say in one sentence why not `/tmp`, and warn against the abstract form `unix:@mykitty`, which carries no filesystem permissions at all.

**Checkpoint**: a maintainer following the README builds the safe setup; the maintainer who
already has `/tmp/mykitty-*` starts unchanged and is told once why the other location is better.

---

## Phase 6: User Story 4 — The diagnostic tells the truth about a refused socket (Priority: P2)

**Goal**: "nothing running", "something is impersonating kitty", and "the location is unsafe" are
three distinguishable answers on every surface that reports a missing socket.

**Independent test**: run the diagnostic against a pattern matching only refused candidates and
read the detail; run it against nothing at all and confirm today's wording is untouched.

- [ ] T017 [US4] In `src/robot_army/boundaries/kitty.py`, extend the `BoundaryError` raised by `_require_socket` so that when refusals were recorded it names them and their reasons, and when there were none it keeps today's message word for word. This is the message `attach` and every launch failure already quote.
- [ ] T018 [P] [US4] In `src/robot_army/operations.py`, compose the `doctor` terminal-socket check's detail from the same refusals: the socket when one was found, today's `nothing answered '<pattern>'` when no candidate matched, and the refusals with their reasons when candidates matched and were refused.
- [ ] T019 [P] [US4] In `src/robot_army/daemon.py`, make the startup problem for a missing socket carry the same three-way distinction, keeping the existing "kitty must be running with `allow_remote_control yes` and `listen_on` set" guidance for the case where nothing matched.
- [ ] T020 [US4] In `tests/unit/test_kitty_socket_trust.py`, add cases asserting all three shapes of the `BoundaryError` message, and in `tests/unit/test_doctor_projects.py` add a case that the diagnostic's terminal-socket detail names a refusal and its reason when every candidate is refused, and is unchanged when none matched.

**Checkpoint**: the maintainer can tell the three failures apart without reading the audit log.

---

## Phase 7: User Story 5 — The maintainer is told what a dispatch puts in plain view (Priority: P3)

**Goal**: the exposure this feature does *not* close is written down where a maintainer would
otherwise walk into it.

**Independent test**: read both documents and confirm each states that launch arguments are
visible to other local processes.

- [ ] T021 [P] [US5] In `share/config.example.toml`, add a comment to the `[repos.*] env` block stating that these values are passed as command arguments and are readable by any local process while a session launches — so a credential does not belong there — and reconsider the shipped `DATABASE_URL` example in that light.
- [ ] T022 [P] [US5] In `README.md`, state once, where dispatch is described, that the composed prompt and every `env` value reach the terminal as command arguments and are therefore visible in the process table for the life of the launch.

**Checkpoint**: the residual exposure is documented rather than implied.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T023 In `docs/security-analysis.md`, mark RA-15 resolved: what now refuses the impostor (the ownership and directory rule, before the probe), what changed by default (the socket location), what is deliberately only a warning (an existing shared-directory pattern), and what remains true (launch arguments in the process table, now documented).
- [ ] T024 Run the by-hand proof in [quickstart.md](quickstart.md) — most importantly the pair that plants a listener in a `0777` directory and then sets the sticky bit on the same directory — and confirm each expected outcome.
- [ ] T025 Run `uv run pytest` and `uv run ruff check src/ tests/` — the two gates CI runs; the whole suite must pass before the feature is complete (constitution, Development Workflow).

---

## Dependencies & Execution Order

- **Phase 1 (Setup)** → **Phase 2 (Foundational)**: T002–T005 create the seam every later phase writes against. Nothing else may start first.
- **Phase 3 (US1)** and **Phase 4 (US2)**: both edit `_refuse` in the same file, so they are sequential with respect to each other, US1 first as the higher-value clause. Either is independently testable and independently valuable.
- **Phase 5 (US3)**, **Phase 6 (US4)**, **Phase 7 (US5)**: independent of each other. US4 depends on Phase 2 for `refusals`; US3 and US5 depend on nothing but the repository.
- **Phase 8**: after everything else.

### Parallel opportunities

- T012 and T015 and T016 (US3: tests, example config, README) touch three different files.
- T018 and T019 (US4: `operations.py`, `daemon.py`) touch two different files.
- T021 and T022 (US5) touch two different files, and can run alongside any of the above.
- T007 and T010 cannot be parallel: same function.

## Implementation Strategy

**MVP is Phase 2 + Phase 3.** At that point the finding is closed for the configuration the
maintainer already has: a planted listener is refused before it is spoken to, and no dispatch
payload reaches it. Phase 4 removes the residual window, Phase 5 removes the exposure at its
source for a setup built from the README, Phase 6 makes the failure legible, and Phase 7 writes
down what remains. Each phase leaves the tree working and the suite green.
