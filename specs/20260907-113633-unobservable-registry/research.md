# Phase 0 Research: An Observation That Saw Nothing Is Not Evidence That Nothing Is Alive

**Feature**: `specs/20260907-113633-unobservable-registry/`
**Tree measured**: `main` at 60f2914 (post-#33), plus the spec commit on this branch.

Every measurement below was taken by driving `reconcile.reconcile` through the real fixtures in
`tests/conftest.py` — the same synthetic registry and `/proc` trees the suite uses — rather than
by reading the code. The throwaway measurement file was deleted afterwards; the numbers are
reproduced by the tests this feature ships.

---

## R1 — The issue's four rows, re-measured post-#33

The issue's table was taken against `main` at 15bf843, before #33 merged, and predicted that all
four rows would read `interrupted=3` once it did. They do. Three `active` items, each owning one
`running` session with a real pid and a real `proc_start`, one pass:

| Condition | `interrupted` | items after | sessions after |
|---|---|---|---|
| `live` / missing dir | 3 | all `interrupted` | all `lost` |
| `live` / empty dir | 3 | all `interrupted` | all `lost` |
| `live` / unlistable dir | 3 | all `interrupted` | all `lost` |
| `live` / unrecognised version | 3 | all `interrupted` | all `lost` |
| `no-remote` / missing dir | 3 | all `interrupted` | all `lost` |
| `no-remote` / empty dir | 3 | all `interrupted` | all `lost` |
| `no-remote` / unlistable dir | 3 | all `interrupted` | all `lost` |
| `no-remote` / unrecognised version | 3 | all `interrupted` | all `lost` |

**Decision**: the defect is confirmed as described, at both levels, and the `dry_run` mask the
issue named as hiding two of the rows is gone. Two conditions the issue did not enumerate —
an unlistable directory and an unrecognised registry version — behave identically, which
matters because they are reached by different flags.

**Anomalies raised**: none, in six of the eight rows. The two `unrecognised version` rows raise
`registry_version_unknown` — which says the *identification path* degraded, not that a
reconciliation pass concluded anything from it. Nothing anywhere reports the mass interruption
as blind.

---

## R2 — Which flag each condition sets

Measured at `sessions.scan` itself, so the mapping is not inferred:

| Condition | `directory_missing` | `degraded` | `unknown_versions` | `entries` |
|---|---|---|---|---|
| directory absent | `True` | `False` | `()` | 0 |
| directory present, empty | `False` | `False` | `()` | 0 |
| directory present, mode `000` | `True` | `False` | `()` | 0 |
| one file, version `9.9.9` | `False` | `False` | `('9.9.9',)` | 0 |

Two consequences.

**An unlistable directory is already covered by `directory_missing`.** `sessions.scan` catches
the `OSError` from `os.listdir` and sets the flag, and its comment says why it uses `listdir`
rather than `glob` — glob swallows the refusal and yields nothing, which is the silence the flag
exists to break. So the guard needs no new observation for this case.

**The unrecognised-version case does not set `degraded` at the scan.** It sets it one layer up:
`reconcile.scan_registry` raises the anomaly and then, *only when the first scan produced no
usable entries*, re-scans via `sessions.scan_via_proc`, which returns `degraded=True`. So inside
`reconcile()` the flag is set for the all-refused case and clear for the partly-refused one.

---

## R3 — The partly-refused registry, and why it is treated as unobservable

The gap R2 exposes: one worker on a known version and one on a refused version produces a scan
with `degraded=False`, `directory_missing=False`, one entry, and a non-empty `unknown_versions`.
No flag says "an observation was refused", yet a session belonging to the refused file is
invisible and would be concluded dead.

`capacity.py:_registry_unusable` tolerates that case deliberately — its third clause is
`unknown_versions and not entries`. That is right for a *count*: the count is low by however
many files were refused, and a low count withholds dispatch, which is the safe direction.

It is wrong for a *death conclusion*. A conclusion drawn about a session we refused to read is
not conservative in any direction; it is simply unfounded. And the blindness is systematic
rather than incidental — a worker upgrade changes the version every registry file is written
with, so "one refused file" is the leading edge of "every file refused", not a stable minority.

**Decision**: reconciliation treats any non-empty `unknown_versions` as unobservable, whether or
not usable entries came back with it. Its rule therefore differs from `capacity.py`'s by that
one clause, and the difference is written down at the guard rather than left to be rediscovered.

**Alternative rejected — matching refused files to sessions by pid.** The registry file name
encodes a process identifier in the test fixture, and a session row carries a pid, so in
principle the refused files could be excluded individually. Rejected: the file naming is the
worker's business and undocumented, the fixture's convention is not evidence about the real
one, and the whole reason a version is refused is that we do not know what shape that file is
in. Guessing at the identity of a file we declined to parse is the assumption the version gate
exists to refuse.

---

## R4 — Unreadable files are deliberately *not* treated as unobservable

`RegistryScan.unreadable` collects files that could not be opened, did not parse, or lacked a
`sessionId`/`pid`. It is tempting to add it to the guard: a session whose file was unreadable is
just as invisible as one whose version was refused.

Measured against `parse_entry`: a **truncated** file — the ordinary result of reading while the
worker writes, which the docstring calls out as normal rather than an error — lands in
`unreadable` via the JSON decoder. So this condition occurs on healthy machines during normal
operation.

**Decision**: `unreadable` does not make a pass blind. Including it would switch the liveness
sweep off at random on a busy machine, which is the failure #33 exists to prevent, in exchange
for closing a race whose window is one file write.

**Accepted residual risk, stated rather than hidden**: an item whose registry file is truncated
at the exact instant of a pass can still be interrupted while its worker lives. It is a
single-item, single-pass exposure rather than the wholesale one this feature fixes, `unreadable`
is already reported in the pass summary so the log shows it, and `resume` recovers it. Closing
it needs a second observation — re-reading the one file — which is a different feature with a
different shape.

---

## R5 — Every place a "dead" conclusion is drawn from an absent entry

Read out of the post-#33 tree, then each one measured.

| Site | What it concludes | Blind-scan behaviour, measured |
|---|---|---|
| the active-item sweep in `reconcile()` | item → `interrupted`, session → `lost` | **wrong**: 3 of 3 interrupted (R1) |
| `reclaim_stale_session`, via `_sweep_stale_sessions` (#28) | session → `lost` | **wrong**: `reclaimed=1`, row `lost`, in all four conditions |
| `_sweep_superseded_sessions` (#33) | session → `lost` | **wrong**: `superseded=1`, earlier attempt `lost` |
| `_retire_finished_sessions` (#138) | nothing — "no entry, nothing to end" | already safe |
| `_orphan_sweep` (FR-043) | nothing — it iterates `scan.entries` | already safe |
| `_resolve_orphan_anomalies` | reads `/proc` directly, not the scan | unaffected |

**Decision**: guard the first three, touch neither of the last two. A guard on a sweep that
cannot draw a false conclusion is code with nothing to do, and would tell a later reader it had
something to do.

The second and third rows are why the spec has a second user story. They do not interrupt
items; they close session rows, and a closed row is a returned capacity slot. Closing a row
whose worker is alive reports fewer running sessions than exist — the under-count #28 (PR #43)
established as the only direction of capacity error that does real harm, arriving here from the
opposite side.

---

## R6 — Where the guard goes in the active-item sweep

The sweep makes several decisions before it reaches the registry, and FR-004 requires those to
be untouched. Measured order in `reconcile()`:

1. `session is None` → interrupt. **Registry-independent** — the conclusion is drawn from the
   database. Must stay before the guard.
2. `_sweep_superseded_sessions` → guarded separately, per row.
3. `entry.alive()` → on a blind scan this cannot be true, so control always falls through.
4. session already `exited_clean`/`exited_error` → continue. **Registry-independent.**
5. `not session.pid` → `skipped_never_real`. **Registry-independent**, and the discriminator
   #33 was about; keying anything new off it would re-open that issue.
6. the transition to `lost` and `interrupted`. **This is the only registry-dependent
   conclusion**, and the guard belongs immediately above it.

**Decision**: one guard, at step 6. That placement satisfies FR-004 by construction rather than
by a test remembering to check it — every registry-independent branch has already returned.

---

## R7 — The guard inside `reclaim_stale_session`, and its third caller

`reclaim_stale_session` has three callers: `_sweep_stale_sessions`, `_retire_one`, and
`operations.abandon`. Putting the guard inside the function rather than in the sweep decides
something for all three.

- **`_sweep_stale_sessions`** — the intended target.
- **`_retire_one`** — unreachable while blind. It is only called after `scan.find()` returned a
  *live* entry, which a blind scan cannot produce. Its `settled in ("reclaimed", "left")`
  accounting is therefore unaffected in practice; it is still worth knowing that a third return
  value exists.
- **`operations.abandon`** — reachable, and it takes its own fresh scan. Today a maintainer
  abandoning an item while the registry is unreadable closes a row whose worker may be running.
  With the guard, the row is left open, and the next readable reconciliation pass settles it.

**Decision**: the guard lives in `reclaim_stale_session`, which is the function that already
holds the rule "a worker that can be seen is reported, not closed". Blindness is a case of that
same rule and belongs beside it, not copied into each caller.

`abandon` gains one line of output saying the row was left open and why. Without it the
maintainer is told the item was abandoned while a slot silently stays subscribed, which is the
class of silence this feature is about.

---

## R8 — Recording it: what, where, and how often

Three candidate records, and the constraint on all of them is the 60-second loop.

**The pass summary.** `reconcile.pass` already carries `sessions.summarise(scan, ...)`, which
reports `sessions_found`, `unknown_versions`, `unreadable` and `degraded` — and **not**
`directory_missing`. So today the summary of a pass across a vanished registry is
indistinguishable from the summary of a pass across an idle machine, which is SC-004 exactly.

**Decision**: `summarise` gains `directory_missing`, and the pass result gains a count of
withheld conclusions. One record per pass, unchanged in frequency.

**A per-row record at each withheld decision.** Rejected. Three open rows on a blind machine
would write 4,320 records a day, each carrying one bit. `_retire_finished_sessions` sets the
precedent for declining exactly this, and the count in the pass summary preserves the fact.

**An anomaly.** The issue asks whether a blind pass should raise one, and #33's plan enumerated
the missing distinction as an accepted Principle III gap *on the explicit ground that closing it
was tracked here*. So: yes. `db.raise_anomaly`'s partial unique index absorbs the repeat — the
index is `(kind, COALESCE(entity_type,''), COALESCE(entity_id,''), dry_run)`, so a kind with no
entity dedupes to one open row, which is how `registry_version_unknown` and
`capacity_unobservable` already survive the same loop. No migration is needed.

**Retraction.** Two kinds already retract themselves — `orphan_session` and
`card_create_failing` — each through a narrow `db.open_*_anomalies` helper and a small resolver
in `reconcile`. `registry_unobservable` has the property those two were chosen for: its
condition can be positively re-established as false, by the next pass that reads the registry.

**Decision**: a third resolver, in the same shape as the existing two, run on every pass whose
observation *was* usable. Without it, a transient blindness leaves a permanent row on a list the
maintainer is meant to read — which is the "mostly stale, so cleared without reading" failure
issue #138 named.

---

## R9 — What the anomaly is about

`registry_version_unknown` and `capacity_unobservable` both raise with `entity_type=None`,
`entity_id=None`, `dry_run=False`, and `test_migration_014_leaves_underivable_rows_real` names
both as facts about the machine rather than about a rehearsal.

**Decision**: `registry_unobservable` matches them exactly. It is a statement about this
machine's registry; the effect level of the items that happened to be running is not a property
of it, and FR-012 forbids consulting one anyway.

`ANOMALY_KINDS` in `models.py` must gain it, or `robot-army anomalies` will not list it among
the kinds the system can raise (FR-065). `docs/guide/operating.md` documents each kind and must
gain an entry; `docs/guide/audit-log.md` documents the `reconcile.pass` shape and must record
what it gains.

---

## R10 — Counting the withheld conclusions

`ReconcileResult.checked` is incremented once per `active` item before any skip, so it cannot
carry this. #33 added `skipped_never_real` for the same reason, and its precedent settles the
shape: a distinct counter, named for the reason, reported in the summary.

**Decision**: one counter, `liveness_withheld`, incremented at each of the three guards. One
rather than three, because FR-008 asks how many conclusions were withheld and not which sweep
would have drawn them — the sweep is recoverable from the state each row is left in, and three
counters that are always zero together would be three ways to say one thing.

The pass summary also gains `registry_observable`, a boolean, and the reason string when it is
false. Between them a reader can tell "nothing to withhold" (`registry_observable: true`,
`liveness_withheld: 0`) from "withheld everything" (`false`, `3`) from "blind, but there was
nothing running anyway" (`false`, `0`) — three genuinely different passes that today write the
same line.

---

## R11 — What is deliberately not built

| Rejected | Why |
|---|---|
| A `/proc` fallback, as `capacity.py` does | `scan_via_proc` cannot recover session identifiers, so nothing it returns can be matched to a session row. It answers capacity's question and not this one; running it would cost a full `/proc` enumeration to learn nothing (FR-006) |
| A configuration key for the behaviour | Declining to conclude from an observation that failed is not a policy. One caller, no second use in hand (Principle I, FR-013) |
| Escalation after N blind passes | State that has to persist between passes, to say something the standing anomaly already says |
| A new field on `RegistryScan` | The three flags needed are all present. The judgement is the consumer's, and `capacity.py` already makes its own |
| Sharing `capacity._registry_unusable` | Its third clause is deliberately different (R3), and the two functions answer different questions about the same object. Sharing them would make one of the two silently wrong the next time either is edited |
| Treating `unreadable` as blindness | R4 — it happens on healthy machines during normal operation |
| Guarding `_retire_finished_sessions` or `_orphan_sweep` | R5 — neither draws a false conclusion from an empty scan |
