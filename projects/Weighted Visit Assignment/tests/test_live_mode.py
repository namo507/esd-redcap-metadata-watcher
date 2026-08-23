"""Tests for a board running on real data instead of the synthetic lab.

The demo exists to exercise the engine, and it is the right default. What it
must never do is blend into a real board: a coordinator looking at entered
visits and uploaded calendars should not be reading invented families, invented
busy time, or a page that still calls itself a demonstration.

Run:  python3 tests/test_live_mode.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures"))

TMP = tempfile.mkdtemp(prefix="esd-live-")


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def live_session(name="live.db"):
    """A session in live mode, on its own database."""
    os.environ["ESD_MODE"] = "live"
    from backend.session import LabSession
    return LabSession(db_path=os.path.join(TMP, name))


def demo_session(name="demo.db"):
    os.environ["ESD_MODE"] = "demo"
    from backend.session import LabSession
    return LabSession(db_path=os.path.join(TMP, name))


A_VISIT = {
    "family_id": "F9100",
    "protocol": "NANO",
    "checkpoint": "6m",
    "window_start": "2026-08-20T09:00:00",
    "window_end": "2026-08-23T17:00:00",
    "anchor_date": "2026-02-20",
}


def test_demo_is_still_the_default():
    """Nothing changes for anyone who has not asked for live mode."""
    os.environ.pop("ESD_MODE", None)
    from backend.session import board_mode
    expect(board_mode() == "demo", "an unset ESD_MODE should still mean demo")


def test_a_live_board_invents_nothing():
    s = live_session("empty.db")
    expect(s.mode == "live", f"mode is {s.mode!r}")
    expect(len(s.visits) == 0, f"a live board starts with {len(s.visits)} visits")
    expect(not s.state.families, "a live board starts with invented families")
    expect(not s.state.history, "a live board starts with invented history")
    expect(s.state.coordinators, "the roster should still be there")


def test_nobody_is_free_until_a_calendar_is_read():
    """The whole safety rule in one place: unknown is not free."""
    s = live_session("unknown.db")
    s.add_visit(dict(A_VISIT))
    detail = s.candidates(s.order[0])
    expect(not detail.get("pairs"),
           "a board with no calendars offered a pairing anyway")
    reasons = " ".join(str(e.get("reason", "")) for e in detail.get("excluded", []))
    expect("calendar" in reasons.lower(),
           f"nobody was held back for want of a calendar: {reasons[:200]}")


def test_the_board_says_where_its_calendars_came_from():
    """`calendar_source` was hardcoded to demo, which read as a claim."""
    s = live_session("source.db")
    expect(s.health()["calendar_source"] == "none",
           "a live board with no upload should say none")
    expect(demo_session("source-demo.db").health()["calendar_source"] == "demo",
           "the demo board should still say demo")


def test_an_entered_visit_survives_a_restart():
    s = live_session("persist.db")
    s.add_visit(dict(A_VISIT))
    vid = s.order[0]
    again = live_session("persist.db")
    expect(vid in again.visits,
           f"visit {vid} did not come back after a restart")
    expect(again.state.families.get("F9100") is not None,
           "the family did not come back with it")


def test_a_removed_visit_stays_removed():
    s = live_session("removed.db")
    s.add_visit(dict(A_VISIT))
    vid = s.order[0]
    expect(s.remove_visit(vid), "removing an entered visit reported failure")
    expect(s.remove_visit(vid) is False, "removing it twice reported success")
    expect(vid not in live_session("removed.db").visits,
           "a removed visit came back after a restart")


def test_a_visit_the_engine_cannot_use_is_refused():
    s = live_session("refuse.db")
    for bad, why in (
        ({}, "nothing at all"),
        (dict(A_VISIT, window_start="not a date"), "an unreadable date"),
        (dict(A_VISIT, window_start="2026-08-23T09:00:00",
              window_end="2026-08-20T17:00:00"), "a window that ends first"),
    ):
        try:
            s.add_visit(bad)
        except ValueError:
            continue
        raise AssertionError(f"the board accepted {why}")


def test_the_protocol_clock_believes_what_the_lab_says_was_done():
    """A family seen up to 3m is not overdue for their first visit."""
    s = live_session("history.db")
    s.add_visit(dict(A_VISIT, family_id="F9100"))
    s.add_visit(dict(A_VISIT, family_id="F9200", completed_through="3m"))
    rows = {r["family_id"]: r for r in s.schedule_rows()["rows"]}
    expect(rows["F9100"]["checkpoint"] == "1m",
           "a family with no history should owe their first checkpoint")
    expect(rows["F9200"]["checkpoint"] == "6m",
           f"a family seen to 3m should owe 6m, not {rows['F9200']['checkpoint']}")
    expect(rows["F9200"]["completed"] == 3,
           f"expected 3 completed, got {rows['F9200']['completed']}")


def test_seeded_history_credits_nobody_with_the_visit():
    """Continuity rewards whoever ran the last visit, so it cannot be guessed."""
    s = live_session("credit.db")
    s.add_visit(dict(A_VISIT, completed_through="3m"))
    seeded = [h for h in s.state.history if h.family_id == "F9100"]
    expect(seeded, "no history was seeded")
    expect(all(h.coordinator_id == "" for h in seeded),
           "a seeded past visit named a coordinator who was never recorded")


def test_the_weight_label_moves_with_the_weights():
    """A label that stays put while the numbers change is a mislabel."""
    from esd_scheduler.config import WeightVector, load_config
    a = load_config()
    b = load_config()
    b.weights = WeightVector(phi=0.05, omega=0.80, psi=0.10, p=0.05)
    expect(a.vector_id() != b.vector_id(),
           f"both weight sets report the same id {a.vector_id()!r}")
    expect(a.weight_vector_id in a.vector_id(),
           "the human label should still be readable in the id")


def test_changing_a_weight_does_not_overwrite_the_old_one():
    """Past decisions have to stay traceable to the numbers that made them."""
    from esd_scheduler.config import WeightVector, load_config
    from esd_scheduler.store import AuditStore
    store = AuditStore(os.path.join(TMP, "weights.db"))
    first = load_config()
    store.record_config(first)
    second = load_config()
    second.weights = WeightVector(phi=0.05, omega=0.80, psi=0.10, p=0.05)
    store.record_config(second)
    rows = store.query("SELECT weight_vector_id, w_phi FROM weight_vector")
    expect(len(rows) == 2,
           f"expected both weight sets on file, found {len(rows)}")
    expect({round(r[1], 2) for r in rows} == {0.45, 0.05},
           "the stored rows do not hold both sets of numbers")


def test_a_bad_weight_set_says_what_is_wrong_in_readable_numbers():
    from esd_scheduler.config import WeightVector
    try:
        WeightVector(phi=0.5, omega=0.2, psi=0.2, p=0.2).validate()
    except ValueError as exc:
        text = str(exc)
        expect("1.0999" not in text,
               f"the float noise is still printed: {text}")
        expect("1.1" in text, f"the actual total is not shown: {text}")
        return
    raise AssertionError("a weight set summing to 1.1 was accepted")


def test_a_lab_calendar_is_not_reported_as_an_unknown_person():
    """The lab's own calendars were being listed as a failure to attribute."""
    import fitz  # noqa: F401  -- the fixture needs it
    from make_work_week_pdf import build
    from esd_scheduler.calendar_import import import_pdf
    from esd_scheduler.demo import build_lab

    pdf = build(os.path.join(TMP, "roles.pdf"))
    state, _ = build_lab(datetime(2026, 8, 17, 9, 0))
    result = import_pdf(pdf, coordinators=state.coordinators, year_hint=2026)
    notes = " ".join(result.notes)
    for name in ("Offered Times ESD", "Clinician Shifts"):
        expect(name not in notes.split("Not on the roster")[-1]
               or "Read as lab calendars" in notes,
               f"{name} is still reported as an unattributed person")
    expect("Read as lab calendars" in notes,
           f"the lab's own calendars are not explained at all: {notes[:200]}")


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
    os.environ.pop("ESD_MODE", None)
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
