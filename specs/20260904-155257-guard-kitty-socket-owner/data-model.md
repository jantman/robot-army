# Data Model: Only the maintainer's own terminal socket may receive a dispatch

**Feature**: `specs/20260904-155257-guard-kitty-socket-owner` · **Date**: 2026-09-04

Nothing here is persisted. Two of the three shapes live only for the duration of one discovery;
the third lives on the display object for the life of the process, next to the socket it already
caches.

## Candidate

A path produced by expanding the configured pattern.

| Field | Source | Notes |
|-------|--------|-------|
| `path` | `glob.glob(pattern)` | filesystem path; the `unix:` prefix, if the pattern carried one, is stripped before inspection and re-attached before use |
| `order` | `sorted(..., reverse=True)` | unchanged from today; ordering decides only which *acceptable* candidate wins |

A candidate is not trusted for being a candidate. It becomes usable only by passing the
acceptance rule in [contracts/discovery.md](./contracts/discovery.md).

## Refusal

Why one candidate was not used. Produced by the acceptance rule, consumed by the audit record,
the diagnostic, the daemon's startup problem, and the `BoundaryError` message.

| Field | Type | Notes |
|-------|------|-------|
| `socket` | `str` | the candidate, as it would have been addressed |
| `reason` | `str` | one of the fixed reasons below, with the offending path interpolated where it is a directory |

Reasons, exhaustive:

- `not a socket` — the name exists but is a file, a directory, or a symbolic link.
- `owned by uid <n>, not <ours>` — a socket somebody else created.
- `directory <path> is writable by others without the sticky bit` — the name could be swapped
  between the check and the connection.
- `directory <path> is owned by uid <n>` — its owner can replace entries in it whatever the mode.
- `directory <path> is a symbolic link` — what it resolves to may differ by the time the name is
  used, and following it to find out is the substitution being refused.
- `cannot be inspected: <errno description>` — vanished, or a directory on the path is unreadable.

The reasons are stable strings because three surfaces quote them and a test asserts on them. A
refusal never carries the file's contents or anything read through the socket — there is nothing
to read, because a refused candidate is never spoken to.

## Discovery outcome

What one call to `probe()` produced. The socket is already cached on the display object; the
refusals join it.

| Field | Type | Notes |
|-------|------|-------|
| `socket` | `str \| None` | unchanged: the selected `unix:`-prefixed target, cached for the process lifetime |
| `refusals` | `tuple[Refusal, ...]` | from the most recent discovery; empty when every candidate was probed or when nothing matched |
| `probed` | existing `tried` list | unchanged: candidates that passed the rule and were asked, with their exit status |

`candidates found but all refused` and `no candidates at all` are distinguished by whether
`refusals` is empty when `socket` is `None` — which is the whole of what FR-007, FR-013 and
FR-014 need.

## Configuration

| Key | Before | After |
|-----|--------|-------|
| `[terminal] socket_glob` | `"/tmp/mykitty-*"`, fixed at import | `f"{paths.runtime_dir()}/mykitty-*"`, computed at load |
| validation | warns if no wildcard | that warning unchanged, plus a warning if the pattern's fixed leading directory fails the directory half of the acceptance rule |

The value stays a single string, and no key is added or removed: a maintainer's existing file
parses identically after this change.
