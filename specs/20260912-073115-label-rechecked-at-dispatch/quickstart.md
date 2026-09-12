# Quickstart: Validating the Label Re-check

```bash
uv sync
uv run pytest tests/unit/test_label_hold.py tests/unit/test_poll.py \
  tests/integration/test_daemon_loop.py tests/integration/test_dispatch_capacity.py
uv run pytest      # the whole suite; must pass
```

## Scenarios the tests exercise

1. **De-scoped items do not dispatch** (spec US1):
   - Setup: seed `ready` items with `["robot-army"]` and configure `[github] label = "scratch"`.
   - Expect: `ordering.plan` holds each one with `not_labelled`. `select_and_dispatch` with free
     capacity dispatches nothing and records `dispatch.at_capacity` with `reason: not_labelled`.
2. **Precedence** (US1 AS3):
   - Setup: the same items on a machine at its cap.
   - Expect: the reason is `not_labelled`, not `global_cap`. Paused or held still wins.
3. **Skip, not stop** (US1 AS5):
   - Setup: a de-scoped item ahead of one carrying `scratch`.
   - Expect: the second dispatches in the same pass.
4. **Release by labelling** (US2):
   - Setup: a poll listing contains the held item's issue carrying `scratch`.
   - Expect: `poll.labels_refreshed` is recorded and the next plan has no hold. The same poll
     with identical labels writes and records nothing.
5. **Startup warning** (US3):
   - Setup: start the daemon with de-scoped `ready` items.
   - Expect: exactly one `daemon.label_warning`. With none de-scoped, no such record.

## By hand (optional)

On a scratch installation, change `[github] label`, restart, and run `robot-army status`. Every
previously queued item shows `not_labelled` with the label named, and `robot-army log` shows
the `daemon.label_warning` line.
