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
        ("/app.js", "api/board"),
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
    expect(assigned >= len(board["queue"]) - 1,
           f"only {assigned} of {len(board['queue'])} visits could be filled")
    call("POST", "/api/reset")


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
    _, detail = call("GET", f"/api/visit?id={vid}")
    top = next(c for c in detail["candidates"] if c["assignable"])
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


def test_unconfirmed_colour_map_attributes_to_nobody():
    """Without a confirmed legend, a parsed block must not land on a person."""
    _clear_colours()
    status, body = _upload(WEEK_PDF)
    expect(status == 200, f"work-week upload returned {status}: {body}")
    expect(body["tier"] == 2, f"work week must be tier 2, got {body['tier']}")
    expect(body["block_count"] == 0,
           "an unconfirmed colour map must not attribute blocks to anyone")
    expect(any("COLOUR MAP NOT CONFIRMED" in b for b in body["blockers"]),
           f"missing the unconfirmed-map blocker: {body['blockers']}")


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


def test_work_week_upload_becomes_reviewable_blocks_with_real_intervals():
    _confirm_colours()
    _, before = call("GET", "/api/calendar/imports")

    status, body = _upload(WEEK_PDF)
    expect(status == 200, f"work-week upload returned {status}")
    expect(body["tier"] == 2 and body["schedulable"] is True,
           "a timed export must be schedulable")
    expect(body["block_count"] == 6, f"expected 6 blocks, got {body['block_count']}")
    expect(body["pending_review"] == 6, "every fresh block must await review")

    # The invariant: uploading alone adds no evidence, whatever was confirmed
    # by an earlier upload.
    _, after = call("GET", "/api/calendar/imports")
    expect(after["confirmed_blocks"] == before["confirmed_blocks"],
           "an upload must not create evidence before anyone has reviewed it")


def test_only_confirmed_blocks_become_evidence():
    # Self-contained: tests run in name order, so this cannot lean on another
    # test having uploaded first.
    _confirm_colours()
    _upload(WEEK_PDF)

    _, imports = call("GET", "/api/calendar/imports")
    pending = imports["pending_review"]
    expect(pending, "no pending blocks to review")
    before = imports["confirmed_blocks"]

    status, body = call("POST", "/api/calendar/review",
                        {"block_id": pending[0]["block_id"], "confirmed": True,
                         "reviewer": "Test Coordinator"})
    expect(status == 200, f"review returned {status}: {body}")
    expect(body["confirmed_blocks"] == before + 1,
           "confirming a block did not add evidence")

    status, body = call("POST", "/api/calendar/review",
                        {"block_id": pending[1]["block_id"], "confirmed": False,
                         "reviewer": "Test Coordinator"})
    expect(status == 200, f"reject returned {status}")
    expect(body["confirmed_blocks"] == before + 1,
           "a rejected block must never count as evidence")


def test_review_of_an_unknown_block_is_404():
    status, _ = call("POST", "/api/calendar/review",
                     {"block_id": "nope", "confirmed": True})
    expect(status == 404, f"unknown block returned {status}")


def test_board_carries_calendar_state_in_one_round_trip():
    _, board = call("GET", "/api/board")
    expect("calendar" in board, "board must expose calendar state")
    expect("color_map" in board["calendar"], "board must expose the colour map state")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="visitboard-test-")
    # Never let a test rewrite the real confirmed legend in config/.
    os.environ["ESD_COLOR_MAP_PATH"] = os.path.join(tmp, "calendar-colors.json")

    ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(ROOT_DIR, "tests", "fixtures"))
    from make_work_week_pdf import build as build_week_pdf  # noqa: E402
    from make_month_pdf import build as build_month_pdf  # noqa: E402

    # Both fixtures are synthetic. A real month export prints event titles for
    # every overlaid calendar, so one can never be committed here.
    WEEK_PDF = build_week_pdf(os.path.join(tmp, "work-week.pdf"))
    MONTH_PDF = build_month_pdf(os.path.join(tmp, "month.pdf"))
    httpd = srv.build_server(port=0, db_path=os.path.join(tmp, "test.db"))
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
