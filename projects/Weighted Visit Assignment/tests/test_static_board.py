"""Pin the static build against the live engine.

The public copy has no Python, so StaticBoard recomputes one thing in the
browser: the burden term. This asserts the snapshot it reads is the engine's own
output, and that the formula StaticBoard applies reproduces the engine's values
for the unassigned state. A drift between esd_scheduler/scoring.py and
frontend/static-board.js becomes a failing test rather than a quiet
disagreement on a public URL.

Run:  python3 tests/test_static_board.py
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist-static")

from backend.session import LabSession  # noqa: E402


def board():
    with open(os.path.join(DIST, "board.json"), encoding="utf-8") as fh:
        return json.load(fh)


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_bundle_is_complete_and_self_contained():
    for name in ("index.html", "app.js", "static-board.js", "styles.css", "board.json"):
        expect(os.path.isfile(os.path.join(DIST, name)), f"missing {name}")
    html = open(os.path.join(DIST, "index.html"), encoding="utf-8").read()
    # Absolute paths would 404 on a host that serves from a subpath.
    expect('src="/app.js"' not in html and 'href="/styles.css"' not in html,
           "bundle still references absolute asset paths")
    expect("static-board.js" in html, "static adapter not loaded")


def test_snapshot_matches_the_live_engine():
    data = board()
    session = LabSession(os.path.join(ROOT, "data", "static-test.db"))
    expect(len(data["visits"]) == len(session.order), "visit count differs")
    for i, visit_id in enumerate(session.order):
        live = session.candidates(visit_id)
        baked = data["visits"][i]
        expect(live["visit"]["id"] == baked["visit"]["id"], f"order differs at {i}")
        lc = {c["id"]: round(c["score"], 6) for c in live["candidates"]}
        bc = {c["id"]: round(c["score"], 6) for c in baked["candidates"]}
        expect(lc == bc, f"{visit_id}: scores differ")
        expect(live["recommended_id"] == baked["recommended_id"],
               f"{visit_id}: recommendation differs")
        expect({e["id"] for e in live["excluded"]} == {e["id"] for e in baked["excluded"]},
               f"{visit_id}: exclusions differ")
    session.store.close()


def test_browser_burden_formula_reproduces_the_engine():
    """The one formula StaticBoard recomputes, checked against the snapshot."""
    data = board()
    gamma = data["meta"]["gammaTravel"]
    roster = {r["id"]: r for r in data["roster"]}
    for v in data["visits"]:
        duration = v["visit"]["duration_hours"]
        for c in v["candidates"]:
            person = roster[c["id"]]
            psi = next(x["value"] for x in c["contributions"] if x["key"] == "psi")
            burden = person["committed_hours"] + duration + gamma * c["travel_minutes"] / 60
            cap = person["effective_capacity_hours"]
            recomputed = 0.0 if cap <= 0 else 1 - min(1, max(0, burden / cap))
            expect(abs(recomputed - psi) < 0.002,
                   f"{v['visit']['id']} {c['name']}: psi {recomputed:.4f} vs {psi}")


def test_weighted_sum_reproduces_every_score():
    data = board()
    w = data["meta"]["weights"]
    for v in data["visits"]:
        for c in v["candidates"]:
            vals = {x["key"]: x["value"] for x in c["contributions"]}
            total = (w["phi"] * vals["phi"] + w["omega"] * vals["omega"]
                     + w["psi"] * vals["psi"] + w["p"] * vals["p"])
            expect(abs(total - c["score"]) < 0.002,
                   f"{v['visit']['id']} {c['name']}: {total:.4f} vs {c['score']}")


def test_recommendation_is_the_first_assignable():
    for v in board()["visits"]:
        assignable = [c for c in v["candidates"] if c["assignable"]]
        expected = assignable[0]["id"] if assignable else None
        expect(v["recommended_id"] == expected,
               f"{v['visit']['id']}: recommendation is not the first assignable")


def test_no_event_titles_anywhere_in_the_public_payload():
    """The board reads free/busy only. Nothing on a public URL may say otherwise."""
    raw = open(os.path.join(DIST, "board.json"), encoding="utf-8").read()
    for banned in ('"subject"', '"isPrivate"', "scheduleItems"):
        expect(banned not in raw, f"public payload contains {banned}")


def test_exclusions_are_plain_language():
    for v in board()["visits"]:
        for e in v["excluded"]:
            expect(e["reason"] and not re.match(r"^[a-z_]+(:|$)", e["reason"]),
                   f"raw code on a public page: {e['reason']}")


def test_demonstration_notice_is_visible_not_buried():
    html = open(os.path.join(DIST, "index.html"), encoding="utf-8").read()
    expect("demo-note" in html, "no demonstration notice")
    hero_end = html.find("</section>", html.find('class="hero"'))
    expect(0 < html.find("demo-note") < hero_end,
           "the demonstration notice must sit in the hero, not the footer")


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
