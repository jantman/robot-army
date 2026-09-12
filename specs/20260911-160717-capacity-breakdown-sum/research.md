# Research: Capacity breakdown that sums to its total

## R1 — Where the missing sessions come from

**Decision**: The missing term is `unmatched` in `capacity.snapshot`: open (`starting`,
`running`) session rows, simulated included, whose `session_id` is not in the registry scan.

**Rationale**: `total = len(scan.entries) + len(unmatched)`; `ours` and `others` partition
`scan.entries` only (`len(ours) <= len(entries)` always, so `others`' `max(..., 0)` never
clamps). Therefore `total - len(ours) - others == len(unmatched)` exactly, and reporting a
partition of `unmatched` makes the breakdown sum by construction.

## R2 — Split `unmatched` in two, not one

**Decision**: `simulated` = rows with `Session.hosted_by_simulation`; `in_flight` = the rest.

**Rationale**: The issue's point is that the two causes have opposite expectations — a real
launch resolves within seconds, a simulated row persists until purged (issue #28). One figure
would fix the arithmetic and keep hiding that. `hosted_by_simulation` is the existing, tested
signature (`dry_run`, `pid == 0`, no `proc_start`) that `cancel` and `purge-simulated` already
rely on; `dry_run` alone would misfile `no-remote` rows, which have real workers.

**Alternatives considered**: splitting by `dry_run` (wrong at `no-remote`, R2 of issue #59);
splitting by `state` (`starting` vs `running`) — a simulated row is `running`, and a real
`running` row can lack a registry entry after its worker exits, so state does not name the cause.

## R3 — What "in flight" honestly covers

**Decision**: Name it *in flight* and describe it as "dispatched, no registry entry" — launching,
or ended and not yet reconciled.

**Rationale**: Besides the launch window (R3 of milestone 004), a real row whose worker has
exited stays open until reconciliation closes it, and it too has no registry entry. A label
claiming "launching" would be wrong for that row. A simulated row not yet confirmed (no pid yet)
also lands here for the seconds before confirmation — accurate, since it is mid-launch.

## R4 — The degraded (`/proc`) path

**Decision**: No special case.

**Rationale**: On `/proc` the entries carry no session ids, so `known` is empty, `ours` is
empty, every `/proc` worker is in `others`, and every open row is unmatched. The four terms
still sum to the total; the existing "ceiling rather than a fact" warning already explains why
our own sessions are counted twice.

## R5 — Zero terms in one-line forms

**Decision**: The `capacity` block always prints all four lines; `describe()`, the web pill and
the global-cap hold detail print `simulated` and `in flight` only when non-zero.

**Rationale**: The invariant is that printed components sum to the printed total; a zero term
omitted does not break it. An always-present `0 simulated, 0 in flight` on every page's chrome
is noise in the ordinary case (a real, registered machine). `ours` and `other` stay always
present so existing readers see what they saw before. The block is the explanation surface, so
it is complete.

**Alternatives considered**: always print all four everywhere (noise); print only the sum as
one "unregistered" figure (R2).
