# Quickstart: verifying the onboarding review is real at every effect level

**Feature**: the onboarding security review reads real committed settings at every effect level

The bug this validates against: at `effect_level = "plan"`, `onboard` printed
`no committed .claude/settings*.json at the base ref` for every repository, and recorded an empty
fingerprint as approved.

**The headline result to check is a comparison, not an output**: the same repository onboarded at
`plan` and at `live` must produce the same screen and the same recorded hashes.

## Prerequisites

```bash
uv sync
uv run pytest        # the suite must pass
```

A repository with `.claude/settings.json` committed at its base branch. If none is to hand, make
one:

```bash
mkdir -p /tmp/ra-demo && cd /tmp/ra-demo && git init -q .
mkdir -p .claude
printf '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"echo hi"}]}]}}\n' \
  > .claude/settings.json
git add .claude/settings.json && git commit -qm 'settings'
git remote add origin git@github.com:jantman/ra-demo.git
```

Point a `[repos."jantman/ra-demo"]` section at it, or clone a real repository of yours that has
settings committed.

---

## Scenario 1 — The review is not blank at `plan` (FR-001, FR-002, US1)

`onboard` has no effect-level flag — it reads `[daemon] effect_level` from the config file, which
is exactly how the installation that hit this bug was running.

```bash
sed -i 's/^effect_level = .*/effect_level = "plan"/' ~/.config/robot-army/config.toml
uv run robot-army onboard jantman/ra-demo
```

**Expected**: the screen prints

```
committed tool-permission settings at the base ref:
  These are applied to a dispatched session WITHOUT asking. Read them.

  --- .claude/settings.json ---
  {"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"echo hi"}]}]}}
```

**Was**: `no committed .claude/settings*.json at the base ref`, every time, for every repository.

Answer `n` — this scenario is about the screen, not the approval.

## Scenario 2 — `plan` and `live` agree (SC-001, FR-003)

Run the same command at each level with `--json`, and compare:

```bash
for level in plan local no-remote live; do
  sed -i "s/^effect_level = .*/effect_level = \"$level\"/" ~/.config/robot-army/config.toml
  uv run robot-army onboard jantman/ra-demo --json </dev/null \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["fingerprint"], sorted(d["committed_settings"]))'
done
```

`--json` suppresses the approval screen and writes the prompt to stderr, so `</dev/null` declines
each one and stdout stays a single parseable document.

**Expected**: four identical lines. Any difference between levels is the bug, in whichever
direction it appears.

## Scenario 3 — An approval made against a blank screen no longer stands (US2, FR-004, SC-003)

This reproduces what is already recorded on this installation. Approve at `plan` **with the fix
reverted** — or, equivalently, write an empty fingerprint into the row by hand:

```bash
sqlite3 ~/.local/state/robot-army/state.db \
  "UPDATE repos SET settings_fingerprint = '{}' WHERE repo_key = 'jantman/ra-demo'"
```

Then attempt a dispatch (label an issue, or `robot-army run --once` at any level).

**Expected**: the item is blocked, not dispatched, with

```
committed tool-permission settings at <base ref> differ from what was approved at onboarding
(added: ['.claude/settings.json']; removed: none; changed: none).
Review them and run `robot-army onboard jantman/ra-demo --reapprove`
```

**Then**: `uv run robot-army onboard jantman/ra-demo --reapprove` shows the real settings and the
diff against the approved (empty) set. Approving it clears the block.

## Scenario 4 — A repository that really commits nothing still says so (FR-002)

```bash
uv run robot-army onboard <repo-with-no-committed-settings>     # still at effect_level = "plan"
```

**Expected**: `no committed .claude/settings*.json at the base ref` — now a finding rather than a
constant.

## Scenario 5 — The simulation stops inventing a remote (US3, FR-006)

Against a clone with **no** configured remote, at `plan`:

```bash
git -C /tmp/ra-demo remote remove origin
uv run robot-army run --dry-run --once      # --dry-run is the alias for --effect-level plan
```

**Expected**: the `worktree.prepare` audit record carries
`fetch_skipped: the repository has no configured remote` — the same thing the real path records.

**Was**: the simulation answered `"origin"` for every clone, so that record could never appear
below `local`.

```bash
uv run robot-army log --since 5m --include-simulated | grep worktree.prepare
```

---

## What this closes in the older quickstarts

[001 scenario 6](../../001-minimum-daemon/quickstart.md) and 005's settings-review task could not
be walked below `local`: the screen they check was empty by construction, so a `plan`-level
rehearsal appeared to pass while checking nothing. Their **first half — the review screen — is now
verifiable at every level.** The second half (change the settings, confirm dispatch blocks) still
needs `local` or above only because it needs a dispatch to block.

Those documents are project history and are not rewritten. The published guide is where this is
now stated, on [`docs/guide/1-setup.md`](../../../docs/guide/1-setup.md).
