# Quickstart: validating the `github.get_repo` record

The unit and integration tests cover every row of
[contracts/github-get-repo-record.md](contracts/github-get-repo-record.md):

```bash
uv run pytest tests/unit/test_github_boundary.py tests/integration/test_onboard.py -q
uv run pytest            # the whole suite
```

## By hand (scenario 9 of the 005 quickstart, now as written)

```bash
robot-army onboard someoneelse/theirs      # refused: not owned, not listed
robot-army onboard jantman/typoed-nmae     # refused: no such repository
robot-army log --since 10m | grep -c 'github.*"/repos/'
```

**Expected**: `2`, one lookup per attempt, and each line's `path` is `/repos/<the key>`. A path
of `/user/repos` or any `page` parameter would mean onboarding enumerates, and SC-009 fails.

```bash
robot-army log --since 10m | grep github.get_repo
```

**Expected**: the typo's line reads `[ok]` with `"status": 404, "exists": false`. The
`repo.onboard` line after it is the refusal. With a deliberately bad token, the lookup's line is
`[error]` with `"status": 401`, and `onboard` refuses with `source_unreachable`.
