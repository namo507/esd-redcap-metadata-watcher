"""Stdlib HTTP server for the ESD Visitboard.

    python3 -m backend.server [--port 8765] [--no-browser]

Serves the JSON API and the static frontend from one process on one port, so
there is no CORS setup, no proxy, no second terminal and no build step. The
whole stack starts with a single command and only needs Python.

Every route is a thin translation of ``backend.session``; no scheduling
decision is made here.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import os
import posixpath
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import __version__  # noqa: E402
from backend.session import LabSession  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, "frontend")

SESSION: Optional[LabSession] = None
Handler = Callable[[dict, dict], Tuple[int, object]]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

GET_ROUTES: Dict[str, Handler] = {}
POST_ROUTES: Dict[str, Handler] = {}


def get(path: str):
    def wrap(fn):
        GET_ROUTES[path] = fn
        return fn
    return wrap


def post(path: str):
    def wrap(fn):
        POST_ROUTES[path] = fn
        return fn
    return wrap


@get("/api/calendar/imports")
def r_calendar_imports(params, body):
    return 200, SESSION.imports()


@get("/api/calendar/colors")
def r_calendar_colors(params, body):
    return 200, SESSION.color_map_state()


@post("/api/calendar/colors")
def r_calendar_colors_save(params, body):
    """Confirm which coordinator owns each calendar colour."""
    try:
        return 200, SESSION.save_color_map(
            body.get("map") or {}, str(body.get("confirmed_by") or "")
        )
    except ValueError as exc:
        return 400, {"error": str(exc)}


@post("/api/calendar/upload")
def r_calendar_upload(params, body):
    """Accept a printed Outlook calendar and ingest it at its true tier.

    The PDF arrives base64-encoded inside JSON so the whole board keeps one
    request format; the handler still verifies the decoded bytes really are a
    PDF rather than trusting the filename.
    """
    raw = body.get("data") or ""
    if isinstance(raw, str) and "," in raw[:64] and raw[:5] == "data:":
        raw = raw.split(",", 1)[1]
    try:
        blob = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError, binascii.Error):
        return 400, {"error": "The upload could not be decoded. Try the file again."}
    hours = body.get("hours")
    span = None
    if isinstance(hours, (list, tuple)) and len(hours) == 2:
        try:
            span = (float(hours[0]), float(hours[1]))
        except (TypeError, ValueError):
            span = None
    try:
        return 200, SESSION.upload_calendar_pdf(
            str(body.get("filename") or ""), blob, image_hours=span)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except RuntimeError as exc:
        return 503, {"error": str(exc)}


@post("/api/calendar/review-all")
def r_calendar_review_all(params, body):
    """Confirm or reject every block still waiting on a human."""
    return 200, SESSION.review_all_pending(
        bool(body.get("confirmed")),
        str(body.get("reviewer") or "coordinator"))


@post("/api/calendar/review")
def r_calendar_review(params, body):
    """Confirm or reject one parsed block. Only confirmed blocks become evidence."""
    block_id = str(body.get("block_id") or "")
    if not block_id:
        return 400, {"error": "Which block?"}
    try:
        return 200, SESSION.review_import_block(
            block_id,
            bool(body.get("confirmed")),
            str(body.get("reviewer") or "coordinator"),
        )
    except KeyError as exc:
        return 404, {"error": str(exc)}


@get("/api/coordinators")
def r_coordinators(params, body):
    """One row per coordinator: their week and what they can take on."""
    return 200, SESSION.coordinator_table()


@get("/api/availability")
def r_availability(params, body):
    """Who is free in each slot, and whose calendar is still missing."""
    try:
        minutes = max(15, min(120, int(params.get("slot", 30))))
    except (TypeError, ValueError):
        minutes = 30
    return 200, SESSION.availability_grid(slot_minutes=minutes)


@get("/api/logic")
def r_logic(params, body):
    """How the board decides, described from its own configuration."""
    return 200, SESSION.logic()


@get("/api/schedule")
def r_schedule(params, body):
    """Which families are owed a visit, most pressing first."""
    return 200, SESSION.schedule_rows()


@get("/api/health")
def r_health(params, body):
    return 200, SESSION.health()


@get("/api/board")
def r_board(params, body):
    """One call for the whole screen. Fewer round trips, no partial states."""
    # Both happen before anything is read, so one payload is internally
    # consistent: pick up imports filed by the inbox job, then re-pull the mock
    # calendars if they have aged past the sync interval.
    SESSION.refresh_calendar()
    SESSION.keep_calendars_fresh()
    return 200, {
        "health": SESSION.health(),
        "roster": SESSION.roster(),
        "queue": SESSION.queue(),
        "fairness": SESSION.fairness(),
        "reason_codes": SESSION.reason_codes(),
        "activity": SESSION.activity[:12],
        "calendar": SESSION.imports(),
        "schedule": SESSION.schedule_rows(),
        "logic": SESSION.logic(),
        "availability": SESSION.availability_grid(),
        "coordinators": SESSION.coordinator_table(),
    }


@get("/api/roster")
def r_roster(params, body):
    return 200, {"roster": SESSION.roster()}


@get("/api/visits")
def r_visits(params, body):
    return 200, {"visits": SESSION.queue()}


@get("/api/visit")
def r_visit(params, body):
    visit_id = params.get("id")
    if not visit_id or visit_id not in SESSION.visits:
        return 404, {"error": f"No visit {visit_id!r}."}
    return 200, SESSION.candidates(visit_id)


@get("/api/fairness")
def r_fairness(params, body):
    return 200, SESSION.fairness()


@get("/api/week")
def r_week(params, body):
    return 200, SESSION.week_plan()


@post("/api/assign")
def r_assign(params, body):
    visit_id = body.get("visit_id")
    coordinator_id = body.get("coordinator_id")
    if not visit_id or visit_id not in SESSION.visits:
        return 404, {"error": f"No visit {visit_id!r}."}
    if not coordinator_id:
        return 400, {"error": "coordinator_id is required."}
    try:
        record = SESSION.assign(
            visit_id,
            coordinator_id,
            reason_code=body.get("reason_code") or None,
            reason_text=body.get("reason_text") or None,
            tech_id=str(body.get("tech_id")) if body.get("tech_id") else None,
        )
    except ValueError as exc:
        return 400, {"error": str(exc)}
    return 200, {"assignment": record, "visit": SESSION.visit_summary(visit_id)}


@post("/api/unassign")
def r_unassign(params, body):
    visit_id = body.get("visit_id")
    if not visit_id or visit_id not in SESSION.visits:
        return 404, {"error": f"No visit {visit_id!r}."}
    SESSION.unassign(visit_id)
    return 200, {"visit": SESSION.visit_summary(visit_id)}


@post("/api/reset")
def r_reset(params, body):
    SESSION.reset()
    return 200, {"ok": True}


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class VisitboardHandler(BaseHTTPRequestHandler):
    server_version = f"ESDVisitboard/{__version__}"

    def log_message(self, fmt, *args):  # quieter console
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("  %s\n" % (fmt % args))

    # -- helpers ------------------------------------------------------------

    def _send_json(self, status: int, payload: object) -> None:
        blob = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(blob)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _params(self, parsed) -> dict:
        out: dict = {}
        for pair in (parsed.query or "").split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                out[unquote(k)] = unquote(v)
        return out

    def _serve_static(self, path: str) -> None:
        rel = posixpath.normpath(unquote(path)).lstrip("/")
        if rel in ("", "index.html"):
            rel = "index.html"
        target = os.path.join(FRONTEND, rel)
        # Never serve outside the frontend directory.
        if not os.path.abspath(target).startswith(os.path.abspath(FRONTEND)):
            self._send_json(403, {"error": "Forbidden"})
            return
        if not os.path.isfile(target):
            self._send_json(404, {"error": f"Not found: {rel}"})
            return
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        with open(target, "rb") as fh:
            blob = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(blob)))
        # The board is a live operational view; never let a proxy pin it.
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(blob)

    # -- verbs --------------------------------------------------------------

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        route = GET_ROUTES.get(parsed.path)
        if route is None:
            self._serve_static(parsed.path)
            return
        try:
            status, payload = route(self._params(parsed), {})
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            status, payload = 500, {"error": "Internal error. See the server log."}
        self._send_json(status, payload)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        route = POST_ROUTES.get(parsed.path)
        if route is None:
            self._send_json(404, {"error": f"No route {parsed.path}"})
            return
        try:
            status, payload = route(self._params(parsed), self._read_body())
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            status, payload = 500, {"error": "Internal error. See the server log."}
        self._send_json(status, payload)


def build_server(port: int = 8765, db_path: Optional[str] = None) -> ThreadingHTTPServer:
    global SESSION
    SESSION = LabSession(db_path or os.path.join(ROOT, "data", "visitboard.db"))
    return ThreadingHTTPServer(("127.0.0.1", port), VisitboardHandler)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    try:
        httpd = build_server(args.port)
    except OSError as exc:
        print(f"Could not bind port {args.port}: {exc}", file=sys.stderr)
        print("Another copy may already be running. Try --port 8766.", file=sys.stderr)
        return 2

    url = f"http://127.0.0.1:{args.port}/"
    print(f"ESD Visitboard  ->  {url}")
    print(f"  engine {SESSION.health()['engine_version']}   "
          f"weights {SESSION.cfg.weight_vector_id}")
    print(f"  {len(SESSION.order)} open visits, {len(SESSION.roster())} coordinators")
    print("  free/busy only; the board never requests event titles")
    print("  ctrl-c to stop")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
