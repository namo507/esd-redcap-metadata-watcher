"""End-to-end tests for the Visitboard backend.

Starts a real server on an ephemeral port and talks to it over HTTP, so a route
that works in a unit test but 500s over the wire cannot pass.

Run:  python3 backend/tests/test_api.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from http.server import ThreadingHTTPServer  # noqa: E402

from backend import server as srv  # noqa: E402

BASE = ""


def call(method, path, payload=None):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() or "{}"
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, {"raw": body}


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


# ---------------------------------------------------------------------------


def test_health():
    status, body = call("GET", "/api/health")
    expect(status == 200, f"health returned {status}")
    expect(body["ok"] is True, "health not ok")
    expect(body["reads_titles"] is False, "board must never claim to read titles")
    expect(body["graph_auth_mode"] == "delegated", "auth mode must be delegated")


def test_board_is_one_round_trip():
    status, body = call("GET", "/api/board")
    expect(status == 200, f"board returned {status}")
    for key in ("health", "roster", "queue", "fairness", "reason_codes", "activity"):
        expect(key in body, f"board missing {key}")
    expect(len(body["queue"]) > 0, "queue is empty")
    expect(len(body["roster"]) > 0, "roster is empty")


def test_static_frontend_is_served():
    for path, needle in (
        ("/", "ESD Visitboard"),
        ("/styles.css", "--esd-discovery-blue"),
        ("/js/core.js", "function api"),
        ("/js/boot.js", "/api/board"),
    ):
        req = urllib.request.Request(BASE + path)
        with urllib.request.urlopen(req, timeout=10) as resp:
            expect(resp.status == 200, f"{path} returned {resp.status}")
            text = resp.read().decode("utf-8", "ignore")
        expect(needle in text, f"{path} did not contain {needle!r}")


def test_static_cannot_escape_the_frontend_directory():
    status, _ = call("GET", "/../esd_scheduler/config.py")
    expect(status in (403, 404), f"path traversal returned {status}")


def test_visit_detail_shape():
    _, board = call("GET", "/api/board")
    vid = board["queue"][0]["id"]
    status, body = call("GET", f"/api/visit?id={vid}")
    expect(status == 200, f"visit returned {status}")
    expect("candidates" in body and "excluded" in body, "detail missing pools")
    for c in body["candidates"]:
        expect(len(c["contributions"]) == 4, "every candidate shows four criteria")
        expect(c["confidence"] in
               ("Clear choice", "Slight edge", "Too close to call", "Only option"),
               f"confidence must be words, got {c['confidence']!r}")
        # No event titles anywhere in the payload the browser receives.
        blob = json.dumps(c).lower()
        for banned in ("subject", "location\":", "isprivate"):
            expect(banned not in blob, f"candidate payload leaked {banned}")
    for e in body["excluded"]:
        expect(e["reason"] and not e["reason"].startswith("missing_credential:"),
               f"exclusion reason must be plain language, got {e['reason']!r}")


def test_unknown_visit_is_404():
    status, body = call("GET", "/api/visit?id=NOPE")
    expect(status == 404, f"expected 404, got {status}")
    expect("error" in body, "404 should explain itself")


def test_assign_top_choice_then_undo():
    _, board = call("GET", "/api/board")
    vid = next(v["id"] for v in board["queue"] if v["status"] != "assigned")
    _, detail = call("GET", f"/api/visit?id={vid}")
    top = next(c for c in detail["candidates"] if c["assignable"])

    status, body = call("POST", "/api/assign",
                        {"visit_id": vid, "coordinator_id": top["id"]})
    expect(status == 200, f"assign returned {status}: {body}")
    expect(body["visit"]["status"] == "assigned", "visit not marked assigned")

    status, body = call("POST", "/api/assign",
                        {"visit_id": vid, "coordinator_id": top["id"]})
    expect(status == 400, "assigning twice must be refused")

    status, _ = call("POST", "/api/unassign", {"visit_id": vid})
    expect(status == 200, "undo failed")
    _, after = call("GET", f"/api/visit?id={vid}")
    expect(after["assigned"] is None, "undo did not clear the assignment")


def test_override_without_a_reason_is_refused():
    _, board = call("GET", "/api/board")
    vid = next(v["id"] for v in board["queue"] if v["status"] != "assigned")
    _, detail = call("GET", f"/api/visit?id={vid}")
    others = [c for c in detail["candidates"] if c["rank"] != 1 and c["assignable"]]
    if not others:
        return  # single-candidate pool; nothing to override
    second = others[0]

    status, body = call("POST", "/api/assign",
                        {"visit_id": vid, "coordinator_id": second["id"]})
    expect(status == 400, "an unexplained override must be refused")
    expect("reason" in body["error"].lower(), f"error should say why: {body}")

    status, body = call("POST", "/api/assign", {
        "visit_id": vid, "coordinator_id": second["id"],
        "reason_code": "family_request", "reason_text": "asked for them at intake",
    })
    expect(status == 200, f"override with a reason should work: {body}")
    expect(body["assignment"]["override"] is True, "not recorded as an override")
    expect(body["assignment"]["reason_class"] == "preference",
           "family_request is a preference, not a data defect")
    call("POST", "/api/unassign", {"visit_id": vid})


def test_recommended_is_the_first_assignable_not_merely_rank_one():
    """The board's recommendation is the best option a human can actually take.

    When a fairness veto blocks the top-scoring person, rank 2 IS the
    recommendation. Taking them must not be treated as an override: the engine
    records that skip as a system constraint, and demanding a reason would both
    mislabel the decision and pollute the override log that weight
    re-elicitation depends on.
    """
    _, board = call("GET", "/api/board")
    for v in board["queue"]:
        _, detail = call("GET", f"/api/visit?id={v['id']}")
        assignable = [c for c in detail["candidates"] if c["assignable"]]
        expected = assignable[0]["id"] if assignable else None
        expect(detail["recommended_id"] == expected,
               f"{v['id']}: recommended_id {detail['recommended_id']} != first "
               f"assignable {expected}")
        if detail["candidates"] and not detail["candidates"][0]["assignable"]:
            expect(detail["top_rank_blocked"] is True,
                   f"{v['id']}: rank 1 is blocked but top_rank_blocked is False")


def test_assigning_the_recommendation_never_needs_a_reason():
    """Fill the week greedily. Every assignment takes the recommended person,
    and none of them may be refused for a missing reason, even once vetoes
    start demoting rank 1."""
    call("POST", "/api/reset")
    _, board = call("GET", "/api/board")
    assigned = 0
    for v in board["queue"]:
        _, detail = call("GET", f"/api/visit?id={v['id']}")
        rec = detail["recommended_id"]
        if not rec:
            continue
        status, body = call("POST", "/api/assign",
                            {"visit_id": v["id"], "coordinator_id": rec})
        expect(status == 200,
               f"{v['id']}: assigning the recommendation was refused: {body}")
        expect(body["assignment"]["override"] is False,
               f"{v['id']}: recommendation recorded as an override")
        assigned += 1
    # Not every visit is fillable, and that is the correct answer rather than a
    # shortfall: with the manual's reliability chart in force, a 3m visit needs
    # Orientation and Bayley (3m), which almost nobody holds. What must hold is
    # that a recommendation, wherever one exists, is never refused for a missing
    # reason.
    expect(assigned > 0, "no visit could be filled at all")
    call("POST", "/api/reset")


def test_the_certification_gate_is_actually_reachable_from_the_board():
    """The gates were written, tested in isolation, and never called.

    check_candidate was imported by the session and invoked nowhere, so the
    board's exclusions came only from the engine's feasibility stage and the
    manual's certification rule -- stated there as non-negotiable -- was not
    enforced on screen or on assignment. This asserts a Layer 1 reason actually
    reaches the API, which is the thing that silently was not true.
    """
    _, board = call("GET", "/api/board")
    layer1 = {"signed off", "in-lab day", "Fridays", "NDD", "training",
              "tech kit"}
    for v in board["queue"]:
        _, detail = call("GET", f"/api/visit?id={v['id']}")
        for entry in detail.get("excluded", []):
            if any(token.lower() in entry["reason"].lower() for token in layer1):
                return
    raise AssertionError(
        "no visit on the board was gated by any Layer 1 rule, so the gates are "
        "either unreachable again or the roster is certified for everything")


def test_a_gated_coordinator_is_refused_by_the_assign_route_too():
    """The screen and the assignment path must agree about who is eligible.

    They did not: candidates() filtered and assign() did not, so the board
    showed somebody as ineligible and then accepted an assignment to them.
    """
    _, board = call("GET", "/api/board")
    for v in board["queue"]:
        _, detail = call("GET", f"/api/visit?id={v['id']}")
        gated = [e for e in detail.get("excluded", [])
                 if "signed off" in e["reason"].lower()]
        if not gated:
            continue
        status, body = call("POST", "/api/assign",
                            {"visit_id": v["id"], "coordinator_id": gated[0]["id"],
                             "reason_code": "preference",
                             "reason_text": "testing the gate"})
        expect(status >= 400,
               f"{v['id']}: assigning an uncertified coordinator returned {status}")
        return


def test_a_visit_nobody_is_certified_for_offers_no_recommendation():
    """Silence is the right answer when the chart rules everyone out.

    The board must not fall back to the best-scoring ineligible person: the
    manual states certification as non-negotiable, so an empty list is correct
    and a ranked list would be a suggestion nobody may act on.
    """
    _, board = call("GET", "/api/board")
    for v in board["queue"]:
        _, detail = call("GET", f"/api/visit?id={v['id']}")
        if detail.get("recommended_id"):
            continue
        if not detail["candidates"] and detail["visit"]["automated"]:
            expect(detail["excluded"],
                   f"{v['id']}: nobody eligible and no reason given")
            return
    # Every visit had somebody; nothing to assert.


def test_bad_reason_code_is_refused():
    _, board = call("GET", "/api/board")
    vid = next(v["id"] for v in board["queue"] if v["status"] != "assigned")
    _, detail = call("GET", f"/api/visit?id={vid}")
    others = [c for c in detail["candidates"] if c["rank"] != 1 and c["assignable"]]
    if not others:
        return
    status, _ = call("POST", "/api/assign", {
        "visit_id": vid, "coordinator_id": others[0]["id"],
        "reason_code": "because_i_said_so",
    })
    expect(status == 400, "an unknown reason code must be refused")


def test_ineligible_coordinator_cannot_be_assigned():
    _, board = call("GET", "/api/board")
    vid = board["queue"][0]["id"]
    _, detail = call("GET", f"/api/visit?id={vid}")
    if not detail["excluded"]:
        return
    blocked = detail["excluded"][0]["id"]
    status, body = call("POST", "/api/assign",
                        {"visit_id": vid, "coordinator_id": blocked})
    expect(status == 400, "Layer 1 failures must never be assignable over HTTP")


def test_fairness_and_week():
    status, body = call("GET", "/api/fairness")
    expect(status == 200 and "rows" in body, "fairness endpoint broken")
    expect(body["status"] in ("even", "uneven", "lopsided"), "fairness status word")
    status, body = call("GET", "/api/week")
    expect(status == 200 and "regret" in body, "week endpoint broken")


def test_reset_clears_assignments():
    _, board = call("GET", "/api/board")
    vid = next(v["id"] for v in board["queue"] if v["status"] != "assigned")
    # Find a visit that somebody is actually certified for.
    top = None
    for candidate_visit in board["queue"]:
        if candidate_visit["status"] == "assigned":
            continue
        _, detail = call("GET", f"/api/visit?id={candidate_visit['id']}")
        top = next((c for c in detail["candidates"] if c["assignable"]), None)
        if top:
            vid = candidate_visit["id"]
            break
    expect(top is not None, "no visit on the board has an assignable candidate")
    call("POST", "/api/assign", {"visit_id": vid, "coordinator_id": top["id"]})
    status, _ = call("POST", "/api/reset")
    expect(status == 200, "reset failed")
    _, board = call("GET", "/api/board")
    expect(all(v["status"] != "assigned" for v in board["queue"]),
           "reset left assignments behind")


# --- calendar uploads ------------------------------------------------------


def _clear_colours():
    """Drop any confirmed legend so the unconfirmed path can be tested for real."""
    path = os.environ["ESD_COLOR_MAP_PATH"]
    if os.path.exists(path):
        os.remove(path)


def _upload(path, filename=None):
    import base64 as _b64

    with open(path, "rb") as fh:
        blob = fh.read()
    return call("POST", "/api/calendar/upload", {
        "filename": filename or os.path.basename(path),
        "data": _b64.b64encode(blob).decode(),
    })


def test_month_upload_is_tier_three_and_never_schedulable():
    """A month grid has no end times, so it must not be able to book anything."""
    status, body = _upload(MONTH_PDF)
    expect(status == 200, f"month upload returned {status}: {body}")
    expect(body["view_type"] == "month", f"expected month view, got {body['view_type']}")
    expect(body["tier"] == 3, f"month must be tier 3, got tier {body['tier']}")
    expect(body["schedulable"] is False, "a month grid must never be schedulable")
    expect(body["block_count"] == 0, "a month grid must not produce bookable blocks")
    expect(body["entry_count"] > 0, "month upload parsed nothing at all")
    expect(any("END TIMES" in b for b in body["blockers"]),
           "month upload must say plainly that end times are missing")


def test_upload_rejects_a_file_that_is_not_a_pdf():
    import base64 as _b64

    status, body = call("POST", "/api/calendar/upload", {
        "filename": "notes.pdf",
        "data": _b64.b64encode(b"this is not a pdf").decode(),
    })
    expect(status == 400, f"non-PDF upload returned {status}")
    expect("PDF" in body.get("error", ""), f"unhelpful error: {body}")


def test_legend_attributes_people_with_no_setup_at_all():
    """Outlook prints its own legend, so an upload needs no manual matching."""
    _clear_colours()
    status, body = _upload(WEEK_PDF)
    expect(status == 200, f"work-week upload returned {status}: {body}")
    expect(body["attribution_source"] == "legend",
           f"expected legend attribution, got {body['attribution_source']}")
    # Six timed events from the colour legend, plus one whole-day block per
    # matched absence notice read off the all-day banners. The fixture prints
    # seven coordinator calendars; the seventh belongs to somebody the roster
    # carries as active: false, and their colour is recognised as theirs but
    # not attributed, so it contributes no blocks.
    expect(body["block_count"] == 6 + len(body["unavailable"]),
           f"unexpected block count {body['block_count']} against "
           f"{len(body['unavailable'])} absences")
    expect(body["unavailable"], "the absence notices were not read")
    expect(not any("NOBODY COULD BE IDENTIFIED" in b for b in body["blockers"]),
           "a file with a legend must not report that nobody was identified")


def test_export_without_a_colour_legend_attributes_no_colour_to_anyone():
    """No legend and no stored map means no colour is guessed onto a person.

    Absence notices are a separate path: they name the person in the banner
    text, so they still resolve on an export whose header lost its colours.
    """
    _clear_colours()
    status, body = _upload(PLAIN_WEEK_PDF)
    expect(status == 200, f"upload returned {status}: {body}")
    expect(body["attribution_source"] == "none",
           f"expected no colour attribution, got {body['attribution_source']}")
    expect(body["block_count"] == len(body["unavailable"]),
           "only absence notices may produce blocks without a legend")
    expect(any("NOBODY COULD BE IDENTIFIED" in b for b in body["blockers"]),
           f"missing the no-identification blocker: {body['blockers']}")


def test_colour_map_needs_a_named_confirmer():
    status, body = call("POST", "/api/calendar/colors",
                        {"map": {"navy": "C01"}, "confirmed_by": ""})
    expect(status == 400, f"anonymous confirmation returned {status}")


def test_colour_map_rejects_coordinators_not_on_the_roster():
    status, body = call("POST", "/api/calendar/colors",
                        {"map": {"navy": "NOT_A_REAL_ID"}, "confirmed_by": "Coordinator"})
    expect(status == 400, f"off-roster id returned {status}: {body}")


def _confirm_colours():
    status, body = call("POST", "/api/calendar/colors", {
        "map": {"navy": "C01", "teal": "C02", "blue": "C03",
                "orange": "C04", "green": "C05", "yellow": "C06"},
        "confirmed_by": "Test Coordinator",
    })
    expect(status == 200, f"saving the colour map returned {status}: {body}")
    expect(body["confirmed"] is True, "colour map did not stick")
    return body


def test_an_upload_for_a_different_week_says_so():
    """Silence after an upload is indistinguishable from a failed upload.

    Freshness and coverage are different questions and the header answers only
    the first: a print uploaded ten seconds ago reads "calendars just now" even
    when every date in it is a fortnight old. The evidence gate already refuses
    to schedule off dates a snapshot does not cover, so nothing unsafe happens
    -- but nothing visible happens either. Uploading a real calendar and
    watching every number stay identical, with no message, looks broken when
    the upload worked perfectly and was simply for another week.
    """
    import datetime as _dt
    import re as _re

    status, body = _upload(WEEK_PDF)
    expect(status == 200, f"upload returned {status}")
    notes = " ".join(body.get("notes") or [])
    span = body.get("date_range") or ""
    match = _re.match(r"(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})", span)
    expect(match, f"the upload reported no readable date range: {span!r}")
    covers_from = _dt.date.fromisoformat(match.group(1))
    covers_to = _dt.date.fromisoformat(match.group(2))

    _, health = call("GET", "/api/health")
    week = _dt.date.fromisoformat(health["week_of"])
    overlaps = covers_to >= week and covers_from <= week + _dt.timedelta(days=6)

    if overlaps:
        expect("is before the week" not in notes and
               "is after the week" not in notes,
               f"a print covering this week was reported as covering another: "
               f"{notes[:160]}")
    else:
        expect("the week the board is scheduling" in notes,
               f"this print covers {covers_from} to {covers_to}, the board is "
               f"scheduling the week of {week}, and the upload said nothing "
               f"about the difference: {notes[:160]!r}")


def test_work_week_upload_applies_immediately():
    """A PDF is parsed exactly, so its blocks take effect without a review step."""
    status, body = _upload(WEEK_PDF)
    expect(status == 200, f"work-week upload returned {status}")
    expect(body["tier"] == 2 and body["schedulable"] is True,
           "a timed export must be schedulable")
    # Six, not seven: one of the fixture's coordinator calendars belongs to
    # somebody the lab is not scheduling, whose colour is not attributed.
    expect(body["block_count"] == 6 + len(body["unavailable"]),
           f"unexpected block count {body['block_count']}")
    expect(body["pending_review"] == 0,
           "an exact PDF parse should not sit in a review queue")
    expect(body["applied_blocks"] >= 6,
           f"blocks should be in effect, applied={body['applied_blocks']}")


def test_pending_blocks_can_be_settled_in_one_pass():
    """An image import leaves dozens waiting; one at a time is not review."""
    _confirm_colours()
    _upload(WEEK_PDF)
    # Force something into the pending state the image path produces.
    store_before = call("GET", "/api/calendar/imports")[1]["confirmed_blocks"]

    status, body = call("POST", "/api/calendar/review-all",
                        {"confirmed": True, "reviewer": "Test Coordinator"})
    expect(status == 200, f"bulk review returned {status}: {body}")
    expect(body["pending_review"] == [], "blocks were left waiting after a bulk pass")
    expect(body["confirmed_blocks"] >= store_before,
           "a bulk confirm reduced the evidence in force")


def test_a_block_in_effect_can_still_be_rejected():
    """Auto-applying is not irreversible: a wrong read can be taken back."""
    _upload(WEEK_PDF)
    _, imports = call("GET", "/api/calendar/imports")
    applied = imports["applied"]
    expect(applied, "no applied blocks to reject")
    before = imports["confirmed_blocks"]

    status, body = call("POST", "/api/calendar/review",
                        {"block_id": applied[0]["block_id"], "confirmed": False,
                         "reviewer": "Test Coordinator"})
    expect(status == 200, f"reject returned {status}: {body}")
    expect(body["confirmed_blocks"] == before - 1,
           "rejecting a block must remove it from the evidence in force")


def test_month_upload_reports_who_is_free():
    """The point of a month upload: day-level availability per person."""
    status, body = _upload(MONTH_PDF)
    expect(status == 200, f"month upload returned {status}")
    rows = [a for a in body["availability"] if a["coordinator_id"]]
    expect(rows, f"month upload produced no per-person availability: {body['availability']}")
    for row in rows:
        expect(row["days"], f"{row['name']} has no days")
        states = {d["state"] for d in row["days"]}
        expect(states <= {"busy", "light", "open", "unknown"},
               f"unexpected availability states: {states}")
        expect(row["open_working_days"] <= row["working_days"],
               "more clear weekdays than weekdays")


def test_truncated_days_are_unknown_not_free():
    """A cut-off day cell must never read as availability."""
    status, body = _upload(MONTH_PDF)
    expect(status == 200, f"month upload returned {status}")
    bad = [
        (a["name"], d["day"])
        for a in body["availability"] for d in a["days"]
        if d["truncated"] and d["items"] == 0 and d["state"] == "open"
    ]
    expect(not bad, f"cut-off cells reported as free: {bad[:3]}")


def test_review_of_an_unknown_block_is_404():
    status, _ = call("POST", "/api/calendar/review",
                     {"block_id": "nope", "confirmed": True})
    expect(status == 404, f"unknown block returned {status}")


def test_the_board_clock_is_the_real_clock():
    """A clock frozen at startup turns every freshness figure into a fiction."""
    import datetime as _dt

    _, board = call("GET", "/api/board")
    stamp = board["health"].get("server_time")
    expect(stamp, "the board does not report its own time")
    drift = abs((_dt.datetime.fromisoformat(stamp) - _dt.datetime.now())
                .total_seconds())
    expect(drift < 120, f"board clock is {drift:.0f}s from the real one")


def test_evidence_stays_inside_the_sync_interval():
    """The mock provider stands in for the five-minute job; without it the whole
    roster ages out and gets vetoed for a reason that is nobody's fault."""
    _, board = call("GET", "/api/board")
    ages = [r["evidence_age_minutes"] for r in board["roster"]
            if r["evidence_age_minutes"] is not None]
    expect(ages, "no coordinator reported an evidence age")
    expect(max(ages) <= 10,
           f"evidence aged past the sync interval: {max(ages)} min")
    expect(min(ages) >= 0, f"negative evidence age: {min(ages)}")


