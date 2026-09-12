"""Eligibility, ETag reuse, and idempotent discovery (T056).

Each eligibility condition is tested failing **in isolation**, because a check that only
works when the other three also fail is not a check.
"""

from __future__ import annotations

from tests.conftest import FakeIssueReader, make_boundaries, make_issue

from robot_army import db, poll
from robot_army.boundaries import TransportError
from robot_army.states import WorkItemState


def onboard(conn, key="demo"):
    with db.transaction(conn):
        db.upsert_repo(conn, repo_key=key, settings_fingerprint=None, trust_verified=True)


def test_a_fully_eligible_issue_passes(config):
    verdict = poll.evaluate(make_issue(), config=config, repo_key="demo", onboarded=True)
    assert verdict.eligible


def test_wrong_author_is_rejected_and_persisted(config):
    """FR-007's security boundary. The label is a trigger anyone with write access could
    apply; the author check is what stops that being a path into this machine."""
    verdict = poll.evaluate(
        make_issue(author="someone-else"), config=config, repo_key="demo", onboarded=True
    )
    assert not verdict.eligible
    assert "security boundary" in (verdict.reason or "")
    assert verdict.persist, "a deliberately labelled issue deserves a persisted reason"


def test_the_author_check_cannot_be_bypassed_by_an_empty_config_value(config):
    """There is deliberately no "any author" value; config validation rejects a blank
    one, so no issue can ever match by accident."""
    from dataclasses import replace

    blank = replace(config, github=replace(config.github, author=""))
    verdict = poll.evaluate(
        make_issue(author=""), config=blank, repo_key="demo", onboarded=True
    )
    # Even if a blank author somehow reached here, it can only ever match an issue with a
    # blank author — which GitHub does not produce. Validation is the real guard, and
    # test_config.py asserts it rejects the blank value outright.
    assert verdict.eligible is True
    from robot_army.config import ConfigError

    try:
        from tests.conftest import config_dict, monkey_token

        from robot_army.config import parse

        monkey_token()
        parse(
            config_dict(config.repos["demo"].path, config.layout, config.worktree_root,
                        github={"author": ""}),
            config.path,
        )
    except ConfigError as exc:
        assert any("security boundary" in p for p in exc.problems)
    else:  # pragma: no cover
        raise AssertionError("a blank author must be a validation error")


def test_missing_label_is_rejected_without_a_row(config):
    verdict = poll.evaluate(
        make_issue(labels=("bug",)), config=config, repo_key="demo", onboarded=True
    )
    assert not verdict.eligible
    assert not verdict.persist


def test_a_section_is_no_longer_what_makes_a_repository_eligible(config):
    """Milestone 005's intentional inversion. An onboarded repository with no
    ``[repos.*]`` section is eligible — that is the whole milestone — and the section that
    used to be the gate is now an override the eligibility check has no opinion about."""
    verdict = poll.evaluate(make_issue(), config=config, repo_key="other", onboarded=True)
    assert verdict.eligible
    assert "other" not in config.repos, "and it genuinely has no section"


def test_not_onboarded_is_rejected_without_a_row(config):
    """No row, because ``work_items.repo_key`` is a foreign key into ``repos`` and that
    table only gets an entry once onboarding happened."""
    verdict = poll.evaluate(make_issue(), config=config, repo_key="demo", onboarded=False)
    assert not verdict.eligible
    assert "onboard" in (verdict.reason or "")
    assert not verdict.persist


def test_a_closed_issue_is_rejected(config):
    verdict = poll.evaluate(
        make_issue(state="closed"), config=config, repo_key="demo", onboarded=True
    )
    assert not verdict.eligible


def test_polling_creates_a_ready_item(conn, audit, config):
    onboard(conn)
    reader = FakeIssueReader([make_issue()])
    boundaries = make_boundaries(audit, reader=reader)

    outcome = poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    assert outcome.created == 1
    items = db.list_work_items(conn)
    assert len(items) == 1
    assert items[0].state is WorkItemState.READY
    assert items[0].title == "Fix the thing"


def test_a_rejected_but_labelled_issue_lands_in_failed_with_a_reason(conn, audit, config):
    """FR-009: the maintainer deliberately labelled it and will want to know why nothing
    happened."""
    onboard(conn)
    reader = FakeIssueReader([make_issue(author="stranger")])
    boundaries = make_boundaries(audit, reader=reader)

    poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    item = db.list_work_items(conn)[0]
    assert item.state is WorkItemState.FAILED
    assert "security boundary" in (item.blocked_reason or "")


