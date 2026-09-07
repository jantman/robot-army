# Quickstart: verifying the delivery block follows the effect level

Everything below runs on this machine, from the repository root, with `uv`. Nothing here needs
a dispatch to be watched to completion, and nothing needs GitHub to be written to.

## 1. The suite

```bash
uv run pytest
```

The behaviour of this feature is prose, so the suite is where most of it is checked:
`tests/unit/test_delivery_prompt.py` holds both forms to
[contracts/delivery-forms.md](contracts/delivery-forms.md), `tests/unit/test_effects.py` holds
the selection table, and `tests/unit/test_prompt_preview.py` holds the preview to the level it
is run at.

## 2. The two forms, side by side

```bash
uv run robot-army prompt jantman/robot-army 32 --effect-level live > /tmp/live.txt
uv run robot-army prompt jantman/robot-army 32 --effect-level no-remote > /tmp/contained.txt
diff /tmp/live.txt /tmp/contained.txt
```

**Expected**: the delivery section differs and nothing else does but the fence nonce, which is
random per compose by design. The `live` copy says to push the branch and open a pull request;
the `no-remote` copy says to commit and stop, names the pull request and the issue comment as
things not to do, and carries the sentence about instructions above.

Run it at `plan` and at `local` too: both produce the same delivery section as `no-remote`.

## 3. The `live` prompt did not change

```bash
uv run pytest tests/unit/test_speckit_prompt.py -q
```

**Expected**: passing, and — the part that matters — `GOLDEN` in that file is *unedited by this
feature*. It is the whole `live` assembly written out as a literal, so a change to what a live
dispatch reads shows up there as a diff. `test_below_live_only_the_delivery_section_changes`
holds the other half: substituting the `local` form into `GOLDEN` is exactly what composing with
it produces, so nothing outside the delivery section moved (SC-002, SC-003).

## 4. The Spec Kit contradiction, which is the case that matters here

This repository configures `[speckit] implement` with an instruction that says to push. Preview
a Spec Kit issue at `no-remote`:

```bash
uv run robot-army prompt jantman/robot-army 32 --effect-level no-remote | sed -n '1,80p'
```

(The issue must be in an onboarded repository; any issue number in one works, dispatched or
not, because the preview creates nothing.)

**Expected**: the Spec Kit block still carries the operator's configured instruction, unchanged —
and the delivery section below it says that an instruction above asking for a push describes a
`live` dispatch and does not apply to this run. If that sentence is missing, a `no-remote`
dispatch of a Spec Kit issue in this repository will still push (research.md R8).

## 5. The record says which form

```bash
uv run robot-army prompt jantman/robot-army 32 --effect-level no-remote > /dev/null
uv run robot-army log --limit 5 --json | grep -A2 prompt.preview
```

**Expected**: the `prompt.preview` record carries `"delivery": "local"`, beside the
`instructions` and `speckit` flags it already carried. Run the same preview at `live` and it
reads `"push"`. No record carries any part of the prompt itself.

Then start the daemon at a reduced level and read its startup record:

```bash
uv run robot-army run --effect-level no-remote --once
uv run robot-army log --limit 20 | grep daemon.start
```

**Expected**: the `boundaries` map carries `"delivery": "local"` alongside the wired
implementations, so the startup record alone answers what every session that daemon dispatches
will be told.

## 6. The end-to-end check, which is the one the issue asked for

With a labelled issue available, run at `no-remote` and let the session work:

```bash
uv run robot-army run --effect-level no-remote
# …after the session has finished…
git ls-remote --heads origin 'refs/heads/robot-army/*'
```

**Expected**: no new remote branch, and no new pull request. Before this feature, the same run
left one of each.

**And the honest caveat, which is a deliverable rather than a footnote**: nothing enforces this.
The session runs as the same user with the same credentials, and a session that pushes anyway
is not prevented, only asked. That is what `contracts/boundaries.md`, quickstart scenario 3 and
[the setup guide](../../docs/guide/1-setup.md) now say out loud.

## 7. The generated configuration still loads clean

```bash
uv run robot-army example-config --output share/config.example.toml --force
git diff --exit-code share/config.example.toml
```

**Expected**: no diff, because the file was regenerated when the `effect_level` comment changed.
`tests/unit/test_example_config_drift.py` fails naming this command if it was not.
