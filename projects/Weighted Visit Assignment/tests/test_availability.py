"""Tests for combining per-coordinator calendars into who is free when.

The property that matters is the one a grid makes easy to lose: a coordinator
the board has never synced must read as *unknown*, not free. A grid that
promoted missing data to availability would look complete and send someone to
a family at a time nobody checked.

Run:  python3 tests/test_availability.py
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from esd_scheduler.availability import (  # noqa: E402
    coverage_report,
    day_grid,
    week_grid,
)
from esd_scheduler.demo import build_lab  # noqa: E402
from esd_scheduler.models import BusyBlock  # noqa: E402

NOW = datetime(2026, 8, 17, 9, 0)


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def _lab():
    return build_lab(NOW)[0]


def test_every_coordinator_is_accounted_for_in_every_slot():
    state = _lab()
    team = len(list(state.active_coordinators()))
    for slot in day_grid(state, date(2026, 8, 17), NOW):
        total = len(slot.free) + len(slot.busy) + len(slot.unknown)
        expect(total == team,
               f"{slot.start:%H:%M}: {total} accounted for, team is {team}")


def test_a_coordinator_with_no_calendar_is_unknown_not_free():
    state = _lab()
    victim = next(iter(state.calendars))
    del state.calendars[victim]
    for slot in day_grid(state, date(2026, 8, 17), NOW):
        expect(victim not in slot.free,
               "a coordinator with no calendar was reported free")
        expect(victim in slot.unknown,
               "a coordinator with no calendar should be unknown")


def test_a_failed_sync_is_unknown_not_free():
    state = _lab()
    victim = next(iter(state.calendars))
    state.calendars[victim].sync_ok = False
    for slot in day_grid(state, date(2026, 8, 17), NOW):
        expect(victim not in slot.free, "a failed sync was reported as free")


def test_a_busy_block_removes_exactly_the_slots_it_covers():
    state = _lab()
    who = next(iter(state.calendars))
    start = datetime(2026, 8, 17, 13, 0)
    end = datetime(2026, 8, 17, 14, 0)
    state.calendars[who].blocks.append(BusyBlock(start=start, end=end, status="busy"))
    for slot in day_grid(state, date(2026, 8, 17), NOW):
        overlaps = slot.start < end and start < slot.end
        if overlaps:
            expect(who in slot.busy,
                   f"{slot.start:%H:%M} overlaps a busy block but is not busy")


def test_coverage_names_who_is_outstanding():
    state = _lab()
    victim = next(iter(state.calendars))
    name = state.coordinators[victim].name
    del state.calendars[victim]
    report = coverage_report(state, NOW)
    expect(not report["complete"], "coverage claimed complete with one missing")
    expect(name in report["outstanding"],
           f"{name} is missing but not named: {report['outstanding']}")
    expect(report["counts"]["missing"] == 1,
           f"wrong missing count: {report['counts']}")


def test_coverage_is_complete_when_every_calendar_is_current():
    state = _lab()
    for snapshot in state.calendars.values():
        snapshot.fetched_at = NOW
    report = coverage_report(state, NOW)
    expect(report["complete"], f"expected complete, got {report['counts']}")
    expect(not report["outstanding"], f"outstanding: {report['outstanding']}")


def test_a_stale_calendar_is_reported_as_stale():
    state = _lab()
    who = next(iter(state.calendars))
    state.calendars[who].fetched_at = NOW - timedelta(hours=6)
    report = coverage_report(state, NOW, fresh_minutes=90)
    row = next(r for r in report["rows"] if r["coordinator_id"] == who)
    expect(row["state"] == "stale", f"expected stale, got {row['state']}")


def test_the_week_grid_covers_five_working_days():
    state = _lab()
    week = week_grid(state, date(2026, 8, 17), NOW)
    expect(len(week) == 5, f"expected 5 days, got {len(week)}")
    expect([d["day"] for d in week] == [
        "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"],
        "the week does not run Monday to Friday")
    for day in week:
        expect(day["slots"], f"{day['day']} has no slots")
        expect(day["best"] <= len(list(state.active_coordinators())),
               "more people free than exist")


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
