"""``status`` may not deny the rows it has just printed (milestone 008).

Issue #13: at ``effect_level = "plan"`` the command printed a four-row queue and then
"no work items yet" and "no matching work items" in the same output. Both halves were
individually correct — the queue includes simulated rows because they occupy capacity, the
counts and the listing exclude them because FR-056 makes that the default — and nothing
reconciled them at the point of rendering.

The invariant is asserted directly rather than by proxy: no single invocation may both
display work item rows and claim there are none. Everything else here is a consequence.
"""

from __future__ import annotations

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import operations
from robot_army.states import WorkItemState

FLAG = "--include-simulated"


@pytest.fixture
def ctx(config, conn, monkeypatch):
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log: make_boundaries(log, level=level)
    )
    built = operations.build_context(config)
    yield built
    built.close()


def render(ctx, idle_machine, **kwargs):
    """``status`` against a machine with nothing running, so the queue is deterministic."""
    registry, proc = idle_machine
    result = operations.status(ctx, registry_dir=registry, proc_root=proc, **kwargs)
    return result, "\n".join(result.lines)


def ready(conn, issue_number, *, dry_run, repo_key="demo"):
    item = seed_item(
        conn,
        repo_key=repo_key,
        issue_number=issue_number,
        dry_run=dry_run,
        state=str(WorkItemState.READY),
    )
    return item


# -- the invariant ----------------------------------------------------------


def test_a_populated_queue_is_never_printed_beside_a_claim_of_emptiness(ctx, conn, idle_machine):
    """The reported bug, asserted as the property rather than as the string."""
    for issue in (26, 27, 28, 30):
        ready(conn, issue, dry_run=True)

    _, text = render(ctx, idle_machine)

    assert "queue (4 eligible)" in text
    assert "no work items yet" not in text
    assert "no matching work items\n" not in text + "\n"
    assert text.count(f"4 simulated rows withheld — pass {FLAG} to show them") == 2


@pytest.mark.parametrize("simulated,real", [(4, 0), (4, 2), (0, 2), (0, 0)])
@pytest.mark.parametrize("include_simulated", [False, True])
def test_no_invocation_shows_rows_and_denies_them(
    ctx, conn, idle_machine, simulated, real, include_simulated
):
    issue = 1
    for _ in range(simulated):
        ready(conn, issue, dry_run=True)
        issue += 1
    for _ in range(real):
        ready(conn, issue, dry_run=False)
        issue += 1

    result, text = render(ctx, idle_machine, include_simulated=include_simulated)

    shows_rows = "eligible) — in dispatch order" in text or "counts by state:" in text
    denies_rows = "no work items yet" in text
    assert not (shows_rows and denies_rows)
    assert result.code == operations.EXIT_OK


# -- what the disclosure says -----------------------------------------------


def test_the_stated_number_is_the_number_the_flag_reveals(ctx, conn, idle_machine):
    for issue in (26, 27, 28, 30):
        ready(conn, issue, dry_run=True)
    ready(conn, 31, dry_run=False)

    hidden, _ = render(ctx, idle_machine)
    shown, _ = render(ctx, idle_machine, include_simulated=True)

    revealed = len(shown.data["items"]) - len(hidden.data["items"])
    assert revealed == 4
    assert f"4 simulated rows withheld — pass {FLAG} to show them" in "\n".join(hidden.lines)


def test_the_disclosure_is_not_limited_to_an_empty_listing(ctx, conn, idle_machine):
    """FR-003. Two visible rows beneath a six-row queue is the same defect, only quieter."""
    for issue in (26, 27, 28, 30):
        ready(conn, issue, dry_run=True)
    ready(conn, 31, dry_run=False)
    ready(conn, 32, dry_run=False)

    _, text = render(ctx, idle_machine)

    assert "queue (6 eligible)" in text
    assert "counts by state:" in text
    assert f"4 simulated rows withheld — pass {FLAG} to show them" in text


def test_nothing_is_said_when_nothing_was_withheld(ctx, conn, idle_machine):
    for issue in (31, 32):
        ready(conn, issue, dry_run=False)

    _, text = render(ctx, idle_machine)

    assert "withheld" not in text
    assert "0 simulated" not in text


def test_asking_for_simulated_rows_withholds_nothing(ctx, conn, idle_machine):
    for issue in (26, 27, 28, 30):
        ready(conn, issue, dry_run=True)

    result, text = render(ctx, idle_machine, include_simulated=True)

    assert "withheld" not in text
    assert result.data["counts"] == {"ready": 4}
    assert len(result.data["items"]) == 4


def test_an_empty_database_still_says_so_plainly(ctx, conn, idle_machine):
    _, text = render(ctx, idle_machine)

    assert "no work items yet" in text
    assert "no matching work items" in text
    assert "withheld" not in text


