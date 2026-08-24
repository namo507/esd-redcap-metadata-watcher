"""Tests for the lab's physical limits, all transcribed from the manual.

These are the rules that stop a visit happening whatever anyone scores: there
is no third tech kit, the university is shut, nobody on the visit can drive the
van. They are separate from the weighted score on purpose. A good score is not
allowed to argue with a locked cupboard.

Run:  python3 tests/test_resources.py
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from esd_scheduler.resources import LabResources  # noqa: E402

RES = LabResources.load()


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_there_are_two_nano_kits():
    """"No more than 2 NANO visits can happen at one time"."""
    expect(RES.kit_ceiling("NANO") == 2,
           f"NANO ceiling is {RES.kit_ceiling('NANO')}, manual says 2")
    expect(RES.kit_ceiling("NICO") is None,
           "a protocol with no kit limit should be unlimited, not zero")


def test_friday_is_held_for_lab_meetings():
    friday = date(2026, 8, 21)
    thursday = date(2026, 8, 20)
    expect(RES.closed_on(friday), "Friday is not marked closed")
    expect("lab meeting" in RES.closed_on(friday),
           f"Friday's reason does not mention lab meetings: {RES.closed_on(friday)}")
    expect(RES.closed_on(thursday) is None, "Thursday was marked closed")


def test_an_empty_holiday_list_is_not_a_claim_that_there_are_none():
    """The manual gives the rule but not the dates.

    Shipping an empty list and treating it as "no holidays" would schedule
    straight through one, which the manual calls out as having no exceptions.
    """
    expect(RES.holidays_known is False or RES.holidays,
           "holidays_known should be False while the list is empty")


def test_a_holiday_cannot_be_overridden_but_a_friday_can():
    """The manual: Fridays need approval, holidays have "no exceptions"."""
    res = LabResources(closed_weekdays=[4], holidays=[date(2026, 11, 26)])
    expect("no exceptions" in (res.closed_on(date(2026, 11, 26)) or ""),
           "the holiday reason does not carry the manual's wording")
    expect("lab meeting" in (res.closed_on(date(2026, 8, 21)) or ""),
           "the Friday reason changed")


def test_out_of_hours_is_the_manuals_definition():
    """"beyond 30 minutes outside of 9am-5pm on Monday-Friday"."""
    cases = [
        ((datetime(2026, 8, 20, 10, 0), datetime(2026, 8, 20, 12, 0)), False),
        ((datetime(2026, 8, 20, 8, 45), datetime(2026, 8, 20, 10, 0)), False),
        ((datetime(2026, 8, 20, 7, 0), datetime(2026, 8, 20, 9, 0)), True),
        ((datetime(2026, 8, 20, 16, 0), datetime(2026, 8, 20, 18, 0)), True),
        ((datetime(2026, 8, 22, 10, 0), datetime(2026, 8, 22, 12, 0)), True),
    ]
    for (start, end), want in cases:
        got = RES.is_out_of_hours(start, end)
        expect(got == want,
               f"{start:%a %H:%M}-{end:%H:%M} read as "
               f"{'out of hours' if got else 'normal'}, expected the opposite")


def test_the_out_of_hours_rotation_is_a_cycle_not_a_tally():
    """"should not be scheduled for another one until all of the other
    clinicians/techs have gone on one"."""
    up = lambda counts: sorted(k for k, v in RES.out_of_hours_turn(counts).items() if v)
    expect(up({"a": 0, "b": 0, "c": 0}) == ["a", "b", "c"],
           "with nobody having gone, everybody should be up")
    expect(up({"a": 1, "b": 0, "c": 0}) == ["b", "c"],
           "somebody who has gone should wait for the others")
    expect(up({"a": 1, "b": 1, "c": 0}) == ["c"],
           "the last person left should be the only one up")
    expect(up({"a": 1, "b": 1, "c": 1}) == ["a", "b", "c"],
           "a completed cycle should put everyone back up")
    expect(up({"a": 2, "b": 1, "c": 1}) == ["b", "c"],
           "two visits ahead is still out, not merely behind")


def test_the_van_goes_where_the_manual_sends_it():
    both = RES.vehicle_for(drive_minutes=30, van_trained_on_visit=2)
    one = RES.vehicle_for(drive_minutes=30, van_trained_on_visit=1)
    none = RES.vehicle_for(drive_minutes=30, van_trained_on_visit=0)
    blocked = RES.vehicle_for(drive_minutes=30, van_trained_on_visit=2,
                              van_inaccessible=True)
    taken = RES.vehicle_for(drive_minutes=30, van_trained_on_visit=2,
                            van_taken=True)
    expect(both[0] == "van", "two van-trained staff should take the van")
    expect(one[0] == "van", "one approved driver is enough for the van")
    expect(none[0] == "rental", "nobody trained should mean the rental")
    expect(blocked[0] == "rental",
           "a van-inaccessible home should never be sent the van")
    expect(taken[0] == "rental", "the van cannot go out twice at once")


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
