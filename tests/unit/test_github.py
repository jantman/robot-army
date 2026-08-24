"""The GitHub boundary: conditional requests, backoff, and refusing to swallow (T057).

Driven through an ``httpx.MockTransport`` rather than a hand-rolled fake, so the code
under test is the real client with the real header handling — which is the part the ETag
and rate-limit requirements actually live in.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from robot_army.boundaries import TransportError
from robot_army.boundaries.github import GitHubReader, GitHubWriter, SimulatedIssueWriter

ISSUE_JSON = {
    "number": 42,
    "title": "Fix the thing",
    "body": "Please fix it.",
    "html_url": "https://github.com/jantman/demo/issues/42",
    "labels": [{"name": "robot-army"}],
    "user": {"login": "jantman"},
    "state": "open",
}


def make_reader(config, audit, handler, *, sleeps: list[float] | None = None) -> GitHubReader:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
        headers={"Authorization": "Bearer x"},
    )
    recorder = (lambda seconds: sleeps.append(seconds)) if sleeps is not None else (lambda _: None)
    return GitHubReader(config, audit, client=client, sleep=recorder)


def test_poll_sends_if_none_match_and_reports_304_as_healthy(config, audit):
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("If-None-Match"))
        return httpx.Response(304, headers={"X-RateLimit-Remaining": "4999"})

    reader = make_reader(config, audit, handler)
    result = reader.poll("jantman/demo", 'W/"abc"')

    assert seen == ['W/"abc"']
    assert result.status == 304
    assert result.unchanged
    assert result.items == ()
    assert result.etag == 'W/"abc"', "the etag is carried forward, not cleared"
    assert result.rate_limit_remaining == 4999


def test_poll_parses_issues_and_captures_the_new_etag(config, audit):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["labels"] == "robot-army"
        assert request.url.params["state"] == "open"
        return httpx.Response(
            200, json=[ISSUE_JSON], headers={"ETag": 'W/"new"', "X-RateLimit-Remaining": "4998"}
        )

    reader = make_reader(config, audit, handler)
    result = reader.poll("jantman/demo", None)

    assert result.etag == 'W/"new"'
    assert len(result.items) == 1
    issue = result.items[0]
    assert (issue.number, issue.author, issue.labels) == (42, "jantman", ("robot-army",))


def test_pull_requests_in_the_issues_listing_are_ignored(config, audit):
    """The issues endpoint returns pull requests too; a PR is not work here."""
    pr = {**ISSUE_JSON, "number": 43, "pull_request": {"url": "..."}}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[ISSUE_JSON, pr])

    reader = make_reader(config, audit, handler)
    assert [i.number for i in reader.poll("jantman/demo", None).items] == [42]


def test_a_transport_failure_raises_rather_than_returning_empty(config, audit):
    """Conflating "I could not ask" with "nothing found" is the silent failure
    Principle III forbids — and it would make the daemon look idle during an outage."""
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("connection refused")

    reader = make_reader(config, audit, handler, sleeps=[])
    with pytest.raises(TransportError):
        reader.poll("jantman/demo", None)
    assert calls["n"] == config.github.max_retries + 1, "retries are bounded, and it retried"


def test_an_http_error_raises_with_the_body_for_diagnosis(config, audit):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    reader = make_reader(config, audit, handler)
    with pytest.raises(TransportError, match="404"):
        reader.poll("jantman/demo", None)


def test_retry_after_is_honoured(config, audit):
    sleeps: list[float] = []
    responses = [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, json=[]),
    ]

    def handler(_: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    reader = make_reader(config, audit, handler, sleeps=sleeps)
    reader.poll("jantman/demo", None)
    assert sleeps == [7.0]


def test_rate_limit_reset_is_honoured_when_the_budget_is_exhausted(config, audit):
    sleeps: list[float] = []
    reset = int(time.time()) + 30
    responses = [
        httpx.Response(
            403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)}
        ),
        httpx.Response(200, json=[]),
    ]

    def handler(_: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    reader = make_reader(config, audit, handler, sleeps=sleeps)
    reader.poll("jantman/demo", None)
    assert 25 <= sleeps[0] <= 31


def test_backoff_is_bounded_even_for_an_absurd_reset(config, audit):
    """Honouring a far-future reset literally would wedge the whole tick loop."""
    sleeps: list[float] = []
    reset = int(time.time()) + 86_400
    responses = [
        httpx.Response(
            403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)}
        ),
        httpx.Response(200, json=[]),
    ]

    def handler(_: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    reader = make_reader(config, audit, handler, sleeps=sleeps)
    reader.poll("jantman/demo", None)
    assert sleeps[0] <= 120.0


def test_exponential_backoff_grows_and_carries_jitter(config, audit):
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    reader = make_reader(config, audit, handler, sleeps=sleeps)
    with pytest.raises(TransportError):
        reader.poll("jantman/demo", None)
    assert len(sleeps) == config.github.max_retries
    assert sleeps == sorted(sleeps), "each wait is at least as long as the last"
    assert any(s != int(s) for s in sleeps), "jitter is applied"


def test_every_retry_is_logged_individually(config, audit, layout):
    """The plan's Principle III gap covers *successful reads* only; failures and retries
    are logged one by one."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    reader = make_reader(config, audit, handler, sleeps=[])
    with pytest.raises(TransportError):
        reader.poll("jantman/demo", None)
    audit.close()

    text = "\n".join(p.read_text(encoding="utf-8") for p in layout.log_dir.glob("*.jsonl"))
    retries = [json.loads(line) for line in text.splitlines() if '"github.retry"' in line]
    assert len(retries) == config.github.max_retries


