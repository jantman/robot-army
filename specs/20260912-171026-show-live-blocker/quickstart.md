# Quickstart: validating `show`'s current blocker

The unit tests cover every row of [contracts/show-output.md](contracts/show-output.md):

```bash
uv run pytest tests/unit/test_show_blocker.py tests/unit/test_operations_retry.py -q
uv run pytest            # the whole suite
```

## The incident, by hand (scenario 9 of the 005 quickstart)

Against a scratch repository that is onboarded and has a `failed` item `<id>`:

1. Move the clone away. Run `robot-army retry <id>`. It refuses with "the clone approved for …
   is no longer at …". `robot-army show <id>` shows that same sentence on its `blocked` line,
   `(checked now)`.
2. Move the clone back, run `robot-army onboard <repo> --reapprove`, and remove the clone's entry
   from `~/.claude.json`, leaving it untrusted.
3. `robot-army retry <id>` refuses with "workspace trust check failed: …".
4. `robot-army show <id>` prints the same "workspace trust check failed: …" sentence on its
   `blocked` line, suffixed `(checked now; not the reason recorded when it failed)`, and the
   `failure` line still names the missing clone.
5. Open `/item/<id>`. The `blocked` entry says the same, and `anomaly_count` is unchanged.
6. Trust the clone again. `show <id>` says nothing on this machine blocks it now, and that
   `retry` re-reads the issue.
