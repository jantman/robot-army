# Contract: an installation with nothing onboarded

"Nothing onboarded" means `repos.resolved_all()` is empty — the set card resolution matches
against (research R1).

## `doctor`

One check, emitted whether or not anything is onboarded:

| State | Line |
|---|---|
| ≥ 1 onboarded | `[ok] onboarded repositories  N onboarded` |
| none | `[FAIL] onboarded repositories  none — nothing can be dispatched and every card will be held. Onboard with `robot-army onboard <owner/name>`. Onboarding lives in <db path> and is not recovered if that file is lost.` |

A failure makes `doctor` exit `EXIT_CHECK_FAILED` (4) and appears in `failures`.

## Card resolution

With nothing onboarded, `resolve_repository` returns, whatever the card says:

- `repo_key = None`, `candidates = ()`, `source = "onboarding"`
- `reason = NOTHING_ONBOARDED`:

  > no repository is onboarded, so no card can be filed anywhere — nothing on this card is
  > wrong. Run `robot-army onboard <owner/name>` for the repository this card is for. If the
  > state database was lost or replaced, every repository has to be onboarded again.

With one or more onboarded, behaviour and text are unchanged.

## Held-card comment

For `reason == NOTHING_ONBOARDED`:

```
🤖 robot-army could not file an issue for this card yet.

<NOTHING_ONBOARDED>

Nothing on this card needs to change: it will be picked up automatically on the first pass
after a repository is onboarded.
```

For every other reason: unchanged — it still names the `robot-army:` line.

## Re-evaluation

A held card is normally re-evaluated only when its board activity changes (003 FR-023). A card
whose recorded reason is `NOTHING_ONBOARDED` is also re-evaluated when the onboarded set is no
longer empty — once, since the re-evaluation either resolves it or replaces its reason with
the ordinary one. While nothing is onboarded, the activity gate applies unchanged, so an inert
installation does not re-resolve and re-log every held card on every poll.
