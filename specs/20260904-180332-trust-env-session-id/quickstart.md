# Quickstart: proving the wrapper ignores what the prompt claims

**Feature**: `specs/20260904-180332-trust-env-session-id` | **Date**: 2026-09-04

Four checks. The first is the finding, reproduced and then re-run against the fix; the rest
cover the guards and the escaping. All are runnable by hand and all are mirrored by tests.

## Prerequisites

```bash
cd /path/to/robot-army
uv sync              # once
```

The wrapper needs nothing installed — it is a bash script run directly from `share/`.

---

## 1. The attack: a prompt that names its own session id

Set up a spool directory and a sibling directory standing in for `~/.claude/sessions/`, then
run the wrapper the way the daemon does, with a final argument that begins the way a hostile
`.claude/robot-army.md` would.

```bash
WORK="$(mktemp -d)"; mkdir -p "$WORK/spool" "$WORK/logs" "$WORK/sessions"
ROBOT_ARMY_SESSION_ID=0d5b1f3e-9c2a-4f1b-8e77-6a1d2c3b4e5f \
ROBOT_ARMY_SPOOL_DIR="$WORK/spool" \
ROBOT_ARMY_LOG_DIR="$WORK/logs" \
bash share/robot-army-session-wrapper.sh 42 -- \
  /bin/sh -c 'exit 0' --session-id 0d5b1f3e-9c2a-4f1b-8e77-6a1d2c3b4e5f \
  '--session-id=../sessions/hijacked

the rest of a composed prompt'
echo "exit: $?"
ls "$WORK/spool" "$WORK/sessions"
```

**Expected after the fix**: `spool` holds
`0d5b1f3e-9c2a-4f1b-8e77-6a1d2c3b4e5f.start.json` and `...exit.json`, and `sessions` is
**empty**.

**Before the fix**, for contrast: `sessions` holds files whose names begin `hijacked`, and
the spool is empty. Run this against `git stash`-ed changes, or against `main`, to see the
finding rather than take it on trust.

---

## 2. The guards: an identifier the system would never issue

```bash
# A session id that is not a UUID
ROBOT_ARMY_SESSION_ID='../../escape' ROBOT_ARMY_SPOOL_DIR="$WORK/spool" \
ROBOT_ARMY_LOG_DIR="$WORK/logs" \
bash share/robot-army-session-wrapper.sh 42 -- /bin/sh -c 'exit 0'
echo "exit: $?"      # expect 2, and a message naming the session id

# No session id at all
env -u ROBOT_ARMY_SESSION_ID ROBOT_ARMY_SPOOL_DIR="$WORK/spool" \
ROBOT_ARMY_LOG_DIR="$WORK/logs" \
bash share/robot-army-session-wrapper.sh 42 -- /bin/sh -c 'exit 0'
echo "exit: $?"      # expect 2

# An item id that is not an integer
ROBOT_ARMY_SESSION_ID=0d5b1f3e-9c2a-4f1b-8e77-6a1d2c3b4e5f \
ROBOT_ARMY_SPOOL_DIR="$WORK/spool" ROBOT_ARMY_LOG_DIR="$WORK/logs" \
bash share/robot-army-session-wrapper.sh '../../evil' -- /bin/sh -c 'exit 0'
echo "exit: $?"      # expect 2, and no file created under that name
```

In every case: exit 2, an explanatory line on standard error, and nothing added anywhere
under `$WORK`. Check the last part, since it is the one that is easy to get wrong:

```bash
find "$WORK" -newer share/robot-army-session-wrapper.sh -type f
```

---

## 3. The escaping: a control character in the text

```bash
printf -v VT '\x0b'
ROBOT_ARMY_SESSION_ID=0d5b1f3e-9c2a-4f1b-8e77-6a1d2c3b4e5f \
ROBOT_ARMY_SPOOL_DIR="$WORK/spool" ROBOT_ARMY_LOG_DIR="$WORK/logs" \
bash share/robot-army-session-wrapper.sh 42 -- /bin/sh -c 'exit 0' "issue body${VT}text"

python3 -c '
import json,sys
p="'"$WORK"'/spool/0d5b1f3e-9c2a-4f1b-8e77-6a1d2c3b4e5f.exit.json"
d=json.loads(open(p,encoding="utf-8").read(), strict=True)
assert "issue body\x0btext" in d["argv"], d["argv"]
print("parsed strictly, and round tripped")
'
```

**Expected**: `parsed strictly, and round tripped`. Before the fix this raises
`json.decoder.JSONDecodeError: Invalid control character`.

---

## 4. Nothing normal broke

```bash
uv run pytest tests/integration/test_spool_recovery.py tests/unit/test_session_wrapper_input.py -q
uv run pytest -q
```

The first command covers the wrapper end to end: a session runs, its record is written, the
daemon's drain applies it, and the work item reaches its real final state. The second is the
gate — the full suite must pass before the feature is complete.

## Cleanup

```bash
rm -rf "$WORK"
```
