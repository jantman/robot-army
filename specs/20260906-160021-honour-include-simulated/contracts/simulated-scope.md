# Contract: what `--include-simulated` means on each verb

Amends the universal rule in
[milestone 001's CLI contract](../../001-minimum-daemon/contracts/cli.md), the way
[008's status-output contract](../../008-status-hidden-simulated/contracts/status-output.md)
amended what `status` prints. That rule said the flag "on any listing command includes `dry_run`
rows"; it was true of the parser and false of three of the commands.

**The rule, restated so it cannot be true of a parser and false of a command:**

> A verb offers `--include-simulated` **if and only if** the rows it prints can be rehearsed and
> it filters them. Every other verb does not accept the option.

## The set

`cli.py` holds this set as a named constant, and the parser decorates from it. It is the subject
of the cross-verb test, so the parser's claim and the test's population are one object.

| Verb | Offers the flag | What it scopes | Status |
|---|---|---|---|
| `status` | yes | work-item counts, the listing, **and the anomaly block** | anomaly block newly scoped |
| `cards` | yes | card rows | already correct |
| `worktree list` | yes | work items carrying a worktree path | already correct; newly tested |
| `anomalies` | yes | anomaly rows | **new** |
| `log` | yes | audit records | **new** |
| `repos` | **no** | — | **the option is removed** |

`repos` prints one row per onboarding record. Onboarding inspects a real clone on disk, computes
a fingerprint from real `git` output, and records the origin it actually found; nothing in
`effects.py` intercepts it and there is no rehearsed path. The table cannot hold a rehearsed row,
so the option is removed rather than made to filter an empty population.
`robot-army repos --include-simulated` is an argparse usage error, exit 2, naming the
unrecognised option. No deprecation path — the option never did anything.

## What each verb prints

### The withheld sentence

Unchanged and shared, from `operations._withheld_note`:

```
N simulated rows withheld — pass --include-simulated to show them
```

One definition, so the count and the flag name cannot drift apart. Every listing below uses it
verbatim.

The number must equal **exactly** the rows the flag would then reveal, under the same filters the
visible rows were subject to. That equality is structural, not maintained by hand: the visible
set and the withheld set come from two accessors sharing one predicate, and any Python-side
filter is applied to both.

### `anomalies`

| Case | Output |
|---|---|
| Rows visible, some withheld | the listing, a blank line, the withheld sentence, then the known-kinds line |
| No rows visible, some withheld, no window | `no outstanding anomalies (N simulated rows withheld — pass --include-simulated to show them)` |
| No rows visible, some withheld, `--since D` | `no anomalies detected in the last D (N simulated rows withheld — …)` |
| No rows visible, none withheld | unchanged: `no outstanding anomalies`, or `no anomalies detected in the last D` |

The two empty listings stay distinguishable, which is what milestone 012 added them for: a window
that matched nothing is not an all-clear, and neither is a scope that withheld everything.

`--since` composes with the flag rather than replacing it, and the withheld count is scoped to
the same window. `--all` composes too: it widens the listing to acknowledged and resolved rows,
and the withheld count then describes the rehearsed rows *within that wider set*.

A rehearsed anomaly shown under the flag is marked. Marking follows the convention the other
listings already use — `*` against the row's leading identifier — so `[7]` becomes `[7]*`, and a
legend line `* = simulated (dry-run) row` appears once beneath a listing that contains one.

`--acknowledge <id>` is unaffected: an explicit id is already an explicit act, so it reaches a
rehearsed anomaly without the flag. This is the same rule `db.get_work_item` follows.

### `status`

The anomaly block is scoped by the same value as the rest of the command:

```
unacknowledged anomalies (N):
  [7] card_create_failing card:abc @ 2026-09-06 14:02
  N simulated rows withheld — pass --include-simulated to show them
```

The withheld sentence is indented with the rows it belongs to, matching how the counts section
already places its own. When the flag is given, rehearsed rows appear marked and the legend
already printed for the work-item listing covers them.

A block that would be empty prints nothing, as today — with one exception: if every anomaly was
withheld, the block prints its header with a count of `0` and the withheld sentence. Silence
there would say "nothing was detected", which is the misreading this whole feature exists to
prevent.

### `log`

```
2026-09-06 14:02 → trello.issue.create [error] card:abc  {...}

(N simulated records withheld — pass --include-simulated to show them)
```

The unfiltered reader (`robot-army log`) scans every daily file, so its count is the true number
withheld across the whole scan, under whatever `--since`, `--item` and `--limit` were in force.

The paged reader (the web's `/log`) stops when the page is full or its byte budget is spent, so
it reports the number withheld **from the records it scanned for this page**, in those words. It
is the only figure that is both true and useful; the page already tells the reader when its scan
stopped early.

`--limit` applies **after** the simulated filter, so `log --limit 20` shows twenty real records
rather than twenty records of which some are hidden. The filter is applied inside the scan, not
to a finished page, so a page whose region is entirely rehearsed cannot come back empty while
older matching records remain.

**A limit bounds the withheld count too, and changes the sentence.** Counted over the whole
scan, `log --limit 3` against ten rehearsed and five real records would promise ten withheld
while `--limit 3 --include-simulated` returned the same three and revealed none of them — a
number true of the file and false of the output printed beneath it. Under a limit the count
describes the stretch beginning at the oldest record shown, and says so:

```
N simulated record(s) among these withheld — pass --include-simulated to show them
```

Unlimited, the count really is every record the flag would add, and the shared unqualified
sentence is used. This is the same distinction `read_log_page` draws with "on this page", for
the same reason: a bounded reader may only make claims about what it bounded.

Records shown under the flag keep the `[simulated]` marker `_format_record` has always written.
The unparseable-line count is independent of the simulated filter and is reported as it is today.

`log --follow` is scoped the same way. It is a mode of the same verb and takes the same option,
so a tail showing rehearsed records either way would be this defect surviving one level down —
and it is the mode where a rehearsal drowns real work most completely, because a dry run at
speed writes far more records than live work does. Nothing is counted there: a withheld total is
a statement about a finite scan, and a tail has no end to count against. A line that cannot be
parsed is still printed rather than filtered, because a line we cannot judge is not a line we
may drop.

### `worktree list` and `cards`

Unchanged. Both already filter and both already print the withheld sentence; they are named here
because the contract is the set, and a verb absent from it would read as one that does not
filter.

## Machine-readable output

`--json` mirrors the rendered output exactly — one list, not two renderings that can disagree.

| Verb | Payload |
|---|---|
| `anomalies` | `anomalies` is the visible set; `withheld_simulated` is the count. `known_kinds` unchanged |
| `status` | `anomalies` is the visible set; `withheld_simulated` gains an `anomalies` key beside its existing `counts` and `items` |
| `log` | `records` is the visible set; `withheld_simulated` is the count. `unparseable_lines` unchanged |

`withheld_simulated` is **always present**, including as `0`. A consumer must never have to tell
"nothing was withheld" apart from "this build does not report it" — the absent-versus-zero
ambiguity milestone 008 removed from `status` and which is removed here from its siblings.

Every anomaly object gains `"simulated": true|false`, beside the existing `resolved_at` and
`acknowledged_at`. A payload that hid rows without letting a consumer identify the ones it did
show would have moved the ambiguity rather than removed it.

## The web interface

The site-wide simulated toggle already exists, is already carried on every link, and is already
handed to both views. Two of them discard it.

| Surface | Behaviour |
|---|---|
| `/anomalies` | filtered by the toggle; rehearsed rows marked; the withheld count stated on the page |
| `/log` | filtered by the toggle; the page's scanned-region withheld count stated |

FR-057 applies on both front ends: a rehearsed row is marked wherever it is shown. The CLI
writes `*` after the row's leading identifier; the web uses `mark_simulated`, as every other
listing on the site does. A page that filters correctly but renders a revealed rehearsed row
identically to a real one has fixed one half of the defect and left the other.

Each view discloses a withheld row **exactly once**. A section that rendered nothing carries its
count in place of its empty text, so the standalone note stands down when there is nothing to
stand beside it — otherwise a page whose whole window was rehearsed states the number twice.
| the anomaly pill in the chrome | counts within the scope the page was served with, on **every** page |

The pill is the part most easily left behind: it is rendered on every view and links to
`/anomalies`, so an unscoped count disagreed with its own destination the moment the toggle was
off — one interface handing the reader two numbers for one question.

`repos` has no web page at all (the nav is `/active`, `/queue`, `/interrupted`, `/cards`,
`/anomalies`, `/log`), so removing the CLI option removes that verb's whole surface and the web
needs no corresponding decision.

## Retraction

`anomalies`'s help no longer says "conditions detected but not resolvable". Two kinds now retract
themselves, and a third claim that none do would be wrong in the one place a reader looks first.

| Kind | Retracted when | By |
|---|---|---|
| `orphan_session` | the recorded pid and start time no longer name a live process | `reconcile._resolve_orphan_anomalies` (issue #138) |
| `card_create_failing` | the card it names has reached `linked` | `reconcile._resolve_card_create_anomalies` (**new**) |

Every other kind still waits for `--acknowledge`, and that restraint is deliberate: these two are
the kinds whose truth can be positively re-established as *false*. A retraction writes
`anomaly.resolved` to the audit log carrying the evidence, before the row leaves the open list. A
second pass over already-resolved state writes nothing and logs nothing. An anomaly whose card
cannot be found is left outstanding — "I could not check" is never recorded as "it is fine".

A resolved anomaly stays distinguishable from an acknowledged one under `--all`, as migration 012
established.