def test_an_unlabelled_issue_produces_no_row(conn, audit, config):
    onboard(conn)
    reader = FakeIssueReader([make_issue(labels=("bug",))])
    boundaries = make_boundaries(audit, reader=reader)

    outcome = poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    assert outcome.created == 0
    assert db.list_work_items(conn) == []


def test_repolling_the_same_issue_is_a_no_op(conn, audit, config):
    """FR-072: re-polling must not produce a second worktree and a second session."""
    onboard(conn)
    reader = FakeIssueReader([make_issue()])
    boundaries = make_boundaries(audit, reader=reader)

    for _ in range(3):
        # Force a fresh listing each time rather than a 304, so the idempotency being
        # tested is the unique index rather than the conditional request.
        reader.etag = None
        poll.poll_repo(
            conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
        )
    assert len(db.list_work_items(conn)) == 1


# -- a queued row's labels follow the listing (issue #62) --------------------


def label_records(layout, audit) -> list[dict]:
    import json

    audit.close()
    out = []
    for path in sorted(layout.log_dir.glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["action"] == "poll.labels_refreshed":
                out.append(record)
    return out


def poll_twice(conn, audit, config, first, second):
    """Discover ``first``, then list the same issue again as ``second``, unconditionally."""
    onboard(conn)
    reader = FakeIssueReader([first])
    boundaries = make_boundaries(audit, reader=reader)
    poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    reader.issues = [second]
    reader.etag = None
    return boundaries


def repoll(conn, audit, config, boundaries):
    poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )


def test_labelling_a_queued_issue_refreshes_its_row_and_releases_its_hold(
    conn, audit, config, layout
):
    """Without the refresh, labelling the issue under a new ``[github] label`` would reach
    the existing-row branch and be skipped, and the item would stay held for good."""
    from dataclasses import replace

    from tests.unit.test_ordering import snapshot

    from robot_army import ordering

    scratch = replace(config, github=replace(config.github, label="scratch"))
    boundaries = poll_twice(
        conn, audit, config, make_issue(), make_issue(labels=("robot-army", "scratch"))
    )
    assert ordering.plan(conn, config=scratch, capacity=snapshot())[0].hold is (
        ordering.HoldReason.NOT_LABELLED
    )

    repoll(conn, audit, scratch, boundaries)

    item = db.list_work_items(conn)[0]
    assert item.label_list == ["robot-army", "scratch"]
    assert ordering.plan(conn, config=scratch, capacity=snapshot())[0].hold is None
    refreshed = label_records(layout, audit)
    assert len(refreshed) == 1
    assert refreshed[0]["entity_id"] == item.id
    assert refreshed[0]["detail"] == {"old": ["robot-army"], "new": ["robot-army", "scratch"]}


def test_the_same_labels_in_another_order_write_and_record_nothing(conn, audit, config, layout):
    """The steady-state poll must stay as quiet as it was."""
    boundaries = poll_twice(
        conn,
        audit,
        config,
        make_issue(labels=("bug", "robot-army")),
        make_issue(labels=("robot-army", "bug")),
    )
    before = dict(conn.execute("SELECT labels, updated_at FROM work_items").fetchone())

    repoll(conn, audit, config, boundaries)

    assert dict(conn.execute("SELECT labels, updated_at FROM work_items").fetchone()) == before
    assert label_records(layout, audit) == []


def test_a_row_past_ready_is_not_refreshed(conn, audit, config, layout):
    """Only ``ready`` consults its labels again; nothing past dispatch looks at them."""
    boundaries = poll_twice(
        conn, audit, config, make_issue(), make_issue(labels=("robot-army", "scratch"))
    )
    conn.execute("UPDATE work_items SET state = ?", (str(WorkItemState.ACTIVE),))

    repoll(conn, audit, config, boundaries)

    assert db.list_work_items(conn)[0].label_list == ["robot-army"]
    assert label_records(layout, audit) == []


def test_unreadable_stored_labels_heal_on_the_next_listing(conn, audit, config, layout):
    boundaries = poll_twice(conn, audit, config, make_issue(), make_issue())
    conn.execute("UPDATE work_items SET labels = 'not json'")

    repoll(conn, audit, config, boundaries)

    assert db.list_work_items(conn)[0].label_list == ["robot-army"]
    assert label_records(layout, audit)[0]["detail"]["old"] is None


