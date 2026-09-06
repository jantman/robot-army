# Quickstart: proving the base ref comes from the repository

Prerequisites: `uv sync`, and a scratch directory. Nothing here touches a real repository or
the network.

## 1. The suite

```bash
uv run pytest
```

The whole suite must pass — the constitution's bar for "complete". The example-configuration
drift test is the one that fails loudly if step 5 was skipped, and it names the command to run.

## 2. Build a `master` repository and a clone of it

```bash
cd "$(mktemp -d)"
git init -q -b master upstream
git -C upstream commit -q --allow-empty -m initial
mkdir -p .claude
printf '{"permissions":{"allow":["Bash(ls:*)"]}}\n' > upstream/.claude-settings.json
git -C upstream add -A && git -C upstream commit -q -m settings
git clone -q upstream demo
git -C demo symbolic-ref refs/remotes/origin/HEAD    # refs/remotes/origin/master
```

## 3. Onboard it

Point `[paths] repo_root` at the parent of `demo` in a scratch config, with **no**
`base_branch` set anywhere, then:

```bash
uv run robot-army --config /path/to/scratch-config.toml onboard owner/demo
```

Expected — the line this issue is about, now with its provenance:

```
base ref     : master   (detected from origin/HEAD)
```

and, if `.claude/settings.json` is committed on `master`, its full contents under "committed
tool-permission settings at the base ref". Before this change the same clone printed `main` and
"no committed `.claude/settings*.json` at the base ref".

The machine-readable form says the same thing:

```bash
uv run robot-army --config … onboard owner/demo --json | jq '{base_ref, base_ref_source}'
# { "base_ref": "master", "base_ref_source": "detected" }
```

## 4. Prove each rung of the ladder

| Do this | Expect |
|---|---|
| add `[repos."owner/demo"] base_branch = "develop"` | `develop   ([repos."owner/demo"] base_branch)` — and no `symbolic-ref` in the audit log for that run |
| remove it; `git -C demo symbolic-ref -d refs/remotes/origin/HEAD` | `main   (the default; origin/HEAD is not set)` |
| add `[worker] base_branch = "trunk"` with the ref still deleted | `trunk   ([worker] base_branch; origin/HEAD is not set)` |
| restore the ref (`git -C demo remote set-head origin --auto`) with `[worker] base_branch = "trunk"` still set | `master   (detected from origin/HEAD)` — detection outranks the global key |

## 5. The example configuration

```bash
uv run robot-army example-config --output share/config.example.toml --force
git diff share/config.example.toml
```

Expected: `base_branch` under `[worker]` is now commented out, with its reason on the indented
line beneath it. The rendered file must still load clean and configure nothing outward-facing —
`tests/unit/test_example_config.py` checks both.

## 6. The audit record

```bash
uv run robot-army audit --action repo.onboard --limit 1
```

The `detail` carries `base_ref` and `base_ref_source`, so the log alone answers what branch was
approved and what decided it — Principle III's reconstruction standard applied to the line that
used to be a guess.
