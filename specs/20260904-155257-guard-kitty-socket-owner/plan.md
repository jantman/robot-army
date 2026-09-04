# Implementation Plan: Only the maintainer's own terminal socket may receive a dispatch

**Branch**: `speckit/20260904-155257-guard-kitty-socket-owner` | **Date**: 2026-09-04 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260904-155257-guard-kitty-socket-owner/spec.md`

## Summary

Socket discovery currently trusts any path the configured glob matches. It will instead trust
only a path that is a socket, owned by this user, sitting under directories no stranger can
rearrange — checked with one `lstat` and a short walk up the parents before the probe command
runs, so a planted listener is refused without being spoken to. Alongside that, the built-in
default moves out of `/tmp` into the per-user runtime directory (which `paths.runtime_dir()`
already resolves, with an owned-and-private fallback), configuration load warns when a pattern
is rooted somewhere shared, and the three surfaces that report a missing socket — `doctor`, the
daemon's startup check, and `attach` — stop saying "nothing answered" when something answered
and was refused. The launch arguments remain readable through the process table; that is
documented rather than changed, and the reason is recorded in the spec's Assumptions.

## Technical Context

**Language/Version**: Python 3.13 (`requires-python = ">=3.13"`)

**Primary Dependencies**: none added. `os`, `stat`, `glob`, `pathlib` from the standard library;
`stat` is already imported by `config.py` for the token-file mode check.

**Storage**: none. Nothing here persists; the refusals live in the audit log and in memory for
the life of the process.

**Testing**: `pytest`, `tests/unit/`, run with `uv run pytest`. Lint and types with
`uv run ruff check` and `uv run mypy src`.

**Target Platform**: single Linux workstation, one user, kitty 0.48.2 with
`allow_remote_control yes`.

**Project Type**: single Python package (`src/robot_army/`) with a CLI, a daemon, and a local web
interface.

**Performance Goals**: unchanged. Discovery gains at most one `lstat` plus four `stat` calls per
candidate, on a path that runs once per process and then caches.

**Constraints**: the maintainer's existing `/tmp/mykitty-*` configuration must keep working
after the change, with a warning and no edit required.

**Scale/Scope**: five source files, three documentation files, one new test module and additions
to two existing ones.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design. No violations; the Complexity
Tracking table is empty and omitted.*

**I. Simplicity First (YAGNI & KISS)** — PASS. No new dependency, no new module, no new
abstraction. The check is one private function in the file that already does discovery. The
directory rule is one loop rather than a special case for `/tmp` plus a special case for the
runtime directory, which is fewer moving parts, not more. Rejected as speculative: a `ProbeResult`
value object (R5), a pluggable trust policy, and re-validating the cached socket on every use.

**II. Single-User, Local-First** — PASS, and the feature is an expression of the principle: the
operating-system user *is* the trust boundary, so "owned by `os.getuid()`" is the whole of the
identity check and no other identity is ever legitimate. Nothing here reaches the network, and
the default moves state from a shared directory to a per-user one.

**III. Total Accountability** — PASS. The single `kitty.probe` audit record already written at
the end of discovery gains the refused candidates and their reasons alongside the ones probed, so
the log alone answers what was found, what was refused, why, and what was selected. No action
here goes unlogged, and **no exception is claimed**: the per-candidate `lstat` is not a state
change and is fully described by the aggregate record it feeds. The configuration warning travels
the existing `Config.warnings` path, which the CLI, the daemon startup, `doctor`, and the web
banner all already print.

**IV. Interruption Tolerance** — PASS. Discovery is read-only and holds nothing: killed at any
point it leaves no partial state, and the next process repeats it from scratch. No file is
written, so atomicity does not arise. The probe subprocess keeps its existing explicit timeout.
The check adds no retry and no blocking call — `lstat` on a local filesystem is the only new
syscall, and a candidate that cannot be inspected is refused rather than waited on.

**V. Public Code, Unsupported Project** — PASS. No credential, hostname, or address is added.
The documentation changes are written for the maintainer's future self and say plainly what is
protected and what is still exposed. There is no compatibility shim: the old configured location
keeps working because it is genuinely safe under the new rule, not because a special case was
added for it.

**Development Workflow** — the two mandated questions:

- *What does this log?* Every discovery attempt, in one record: the pattern, the candidate count,
  each candidate probed with its exit status, and now each candidate refused with its reason. The
  configuration warning is emitted through `Config.warnings` at load and printed by every front
  end. Nothing new is silently swallowed.
- *What happens if it is killed halfway through?* Nothing to recover. Discovery writes no state
  and takes no lock; a process killed mid-discovery leaves the filesystem exactly as it found it,
  and the audit record for that attempt is simply absent — the next start re-runs the whole
  discovery. The change removes no existing failure path and adds none.

*Code parsing external input* (Development Workflow): the candidate paths are external input —
they are filenames another local user can create — so the tests exercise the refusal paths
(unowned, not a socket, symlink, vanished, unsafe directory) and not only the accepting one.

## Project Structure

### Documentation (this feature)

```text
specs/20260904-155257-guard-kitty-socket-owner/
├── spec.md              # /speckit-specify output
├── plan.md              # This file
├── research.md          # Phase 0 — R1..R7
├── data-model.md        # Phase 1 — the candidate, the verdict, the refusal reasons
├── quickstart.md        # Phase 1 — how to prove it by hand
├── contracts/
│   ├── config.md        # [terminal] socket_glob: default, warning, unchanged keys
│   └── discovery.md     # the acceptance rule and what each surface reports
├── checklists/
│   └── requirements.md  # /speckit-specify quality gate
└── tasks.md             # /speckit-tasks output (not created here)
```

### Source Code (repository root)

```text
src/robot_army/
├── boundaries/
│   └── kitty.py         # the acceptance rule, the refusal record, the richer BoundaryError
├── config.py            # computed default; the world-writable-root warning
├── paths.py             # unchanged — runtime_dir() already answers R3
├── daemon.py            # startup check reports refusals
└── operations.py        # doctor's terminal-socket detail reports refusals

tests/unit/
├── test_kitty_socket_trust.py   # new — the acceptance rule, every refusal branch
├── test_config.py               # default location, the new warning, existing warning intact
└── test_doctor_projects.py      # doctor's detail distinguishes the three states

README.md                # listen_on line, why not /tmp, the argv exposure
share/config.example.toml # socket_glob default; a comment on [repos.*] env visibility
docs/security-analysis.md # RA-15 marked resolved, with what remains true
```

**Structure Decision**: The existing single-package layout is unchanged. The security control
lives in `boundaries/kitty.py` because that is the only place a socket path becomes a thing we
talk to; `config.py` gains the default and the warning because that is where every other
configured value is judged; the three reporting surfaces change only in the string they compose.

## Phase 1 design

See [data-model.md](./data-model.md) for the candidate verdict and its reasons,
[contracts/discovery.md](./contracts/discovery.md) for the acceptance rule and the surface
wording, [contracts/config.md](./contracts/config.md) for the `[terminal]` changes, and
[quickstart.md](./quickstart.md) for the by-hand proof — including the one that matters most:
plant a listener that sorts ahead of the real socket and confirm it receives nothing.
