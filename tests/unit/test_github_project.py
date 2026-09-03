"""Reading a GitHub project board (issue #48, T014, T018, T028).

The single most important thing in this file is the first section. A GraphQL failure
arrives as **HTTP 200** with an ``errors`` array, invisible to ``_request``, which raises
only on ``>= 400``. Untested, that degrades into an empty board — which is
indistinguishable from a board with nothing on it, so the system would quietly stop
ordering anything while reporting success.
"""

from __future__ import annotations

import httpx
import pytest

from robot_army.boundaries import TransportError
from robot_army.boundaries.github import NO_STATUS_COLUMN, GitHubReader


def make_reader(config, audit, handler) -> GitHubReader:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
        headers={"Authorization": "Bearer x"},
    )
    return GitHubReader(config, audit, client=client, sleep=lambda _: None)


def graphql(payload, *, status: int = 200):
    """A handler answering every GraphQL POST with one payload."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/graphql"
        return httpx.Response(status, json=payload)

    return handler


def sequence(*payloads):
    """A handler answering successive POSTs with successive payloads."""
    remaining = list(payloads)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=remaining.pop(0))

    return handler


def project_node(number: int = 3, *, options=("Backlog", "Ready", "Done"), field=True):
    node = {
        "id": f"PVT_{number}",
        "number": number,
        "title": "robot-army",
        "url": f"https://github.com/users/jantman/projects/{number}",
        "field": (
            {"id": "F1", "name": "Status", "options": [
                {"id": f"o{i}", "name": name} for i, name in enumerate(options)
            ]}
            if field
            else None
        ),
    }
    return node


def linked(*nodes):
    return {"data": {"repository": {"projectsV2": {"nodes": list(nodes)}}}}


def board(nodes, *, total=None, has_next=False, cursor=None, number=3):
    return {
        "data": {
            "node": {
                "number": number,
                "title": "robot-army",
                "url": "https://github.com/users/jantman/projects/3",
                "items": {
                    "totalCount": total if total is not None else len(nodes),
                    "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                    "nodes": nodes,
                },
            }
        }
    }


def issue_item(number: int, status: str | None, *, repo="jantman/demo", kind="ISSUE"):
    return {
        "type": kind,
        "content": {
            "number": number,
            "state": "OPEN",
            "repository": {"nameWithOwner": repo},
        },
        "fieldValueByName": {"name": status} if status is not None else None,
    }


# -- _graphql: a 200 is not a success (T014) --------------------------------


@pytest.mark.parametrize(
    "error_type",
    ["INSUFFICIENT_SCOPES", "FORBIDDEN", "NOT_FOUND"],
)
def test_a_200_carrying_errors_raises_rather_than_returning_nothing(
    config, audit, error_type
):
    """The whole feature rests on this. An empty board and an unreadable one look
    identical to everything downstream, so returning the first for the second would stop
    the ordering silently while every surface reported success."""
    reader = make_reader(
        config,
        audit,
        graphql({"data": None, "errors": [{"type": error_type, "message": "nope"}]}),
    )

    with pytest.raises(TransportError) as caught:
        reader._graphql("query {}", {})

    assert error_type in str(caught.value)


def test_errors_beside_usable_data_are_still_a_failure(config, audit):
    """A partly believed board is worse than no board: the missing half is invisible, so
    the order would be confidently wrong rather than absent."""
    reader = make_reader(
        config,
        audit,
        graphql(
            {
                "data": {"repository": {"projectsV2": {"nodes": []}}},
                "errors": [{"type": "FORBIDDEN", "message": "half of it"}],
            }
        ),
    )

    with pytest.raises(TransportError):
        reader._graphql("query {}", {})


def test_a_401_carries_no_errors_array_and_still_raises(config, audit):
    """Bad credentials come back REST-shaped, with no `errors` key at all."""
    reader = make_reader(
        config,
        audit,
        graphql({"message": "Bad credentials"}, status=401),
    )

    with pytest.raises(TransportError):
        reader._graphql("query {}", {})


def test_a_clean_response_returns_data(config, audit):
    reader = make_reader(config, audit, graphql({"data": {"viewer": {"login": "x"}}}))

    assert reader._graphql("query {}", {}) == {"viewer": {"login": "x"}}


def test_a_response_with_no_data_key_raises(config, audit):
    reader = make_reader(config, audit, graphql({}))

    with pytest.raises(TransportError):
        reader._graphql("query {}", {})


# -- discovery (T028) -------------------------------------------------------


def test_one_linked_project_resolves_with_no_configuration(config, audit):
    reader = make_reader(config, audit, graphql(linked(project_node())))

    resolution = reader.resolve_project("jantman/demo", project=None, column=None)

    assert resolution.resolved
    assert resolution.project_id == "PVT_3"
    assert resolution.column_name == "Ready"
    assert resolution.project_source == "discovered"
    assert resolution.column_source == "discovered"


def test_two_linked_projects_are_ambiguous_and_name_both(config, audit):
    """Picking one would choose the author's workflow for them, and they would find out
    by watching the wrong issue start."""
    reader = make_reader(
        config, audit, graphql(linked(project_node(3), project_node(4)))
    )

    resolution = reader.resolve_project("jantman/demo", project=None, column=None)

    assert not resolution.resolved
    assert "#3" in resolution.reason
    assert "#4" in resolution.reason
    assert resolution.candidates == ("#3 robot-army", "#4 robot-army")


def test_no_linked_project_is_reported_not_guessed(config, audit):
    reader = make_reader(config, audit, graphql(linked()))

    resolution = reader.resolve_project("jantman/demo", project=None, column=None)

    assert not resolution.resolved
    assert "no project is linked" in resolution.reason


@pytest.mark.parametrize("column", ["Ready", "Todo", "To do"])
def test_each_recognised_column_resolves_on_its_own(config, audit, column):
    reader = make_reader(
        config, audit, graphql(linked(project_node(options=("Backlog", column, "Done"))))
    )

    resolution = reader.resolve_project("jantman/demo", project=None, column=None)

    assert resolution.column_name == column


def test_a_board_offering_both_ready_and_todo_is_ambiguous(config, audit):
    reader = make_reader(
        config, audit, graphql(linked(project_node(options=("Ready", "Todo", "Done"))))
    )

    resolution = reader.resolve_project("jantman/demo", project=None, column=None)

    assert not resolution.resolved
    assert "more than one recognised" in resolution.reason


def test_a_board_offering_neither_is_reported_with_what_it_does_offer(config, audit):
    reader = make_reader(
        config, audit, graphql(linked(project_node(options=("Icebox", "Doing"))))
    )

    resolution = reader.resolve_project("jantman/demo", project=None, column=None)

    assert not resolution.resolved
    assert "Icebox" in resolution.reason and "Doing" in resolution.reason


def test_a_project_with_no_status_field_is_reported(config, audit):
    reader = make_reader(config, audit, graphql(linked(project_node(field=False))))

    resolution = reader.resolve_project("jantman/demo", project=None, column=None)

    assert not resolution.resolved
    assert "no single-select Status field" in resolution.reason


def test_a_configured_column_wins_over_the_recognised_one(config, audit):
    reader = make_reader(
        config, audit, graphql(linked(project_node(options=("Backlog", "Ready"))))
    )

    resolution = reader.resolve_project("jantman/demo", project=None, column="Backlog")

    assert resolution.column_name == "Backlog"
    assert resolution.column_source == "configured"


def test_column_matching_ignores_case_and_spacing(config, audit):
    reader = make_reader(
        config, audit, graphql(linked(project_node(options=("To Do", "Done"))))
    )

    resolution = reader.resolve_project("jantman/demo", project=None, column="to  do")

    assert resolution.column_name == "To Do"


def test_a_configured_column_absent_from_the_board_is_reported(config, audit):
    reader = make_reader(
        config, audit, graphql(linked(project_node(options=("Backlog", "Ready"))))
    )

    resolution = reader.resolve_project("jantman/demo", project=None, column="Queued")

    assert not resolution.resolved
    assert "Queued" in resolution.reason
    assert "Backlog, Ready" in resolution.reason


def test_a_configured_number_selects_among_the_linked_projects(config, audit):
    reader = make_reader(
        config, audit, graphql(linked(project_node(3), project_node(4)))
    )

    resolution = reader.resolve_project("jantman/demo", project="4", column=None)

    assert resolution.project_number == 4
    assert resolution.project_source == "configured"


def test_a_configured_number_that_is_not_linked_is_reported(config, audit):
    reader = make_reader(config, audit, graphql(linked(project_node(3))))

    resolution = reader.resolve_project("jantman/demo", project="9", column=None)

    assert not resolution.resolved
    assert "not linked" in resolution.reason


def test_a_configured_url_bypasses_the_repository_entirely(config, audit):
    """The URL form exists because discovery sees only *linked* projects, so an unlinked
    board can be named no other way."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content.decode())
        return httpx.Response(200, json={"data": {"owner": {"projectV2": project_node(7)}}})

    reader = make_reader(config, audit, handler)

    resolution = reader.resolve_project(
        "jantman/demo",
        project="https://github.com/users/jantman/projects/7",
        column=None,
    )

    assert resolution.project_number == 7
    assert "user(login" in seen[0]
    assert "projectsV2" not in seen[0]