def test_the_etag_is_persisted_and_replayed(conn, audit, config):
    """304 is the healthy steady state — it costs nothing against the rate limit (R4)."""
    onboard(conn)
    reader = FakeIssueReader([make_issue()], etag='W/"abc"')
    boundaries = make_boundaries(audit, reader=reader)

    first = poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    assert first.status == 200
    state = db.get_poll_state(conn, "demo")
    assert (state.etag, state.etag_request) == ('W/"abc"', reader.request), "stored as a pair"

    second = poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    assert second.status == 304
    assert second.found == 0
    assert reader.poll_calls[-1] == ("demo", 'W/"abc"', reader.request)


def test_a_transport_failure_is_recorded_and_backed_off_not_swallowed(conn, audit, config):
    """"No eligible work" and "I could not ask" are different facts."""
    onboard(conn)
    reader = FakeIssueReader([make_issue()])
    reader.raise_on_poll = TransportError("connection reset")
    boundaries = make_boundaries(audit, reader=reader)

    outcome = poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    assert outcome.error is not None
    assert outcome.created == 0
    state = db.get_poll_state(conn, "demo")
    assert state.consecutive_failures == 1
    assert state.backoff_until is not None


def test_a_repository_in_backoff_is_skipped(conn, audit, config):
    onboard(conn)
    reader = FakeIssueReader([make_issue()])
    boundaries = make_boundaries(audit, reader=reader)
    from robot_army.models import PollState
    from robot_army.reconcile import within

    with db.transaction(conn):
        db.save_poll_state(
            conn, PollState(repo_key="demo", backoff_until=within(600), consecutive_failures=3)
        )
    outcome = poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    assert outcome.skipped_reason is not None
    assert reader.poll_calls == []


def test_a_successful_poll_clears_the_failure_counter(conn, audit, config):
    onboard(conn)
    reader = FakeIssueReader([make_issue()])
    boundaries = make_boundaries(audit, reader=reader)
    from robot_army.models import PollState

    with db.transaction(conn):
        db.save_poll_state(conn, PollState(repo_key="demo", consecutive_failures=4))
    poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    assert db.get_poll_state(conn, "demo").consecutive_failures == 0


def test_dry_run_rows_are_marked_and_coexist_with_live_ones(conn, audit, config):
    onboard(conn)
    reader = FakeIssueReader([make_issue()])
    boundaries = make_boundaries(audit, reader=reader)

    reader.etag = None
    poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=True
    )
    reader.etag = None
    poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    everything = db.list_work_items(conn, include_simulated=True)
    assert len(everything) == 2
    assert {i.dry_run for i in everything} == {True, False}
    assert len(db.list_work_items(conn)) == 1


def test_an_item_left_in_discovered_is_re_evaluated_on_the_next_poll(conn, audit, config):
    """The interruption case: the row was written before evaluation and the process died."""
    onboard(conn)
    with db.transaction(conn):
        item_id = db.insert_work_item(
            conn,
            source="github",
            source_id="demo#42",
            source_url="u",
            repo_key="demo",
            issue_number=42,
            title="t",
            body="b",
            labels='["robot-army"]',
            author="jantman",
            dry_run=False,
        )
    reader = FakeIssueReader([make_issue()])
    boundaries = make_boundaries(audit, reader=reader)
    poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    item = db.get_work_item(conn, item_id)
    assert item is not None and item.state is WorkItemState.READY


def test_poll_all_continues_past_one_repositorys_failure(conn, audit, config):
    onboard(conn)
    reader = FakeIssueReader([make_issue()])
    boundaries = make_boundaries(audit, reader=reader)
    outcomes = poll.poll_all(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        dry_run=False,
        only_repo="not-configured",
    )
    assert len(outcomes) == 1
    assert outcomes[0].error is not None


# -- the polled set comes from the record, not the file (milestone 005, T034) ----


def test_a_section_with_no_onboarding_record_is_never_polled(conn, audit, config):
    """The one intentional breaking change in milestone 005 (FR-015, FR-016).

    ``demo`` has a ``[repos.*]`` section in the fixture and no row in ``repos``. It was
    never dispatchable — onboarding has always been the gate — so what changes is that the
    system stops *pretending* to watch it."""
    assert "demo" in config.repos
    boundaries = make_boundaries(audit, reader=FakeIssueReader([make_issue()]))

    outcomes = poll.poll_all(
        conn, boundaries=boundaries, audit=audit, config=config, dry_run=False
    )

    assert outcomes == []
    assert boundaries.issue_reader.poll_calls == [], "not even a request was made"
    assert db.list_work_items(conn, include_simulated=True) == []


