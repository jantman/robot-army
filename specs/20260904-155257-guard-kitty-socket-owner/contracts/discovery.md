# Contract: socket discovery

**Feature**: `specs/20260904-155257-guard-kitty-socket-owner` · **Date**: 2026-09-04

## The acceptance rule

A candidate is acceptable if and only if **all** of the following hold. They are evaluated in
this order and the first failure is the refusal reason.

1. `os.lstat(path)` succeeds. `OSError` → `cannot be inspected: <strerror>`.
2. `stat.S_ISSOCK(st.st_mode)`. A symbolic link fails here, because `lstat` describes the link
   and not its target — which is the point (research R1).
3. `st.st_uid == os.getuid()`.
4. For the candidate's parent directory and every directory above it up to the filesystem root:
   - the directory is not itself a symbolic link — refused by name, because a symlink's own
     mode is `0777` on Linux and the mode clause below would otherwise refuse it with a reason
     that says nothing true about it, and
   - the directory is owned by `os.getuid()` or by uid 0, and
   - it is not writable by group or other, **or** it carries the sticky bit.

   Any `OSError` while walking is a refusal, not a pass.

Rule 4 accepts `/tmp` (root-owned, `1777`) and `/run/user/<uid>` (user-owned, `0700`). It refuses
a `0777` directory without the sticky bit, and any directory owned by a third party.

Only an acceptable candidate is passed to `kitty @ --to`. A refused candidate is never addressed,
so it receives nothing — not the probe, and not a launch.

## Ordering and caching

Unchanged. Candidates are still sorted in reverse and tried in that order; the first that is
acceptable *and* answers is cached for the life of the process. Refusals do not stop the walk.

## What each surface reports

All three compose from the same refusals, so they say the same thing in their own register.

| Surface | Socket found | No candidates matched | Candidates matched, all refused |
|---------|--------------|----------------------|--------------------------------|
| `doctor`, "terminal socket" check | the socket path, as today | `nothing answered '<pattern>'` — unchanged wording | `N candidate(s) refused: <path> (<reason>); …` |
| daemon startup problem | no problem | today's message, unchanged | today's message plus the refusals and their reasons |
| `BoundaryError` from `_require_socket` (reaches `attach` and every launch) | not raised | today's message, unchanged | today's message plus the refusals and their reasons |

The audit record `kitty.probe` carries `refused` alongside the existing `tried`, in the one
record discovery already writes at the end of the walk — success and failure alike.

## Unchanged by this contract

- The `Display` protocol: `probe()` still returns `str | None`.
- `SimulatedDisplay`: still answers with its fictional path, still performs no filesystem check.
- The probe subprocess, its arguments, and its timeout.
- Which acceptable socket wins when several are acceptable.
