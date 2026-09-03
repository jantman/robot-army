"""``doctor``'s project board checks (issue #48, T036).

Two properties carry the weight. A **fine-grained token is named as such** rather than
reported as a generic refusal, because no amount of configuration fixes it and the author
would otherwise chase the wrong thing. And a view sort whose field is unset produces **no
warning at all** — a check that cries wolf is a check that stops being read.
"""

from __future__ import annotations

from tests.conftest import FakeIssueReader, make_boundaries

from robot_army import db, poll
from robot_army.boundaries import ProjectAccess, ProjectResolution, TransportError


def onboard(conn, key="demo"):
    with db.transaction(conn):
        db.upsert_repo(conn, repo_key=key, settings_fingerprint=None, trust_verified=True)


def resolved():
    return ProjectResolution(
        project_id="PVT_3",
        project_number=3,
        project_title="robot-army",
        project_url="https://github.com/users/jantman/projects/3",
        project_source="discovered",
        column_name="Ready",
        column_source="discovered",
    )


def check(conn, config, audit, reader):
    return poll.check_project(
        conn,
        boundaries=make_boundaries(audit, reader=reader),
        config=config,
        repo_key="demo",
    )


def named(checks, suffix):
    return next(c for c in checks if c.name.endswith(suffix))


def test_a_healthy_board_passes_every_check(conn, config, audit):
    onboard(conn)
    reader = FakeIssueReader()
    reader.resolution = resolved()

    checks = check(conn, config, audit, reader)

    assert all(c.ok for c in checks)
    assert {c.name.split()[-1] for c in checks} >= {"token", "project", "column", "sort"}


def test_a_fine_grained_token_is_named_rather_than_reported_generically(
    conn, config, audit
):
    """GitHub has no account-level Projects permission for fine-grained tokens, so one
    cannot read a user-owned board however it is configured. Saying "forbidden" would send
    the author to edit settings that cannot help."""
    onboard(conn)
    reader = FakeIssueReader()
    reader.access = ProjectAccess(
        ok=False,
        credential_kind="fine-grained or app",
        scopes=(),
        detail=(
            "this looks like a fine-grained token or GitHub App (FORBIDDEN). GitHub has "
            "no account-level Projects permission for fine-grained tokens, so one cannot "
            "read a user-owned board at all — use a classic token with read:project"
        ),
    )

    checks = check(conn, config, audit, reader)

    token = named(checks, "token")
    assert not token.ok
    assert "fine-grained" in token.detail
    assert "classic token with read:project" in token.detail


def test_a_failing_token_stops_the_remaining_questions(conn, config, audit):
    """Their answers would all be the same refusal, and four copies of one problem is how
    a check list stops being read."""
    onboard(conn)
    reader = FakeIssueReader()
    reader.access = ProjectAccess(ok=False, credential_kind="classic", detail="no scope")

    checks = check(conn, config, audit, reader)

    assert [c.name.split()[-1] for c in checks] == ["token"]


def test_an_absent_board_passes_rather_than_failing_the_command(conn, config, audit):
    """Found in review, round three. `doctor` exits non-zero on any failed check, so
    reporting absence as a failure would make the command fail on every installation that
    has no project board — which is most of them, and is not a problem."""
    onboard(conn)
    reader = FakeIssueReader()  # the default: absent, nothing linked

    checks = check(conn, config, audit, reader)

    project = named(checks, "project")
    assert project.ok
    assert "no project is linked" in project.detail
    assert "no effect here" in project.detail
    assert all(c.ok for c in checks)


def test_doctor_exits_zero_on_an_installation_with_no_boards(conn, config, monkeypatch):
    from robot_army import operations

    onboard(conn)
    reader = FakeIssueReader()
    monkeypatch.setattr(
        operations,
        "wire",
        lambda level, cfg, log: make_boundaries(log, level=level, reader=reader),
    )
    ctx = operations.build_context(config)
    try:
        result = operations.doctor(ctx)
    finally:
        ctx.close()

    board_failures = [f for f in result.data["failures"] if f.startswith("project:")]
    assert board_failures == []


def test_an_unresolved_project_is_reported_with_its_reason(conn, config, audit):
    onboard(conn)
    reader = FakeIssueReader()
    reader.resolution = ProjectResolution(reason="two projects are linked to demo")

    checks = check(conn, config, audit, reader)

    project = named(checks, "project")
    assert not project.ok
    assert "two projects are linked" in project.detail


def test_a_view_sort_with_no_values_set_produces_no_warning(conn, config, audit):
    """The measured state of the author's own board: view 1 sorts by Priority, and every
    card in Ready has Priority unset, so manual position is exactly what is displayed."""
    onboard(conn)
    reader = FakeIssueReader()
    reader.resolution = resolved()
    reader.view_conflicts = []

    checks = check(conn, config, audit, reader)

    sort = named(checks, "sort")
    assert sort.ok
    assert "no board view sorts" in sort.detail


def test_a_view_sort_that_would_bite_is_reported(conn, config, audit):
    onboard(conn)
    reader = FakeIssueReader()
    reader.resolution = resolved()
    reader.view_conflicts = ["view #1 'Backlog' sorts by 'Priority'"]

    checks = check(conn, config, audit, reader)

    sort = named(checks, "sort")
    assert not sort.ok
    assert "Priority" in sort.detail


def test_a_repository_with_ordering_off_reports_that_and_asks_nothing(
    conn, config, audit
):
    from dataclasses import replace

    onboard(conn)
    reader = FakeIssueReader()
    off = replace(config, dispatch=replace(config.dispatch, project_ordering=False))

    checks = check(conn, off, audit, reader)

    assert len(checks) == 1
    assert checks[0].ok
    assert "board ordering off" in checks[0].detail
    assert reader.access_calls == []


def test_a_stale_snapshot_fails_the_freshness_check(conn, config, audit):
    from robot_army.models import RepoProject

    onboard(conn)
    with db.transaction(conn):
        db.save_repo_project(
            conn,
            RepoProject(
                repo_key="demo",
                last_read_at="2026-09-01T00:00:00Z",
                last_error="GitHub is down",
                consecutive_failures=3,
            ),
        )
    reader = FakeIssueReader()
    reader.resolution = resolved()

    checks = check(conn, config, audit, reader)

    freshness = named(checks, "freshness")
    assert not freshness.ok
    assert "GitHub is down" in freshness.detail


def test_a_transport_failure_becomes_a_finding_not_an_exception(conn, config, audit):
    """`doctor` reports problems. One unreachable board must not stop it reporting on
    everything else."""
    onboard(conn)
    reader = FakeIssueReader()
    reader.raise_on_resolve = TransportError("GitHub is down")

    checks = check(conn, config, audit, reader)

    project = named(checks, "project")
    assert not project.ok
    assert "GitHub is down" in project.detail


def test_doctor_surfaces_the_checks_under_a_project_prefix(conn, config, audit, monkeypatch):
    from robot_army import operations

    onboard(conn)
    reader = FakeIssueReader()
    reader.resolution = resolved()
    monkeypatch.setattr(
        operations,
        "wire",
        lambda level, cfg, log: make_boundaries(log, level=level, reader=reader),
    )
    ctx = operations.build_context(config)
    try:
        result = operations.doctor(ctx)
    finally:
        ctx.close()

    names = [c["name"] for c in result.data["checks"]]
    assert any(n.startswith("project: demo") for n in names)
