"""Milestone 009: what a request shows, and how that choice survives a click.

Two rules, and the interesting cases are all at their seam. **The effect level decides when
the operator has not** — below ``live`` every row is a simulated row, so hiding them by
default rendered an empty page describing a system that had found no work, which is also
exactly what a broken daemon looks like. **The operator's word overrides it in both
directions**, and has to keep overriding it after they navigate, which is why every generated
URL now states the value instead of omitting it when false.

The ``live`` half of every matrix below is a regression check, not a new claim: 009 changed
no default there, and the rest of the web suite runs at ``live`` for exactly that reason.
"""

from __future__ import annotations

import re

import pytest
from tests.conftest import seed_item

from robot_army.web.server import parse_request

LEVELS_BELOW_LIVE = ("plan", "local", "no-remote")
ALL_LEVELS = (*LEVELS_BELOW_LIVE, "live")


# -- the parse: three values, not two ----------------------------------------


@pytest.mark.parametrize("spelling", ["1", "true", "TRUE", "yes", "on"])
def test_every_truthy_spelling_is_a_yes(spelling: str) -> None:
    assert parse_request("GET", f"/queue?include_simulated={spelling}", {}).simulated_preference


@pytest.mark.parametrize("spelling", ["0", "false", "FALSE", "no", "off"])
def test_every_falsey_spelling_is_a_no(spelling: str) -> None:
    assert (
        parse_request("GET", f"/queue?include_simulated={spelling}", {}).simulated_preference
        is False
    )


@pytest.mark.parametrize("query", ["", "?include_simulated=", "?include_simulated=treu"])
def test_saying_nothing_and_saying_nonsense_are_both_unstated(query: str) -> None:
    """FR-004. Unrecognised folds into "unstated", not into "no", and never into a 400.

    This parameter is typed by hand on a phone. A typo should not produce an error page, and
    below ``live`` the forgiving direction is also the useful one — the rows appear.
    """
    assert parse_request("GET", f"/queue{query}", {}).simulated_preference is None


def test_a_mistyped_value_still_renders_a_page(web_at) -> None:
    response = web_at("plan").get("/queue?include_simulated=treu")
    assert response.status == 200


# -- the resolution matrix ---------------------------------------------------


def _shows_simulated(harness, conn, path: str = "/queue") -> bool:
    return 'class="sim"' in harness.get(path).text


@pytest.mark.parametrize("level", LEVELS_BELOW_LIVE)
def test_below_live_an_unstated_preference_shows_the_rows(web_at, conn, level: str) -> None:
    """The whole defect, in one assertion per level."""
    seed_item(conn, issue_number=26, dry_run=True, state="ready")
    assert _shows_simulated(web_at(level), conn)


def test_at_live_an_unstated_preference_still_hides_them(web_at, conn) -> None:
    """001's FR-019 is unchanged where it was never the problem."""
    seed_item(conn, issue_number=26, dry_run=True, state="ready")
    assert not _shows_simulated(web_at("live"), conn)


@pytest.mark.parametrize("level", ALL_LEVELS)
def test_a_stated_yes_wins_at_every_level(web_at, conn, level: str) -> None:
    seed_item(conn, issue_number=26, dry_run=True, state="ready")
    assert 'class="sim"' in web_at(level).get("/queue?include_simulated=1").text


@pytest.mark.parametrize("level", ALL_LEVELS)
def test_a_stated_no_wins_at_every_level(web_at, conn, level: str) -> None:
    """SC-007: the old behaviour stays reachable in a single request."""
    seed_item(conn, issue_number=26, dry_run=True, state="ready")
    assert 'class="sim"' not in web_at(level).get("/queue?include_simulated=0").text


