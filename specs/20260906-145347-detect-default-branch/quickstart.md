# Quickstart: proving the base ref comes from the repository

Prerequisites: `uv sync`, and a scratch directory. Nothing here touches a real repository —
but note that `onboard` asks GitHub whether the repository is eligible before it reads
anything, which it has always done, so the walk needs a token in `[github] token_env` and a
network. **Detection itself is local**: the four rungs below were exercised with the clone's
`origin` pointing at a URL that was never contacted.

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
mkdir -p upstream/.claude
printf '{"permissions":{"allow":["Bash(ls:*)"]}}\n' > upstream/.claude/settings.json
git -C upstream add -A && git -C upstream commit -q -m initial
git clone -q upstream demo
git -C demo remote set-url origin git@github.com:jantman/demo.git
git -C demo symbolic-ref refs/remotes/origin/HEAD    # refs/remotes/origin/master
```

`git remote set-url` is what makes the identity check pass without the clone ever having
spoken to GitHub; it leaves `origin/HEAD` alone. Name the directory after a repository the
`[github] author` really owns — eligibility is asked of the API, not of the disk.

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

and the full contents of the file committed on `master` under "committed tool-permission
settings at the base ref". Before this change the same clone printed `main` and "no committed
`.claude/settings*.json` at the base ref" — which is exactly what the previous release still
prints, if you want to see the bug and the fix side by side.

The machine-readable form says the same thing:

```bash
uv run robot-army --config … onboard owner/demo --json
# "base_ref": "master", "base_ref_source": "detected",
# "base_ref_detail": "detected from origin/HEAD"
```

(The approval prompt is written to the terminal before the document, so pipe it to a file
rather than straight into `jq`.)

## 4. Prove each rung of the ladder

| Do this | Expect |
|---|---|
| add `[repos."owner/demo"] base_branch = "develop"` | `develop   ([repos."owner/demo"] base_branch)` — and no `symbolic-ref` in the audit log for that run |
| remove it; `git -C demo symbolic-ref -d refs/remotes/origin/HEAD` | `main   (the default; the clone does not say which branch is its default)` |
| add `[worker] base_branch = "trunk"` with the ref still deleted | `trunk   ([worker] base_branch; the clone does not say which branch is its default)` |
| restore the ref (`git -C demo symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/master`) with `[worker] base_branch = "trunk"` still set | `master   (detected from origin/HEAD)` — detection outranks the global key |

All four were walked by hand on 2026-09-06 and print exactly this.

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
jq -r 'select(.action == "repo.onboard")
       | "\(.entity_id) \(.detail.base_ref) \(.detail.base_ref_source)"' \
  <state_dir>/logs/audit-*.jsonl
```

(`robot-army log --limit 20` prints the same records for a human; there is no `--action`
filter, so the field query is `jq`'s.) The `detail` carries `base_ref` and `base_ref_source`,
so the log alone answers what branch was approved and what decided it — Principle III's
reconstruction standard applied to the line that used to be a guess.