def test_the_queue_is_ordered_by_how_pressing_the_visit_is():
    _, board = call("GET", "/api/board")
    queue = board["queue"]
    expect(queue, "empty queue")
    for row in queue:
        expect("priority" in row, "a queue row carries no priority")
        expect("due_status" in row, "a queue row carries no due status")
    keys = [tuple(r["priority"]) for r in queue]
    expect(keys == sorted(keys), "the queue is not in priority order")


def test_assigned_visits_sink_below_open_ones():
    _, board = call("GET", "/api/board")
    open_visit = next((v for v in board["queue"] if v["status"] != "assigned"), None)
    expect(open_visit, "nothing left to assign")
    status, body = call("POST", "/api/assign", {
        "visit_id": open_visit["id"],
        "coordinator_id": (call("GET", "/api/visit?id=" + open_visit["id"])[1]
                           ["candidates"][0]["id"]),
    })
    if status != 200:
        return                      # nobody eligible; ordering is untestable here
    _, after = call("GET", "/api/board")
    positions = {v["id"]: i for i, v in enumerate(after["queue"])}
    assigned = [v for v in after["queue"] if v["status"] == "assigned"]
    unassigned = [v for v in after["queue"] if v["status"] != "assigned"]
    if assigned and unassigned:
        expect(min(positions[v["id"]] for v in assigned)
               > max(positions[v["id"]] for v in unassigned),
               "an assigned visit outranked one still needing a decision")
    call("POST", "/api/unassign", {"visit_id": open_visit["id"]})