def test_an_orgs_url_queries_the_organization(config, audit):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content.decode())
        return httpx.Response(200, json={"data": {"owner": {"projectV2": project_node(2)}}})

    reader = make_reader(config, audit, handler)

    reader.resolve_project(
        "acme/demo", project="https://github.com/orgs/acme/projects/2", column=None
    )

    assert "organization(login" in seen[0]


def test_a_url_naming_a_project_that_does_not_exist_is_reported(config, audit):
    reader = make_reader(config, audit, graphql({"data": {"owner": {"projectV2": None}}}))

    resolution = reader.resolve_project(
        "jantman/demo",
        project="https://github.com/users/jantman/projects/99",
        column=None,
    )

    assert not resolution.resolved
    assert "was not found" in resolution.reason


# -- read_board (T018) ------------------------------------------------------


def test_the_column_is_ranked_in_board_order(config, audit):
    reader = make_reader(
        config,
        audit,
        graphql(
            board([
                issue_item(48, "Ready"),
                issue_item(20, "Backlog"),
                issue_item(1, "Ready"),
                issue_item(41, "Ready"),
            ])
        ),
    )

    snapshot = reader.read_board("jantman/demo", project_id="PVT_3", column_name="Ready")

    assert [(e.issue_number, e.position) for e in snapshot.ranked] == [
        (48, 1),
        (1, 2),
        (41, 3),
    ]
    assert snapshot.elsewhere == {20: "Backlog"}


