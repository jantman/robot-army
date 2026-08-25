"""The capacity snapshot: what it counts, what it refuses to count, and what it will not
carry (T012, T013, T023).

Three properties are being defended here, and they are not equally important. The first
is that the count is never *low*: an under-count offers a free slot that does not exist,
which oversubscribes the very subscription the cap exists to protect. The second is that
an observation which failed is distinguishable from one that observed an idle machine.
The third is structural — the snapshot carries no handle to a session the system did not
start, so FR-006 is kept by the type rather than by remembering.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from tests.conftest import seed_item, seed_session, write_proc, write_registry

from robot_army import capacity, db
from robot_army.states import SessionState

WORKTREES = "worktrees"


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    """An empty-but-present registry: the genuinely idle machine."""
    directory = tmp_path / "registry"
    directory.mkdir()
    return directory


@pytest.fixture
def proc(tmp_path: Path) -> Path:
    """A /proc holding one non-worker process, so enumeration demonstrably works."""
    root = tmp_path / "proc"
    write_proc(root, 1, starttime="1", exe="/usr/lib/systemd/systemd")
    return root


def take(conn, config, registry: Path, proc: Path, audit=None):
    return capacity.snapshot(
        conn, config=config, audit=audit, registry_dir=registry, proc_root=proc
    )


def add_live_session(
    registry: Path, proc: Path, *, pid: int, session_id: str, cwd: str
) -> None:
    """A registry entry whose process really is alive, which is the only kind that counts."""
    write_registry(registry, pid=pid, session_id=session_id, proc_start=str(pid), cwd=cwd)
    write_proc(proc, pid, starttime=str(pid), cwd=cwd)


# -- the ordinary counts ----------------------------------------------------


def test_an_idle_machine_is_observable_and_empty(conn, config, registry, proc):
    snap = take(conn, config, registry, proc)
    assert snap.observable is True
    assert snap.degraded is False
    assert snap.total == 0
    assert snap.ours == ()
    assert snap.others == 0
    assert snap.global_cap == config.daemon.max_concurrent_sessions


def test_a_session_of_ours_and_one_of_the_authors_are_counted_and_told_apart(
    conn, config, registry, proc, tmp_path
):
    ours = tmp_path / WORKTREES / "demo-42"
    ours.mkdir(parents=True)
    add_live_session(registry, proc, pid=101, session_id="s-ours", cwd=str(ours))
    add_live_session(registry, proc, pid=102, session_id="s-theirs", cwd=str(tmp_path / "GIT"))

    snap = take(conn, config, registry, proc)
    assert snap.total == 2
    assert snap.ours == ("s-ours",)
    assert snap.others == 1


def test_a_recycled_pid_with_a_mismatched_proc_start_does_not_count(
    conn, config, registry, proc, tmp_path
):
    """PID alone is never identity (FR-038). A registry entry naming a pid the kernel has
    since handed to something unrelated is not a live session, and counting it would hold
    dispatch on the strength of a process that no longer exists."""
    write_registry(registry, pid=303, session_id="s-stale", proc_start="777", cwd=str(tmp_path))
    write_proc(proc, 303, starttime="999")  # same pid, different process

    snap = take(conn, config, registry, proc)
    assert snap.total == 0
    assert snap.ours == ()


# -- the launch window (R3) -------------------------------------------------


def test_a_starting_row_with_no_registry_file_yet_still_occupies_a_slot(
    conn, config, registry, proc
):
    """The sharpest correctness trap in the milestone.

    Between the host returning and the worker writing its registry file, a dispatch in
    flight is invisible to the registry. A registry-only count would offer the same free
    slot to a second dispatch in the same tick, and FR-009's guarantee — that a batch
    cannot collectively exceed the cap — would fail in exactly the case it exists for.
    """
    item = seed_item(conn, issue_number=1)
    seed_session(conn, item, state=str(SessionState.STARTING), session_id="s-inflight")

    snap = take(conn, config, registry, proc)
    assert snap.total == 1, "a dispatch in flight must not read as free capacity"


def test_the_count_is_a_union_not_a_sum(conn, config, registry, proc, tmp_path):
    """A row that *does* have a registry entry is counted once, not twice.

    ``max(registry, database)`` is wrong whenever the author has sessions running and a
    dispatch is in flight — the busy case — and a sum is wrong the rest of the time.
    """
    worktree = tmp_path / WORKTREES / "demo-1"
    worktree.mkdir(parents=True)
    item = seed_item(conn, issue_number=1)
    seed_session(conn, item, state=str(SessionState.RUNNING), session_id="s-known")
    add_live_session(registry, proc, pid=201, session_id="s-known", cwd=str(worktree))

    item2 = seed_item(conn, issue_number=2)
    seed_session(conn, item2, state=str(SessionState.STARTING), session_id="s-inflight")

    snap = take(conn, config, registry, proc)
    assert snap.total == 2, "one matched entry plus one unmatched row"
    assert snap.ours == ("s-known",)


def test_an_ended_session_row_occupies_nothing(conn, config, registry, proc):
    item = seed_item(conn, issue_number=1)
    seed_session(conn, item, state=str(SessionState.EXITED_CLEAN), session_id="s-gone")
    assert take(conn, config, registry, proc).total == 0


# -- simulated sessions (FR-004, and FR-055 before it) ----------------------


def test_a_simulated_session_counts_toward_both_caps(conn, config, registry, proc):
    """They burn the same subscription quota, so pretending they are free would make a
    dry run misleading about the one thing it exists to rehearse."""
    item = seed_item(conn, issue_number=1, dry_run=True)
    seed_session(conn, item, state=str(SessionState.RUNNING), session_id="s-sim", dry_run=True)

    snap = take(conn, config, registry, proc)
    assert snap.total == 1
    assert snap.per_repo == {"demo": 1}


# -- per-repository counting ------------------------------------------------


def test_per_repo_groups_our_sessions_by_repository(conn, config, registry, proc):
    first = seed_item(conn, repo_key="demo", issue_number=1)
    second = seed_item(conn, repo_key="demo", issue_number=2)
    other = seed_item(conn, repo_key="other", issue_number=3)
    for item in (first, second, other):
        seed_session(conn, item, state=str(SessionState.RUNNING))

    snap = take(conn, config, registry, proc)
    assert snap.per_repo == {"demo": 2, "other": 1}


def test_an_out_of_band_session_is_attributed_to_no_repository(
    conn, config, registry, proc, tmp_path
):
    """Not an omission. The author works in their own clone, which is not under the
    worktree root, so there is no repository key to attribute it to — while the global
    count, which is what accounts for everything running, still sees it."""
    add_live_session(registry, proc, pid=404, session_id="s-theirs", cwd=str(tmp_path / "GIT"))
    snap = take(conn, config, registry, proc)
    assert snap.total == 1
    assert snap.others == 1
    assert snap.per_repo == {}


# -- the degraded path ------------------------------------------------------


def test_a_missing_registry_directory_falls_back_to_proc(conn, config, tmp_path):
    """The failure that otherwise reads as an idle machine (R4). /proc still answers, so
    the observation degrades rather than failing — and says so."""
    proc = tmp_path / "proc"
    write_proc(proc, 1, starttime="1", exe="/usr/lib/systemd/systemd")
    write_proc(proc, 55, starttime="55", exe="/usr/bin/claude", cwd=str(tmp_path / "GIT"))

    snap = capacity.snapshot(
        conn, config=config, registry_dir=tmp_path / "absent", proc_root=proc
    )
    assert snap.observable is True
    assert snap.degraded is True
    assert snap.total == 1


def test_the_degraded_path_cannot_name_our_sessions_so_it_over_counts_upward(
    conn, config, tmp_path
):
    """Session ids only exist in the registry. Without them our own row cannot be matched
    to the process it started, so it is counted twice — which is the safe direction, and
    ``degraded`` is what tells every surface the number is a ceiling rather than a fact."""
    proc = tmp_path / "proc"
    write_proc(proc, 1, starttime="1", exe="/usr/lib/systemd/systemd")
    write_proc(proc, 55, starttime="55", exe="/usr/bin/claude", cwd=str(tmp_path / "wt"))
    item = seed_item(conn, issue_number=1)
    seed_session(conn, item, state=str(SessionState.RUNNING), session_id="s-1")

    snap = capacity.snapshot(
        conn, config=config, registry_dir=tmp_path / "absent", proc_root=proc
    )
    assert snap.degraded is True
    assert snap.ours == ()
    assert snap.total == 2, "one process plus one unmatchable row — never fewer than truth"


def test_an_unrecognised_registry_version_degrades_rather_than_reading_as_empty(
    conn, config, tmp_path
):
    registry = tmp_path / "registry"
    write_registry(registry, pid=77, session_id="s-x", version="9.9.9")
    proc = tmp_path / "proc"
    write_proc(proc, 1, starttime="1", exe="/usr/lib/systemd/systemd")
    write_proc(proc, 77, starttime="77", exe="/usr/bin/claude", cwd=str(tmp_path))

    snap = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    assert snap.degraded is True
    assert snap.total == 1


# -- the unobservable path --------------------------------------------------


def test_a_registry_that_cannot_be_read_and_a_proc_that_cannot_be_enumerated_withholds(
    conn, config, audit, tmp_path
):
    """Every unresolved doubt resolves to "hold" (R4, FR-007). A visible stall is a better
    failure than an invisible over-dispatch."""
    snap = capacity.snapshot(
        conn,
        config=config,
        audit=audit,
        registry_dir=tmp_path / "absent",
        proc_root=tmp_path / "no-proc-here",
    )
    assert snap.observable is False
    assert snap.reason
    assert snap.total == 0


def test_the_unobservable_path_raises_a_deduplicated_anomaly(conn, config, audit, tmp_path):
    """Visible in ``robot-army anomalies`` rather than only to whoever is watching the log —
    and the partial unique index means a 5-second tick cannot turn it into a flood."""
    for _ in range(3):
        capacity.snapshot(
            conn,
            config=config,
            audit=audit,
            registry_dir=tmp_path / "absent",
            proc_root=tmp_path / "no-proc-here",
        )
    raised = [a for a in db.list_anomalies(conn) if a.kind == capacity.UNOBSERVABLE]
    assert len(raised) == 1


def test_the_unobservable_path_writes_an_audit_record_naming_the_consequence(
    conn, config, audit, tmp_path, layout
):
    import json

    capacity.snapshot(
        conn,
        config=config,
        audit=audit,
        registry_dir=tmp_path / "absent",
        proc_root=tmp_path / "no-proc-here",
    )
    audit.close()
    records = [
        json.loads(line)
        for path in sorted(layout.log_dir.glob("audit-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    unobservable = [r for r in records if r["action"] == "capacity.unobservable"]
    assert unobservable, records
    assert "dispatch withheld" in unobservable[0]["detail"]["consequence"]


def test_an_idle_machine_with_no_registry_is_still_dispatchable(conn, config, tmp_path):
    """The fresh install. Claude has never run here, so there is no registry directory and
    no worker process — and treating that as an enumeration failure would leave the daemon
    unable to dispatch its first session, forever, for the crime of not having run one."""
    proc = tmp_path / "proc"
    write_proc(proc, 1, starttime="1", exe="/usr/lib/systemd/systemd")

    snap = capacity.snapshot(
        conn, config=config, registry_dir=tmp_path / "absent", proc_root=proc
    )
    assert snap.observable is True
    assert snap.total == 0


# -- FR-006, checked structurally (T013) ------------------------------------


def test_the_snapshot_carries_no_handle_to_a_session_we_did_not_start():
    """FR-006 forbids terminating, signalling, resuming, or attaching to a session the
    system did not start. A rule like that is kept by making the handle *unavailable*, not
    by remembering not to use it — so the guarantee is checked here against the type,
    once, rather than by auditing every call site forever."""
    names = {f.name: f for f in dataclasses.fields(capacity.CapacitySnapshot)}

    assert "others" in names
    assert names["others"].type in ("int", int), (
        "others must be a bare count; anything richer is a handle by another name"
    )

    forbidden = {"pid", "pids", "entries", "processes", "handles", "others_pids", "other_sessions"}
    assert forbidden.isdisjoint(names), (
        f"CapacitySnapshot must expose no process handle: {sorted(forbidden & set(names))}"
    )

    # ``ours`` is the deliberate exception, and it is only session ids — of sessions this
    # system started, which it is entitled to control.
    assert names["ours"].type in ("tuple[str, ...]",)


def test_no_field_of_a_populated_snapshot_leaks_an_out_of_band_identity(
    conn, config, registry, proc, tmp_path
):
    """The value-level companion to the type-level check: with an out-of-band session
    running, nothing anywhere in the snapshot names it."""
    add_live_session(registry, proc, pid=909, session_id="s-theirs", cwd=str(tmp_path / "GIT"))
    snap = take(conn, config, registry, proc)

    rendered = repr(dataclasses.asdict(snap))
    assert "s-theirs" not in rendered
    assert "909" not in rendered
    assert snap.others == 1


def test_no_code_path_signals_a_pid_it_did_not_read_from_the_sessions_table():
    """The call-site companion to the structural check (T023, FR-006).

    ``capacity`` is the only module that observes processes the system did not start, and
    it is not permitted to act on them. Anything that signals, terminates, or attaches
    must source its pid from the ``sessions`` table, which by construction holds only our
    own — so the check is that this module reaches for none of those verbs at all.
    """
    source = Path(capacity.__file__).read_text(encoding="utf-8")
    for forbidden in ("os.kill", "signal.", "SIGTERM", "SIGKILL", "terminate(", "attach("):
        assert forbidden not in source, (
            f"capacity.py must not reference {forbidden!r}: it is the one module holding "
            "out-of-band observations, and it may notice them but never touch them"
        )
