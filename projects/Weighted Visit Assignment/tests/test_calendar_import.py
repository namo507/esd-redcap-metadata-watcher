"""Tests for reading an Outlook PDF print into availability.

The interesting claims here are about what the file does and does not contain:
that Outlook hides a usable colour legend in its header, that a month grid can
never yield an interval, and that a cut-off day cell is not free time.

Run:  python3 tests/test_calendar_import.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures"))

import fitz  # noqa: E402

from esd_scheduler.calendar_import import (  # noqa: E402
    TIER_MONTH_GRID,
    TIER_TIMED_EXPORT,
    ColorMap,
    import_pdf,
    suggest_roster_matches,
)
from esd_scheduler.demo import build_lab  # noqa: E402
from esd_scheduler.ingest_outlook_pdf import (  # noqa: E402
    _nearest_hue,
    detect_view_type,
    extract_legend,
    load,
)
from make_month_pdf import build as build_month  # noqa: E402
from make_work_week_pdf import build as build_week  # noqa: E402

TMP = tempfile.mkdtemp(prefix="esd-cal-")
os.environ["ESD_COLOR_MAP_PATH"] = os.path.join(TMP, "colors.json")

WEEK = build_week(os.path.join(TMP, "week.pdf"))
PLAIN_WEEK = build_week(os.path.join(TMP, "week-plain.pdf"), coloured_legend=False)
MONTH = build_month(os.path.join(TMP, "month.pdf"))

STATE, _ = build_lab(datetime(2026, 8, 17, 9, 0))
ROSTER = STATE.coordinators


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


# --- the legend Outlook prints ---------------------------------------------


def test_legend_is_recovered_from_header_colours():
    doc = fitz.open(MONTH)
    legend = extract_legend(doc[0])
    doc.close()
    expect(len(legend) >= 6, f"expected a full legend, got {legend}")
    expect(legend.get("Bell, Margaret") == "teal",
           f"wrong hue for a known calendar: {legend}")


def test_legend_never_swallows_grid_content():
    """Event titles live in coloured cells; they are not calendar names."""
    doc = fitz.open(MONTH)
    legend = extract_legend(doc[0])
    doc.close()
    for label in legend:
        expect(not label[0].isdigit(),
               f"a time leaked into the legend: {label!r}")
        expect("Busy" not in label and "Tentative" not in label,
               f"a status word leaked into the legend: {label!r}")


def test_nearest_hue_refuses_a_colour_outside_the_palette():
    """Rounded palette colours match; unrelated ones must not be forced in."""
    expect(_nearest_hue(0x0E6BBD) is not None, "a rounded palette colour should match")
    expect(_nearest_hue(0x414141) is None, "neutral grey text is not a calendar hue")
    expect(_nearest_hue(0xFF00FF) is None, "magenta is not in the palette")


def test_hue_variants_collapse_to_one_family():
    """orange2 and teal_lt are orange and teal, or attribution splits in two."""
    expect(_nearest_hue(0xF6620C)[0] == "orange", "orange variant did not collapse")
    expect(_nearest_hue(0xF7630C)[0] == "orange", "orange variant did not collapse")


# --- what each view is worth -----------------------------------------------


def test_month_view_is_detected_and_yields_no_intervals():
    doc = fitz.open(MONTH)
    expect(detect_view_type(doc[0]) == "month", "month grid misdetected")
    doc.close()
    parsed = load(MONTH, year_hint=2026)
    expect(parsed.entries, "month parse found nothing")
    expect(all(e.end_time is None for e in parsed.entries),
           "a month grid cannot carry end times")


def test_work_week_is_detected_despite_hour_labels_in_month_cells():
    """Month cells print hour labels too; only a real gutter is monotonic."""
    doc = fitz.open(WEEK)
    expect(detect_view_type(doc[0]) == "work_week", "work week misdetected")
    doc.close()


def test_work_week_times_are_read_exactly():
    result = import_pdf(WEEK, coordinators=ROSTER, year_hint=2026)
    expect(result.tier == TIER_TIMED_EXPORT, f"expected tier 2, got {result.tier}")
    spans = sorted(
        (b.start.strftime("%Y-%m-%d %H:%M"), b.end.strftime("%H:%M"))
        for b in result.blocks
    )
    expect(("2026-08-17 09:00", "10:00") in spans, f"missing a known block: {spans}")
    expect(("2026-08-17 13:30", "15:00") in spans, f"missing a known block: {spans}")


def test_month_import_is_never_schedulable():
    result = import_pdf(MONTH, coordinators=ROSTER, year_hint=2026)
    expect(result.tier == TIER_MONTH_GRID, f"expected tier 3, got {result.tier}")
    expect(result.schedulable is False, "a month grid must never be schedulable")
    expect(not result.blocks, "a month grid must not produce bookable blocks")


# --- attribution ------------------------------------------------------------


def test_attribution_comes_from_the_file_with_no_setup():
    result = import_pdf(MONTH, coordinators=ROSTER, year_hint=2026)
    expect(result.attribution_source == "legend",
           f"expected legend attribution, got {result.attribution_source}")
    named = [a for a in result.availability if a["coordinator_id"]]
    expect(len(named) >= 5, f"legend attributed only {len(named)} people")


def test_a_legend_beats_a_stored_map_that_disagrees():
    """The file is evidence; a stored map is someone's memory of it."""
    wrong = ColorMap(mapping={"teal": "C05"}, confirmed=True, confirmed_by="stale")
    result = import_pdf(MONTH, coordinators=ROSTER, color_map=wrong, year_hint=2026)
    teal = [a for a in result.availability if a["hue"] == "teal"]
    expect(teal, "teal calendar vanished")
    expect(teal[0]["coordinator_id"] == "C01",
           f"stored map overrode the printed legend: {teal[0]['coordinator_id']}")


