# Contract: HTTP response headers

**Feature**: `specs/20260904-113701-web-security-headers`

The web interface's contract with a browser. This document states what every response carries,
what each header means for the interface specifically, and what an existing header keeps.

## The four security headers

Present on **every** response, whatever its status, method, content type or route.

| Header | Value |
| --- | --- |
| `Content-Security-Policy` | `frame-ancestors 'none'; default-src 'self'; base-uri 'none'; form-action 'self'` |
| `X-Frame-Options` | `DENY` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `no-referrer` |

The values are constant. They do not vary by route, by request, by configuration, or by whether
the caller asked for HTML or JSON.

### What each directive means here

- **`frame-ancestors 'none'`** — no document may embed this response in a frame. This is the
  finding: the same-origin check cannot distinguish a click on a real form inside a hostile
  frame from a click on the same form in an honest tab, because in both cases the browser
  honestly reports `Sec-Fetch-Site: same-origin`. Refusing the frame is the only place the
  distinction can be made.
- **`X-Frame-Options: DENY`** — the same instruction for browsers that predate
  `frame-ancestors`. Where both are understood, `frame-ancestors` wins; they agree, so it does
  not matter which.
- **`default-src 'self'`** — the fallback for `script-src`, `style-src`, `img-src`, `font-src`
  and `connect-src`. The interface emits exactly two subresources, `/static/app.css` and
  `/static/app.js`, both from this server; `app.js` fetches only `window.location.href`. No
  `'unsafe-inline'` is needed because there is no inline script, no inline style, and no `on*`
  attribute anywhere in the generated HTML. A second line under the escaping in `html.py`: an
  injected `<img onerror>` reaching the refresh loop's `innerHTML` swap is refused by the policy
  even if the escaping ever fails.
- **`base-uri 'none'`** — nothing emits `<base>`, so nothing may re-point the page's relative
  URLs.
- **`form-action 'self'`** — every form on every page submits to this server.
- **`nosniff`** — a `.json` response is JSON and a stylesheet is a stylesheet; the browser may
  not guess otherwise.
- **`no-referrer`** — the audit and item views link out to `github.com`. Following one must not
  tell the destination the interface's address, port, or the path being viewed.

## Headers a response keeps

Attaching the four above removes, alters and duplicates nothing. Each of these still appears,
with the value it had before, on the responses that set it:

| Header | Set by | Value |
| --- | --- | --- |
| `Cache-Control` | every routed response | `no-store` |
| `Cache-Control` | `/static/app.css`, `/static/app.js` | `public, max-age=3600` |
| `Location` | the `303` after a successful action, and `/` | the target path |
| `Allow` | a `405` | the methods the path accepts |
| `Connection` | the `413` | `close` |
| `Content-Type` | every response | as declared by the response |
| `Content-Length` | every response | the body length |

A header name appears at most once in a response. Where a caller sets a name that the security
set also uses, the caller's value wins — none do today.

## Coverage

Every response path carries the four headers:

| Path | Where it is built |
| --- | --- |
| HTML page (200, and every view) | `_render` |
| JSON page (`.json`, or `Accept: application/json`) | `_render` |
| `303` redirect after an action | `_render_redirect` |
| `404`, `405`, `503` schema mismatch, `500` | `_bare` → `_render` |
| Refusal pages (`Refusal` raised in a handler, incl. the host check) | `_render` / `_bare` |
| `/static/app.css`, `/static/app.js` | the static branch of `handle` |
| `413` request body too large | `Handler._dispatch`, at the socket |
| A response path added in future | wherever it is built |

The last row is the contract that matters. The headers attach in `Response.__post_init__`, so
carrying them is a property of being a response rather than something each site remembers.

## Method behaviour

A `HEAD` response carries the same headers as the equivalent `GET`; only the body is withheld.
This falls out of `Handler._respond`, which writes the headers before deciding whether to write
the body.