@pytest.mark.parametrize(
    ("path", "denial"),
    [
        ("/active", "Nothing is running."),
        ("/queue", "Nothing is ready."),
        ("/interrupted", "Nothing is interrupted."),
    ],
)
def test_every_view_the_issue_named_stops_claiming_emptiness(
    web_at, conn, path: str, denial: str
) -> None:
    """Three of the four denials from the issue's table, at ``plan``.

    Each names its own section rather than the whole page: "Nothing is being prepared." is
    still true on a queue with nothing dispatching, and asserting it away would be asserting
    a lie.
    """
    for number, state in ((26, "active"), (27, "ready"), (28, "interrupted")):
        seed_item(conn, issue_number=number, dry_run=True, state=state)
    body = web_at("plan").get(path).text
    assert denial not in body, f"{path} still claims to be empty at plan"
    assert 'class="sim"' in body


# -- the preference survives the click ---------------------------------------

_HREF = re.compile(r'href="(/[^"]*)"')


@pytest.mark.parametrize("stated", ["0", "1"])
def test_every_generated_link_restates_the_preference(web_at, conn, stated: str) -> None:
    """FR-003, and the regression omission used to hide.

    Omitting the parameter when false was correct while false was also the default. Now that
    the default varies by level, omission means "use the default" — so an operator who
    deliberately hid the rows below ``live`` would have them back on the next click.
    """
    seed_item(conn, issue_number=26, dry_run=True, state="ready")
    body = web_at("plan").get(f"/queue?include_simulated={stated}").text
    internal = [href for href in _HREF.findall(body) if not href.startswith("/static")]
    assert internal, "the page generated no internal links to check"
    flipped = "1" if stated == "0" else "0"
    carrying = [href for href in internal if f"include_simulated={stated}" in href]
    toggling = [href for href in internal if f"include_simulated={flipped}" in href]
    # Every internal link states it. The ones stating the opposite are the deliberate ways
    # back — the visibility toggle in the chrome, and any "show them" in a withheld-row
    # disclosure — so they are counted, not excluded.
    assert len(carrying) + len(toggling) == len(internal), sorted(
        set(internal) - set(carrying) - set(toggling)
    )
    assert toggling, "no link offers the other direction"


@pytest.mark.parametrize("stated", ["0", "1"])
def test_every_form_restates_the_preference(web_at, conn, stated: str) -> None:
    seed_item(conn, issue_number=26, dry_run=True, state="ready")
    body = web_at("plan").get(f"/queue?include_simulated={stated}").text
    fields = re.findall(r'<input type="hidden" name="include_simulated" value="([01])"', body)
    assert fields, "the page generated no forms to check"
    assert set(fields) == {stated}


def test_the_redirect_after_an_action_carries_it_too(web_at, conn) -> None:
    """The ``303`` is a navigation like any other, and used to drop the preference."""
    seed_item(conn, issue_number=26, dry_run=True, state="ready")
    response = web_at("plan").post("/dispatch/pause", form={"include_simulated": "0"})
    assert response.status == 303
    assert "include_simulated=0" in response.headers["Location"]


def test_the_toggle_pill_offers_the_other_direction(web_at, conn) -> None:
    """R9: the issue's complaint was that nothing on the page suggested the override existed.

    Present in *both* states, because below ``live`` — where rows now show by default —
    nothing else on the page would point at the hidden view at all.
    """
    shown = web_at("plan").get("/queue").text
    assert '<a href="/queue?include_simulated=0" class="pill quiet">simulated rows included' in shown

    hidden = web_at("plan").get("/queue?include_simulated=0").text
    assert '<a href="/queue?include_simulated=1" class="pill quiet">simulated rows hidden' in hidden


# -- the payload agrees with the page ----------------------------------------


def test_the_json_reports_both_what_was_asked_and_what_was_served(web_at, conn) -> None:
    """FR-022. A consumer must be able to tell a deliberate choice from a default."""
    seed_item(conn, issue_number=26, dry_run=True, state="ready")

    unstated = web_at("plan").get_json("/queue").json()
    assert unstated["include_simulated"] is True
    assert unstated["simulated_preference"] is None
    assert unstated["effective_level"] == "plan"
    assert unstated["withheld_simulated"] == 0

    stated = web_at("plan").get_json("/queue?include_simulated=0").json()
    assert stated["include_simulated"] is False
    assert stated["simulated_preference"] is False
    assert stated["withheld_simulated"] == 1


