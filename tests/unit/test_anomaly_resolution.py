"""An ``orphan_session`` whose process is gone resolves itself (issue #138).

Nothing ever took a row off ``robot-army anomalies`` but a maintainer typing
``--acknowledge``. The report that prompted this feature listed three orphans, one of which
named pid 498936 — a process that had not existed for hours. The cost is not the row. It is
that the command is read as *a list of things needing attention*, so a list that is mostly
stale teaches the habit of clearing it without reading it, which is how the anomaly that
mattered gets acknowledged along with the noise.

Two things here are worth more attention than the happy path.

**The index.** The partial unique index is what stops a 60-second loop writing 1,440 rows a
day for one orphan, and a resolved row left inside it would silently block that condition
from ever being reported again. Getting that wrong produces no error and no failing
assertion anywhere else — the symptom is an anomaly that never appears, months later.

**The scope.** One kind. Widening this to every anomaly whose condition "looks passed" is
the speculative generality Principle I forbids, and each other kind has its own settling
story that this mechanism has no business guessing at.

Contract: ``specs/20260905-121903-retire-finished-sessions/contracts/anomaly-resolution.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import write_proc

from robot_army import db, reconcile

PID = 498936
START = "187024898"


@pytest.fixture
def proc(tmp_path: Path) -> Path:
    root = tmp_path / "proc"
    write_proc(root, 1, starttime="1", exe="/usr/lib/systemd/systemd")
    return root


def raise_orphan(conn, *, pid: int | None = PID, proc_start: str | None = START,
                 entity_id: str = "sess-1", kind: str = "orphan_session") -> int:
    detail: dict = {"cwd": "/home/jantman/worktrees/robot-army/issue-136"}
    if pid is not None:
        detail["pid"] = pid
    if proc_start is not None:
        detail["proc_start"] = proc_start
    with db.transaction(conn):
        db.raise_anomaly(
            conn, kind=kind, entity_type="session", entity_id=entity_id, detail=detail
        )
    return next(a.id for a in db.list_anomalies(conn) if a.entity_id == entity_id)


def resolve(conn, audit, proc: Path) -> int:
    return reconcile._resolve_orphan_anomalies(conn, audit=audit, proc_root=proc)


def records(layout, action: str) -> list[dict]:
    out = []
    for path in sorted(layout.log_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                if record.get("action") == action:
                    out.append(record)
    return out


# -- A2: the decision table --------------------------------------------------


def test_an_anomaly_whose_process_is_gone_is_resolved(conn, audit, layout, proc):
    """The reported case: anomaly 24 on the machine, naming a pid gone for hours."""
    anomaly_id = raise_orphan(conn)

    assert resolve(conn, audit, proc) == 1

    assert db.list_anomalies(conn) == []
    stored = next(a for a in db.list_anomalies(conn, unacknowledged_only=False))
    assert stored.id == anomaly_id
    assert stored.resolved_at is not None
    assert stored.acknowledged_at is None, (
        "resolution must stay distinguishable from a maintainer dismissing it"
    )


def test_an_anomaly_whose_process_is_alive_stays_listed(conn, audit, proc):
    """A2 rule 2. The condition is still true, so the report is still correct."""
    write_proc(proc, PID, starttime=START)
    raise_orphan(conn)

    assert resolve(conn, audit, proc) == 0
    assert [a.kind for a in db.list_anomalies(conn)] == ["orphan_session"]


def test_a_recycled_pid_resolves_because_identity_is_what_is_asked_about(
    conn, audit, proc
):
    """FR-024. The number is live but belongs to something else, so the process this
    anomaly was about is gone — which is exactly what it should conclude. ``is_alive``
    compares ``/proc/<pid>/stat`` field 22, so this falls out of the guard already there
    rather than needing one of its own."""
    write_proc(proc, PID, starttime="a-completely-different-start-time")
    raise_orphan(conn)

    assert resolve(conn, audit, proc) == 1
    assert db.list_anomalies(conn) == []


@pytest.mark.parametrize("pid", [None])
def test_an_anomaly_with_no_pid_is_left_alone_permanently(conn, audit, proc, pid):
    """A2 rule 1. There is nothing to re-check against, and "we could not check" must
    never be recorded as "it is fine". Ten passes, because the failure mode being guarded
    is a row that quietly resolves on some later pass instead."""
    raise_orphan(conn, pid=pid)

    for _ in range(10):
        assert resolve(conn, audit, proc) == 0
    assert len(db.list_anomalies(conn)) == 1


def test_an_anomaly_with_a_non_integer_pid_is_left_alone(conn, audit, proc):
    """A detail dict is written by whatever raised the anomaly. A wrong type must not raise
    and must not be guessed at. ``True`` is a separate case because bool is an int
    subclass in Python and would otherwise be read as pid 1."""
    with db.transaction(conn):
        db.raise_anomaly(
            conn,
            kind="orphan_session",
            entity_type="session",
            entity_id="odd",
            detail={"pid": "498936"},
        )
        db.raise_anomaly(
            conn,
            kind="orphan_session",
            entity_type="session",
            entity_id="odder",
            detail={"pid": True},
        )

    assert resolve(conn, audit, proc) == 0
    assert len(db.list_anomalies(conn)) == 2


def test_an_anomaly_with_no_proc_start_still_resolves_when_the_pid_is_absent(
    conn, audit, proc
):
    """``is_alive`` degrades to a bare existence check without a start time. That is
    weaker, and it is the documented behaviour rather than something this changes — but it
    must still answer correctly when the pid is simply not there."""
    raise_orphan(conn, proc_start=None)

    assert resolve(conn, audit, proc) == 1


def test_resolution_is_idempotent(conn, audit, layout, proc):
    """A second pass writes no second record. The ``resolved_at IS NULL`` guard on the
    UPDATE is what makes this a genuine no-op rather than a repeated write."""
    raise_orphan(conn)

    assert resolve(conn, audit, proc) == 1
    for _ in range(5):
        assert resolve(conn, audit, proc) == 0

    assert len(records(layout, "anomaly.resolved")) == 1


def test_the_record_carries_the_evidence(conn, audit, layout, proc):
    """FR-022. No ``resolved_reason`` column exists, so the log is where "on what grounds"
    lives — which is what Principle III already makes it."""
    anomaly_id = raise_orphan(conn)

    resolve(conn, audit, proc)

    written = records(layout, "anomaly.resolved")
    assert len(written) == 1
    assert written[0]["entity_id"] == str(anomaly_id)
    assert written[0]["detail"]["pid"] == PID
    assert written[0]["detail"]["proc_start"] == START
    assert "no longer running" in written[0]["detail"]["reason"]


# -- A5: the index, and the failure that leaves no trace ---------------------


def test_the_same_condition_can_be_reported_again_after_resolution(conn, audit, proc):
    """The one that catches a wrong partial index.

    A resolved row left inside ``idx_anomalies_open`` would absorb every later occurrence
    of the same ``(kind, entity_type, entity_id)`` — so an orphan retired today would make
    the *next* orphan under that session id invisible forever. Nothing else in the suite
    would fail; the symptom appears months later as an anomaly that never arrives.
    """
    raise_orphan(conn)
    assert resolve(conn, audit, proc) == 1

    raise_orphan(conn)

    assert len(db.list_anomalies(conn)) == 1, "the new occurrence must be reported"
    assert len(db.list_anomalies(conn, unacknowledged_only=False)) == 2, (
        "and the resolved one must still be on record"
    )


def test_an_unresolved_anomaly_is_still_deduplicated(conn, audit, proc):
    """The property the index existed for in the first place, which the rebuild must keep:
    a 60-second loop must not produce 1,440 rows a day for one condition."""
    write_proc(proc, PID, starttime=START)
    for _ in range(20):
        raise_orphan(conn)

    assert len(db.list_anomalies(conn)) == 1


# -- A6: the scope is one kind -----------------------------------------------


@pytest.mark.parametrize(
    "kind", ["no_transcript", "stale_socket", "prunable_worktree", "dispatching_timeout"]
)
def test_no_other_anomaly_kind_resolves_itself(conn, audit, proc, kind):
    """Each of these has its own settling story. Widening the mechanism to kinds nobody
    asked about is the speculative generality Principle I forbids — and a `pid` in the
    detail of some future kind must not be enough to make it disappear."""
    raise_orphan(conn, kind=kind, entity_id="other")

    assert resolve(conn, audit, proc) == 0
    assert [a.kind for a in db.list_anomalies(conn)] == [kind]


def test_an_acknowledged_anomaly_is_not_touched(conn, audit, proc):
    """Acknowledged rows have already left the open list; re-checking them would be work
    for nobody, and stamping ``resolved_at`` on one would rewrite what the maintainer did."""
    anomaly_id = raise_orphan(conn)
    with db.transaction(conn):
        db.acknowledge_anomaly(conn, anomaly_id)

    assert resolve(conn, audit, proc) == 0
    stored = next(a for a in db.list_anomalies(conn, unacknowledged_only=False))
    assert stored.resolved_at is None
    assert stored.acknowledged_at is not None


# -- A4: what the surfaces show ----------------------------------------------


def test_the_cli_hides_resolved_rows_and_names_them_under_all(conn, audit, config, proc):
    """One change in ``db.list_anomalies`` makes three callers correct. This asserts both
    halves: gone from the default listing, and *distinguishable* under ``--all``."""
    from tests.conftest import make_boundaries

    from robot_army import operations
    from robot_army.effects import EffectLevel

    raise_orphan(conn)
    resolve(conn, audit, proc)
    ctx = operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit),
        effect_level=EffectLevel.LIVE,
    )

    default = operations.anomalies(ctx)
    assert "no outstanding anomalies" in "\n".join(default.lines)

    everything = "\n".join(operations.anomalies(ctx, show_all=True).lines)
    assert "orphan_session" in everything
    assert "resolved" in everything
    assert "acknowledged" not in everything, (
        "a self-resolving anomaly must not read as one somebody dismissed"
    )


def test_the_web_page_hides_resolved_rows(conn, audit, config, proc, web):
    """It follows from ``db.list_anomalies`` with no change to ``web/pages.py`` at all,
    which is the reason the filter lives there."""
    raise_orphan(conn)
    # The session id, not the kind: the page also prints the list of kinds this system can
    # raise, which is present whether or not any anomaly is outstanding.
    assert "sess-1" in web.get("/anomalies").text

    resolve(conn, audit, proc)

    assert "sess-1" not in web.get("/anomalies").text
