# Contract: `robot-army anomalies`

The CLI is this project's only interface contract (Operating Constraints: every capability
reachable and observable from the terminal). It is explicitly **not** a stable public API —
Principle V — so this document records what the command does after this feature, not a promise
to outside consumers.

## Synopsis

```
robot-army anomalies [--since DURATION] [--all] [--acknowledge ID]
                     [--json] [--include-simulated]
```

## Arguments

| Argument | Status | Meaning |
|---|---|---|
| `--since DURATION` | **NEW** | List only anomalies detected within `DURATION` of now. Omit for everything. |
| `--all` | existing | Include acknowledged anomalies. Without it, only unacknowledged. |
| `--acknowledge ID` | existing | Mark anomaly `ID` acknowledged, then list. |
| `--json` | existing | Machine-readable payload on stdout. |
| `--include-simulated` | existing | Unchanged; carried by every read command. |

### `DURATION` grammar

A whole number followed by a single unit character. Identical to `log --since`, because it is
the same parser:

```
DURATION := <digits> ( "s" | "m" | "h" | "d" )
```

Examples: `30s`, `10m`, `2h`, `1d`. Rejected: `2 weeks`, `1.5h`, `-5m`, `10 fortnights`, `abc`.
Each rejection prints the parser's own explanation of what is wrong with it.

An **empty** value (`--since ""`) is not a rejection: like `log --since ""`, it means no window.
The parser would refuse an empty string, but neither command hands it one — and matching `log`
at this edge is what FR-002 asks for.

## Semantics

1. `--since` is parsed **first**. A value the parser rejects ends the command with the usage
   exit status, having neither acknowledged nor listed anything (research.md R5).
2. `--acknowledge` is applied next, exactly as before: it writes its `anomaly.acknowledge`
   audit record, and a missing or already-acknowledged id fails with the existing message and
   the failure exit status.
3. The listing is selected by `--all` (acknowledged eligible or not) and then narrowed by
   `--since` (detected at or after `now - DURATION`).
4. Ordering is newest detection first, unchanged.
5. The list of kinds the system can raise is printed at the end, unchanged, in every case.

## Exit statuses

| Status | When |
|---|---|
| `0` | Listed successfully, including when the listing is empty. |
| `1` | `--acknowledge ID` named no unacknowledged anomaly. Unchanged. |
| `2` | `--since` value the duration parser rejects. |

## Output

### Human-readable, rows present

Unchanged from today — one header line per anomaly with its id, kind, entity and locally
rendered detection time, its detail keys indented beneath, then the kinds trailer.

### Human-readable, no rows

Two distinguishable cases, and the distinction is load-bearing (FR-009):

| Situation | Message |
|---|---|
| No `--since`, nothing to report | `no outstanding anomalies` — unchanged; means all clear |
| `--since` given, nothing inside the window | a message naming the window; does **not** claim there are no outstanding anomalies |

### `--json`

Keys unchanged: `anomalies`, `known_kinds`. The `anomalies` list holds exactly what the
human-readable listing showed, so the two never disagree about the window.

## Invariants this contract asserts

- **Without `--since`, output is byte-identical to the previous release.** The default is the
  whole set; the filter is opt-in.
- **A duration accepted by `log --since` is accepted here with the same meaning**, and one it
  rejects is rejected here with the same message.
- **No row is dropped without the reader being able to tell.** A row whose stored detection
  time cannot be interpreted is listed rather than filtered out.

## Not part of this contract

The web `/anomalies` view and the anomaly count in the web chrome take no `since` and continue
to show the unfiltered set.
