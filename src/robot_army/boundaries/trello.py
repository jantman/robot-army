"""The Trello REST API, spoken directly over ``httpx`` (research.md R2).

**This is the only module that knows the Trello API exists.** Everything above it —
:mod:`robot_army.intake` in particular — deals in cards and lists, never in URLs, and
that split is the plan's Structure Decision rather than a stylistic preference: it is
what makes the effect-level wiring the single place a board write can be suppressed.

Two things here are load-bearing rather than incidental:

* **Credentials travel in the ``Authorization`` header, never the query string** (R3).
  Trello's own documentation puts ``?key=…&token=…`` on every request, and this project
  logs request targets. ``audit.py`` redacts by *field name*, so a secret embedded inside
  a URL under a key called ``url`` sails straight through the choke point that exists to
  catch it. The header form removes the hazard at its source; no log line in this module
  ever carries a full URL with a query string.
* **A transport failure raises** :class:`~robot_army.boundaries.TransportError`, reusing
  the GitHub boundary's exception rather than paralleling it. It must never be caught and
  turned into an empty card list: "no cards" and "I could not ask" are different facts,
  and conflating them is the silent failure Principle III forbids (FR-009).
"""

from __future__ import annotations

import random
import re
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from robot_army.boundaries import BoardInfo, Card, CardWriteResult, TransportError

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import Config

#: Statuses worth retrying. 429 is Trello's rate-limit shape (300 requests per 10 seconds
#: per key); 5xx is transient. 401 and 403 are *not* here — a bad credential does not get
#: better by being retried, and retrying it four times is four more chances to be
#: rate-limited for it.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Cap on a single backoff sleep, so honouring an implausible ``Retry-After`` cannot wedge
#: the daemon's whole tick loop.
_MAX_BACKOFF_SECONDS = 120.0

#: What a redacted credential looks like in a record. Matches ``audit.py``'s spelling, so
#: a reader meets one word rather than two for the same fact.
REDACTED = "<redacted>"

#: Everything from a ``?`` to the next whitespace or quote. Used by :func:`_scrub` on any
#: text that reaches a record, as the second line of R3's defence.
_QUERY = re.compile(r"\?[^\s'\"]*")


