"""``GitHubReader.get_repo``, the single-repository lookup added by milestone 005 (T015).

Separate from ``test_github.py`` — which covers polling, backoff and the refusal to
swallow a transport failure — because everything here is about one property: onboarding
costs **one** request no matter how many repositories the author owns (SC-009).
"""

from __future__ import annotations

import httpx

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
