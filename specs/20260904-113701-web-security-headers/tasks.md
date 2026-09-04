---

description: "Task list for: security headers on every web response"
---

# Tasks: Refuse to be framed — security headers on every web response

**Input**: Design documents from `specs/20260904-113701-web-security-headers/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/http-headers.md](./contracts/http-headers.md), [quickstart.md](./quickstart.md)

**Tests**: Required. The constitution's Development Workflow section makes unit tests mandatory for
every new or changed unit of behaviour, and this change is code emitting output to an external
consumer, so its failure directions — a response path that misses the headers, an existing header
displaced — are tested, not only the happy path.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Exact file paths are given in every task

## Path Conventions

Single project: `src/robot_army/`, `tests/` at the repository root.

## A note on how these stories split

The whole source change is one constant and one `__post_init__` in
`src/robot_army/web/server.py`. The stories split by *what the constant says*, not by where the
code lives: Phase 2 builds the attachment mechanism with an empty constant, and each story then
adds its own entries to it. Stopping after any story leaves a working, tested increment — after
US1 the framing hole is closed, which is the finding.

The Phase 2 test is written to iterate `SECURITY_HEADERS` rather than name specific headers.
That is deliberate: it makes "every response path carries every security header" a property of
the suite, so each later story's headers are automatically checked on all eight response paths
without that story re-writing the coverage matrix.

---

## Phase 1: Setup

**Purpose**: Establish the baseline so that any later failure is attributable to this change.

- [X] T001 Run the existing web suite green before touching anything: `uv run pytest -q tests/unit/test_web_routing.py tests/unit/test_web_render.py tests/unit/test_web_actions.py tests/integration/test_web_end_to_end.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The single attachment point every response passes through (FR-005), and the tests
that hold it there.

**⚠️ CRITICAL**: No user story can be delivered until this phase is complete — each of them is
entries in the constant this phase creates.

- [X] T002 Add a module constant `SECURITY_HEADERS: dict[str, str] = {}` to `src/robot_army/web/server.py`, immediately above the `Response` dataclass, with a comment stating that it is attached once at `Response` construction so that a response path added later carries it without its author knowing this exists — and that this is why it is not folded into `NO_STORE`, which reaches neither the static assets nor the `413`
- [X] T003 Add `__post_init__` to the `Response` dataclass in `src/robot_army/web/server.py` (line 116) setting `self.headers = {**SECURITY_HEADERS, **self.headers}`, with a comment noting the merge order: the caller's explicit header wins a name collision, and a dict keyed by name makes a duplicated header unrepresentable
- [X] T004 Create `tests/unit/test_web_security_headers.py` with the coverage matrix: a helper listing every response path — HTML page, `.json` page, `303` redirect, `404`, `405`, `503` schema mismatch, `/static/app.css`, `/static/app.js` — and a test asserting that for each path, every name in `SECURITY_HEADERS` is present with exactly its constant value
- [X] T005 Add to `tests/unit/test_web_security_headers.py` the FR-005 pin: a bare `server.Response()` constructed with no arguments already carries every entry in `SECURITY_HEADERS`, and a `Response(headers={"X-Frame-Options": "SAMEORIGIN"})` keeps the caller's value — the merge-order rule from [data-model.md](./data-model.md)
- [X] T006 Add to `tests/unit/test_web_security_headers.py` the FR-006 regression: `Cache-Control` is still `no-store` on `/active` and `public, max-age=3600` on both static assets, `Location` is still present on the `303` from `/`, and `Allow` is still present on a `405` — each alongside the security headers rather than displaced by them

**Checkpoint**: the mechanism is in place and proven. `SECURITY_HEADERS` is still empty, so no
behaviour has changed yet and the suite is green.

---

## Phase 3: User Story 1 - A framed page cannot be used against the operator (Priority: P1) 🎯 MVP

**Goal**: No document may embed any response of the interface in a frame, so a baited click has
nothing to land on.

**Independent Test**: `curl -sSI http://127.0.0.1:8420/queue` shows both `X-Frame-Options: DENY`
and a CSP whose `frame-ancestors` is `'none'`; the `<iframe>` page in
[quickstart.md](./quickstart.md) §3 renders blank.

- [X] T007 [US1] Add `"X-Frame-Options": "DENY"` and `"Content-Security-Policy": "frame-ancestors 'none'"` to `SECURITY_HEADERS` in `src/robot_army/web/server.py`, with a comment saying the two are the same instruction for two generations of browser, and why the same-origin check cannot make this distinction itself
- [X] T008 [US1] Add to `tests/unit/test_web_security_headers.py`: the exact values of both headers, and that the confirm page reached by a plain `GET` (`/item/<id>/confirm/abandon`) and the database-less refusal pages carry them — the two paths a clickjack would target directly, per spec User Story 1 scenarios 2 and 3
- [X] T009 [US1] Extend `tests/integration/test_web_end_to_end.py` with a test that the framing headers survive the socket on a page, on a static asset, on the `303` returned after a successful POST, and on a `HEAD` (FR-008)
- [X] T010 [US1] Extend `tests/integration/test_web_end_to_end.py`'s existing oversized-body test so it also asserts the framing headers on the `413`, the one response written directly at the socket boundary and never seen by the page renderer

**Checkpoint**: RA-12 is closed. Everything after this is hardening.

---