class TrelloCardReader:
    """Reads. Selected at **every** effect level (FR-038).

    There is no simulated counterpart anywhere in this module, and no level selects one, so
    a bug that tries to fake board reads fails to import rather than quietly returning
    fixtures.
    """

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

    @property
    def _trello(self) -> Any:
        trello = self._config.trello
        if trello is None:
            # Reaching here means something constructed a board client for an installation
            # that configured no board. That is a wiring bug, and it must be loud: the
            # alternative is a client that quietly returns nothing and looks like an empty
            # board, which is the exact conflation FR-009 forbids.
            raise TransportError("no [trello] section is configured; there is no board to read")
        return trello

    def _http(self) -> httpx.Client:
        if self._client is None:
            trello = self._trello
            self._client = httpx.Client(
                base_url=trello.api_base,
                timeout=httpx.Timeout(
                    connect=min(10.0, trello.timeout_seconds),
                    read=float(trello.timeout_seconds),
                    write=float(trello.timeout_seconds),
                    pool=float(trello.timeout_seconds),
                ),
                headers={"Accept": "application/json", "User-Agent": "robot-army/0.1"},
                follow_redirects=True,
            )
        return self._client

    def _secrets(self) -> tuple[str, str]:
        """The key and token, resolved at the moment they are needed.

        Read per request rather than baked into the client at construction, which is both
        what ``TrelloConfig`` promises — "never storing it in the config" — and what makes
        the authentication *testable*: a client injected by a test still authenticates,
        so the header below is exercised rather than assumed. A rotated credential also
        takes effect without a restart. At a 300-second poll this costs nothing.
        """
        trello = self._trello
        return trello.read_key(), trello.read_token()

    def _auth_header(self) -> dict[str, str]:
        """R3, and the single most dangerous difference from the GitHub client.

        Trello documents ``?key=…&token=…``, which would put both secrets into every
        logged URL and straight past ``audit.py``'s redaction — which is keyed on *field
        names* and cannot see inside a string called ``url``. The header form removes the
        hazard at its source rather than relying on a scrubber somebody must remember.
        """
        key, token = self._secrets()
        return {"Authorization": f'OAuth oauth_consumer_key="{key}", oauth_token="{token}"'}

    def _clean(self, text: str) -> str:
        """Strip query strings **and the credentials themselves** from anything recorded.

        The query-string half is R3's belt and braces: this client never puts a credential
        in a URL, but an error string is assembled by a library we do not control.

        The credential half closes the one path R3's first-line fix cannot reach — a remote
        that quotes back what it was sent, which rejection bodies do. This is **not** the
        value-matching redaction R3 rejected for ``audit.py``: that would have been a global
        rule scanning every field of every record, where issue bodies produce false
        positives. Here the scope is one boundary that knows exactly two secret values and
        records nothing that could legitimately contain them.
        """
        try:
            secrets = self._secrets()
        except Exception:  # noqa: BLE001 - unreadable credentials must not break redaction
            secrets = ()
        return _scrub(text, secrets)

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> httpx.Response:
        """One request with bounded exponential backoff and jitter (FR-008).

        **Every record written here names the method and the path only** — never a full
        URL, and never the parameters. That is not caution for its own sake: the path is
        what makes a failure diagnosable, and everything else is either uninteresting or
        the thing R3 exists to keep out of the log.
        """
        trello = self._trello
        attempts = trello.max_retries + 1
        target = f"{method} {path}"
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = self._http().request(
                    method, path, json=json_body, params=params, headers=self._auth_header()
                )
            except httpx.HTTPError as exc:
                last_error = exc
                self._audit.record(
                    "trello.retry",
                    outcome="error",
                    target=target,
                    detail={
                        "attempt": attempt,
                        "of": attempts,
                        "error_type": type(exc).__name__,
                        # str(exc) on an httpx error can carry the request URL. It never
                        # carries a query string here, because this client never puts one
                        # on a request — but the safe reading is not to find out.
                        "error": self._clean(str(exc)),
                    },
                )
                if attempt == attempts:
                    break
                self._sleep(self._backoff_delay(attempt, None))
                continue

            if response.status_code in _RETRY_STATUSES and attempt < attempts:
                delay = self._backoff_delay(attempt, response)
                self._audit.record(
                    "trello.retry",
                    outcome="error",
                    target=target,
                    detail={
                        "attempt": attempt,
                        "of": attempts,
                        "status": response.status_code,
                        "backoff_s": round(delay, 2),
                        "retry_after": response.headers.get("Retry-After"),
                    },
                )
                self._sleep(delay)
                continue

            if response.status_code == 404 and allow_404:
                return response
            if response.status_code >= 400:
                self._audit.record(
                    "trello.request",
                    outcome="error",
                    target=target,
                    detail={
                        "status": response.status_code,
                        "body": self._clean(response.text[:2000]),
                    },
                )
                raise TransportError(
                    f"{target} failed with HTTP {response.status_code}: "
                    f"{self._clean(response.text[:400])}"
                )
            return response

        raise TransportError(
            f"{target} failed after {attempts} attempts: {self._clean(str(last_error))}"
        )

    def _backoff_delay(self, attempt: int, response: httpx.Response | None) -> float:
        """Honour ``Retry-After``; otherwise exponential with jitter."""
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(float(retry_after), _MAX_BACKOFF_SECONDS)
                except ValueError:
                    pass
        base = min(2.0 ** (attempt - 1), _MAX_BACKOFF_SECONDS)
        return base + random.uniform(0, base * 0.25)  # noqa: S311 - jitter, not crypto

    # -- reads --------------------------------------------------------------

    def board_info(self) -> BoardInfo:
        """The board's identity, privacy, members, labels and lists.

        Three calls, made once per process at startup (R10). Checking rather than assuming
        is the whole point: an assumption written in a planning document does not revisit
        itself, and sharing the board should be a loud failure rather than a silent
        widening of who can queue work onto the author's machine.
        """
        board_id = self._trello.board_id
        path = f"/boards/{quote(board_id, safe='')}"
        board = self._request("GET", path, params={"fields": "name,prefs"}).json()
        members = self._request("GET", f"{path}/members", params={"fields": "id"}).json()
        labels = self._request("GET", f"{path}/labels", params={"fields": "name", "limit": 1000})
        lists = self._request("GET", f"{path}/lists", params={"fields": "name", "filter": "open"})

        info = BoardInfo(
            board_id=board_id,
            name=str(board.get("name") or ""),
            permission_level=str((board.get("prefs") or {}).get("permissionLevel") or ""),
            member_ids=tuple(str(m.get("id") or "") for m in members),
            # A label with no name is legal in Trello (the colour-only kind) and cannot be
            # named in configuration, so it is dropped rather than stored under "".
            labels={
                str(item.get("name")): str(item.get("id"))
                for item in labels.json()
                if item.get("name")
            },
            lists={str(item.get("name")): str(item.get("id")) for item in lists.json()},
        )
        self._audit.record(
            "trello.board.check",
            outcome="ok",
            entity_type="board",
            entity_id=board_id,
            detail={
                "name": info.name,
                "permission_level": info.permission_level,
                # Recorded, never gated on (FR-004a). Who else may see the author's own
                # private board is the author's decision.
                "member_ids": list(info.member_ids),
                "labels": sorted(info.labels),
                "lists": sorted(info.lists),
            },
        )
        return info

    def poll(self, board_id: str, label_id: str) -> list[Card]:
        """Every currently tagged, unarchived card. Not a delta (R13)."""
        response = self._request(
            "GET",
            f"/boards/{quote(board_id, safe='')}/cards",
            params={
                "filter": "open",
                "fields": "name,desc,url,idList,idLabels,dateLastActivity,closed",
                "limit": 1000,
            },
        )
        cards = [_card_from_json(payload, board_id) for payload in response.json()]
        # Filtering by label id rather than name is R11's second dividend: it is an
        # equality check rather than a string match, and it survives the label being
        # renamed halfway through a run.
        #
        # No audit record here. The **cycle** is what gets logged, by ``intake.poll_board``
        # — one aggregate record per pass, which is the Principle III exception the plan
        # enumerates. Recording it here as well would duplicate it, and recording it *only*
        # here would make the log depend on which reader implementation is in play.
        return [card for card in cards if label_id in card.label_ids]

    def get_card(self, card_id: str) -> Card | None:
        """One card as it is now. ``None`` for a card that has been deleted outright."""
        response = self._request(
            "GET",
            f"/cards/{quote(card_id, safe='')}",
            params={"fields": "name,desc,url,idList,idLabels,dateLastActivity,closed,idBoard"},
            allow_404=True,
        )
        if response.status_code == 404:
            return None
        payload = response.json()
        return _card_from_json(payload, str(payload.get("idBoard") or ""))

    def card_comments(self, card_id: str) -> list[str]:
        """Comment bodies, newest first. Exists only for R7's recovery path.

        Not called when a mapping row exists — that ordering is enforced by the caller, in
        ``intake.py``, with a test asserting this method is never reached in the normal
        case.
        """
        response = self._request(
            "GET",
            f"/cards/{quote(card_id, safe='')}/actions",
            params={"filter": "commentCard", "limit": 100},
        )
        return [
            str((action.get("data") or {}).get("text") or "") for action in response.json()
        ]


