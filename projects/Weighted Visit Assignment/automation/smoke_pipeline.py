"""End-to-end proof that the page and the engine stay in step.

    make smoke                     invented calendars
    make smoke FILES="a.pdf b.pdf" your own prints

Boots the real server on an ephemeral port and drives the whole scheduling
pipeline over the same HTTP endpoints the dashboard itself calls. Nothing is
stubbed, nothing is mocked, and no external service is contacted: the upload
is a real file, decoded and parsed in-process by the same reader the browser
triggers, and every assertion is made against what the API actually returned.

WHY THIS EXISTS SEPARATELY FROM THE UNIT TESTS. The unit tests prove each
piece in isolation. This proves the seams: that an upload changes what the
board reports, that entering a visit changes the queue, that a decision made
on the screen is the decision the assign route will accept, and that undoing
it puts the board back. Those are exactly the places where two correct halves
have disagreed in this codebase before.

The run is a sequence of numbered checks. Each prints what it asked for and
what came back, so a failure says which seam parted rather than only that
something is wrong.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures"))

BASE = ""
FAILURES: list = []
WIDTH = 74


# ------------------------------------------------------------------ plumbing

def call(method: str, path: str, payload=None):
    """One HTTP call to the board, exactly as the page makes it."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    def _decode(raw: str):
        # The API answers JSON; the page, its stylesheet and its scripts do
        # not. Both are legitimate replies and this has to read either.
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            return {"_text": raw[:200]}

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, _decode(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, _decode(exc.read().decode("utf-8", "replace"))


def check(number: int, title: str, ok: bool, detail: str = "") -> bool:
    mark = "ok  " if ok else "FAIL"
    print(f"  {mark} {number:>2}. {title}")
    if detail:
        for line in str(detail).splitlines():
            print(f"          {line}")
    if not ok:
        FAILURES.append(f"{number}. {title}")
    return ok


def head(text: str) -> None:
    print()
    print("  " + text)
    print("  " + "-" * (WIDTH - 2))


# --------------------------------------------------------------------- steps

def run(files=None) -> int:
    global BASE

    os.environ["ESD_MODE"] = "live"          # a real board, nothing invented
    tmp = tempfile.mkdtemp(prefix="esd-smoke-")
    os.environ["ESD_DATA_DIR"] = tmp         # keep uploads out of the repo

    from http.server import ThreadingHTTPServer   # noqa: F401
    from backend import server as srv

    # The board logs every request. Useful when serving, noise when the point
    # of the run is the checks themselves.
    srv.VisitboardHandler.log_message = lambda *a, **k: None

    httpd = srv.build_server(port=0, db_path=os.path.join(tmp, "smoke.db"))
    BASE = f"http://127.0.0.1:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    print()
    print("  ESD Visitboard: end-to-end pipeline check")
    print("  " + "=" * (WIDTH - 2))
    print(f"  server {BASE}   mode live   data {tmp}")

    # -- the calendars this run will use ------------------------------------
    if files:
        prints = [os.path.abspath(f) for f in files]
        for path in prints:
            if not os.path.exists(path):
                print(f"  no such file: {path}")
                return 2
        source = "your own prints"
    else:
        from make_work_week_pdf import build as build_week
        prints = [build_week(os.path.join(tmp, "work-week.pdf"))]
        source = "an invented work-week print"

    # ---------------------------------------------------------------- serving
    head("The server is up and serving the page")

    status, health = call("GET", "/api/health")
    check(1, "health answers", status == 200 and health.get("ok"),
          f"mode={health.get('mode')}  engine={health.get('engine_version')}")
    check(2, "board is live, not the demo", health.get("mode") == "live")
    check(3, "no calendar read yet",
          health.get("calendar_source") == "none",
          f"calendar_source={health.get('calendar_source')!r}"
          "   (not 'nobody is busy' -- nobody has been looked at)")

    page_status, _ = call("GET", "/")
    check(4, "the page itself is served", page_status == 200)

    for asset in ("/styles.css", "/js/core.js", "/js/boot.js", "/config.js"):
        code, _ = call("GET", asset)
        if not check(5, f"the page's {asset} is served", code == 200):
            break

    # -------------------------------------------------------------- the board
    head("An empty board reports itself empty")

    status, board = call("GET", "/api/board")
    check(6, "board answers in one round trip", status == 200,
          f"sections: {', '.join(sorted(board))}")
    check(7, "no visits yet", len(board.get("queue", [])) == 0)
    check(8, "the roster is still there",
          len(board.get("roster", [])) > 0,
          f"{len(board.get('roster', []))} coordinators")

    free_before = {r["name"]: r["free_hours"]
                   for r in board.get("coordinators", {}).get("rows", [])}
    check(9, "nobody reads as free before a calendar is read",
          all(v == 0 for v in free_before.values()),
          f"free hours: {sorted(set(free_before.values()))}")

    # ------------------------------------------------------- runtime upload
    head(f"Runtime upload and processing, using {source}")

    covered = None
    for path in prints:
        with open(path, "rb") as fh:
            blob = fh.read()
        status, out = call("POST", "/api/calendar/upload", {
            "filename": os.path.basename(path),
            "data": base64.b64encode(blob).decode(),
        })
        name = os.path.basename(path)
        ok = status == 200 and out.get("tier")
        check(10, f"{name} uploaded and parsed in-process", ok,
              f"view={out.get('view_type')}  tier={out.get('tier')}  "
              f"blocks={out.get('block_count')}  covers={out.get('date_range')}")
        covered = out.get("date_range") or covered

    status, health = call("GET", "/api/health")
    check(11, "the board now says where its busy time came from",
          health.get("calendar_source") == "upload",
          f"calendar_source={health.get('calendar_source')!r}")

    status, board = call("GET", "/api/board")
    imports = board.get("calendar", {}).get("imports", [])
    check(12, "the upload is on the board's own record",
          len(imports) >= len(prints),
          f"{len(imports)} import(s) recorded")

    # ------------------------------------------------------- entering a visit
    head("Entering a visit, and asking who should take it")

    window = _window_inside(covered)
    status, created = call("POST", "/api/visits", {
        "family_id": "5901", "protocol": "NANO", "checkpoint": "9m",
        "window_start": window[0], "window_end": window[1],
        "participant_status": "TD", "birth_date": "2025-11-17",
        "completed_through": "6m",
    })
    visit_id = (created.get("visit") or {}).get("visit_id")
    check(13, "the visit was accepted", status == 200 and bool(visit_id),
          f"visit {visit_id}  window {window[0][:10]} to {window[1][:10]}")

    status, bad = call("POST", "/api/visits", {
        "family_id": "9999", "protocol": "NANO", "checkpoint": "9m",
        "window_start": window[0], "window_end": window[1]})
    check(14, "a participant id that is not a NANO id is refused",
          status == 400, bad.get("error", "")[:70])

    status, board = call("GET", "/api/board")
    queue = board.get("queue", [])
    check(15, "the queue picked the visit up without a reload",
          any(v["id"] == visit_id for v in queue),
          f"{len(queue)} visit(s) on the board")
    row = next((v for v in queue if v["id"] == visit_id), {})
    check(16, "the queue shows the whole participant id",
          "5901" in str(row.get("family_label")),
          f"label={row.get('family_label')!r}")
    check(17, "the visit length came from the protocol, not a default",
          row.get("duration_hours") == 2.0,
          f"9m is 2 hours in the manual; board says {row.get('duration_hours')}")

    # ------------------------------------------------------------- the decision
    head("The decision, and the reasons behind it")

    status, detail = call("GET", f"/api/visit?id={visit_id}")
    check(18, "the visit detail answers", status == 200)

    elig = detail.get("eligibility") or {}
    check(19, "eligibility ran before scoring",
          bool(elig.get("clinicians") or elig.get("excluded")),
          f"clinicians={[v['name'] for v in elig.get('clinicians', [])]}")
    check(20, "everyone refused has a reason attached",
          all(v.get("reason") for v in
              elig.get("excluded", []) + elig.get("clinician_blocked", [])),
          "; ".join(f"{v['name']}: {v['reason'][:38]}"
                    for v in (elig.get("excluded", [])
                              + elig.get("clinician_blocked", []))[:3]))

    pairs = detail.get("pairs") or []
    check(21, "at least one workable pairing", bool(pairs),
          "\n".join(f"{p['clinician']} + {p['tech']}  {p.get('slot')}  "
                    f"{p['score']}  {p.get('vehicle')}" for p in pairs[:3]))
    if pairs:
        top = pairs[0]
        check(22, "the pair is one clinician and one tech, not the same person",
              top["clinician_id"] != top["tech_id"])
        check(23, "the score is the weighted sum the board reports",
              _score_matches(top, health.get("weights", {})),
              f"components {top.get('components')} -> {top['score']}")

    # ------------------------------------------------------------- committing
    head("Committing, and putting it back")

    if pairs:
        top = pairs[0]
        status, assigned = call("POST", "/api/assign", {
            "visit_id": visit_id,
            "coordinator_id": top["clinician_id"],
            "tech_id": top["tech_id"],
        })
        check(24, "the board accepts its own recommendation without a reason",
              status == 200,
              f"{top['clinician']} + {top['tech']}"
              if status == 200 else assigned.get("error", "")[:70])

        status, board = call("GET", "/api/board")
        row = next((v for v in board["queue"] if v["id"] == visit_id), {})
        check(25, "the queue reflects the assignment immediately",
              row.get("status") == "assigned",
              f"status={row.get('status')!r}")

        status, _ = call("POST", "/api/unassign", {"visit_id": visit_id})
        status, board = call("GET", "/api/board")
        row = next((v for v in board["queue"] if v["id"] == visit_id), {})
        check(26, "undoing puts it back on the queue",
              row.get("status") == "needs_assignment",
              f"status={row.get('status')!r}")

        # The seam that has parted here before: the screen refusing somebody
        # and the assign route accepting them anyway. Assigning the
        # recommendation proves nothing about that, because the recommendation
        # is eligible under either behaviour, so this tries somebody the screen
        # refused and requires the route to refuse them for that reason rather
        # than because the visit happened to be taken already.
        #
        # Worth knowing what this does and does not prove. On the current
        # roster the eligibility layer and the older Layer 1 certification gate
        # refuse exactly the same people, so it cannot tell which of the two
        # did the refusing, and deleting either filter still leaves this check
        # passing. It holds the invariant the lab cares about -- the two paths
        # agree -- and the unit tests in test_eligibility.py are what pin the
        # eligibility layer's own behaviour, using synthetic staff who sit in
        # the gap between the two.
        refused = (elig.get("excluded") or []) + (elig.get("clinician_blocked") or [])
        if refused:
            victim = refused[0]
            status, denied = call("POST", "/api/assign", {
                "visit_id": visit_id,
                "coordinator_id": victim["coordinator_id"],
                "tech_id": top["tech_id"],
            })
            message = str(denied.get("error", ""))
            check(28, "the assign route refuses whoever the screen refused",
                  status != 200 and "already assigned" not in message,
                  f"{victim['name']}, refused on screen for: "
                  f"{victim['reason'][:44]}\n"
                  f"assign route answered {status}: {message[:60]}")

    # --------------------------------------------------- a visit needing nobody
    head("The timepoint that needs nobody")

    status, remote = call("POST", "/api/visits", {
        "family_id": "5912", "protocol": "NANO", "checkpoint": "24m",
        "window_start": window[0], "window_end": window[1],
        "participant_status": "TD", "birth_date": "2024-08-20"})
    remote_id = (remote.get("visit") or {}).get("visit_id")
    status, detail = call("GET", f"/api/visit?id={remote_id}")
    check(27, "a 24m timepoint offers no staff at all",
          len(detail.get("pairs") or []) == 0 and detail.get("remote"),
          (detail.get("notices") or [{}])[0].get("message", "")[:70])

    # -------------------------------------------------------------- shutdown
    httpd.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("  " + "=" * (WIDTH - 2))
    if FAILURES:
        print(f"  {len(FAILURES)} seam(s) parted:")
        for line in FAILURES:
            print(f"    {line}")
        return 1
    print("  every seam held: upload -> parse -> board -> decision -> commit")
    print()
    return 0


def _window_inside(date_range):
    """A visit window inside the week the uploaded print actually covered.

    Asking for a decision outside it would correctly return nobody, which
    would look like a broken pipeline rather than the coverage rule working.
    """
    if date_range and " to " in str(date_range):
        lo, hi = [p.strip() for p in str(date_range).split(" to ")]
        return f"{lo}T09:00:00", f"{hi}T17:00:00"
    from datetime import datetime, timedelta
    monday = datetime.now() - timedelta(days=datetime.now().weekday())
    return (monday.strftime("%Y-%m-%dT09:00:00"),
            (monday + timedelta(days=4)).strftime("%Y-%m-%dT17:00:00"))


def _score_matches(pair, weights) -> bool:
    """The printed score has to be the criteria times the weights."""
    parts = pair.get("components") or {}
    if not parts or not weights:
        return False
    total = sum(parts.get(k, 0.0) * w for k, w in weights.items())
    return abs(total - pair["score"]) < 0.01


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:] or None))
