"""Tests for the protocol clock and the exhaustive gate enumeration.

Two properties matter most here and neither is checkable by example:

* a due date is arithmetic, so it must never appear where the inputs cannot
  support one;
* every hard gate must stay reachable, and the reason shown must always be the
  highest-priority one.

Run:  python3 tests/test_schedule.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp(prefix="esd-sched-")
os.environ.setdefault("ESD_CALENDAR_ROLES_PATH", os.path.join(TMP, "roles.json"))

from esd_scheduler.demo import build_lab  # noqa: E402
from esd_scheduler.models import Family  # noqa: E402
from esd_scheduler.scenarios import (  # noqa: E402
    GATE_ORDER,
    coverage,
    enumerate_scenarios,
    resource_matrix,
)
from esd_scheduler.schedule import (  # noqa: E402
    STATUS_CLOSING,
    STATUS_COMPLETE,
    STATUS_OPEN,
    STATUS_OVERDUE,
    STATUS_UNKNOWN,
    STATUS_UPCOMING,
    Checkpoint,
    ProtocolSchedule,
    next_visit_for,
    upcoming,
    urgency_of,
)

NOW = datetime(2026, 8, 17, 9, 0)


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def _schedule():
    return ProtocolSchedule(
        checkpoints={"NICO": [
            Checkpoint("6mo", 180, 30, 30),
            Checkpoint("12mo", 360, 30, 30),
        ]},
        confirmed=True,
    )


def _family(anchor):
    return Family(family_id="FT", protocol="NICO", anchor_date=anchor)


# --- due dates --------------------------------------------------------------


def test_a_family_with_no_anchor_is_unknown_not_overdue():
    """Missing data is not evidence of lateness."""
    row = next_visit_for(_family(None), [], NOW, _schedule())
    expect(row.status == STATUS_UNKNOWN, f"expected unknown, got {row.status}")
    expect(row.target_date is None, "a due date was invented without an anchor")
    expect(row.urgency == 0.0, "an unknown family must carry no urgency")


def test_each_window_position_gets_the_right_status():
    sched = _schedule()
    # First checkpoint is anchor + 180 days, window +/- 30.
    cases = {
        -45: STATUS_OVERDUE,     # target 45 days ago, window closed 15 days ago
        -20: STATUS_CLOSING,     # window closes in 10 days
        -5: STATUS_OPEN,         # window closes in 25 days
        40: STATUS_UPCOMING,     # window opens in 10 days
    }
    for delta, want in cases.items():
        anchor = (NOW - timedelta(days=180) + timedelta(days=delta)).date()
        row = next_visit_for(_family(anchor), [], NOW, sched)
        expect(row.status == want,
               f"anchor offset {delta}: expected {want}, got {row.status}")


def test_urgency_is_bounded_and_rises_as_the_window_closes():
    seen = []
    for remaining in range(60, -1, -5):
        u = urgency_of(STATUS_OPEN, remaining, 60)
        expect(0.0 <= u <= 1.0, f"urgency {u} out of bounds")
        seen.append(u)
    expect(seen == sorted(seen), f"urgency did not rise monotonically: {seen}")
    expect(urgency_of(STATUS_OVERDUE, -10, 60) == 1.0, "overdue must be 1.0")
    expect(urgency_of(STATUS_UPCOMING, 90, 60) == 0.0, "not-yet-due must be 0.0")
    expect(urgency_of(STATUS_COMPLETE, None, 60) == 0.0, "complete must be 0.0")


def test_a_completed_checkpoint_advances_to_the_next_one():
    sched = _schedule()
    anchor = (NOW - timedelta(days=180)).date()

    class Done:
        family_id = "FT"
        checkpoint = "6mo"
        no_show = False

    row = next_visit_for(_family(anchor), [Done()], NOW, sched)
    expect(row.checkpoint == "12mo", f"did not advance: {row.checkpoint}")
    expect(row.completed == 1, f"completed count wrong: {row.completed}")


def test_a_no_show_does_not_count_as_completed():
    sched = _schedule()

    class NoShow:
        family_id = "FT"
        checkpoint = "6mo"
        no_show = True

    row = next_visit_for(_family((NOW - timedelta(days=180)).date()),
                         [NoShow()], NOW, sched)
    expect(row.checkpoint == "6mo", "a no-show was treated as a completed visit")


def test_finishing_every_checkpoint_reports_complete():
    sched = _schedule()

    class Done:
        no_show = False

        def __init__(self, cp):
            self.family_id = "FT"
            self.checkpoint = cp

    row = next_visit_for(_family(date(2020, 1, 1)),
                         [Done("6mo"), Done("12mo")], NOW, sched)
    expect(row.status == STATUS_COMPLETE, f"expected complete, got {row.status}")
    expect(row.checkpoint is None, "a completed protocol should owe nothing")


def test_the_queue_puts_actionable_families_first():
    state, _ = build_lab(NOW)
    rows = upcoming(state.families, state.history, NOW)
    tiers = [r.status for r in rows]
    rank = {STATUS_OVERDUE: 0, STATUS_CLOSING: 1, STATUS_OPEN: 2,
            STATUS_UPCOMING: 3, STATUS_UNKNOWN: 4, STATUS_COMPLETE: 5}
    ranks = [rank[t] for t in tiers]
    expect(ranks == sorted(ranks), f"ordering is not by urgency tier: {tiers}")


def test_the_shipped_schedule_matches_the_manual():
    """Transcribed from the manual's Visit Windows and Visit Lengths tables.

    Pinned so a later edit cannot quietly drift from the document the lab
    actually schedules by. If the manual changes, this test is the place that
    says so.
    """
    sched = ProtocolSchedule.load()
    expect(sched.confirmed, "the schedule should be confirmed against the manual")
    nano = {c.name: c for c in sched.for_protocol("NANO")}
    expect(nano, "NANO is missing from the shipped schedule")

    # (window before, window after, hours) exactly as the manual states them.
    for name, before, after, hours in (
            ("1m", 5, 7, 1.0),
            ("3m", 5, 7, 1.5),
            ("6m", 14, 28, 1.5),
            ("9m", 14, 28, 2.0),
            ("12m", 14, 42, 2.0),
            ("24m", 14, 42, 1.0),
            ("36m", 0, 364, 3.0)):
        row = nano.get(name)
        expect(row is not None, f"NANO {name} is missing")
        expect((row.window_before, row.window_after) == (before, after),
               f"{name} window is -{row.window_before}/+{row.window_after}, "
               f"manual says -{before}/+{after}")
        expect(row.duration_hours == hours,
               f"{name} runs {row.duration_hours}h, manual says {hours}h")


# --- exhaustive gate enumeration -------------------------------------------


def test_every_gate_is_reachable():
    counts = coverage(NOW)
    dead = [gate for gate, n in counts.items() if n == 0]
    expect(not dead,
           f"these gates never fire, so they are rules nothing enforces: {dead}")


def test_the_reason_shown_is_always_the_highest_priority_one():
    mismatches = [
        (s.factors, s.reason, s.expected_first)
        for s in enumerate_scenarios(NOW)
        if s.expected_first and s.reason != s.expected_first
    ]
    expect(not mismatches,
           f"the board would explain the same situation inconsistently: {mismatches[:3]}")


def test_no_scenario_is_vetoed_for_a_reason_nothing_asked_for():
    surprises = [
        (s.factors, s.reason) for s in enumerate_scenarios(NOW)
        if not s.expected_first and not s.passed
    ]
    expect(not surprises, f"unexplained vetoes: {surprises[:3]}")


def test_a_candidate_with_nothing_against_them_passes():
    clean = [s for s in enumerate_scenarios(NOW) if s.passed]
    expect(clean, "no combination of factors produced an eligible candidate")
    for s in clean:
        expect(not s.expected_reasons,
               f"a scenario passed despite expecting {s.expected_reasons}")


def test_every_gate_name_is_in_the_documented_order():
    for s in enumerate_scenarios(NOW):
        if s.reason:
            expect(s.reason in GATE_ORDER,
                   f"gate {s.reason!r} fired but is not in the documented order")


def test_policy_calendars_never_pass_on_absence():
    """An absent calendar must read as not-applicable, never as approval."""
    for row in resource_matrix():
        if not row["offered_calendar"]:
            expect(row["offered"] == "not_applicable",
                   "an absent offered-times calendar was treated as a pass")
        if row["requires_clinician"] and not row["shift_calendar"]:
            expect(row["clinician"] == "not_applicable",
                   "an absent shift calendar was treated as a pass")
        if row["in_lab"] and not row["lab_calendar"]:
            expect(row["lab"] == "not_applicable",
                   "an absent lab calendar was treated as a pass")
        if not row["in_lab"]:
            expect(row["lab"] == "not_applicable",
                   "the lab gate fired on a visit not held in the lab")


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
