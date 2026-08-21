"""Build the public, backend-free copy of the Visitboard.

    python3 -m backend.build_static            -> dist-static/

The hosted copy has no Python, so the engine runs here and its answers are
frozen into board.json. StaticBoard then serves the same routes the API does,
which is why the frontend is byte-identical between the two modes.

Nothing about the ranking is reimplemented for the web. The only quantity the
browser recomputes is the burden term, because assigning a visit changes a
coordinator's committed hours; tests/test_static_board.py pins that against the
snapshot written here.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.session import LabSession  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "frontend")
OUT = os.path.join(ROOT, "dist-static")


def _demo_calendar(session) -> dict:
    """A month-availability grid for the public copy, built from a fake export.

    The published board must never carry a real one. Availability derived from
    the lab's actual Outlook print would publish six named people's genuine busy
    days, which is exactly the disclosure this project exists to avoid — so the
    demo imports a generated PDF instead. The roster names are real and the
    pattern is invented, matching the demonstration notice the page already
    carries.
    """
    sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures"))
    try:
        from make_month_pdf import build as build_month
        from make_work_week_pdf import build as build_week

        from esd_scheduler.calendar_import import import_pdf
    except ImportError:
        return {}

    tmp = tempfile.mkdtemp(prefix="esd-demo-cal-")
    path = build_month(os.path.join(tmp, "demo-month.pdf"),
                       year=session.now.year, month=session.now.month, vary=True)
    result = import_pdf(path, coordinators=session.state.coordinators,
                        year_hint=session.now.year)

    # A second, timed export so the public copy also shows the policy filters.
    # Both are generated: a real work-week print names who is in the lab and
    # when, which is not ours to publish.
    week_path = build_week(
        os.path.join(tmp, "demo-week.pdf"),
        first_day=session.now.day, month=session.now.month, year=session.now.year)
    week = import_pdf(week_path, coordinators=session.state.coordinators,
                      year_hint=session.now.year)
    session.resources = week.resources
    session.calendar_roles = week.to_dict()["role_summary"]
    session.unavailable = week.unavailable
    session.unresolved_names = week.unresolved_names
    month = ""
    days = [d["day"] for a in result.availability for d in a.get("days", [])]
    if days:
        month = datetime.fromisoformat(sorted(days)[len(days) // 2]).strftime("%B %Y")
    return {
        "imports": [], "pending_review": [], "applied": [], "confirmed_blocks": 0,
        "last_import": None,
        "color_map": {"confirmed": False, "map": {}, "hues_seen": {},
                      "roster": [], "calendar_names": []},
        "availability": result.availability,
        "availability_month": month,
        "resources": week.resources,
        "roles": week.to_dict()["role_summary"],
        "filters": session.filter_state(),
        "unavailable": week.unavailable,
        "unresolved_names": week.unresolved_names,
    }


def build() -> str:
    session = LabSession(os.path.join(ROOT, "data", "static-build.db"))

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    shutil.copytree(SRC, OUT, ignore=shutil.ignore_patterns("README.md"))

    payload = {
        "meta": {
            "health": session.health(),
            "weights": session.cfg.weights.as_dict(),
            "gammaTravel": session.cfg.gamma_travel,
            "reviewBand": round(session.cfg.epsilon_review_band, 3),
        },
        "roster": session.roster(),
        "reasonCodes": session.reason_codes(),
        "calendar": _demo_calendar(session),
        "schedule": session.schedule_rows(),
        "visits": [],
    }
    for visit_id in session.order:
        detail = session.candidates(visit_id)
        detail.pop("assigned", None)
        payload["visits"].append(detail)

    with open(os.path.join(OUT, "board.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"), default=str)

    # Netlify: everything is static, and the board is a single page.
    # Scripts and data must revalidate. A redeploy changes app.js, board.json
    # and static-board.js together, and a browser holding a cached script
    # against fresh data shows numbers that do not match the board's own logic.
    # Caught locally: a stale static-board.js kept reporting a visit as needing
    # attention after it had been assigned. Images are content-stable, so they
    # keep the default long cache.
    with open(os.path.join(OUT, "_headers"), "w", encoding="utf-8") as fh:
        fh.write(
            "/*\n"
            "  X-Content-Type-Options: nosniff\n"
            "  Referrer-Policy: no-referrer\n"
            "  X-Frame-Options: SAMEORIGIN\n"
            "\n/index.html\n  Cache-Control: no-cache\n"
            "\n/*.js\n  Cache-Control: no-cache\n"
            "\n/*.css\n  Cache-Control: no-cache\n"
            "\n/board.json\n  Cache-Control: no-cache\n"
        )
    # No _redirects file on purpose. The board is a single page with no client
    # routing, so a catch-all would only turn a missing asset into a 200 that
    # serves HTML - which hides exactly the kind of build mistake worth seeing.

    session.store.close()
    size = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _, fs in os.walk(OUT) for f in fs
    )
    print(f"built {OUT}")
    print(f"  {len(payload['visits'])} visits, {len(payload['roster'])} coordinators")
    print(f"  {size / 1024:.0f} KB total")
    return OUT


if __name__ == "__main__":
    build()