@pytest.mark.parametrize("kind", ["DRAFT_ISSUE", "PULL_REQUEST", "REDACTED"])
def test_non_issue_items_contribute_nothing(config, audit, kind):
    """REDACTED is the one that bites: its content is null, so a naive read crashes
    rather than skips."""
    redacted = {"type": kind, "content": None, "fieldValueByName": {"name": "Ready"}}
    reader = make_reader(
        config, audit, graphql(board([redacted, issue_item(1, "Ready")]))
    )

    snapshot = reader.read_board("jantman/demo", project_id="PVT_3", column_name="Ready")

    assert [e.issue_number for e in snapshot.ranked] == [1]


def test_another_repositorys_items_take_no_part(config, audit):
    """A project may span repositories; only this one's items take part in its order."""
    reader = make_reader(
        config,
        audit,
        graphql(
            board([
                issue_item(5, "Ready", repo="jantman/other"),
                issue_item(1, "Ready"),
            ])
        ),
    )

    snapshot = reader.read_board("jantman/demo", project_id="PVT_3", column_name="Ready")

    assert [(e.issue_number, e.position) for e in snapshot.ranked] == [(1, 1)]


def test_positions_are_dense_per_repository(config, audit):
    """A board shared by two repositories gives each its own 1..n rather than a sparse
    global count with gaps where the other repository's cards sat."""
    reader = make_reader(
        config,
        audit,
        graphql(
            board([
                issue_item(90, "Ready", repo="jantman/other"),
                issue_item(1, "Ready"),
                issue_item(91, "Ready", repo="jantman/other"),
                issue_item(2, "Ready"),
            ])
        ),
    )

    snapshot = reader.read_board("jantman/demo", project_id="PVT_3", column_name="Ready")

    assert [e.position for e in snapshot.ranked] == [1, 2]


def test_a_card_with_no_status_is_parked_rather_than_dispatchable(config, audit):
    """It is on the board and in no column. The author put it there and has not said
    where, which is a signal — not the absence of one."""
    reader = make_reader(config, audit, graphql(board([issue_item(1, None)])))

    snapshot = reader.read_board("jantman/demo", project_id="PVT_3", column_name="Ready")

    assert snapshot.ranked == ()
    assert snapshot.elsewhere == {1: NO_STATUS_COLUMN}


def test_order_survives_assembly_across_pages(config, audit):
    reader = make_reader(
        config,
        audit,
        sequence(
            board([issue_item(48, "Ready")], total=3, has_next=True, cursor="c1"),
            board([issue_item(1, "Ready"), issue_item(41, "Ready")], total=3),
        ),
    )

    snapshot = reader.read_board("jantman/demo", project_id="PVT_3", column_name="Ready")

    assert [e.issue_number for e in snapshot.ranked] == [48, 1, 41]
    assert snapshot.total_items == 3