class TrelloCardWriter:
    """Writes to the board. Selected only at ``live`` (FR-039).

    Neither call decides whether it is *allowed*: the check against ``placed_list_id``
    (R12) is the caller's, because it is policy about the author's intent rather than a
    property of the transport.
    """

    def __init__(self, config: Config, audit: AuditLog, *, reader: TrelloCardReader | None = None):
        self._audit = audit
        self._reader = reader or TrelloCardReader(config, audit)

    def comment(self, card_id: str, body: str) -> CardWriteResult:
        with self._audit.action(
            "trello.card.comment",
            entity_type="card",
            entity_id=card_id,
            target=card_id,
            detail={"body_chars": len(body)},
        ) as outcome:
            response = self._reader._request(
                "POST",
                f"/cards/{quote(card_id, safe='')}/actions/comments",
                params={"text": body},
            )
            url = str(response.json().get("url") or "") or None
            outcome["comment_url"] = url
            return CardWriteResult(url=url, last_activity=self._refresh(card_id, outcome))

    def move(self, card_id: str, list_id: str) -> CardWriteResult:
        with self._audit.action(
            "trello.card.move",
            entity_type="card",
            entity_id=card_id,
            target=card_id,
            detail={"to_list": list_id},
        ) as outcome:
            self._reader._request(
                "PUT", f"/cards/{quote(card_id, safe='')}", params={"idList": list_id}
            )
            return CardWriteResult(url=None, last_activity=self._refresh(card_id, outcome))

    def _refresh(self, card_id: str, outcome: dict[str, Any]) -> str | None:
        """Re-read the card's activity stamp so the caller can rebase its baseline (R9).

        Our own write just changed ``dateLastActivity``, and that field is the rescan
        trigger. Without this the next poll sees an edit that nobody made, re-evaluates,
        and does it again forever. A failure to re-read is recorded and returns ``None``,
        which the caller treats as "leave the baseline alone" — one redundant
        re-evaluation is a far smaller problem than a write reported as not having
        happened.
        """
        try:
            card = self._reader.get_card(card_id)
        except TransportError as exc:
            outcome["activity_refresh_error"] = str(exc)
            return None
        return card.last_activity if card else None


