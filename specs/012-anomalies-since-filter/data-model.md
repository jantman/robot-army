# Phase 1 Data Model: A `--since` Window on `anomalies`

**No schema change. No migration. No new persisted field.** This feature reads a column that
already exists on every row.

## Entity: `Anomaly` (existing, unchanged)

Table `anomalies`, dataclass `robot_army.models.Anomaly`.

| Field | Type | Role in this feature |
|---|---|---|
| `id` | INTEGER | Displayed. Untouched. |
| `kind` | TEXT | Displayed. Untouched. |
| `detail` | TEXT (JSON) | Displayed via `detail_obj`, redacted in the dict form. Untouched. |
| `detected_at` | TEXT | **Read by the filter.** UTC ISO 8601 with a `Z` suffix, written by `states.utcnow()` as `%Y-%m-%dT%H:%M:%SZ`. |
| `entity_type` | TEXT NULL | Displayed. Untouched. |
| `entity_id` | TEXT NULL | Displayed. Untouched. |
| `acknowledged_at` | TEXT NULL | Governs `--all` eligibility, as today. **Not** what `--since` filters on. |

Ordering is `detected_at DESC, id DESC` and stays that way; filtering removes rows, it never
reorders them.

## Transient value: the window

Not persisted, not part of any record. Computed once per command invocation.

- **Input**: the `--since` string, e.g. `1h`. Vocabulary: a whole number followed by one of
  `s`, `m`, `h`, `d` — defined by `operations.parse_duration` and shared with `log --since`.
- **Cutoff**: `datetime.now(UTC) - parse_duration(since)`, a timezone-aware UTC instant.
- **Predicate**: a row is inside the window when its parsed `detected_at` is **at or after**
  the cutoff. Inclusive on the boundary, matching `log`.
- **Absent input**: `since is None` means no cutoff and no filtering — the existing behaviour.

### Judging a row

Three outcomes, of which the third is the one that matters:

| Case | Result |
|---|---|
| `detected_at` parses, at or after cutoff | listed |
| `detected_at` parses, before cutoff | omitted |
| `detected_at` cannot be parsed | **listed** — see research.md R4; a detected condition is never hidden because its timestamp is unreadable |

## Output shape (`--json`)

The payload keys are unchanged — `anomalies` (a list of the existing per-anomaly dicts, with
`detail` redacted as today) and `known_kinds`. The only difference under `--since` is which
anomalies appear in the list, so the machine-readable and human-readable views cannot disagree
about the window (FR-008).
