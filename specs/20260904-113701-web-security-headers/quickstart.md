# Quickstart: verifying the security headers

**Feature**: `specs/20260904-113701-web-security-headers`

Three ways to check this, in increasing order of effort and decreasing order of how often you
should need them. The first is the one CI runs.

## Prerequisites

```bash
uv sync --locked
```

## 1. The test suite

```bash
uv run ruff check src/ tests/
uv run pytest -q -rs
```

The feature's own tests:

```bash
uv run pytest tests/unit/test_web_security_headers.py -q
uv run pytest tests/integration/test_web_end_to_end.py -q
```

**Expected**: green. The unit file asserts all four headers on a page, a JSON response, a
redirect, a 404, a 405 (alongside its `Allow`), a 503 schema mismatch and both static assets;
that `Cache-Control` still reads `no-store` on routed responses and `public, max-age=3600` on the
assets; and that a bare `Response()` already carries the four — which is the assertion that pins
FR-005, because it can only pass if the headers attach at construction rather than at a call site.
The integration file re-checks them across a real socket, including on a `HEAD` and on the `413`,
the two paths that never reach the page renderer.

## 2. By hand, against a running interface

```bash
uv run robot-army serve --port 8420 &
curl -sSI http://127.0.0.1:8420/active
curl -sSI http://127.0.0.1:8420/static/app.css
curl -sS -o /dev/null -D - -X POST -H 'Origin: http://127.0.0.1:8420' \
     -H 'Sec-Fetch-Site: same-origin' http://127.0.0.1:8420/poll
```

**Expected**: each response carries

```text
Content-Security-Policy: frame-ancestors 'none'; default-src 'self'; base-uri 'none'; form-action 'self'
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
```

and keeps what it carried before — `Cache-Control: no-store` on `/active`, `Cache-Control:
public, max-age=3600` on the stylesheet, `Location:` on the `303` from `POST /poll`. See
[contracts/http-headers.md](./contracts/http-headers.md) for the full list of what each response
keeps.

## 3. In a browser — the part the tests cannot prove

Two things need a real browser, because they are about what a browser *does* with the headers
rather than what the server sends.

**The policy refuses nothing the interface does (FR-007, SC-004).** Open
`http://127.0.0.1:8420/active`, open the developer console, and leave it for a minute.

**Expected**: the page is styled, the footer's "just now" counter ticks, the content updates in
place every ten seconds, and the console is empty — no `Refused to load`, no `Refused to
connect`, no CSP violation of any kind. A styled page with a silent console is the whole check:
if `default-src 'self'` were wrong, the stylesheet or the refresh would visibly fail.

**Framing is refused (SC-001).** Save this beside nothing in particular and open it as a
`file://` URL:

```html
<!doctype html>
<p>below is a frame of the interface:</p>
<iframe src="http://127.0.0.1:8420/queue" width="600" height="400"></iframe>
```

**Expected**: the frame is blank, and the console says the page refused to display in a frame
because it sets `X-Frame-Options` to `deny` — or names the `frame-ancestors` directive, depending
on the browser. Nothing of the interface is visible or clickable inside it.

Before this feature, the same file rendered the queue inside the frame, with every control live.

## Cleanup

```bash
kill %1
```