def test_the_json_holds_the_same_rows_the_page_holds(web_at, conn) -> None:
    """FR-021, which follows from ``View`` assembling both from one payload."""
    seed_item(conn, issue_number=26, dry_run=True, state="ready")
    harness = web_at("plan")
    assert len(harness.get_json("/queue").json()["ready"]) == 1
    assert 'class="sim"' in harness.get("/queue").text


# -- the display default must not reach dispatch -----------------------------


def test_the_queue_order_is_the_same_whoever_is_looking(web_at, conn) -> None:
    """FR-005, SC-008.

    ``ordering.plan`` includes simulated rows unconditionally because a simulated row
    occupies a dispatch slot regardless of who is looking. Hiding a row must change what is
    *rendered* and nothing else — including the positions of the rows that remain.
    """
    seed_item(conn, issue_number=26, dry_run=False, state="ready")
    seed_item(conn, issue_number=27, dry_run=True, state="ready")
    harness = web_at("plan")
    shown = harness.get_json("/queue?include_simulated=1").json()["ready"]
    hidden = harness.get_json("/queue?include_simulated=0").json()["ready"]
    assert [row["position"] for row in shown] == [1, 2]
    # The real row keeps the position the dispatcher would give it, not the position it
    # would have if the simulated row had never existed.
    assert [row["position"] for row in hidden] == [row["position"] for row in shown if not row["simulated"]]


# -- the two views that accepted the preference and discarded it (issue #21) --
#
# `/anomalies` and `/log` were handed `include_simulated` by the router, threaded it through
# every link and form they rendered, and then ignored it when fetching their rows. The
# preference survived the click and changed nothing when it arrived.


def _anomaly(conn, entity_id: str, *, dry_run: bool) -> None:
    from robot_army import db

    with db.transaction(conn):
        db.raise_anomaly(
            conn,
            kind="card_create_failing",
            entity_type="card",
            entity_id=entity_id,
            detail={"attempts": 3},
            dry_run=dry_run,
        )


def test_the_anomalies_page_hides_rehearsed_rows_when_told_to(web_at, conn) -> None:
    _anomaly(conn, "card-real", dry_run=False)
    _anomaly(conn, "card-sim", dry_run=True)

    body = web_at("plan").get("/anomalies?include_simulated=0").text

    assert "card-real" in body
    assert "card-sim" not in body
    assert "1 simulated row hidden" in body


def test_the_anomalies_page_shows_them_when_told_to(web_at, conn) -> None:
    _anomaly(conn, "card-real", dry_run=False)
    _anomaly(conn, "card-sim", dry_run=True)

    body = web_at("plan").get("/anomalies?include_simulated=1").text

    assert "card-real" in body and "card-sim" in body
    # Not a bare "hidden": every form on the page carries an <input type="hidden"> restating
    # the preference, which is 009's doing and correct.
    assert "simulated row hidden" not in body


def test_the_anomalies_page_never_claims_nothing_outstanding_while_withholding(
    web_at, conn
) -> None:
    """The web half of the reported defect, in the direction that misleads.

    "Nothing outstanding." over two withheld rows is the page telling the reader the machine
    is clear when it is not.
    """
    _anomaly(conn, "card-sim", dry_run=True)

    body = web_at("plan").get("/anomalies?include_simulated=0").text

    assert "Nothing outstanding." not in body
    assert "1 simulated row" in body


def test_the_header_pill_agrees_with_the_page_it_links_to(web_at, conn) -> None:
    """The pill is rendered on every view and links to /anomalies.

    An unscoped count disagreed with its own destination the moment the toggle was off, which
    is one interface handing the reader two numbers for one question.
    """
    _anomaly(conn, "card-real", dry_run=False)
    _anomaly(conn, "card-sim", dry_run=True)
    harness = web_at("plan")

    for path in ("/anomalies", "/queue", "/cards", "/log"):
        hidden = harness.get(f"{path}?include_simulated=0")
        shown = harness.get(f"{path}?include_simulated=1")
        assert "1 anomaly" in hidden.text, path
        assert "2 anomalies" in shown.text, path