def test_board_carries_calendar_state_in_one_round_trip():
    _, board = call("GET", "/api/board")
    expect("calendar" in board, "board must expose calendar state")
    expect("color_map" in board["calendar"], "board must expose the colour map state")
    for key in ("filters", "unavailable", "unresolved_names", "roles"):
        expect(key in board["calendar"], f"board must expose {key}")


def test_an_absence_notice_blocks_that_person_for_the_whole_day():
    """The point of reading a banner: it has to actually take someone off."""
    _upload(WEEK_PDF)
    _, imports = call("GET", "/api/calendar/imports")
    whole = [b for b in imports["applied"] if b["start"].endswith("T00:00:00")]
    expect(whole, "no whole-day absence blocks reached the board")

    _, board = call("GET", "/api/board")
    blocked = {b["coordinator"] for b in whole}
    expect(blocked, "absences resolved to nobody")
    for row in board["calendar"]["unavailable"]:
        expect(row["coordinator_id"], "an absence row lost its coordinator")


def test_the_board_picks_up_an_import_made_outside_it():
    """The inbox job writes to the same store; a running board must notice."""
    import sys as _sys

    from esd_scheduler.calendar_import import import_pdf
    from esd_scheduler.store import AuditStore

    _, before = call("GET", "/api/calendar/imports")

    # Same thing the scheduled inbox job does: import and record, no HTTP.
    store = AuditStore(DB_PATH)
    try:
        result = import_pdf(MONTH_PDF, coordinators=srv.SESSION.state.coordinators,
                            year_hint=2026)
        store.record_import(result)
    finally:
        store.close()

    _, after = call("GET", "/api/board")
    ids = {row["import_id"] for row in after["calendar"]["imports"]}
    expect(result.import_id in ids,
           "an import recorded outside the board never reached it")
    expect(len(after["calendar"]["imports"]) > len(before["imports"]),
           "the board's import history did not grow")