def test_naming_an_unonboarded_repository_explicitly_reports_why(conn, audit, config):
    boundaries = make_boundaries(audit, reader=FakeIssueReader([make_issue()]))

    outcomes = poll.poll_all(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        dry_run=False,
        only_repo="demo",
    )

    assert len(outcomes) == 1
    assert "is not onboarded" in (outcomes[0].error or "")
    assert "robot-army onboard demo" in (outcomes[0].error or "")


def test_an_onboarded_repository_with_no_section_is_polled(conn, audit, config):
    """The mirror image, and the milestone's actual point."""
    onboard(conn, "jantman/no-section")
    boundaries = make_boundaries(audit, reader=FakeIssueReader([]))

    outcomes = poll.poll_all(
        conn, boundaries=boundaries, audit=audit, config=config, dry_run=False
    )

    assert [o.repo_key for o in outcomes] == ["jantman/no-section"]
    assert outcomes[0].error is None


def repos_table(result):
    """Rendered `robot-army repos` rows as {header: cell} dicts.

    Parsed against the **dashed rule**, which `_table` sizes to each column, rather than
    against cell order. That is the whole point: this file shipped a row whose cells were
    correct in order and wrong under the headers, because a column had been renamed and one
    row-builder was not updated with it. Asserting on positions would have missed it again.
    """
    lines = [line for line in result.lines if line.strip()]
    header, rule, *body = lines
    spans, start = [], 0
    for dashes in rule.split("  "):
        spans.append((start, start + len(dashes)))
        start += len(dashes) + 2
    names = [header[a:b].strip() for a, b in spans]
    return [
        {name: row[a:b].strip() for name, (a, b) in zip(names, spans, strict=True)}
        for row in body
    ]


def test_the_repos_verb_reports_a_section_without_a_record_as_not_onboarded(
    conn, audit, config
):
    """FR-017. Listing it as known is how "why is nothing happening for this repo" got
    asked in the first place."""
    from robot_army import operations
    from robot_army.effects import EffectLevel

    ctx = operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit),
        effect_level=EffectLevel.LIVE,
    )

    result = operations.repos(ctx)

    rows = {entry["repo_key"]: entry for entry in result.data["repos"]}
    assert rows["demo"]["onboarded"] is False
    assert "never onboarded" in rows["demo"]["note"]
    assert "NOT ONBOARDED" in "\n".join(result.lines)


def test_every_repos_row_puts_its_values_under_the_right_headers(conn, audit, config):
    """The three row shapes the verb produces, checked against the headers rather than
    against each other. A renamed column is invisible to a test that reads by position."""
    from robot_army import operations
    from robot_army.effects import EffectLevel

    onboard(conn, "jantman/pre-005")  # a record with no clone_path — the pre-005 shape
    ctx = operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit),
        effect_level=EffectLevel.LIVE,
    )

    rows = {row["repo"]: row for row in repos_table(operations.repos(ctx))}

    # Onboarded before migration 005: no location, and the cell that would say where it
    # came from says what to do instead.
    legacy = rows["jantman/pre-005"]
    assert legacy["clone path"] == "(never recorded)"
    assert legacy["path source"] == "NEEDS REAPPROVE"
    assert "yes" not in legacy.values(), (
        "'yes' was the boolean for the old 'onboarded' column; nothing may still emit it"
    )

    # A section with no record: not watched, and the verb says so in the same column.
    assert rows["demo"]["path source"] == "NOT ONBOARDED"


def test_a_normal_row_reports_its_path_source_verbatim(conn, audit, config, repo_clone):
    from tests.conftest import onboard_repo

    from robot_army import operations
    from robot_army.effects import EffectLevel

    onboard_repo(conn, "jantman/derived", repo_clone, path_source="derived")
    onboard_repo(conn, "jantman/explicit", repo_clone, path_source="configured")
    ctx = operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit),
        effect_level=EffectLevel.LIVE,
    )

    rows = {row["repo"]: row for row in repos_table(operations.repos(ctx))}

    assert rows["jantman/derived"]["path source"] == "derived"
    assert rows["jantman/explicit"]["path source"] == "configured"
    assert rows["jantman/derived"]["clone path"] == str(repo_clone)


