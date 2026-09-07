# Contract — What a Pass That Could Not See May Conclude

The rule reconciliation applies to its own observation *before* applying any rule to a session.
Written as a contract because the same judgement is reached from three places in one pass, a
fourth place in the codebase reaches a deliberately different one about the same object, and
nothing but this document says why they differ.

---

## C1 — The predicate

> An observation is **unusable** when the registry directory could not be listed, when the scan
> fell back to process enumeration, or when any registry file was refused by the version gate.

```
reconcile._registry_unobservable(scan) -> str | None

    scan.directory_missing   -> "the registry directory is absent or unreadable"
    scan.degraded            -> "the registry scan fell back to /proc enumeration"
    scan.unknown_versions    -> "registry version(s) not recognised: <seen>"
    otherwise                -> None
```

Returns the reason rather than a boolean, because the reason is what the record has to carry
(FR-011) and a boolean would make each call site invent its own wording.

**It is not a function of `len(scan.entries)`.** An observation that saw nothing is exactly as
blind whether or not it also found a process, and a clause counting entries would re-collapse
"the machine is idle" into "the registry moved" — the collapse `directory_missing` exists to
prevent.

**An empty-but-present directory is usable.** All three clauses are clear, the predicate returns
`None`, and every sweep concludes exactly as it does today. This is the invariant of User Story 4
and the property the whole existing suite measures.

## C2 — Why this differs from `capacity._registry_unusable`

The two predicates read the same object and disagree on one clause. That is deliberate, and both
sides must keep saying so.

| Clause | `capacity` | `reconcile` |
|---|---|---|
| `degraded` | unusable | unusable |
| `directory_missing` | unusable | unusable |
| version refused | unusable **only when no entry survived** | unusable **always** |

`capacity` is producing a count. A partly-refused registry makes the count low by however many
files were refused, and a low count withholds dispatch — the safe direction, which is the whole
of #28's reasoning.

`reconcile` is producing a conclusion about a *named* session. A conclusion drawn about a session
whose file we refused to parse is not conservative in any direction; it is unfounded. And the
blindness is systematic rather than incidental: a worker upgrade changes the version every file
is written with, so one refused file is the leading edge of all of them.

**Forbidden**: merging the two into one shared function. Doing so would make one of the two
callers silently wrong the next time either is edited, and neither call site would say so.

## C3 — What the predicate does *not* cover

`scan.unreadable` — a file that could not be opened or parsed — does **not** make a pass blind.
A truncated file is the ordinary result of reading while the worker writes, so including it
would switch the liveness sweep off at random on a healthy busy machine (research.md R4).

The residual exposure is one item, one pass, and it is already visible: `unreadable` is reported
in the pass summary and `resume` recovers the item.

## C4 — The three guarded conclusions

Every place that concludes "dead" from the *absence* of a registry entry, and nowhere else.

**Each guard tests two things, not one** (added after PR #160's review): the observation was
unusable, **and** the scan holds no entry for this session. C1's predicate answers a question
about the *pass*; these guards decide about a *row*, and `unknown_versions` is the case where
those come apart — one file refused, every other file's live entry appended in the same loop. An
entry the scan read is a positive observation about that session, and no blindness about other
files makes it less so.

Keying on the pass alone leaked rows: `_retire_one` settles a worker it has just terminated,
that worker's entry was in the scan, and the guard answered "withheld" — process dead, row
`running`, slot subscribed for ever. Every guard below therefore sits *under* the entry lookup.

### C4.1 — The active-item sweep

```
session record is absent                -> item -> INTERRUPTED           (registry-independent)
superseded attempts                     -> C4.3
registry entry found and alive          -> claim its pid; leave
session already records an exit         -> leave                          (registry-independent)
session has no process identifier       -> skip; skipped_never_real       (registry-independent)
unusable AND no entry for this session  -> leave; liveness_withheld += 1  <- NEW
otherwise                               -> session -> LOST, item -> INTERRUPTED
```

**The guard's position is the whole of FR-004.** Every registry-independent branch has already
returned by the time it is reached, so those conclusions survive by construction rather than
because a test remembered to check them. Moving it earlier would break FR-004; moving it later is
impossible, because the line below it is the conclusion.

### C4.2 — `reclaim_stale_session`

```
session is not starting/running         -> "left"                         (registry-independent)
work item is dispatching/active         -> "left"                         (registry-independent)
registry entry found and alive          -> raise orphan_session; "reported"
unusable AND no entry for this session  -> "withheld"                     <- NEW
otherwise                               -> session -> LOST; "reclaimed"
```

Guarded inside the function rather than at its callers, because this function already holds the
rule "a worker that can be seen is reported, not closed" and blindness is a case of that same
rule. Its three callers are settled in [data-model.md §5](../data-model.md).

### C4.3 — The superseded sweep

```
row is the current attempt              -> skip                           (registry-independent)
row is not starting/running             -> skip                           (registry-independent)
registry entry found and alive          -> claim its pid; raise orphan_session; leave open
row has no process identifier           -> skip                           (registry-independent)
unusable AND no entry for this session  -> leave; liveness_withheld += 1  <- NEW
otherwise                               -> session -> LOST
```

### C4.4 — Sweeps deliberately left alone

`_retire_finished_sessions` and `_orphan_sweep` are **unchanged**. Neither draws a conclusion
from an absent entry: the first has nothing to end, the second iterates `scan.entries` and so
reports nothing. A guard on either would be code with nothing to do, and would tell a later
reader it had something to do.

## C5 — What a blind pass records

| Record | Content |
|---|---|
| `reconcile.pass` | `liveness_withheld`, and `directory_missing` alongside the existing `degraded`, `unknown_versions` and `unreadable` |
| `registry_unobservable` anomaly | raised once; `detail` carries the predicate's reason and the withheld count |

**Nothing is written per withheld decision.** Three open rows on a blind machine would put 4,320
records a day into the log, each carrying one bit. `_retire_finished_sessions` declines to record
its own "not yet" for the same reason and sets the precedent. This is the one Principle III gap
this feature opens, and the plan enumerates it.

## C6 — Retraction

The anomaly is retracted by the first pass whose observation was usable — the third kind to
retract itself, after `orphan_session` and `card_create_failing`, and chosen for the property
those two were chosen for: its condition can be positively re-established as *false*, by an
observation that succeeded.

Without retraction, a transient blindness leaves a permanent row on a list the maintainer is
meant to read, which is the "mostly stale, so cleared without reading" failure issue #138 named.

## C7 — Invariants a change to any of the above must preserve

1. **A usable observation behaves exactly as it does today.** Including an empty directory.
2. **A registry-independent conclusion is never withheld.** An item with no session record is
   still interrupted; a record with no process is still skipped as never-real.
3. **The effect level is never consulted.** Not in the predicate, not at a guard, not in a
   comment — `test_only_effects_py_knows_the_effect_level_exists` greps this file's text.
4. **A blind pass writes no state transition that rests on an absence it cannot vouch for.**
   Not "writes no transition at all": a session whose own entry the scan read is one it may
   still conclude about, and a worker this pass terminated must still have its row settled.
5. **Repeated blind passes do not accumulate anomalies**, and a returning registry needs no
   maintainer action.