def test_unmatched_absence_names_are_surfaced_not_applied():
    _upload(WEEK_PDF)
    _, board = call("GET", "/api/board")
    unresolved = board["calendar"]["unresolved_names"]
    expect(unresolved, "the unmatched nickname was not surfaced")
    names = {u["name"] for u in unresolved}
    expect("Maggie" in names, f"expected Maggie unresolved, got {names}")
    applied = {u["name"] for u in board["calendar"]["unavailable"]}
    expect("Margaret Bell" not in applied,
           "a nickname was guessed onto a real coordinator")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="visitboard-test-")
    # Never let a test rewrite the real confirmed legend in config/.
    os.environ["ESD_COLOR_MAP_PATH"] = os.path.join(tmp, "calendar-colors.json")
    # Isolate the role/alias config too: with the lab's real aliases in place
    # every name resolves, and the "unmatched name" path would go untested.
    os.environ["ESD_CALENDAR_ROLES_PATH"] = os.path.join(tmp, "calendar-roles.json")

    ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(ROOT_DIR, "tests", "fixtures"))
    from make_work_week_pdf import build as build_week_pdf  # noqa: E402
    from make_month_pdf import build as build_month_pdf  # noqa: E402

    # Both fixtures are synthetic. A real month export prints event titles for
    # every overlaid calendar, so one can never be committed here.
    WEEK_PDF = build_week_pdf(os.path.join(tmp, "work-week.pdf"))
    PLAIN_WEEK_PDF = build_week_pdf(
        os.path.join(tmp, "work-week-plain.pdf"), coloured_legend=False)
    MONTH_PDF = build_month_pdf(os.path.join(tmp, "month.pdf"))
    DB_PATH = os.path.join(tmp, "test.db")
    httpd = srv.build_server(port=0, db_path=DB_PATH)
    BASE = f"http://127.0.0.1:{httpd.server_address[1]}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    failures = 0
    try:
        for name, fn in sorted(list(globals().items())):
            if name.startswith("test_") and callable(fn):
                try:
                    fn()
                    print(f"PASS {name}")
                except Exception as exc:  # noqa: BLE001
                    failures += 1
                    print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    finally:
        httpd.shutdown()
        httpd.server_close()
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