# -- issue #60: a stored ETag is bound to the request that produced it -----------------------


class _RealPollReader(FakeIssueReader):
    """The real ``GitHubReader.poll`` over a mock transport; everything else faked.

    Only ``poll`` goes to the wire, so the board read that follows it in ``poll_repo`` is
    answered by the fake rather than by a GraphQL handler this test has no interest in.
    """

    def __init__(self, real) -> None:
        super().__init__()
        self.real = real

    def poll(self, repo_key, etag, *, etag_request):
        return self.real.poll(repo_key, etag, etag_request=etag_request)


def _issue_json(number: int, label: str, author: str) -> dict:
    return {
        "number": number,
        "title": f"issue {number}",
        "body": "",
        "html_url": f"https://github.com/jantman/demo/issues/{number}",
        "labels": [{"name": label}],
        "user": {"login": author},
        "state": "open",
    }


def test_changing_the_label_is_seen_on_the_next_poll(conn, audit, config):
    """The reported defect, end to end.

    The handler answers 304 to **any** ``If-None-Match`` — what GitHub was observed doing
    when the label was swapped away and back — so the only thing that can make the second
    poll see the new label's issue is not offering the old ETag at all.
    """
    from dataclasses import replace

    import httpx

    from robot_army.boundaries.github import GitHubReader

    onboard(conn)
    author = config.github.author
    listing = {
        "robot-army-verify": [_issue_json(7, "robot-army-verify", author)],
        "robot-army": [_issue_json(8, "robot-army", author)],
    }
    offered: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offered.append(request.headers.get("If-None-Match"))
        if "If-None-Match" in request.headers:
            return httpx.Response(304)
        label = request.url.params["labels"]
        return httpx.Response(200, json=listing[label], headers={"ETag": f'W/"{label}"'})

    def poll_under(label: str):
        cfg = replace(config, github=replace(config.github, label=label))
        client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
        reader = _RealPollReader(GitHubReader(cfg, audit, client=client, sleep=lambda _: None))
        return poll.poll_repo(
            conn, boundaries=make_boundaries(audit, reader=reader), audit=audit, config=cfg,
            repo_key="demo", dry_run=False,
        )

    assert poll_under("robot-army-verify").created == 1
    changed = poll_under("robot-army")
    assert changed.status == 200, "the stale ETag was not offered"
    assert changed.created == 1
    assert {i.issue_number for i in db.list_work_items(conn)} == {7, 8}

    again = poll_under("robot-army")
    assert again.status == 304, "conditional again once the new pair is stored"
    assert offered == [None, None, 'W/"robot-army"']


def test_a_transport_failure_keeps_the_etag_and_its_request_together(conn, audit, config):
    """FR-006: a failure says nothing about which request the ETag answered."""
    from robot_army.models import PollState

    onboard(conn)
    with db.transaction(conn):
        db.save_poll_state(
            conn, PollState(repo_key="demo", etag='W/"abc"', etag_request="/repos/demo/issues?x=1")
        )
    reader = FakeIssueReader([make_issue()])
    reader.raise_on_poll = TransportError("connection reset")

    poll.poll_repo(
        conn, boundaries=make_boundaries(audit, reader=reader), audit=audit, config=config,
        repo_key="demo", dry_run=False,
    )

    state = db.get_poll_state(conn, "demo")
    assert (state.etag, state.etag_request) == ('W/"abc"', "/repos/demo/issues?x=1")
    assert state.consecutive_failures == 1


def test_a_row_from_before_the_request_was_recorded_heals_on_its_next_poll(
    conn, audit, config
):
    """User story 2: the machine that hit this. Its ETag would match — only the missing
    request stops it being offered — and one full listing later the row holds a real pair."""
    from robot_army.models import PollState

    onboard(conn)
    with db.transaction(conn):
        db.save_poll_state(conn, PollState(repo_key="demo", etag='W/"abc"'))
    reader = FakeIssueReader([make_issue()], etag='W/"abc"')

    outcome = poll.poll_repo(
        conn, boundaries=make_boundaries(audit, reader=reader), audit=audit, config=config,
        repo_key="demo", dry_run=False,
    )

    assert reader.poll_calls == [("demo", 'W/"abc"', None)]
    assert (outcome.status, outcome.found) == (200, 1)
    state = db.get_poll_state(conn, "demo")
    assert (state.etag, state.etag_request) == ('W/"abc"', reader.request)
