"""Tests for the roster being data rather than code.

Adding, removing or changing a coordinator has to work without editing Python,
because the lab's staffing changes and this project's whole claim is that the
rules live in config. The property that matters most is that removing somebody
is safe: they leave scheduling without taking their history with them.

Run:  python3 tests/test_roster.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from esd_scheduler.roster import Roster  # noqa: E402

TMP = tempfile.mkdtemp(prefix="esd-roster-")
NOW = datetime(2026, 8, 17, 9, 0)


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def _write(rows, path=None):
    path = path or os.path.join(TMP, "roster.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"coordinators": rows}, fh)
    return path


def test_a_new_person_needs_only_a_row():
    path = _write([
        {"id": "C01", "name": "Existing Person"},
        {"id": "C99", "name": "Brand New", "capacity_hours_week": 12.0,
         "van_trained": True},
    ])
    roster = Roster.load(path)
    expect(len(roster.active) == 2, f"expected 2 active, got {len(roster.active)}")
    new = roster.by_id()["C99"]
    expect(new.capacity_hours_week == 12.0, "capacity did not come through")
    expect(new.van_trained, "van training did not come through")


def test_removing_somebody_is_a_flag_not_a_deletion():
    """Their history still references them, so the row has to stay."""
    path = _write([
        {"id": "C01", "name": "Still Here"},
        {"id": "C02", "name": "Left The Lab", "active": False},
    ])
    roster = Roster.load(path)
    expect([e.id for e in roster.active] == ["C01"],
           "an inactive coordinator is still being scheduled")
    expect("C02" in roster.by_id(),
           "the departed coordinator vanished entirely, taking their history "
           "out of reach")


def test_a_row_without_an_id_or_name_is_ignored():
    path = _write([
        {"id": "C01", "name": "Fine"},
        {"name": "No id"},
        {"id": "C03"},
    ])
    roster = Roster.load(path)
    expect(len(roster.entries) == 1, f"expected 1 usable row, got {len(roster.entries)}")


def test_a_name_resolves_the_way_the_manual_writes_it():
    path = _write([
        {"id": "C01", "name": "Margaret Bell", "manual_name": "Maggie"},
        {"id": "C02", "name": "Lauren Puttock", "manual_name": "Lauren"},
    ])
    roster = Roster.load(path)
    expect(roster.resolve("Maggie") == "C01", "the manual's name did not resolve")
    expect(roster.resolve("Margaret Bell") == "C01", "the full name did not resolve")
    expect(roster.resolve("Lauren") == "C02", "a first name did not resolve")
    expect(roster.resolve("Nobody Here") is None, "an unknown name resolved")


def test_a_shared_first_name_resolves_to_nobody():
    """Half a match is not a match when it decides whose calendar this is."""
    path = _write([
        {"id": "C01", "name": "Lauren Puttock"},
        {"id": "C02", "name": "Lauren Smith"},
    ])
    roster = Roster.load(path)
    expect(roster.resolve("Lauren") is None,
           "an ambiguous first name was resolved to one of them anyway")


def test_the_shipped_roster_loads_and_matches_the_board():
    from esd_scheduler.demo import build_lab

    roster = Roster.load()
    expect(roster.active, "the shipped roster is empty")
    state, _ = build_lab(NOW)
    expect({e.id for e in roster.active} == set(state.coordinators),
           "the lab was not built from the roster file")
    for entry in roster.active:
        coord = state.coordinators[entry.id]
        expect(coord.name == entry.name, f"{entry.id}: name differs")
        expect(coord.van_trained == entry.van_trained,
               f"{entry.id}: van training differs")


def test_the_solo_column_decides_who_can_be_the_clinician():
    """The chart's first column is headed "Name (Visits Can Do Solo)".

    A range printed beside a name is what lets that person be THE clinician.
    Ramiro is reliable in Bayley 9-12m and has no range, so he techs a 9m
    visit and never runs it. Reading the assessment list alone would put him
    in the wrong seat.
    """
    from esd_scheduler.roster import Roster
    roster = Roster.load()
    by_id = roster.by_id()
    expected = {
        "C02": ("1m", "12m"),      # Lauren
        "C03": ("1m", "12m"),      # Sanjana
        "C05": ("6m", "12m"),      # Makenzie, printed "Soto, Morgan"
    }
    for cid, (lo, hi) in expected.items():
        entry = by_id[cid]
        expect((entry.solo_from, entry.solo_to) == (lo, hi),
               f"{entry.name} has range {entry.solo_from}-{entry.solo_to}, "
               f"manual says {lo}-{hi}")
    for cid in ("C01", "C04", "C06"):            # Maggie, Sofia, Ramiro
        entry = by_id[cid]
        expect(not roster.can_be_clinician_for(entry, "9m"),
               f"{entry.name} has no range in the manual but was allowed to "
               f"be the clinician on a 9m visit")


def test_one_person_with_two_names_is_one_row():
    """The calendar prints "Soto, Morgan"; the manual calls them Makenzie.

    Both names have to land on the same id. They were once two rows, which
    is the quiet kind of wrong: the same human would hold competencies under
    one name and none under the other, be busy on one calendar and free on
    the other, and the board would happily double-book them against
    themselves. Whichever name a document uses, it resolves here.
    """
    from esd_scheduler.roster import Roster
    roster = Roster.load()

    ids = {roster.resolve(n) for n in ("Morgan Soto", "Makenzie", "Morgan")}
    expect(ids == {"C05"},
           f"the printed name and the manual's name resolve to {ids}, "
           f"so the same person is recorded more than once")

    person = roster.by_id()["C05"]
    expect(roster.can_be_clinician_for(person, "9m"),
           "the merged record lost the manual's 6m-12m solo range")
    expect(person.van_trained, "the merged record lost the van training")


def test_somebody_not_being_scheduled_still_exists():
    """Ramiro is off the roster, not deleted.

    Deleting him would strand his calendar: the export still prints it, and a
    calendar matching nobody reads as one somebody has to go and identify. He
    stays, inactive, so the print is fully explained while he is never offered
    for a visit.
    """
    from esd_scheduler.roster import Roster
    roster = Roster.load()

    expect(roster.resolve("Ramiro") == "C06",
           "Ramiro's calendar no longer resolves to anybody")
    expect("C06" not in {e.id for e in roster.active},
           "Ramiro is still being scheduled")

    scheduled = sorted(e.id for e in roster.active)
    expect(scheduled == ["C01", "C02", "C03", "C04", "C05"],
           f"expected the five research coordinators, got {scheduled}")


def test_a_range_is_read_by_age_not_alphabetically():
    """"12m" is after "9m". Sorting these as strings puts it before "1m"."""
    from esd_scheduler.roster import Roster
    roster = Roster.load()
    makenzie = roster.by_id()["C05"]           # 6m-12m
    for inside in ("6m", "9m", "12m"):
        expect(roster.can_be_clinician_for(makenzie, inside),
               f"{inside} should be inside 6m-12m")
    for outside in ("1m", "2m", "3m", "24m", "36m"):
        expect(not roster.can_be_clinician_for(makenzie, outside),
               f"{outside} should be outside 6m-12m")


def test_an_unknown_checkpoint_name_does_not_empty_the_pool():
    """A protocol naming its timepoints differently is not a veto.

    NICO writes "12mo" where NANO writes "12m". Judging that against a NANO
    range silently removed every clinician from every NICO visit, which looked
    exactly like nobody being qualified.
    """
    from esd_scheduler.roster import Roster
    roster = Roster.load()
    lauren = roster.by_id()["C02"]
    expect(roster.can_be_clinician_for(lauren, "12mo"),
           "an unrecognised checkpoint name removed a clinician entirely")


def test_roles_decide_what_someone_can_do_not_their_name():
    from esd_scheduler.roster import Roster
    roster = Roster.load()
    expect(roster.with_role("clinician"), "nobody holds the clinician role")
    expect(roster.with_role("tech"), "nobody holds the tech role")
    for entry in roster.active:
        expect(entry.roles, f"{entry.name} holds no role at all")
        if roster.can_be_clinician_for(entry, "9m"):
            expect("clinician" in entry.roles,
                   f"{entry.name} can run a visit without the clinician role")


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
