# Quickstart: Verifying Configurable Spec Kit Instructions

Five checks. The first three need nothing but this repository and a text editor; the fourth needs a
Spec Kit clone; the fifth needs a real dispatch and is the only one that costs an afternoon.

## Prerequisites

```bash
cd ~/GIT/robot-army
uv sync
uv run pytest                  # the suite must pass before and after
```

## 1 — Nothing configured changes nothing

The property that makes the rest safe to ship (FR-013, User Story 3).

```bash
uv run pytest tests/unit/test_speckit_prompt.py -q
```

The golden string in that file is the check. It must pass **without being edited** — if the expected
value needed changing, the block was reshaped for an installation that configured nothing, which is
the failure this whole story exists to prevent.

## 2 — A malformed instruction is refused, out loud

Every shape in [contracts/config.md](./contracts/config.md#validation), against a real file:

```bash
cat >> ~/.config/robot-army/config.toml <<'EOF'

[speckit.commands]
implement = 42
specify = ""
plna = "typo in the command name"
EOF

uv run robot-army doctor        # exits non-zero
```

Expected: three problems reported together, not one — a non-string value, an empty global value, and
an unknown command name — each naming its key. Then remove them again.

The point of running `doctor` rather than a unit test here is that the aggregate `ConfigError` path
is what the maintainer actually meets, and a problem that only exists in a test is a problem that
can be reported in an unreadable way without anyone noticing.

## 3 — Resolution and rendering

```bash
uv run pytest tests/unit/test_speckit_commands_config.py tests/unit/test_speckit_guidance_render.py -q
```

Between them these cover the [resolution matrix](./data-model.md#resolution-matrix) — global,
override, override-with-empty, and absent — plus lifecycle ordering regardless of file order,
verbatim carriage of multi-paragraph text, and the absence of any trace for unconfigured commands.

## 4 — What a repository will actually be told, offline

Configure something real and read it back without dispatching anything (FR-027, SC-008):

```toml
# ~/.config/robot-army/config.toml
[speckit.commands]
implement = "when finished with implementation, commit, push the branch to origin, and open a PR."

[repos."jantman/some-repo".speckit_commands]
implement = ""
```

```bash
uv run robot-army repos                     # the spec-kit column is unchanged: yes / no / off / ?
uv run robot-army repos --json | jq '.repos[] | {repo_key, speckit}'
```

Expected: every detected repository shows `instructions: {"implement": "[speckit.commands] implement"}`,
except `jantman/some-repo`, which shows no `instructions` at all — its override resolved the command
to nothing. The human table is identical either way, which is deliberate: that column answers "is
this repository getting the block", and that has not changed.

## 5 — End to end

The only check that proves the text reaches a session.

```bash
uv run robot-army doctor
uv run robot-army run
# label an issue in a Spec Kit repository
```

Then, in the dispatched session's terminal, confirm the block carries the configured instruction
above its closing sentence and below the constitution paragraph, and that the session was told to
supply it *in addition to* the issue for `/speckit-specify`.

And confirm the record:

```bash
uv run robot-army log --item <id>          # find the one `speckit.detect` record
```

Expected: the existing fields plus `instructions`, naming the setting that supplied each one —
`[speckit.commands] implement`, or the repository's own key where an override was in force. The
instruction **text** is deliberately absent; see the Principle III gap enumerated in
[plan.md](./plan.md#iii-total-accountability).

## What must still be true afterwards

```bash
uv run pytest tests/integration/test_speckit_writes_nothing.py -q
```

Unchanged and passing, untouched by this milestone. Nothing here writes into a worktree, runs a
subprocess, or makes a network request (FR-019), and that test is what says so rather than this
sentence.
