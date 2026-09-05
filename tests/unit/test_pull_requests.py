"""The pull-request boundary read (issue #143, contracts C1).

Driven through ``httpx.MockTransport`` like the rest of the GitHub boundary, so what is
under test is the real client building the real GraphQL request — which is where the two
arguments that keep merged pull requests in the answer actually live.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from robot_army.boundaries import TransportError
from robot_army.boundaries.github import GitHubReader


def make_reader(config, audit, handler) -> GitHubReader:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
        headers={"Authorization": "Bearer x"},
    )
    return GitHubReader(config, audit, client=client, sleep=lambda _: None)


def graphql(*, linked: list[dict] | None = None, branch: list[dict] | None = None, issue=...):
    """One ``data`` payload, with each half of the query set independently."""
    node = {} if issue is ... else issue
    if node is not None:
        node = {"closedByPullRequestsReferences": {"nodes": linked or []}}
    return {
        "data": {
            "repository": {
                "issue": node,
                "pullRequests": {"nodes": branch or []},
            }
        }
    }


def responder(payload, seen: list[dict] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(200, json=payload)

    return handler


PR_7 = {"number": 7, "url": "https://github.com/jantman/demo/pull/7", "state": "OPEN"}
PR_9 = {"number": 9, "url": "https://github.com/jantman/demo/pull/9", "state": "MERGED"}


def test_a_pull_request_found_by_the_branch_alone_is_returned(config, audit):
    reader = make_reader(config, audit, responder(graphql(branch=[PR_7])))

    found = reader.pull_requests_for("jantman/demo", 42, "robot-army/42")

    assert [(pr.number, pr.url, pr.state) for pr in found] == [
        (7, "https://github.com/jantman/demo/pull/7", "open")
    ]


def test_a_pull_request_found_by_the_issue_link_alone_is_returned(config, audit):
    """The half no REST endpoint answers, and the reason this read is GraphQL at all: a
    pull request may close the issue without ever having come from our branch."""
    reader = make_reader(config, audit, responder(graphql(linked=[PR_9])))

    found = reader.pull_requests_for("jantman/demo", 42, "robot-army/42")

    assert [(pr.number, pr.state) for pr in found] == [(9, "merged")]


def test_the_same_pull_request_found_by_both_routes_appears_once(config, audit):
    """The ordinary case rather than the odd one: a session opens the pull request from its
    branch *and* writes "Closes #42", so both halves of the query name it."""
    reader = make_reader(config, audit, responder(graphql(linked=[PR_7], branch=[PR_7])))

    found = reader.pull_requests_for("jantman/demo", 42, "robot-army/42")

    assert [pr.number for pr in found] == [7]


def test_the_result_is_sorted_by_number(config, audit):
    """Not cosmetic: the refresh detects "nothing changed" by comparing serialised text, so
    a set whose order wandered would look like a change on every pass."""
    reader = make_reader(config, audit, responder(graphql(linked=[PR_9], branch=[PR_7])))

    found = reader.pull_requests_for("jantman/demo", 42, "robot-army/42")

    assert [pr.number for pr in found] == [7, 9]


def test_every_state_is_lower_cased_and_an_unknown_one_is_passed_through(config, audit):
    """Normalised at the boundary so nothing above it sees GitHub's enum. A state outside
    the three we know is shown as GitHub spelled it rather than mapped to a guess."""
    odd = {"number": 11, "url": "https://github.com/jantman/demo/pull/11", "state": "DRAFT"}
    reader = make_reader(
        config, audit, responder(graphql(branch=[PR_7, PR_9, odd]))
    )

    found = reader.pull_requests_for("jantman/demo", 42, "robot-army/42")

    assert [pr.state for pr in found] == ["open", "merged", "draft"]


def test_a_node_missing_its_url_is_dropped(config, audit):
    """A pull request with no address cannot be linked, and half a row on the page is
    worse than none."""
    reader = make_reader(
        config,
        audit,
        responder(graphql(branch=[{"number": 8, "url": None, "state": "OPEN"}, PR_7])),
    )

    found = reader.pull_requests_for("jantman/demo", 42, "robot-army/42")

    assert [pr.number for pr in found] == [7]


def test_no_pull_request_anywhere_is_an_empty_list(config, audit):
    reader = make_reader(config, audit, responder(graphql()))

    assert reader.pull_requests_for("jantman/demo", 42, "robot-army/42") == []


def test_a_null_issue_without_errors_still_returns_the_branch_half(config, audit):
    """GitHub does not currently do this, which is exactly when a fallback earns its place.
    A missing issue node means the issue route contributed nothing — not that the branch
    route's answer should be thrown away."""
    reader = make_reader(config, audit, responder(graphql(issue=None, branch=[PR_7])))

    assert [pr.number for pr in reader.pull_requests_for("jantman/demo", 42, "b")] == [7]


def test_the_query_asks_for_merged_pull_requests_by_both_routes(config, audit):
    """The two arguments a reviewer cannot see the importance of. Drop either and the
    ordinary successful outcome — a merged pull request — silently vanishes from the
    interface, with no error anywhere."""
    seen: list[dict] = []
    reader = make_reader(config, audit, responder(graphql(), seen))

    reader.pull_requests_for("jantman/demo", 42, "robot-army/42")

    body = seen[0]
    assert "includeClosedPrs: true" in body["query"]
    assert "states: [OPEN, CLOSED, MERGED]" in body["query"]
    assert body["variables"] == {
        "owner": "jantman",
        "name": "demo",
        "number": 42,
        "branch": "robot-army/42",
    }


def test_a_transport_failure_raises_rather_than_returning_an_empty_list(config, audit):
    """The distinction the whole feature rests on. ``[]`` means GitHub answered and there
    are none; a failure must not be able to wear that answer's clothes."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    reader = make_reader(config, audit, handler)

    with pytest.raises(TransportError):
        reader.pull_requests_for("jantman/demo", 42, "robot-army/42")


def test_a_graphql_error_is_a_failure_and_is_logged_under_its_own_action(
    config, audit, layout: Path
):
    """A deleted issue answers HTTP 200 with data *and* errors. That is a failure — and it
    must not be logged as ``github.project.partial``, which would put a board-read failure
    in the log for a pull-request lookup and answer the wrong question later."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {"repository": {"issue": None}},
                "errors": [{"type": "NOT_FOUND", "message": "no such issue"}],
            },
        )

    reader = make_reader(config, audit, handler)

    with pytest.raises(TransportError):
        reader.pull_requests_for("jantman/demo", 99999, "robot-army/42")

    audit.close()
    actions = [
        json.loads(line)["action"]
        for path in sorted(layout.log_dir.glob("audit-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert "github.pull_requests.partial" in actions
    assert "github.project.partial" not in actions
