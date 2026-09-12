# Tasks: Onboarding's Repository Lookup Leaves an Audit Record

**Input**: Design documents from `specs/20260912-175131-get-repo-audit-record/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/github-get-repo-record.md

**Tests**: required by the constitution (Development Workflow). Every changed unit ships with
unit tests, including both failure paths (an HTTP status, exhausted connection retries).

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

None. No dependency, module, table or config key is added.

## Phase 2: Foundational (blocks US2)

- [X] T001 Give `TransportError` in `src/robot_army/boundaries/__init__.py` an `__init__(self, message: str, *, status: int | None = None)` that stores `self.status`, with a docstring line saying why (research R4: a caller that records a failure needs the HTTP status without parsing the message; `None` means no response arrived).
- [X] T002 In `GitHubReader._request` in `src/robot_army/boundaries/github.py`, pass `status=response.status_code` to the `TransportError` raised for an HTTP failure (the `>= 400 and != 304` branch). Leave the exhausted-retries raise without it.

## Phase 3: User Story 1 — every onboarding lookup is in the log (P1) 🎯 MVP

**Goal**: a 200 or a 404 from `get_repo` writes one `github.get_repo` record with outcome `ok`, per contracts/github-get-repo-record.md.

**Independent Test**: onboard one unowned repository and one missing one through a real `GitHubReader`; `read_log`'s lines matching `github.*"/repos/` number 2.

- [X] T003 [US1] In `GitHubReader.get_repo` in `src/robot_army/boundaries/github.py`, build `path = f"/repos/{self._repo_path(repo_key)}"` once, send it, and after the response write `self._audit.record("github.get_repo", outcome="ok", entity_type="repo", entity_id=repo_key, detail={"method": "GET", "path": path, "status": response.status_code, "exists": response.status_code != 404})`. Extend the docstring: why the record exists (issue #64: SC-009 is verified from the log, and the poll exemption does not cover this read) and that it is path only.
- [X] T004 [P] [US1] Tests in `tests/unit/test_github_boundary.py`: 200 writes one `ok` record with method, path, status 200, `exists: true`, entity `repo:jantman/demo`; 404 writes one `ok` record with status 404 and `exists: false`; 503-then-200 writes one `github.get_repo` and one `github.retry`; no record contains the bearer token, a `?` or `https://`. Read the records back from `audit.path.parent` / the `layout.log_dir` via `robot_army.audit.read_records`.
- [X] T005 [P] [US1] Test in `tests/integration/test_onboard.py`: with `make_boundaries(audit, reader=GitHubReader(config, audit, client=<MockTransport client>, sleep=...))` answering 200 (owner `someoneelse`) and 404 (`jantman/typoed-nmae`), two refused `onboard` attempts, then `operations.read_log(ctx, since="10m")`: exactly 2 lines match `re.search(r'github.*"/repos/', line)`, each naming its own path, and each is followed by that attempt's `repo.onboard` record.

**Checkpoint**: the quickstart's check counts one per attempt that reached GitHub.

## Phase 4: User Story 2 — a lookup that fails is recorded as that lookup (P2)

**Goal**: a lookup that raises writes one `github.get_repo` record with outcome `error` and re-raises the same exception.

**Independent Test**: a 401 for the lookup gives one `error` record with status 401, and `onboard` still refuses with `source_unreachable`.

- [X] T006 [US2] In `get_repo` in `src/robot_army/boundaries/github.py`, wrap the `_request` call in `except TransportError as exc:`, record `github.get_repo` with `outcome="error"` and detail `method`, `path`, `status: exc.status`, `error_type: type(exc).__name__`, `error: str(exc)[:400]`, then bare `raise`. Comment on why it is recorded here, not only in `_request` (research R3: failures would otherwise be missing from the count, and `github.request` prints no path).
- [X] T007 [P] [US2] Tests in `tests/unit/test_github_boundary.py`: 401 → one `error` record with status 401, and the same `TransportError` (with `.status == 401`) propagates; connection error on every attempt (`httpx.ConnectError` from the handler) → one `error` record with `status: None` after `max_retries + 1` `github.retry` records; 503 on every attempt → one `error` record with status 503.
- [X] T008 [P] [US2] Test in `tests/integration/test_onboard.py`: the real reader answering 401 → `onboard` exits `EXIT_PRECONDITION` with `source_unreachable` on its `repo.onboard` record, and one `github.get_repo` record with outcome `error` and status 401 precedes it.

## Phase 5: Polish

- [X] T009 [P] Add "The issue #64 record" section to `docs/guide/audit-log.md` (the action, its fields, `ok` for 404, `error` on failure, path only, and the quickstart's check), and add a sentence to the "deliberately not logged" successful-GETs row saying the exemption covers reads inside a poll and that onboarding's lookup is recorded.
- [X] T010 Run `uv run pytest` (whole suite) and `uv run ruff check` if configured; fix anything red.

## Dependencies

- T001 → T002 (the attribute before its use)
- T003 → T004, T005 (US1)
- T002 + T003 → T006 → T007, T008 (US2 needs the status and the path variable)
- T009 once T006's shape is fixed; T010 last

## Parallel Opportunities

- T004 and T005 are different files; so are T007 and T008.
- T009 (docs) can run alongside the US2 tests.

## Implementation Strategy

MVP is US1: the success and 404 records make the quickstart's check work. US2 closes the count
for failed lookups. Both are small enough to land in one implementation commit, with the docs in
a second.
