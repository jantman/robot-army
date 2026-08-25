"""GitHub REST, spoken directly over ``httpx`` (research.md R4).

The single technique that makes a 60-second poll sustainable is the conditional request:
an unchanged issue listing returns ``304`` and costs **zero** against the rate limit. A
high-level client library abstracts response headers away, which is exactly where ETags
and the rate-limit budget live — so the wrapper would obstruct the requirement it exists
to serve. Hence a thin hand-written client.

**A transport failure raises.** It must not be caught and turned into an empty result:
"no eligible work" and "I could not ask" are different facts, and conflating them is the
silent failure Principle III forbids.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from robot_army.boundaries import (
    Issue,
    PollResult,
    PullRequest,
    RepoRef,
    TransportError,
)

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import Config

#: Statuses worth retrying. 403 and 429 are the rate-limit shapes; 5xx is transient.
_RETRY_STATUSES = frozenset({403, 429, 500, 502, 503, 504})

#: Where simulated issue numbers start. High enough that one can never be mistaken for a
#: real issue in a log line, which is the only thing the value has to achieve.
SIMULATED_ISSUE_BASE = 900_000

#: Cap on a single backoff sleep, so honouring a far-future X-RateLimit-Reset cannot
#: wedge the daemon's whole tick loop for an hour.
_MAX_BACKOFF_SECONDS = 120.0


class GitHubReader:
    """Reads. Selected at **every** effect level (FR-052)."""

    def __init__(
        self,
        config: Config,
        audit: AuditLog,
        *,
        client: httpx.Client | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self._config = config
        self._audit = audit
        self._sleep = sleep
        self._client = client
        self._owns_client = client is None

    # -- plumbing -----------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self._client is None:
            gh = self._config.github
            self._client = httpx.Client(
                base_url=gh.api_base,
                # httpx makes the timeout explicit by construction, which is why it was
                # chosen over requests for a requirement that says every call MUST set one.
                timeout=httpx.Timeout(
                    connect=min(10.0, gh.timeout_seconds),
                    read=float(gh.timeout_seconds),
                    write=float(gh.timeout_seconds),
                    pool=float(gh.timeout_seconds),
                ),
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "robot-army/0.1",
                    "Authorization": f"Bearer {self._config.github.read_token()}",
                },
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> httpx.Response:
        """One request with bounded exponential backoff and jitter (FR-008)."""
        gh = self._config.github
        attempts = gh.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = self._http().request(
                    method, url, headers=headers, json=json_body, params=params
                )
            except httpx.HTTPError as exc:
                last_error = exc
                # Every retry is logged individually — the aggregate-logging exception
                # in the plan covers *successful* reads only.
                self._audit.record(
                    "github.retry",
                    outcome="error",
                    target=url,
                    detail={
                        "attempt": attempt,
                        "of": attempts,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                if attempt == attempts:
                    break
                self._sleep(self._backoff_delay(attempt, None))
                continue

            if response.status_code in _RETRY_STATUSES and attempt < attempts:
                delay = self._backoff_delay(attempt, response)
                self._audit.record(
                    "github.retry",
                    outcome="error",
                    target=url,
                    detail={
                        "attempt": attempt,
                        "of": attempts,
                        "status": response.status_code,
                        "backoff_s": round(delay, 2),
                        "rate_limit_remaining": response.headers.get("X-RateLimit-Remaining"),
                        "retry_after": response.headers.get("Retry-After"),
                    },
                )
                self._sleep(delay)
                continue

            if response.status_code == 404 and allow_404:
                return response
            if response.status_code >= 400 and response.status_code != 304:
                self._audit.record(
                    "github.request",
                    outcome="error",
                    target=url,
                    detail={"status": response.status_code, "body": response.text[:2000]},
                )
                raise TransportError(
                    f"{method} {url} failed with HTTP {response.status_code}: "
                    f"{response.text[:400]}"
                )
            return response

        raise TransportError(f"{method} {url} failed after {attempts} attempts: {last_error}")

    def _backoff_delay(self, attempt: int, response: httpx.Response | None) -> float:
        """Honour ``Retry-After`` and ``X-RateLimit-Reset``; otherwise exponential + jitter."""
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(float(retry_after), _MAX_BACKOFF_SECONDS)
                except ValueError:
                    pass
            remaining = response.headers.get("X-RateLimit-Remaining")
            reset = response.headers.get("X-RateLimit-Reset")
            if remaining == "0" and reset:
                try:
                    wait = float(reset) - time.time()
                    if wait > 0:
                        return min(wait, _MAX_BACKOFF_SECONDS)
                except ValueError:
                    pass
        base = min(2.0 ** (attempt - 1), _MAX_BACKOFF_SECONDS)
        return base + random.uniform(0, base * 0.25)  # noqa: S311 - jitter, not crypto

    def _repo_path(self, repo_key: str) -> str:
        """A repo key is ``owner/name``; percent-encode each segment, not the slash."""
        return "/".join(quote(part, safe="") for part in repo_key.split("/", 1))

    # -- reads --------------------------------------------------------------

    def poll(self, repo_key: str, etag: str | None) -> PollResult:
        """List open labelled issues, conditionally.

        A ``304`` returns ``items=()`` with ``status=304``. That is the healthy steady
        state, not an error and not "nothing found".
        """
        gh = self._config.github
        headers = {"If-None-Match": etag} if etag else {}
        response = self._request(
            "GET",
            f"/repos/{self._repo_path(repo_key)}/issues",
            headers=headers,
            params={
                "labels": gh.label,
                "state": "open",
                "per_page": 100,
                "sort": "updated",
                "direction": "desc",
            },
        )
        remaining = _int_header(response, "X-RateLimit-Remaining")
        reset = _int_header(response, "X-RateLimit-Reset")

        if response.status_code == 304:
            result = PollResult(
                items=(), etag=etag, status=304,
                rate_limit_remaining=remaining, rate_limit_reset=reset,
            )
        else:
            issues = tuple(
                _issue_from_json(payload)
                for payload in response.json()
                # The issues endpoint returns pull requests too; a PR is not work here.
                if "pull_request" not in payload
            )
            result = PollResult(
                items=issues,
                etag=response.headers.get("ETag", etag),
                status=response.status_code,
                rate_limit_remaining=remaining,
                rate_limit_reset=reset,
            )

        # One aggregate record per repository per poll, which is the Principle III gap
        # the plan enumerates and justifies. Every failure and retry is still individual.
        self._audit.record(
            "github.poll",
            outcome="ok",
            entity_type="repo",
            entity_id=repo_key,
            detail={
                "status": result.status,
                "etag_hit": result.unchanged,
                "items": len(result.items),
                "rate_limit_remaining": remaining,
            },
        )
        return result

    def get_issue(self, repo_key: str, number: int) -> Issue | None:
        response = self._request(
            "GET", f"/repos/{self._repo_path(repo_key)}/issues/{number}", allow_404=True
        )
        if response.status_code == 404:
            return None
        return _issue_from_json(response.json())

    def is_closed(self, repo_key: str, number: int) -> bool:
        issue = self.get_issue(repo_key, number)
        if issue is None:
            # A deleted or inaccessible issue is not "closed" — saying so would move a
            # work item to a terminal state on the strength of a failed lookup.
            raise TransportError(f"issue {repo_key}#{number} is not accessible")
        return issue.state == "closed"

    def open_pr_for_branch(self, repo_key: str, branch: str) -> PullRequest | None:
        owner = repo_key.split("/", 1)[0]
        response = self._request(
            "GET",
            f"/repos/{self._repo_path(repo_key)}/pulls",
            params={"state": "open", "head": f"{owner}:{branch}", "per_page": 1},
        )
        payloads = response.json()
        if not payloads:
            return None
        payload = payloads[0]
        return PullRequest(
            number=int(payload["number"]),
            url=str(payload["html_url"]),
            state=str(payload["state"]),
        )

    def list_issues_since(
        self, repo_key: str, since: str, *, author: str | None = None, limit: int = 100
    ) -> list[Issue]:
        """Issues in one repository created since ``since``, optionally by one author.

        This exists for milestone 003's crash recovery (R6), and the choice of endpoint is
        the whole point of it. GitHub's **search** index is eventually consistent — by
        minutes in the worst case — so an issue created two seconds before a crash may be
        invisible to it, which would produce precisely the duplicate the recovery exists to
        prevent. ``GET /repos/{owner}/{repo}/issues`` is immediately consistent, and
        bounding it by the intent timestamp keeps it cheap.

        ``since`` filters on *updated* time server-side, which is a superset of what we
        want; the created-time filter is applied here so the caller gets what it asked for.
        """
        response = self._request(
            "GET",
            f"/repos/{self._repo_path(repo_key)}/issues",
            params={
                "state": "all",
                "since": since,
                "per_page": min(limit, 100),
                "sort": "created",
                "direction": "desc",
            },
        )
        issues = []
        for payload in response.json():
            if "pull_request" in payload:
                continue
            issue = _issue_from_json(payload)
            if author is not None and issue.author != author:
                continue
            if str(payload.get("created_at") or "") < since:
                continue
            issues.append(issue)
        return issues

    def list_owned_repos(self) -> list[RepoRef]:
        refs: list[RepoRef] = []
        page = 1
        while True:
            response = self._request(
                "GET",
                "/user/repos",
                params={"affiliation": "owner", "per_page": 100, "page": page},
            )
            payloads = response.json()
            if not payloads:
                break
            refs.extend(
                RepoRef(
                    full_name=str(p["full_name"]),
                    default_branch=str(p.get("default_branch") or "main"),
                )
                for p in payloads
            )
            if len(payloads) < 100:
                break
            page += 1
        return refs


class GitHubWriter:
    """Writes. Selected only at ``live``."""

    def __init__(self, config: Config, audit: AuditLog, *, reader: GitHubReader | None = None):
        self._audit = audit
        self._reader = reader or GitHubReader(config, audit)

    def comment(self, repo_key: str, number: int, body: str) -> str:
        target = f"{repo_key}#{number}"
        with self._audit.action(
            "github.comment",
            entity_type="issue",
            entity_id=target,
            target=target,
            detail={"body_chars": len(body)},
        ) as outcome:
            response = self._reader._request(
                "POST",
                f"/repos/{self._reader._repo_path(repo_key)}/issues/{number}/comments",
                json_body={"body": body},
            )
            url = str(response.json().get("html_url", ""))
            outcome["comment_url"] = url
            return url


    def create_issue(self, repo_key: str, title: str, body: str) -> Issue:
        """File one issue and return it as GitHub reported it.

        **No label parameter, deliberately** (FR-015). The dispatch label is the human
        gate, and it is absent from the signature rather than defended by a rule: a caller
        that wanted to label the issue it just filed cannot express the wish.
        """
        with self._audit.action(
            "github.issue.create",
            entity_type="repo",
            entity_id=repo_key,
            target=repo_key,
            detail={"title": title, "body_chars": len(body)},
        ) as outcome:
            response = self._reader._request(
                "POST",
                f"/repos/{self._reader._repo_path(repo_key)}/issues",
                json_body={"title": title, "body": body},
            )
            issue = _issue_from_json(response.json())
            outcome["number"] = issue.number
            outcome["url"] = issue.url
            # Loud rather than silent: an issue arriving pre-labelled means something on
            # the far side applied it — a repository automation, say — and the human gate
            # has been bypassed by something this code cannot see.
            if issue.labels:
                outcome["unexpected_labels"] = list(issue.labels)
            return issue


class SimulatedIssueWriter:
    """Logs the intended write and returns a structurally valid fake handle.

    Returning ``None`` or raising would let the simulated path diverge from the real one
    at exactly the point the dry-run feature exists to prevent (contracts/boundaries.md).
    """

    def __init__(self, audit: AuditLog) -> None:
        self._audit = audit
        self._counter = 0

    def comment(self, repo_key: str, number: int, body: str) -> str:
        self._counter += 1
        target = f"{repo_key}#{number}"
        url = f"https://github.com/{repo_key}/issues/{number}#issuecomment-simulated-{self._counter}"
        self._audit.record(
            "github.comment",
            outcome="ok",
            entity_type="issue",
            entity_id=target,
            target=target,
            simulated=True,
            detail={"body": body, "would_return": url},
        )
        return url


    def create_issue(self, repo_key: str, title: str, body: str) -> Issue:
        """A structurally valid ``Issue`` with a recognisable fake number.

        Returning ``None`` or raising would let the simulated path diverge from the real
        one at exactly the point FR-015 exists to protect — the caller would take a
        different branch, and the dry run would stop rehearsing the thing being checked.

        The number is drawn from a fixed high offset so it is unmistakable in a log, and
        the row it produces is ``dry_run``, which is what keeps it out of listings and out
        of the live mapping.
        """
        self._counter += 1
        number = SIMULATED_ISSUE_BASE + self._counter
        url = f"https://github.com/{repo_key}/issues/{number}"
        self._audit.record(
            "github.issue.create",
            outcome="ok",
            entity_type="repo",
            entity_id=repo_key,
            target=repo_key,
            simulated=True,
            detail={"title": title, "body": body, "would_return": {"number": number, "url": url}},
        )
        return Issue(
            number=number,
            title=title,
            body=body,
            url=url,
            # Empty, like the real one: FR-015 is what the simulated path must rehearse.
            labels=(),
            author="robot-army-simulated",
            state="open",
        )


def _int_header(response: httpx.Response, name: str) -> int | None:
    value = response.headers.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _issue_from_json(payload: dict[str, Any]) -> Issue:
    return Issue(
        number=int(payload["number"]),
        title=str(payload.get("title") or ""),
        body=str(payload.get("body") or ""),
        url=str(payload.get("html_url") or ""),
        labels=tuple(
            str(label["name"]) if isinstance(label, dict) else str(label)
            for label in payload.get("labels", [])
        ),
        author=str((payload.get("user") or {}).get("login") or ""),
        state=str(payload.get("state") or "open"),
    )
