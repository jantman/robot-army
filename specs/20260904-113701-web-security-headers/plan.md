# Implementation Plan: Refuse to be framed — security headers on every web response

**Branch**: `speckit/20260904-113701-web-security-headers` | **Date**: 2026-09-04 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260904-113701-web-security-headers/spec.md`

## Summary

Every response the web interface emits gains four constant headers — a content policy whose
first directive is `frame-ancestors 'none'`, the legacy `X-Frame-Options: DENY`, `nosniff`, and
`no-referrer`. They are attached in `Response.__post_init__`, which is the single point all five
existing response paths and every future one pass through, merged *under* any headers the caller
passed so no existing header is displaced. No handler, guard, route or check changes.

## Technical Context

**Language/Version**: Python 3.11+ (`requires-python = ">=3.11"`)

**Primary Dependencies**: standard library only for this change — `dataclasses`, `http.server`.
No new dependency.

**Storage**: N/A. This feature reads and writes nothing.

**Testing**: pytest. Unit tests against `handle()` via the `web` fixture in `tests/conftest.py`
(a pure `Request` → `Response` call, no socket); one integration test file
(`tests/integration/test_web_end_to_end.py`) that binds a real ephemeral port.

**Target Platform**: single Linux machine, loopback-bound HTTP server, one operator.

**Project Type**: single project — a CLI and daemon with a local web interface.

**Performance Goals**: unchanged. Four dictionary entries per response; nothing measurable.

**Constraints**: the policy must permit everything the interface actually does (FR-007) — the
same-origin stylesheet and script, `app.js`'s `fetch` back to the same URL, and the in-place
refresh. Verified against `html.py` in [research.md](./research.md), Decision 2.

**Scale/Scope**: one constant, one `__post_init__`, and tests. Five existing response paths, all
already covered by the change by construction.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design.*

| Principle | Assessment |
| --- | --- |
| **I. Simplicity First** | Passes. One module constant and one four-line `__post_init__`. No configuration knob (research Decision 3), no abstraction, no new dependency. Of the two shapes the issue proposed, the plan takes neither verbatim: adding to `NO_STORE` conflates two unrelated concerns, and a dict spread at three call sites is both more code and less complete. `__post_init__` is fewer moving parts than either and is the only one that satisfies FR-005. |
| **II. Single-User, Local-First** | Passes, and is the reason the policy is absolute. One operator on one machine means nothing legitimately frames this interface, so `frame-ancestors 'none'` needs no allowlist and no setting. No account, role, or authorization concept is introduced — the headers are the same for every request because there is only ever one user. |
| **III. Total Accountability** | Passes with **no new exception claimed**. This feature takes no action that changes state outside the process: no file write, no command, no network request, no database write. There is therefore nothing for it to log, and the existing record is unchanged. The one pre-existing enumerated exception — `GET` requests are not audited (FR-041) — is neither widened nor relied upon here; `POST` auditing in `_perform` is untouched. Silent failure is not possible: there is no branch, no fallback and no exception handler in the change. |
| **IV. Interruption Tolerance** | Passes. Nothing persists, so there is no halfway state. A process killed mid-request loses that connection exactly as it does today; the next request constructs a fresh `Response` carrying the headers. No timeout, retry or atomic-write concern arises. |
| **V. Public Code, Unsupported Project** | Passes. No credential, hostname or personal datum is added. No public API is stabilised: `SECURITY_HEADERS` is an internal module constant that may be changed freely. Documentation is the `README.md` line describing what the interface sends and why, written for the author's future self. |

**Development Workflow gates**: this is the Spec Kit flow, and the plan answers the two mandatory
questions explicitly (research Decision 4: it logs nothing because it acts on nothing; there is no
halfway because it persists nothing). Unit tests ship with the change and cover the failure
direction — every response path, including the ones that bypass the renderer — not only the happy
path, as required for code parsing or emitting external input.

**Re-check after Phase 1 design**: unchanged. The design added no entity, no state, no I/O and no
dependency; every row above still holds against the final artifacts.

## Project Structure

### Documentation (this feature)

```text
specs/20260904-113701-web-security-headers/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: where the headers attach, and their values
├── data-model.md        # Phase 1: SECURITY_HEADERS and the Response header map
├── quickstart.md        # Phase 1: how to verify, by test and by browser
├── contracts/
│   └── http-headers.md  # Phase 1: the response-header contract
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
src/robot_army/web/
├── server.py            # CHANGED: SECURITY_HEADERS constant + Response.__post_init__
├── html.py              # unchanged — verified as the reason the strict policy is free
└── pages.py             # unchanged

tests/
├── unit/
│   └── test_web_security_headers.py   # NEW: every response path carries all four
└── integration/
    └── test_web_end_to_end.py         # CHANGED: headers survive the wire, incl. HEAD and 413

README.md                # CHANGED: one paragraph on what the interface sends and why
```

**Structure Decision**: The existing single-project layout is used unchanged. The whole source
change is confined to `src/robot_army/web/server.py`: a `SECURITY_HEADERS` constant beside the
existing `NO_STORE`, and a `__post_init__` on the `Response` dataclass at line 116. `html.py` and
`pages.py` are read during this work — to confirm the policy refuses nothing the pages do — but
are not edited.

## Implementation Outline

1. **`SECURITY_HEADERS` constant** in `server.py`, next to `NO_STORE`, carrying the four
   name/value pairs from [contracts/http-headers.md](./contracts/http-headers.md), with a comment
   saying what each is for and — for the CSP — why the strict form is free here.
2. **`Response.__post_init__`** merges the constant under the caller's headers:
   `self.headers = {**SECURITY_HEADERS, **self.headers}`. One dict keyed by header name, so a
   duplicate header is not representable (spec edge case), and a caller's explicit header always
   wins (FR-006).
3. **Unit tests** (`tests/unit/test_web_security_headers.py`): all four headers present on a page,
   a JSON response, a redirect, a 404, a 405 (alongside `Allow`), a schema-mismatch 503, and both
   static assets; `Cache-Control` unchanged in both its forms; the CSP contains each of the four
   directives; a `Response()` constructed with no arguments already carries them, which is the
   test that pins FR-005.
4. **Integration tests** (`tests/integration/test_web_end_to_end.py`): the headers survive the
   socket on a page, on a static asset, on the `303` after a POST, on a `HEAD`, and on the `413` —
   the two paths that never reach the page renderer.
5. **README**: one short paragraph in the web-interface section stating what every response sends
   and that framing is refused outright, because the same-origin check cannot tell a framed click
   from an honest one.

## Complexity Tracking

No Constitution Check violations. Nothing to justify.
