# Quickstart: seeing the numbering warning

Two ways to check this works — the test suite, which covers every row of the outcomes table, and a
real `onboard` run against a scratch clone, which is the only way to see the screen the way the
maintainer sees it.

## The suite

```bash
uv run pytest
```

The whole suite must pass. The parts belonging to this feature:

```bash
uv run pytest tests/unit/test_speckit_numbering.py -v      # one test per row of the table
uv run pytest tests/integration/test_onboard.py -v         # the screen, the JSON, the record
```

`tests/integration/test_onboard.py` is marked `requires_git` and builds real clones with real
remotes, so it needs `git` on the path.

## Seeing the screen

Onboarding needs the repository to exist on the configured source system and to pass the ownership
guard, so the fastest honest way to see the block is to point a scratch config at a scratch clone of
a repository you actually own.

```bash
# a clone at the derived location, <repo_root>/<name>
mkdir -p /tmp/qs/GIT && git clone git@github.com:jantman/<some-repo>.git /tmp/qs/GIT/<some-repo>

# make it look like a Spec Kit project that numbers by scanning
cd /tmp/qs/GIT/<some-repo>
mkdir -p .specify/templates .claude/skills/speckit-{specify,plan,tasks,implement}
touch .specify/templates/spec-template.md
for c in specify plan tasks implement; do echo "# $c" > .claude/skills/speckit-$c/SKILL.md; done
printf '{"feature_numbering": "sequential"}\n' > .specify/init-options.json
```

Then run onboarding against a config whose `[paths] repo_root` is `/tmp/qs/GIT`:

```bash
uv run robot-army --config /tmp/qs/config.toml onboard jantman/<some-repo>
```

Expect the ordinary screen — repository, clone path, verified origin, base ref, trust, committed
settings — followed by the block, and *then* the approval prompt:

```text
spec kit: this repository numbers feature directories by scanning
  feature_numbering is "sequential" in .specify/init-options.json.
  Two sessions running at once scan the same specs/ and cannot see each other's
  worktrees, so both can claim the same number. Nothing here prevents that.
  Set "feature_numbering": "timestamp" in that file to number by time instead.

Approve jantman/<some-repo> for dispatch, recording this fingerprint? [y/N]
```

Answer `n`. Nothing about the warning depends on approving.

### The three variations

```bash
# silent: the collision-free setting
printf '{"feature_numbering": "timestamp"}\n' > .specify/init-options.json

# warned, with the "not set" wording
rm .specify/init-options.json

# the unknown block
printf 'not json at all\n' > .specify/init-options.json
```

And the case that must stay silent whatever the file says:

```bash
rm -rf .specify .claude    # no Spec Kit, no block
```

## The machine-readable forms

```bash
uv run robot-army --config /tmp/qs/config.toml onboard jantman/<some-repo> --json </dev/null \
  | jq '{speckit, speckit_numbering, speckit_numbering_value}'
```

```json
{
  "speckit": true,
  "speckit_numbering": "scanned",
  "speckit_numbering_value": "sequential"
}
```

No warning sentence appears anywhere in that document.

After an approval, the same finding is in the log:

```bash
jq -r 'select(.action == "repo.onboard") | "\(.entity_id) \(.detail.speckit_numbering)"' \
  ~/.local/state/robot-army/logs/audit-*.jsonl | tail -1
```

## What to check afterwards

- The scratch clone is untouched: `git -C /tmp/qs/GIT/<some-repo> status` shows only the files you
  created by hand. Nothing in this feature writes to an onboarded repository.
- A repository with `timestamp` produces a screen byte-identical to the one it produced before this
  feature existed.
