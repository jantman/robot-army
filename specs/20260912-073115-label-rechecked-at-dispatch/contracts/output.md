# Contract: What the Maintainer Sees

## The hold, on `status` and the web queue

`hold`: `not_labelled`

`detail`, when labels were read:

```
issue does not carry the 'scratch' label (has: robot-army, bug) — label the issue, or change [github] label back
```

`(has: none)` when the list is empty, matching `poll.evaluate`'s wording.

`detail`, when they could not be read:

```
the stored labels could not be read, so the issue is treated as not carrying the 'scratch' label
```

## `poll.labels_refreshed` (new audit action)

```json
{"action": "poll.labels_refreshed", "outcome": "ok",
 "entity_type": "work_item", "entity_id": 17, "target": "owner/name#42",
 "detail": {"old": ["robot-army"], "new": ["robot-army", "scratch"]}}
```

Written only when the label sets differ, for a `ready` row. Carries `dry_run` as the row does.

## `daemon.label_warning` (new audit action)

```json
{"action": "daemon.label_warning", "outcome": "error",
 "detail": {"label": "scratch", "count": 4, "item_ids": [3, 5, 8, 9],
            "warning": "4 ready items do not carry the 'scratch' label and are held: 3, 5, 8, 9"}}
```

At most one per startup, and none when every `ready` item carries the label.

## `dispatch.at_capacity` (existing)

The same shape as before. `reason` may now be `not_labelled`, with `detail` as above.