def test_without_a_legend_nothing_is_attributed():
    result = import_pdf(PLAIN_WEEK, coordinators=ROSTER, year_hint=2026)
    expect(result.attribution_source == "none",
           f"expected no attribution, got {result.attribution_source}")
    expect(not result.blocks, "blocks were attributed with no legend and no map")


def test_calendars_not_on_the_roster_are_left_alone():
    """The export owner is not a coordinator and must not be invented as one."""
    result = import_pdf(MONTH, coordinators=ROSTER, year_hint=2026)
    ids = {a["coordinator_id"] for a in result.availability}
    expect(None in ids, "the off-roster owner calendar was silently dropped or matched")


def test_roster_matching_never_pairs_by_position():
    matches = suggest_roster_matches(["Bell, Margaret", "Not A Person"], ROSTER)
    expect(matches["Bell, Margaret"] == "C01", "name match failed")
    expect(matches["Not A Person"] is None, "an unknown label was matched anyway")


# --- availability -----------------------------------------------------------


def test_a_cut_off_day_is_unknown_not_free():
    result = import_pdf(MONTH, coordinators=ROSTER, year_hint=2026)
    wrong = [
        (a["name"], d["day"])
        for a in result.availability for d in a["days"]
        if d["truncated"] and d["items"] == 0 and d["state"] == "open"
    ]
    expect(not wrong, f"cut-off cells reported as free: {wrong[:3]}")


def test_availability_counts_are_internally_consistent():
    result = import_pdf(MONTH, coordinators=ROSTER, year_hint=2026)
    for a in result.availability:
        total = a["busy_days"] + a["light_days"] + a["open_days"] + a["unknown_days"]
        expect(total == len(a["days"]),
               f"{a['name']}: states sum to {total}, not {len(a['days'])}")
        expect(a["open_working_days"] <= a["working_days"],
               f"{a['name']}: more clear weekdays than weekdays")


def test_a_quiet_calendar_is_not_mistaken_for_a_truncated_one():
    """Three items in a day is a quiet day, not a full cell."""
    quiet = build_month(os.path.join(TMP, "quiet.pdf"), events_per_day=2)
    parsed = load(quiet, year_hint=2026)
    expect(not parsed.saturated_cells,
           f"a two-item day was read as a full cell: {parsed.saturated_cells[:3]}")


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
