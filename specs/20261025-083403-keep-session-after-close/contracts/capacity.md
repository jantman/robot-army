# Contract: how capacity names kept sessions

A **kept session** is an open session row whose item is `done` and kept. It is already
counted in `total` and `ours`, and this contract changes only how it is *named*.

| Surface | Today | With one kept session |
|---|---|---|
| `breakdown` | `2 ours, 0 other` | `2 ours (1 kept), 0 other` |
| Global-cap hold detail (`ordering.py:383`) | `2 of 2 sessions running (2 ours, 0 other)` | `2 of 2 sessions running (2 ours (1 kept), 0 other)` |
| Repo-cap hold detail (`ordering.py:395`) | `repository o/n: 1 of 1 sessions (configured)` | `repository o/n: 1 of 1 sessions (configured), 1 kept` |
| `status` and the web pill | Both render `breakdown` | Inherit it |

Invariants:

- `components` is unchanged, and its counts still sum to `total`.
- With no kept sessions, every string is byte-identical to today's (SC-003).
- On the degraded `/proc` path, where `ours` is empty, the annotation is omitted rather than
  shown against a zero. Kept sessions are then still counted, in `others`, and the existing
  "ceiling rather than a fact" wording stands.
