# Contract: Configuration

One new section with one key, and one new per-repository override. Both default to on, per the
spec's Q3 decision (FR-011).

## `[speckit]`

```toml
[speckit]
enabled = true      # default; omit the section entirely for the same effect
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | bool | `true` | Whether a dispatch into a detected Spec Kit worktree gets the guidance block |

An absent `[speckit]` section is exactly equivalent to `enabled = true`. An unknown key in the
section is a configuration error, consistent with every other section's handling.

**This key governs the prompt block only.** Phase observation and the repositories listing are reads
that cost nothing and mislead no one, and they keep working when the guidance is off — which is what
makes turning it off a safe experiment rather than a trade.

## `[repos.*] speckit`

```toml
[repos."jantman/some-repo"]
speckit = false     # this repository gets no Spec Kit guidance
```

| Value | Effect |
|---|---|
| `false` | suppressed for this repository, whatever `[speckit] enabled` says |
| `true` | enabled for this repository, whatever `[speckit] enabled` says |
| absent | inherits `[speckit] enabled` |

The section remains what milestone 005 made it: a set of overrides for exceptions, not a
registration. A repository needs no section to get this behaviour.

## Resolution

```text
Config.speckit_enabled_for(repo_key) -> tuple[bool, str | None]
```

Returns the answer and the setting that produced it — `None` when the answer came from the default,
`"[speckit] enabled"` when the global key decided it, and `'[repos."<key>"] speckit'` when the
override did. Both halves are needed at both call sites: the audit record must say what suppressed a
dispatch (FR-011) and the repositories listing must say the same thing in a column (FR-022), and
computing the reason twice is how the two come to disagree.

Shape follows `permission_mode_for` / `model_for` / `base_branch_for` exactly, extended only by
returning the provenance alongside the value.

## Audit detail

The `speckit.detect` record carries, in `detail`:

| Field | Meaning |
|---|---|
| `detected` | the predicate's answer |
| `reason` | its sentence, verbatim from the contract table |
| `form` | `skills`, `commands`, `mixed`, or absent |
| `enabled` | whether the guidance was applied |
| `suppressed_by` | the setting that turned it off, present only when it was |
| `path` | the worktree that was read |

## Doctor

`doctor` gains nothing. There is no credential to check, no remote to reach, and no configuration
that can be *wrong* — a repository without Spec Kit is a repository without Spec Kit, not a
misconfiguration, and a check that reports it would be a check that is amber on half the machine's
repositories forever.
