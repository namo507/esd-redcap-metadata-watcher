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
