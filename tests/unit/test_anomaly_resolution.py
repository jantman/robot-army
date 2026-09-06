"""The anomaly kinds that resolve themselves (issues #138 and #21).

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

**The scope.** Two kinds now, and still not a general mechanism. ``orphan_session`` resolves
when the pid and start time it recorded no longer name a live process; ``card_create_failing``
resolves when the card it named has reached ``linked``, which is terminal and is written in
the same transaction that records the issue. Both are conditions that can be positively
re-established as *false*. Widening this to every anomaly whose condition "looks passed" is
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
from robot_army.cardstates import CardState

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


# -- card_create_failing (issue #21) ----------------------------------------
#
# The second retractable kind. Both anomalies on the reporting machine were of this kind,
# both belonged to cards that had resolved themselves the day before, and both were still
# being listed as outstanding — which is the staleness the paragraph at the top of this file
# describes, arriving by a different route.


def _card(conn, *, card_id="card-1", dry_run=False, state=None, board_id="board-1",
          issue_number=7):
    """A card row, optionally driven to a state, without going through the intake path.

    ``issue_number`` is a parameter because ``idx_cards_issue`` is unique over
    ``(repo_key, issue_number, dry_run)`` — the §11 invariant that one issue maps to one card
    — so a test seeding several linked cards has to give each its own.
    """
    with db.transaction(conn):
        row_id = db.insert_card(
            conn,
            board_id=board_id,
            card_id=card_id,
            card_url=f"https://trello.com/c/{card_id}",
            title="a card",
            body="",
            dry_run=dry_run,
        )
        if state is not None:
            conn.execute(
                "UPDATE cards SET state = ?, repo_key = 'jantman/demo', issue_number = ?, "
                "create_failures = 0 WHERE id = ?",
                (str(state), issue_number, row_id),
            )
    return row_id


def raise_create_failing(conn, *, card_id="card-1", dry_run=False) -> int:
    with db.transaction(conn):
        db.raise_anomaly(
            conn,
            kind="card_create_failing",
            entity_type="card",
            entity_id=card_id,
            detail={"repo_key": "jantman/demo", "attempts": 3},
            dry_run=dry_run,
        )
    return next(
        a.id
        for a in db.list_anomalies(conn, include_simulated=True)
        if a.entity_id == card_id
    )


def resolve_cards(conn, audit) -> int:
    return reconcile._resolve_card_create_anomalies(conn, audit=audit)


def test_an_anomaly_whose_card_has_been_linked_is_resolved(conn, audit, layout):
    """The reported case: an anomaly about a creation that has since succeeded."""
    _card(conn, state=CardState.LINKED)
    anomaly_id = raise_create_failing(conn)

    assert resolve_cards(conn, audit) == 1
    assert db.list_anomalies(conn) == []
    every = db.list_anomalies(conn, unacknowledged_only=False)
    assert [a.id for a in every] == [anomaly_id]
    assert every[0].resolved_at is not None
    assert every[0].acknowledged_at is None, (
        "resolved and acknowledged are different facts and must stay distinguishable"
    )


def test_an_anomaly_whose_card_is_still_failing_stays_listed(conn, audit):
    """`creating` is where a failed creation stays — the intent stands and recovery runs."""
    _card(conn, state=CardState.CREATING)
    raise_create_failing(conn)

    assert resolve_cards(conn, audit) == 0
    assert [a.kind for a in db.list_anomalies(conn)] == ["card_create_failing"]


def test_an_anomaly_whose_card_is_not_there_is_left_alone(conn, audit):
    """"I could not check" must never be recorded as "it is fine".

    A purge, or a database restored without the card, leaves nothing to re-check against —
    exactly the case the orphan resolver treats the same way when a pid was never recorded.
    """
    raise_create_failing(conn, card_id="vanished")

    assert resolve_cards(conn, audit) == 0
    assert [a.entity_id for a in db.list_anomalies(conn)] == ["vanished"]


@pytest.mark.parametrize("state", [CardState.DISCOVERED, CardState.NEEDS_INFO, CardState.DROPPED])
def test_no_state_but_linked_resolves_the_anomaly(conn, audit, state):
    """`linked` is the only state that says the creation succeeded, and it is terminal."""
    _card(conn, state=state)
    raise_create_failing(conn)

    assert resolve_cards(conn, audit) == 0
    assert len(db.list_anomalies(conn)) == 1


def test_card_resolution_is_idempotent(conn, audit, layout):
    """The `resolved_at IS NULL` guard: a second pass writes nothing and logs nothing.

    Reconciliation runs every 60 seconds. Without this, every tick would log a resolution for
    a row that resolved once, which is a log that cannot be read.
    """
    _card(conn, state=CardState.LINKED)
    raise_create_failing(conn)

    assert resolve_cards(conn, audit) == 1
    assert resolve_cards(conn, audit) == 0
    assert len(records(layout, "anomaly.resolved")) == 1


def test_the_card_record_carries_the_evidence(conn, audit, layout):
    """Principle III: from the log alone, why this row left the list."""
    _card(conn, state=CardState.LINKED)
    anomaly_id = raise_create_failing(conn)

    resolve_cards(conn, audit)

    record = records(layout, "anomaly.resolved")[0]
    assert record["entity_type"] == "anomaly"
    assert record["entity_id"] == str(anomaly_id)
    assert record["detail"]["kind"] == "card_create_failing"
    assert record["detail"]["anomaly_entity_id"] == "card-1"
    assert record["detail"]["card_state"] == "linked"
    assert record["detail"]["issue"] == "jantman/demo#7"
    assert "has since been linked" in record["detail"]["reason"]


def test_a_rehearsed_anomaly_resolves_against_its_own_rehearsed_card(conn, audit):
    """One card id can hold a real row and a rehearsed one; the lookup carries `dry_run`.

    Resolving a rehearsal's anomaly on the strength of a *real* card's success would retract
    a report about something that never happened, on evidence about something else.
    """
    _card(conn, card_id="card-1", dry_run=True, state=CardState.CREATING)
    _card(conn, card_id="card-1", dry_run=False, state=CardState.LINKED)
    raise_create_failing(conn, card_id="card-1", dry_run=True)

    assert resolve_cards(conn, audit) == 0
    assert [a.dry_run for a in db.list_anomalies(conn, include_simulated=True)] == [True]


def test_a_rehearsed_anomaly_is_retracted_like_any_other(conn, audit, layout):
    """Rehearsed rows are in scope here, and their absence would be the bug.

    ``anomalies`` hides them by default, but a rehearsal's list going stale is the same
    problem as a real one's — and the maintainer who passes ``--include-simulated`` to look
    deserves a list that has been re-checked.
    """
    _card(conn, card_id="card-1", dry_run=True, state=CardState.LINKED)
    raise_create_failing(conn, card_id="card-1", dry_run=True)

    assert resolve_cards(conn, audit) == 1
    assert db.list_anomalies(conn, include_simulated=True) == []
    assert records(layout, "anomaly.resolved")[0]["dry_run"] is True


def test_an_acknowledged_card_anomaly_is_not_touched(conn, audit):
    """It has already left the open list; stamping `resolved_at` would rewrite what I did."""
    _card(conn, state=CardState.LINKED)
    anomaly_id = raise_create_failing(conn)
    with db.transaction(conn):
        db.acknowledge_anomaly(conn, anomaly_id)

    assert resolve_cards(conn, audit) == 0
    row = db.list_anomalies(conn, unacknowledged_only=False)[0]
    assert row.acknowledged_at is not None and row.resolved_at is None


def test_the_same_card_can_raise_the_condition_again_after_resolution(conn, audit):
    """A resolved row must leave the partial unique index, or the next occurrence is silent.

    The failure mode has no error and no failing assertion anywhere else — the symptom is an
    anomaly that never appears, months later.
    """
    _card(conn, state=CardState.LINKED)
    raise_create_failing(conn)
    assert resolve_cards(conn, audit) == 1

    with db.transaction(conn):
        created = db.raise_anomaly(
            conn,
            kind="card_create_failing",
            entity_type="card",
            entity_id="card-1",
            detail={"attempts": 9},
        )

    assert created is True
    assert [a.detail_obj["attempts"] for a in db.list_anomalies(conn)] == [9]


def test_a_pass_killed_midway_keeps_what_it_reached(conn, audit, layout, monkeypatch):
    """One transaction per anomaly, so an interruption settles rows rather than losing them.

    The constitution asks this of every state-settling pass: what happens if it is killed
    halfway. The answer must be "the rows it reached are resolved and logged, the rest are
    outstanding for next time" — not "the whole pass is lost" and not "some row is resolved
    without a record of why".
    """
    for n in (1, 2, 3):
        _card(conn, card_id=f"card-{n}", state=CardState.LINKED, issue_number=n)
        raise_create_failing(conn, card_id=f"card-{n}")

    real_resolve = db.resolve_anomaly
    calls = {"n": 0}

    def explode_on_the_third(conn_, anomaly_id):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("killed mid-pass")
        return real_resolve(conn_, anomaly_id)

    monkeypatch.setattr(db, "resolve_anomaly", explode_on_the_third)
    with pytest.raises(RuntimeError):
        resolve_cards(conn, audit)
    monkeypatch.undo()

    settled = [a for a in db.list_anomalies(conn, unacknowledged_only=False) if a.resolved_at]
    assert len(settled) == 2, "what the pass reached is committed"
    assert len(db.list_anomalies(conn)) == 1, "the rest is left for the next pass"
    assert len(records(layout, "anomaly.resolved")) == 2, (
        "every resolution that happened is in the log; none is silent"
    )

    assert resolve_cards(conn, audit) == 1, "the next pass finishes the job"


def test_a_full_reconciliation_pass_runs_both_resolvers(conn, audit, config, proc, tmp_path):
    """The two are wired into the same pass and counted together."""
    from tests.conftest import make_boundaries

    _card(conn, state=CardState.LINKED)
    raise_create_failing(conn)
    raise_orphan(conn)

    registry = tmp_path / "registry"
    registry.mkdir(exist_ok=True)
    result = reconcile.reconcile(
        conn,
        boundaries=make_boundaries(audit),
        audit=audit,
        config=config,
        layout=config.layout,
        registry_dir=registry,
        proc_root=proc,
    )

    assert result.anomalies_resolved == 2
    assert db.list_anomalies(conn) == []


def test_both_resolvers_mark_a_rehearsed_retraction_as_rehearsed(conn, audit, layout, proc):
    """A retraction of a rehearsed anomaly is itself rehearsed work.

    Without the marker the `anomaly.resolved` record shows in `robot-army log`'s default view
    while the anomaly it is about does not — the same mismatch between what a surface hides
    and what it says that issue #21 exists to remove. Both resolvers, because the older one
    predates the flag and is exactly where the inconsistency would sit unnoticed.
    """
    with db.transaction(conn):
        db.raise_anomaly(
            conn, kind="orphan_session", entity_type="session", entity_id="sim-sess",
            detail={"pid": PID, "proc_start": START}, dry_run=True,
        )
    _card(conn, card_id="sim-card", dry_run=True, state=CardState.LINKED)
    raise_create_failing(conn, card_id="sim-card", dry_run=True)

    assert resolve(conn, audit, proc) == 1
    assert resolve_cards(conn, audit) == 1

    written = records(layout, "anomaly.resolved")
    assert len(written) == 2
    assert all(record.get("dry_run") is True for record in written), (
        "both retractions concern rehearsed work and must say so"
    )