def test_a_board_beyond_the_page_bound_raises_rather_than_truncating(config, audit):
    """A truncated board is not a partial answer. It is a wrong order that looks
    complete, because the cards that fell off the end become "not on the board" and jump
    the tail of the queue."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=board([issue_item(1, "Ready")], total=5000, has_next=True, cursor="c"),
        )

    reader = make_reader(config, audit, handler)

    with pytest.raises(TransportError) as caught:
        reader.read_board("jantman/demo", project_id="PVT_3", column_name="Ready")

    assert "truncated" in str(caught.value)


def test_the_read_is_recorded(config, audit, layout):
    reader = make_reader(config, audit, graphql(board([issue_item(1, "Ready")])))

    reader.read_board("jantman/demo", project_id="PVT_3", column_name="Ready")
    audit.flush() if hasattr(audit, "flush") else None

    written = "".join(
        path.read_text() for path in layout.log_dir.glob("*.jsonl")
    )
    assert "github.project.read" in written


# -- view sort conflicts (issue #48, review round five) ----------------------


def views(*sorts, layout="BOARD_LAYOUT"):
    return {
        "data": {
            "node": {
                "views": {
                    "nodes": [
                        {
                            "number": 1,
                            "name": "Backlog",
                            "layout": layout,
                            "sortByFields": {
                                "nodes": [
                                    {"direction": "ASC", "field": {"name": name}}
                                    for name in sorts
                                ]
                            },
                        }
                    ]
                }
            }
        }
    }


def field_values(*rows, has_next=False, cursor=None):
    """`rows` are (status, sort_value_or_None) pairs."""
    return {
        "data": {
            "node": {
                "items": {
                    "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                    "nodes": [
                        {
                            "type": "ISSUE",
                            "fieldValueByName": {"name": status},
                            "sortValue": {"name": value} if value else None,
                        }
                        for status, value in rows
                    ],
                }
            }
        }
    }


def test_a_view_sort_with_no_values_set_reports_nothing(config, audit):
    """The measured state of the author's own board: view 1 sorts by Priority and no card
    in Ready has one, so manual position is exactly what is displayed. A check that fired
    here would be a check nobody reads."""
    reader = make_reader(
        config,
        audit,
        sequence(views("Priority"), field_values(("Ready", None), ("Ready", None))),
    )

    assert reader.view_sort_conflicts(
        "jantman/demo", project_id="PVT_3", column_name="Ready"
    ) == ()


def test_a_sort_field_with_a_value_in_the_column_is_reported(config, audit):
    reader = make_reader(
        config,
        audit,
        sequence(views("Priority"), field_values(("Ready", None), ("Ready", "P0"))),
    )

    conflicts = reader.view_sort_conflicts(
        "jantman/demo", project_id="PVT_3", column_name="Ready"
    )

    assert len(conflicts) == 1
    assert "Priority" in conflicts[0]


def test_a_value_in_another_column_does_not_count(config, audit):
    """The sort only changes what the author sees in the column being dispatched from."""
    reader = make_reader(
        config,
        audit,
        sequence(views("Priority"), field_values(("Backlog", "P0"), ("Ready", None))),
    )

    assert reader.view_sort_conflicts(
        "jantman/demo", project_id="PVT_3", column_name="Ready"
    ) == ()


def test_the_column_is_matched_the_same_way_the_dispatcher_matches_it(config, audit):
    """Found in review, round five. The column used to be selected by a server-side
    `status:"…"` filter — GitHub's own matching — while every other part of the feature
    compares with `normalise_column`. A name the two disagreed about would have had
    `doctor` inspecting a different set of cards than the dispatcher orders."""
    reader = make_reader(
        config,
        audit,
        sequence(views("Priority"), field_values(("To Do", "P0"))),
    )

    conflicts = reader.view_sort_conflicts(
        "jantman/demo", project_id="PVT_3", column_name="to  do"
    )

    assert len(conflicts) == 1


def test_a_column_name_containing_a_quote_is_handled(config, audit):
    """It used to be interpolated into `status:"{name}"`, which a quote or a backslash
    broke — failing the check on a board that was otherwise healthy."""
    reader = make_reader(
        config,
        audit,
        sequence(views("Priority"), field_values(('Ready "now"', "P0"))),
    )

    conflicts = reader.view_sort_conflicts(
        "jantman/demo", project_id="PVT_3", column_name='Ready "now"'
    )

    assert len(conflicts) == 1


def test_a_table_view_sort_is_ignored(config, audit):
    """Only a board view's sort changes the order of cards in a column."""
    reader = make_reader(config, audit, graphql(views("Priority", layout="TABLE_LAYOUT")))

    assert reader.view_sort_conflicts(
        "jantman/demo", project_id="PVT_3", column_name="Ready"
    ) == ()


def test_an_unfinished_walk_raises_rather_than_reporting_no_conflict(config, audit):
    """A truncated read would report "no conflict" for a board it had not finished looking
    at, which is the reassuring kind of wrong."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if "views" in body:
            return httpx.Response(200, json=views("Priority"))
        return httpx.Response(
            200, json=field_values(("Backlog", None), has_next=True, cursor="c")
        )

    reader = make_reader(config, audit, handler)

    with pytest.raises(TransportError):
        reader.view_sort_conflicts(
            "jantman/demo", project_id="PVT_3", column_name="Ready"
        )