## Phase 4: User Story 2 - The interface declares what it is allowed to load (Priority: P2)

**Goal**: The responses state that the interface loads only its own two same-origin subresources,
that its document base cannot be redefined, and that its forms submit only to itself.

**Independent Test**: the CSP value contains all four directives; the interface loads in a browser
with a clean console — stylesheet applied, script running, ten-second refresh working.

- [X] T011 [US2] Extend the `Content-Security-Policy` value in `SECURITY_HEADERS` in `src/robot_army/web/server.py` to `frame-ancestors 'none'; default-src 'self'; base-uri 'none'; form-action 'self'`, with a comment recording why the strict form is free here: `html.page` emits exactly two same-origin subresources, there is no inline script, style or `on*` attribute anywhere in `html.py`, and `app.js` fetches only `window.location.href`
- [X] T012 [US2] Add to `tests/unit/test_web_security_headers.py` a test that the CSP contains each of the four directives with its expected value, parsed by splitting on `;` rather than by matching the whole string, so a later reordering does not fail the test for the wrong reason
- [X] T013 [US2] Add to `tests/unit/test_web_security_headers.py` a test that the rendered page's only subresource URLs are same-origin — assert every `href`/`src` emitted by `html.page` is a root-relative or fragment URL — so that a future page adding a CDN font breaks here rather than silently in a browser under `default-src 'self'` (FR-007)
- [X] T014 [US2] Follow [quickstart.md](./quickstart.md) §3 in a real browser and confirm no CSP violation appears in the console: page styled, age counter ticking, content updating in place (SC-004)

---

## Phase 5: User Story 3 - Responses are not reinterpreted, and addresses are not leaked (Priority: P3)

**Goal**: A declared content type is final, and following a `github.com` link out of the interface
tells the destination nothing about where the interface lives.

**Independent Test**: `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin` appear
on an HTML page, a JSON response and a static asset alike.

- [X] T015 [US3] Add `"X-Content-Type-Options": "nosniff"` and `"Referrer-Policy": "same-origin"` to `SECURITY_HEADERS` in `src/robot_army/web/server.py`, with a comment noting that `nosniff` matters most on the `.json` responses and the two assets, and that the policy is `same-origin` rather than `no-referrer` because the audit and item views link out to `github.com` and `trello.com` while `_referring_view` still needs the header on our own forms
- [X] T016 [US3] Add to `tests/unit/test_web_security_headers.py` a test asserting both values on an HTML page, on a `.json` response and on both static assets — the three content types the browser could otherwise be tempted to re-guess

**Checkpoint**: all four headers from [contracts/http-headers.md](./contracts/http-headers.md) are
in place on all eight response paths.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T017 [P] Add a short paragraph to the web-interface section of `README.md` stating what every response sends and why framing is refused outright — that the same-origin check cannot tell a framed click from an honest one, so the frame is refused rather than the click
- [X] T018 Run `uv run ruff check src/ tests/` and fix anything it reports
- [X] T019 Run the full suite: `uv run pytest -q -rs`
- [X] T020 Walk [quickstart.md](./quickstart.md) §2 end to end against a running `robot-army serve` and confirm the observed headers match the contract exactly, including the headers each response keeps

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Phase 1 — **blocks every user story**, since each story is
  entries in the constant Phase 2 creates
- **US1 (Phase 3)**: depends on Phase 2
- **US2 (Phase 4)**: depends on Phase 2. T011 edits the same CSP value US1's T007 introduced, so it
  must follow T007 — the one genuine cross-story ordering constraint
- **US3 (Phase 5)**: depends on Phase 2 only. Its two headers are independent of both other
  stories and could be delivered before either
- **Polish (Phase 6)**: depends on all three stories

### Within Each Story

Constant entry first, then the tests that assert it. The tests are not written to fail first:
Phase 2's matrix test already passes vacuously and continues passing as entries are added, which
is the property being maintained rather than a red-to-green step.

### Parallel Opportunities

Small feature, one source file, so parallelism is limited and mostly not worth taking:

- T005 and T006 touch the same new test file as T004 and must follow it, but are independent of
  each other in content
- T016 (US3's tests) is independent of everything in US1 and US2 and could run alongside them
- T017 (README) touches no file any other task touches and is marked `[P]`

Tasks T002, T003, T007, T011 and T015 all edit `src/robot_army/web/server.py` and must be
sequential.

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 → Phase 2 → Phase 3.
2. **Stop and validate**: the `<iframe>` page from [quickstart.md](./quickstart.md) §3 renders
   blank where it previously rendered a live, clickable queue.
3. That alone closes RA-12. US2 and US3 are hardening on top of a closed finding.

### Incremental Delivery

Phase 2 changes no behaviour (empty constant, green suite), so it can land on its own. Each story
after it is a self-contained addition to one dict literal plus its tests, and none of them can
regress a previous story — the Phase 2 matrix test re-checks every header on every path each time
the constant grows.

---

## Notes

- The audit log is untouched: this feature takes no action outside the process, so there is
  nothing for it to record (constitution Principle III; [research.md](./research.md) Decision 4).
  No task adds a log line, and none should.
- `docs/security-analysis.md` is not edited. It is the record of the analysis as written, and the
  preceding RA-* fixes did not amend it either.
- No configuration entry is added. The headers are constant, and a knob here would have exactly
  one caller (constitution Principle I; [research.md](./research.md) Decision 3).