def test_the_yet_is_dropped_only_when_rows_are_being_withheld(ctx, conn, idle_machine):
    ready(conn, 26, dry_run=True)

    _, text = render(ctx, idle_machine)

    assert "no work items yet" not in text
    assert f"no work items (1 simulated rows withheld — pass {FLAG} to show them)" in text


# -- filters ----------------------------------------------------------------


def test_the_listing_count_honours_the_repo_filter(ctx, conn, idle_machine):
    for issue in (26, 27):
        ready(conn, issue, dry_run=True)
    ready(conn, 31, dry_run=False)

    _, text = render(ctx, idle_machine, repo="demo")
    assert "2 simulated rows withheld" in text


def test_a_filter_matching_no_simulated_row_discloses_nothing(ctx, conn, idle_machine):
    """FR-004. Reporting a number the flag would not reveal is a new contradiction."""
    for issue in (26, 27):
        ready(conn, issue, dry_run=True, repo_key="other/repo")
    ready(conn, 31, dry_run=False)

    _, text = render(ctx, idle_machine, repo="demo")

    # The counts section is unfiltered and has never honoured --repo, so it still
    # discloses the two rows it withheld — indented, beneath the counts. The listing is
    # filtered, matched neither simulated row, and must therefore say nothing at all.
    note = f"2 simulated rows withheld — pass {FLAG} to show them"
    lines = text.splitlines()
    assert f"  {note}" in lines
    assert note not in lines
    assert "no matching work items" not in text
    assert text.count("simulated rows withheld") == 1


def test_the_listing_count_honours_the_state_filter(ctx, conn, idle_machine):
    ready(conn, 26, dry_run=True)
    seed_item(conn, issue_number=27, dry_run=True, state=str(WorkItemState.DONE))

    _, text = render(ctx, idle_machine, state=str(WorkItemState.DONE))

    assert "no matching work items (1 simulated rows withheld" in text
    assert "no work items (2 simulated rows withheld" in text


# -- the queue's own marking ------------------------------------------------


def test_simulated_queue_rows_are_marked(ctx, conn, idle_machine):
    """FR-057 applied to the first table, which never used to honour it."""
    ready(conn, 26, dry_run=True)
    ready(conn, 31, dry_run=False)

    _, text = render(ctx, idle_machine)
    queue_block = text.split("queue (2 eligible)")[1].split("\n\n")[0]

    marked = [line for line in queue_block.splitlines() if "#26" in line]
    unmarked = [line for line in queue_block.splitlines() if "#31" in line]
    assert marked and marked[0].split()[1].endswith("*")
    assert unmarked and not unmarked[0].split()[1].endswith("*")
    assert "* = simulated (dry-run) row" in text


def test_a_queue_of_real_rows_carries_no_footnote(ctx, conn, idle_machine):
    ready(conn, 31, dry_run=False)

    _, text = render(ctx, idle_machine)

    assert "queue (1 eligible)" in text
    assert "* = simulated (dry-run) row" not in text


# -- the machine-readable view ----------------------------------------------


def test_the_payload_reports_what_the_text_reported(ctx, conn, idle_machine):
    for issue in (26, 27, 28, 30):
        ready(conn, issue, dry_run=True)

    result, text = render(ctx, idle_machine)

    assert result.data["withheld_simulated"] == {"counts": 4, "items": 4}
    assert result.data["counts"] == {}
    assert result.data["items"] == []
    # The queue array holds the rows the counts and the listing do not, which is exactly
    # the split a consumer could previously read as "zero work items exist".
    assert len(result.data["queue"]) == 4
    assert all(entry["dry_run"] for entry in result.data["queue"])
    assert "4 simulated rows withheld" in text


def test_the_payload_key_is_present_when_nothing_was_withheld(ctx, conn, idle_machine):
    for issue in (26, 27):
        ready(conn, issue, dry_run=True)

    result, _ = render(ctx, idle_machine, include_simulated=True)
    assert result.data["withheld_simulated"] == {"counts": 0, "items": 0}

    empty, _ = render(ctx, idle_machine, include_simulated=False, repo="nobody/nothing")
    assert empty.data["withheld_simulated"]["items"] == 0


def test_the_payload_scopes_the_two_numbers_separately(ctx, conn, idle_machine):
    """The counts query has never honoured --repo; the listing always has."""
    for issue in (26, 27):
        ready(conn, issue, dry_run=True, repo_key="other/repo")
    ready(conn, 31, dry_run=False)

    result, _ = render(ctx, idle_machine, repo="demo")

    assert result.data["withheld_simulated"] == {"counts": 2, "items": 0}


def test_no_existing_payload_key_was_renamed_or_removed(ctx, conn, idle_machine):
    ready(conn, 31, dry_run=False)

    result, _ = render(ctx, idle_machine)

    for key in (
        "effect_level",
        "health",
        "counts",
        "items",
        "anomalies",
        "include_simulated",
        "dispatch_paused",
        "dispatch_paused_at",
        "dispatch_paused_by",
        "capacity",
        "queue",
    ):
        assert key in result.data, f"{key} disappeared from the status payload"
