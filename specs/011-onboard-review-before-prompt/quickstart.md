# Quickstart: Verifying "read before you approve"

**Feature**: 011-onboard-review-before-prompt | **Date**: 2026-08-29

Six checks. The first is the issue and takes ten seconds. The rest cover the exits that the
first one makes reachable. Full behaviour in
[`contracts/onboard-output.md`](contracts/onboard-output.md).

## Prerequisites

- The suite passes: `uv run pytest`
- A configured `robot-army` with at least one owned, cloned, not-yet-onboarded repository.
  `robot-army repos` shows what is already onboarded.

Pick a repository key for the checks below; `jantman/some-repo` stands in for it.

## 1. The screen arrives before the question

```bash
robot-army onboard jantman/some-repo
```

**Expect**: the repository line, the clone path with `(derived from [paths] repo_root)` or
`(configured in [repos."…"])`, the verified origin and the remote it came from, the base ref,
the trust verdict, and the committed settings in full — **all on screen** — and only then
`Approve jantman/some-repo for dispatch, recording this fingerprint? [y/N]` waiting for input.

**Before this feature**: only the prompt, with everything above it appearing after the answer.

Decline it (`n`) for now, so the later checks still have an un-onboarded repository.

## 2. It is really written, not just buffered

```bash
robot-army onboard jantman/some-repo > /tmp/onboard.out
# while it waits at the prompt, in another shell:
cat /tmp/onboard.out
```

**Expect**: the whole screen is in the file while the command is still blocked. This is the
check that distinguishes a flush from a lucky terminal.

Answer `n`.

## 3. Once, on every way out

```bash
for answer in y n; do
  echo "$answer" | robot-army onboard jantman/some-repo > /tmp/o.$answer 2>&1
  echo "$answer -> exit $?  |  'clone path' lines: $(grep -c 'clone path' /tmp/o.$answer)"
done
```

**Expect**: `clone path` lines: `1` in both files. `n` exits 4; `y` exits 0. Then re-run with
no argument change:

```bash
robot-army onboard jantman/some-repo; echo "exit $?"
```

**Expect**: the screen once, `already onboarded and the fingerprint is unchanged; nothing to
do`, exit 0, and no prompt.

## 4. Interrupting leaves a record

```bash
robot-army onboard jantman/other-repo     # press Ctrl-C at the prompt
echo "exit $?"
robot-army log --since 2m | grep repo.onboard
```

**Expect**: exit `1` and `interrupted` on stderr, exactly as before — and now an audit record
naming the repository with cause `interrupted_at_prompt`. Before this feature the log held
nothing at all.

Same shape with input that simply ends:

```bash
robot-army onboard jantman/other-repo < /dev/null; echo "exit $?"
```

**Expect**: exit `4`, a message, and a record with cause `no_answer_available` — not a
traceback.

## 5. Machine-readable stays machine-readable

```bash
echo n | robot-army onboard jantman/other-repo --json 2>/dev/null | python -m json.tool
```

**Expect**: it parses. The prompt went to stderr and was discarded; the document is alone on
stdout. Repeat with `y` and with an already-onboarded repository — every path parses, including
the ones that exit non-zero.

## 6. The other prompts are untouched

```bash
robot-army purge-simulated        # answer n
robot-army cancel <item-id>       # answer n, if an item is running
```

**Expect**: identical wording, identical stdout prompt, identical exit codes to before. This
feature changed one command.

## Test suite

```bash
uv run pytest tests/integration/test_onboard.py tests/unit/test_cli_exit_codes.py -q
uv run pytest -q
```

The ordering tests pass a real stream and snapshot it from inside the injected prompt — the
assertion is "the screen was already there when I was asked", which is the only form that would
have caught the original defect. The four existing composition tests still assert on
`result.lines` with no stream attached; they answer whether the screen is *right*, which is a
different question and still worth asking.
