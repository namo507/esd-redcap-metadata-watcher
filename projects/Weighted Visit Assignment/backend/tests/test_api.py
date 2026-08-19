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


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="visitboard-test-")
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
