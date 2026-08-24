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


def _week_pdf() -> bytes:
    """The synthetic work-week print, as bytes, for upload tests."""
    from make_work_week_pdf import build
    return open(build(os.path.join(TMP, "week.pdf")), "rb").read()


A_VISIT = {
    "family_id": "5031",
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
    expect(again.state.families.get("5031") is not None,
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
    s.add_visit(dict(A_VISIT, family_id="5031"))
    s.add_visit(dict(A_VISIT, family_id="5042", completed_through="3m"))
    rows = {r["family_id"]: r for r in s.schedule_rows()["rows"]}
    expect(rows["5031"]["checkpoint"] == "1m",
           "a family with no history should owe their first checkpoint")
    expect(rows["5042"]["checkpoint"] == "6m",
           f"a family seen to 3m should owe 6m, not {rows['5042']['checkpoint']}")
    expect(rows["5042"]["completed"] == 3,
           f"expected 3 completed, got {rows['5042']['completed']}")


def test_seeded_history_credits_nobody_with_the_visit():
    """Continuity rewards whoever ran the last visit, so it cannot be guessed."""
    s = live_session("credit.db")
    s.add_visit(dict(A_VISIT, completed_through="3m"))
    seeded = [h for h in s.state.history if h.family_id == "5031"]
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


def test_the_week_anchor_is_never_in_the_future():
    """Nine hours a week, the Monday 09:00 anchor had not happened yet.

    The board stamps its calendar evidence against this anchor. Ahead of the
    clock it produces evidence with a negative age, the freshness gate reads
    that as never synced, and the entire roster is vetoed for no reason but
    the hour the server started. CI ran at 00:33 UTC on a Monday and caught
    it; every earlier run had happened later in the day.
    """
    from datetime import timedelta
    from backend.session import week_epoch

    start = datetime(2026, 8, 24, 0, 0)          # a Monday, midnight
    ahead = []
    for hour in range(24 * 14):                  # two full weeks, hour by hour
        now = start + timedelta(hours=hour)
        epoch = week_epoch(now)
        if epoch > now:
            ahead.append((now.isoformat(), epoch.isoformat()))
    expect(not ahead, f"the anchor is ahead of the clock at: {ahead[:4]}")

    # And it still anchors on Monday once Monday morning has actually arrived.
    expect(week_epoch(datetime(2026, 8, 26, 15, 0)) == datetime(2026, 8, 24, 9, 0),
           "mid-week no longer anchors to Monday 09:00")


def test_a_nano_24m_timepoint_is_never_staffed():
    """The manual: "we do not see participants for an in-person visit"."""
    s = live_session("remote.db")
    s.add_visit(dict(A_VISIT, checkpoint="24m"))
    detail = s.candidates(s.order[0])
    expect(not detail.get("pairs"),
           f"24m offered {len(detail['pairs'])} staffed pairings")
    expect(detail.get("remote") is True, "24m is not reported as remote")
    codes = [n.get("code") for n in detail.get("notices", [])]
    expect("REMOTE_CHECKPOINT" in codes,
           f"nothing explains why there is no one to send: {codes}")


def test_an_in_person_timepoint_is_still_staffed():
    """The remote rule must not quietly empty the rest of the board."""
    s = live_session("inperson.db")
    s.upload_calendar_pdf("week.pdf", _week_pdf())
    s.add_visit(dict(A_VISIT, checkpoint="9m"))
    detail = s.candidates(s.order[0])
    expect(detail.get("pairs"), "a 9m visit offered nobody at all")


def test_visit_length_comes_from_the_manual_not_a_default():
    """Entering a visit without a length should use the protocol's own."""
    s = live_session("lengths.db")
    want = {"1m": 1.0, "3m": 1.5, "9m": 2.0, "24m": 1.0, "36m": 3.0}
    for checkpoint in want:
        s.add_visit(dict(A_VISIT, checkpoint=checkpoint))
    got = {q["checkpoint"]: q["duration_hours"] for q in s.queue()}
    for checkpoint, hours in want.items():
        expect(got.get(checkpoint) == hours,
               f"{checkpoint} came out {got.get(checkpoint)}h, manual says {hours}h")


def test_a_participant_id_that_is_not_a_nano_id_is_refused():
    """The manual: "the child's 4 digit NANO ID that starts with 5".

    An ID is how Access, the calendar invite and the visit folder all refer to
    the same child. A typo caught here costs a second; the same typo caught on
    the doorstep costs a visit.
    """
    s = live_session("ids.db")
    for good, expected in (("5031", "5031"), ("F5031", "5031"), (" 5042 ", "5042")):
        out = s.add_visit(dict(A_VISIT, family_id=good, visit_id=f"V{good.strip()}"))
        expect(out["family_id"] == expected,
               f"{good!r} was stored as {out['family_id']!r}")
    for bad in ("6031", "50311", "503", "abc", ""):
        try:
            s.add_visit(dict(A_VISIT, family_id=bad))
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} was accepted as a NANO participant ID")


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