def test_a_successful_poll_logs_one_aggregate_record_per_repository(config, audit, layout):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[ISSUE_JSON], headers={"ETag": 'W/"e"'})

    reader = make_reader(config, audit, handler)
    reader.poll("jantman/demo", None)
    audit.close()

    text = "\n".join(p.read_text(encoding="utf-8") for p in layout.log_dir.glob("*.jsonl"))
    polls = [json.loads(line) for line in text.splitlines() if '"github.poll"' in line]
    assert len(polls) == 1
    assert polls[0]["detail"]["items"] == 1
    assert polls[0]["detail"]["etag_hit"] is False


def test_is_closed_raises_rather_than_guessing_when_the_issue_is_gone(config, audit):
    """Saying "closed" on a failed lookup would move a work item to a terminal state on
    the strength of an error."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    reader = make_reader(config, audit, handler)
    with pytest.raises(TransportError):
        reader.is_closed("jantman/demo", 42)


def test_is_closed_reads_the_issue_state(config, audit):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**ISSUE_JSON, "state": "closed"})

    reader = make_reader(config, audit, handler)
    assert reader.is_closed("jantman/demo", 42) is True


def test_repo_keys_are_encoded_per_segment_not_wholesale(config, audit):
    """The owner/name separator must stay a real path separator while anything else in
    the segments is escaped. ``raw_path`` is checked because ``url.path`` decodes."""
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.split(b"?")[0])
        return httpx.Response(200, json=[])

    reader = make_reader(config, audit, handler)
    reader.poll("some org/weird repo", None)
    assert seen == [b"/repos/some%20org/weird%20repo/issues"]


def test_the_writer_posts_a_comment_and_returns_its_url(config, audit):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert json.loads(request.content)["body"] == "hello"
        return httpx.Response(201, json={"html_url": "https://example.invalid/c1"})

    writer = GitHubWriter(config, audit, reader=make_reader(config, audit, handler))
    assert writer.comment("jantman/demo", 42, "hello") == "https://example.invalid/c1"


def test_the_simulated_writer_returns_a_structurally_valid_handle(audit):
    """Returning ``None`` would let the simulated path diverge from the real one at
    exactly the point the dry-run feature exists to prevent."""
    writer = SimulatedIssueWriter(audit)
    url = writer.comment("jantman/demo", 42, "hello")
    assert isinstance(url, str)
    assert url.startswith("https://github.com/jantman/demo/issues/42#issuecomment-")


def test_the_simulated_writer_logs_the_full_intended_call(audit, layout):
    writer = SimulatedIssueWriter(audit)
    writer.comment("jantman/demo", 42, "the whole body")
    audit.close()
    text = "\n".join(p.read_text(encoding="utf-8") for p in layout.log_dir.glob("*.jsonl"))
    record = json.loads([line for line in text.splitlines() if "github.comment" in line][0])
    assert record["simulated"] is True
    assert record["detail"]["body"] == "the whole body"