class SimulatedCardWriter:
    """Logs the intended write with its full arguments and returns a valid result (FR-040).

    Returning ``None`` or raising would let the simulated path diverge from the real one at
    exactly the point the dry-run feature exists to rehearse — the caller would take a
    different branch, and what was tested would not be what runs.

    The returned ``last_activity`` is ``None`` rather than an invented timestamp: no write
    happened, so the card's real activity stamp did not change, and handing back a fake one
    would make the caller store a baseline that never matches the board.
    """

    def __init__(self, audit: AuditLog) -> None:
        self._audit = audit
        self._counter = 0

    def comment(self, card_id: str, body: str) -> CardWriteResult:
        self._counter += 1
        url = f"https://trello.com/c/{card_id}#comment-simulated-{self._counter}"
        self._audit.record(
            "trello.card.comment",
            outcome="ok",
            entity_type="card",
            entity_id=card_id,
            target=card_id,
            simulated=True,
            detail={"card_id": card_id, "body": body, "would_return": url},
        )
        return CardWriteResult(url=url, last_activity=None)

    def move(self, card_id: str, list_id: str) -> CardWriteResult:
        self._counter += 1
        self._audit.record(
            "trello.card.move",
            outcome="ok",
            entity_type="card",
            entity_id=card_id,
            target=card_id,
            simulated=True,
            detail={"card_id": card_id, "to_list": list_id},
        )
        return CardWriteResult(url=None, last_activity=None)


def _scrub(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove query strings and any of ``secrets`` from a message bound for a record.

    A free function so it can be exercised directly, and so the two rules live in one
    place rather than being repeated at each of the four sites that record text.
    """
    if "?" in text:
        text = _QUERY.sub("?<redacted>", text)
    for secret in secrets:
        # A short or empty value would match everything. It cannot happen — the config
        # refuses an empty credential — but "cannot happen" is not the standard either.
        if secret and len(secret) >= 8:
            text = text.replace(secret, REDACTED)
    return text


def _card_from_json(payload: dict[str, Any], board_id: str) -> Card:
    return Card(
        card_id=str(payload.get("id") or ""),
        board_id=board_id,
        url=str(payload.get("url") or ""),
        title=str(payload.get("name") or ""),
        body=str(payload.get("desc") or ""),
        label_ids=tuple(str(label) for label in payload.get("idLabels") or ()),
        list_id=str(payload.get("idList") or ""),
        # The string the API returned, unparsed: it is compared for equality against a
        # stored baseline and nothing else, and parsing it would invite a timezone bug
        # into a comparison that does not need one.
        last_activity=str(payload.get("dateLastActivity") or ""),
        closed=bool(payload.get("closed")),
    )
