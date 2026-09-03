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
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from robot_army.boundaries import (
    BoardEntry,
    BoardSnapshot,
    Issue,
    PollResult,
    ProjectAccess,
    ProjectResolution,
    PullRequest,
    RepoInfo,
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

#: Hard bound on how many 100-item pages one board read will fetch (issue #48).
#:
#: 2,000 items is far beyond any board this is for. Exceeding it **raises** rather than
#: truncating, and the difference matters: a truncated board is not a partial answer, it
#: is a *wrong order* that looks complete, because the cards that fell off the end would
#: silently become "not on the board" and jump the queue's tail.
_MAX_BOARD_PAGES = 20

#: What a card with no Status set is reported as. It is on the board and in no column,
#: which is parked rather than absent — the author put it there and has not said where.
NO_STATUS_COLUMN = "(no status)"

#: Discovery. ``repository.projectsV2`` is *linked* projects only — a filter, not a
#: superset — which is why a project the repository is not linked to can be named only by
#: URL. Each node carries its own Status options, so candidate selection and column
#: selection cost one request between them rather than one each.
_PROJECTS_FOR_REPO = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    projectsV2(first: 20) {
      nodes {
        id
        number
        title
        url
        field(name: "Status") {
          ... on ProjectV2SingleSelectField { id name options { id name } }
        }
      }
    }
  }
}
"""

#: A project named explicitly by URL, which carries its own owner and so cannot be found
#: through the repository at all when the two are not linked.
_PROJECT_BY_OWNER = """
query($login: String!, $number: Int!) {
  owner: %s(login: $login) {
    projectV2(number: $number) {
      id
      number
      title
      url
      field(name: "Status") {
        ... on ProjectV2SingleSelectField { id name options { id name } }
      }
    }
  }
}
"""

#: A capability probe rather than a scope guess: it asks for the one thing the feature
#: needs, so a token that answers it can do the job whatever its shape.
_VIEWER_PROJECTS = """
query { viewer { login projectsV2(first: 1) { totalCount } } }
"""

#: A project's views and what each one sorts by. ``sortByFields`` yields
#: ``ProjectV2SortByField``, whose ``field`` is the union — spreading the field types
#: directly on it is a schema error, which is the first thing this query got wrong.
_PROJECT_VIEWS = """
query($pid: ID!) {
  node(id: $pid) {
    ... on ProjectV2 {
      views(first: 20) {
        nodes {
          number
          name
          layout
          sortByFields(first: 5) {
            nodes {
              direction
              field {
                ... on ProjectV2Field { name }
                ... on ProjectV2SingleSelectField { name }
                ... on ProjectV2IterationField { name }
              }
            }
          }
        }
      }
    }
  }
}
"""

#: Whether a given field actually has a value on any card in one column. The server-side
#: ``query:`` filter keeps this to the column in question rather than the whole board.
_COLUMN_FIELD_VALUES = """
query($pid: ID!, $filter: String!, $field: String!) {
  node(id: $pid) {
    ... on ProjectV2 {
      items(first: 100, query: $filter) {
        nodes {
          type
          fieldValueByName(name: $field) {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
            ... on ProjectV2ItemFieldNumberValue { number }
            ... on ProjectV2ItemFieldDateValue { date }
            ... on ProjectV2ItemFieldTextValue { text }
            ... on ProjectV2ItemFieldIterationValue { title }
          }
        }
      }
    }
  }
}
"""

#: The board itself. Every item rather than the dispatch column alone, and that is
#: deliberate: ``items(query: 'status:"Ready"')`` would filter server-side and preserve
#: order, but it cannot distinguish "parked in Backlog" from "not on the board", and
#: FR-012 needs exactly that distinction. One unfiltered read answers both questions.
#:
#: ``orderBy`` is passed explicitly even though it matches the observed default, because
#: the default is undocumented and this feature is entirely about the order.
_BOARD_ITEMS = """
query($pid: ID!, $after: String) {
  node(id: $pid) {
    ... on ProjectV2 {
      number
      title
      url
      items(first: 100, after: $after, orderBy: {field: POSITION, direction: ASC}) {
        totalCount
        pageInfo { hasNextPage endCursor }
        nodes {
          type
          content {
            ... on Issue { number state repository { nameWithOwner } }
          }
          fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
        }
      }
    }
  }
}
"""


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

    def _graphql(self, document: str, variables: dict[str, Any]) -> dict[str, Any]:
        """POST one GraphQL document and return ``data``. Raises rather than hollowing out.

        **This must not be bypassed, and the reason is invisible from the call site.**
        ``_request`` raises only on HTTP ``>= 400``, and every GraphQL failure that
        matters here arrives as **HTTP 200 with an ``errors`` array**: a missing
        ``read:project`` scope (``INSUFFICIENT_SCOPES``), a project the token may not see
        (``FORBIDDEN``), a field name that no longer exists. Reading ``payload["data"]``
        directly would turn each of those into an empty board — indistinguishable from a
        board with nothing on it — and the system would quietly stop ordering anything
        while reporting success. That is the silent failure Principle III forbids, wearing
        a 200.

        A response carrying **both** data and errors is treated as failure too. A partly
        believed board is worse than no board: the half that is missing is invisible, so
        the order would be confidently wrong rather than absent.
        """
        response = self._request(
            "POST", "/graphql", json_body={"query": document, "variables": variables}
        )
        payload = response.json()
        errors = payload.get("errors")
        if errors:
            first = errors[0] if isinstance(errors, list) and errors else {}
            kind = first.get("type", "GRAPHQL_ERROR")
            message = first.get("message", "no message")
            self._audit.record(
                "github.project.partial" if payload.get("data") else "github.graphql",
                outcome="error",
                target="/graphql",
                detail={
                    "error_type": kind,
                    "errors": len(errors) if isinstance(errors, list) else 1,
                    "message": message[:400],
                    "had_data": payload.get("data") is not None,
                },
            )
            raise TransportError(f"GraphQL {kind}: {message[:400]}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise TransportError("GraphQL response carried no data")
        return data

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

    def resolve_project(
        self, repo_key: str, *, project: str | None, column: str | None
    ) -> ProjectResolution:
        """Which board and column govern this repository (issue #48, FR-011 to FR-018).

        Configured values win over discovery. An answer that is ambiguous or missing comes
        back as a resolution carrying ``reason`` rather than as an exception, because
        FR-023 requires the repository to keep dispatching under its configured order —
        a board nobody can identify is a reason to order the old way, not to stop.

        Every outcome is recorded, resolved or not. "Why is this repository not ordered by
        its board?" must be answerable from the log as well as from `status`, and a
        resolution that failed silently is the case where the log is the only witness.
        """
        return self._record_resolution(
            repo_key, self._resolve_project(repo_key, project=project, column=column)
        )

    def _record_resolution(
        self, repo_key: str, resolution: ProjectResolution
    ) -> ProjectResolution:
        self._audit.record(
            "github.project.discover",
            outcome="ok" if resolution.resolved else "error",
            entity_type="repo",
            entity_id=repo_key,
            detail={
                "project_number": resolution.project_number,
                "project_title": resolution.project_title,
                "project_source": resolution.project_source,
                "column": resolution.column_name,
                "column_source": resolution.column_source,
                "candidates": list(resolution.candidates),
                "reason": resolution.reason,
            },
        )
        return resolution

    def _resolve_project(
        self, repo_key: str, *, project: str | None, column: str | None
    ) -> ProjectResolution:
        """The decision itself. Split from :meth:`resolve_project` only so every one of
        its eight exits is recorded without eight copies of the recording call."""
        from robot_army.config import (
            RECOGNISED_DISPATCH_COLUMNS,
            normalise_column,
            parse_project_reference,
        )

        candidates: list[dict[str, Any]] = []
        chosen: dict[str, Any] | None = None
        project_source = "configured" if project else "discovered"

        reference = parse_project_reference(project) if project else None
        if project and reference is None:
            # The loader already refuses this shape, so reaching here means a value that
            # parsed at load time and does not now. Reported rather than guessed at.
            return ProjectResolution(reason=f"project {project!r} is not a number or a URL")

        if reference is not None and reference.login:
            owner_field = "organization" if reference.owner_type == "orgs" else "user"
            data = self._graphql(
                _PROJECT_BY_OWNER % owner_field,
                {"login": reference.login, "number": reference.number},
            )
            owner = data.get("owner") or {}
            chosen = owner.get("projectV2")
            if chosen is None:
                return ProjectResolution(
                    reason=(
                        f"configured project {project!r} was not found — no project "
                        f"number {reference.number} for {reference.login}"
                    )
                )
        else:
            owner_name, _, repo_name = repo_key.partition("/")
            data = self._graphql(
                _PROJECTS_FOR_REPO, {"owner": owner_name, "name": repo_name}
            )
            repository = data.get("repository") or {}
            candidates = [
                node
                for node in ((repository.get("projectsV2") or {}).get("nodes") or [])
                if node
            ]
            names = tuple(f"#{n.get('number')} {n.get('title')}" for n in candidates)
            if reference is not None:
                chosen = next(
                    (n for n in candidates if n.get("number") == reference.number), None
                )
                if chosen is None:
                    return ProjectResolution(
                        candidates=names,
                        reason=(
                            f"configured project {project!r} is not linked to {repo_key}; "
                            f"linked projects are {', '.join(names) or 'none'}"
                        ),
                    )
            elif len(candidates) == 1:
                chosen = candidates[0]
            elif not candidates:
                return ProjectResolution(
                    reason=f"no project is linked to {repo_key}"
                )
            else:
                # Two linked projects is not a tie to break. Picking one would choose the
                # author's workflow for them, and they would find out by watching the
                # wrong issue start (FR-018).
                return ProjectResolution(
                    candidates=names,
                    reason=(
                        f"{len(candidates)} projects are linked to {repo_key} "
                        f"({', '.join(names)}); set project in [repos.\"{repo_key}\"]"
                    ),
                )

        field = chosen.get("field") or {}
        options = [
            str(option.get("name", ""))
            for option in (field.get("options") or [])
            if option
        ]
        base = ProjectResolution(
            project_id=str(chosen.get("id")),
            project_number=chosen.get("number"),
            project_title=str(chosen.get("title") or ""),
            project_url=str(chosen.get("url") or ""),
            project_source=project_source,
            candidates=tuple(options),
        )
        if not options:
            return replace(
                base,
                reason=(
                    f"project #{base.project_number} has no single-select Status field, "
                    "so it has no columns to dispatch from"
                ),
            )

        if column:
            wanted = normalise_column(column)
            match = next((o for o in options if normalise_column(o) == wanted), None)
            if match is None:
                return replace(
                    base,
                    reason=(
                        f"configured column {column!r} is not on project "
                        f"#{base.project_number}; it offers {', '.join(options)}"
                    ),
                )
            return replace(base, column_name=match, column_source="configured")

        recognised = [o for o in options if normalise_column(o) in RECOGNISED_DISPATCH_COLUMNS]
        if len(recognised) == 1:
            return replace(base, column_name=recognised[0], column_source="discovered")

        if not recognised:
            return replace(
                base,
                reason=(
                    f"project #{base.project_number} has no recognised dispatch column "
                    f"(it offers {', '.join(options)}); set project_column in "
                    f'[repos."{repo_key}"]'
                ),
            )
        return replace(
            base,
            reason=(
                f"project #{base.project_number} offers more than one recognised dispatch "
                f"column ({', '.join(recognised)}); set project_column in "
                f'[repos."{repo_key}"]'
            ),
        )

    def project_access(self) -> ProjectAccess:
        """Whether these credentials can read projects, and which kind they are.

        Returns rather than raises, because "your token cannot do this" is a finding for
        ``doctor`` to report, not a crash. The probe asks for the capability itself rather
        than inspecting scopes and guessing, so a token that answers it can do the job
        whatever shape it has — and the scope header is used only to *explain* a failure.
        """
        try:
            response = self._request(
                "POST", "/graphql", json_body={"query": _VIEWER_PROJECTS, "variables": {}}
            )
        except TransportError as exc:
            return ProjectAccess(ok=False, credential_kind="unknown", detail=str(exc))
        raw = response.headers.get("x-oauth-scopes", "")
        scopes = tuple(part.strip() for part in raw.split(",") if part.strip())
        kind = "classic" if scopes else "fine-grained or app"
        payload = response.json()
        errors = payload.get("errors") or []
        if not errors and (payload.get("data") or {}).get("viewer"):
            return ProjectAccess(
                ok=True,
                credential_kind=kind,
                scopes=scopes,
                detail=f"{kind} token can read projects",
            )
        first = errors[0] if errors else {}
        error_type = first.get("type", "UNKNOWN")
        if kind == "classic":
            detail = (
                f"classic token cannot read projects ({error_type}); it holds "
                f"[{', '.join(scopes) or 'no scopes'}] and needs read:project"
            )
        else:
            # The wall worth naming explicitly. No amount of configuration fixes it.
            detail = (
                f"this looks like a fine-grained token or GitHub App ({error_type}). "
                "GitHub has no account-level Projects permission for fine-grained "
                "tokens, so one cannot read a user-owned board at all — use a classic "
                "token with read:project"
            )
        return ProjectAccess(ok=False, credential_kind=kind, scopes=scopes, detail=detail)

    def view_sort_conflicts(
        self, repo_key: str, *, project_id: str, column_name: str
    ) -> tuple[str, ...]:
        """Views whose sort would show an order this system cannot reproduce.

        Only where the sort field **has a value on a card in the dispatch column**. A view
        that sorts by a field nobody has filled in displays manual position unchanged, so
        warning about it would be noise — and a check that cries wolf is a check the
        author stops reading.
        """
        data = self._graphql(_PROJECT_VIEWS, {"pid": project_id})
        views = ((data.get("node") or {}).get("views") or {}).get("nodes") or []
        wanted: dict[str, list[str]] = {}
        for view in views:
            if not view or view.get("layout") != "BOARD_LAYOUT":
                continue
            for sort in (view.get("sortByFields") or {}).get("nodes") or []:
                name = ((sort or {}).get("field") or {}).get("name")
                if name:
                    wanted.setdefault(name, []).append(
                        f"#{view.get('number')} {view.get('name')!r}"
                    )
        if not wanted:
            return ()

        conflicts: list[str] = []
        for field_name, view_names in sorted(wanted.items()):
            values = self._graphql(
                _COLUMN_FIELD_VALUES,
                {
                    "pid": project_id,
                    "filter": f'status:"{column_name}"',
                    "field": field_name,
                },
            )
            nodes = ((values.get("node") or {}).get("items") or {}).get("nodes") or []
            if any(
                node and node.get("type") == "ISSUE" and node.get("fieldValueByName")
                for node in nodes
            ):
                conflicts.append(
                    f"view {' and '.join(view_names)} sorts by {field_name!r}, and cards "
                    f"in {column_name!r} have that field set — what you see there is not "
                    f"the order dispatched"
                )
        return tuple(conflicts)

    def read_board(
        self, repo_key: str, *, project_id: str, column_name: str
    ) -> BoardSnapshot:
        """Where this repository's issues sit on the board, in board order (issue #48).

        Raises rather than returning an empty snapshot on failure — see :meth:`_graphql`.
        """
        from robot_army.config import normalise_column

        wanted = normalise_column(column_name)
        ranked: list[BoardEntry] = []
        elsewhere: dict[int, str] = {}
        after: str | None = None
        total = 0
        project: dict[str, Any] = {}

        for page in range(1, _MAX_BOARD_PAGES + 1):
            data = self._graphql(_BOARD_ITEMS, {"pid": project_id, "after": after})
            project = data.get("node") or {}
            items = project.get("items") or {}
            total = int(items.get("totalCount") or 0)
            for node in items.get("nodes") or []:
                if not node or node.get("type") != "ISSUE":
                    # Draft issues, pull requests, and REDACTED items — content the token
                    # cannot see — contribute nothing. REDACTED is the one that bites:
                    # its `content` is null, so a naive read crashes rather than skips.
                    continue
                content = node.get("content") or {}
                number = content.get("number")
                owner = (content.get("repository") or {}).get("nameWithOwner")
                if number is None or owner != repo_key:
                    # A project may span repositories; only this one's items take part in
                    # its order (FR-011).
                    continue
                value = node.get("fieldValueByName") or {}
                status = str(value.get("name") or "") or NO_STATUS_COLUMN
                if normalise_column(status) == wanted:
                    # Dense per repository: the position is this repository's rank in the
                    # column, not the card's index in a project it may share.
                    ranked.append(
                        BoardEntry(
                            issue_number=int(number),
                            repo_key=repo_key,
                            position=len(ranked) + 1,
                        )
                    )
                else:
                    elsewhere[int(number)] = status
            info = items.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            after = info.get("endCursor")
            if page == _MAX_BOARD_PAGES:
                raise TransportError(
                    f"project board for {repo_key} exceeds {_MAX_BOARD_PAGES} pages "
                    f"({total} items); refusing to dispatch from a truncated order"
                )

        snapshot = BoardSnapshot(
            project_id=project_id,
            project_number=int(project.get("number") or 0),
            project_title=str(project.get("title") or ""),
            project_url=str(project.get("url") or ""),
            column_name=column_name,
            ranked=tuple(ranked),
            elsewhere=elsewhere,
            total_items=total,
        )
        self._audit.record(
            "github.project.read",
            outcome="ok",
            entity_type="repo",
            entity_id=repo_key,
            detail={
                "project_id": project_id,
                "column": column_name,
                "ranked": len(ranked),
                "elsewhere": len(elsewhere),
                "total_items": total,
            },
        )
        return snapshot

    def get_repo(self, repo_key: str) -> RepoInfo:
        """One repository, in **one** request (research R5, SC-009).

        A page walk over ``/user/repos`` would answer the same ownership question and cost
        three requests today and more later, for no additional information. This costs one
        regardless of how many repositories the author owns, and the same response carries
        the canonical name — which matters because a case-mismatched name would otherwise
        surface as a missing directory rather than as the typo it is.
        """
        response = self._request(
            "GET", f"/repos/{self._repo_path(repo_key)}", allow_404=True
        )
        if response.status_code == 404:
            return RepoInfo(exists=False)
        payload = response.json()
        owner = payload.get("owner") or {}
        return RepoInfo(
            exists=True,
            owner=str(owner.get("login") or ""),
            name=str(payload.get("name") or ""),
            default_branch=str(payload.get("default_branch") or "main"),
        )


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
