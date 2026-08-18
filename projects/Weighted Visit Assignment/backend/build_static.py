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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.session import LabSession  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "frontend")
OUT = os.path.join(ROOT, "dist-static")


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
        "visits": [],
    }
    for visit_id in session.order:
        detail = session.candidates(visit_id)
        detail.pop("assigned", None)
        payload["visits"].append(detail)

    with open(os.path.join(OUT, "board.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"), default=str)

    # Netlify: everything is static, and the board is a single page.
    with open(os.path.join(OUT, "_headers"), "w", encoding="utf-8") as fh:
        fh.write("/*\n  X-Content-Type-Options: nosniff\n"
                 "  Referrer-Policy: no-referrer\n"
                 "  X-Frame-Options: SAMEORIGIN\n")
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
