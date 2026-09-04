# Quickstart: validating `robot-army prompt`

Runnable checks that the feature does what [spec.md](spec.md) says. Each maps to a success
criterion. Nothing here creates a session or a worktree.

## Prerequisites

- At least one onboarded repository (`robot-army repos` lists them and shows the clone path).
- A `GITHUB_TOKEN` that can read that repository's issues — the command performs one real
  issue read.
- An issue number in that repository. It does **not** need a dispatch label and does not need
  to be open.

Set these once:

```bash
REPO=owner/repo
ISSUE=42
```

## 1. It prints the prompt (SC-001)

```bash
uv run robot-army prompt "$REPO" "$ISSUE"
```

Expect the delivery block, then a separator, then a section naming the repository, the issue
number, the branch, the title, the URL, the labels, and the issue body. If the repository has
a `.claude/robot-army.md`, its contents come first, above everything else. If the repository
is a Spec Kit project and is not opted out, the Spec Kit guidance sits between the two.

## 2. stdout carries the prompt and nothing else (SC-003, FR-003)

```bash
uv run robot-army prompt "$REPO" "$ISSUE" > /tmp/prompt-a.txt
uv run robot-army prompt "$REPO" "$ISSUE" > /tmp/prompt-b.txt
diff /tmp/prompt-a.txt /tmp/prompt-b.txt && echo IDENTICAL
head -1 /tmp/prompt-a.txt
```

The two files must be identical, and the first line must be the first line of the prompt —
not a banner, not the path note. Watch the note appear on the terminal while the file
receives none of it: that is FR-004 in one observation.

## 3. It matches what a dispatch composes (SC-002)

The authoritative check is the unit test that calls `operations.prompt_preview` and
`dispatch.build_launch_plan` over the same fixture and asserts the prompt is the same string.
By hand, against a real dispatched item:

```bash
uv run robot-army show <item-id>          # note the branch and the worktree path
uv run robot-army prompt "$REPO" "$ISSUE" | head -40
```

The branch named in the prompt must equal the branch `show` reports, and the stderr note must
name that item's worktree rather than the clone.

## 4. It changes nothing (SC-004, FR-012)

```bash
DB=~/.local/state/robot-army/state.db
sqlite3 "$DB" .dump > /tmp/before.sql
uv run robot-army prompt "$REPO" "$ISSUE" > /dev/null
sqlite3 "$DB" .dump > /tmp/after.sql
diff /tmp/before.sql /tmp/after.sql && echo "database unchanged"

git -C "$(uv run robot-army repos | grep "$REPO" | awk '{print $2}')" status --porcelain
git -C … worktree list                    # no new worktree
```

The database dump must be identical, the clone must be clean, and no worktree must have
appeared. Confirm on GitHub that the issue gained no comment and no label.

## 5. Every run is in the log (SC-005)

```bash
grep '"prompt.preview"' ~/.local/state/robot-army/logs/audit-*.jsonl | tail -5
```

(`robot-army log` has no action filter; the file is one JSON object per line and `grep` is
the documented way to slice it — see `docs/logging.md`.)

One record per invocation above, including the failing ones below. Each names the repository,
the issue, the outcome, and where the contextual sections were read from. See
[contracts/audit-records.md](contracts/audit-records.md) for the fields.

## 6. Failures are distinguishable by exit code alone (SC-007)

```bash
uv run robot-army prompt not-a-slug 1        ; echo "expect 2, got $?"
uv run robot-army prompt "$REPO" 0           ; echo "expect 2, got $?"
uv run robot-army prompt owner/never-onboarded 1 ; echo "expect 3, got $?"
uv run robot-army prompt "$REPO" 99999999    ; echo "expect 1, got $?"
```

For each, confirm stdout is empty and the reason is on stderr:

```bash
uv run robot-army prompt owner/never-onboarded 1 2>/dev/null | wc -c   # expect 0
```

## 7. An issue that was never dispatched works (FR-006)

Pick an issue with no dispatch label and no work item row:

```bash
uv run robot-army prompt "$REPO" <unlabelled-issue>
```

Expect exit `0`, a prompt, a derived branch of the form
`robot-army/issue-<n>-<slug>`, and a stderr note naming the **clone** as the context source.

## 8. The full suite passes

```bash
uv run pytest
uv run ruff check
```

Required before the feature is complete (Development Workflow).