def test_the_anomalies_payload_states_what_it_withheld(web_at, conn) -> None:
    import json

    _anomaly(conn, "card-real", dry_run=False)
    _anomaly(conn, "card-sim", dry_run=True)

    payload = json.loads(web_at("plan").get("/anomalies.json?include_simulated=0").text)

    assert payload["count"] == 1
    assert payload["withheld_simulated"] == 1
    assert [a["entity_id"] for a in payload["anomalies"]] == ["card-real"]


def _write_log(layout, records: list[dict]) -> None:
    import json as _json
    from datetime import UTC, datetime

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    path = layout.log_dir / f"audit-{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with path.open("a", encoding="utf-8") as handle:
        for rec in records:
            handle.write(_json.dumps({"ts": stamp, "component": "daemon", **rec}) + "\n")


def test_the_log_page_hides_rehearsed_records_when_told_to(web_at, layout) -> None:
    harness = web_at("plan")
    _write_log(
        layout,
        [
            {"action": "real.thing", "outcome": "ok"},
            {"action": "rehearsed.thing", "outcome": "ok", "simulated": True},
        ],
    )

    body = harness.get("/log?include_simulated=0").text

    assert "real.thing" in body
    assert "rehearsed.thing" not in body
    assert "1 simulated record(s) on this page hidden" in body


def test_the_log_page_shows_them_when_told_to(web_at, layout) -> None:
    harness = web_at("plan")
    _write_log(
        layout,
        [
            {"action": "real.thing", "outcome": "ok"},
            {"action": "rehearsed.thing", "outcome": "ok", "dry_run": True},
        ],
    )

    body = harness.get("/log?include_simulated=1").text

    assert "real.thing" in body and "rehearsed.thing" in body
    assert "on this page hidden" not in body


def test_the_log_page_never_claims_no_records_match_while_withholding(web_at, layout) -> None:
    """The web half of US2's empty state, in the direction that misleads.

    "No records match." over a page of rehearsed records tells the reader nothing happened.
    """
    harness = web_at("plan")
    _write_log(layout, [{"action": "rehearsed.thing", "outcome": "ok", "simulated": True}])

    body = harness.get("/log?include_simulated=0").text

    assert "No records match." not in body
    assert "1 simulated record" in body


def test_the_log_page_states_the_withheld_count_once(web_at, layout) -> None:
    """A view discloses each withheld row exactly once — `withheld_note`'s own invariant.

    When the page has no visible records, the empty state already carries the count in place
    of its text, so the standalone paragraph must stand down. `anomalies_view` has always done
    this; `/log` printed the number twice for a page whose whole scanned window was rehearsed.
    """
    _write_log(
        layout,
        [
            {"action": "rehearsed.one", "outcome": "ok", "simulated": True},
            {"action": "rehearsed.two", "outcome": "ok", "simulated": True},
        ],
    )

    body = web_at("plan").get("/log?include_simulated=0").text

    assert body.count("2 simulated record") == 1, "the count is stated once, not twice"
    assert "Nothing to show here." in body


def test_the_anomalies_page_marks_the_rehearsed_rows_it_shows(web_at, conn) -> None:
    """FR-057: shown means marked, on every surface.

    The CLI writes `*` after the id. Without the same claim here, a rehearsed anomaly revealed
    by the toggle renders identically to a real one — the half of the defect that survives
    filtering, since the reader has asked to see both and can no longer tell them apart.
    """
    _anomaly(conn, "card-real", dry_run=False)
    _anomaly(conn, "card-sim", dry_run=True)

    body = web_at("plan").get("/anomalies?include_simulated=1").text

    assert body.count('class="sim"') == 1, "exactly one row carries the marker"
    # …and it is the rehearsed one. Each anomaly renders as its own `card` div, so the marker
    # has to fall inside the block that names `card-sim` rather than merely somewhere on a
    # page that happens to contain both.
    blocks = [block for block in body.split('class="card"') if "card_create_failing" in block]
    assert len(blocks) == 2
    rehearsed = next(b for b in blocks if "card-sim" in b)
    real = next(b for b in blocks if "card-real" in b)
    assert 'class="sim"' in rehearsed
    assert 'class="sim"' not in real
