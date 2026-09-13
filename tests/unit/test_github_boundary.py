"""``GitHubReader.get_repo``, the single-repository lookup added by milestone 005 (T015).

Separate from ``test_github.py`` — which covers polling, backoff and the refusal to
swallow a transport failure — because everything here is about one property: onboarding
costs **one** request no matter how many repositories the author owns (SC-009).
"""

from __future__ import annotations

import contextlib
import json

import httpx
import pytest

from robot_army.audit import read_records
from robot_army.boundaries import TransportError
from robot_army.boundaries.github import GitHubReader


def make_reader(config, audit, handler) -> GitHubReader:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
        headers={"Authorization": "Bearer x"},
    )
    return GitHubReader(config, audit, client=client, sleep=lambda _: None)


def repo_json(owner: str = "jantman", name: str = "demo") -> dict:
    return {
        "name": name,
        "full_name": f"{owner}/{name}",
        "owner": {"login": owner},
        "default_branch": "main",
    }


def test_an_owned_repository_reports_its_owner_and_canonical_name(config, audit):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=repo_json())

    info = make_reader(config, audit, handler).get_repo("jantman/demo")

    assert seen == ["/repos/jantman/demo"]
    assert info.exists
    assert info.owner == "jantman"
    assert info.name == "demo"
    assert info.full_name == "jantman/demo"
    assert info.default_branch == "main"


def test_a_repository_owned_by_someone_else_reports_that_owner(config, audit):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=repo_json(owner="someoneelse", name="theirs"))

    info = make_reader(config, audit, handler).get_repo("someoneelse/theirs")

    assert info.exists
    assert info.owner == "someoneelse"


def test_the_canonical_name_comes_back_even_when_the_key_differs_in_case(config, audit):
    """The third question one request answers. A case-mismatched name is otherwise
    diagnosed as a missing directory rather than as the typo it is (research R5)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=repo_json(name="Demo"))

    assert make_reader(config, audit, handler).get_repo("jantman/demo").name == "Demo"


def test_a_repository_that_does_not_exist_is_a_fact_not_a_failure(config, audit):
    """A 404 is returned rather than raised, so the allowlist can say "no such
    repository" differently from "you may not onboard that one"."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    info = make_reader(config, audit, handler).get_repo("jantman/never-existed")

    assert not info.exists
    assert info.owner == ""


def test_exactly_one_request_is_issued_and_it_is_not_a_page_walk(config, audit):
    """SC-009. A fake account with three repositories would pass an implementation that
    enumerates 252, so the assertion is on the *shape* of the traffic, not the answer."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=repo_json())

    make_reader(config, audit, handler).get_repo("jantman/demo")

    assert len(requests) == 1
    assert requests[0].url.path == "/repos/jantman/demo"
    assert "/user/repos" not in str(requests[0].url)
    assert "page" not in requests[0].url.params


# -- the github.get_repo record (issue #64) ----------------------------------


def records(layout, action: str) -> list[dict]:
    return [r for r, _ in read_records(layout.log_dir) if r and r["action"] == action]


def test_a_found_repository_writes_one_ok_record_with_method_path_and_status(
    config, audit, layout
):
    """The record the 005 quickstart's SC-009 check counts. Before issue #64 there was none,
    so the check printed 0 whatever onboarding did."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=repo_json())

    make_reader(config, audit, handler).get_repo("jantman/demo")

    [record] = records(layout, "github.get_repo")
    assert record["outcome"] == "ok"
    assert record["entity_type"] == "repo"
    assert record["entity_id"] == "jantman/demo"
    assert record["detail"] == {
        "method": "GET",
        "path": "/repos/jantman/demo",
        "status": 200,
        "exists": True,
    }


def test_a_missing_repository_is_recorded_as_an_ok_lookup_that_found_nothing(
    config, audit, layout
):
    """A 404 is the answer, not a failure; the refusal is ``repo.onboard``'s to record."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    make_reader(config, audit, handler).get_repo("jantman/typoed-nmae")

    [record] = records(layout, "github.get_repo")
    assert record["outcome"] == "ok"
    assert record["detail"]["path"] == "/repos/jantman/typoed-nmae"
    assert record["detail"]["status"] == 404
    assert record["detail"]["exists"] is False


def test_a_retried_lookup_is_still_one_lookup(config, audit, layout):
    """The record is per lookup, not per HTTP attempt; the attempt is ``github.retry``'s."""
    responses = iter([httpx.Response(503), httpx.Response(200, json=repo_json())])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    make_reader(config, audit, handler).get_repo("jantman/demo")

    [record] = records(layout, "github.get_repo")
    assert record["detail"]["status"] == 200
    assert len(records(layout, "github.retry")) == 1


def test_a_refused_lookup_writes_one_error_record_and_raises_unchanged(
    config, audit, layout
):
    """Without this record the lookups that failed would be the ones missing from the
    count. The exception is the same one, so onboarding's ``source_unreachable`` holds."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    with pytest.raises(TransportError) as raised:
        make_reader(config, audit, handler).get_repo("jantman/demo")

    assert raised.value.status == 401
    [record] = records(layout, "github.get_repo")
    assert record["outcome"] == "error"
    assert record["detail"]["method"] == "GET"
    assert record["detail"]["path"] == "/repos/jantman/demo"
    assert record["detail"]["status"] == 401
    assert record["detail"]["error_type"] == "TransportError"
    assert "HTTP 401" in record["detail"]["error"]
    assert "exists" not in record["detail"]
    # `_request`'s own failure record is unchanged and still written.
    assert len(records(layout, "github.request")) == 1


def test_a_lookup_that_stays_unavailable_records_its_final_status(config, audit, layout):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(TransportError):
        make_reader(config, audit, handler).get_repo("jantman/demo")

    [record] = records(layout, "github.get_repo")
    assert record["outcome"] == "error"
    assert record["detail"]["status"] == 503
    assert len(records(layout, "github.retry")) == config.github.max_retries


def test_a_lookup_that_never_connects_records_no_status(config, audit, layout):
    """No response arrived, so there is no status to report — ``None``, not a guess."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(TransportError) as raised:
        make_reader(config, audit, handler).get_repo("jantman/demo")

    assert raised.value.status is None
    [record] = records(layout, "github.get_repo")
    assert record["outcome"] == "error"
    assert record["detail"]["status"] is None
    assert record["detail"]["path"] == "/repos/jantman/demo"
    assert len(records(layout, "github.retry")) == config.github.max_retries + 1


@pytest.mark.parametrize("status", [200, 404, 401])
def test_the_record_carries_a_path_and_nothing_that_could_hold_a_credential(
    config, audit, layout, status
):
    """Path only (FR-005): no host, no query string, no header — so no token."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=repo_json())

    with contextlib.suppress(TransportError):
        make_reader(config, audit, handler).get_repo("jantman/demo")

    [record] = records(layout, "github.get_repo")
    text = json.dumps(record)
    assert "Bearer" not in text
    assert "Authorization" not in text
    assert "https://" not in text
    assert "?" not in text
