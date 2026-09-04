# Feature Specification: Refuse to be framed — security headers on every web response

**Feature Branch**: `speckit/20260904-113701-web-security-headers`

**Created**: 2026-09-04

**Status**: Draft

**Input**: GitHub issue [jantman/robot-army#122](https://github.com/jantman/robot-army/issues/122) — "RA-12: no X-Frame-Options or CSP, so framing bypasses the CSRF defence". Severity Medium; RA-12 in `docs/security-analysis.md`.

## Context

The web interface's cross-site defence is otherwise sound. Every state-changing route is a
`POST`, every `POST` passes through the same-origin check, and every request — `GET` included —
passes through the host check that closes DNS rebinding. No `GET` route mutates state.

Framing walks around all of it. A page the operator visits in another tab embeds the interface
in an invisible `<iframe>` at the shipped default address and positions a bait button under a
real control. The click submits a form belonging to the framed document, so the browser reports
`Sec-Fetch-Site: same-origin` and an `Origin` that matches the `Host`. The same-origin check
passes, and it passes *honestly*: the request really did originate from a page this server
served. The check answers the question it was asked. The question was the wrong one.

What is missing is the instruction that stops a browser from putting the interface in a frame at
all. No response sets `X-Frame-Options` and no response sets a `Content-Security-Policy`; the
only headers the server emits are `Cache-Control`, `Location`, the asset cache directive and
`Allow`. The attacker cannot read what is inside the frame — the same-origin policy still holds
— but reading is not needed. The routes are enumerable from a public repository and the item
identifiers are small integers, so a blind click is enough to pause dispatch, hold an item,
acknowledge a real anomaly, or open a terminal window on the desktop.

This feature adds the missing instruction, and the small set of adjacent declarations that are
free to state here because the interface loads nothing from anywhere else by design: no web
font, no CDN, no icon set, no inline script or style. It does not change the same-origin check,
the host check, routing, or any handler.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A framed page cannot be used against the operator (Priority: P1)

The operator visits a hostile page while the interface is running on its default address. The
page embeds the interface in a frame and baits a click over a real control. The browser refuses
to display the interface in the frame at all, so there is nothing under the bait button to
click, and no action reaches the server.

**Why this priority**: This is the finding. Every other header in this feature is hardening
around it.

**Independent Test**: Request any page of the interface and confirm the response carries a
framing refusal that a browser will honour, both in its modern form and in the legacy form older
browsers understand.

**Acceptance Scenarios**:

1. **Given** any page response from the interface, **When** its headers are read, **Then** they
   forbid the page being embedded by any other document.
2. **Given** a confirmation page reached by a plain `GET` link — the page that a two-click
   clickjack would target directly — **When** its headers are read, **Then** it carries the same
   refusal as every other page.
3. **Given** a refusal page rendered without a database behind it — a 404, a 405, a schema
   mismatch, an unhandled failure — **When** its headers are read, **Then** it carries the same
   refusal. A page that only appears when something is already wrong is not a gap in the fence.
4. **Given** the redirect returned after a successful action, **When** its headers are read,
   **Then** it carries the same refusal alongside its `Location`.

---

### User Story 2 - The interface declares what it is allowed to load (Priority: P2)

The interface's pages are self-contained: one stylesheet and one script, both served by the
interface itself, no inline script or style, no external resource of any kind. The responses say
so, so that a browser will refuse to load anything else even if a page somehow asks it to.

**Why this priority**: It is a second line under the escaping in the HTML builder, which is
today the only thing preventing injected markup from executing during the ten-second refresh —
and it costs nothing, because the interface genuinely loads nothing external.

**Independent Test**: Read the policy from any page response and confirm it permits only
same-origin resources, forbids a document base being redefined, and confines form submission to
the interface itself; then load the interface in a browser and confirm the stylesheet, the
script and the auto-refresh all still work.

**Acceptance Scenarios**:

1. **Given** any page response, **When** its policy is read, **Then** resources are confined to
   the interface's own origin, a document base cannot be redefined, and forms may submit only to
   the interface's own origin.
2. **Given** the policy in force, **When** a page is loaded in a browser, **Then** the stylesheet
   applies, the script runs, and the ten-second refresh continues to work — the policy refuses
   nothing the interface actually does.

---

### User Story 3 - Responses are not reinterpreted, and addresses are not leaked (Priority: P3)

A response that says it is JSON is treated as JSON and never sniffed into something executable,
and following an external link out of the interface does not hand the destination the address
and path the operator was looking at.

**Why this priority**: Both are one-line declarations with no cost here, and both close small
gaps that only matter once something else has gone wrong.

**Independent Test**: Read the content-type and referrer declarations from a page response, a
JSON response and a static asset response.

**Acceptance Scenarios**:

1. **Given** any response — HTML, JSON, or a static asset — **When** its headers are read,
   **Then** they forbid the browser from guessing a content type other than the one declared.
2. **Given** a page containing a link to an external site, **When** the operator follows it,
   **Then** the destination is sent no referrer.

---

### Edge Cases

- **Static assets.** The stylesheet and the script are served from a separate branch of the
  request path that does not build a `Context` and sets a long cache directive rather than
  `no-store`. They must still carry the security headers; a fence with a hole in it is not a
  fence, and their cache directive must survive unchanged.
- **The oversized-body refusal.** The `413` for a too-large body is written directly at the
  socket boundary and never passes through the page renderer. It must carry the headers too.
- **`HEAD` requests.** A `HEAD` sends headers and no body. The headers it sends must be the same
  ones the equivalent `GET` would send.
- **Route-specific headers.** A `405` carries `Allow` and a redirect carries `Location`. Adding
  the security headers must not displace those, and those must not displace the security
  headers.
- **A future response path.** The headers must be attached at a single place that every response
  passes through, so that a response added later carries them without anyone remembering to add
  them. A per-call-site list is how the next gap gets introduced.
- **Duplicate headers.** No response may emit the same header name twice with different values;
  a browser's behaviour when it sees two conflicting policies is not something to rely on.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every HTTP response the web interface emits MUST carry a policy forbidding the
  response from being embedded in a frame by any document, in both its modern (content policy)
  and legacy (frame-options) forms.
- **FR-002**: Every HTTP response MUST carry a content policy that confines resource loading to
  the interface's own origin, forbids redefining the document base, and confines form submission
  to the interface's own origin.
- **FR-003**: Every HTTP response MUST declare that its content type is not to be sniffed.
- **FR-004**: Every HTTP response MUST declare that no referrer is to be sent when following a
  link out of the interface.
- **FR-005**: The headers in FR-001 through FR-004 MUST be attached at a single point that every
  response passes through, including responses that bypass the page renderer: static assets, the
  oversized-body refusal, and any response path added in future.
- **FR-006**: Attaching these headers MUST NOT remove, alter, or duplicate any header a response
  already sets — `Cache-Control` (both the `no-store` and the asset `max-age` forms), `Location`,
  `Allow`, `Connection`, `Content-Type` and `Content-Length`.
- **FR-007**: The content policy MUST permit everything the interface actually does — the
  same-origin stylesheet, the same-origin script, its `fetch` back to the interface, and the
  in-place refresh — so that no page function is broken by it.
- **FR-008**: A `HEAD` response MUST carry the same headers as the equivalent `GET`.
- **FR-009**: The same-origin check, the host check, routing, and every handler MUST be
  unchanged in behaviour by this feature.

### Key Entities

- **Security headers**: the fixed set of response header names and values from FR-001 to FR-004.
  They are constant — they do not vary by route, by request, by configuration, or by whether the
  request came from a browser or a script.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A page embedding the interface at its default address in a frame displays nothing;
  no control of the interface is reachable through it.
- **SC-002**: Every response the interface can produce — page, JSON, redirect, static asset,
  404, 405, 503, 500, 413, and `HEAD` — carries all four declarations. Measured as 100% of
  response paths, with no exceptions.
- **SC-003**: Every header a response set before this feature is still present, with the same
  value, after it.
- **SC-004**: Loading the interface in a browser produces no policy violation: the stylesheet
  applies, the script runs, the auto-refresh updates the page, and the browser console is clean.
- **SC-005**: A response path added later carries the headers without its author doing anything
  to make that happen — demonstrable by the headers being attached in exactly one place.

## Assumptions

- The four declarations are those named in the issue. Three carry the values it proposes; the
  referrer policy is `same-origin` rather than the `no-referrer` it suggested, because the
  interface reads the referrer of its own POSTs to decide where a refused control offers a way
  back to. The purpose the issue gave for that header — that following a link out does not hand
  the destination this interface's address — is met in full either way. They are otherwise the
  standard, conservative choice for a self-contained local interface, and the interface's own
  design — no external resource, no inline script or style — is what makes the strict form free.
- Nothing legitimately frames this interface. It is a single-user local tool with one operator
  at one machine; there is no dashboard, no embed, and no second surface that would need to
  include it. So the framing refusal is absolute rather than an allowlist, and needs no
  configuration knob (Principle I, Principle II).
- These headers are constant and require no configuration. Making them adjustable would be a
  knob with one caller and no second use in hand.
- The response record is unchanged: this feature performs no action, changes no state outside
  the process, and writes nothing, so it adds nothing to the audit log. `GET` requests remain
  the enumerated Principle III exception already documented as FR-041.
- `docs/security-analysis.md` is the record of the finding as analysed, not a live status board;
  as with the preceding security fixes it is not edited by the fix.
