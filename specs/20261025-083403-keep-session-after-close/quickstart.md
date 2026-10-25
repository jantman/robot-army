# Quickstart validation

## Automated

```bash
uv run pytest            # the whole suite; must be green
```

The feature-specific tests are listed in [plan.md](./plan.md#tests).

## Live, on the real machine (privatepuppet or a scratch repository)

**Prerequisites**: the daemon is running at a real effect level, and an onboarded repository
has an item whose session has opened a pull request.

1. **Keep it before merging.** Run `uv run robot-army keep <id>`. Expect the "kept at" line,
   and confirm that `keeps` lists the item with `live <session-id>`.
2. **Merge the PR.** Within one pass, `show <id>` reports `done`.
3. **Verify it survived.** Across at least three passes, check the following:
   - `uv run robot-army log --item <id>` shows no `session.retire`;
   - the kitty tab is still open and attachable (`attach <id>`);
   - `worktree list` shows the checkout present;
   - `anomalies` has no `orphan_session` for the session;
   - `status` shows `(1 kept)` in the breakdown;
   - the web `/active` page lists the item in its "kept" section.
4. **Use it.** Type into the session. With `[cleanup] on_issue_close = true`, confirm that no
   `cleanup.considered` record is written for the item while it is kept.
5. **Release it.** Run `uv run robot-army unkeep <id>`, or press release on `/active`. On the
   first pass that finds the worker idle, expect a `session.retire` with
   `signal: merged_pull_request`, then the tab closing, then cleanup when it is enabled. The
   preceding `unkeep.item` record lists the session id in `open_sessions`.

## Exit path (US1 scenario 3)

Repeat steps 1–3, then exit the session yourself instead of releasing it. Expect the tab to
close on the next pass, and `keeps` to show the item with `no live session` until you run
`unkeep`.

## Refusal (US3 scenario 2)

Run `uv run robot-army keep <id>` on a `done` item whose session was already retired. Expect
exit `EXIT_PRECONDITION`, the "nothing to keep" message and the `claude --resume <session-id>`
line.

## Clarification 3a

Pause dispatch and hold the item's repository, without placing a keep. Merge an item's PR.
Expect its session to be retired as it is today.
