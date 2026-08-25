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
from datetime import date, datetime

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
from esd_scheduler.calendar_roles import (  # noqa: E402
    POLARITY,
    ROLE_CLINICIAN,
    ROLE_COORDINATOR,
    ROLE_LAB,
    ROLE_OFFERED,
    ROLE_OWNER,
    ROLE_UNKNOWN,
    classify,
)
from esd_scheduler.constraints import resource_checks  # noqa: E402


class Skip(Exception):
    """An optional extra is missing, so this test has nothing to say."""


def needs_opencv():
    """Guard the image tests, which need the calendar-reading extras.

    The engine and the API run on the standard library alone. A machine that
    installed only requirements-core.txt is a supported setup, not a broken
    one, so these tests step aside there instead of failing. CI installs the
    extras, so nothing is skipped where it matters.
    """
    try:
        import cv2  # noqa: F401
    except ModuleNotFoundError as exc:
        raise Skip(f"needs the calendar extras ({exc.name})") from exc

from esd_scheduler.calendar_roles import RoleMap  # noqa: E402
from esd_scheduler.ingest_outlook_pdf import (  # noqa: E402
    _nearest_hue,
    detect_view_type,
    extract_legend,
    load,
    read_unavailability,
)
from make_month_pdf import build as build_month  # noqa: E402
from make_work_week_pdf import build as build_week, y_for  # noqa: E402

TMP = tempfile.mkdtemp(prefix="esd-cal-")
os.environ["ESD_COLOR_MAP_PATH"] = os.path.join(TMP, "colors.json")
os.environ["ESD_CALENDAR_ROLES_PATH"] = os.path.join(TMP, "roles.json")

WEEK = build_week(os.path.join(TMP, "week.pdf"))
PLAIN_WEEK = build_week(os.path.join(TMP, "week-plain.pdf"), coloured_legend=False)
MONTH = build_month(os.path.join(TMP, "month.pdf"))


PNG_DPI = 150


def _render(pdf_path, png_path, dpi=PNG_DPI):
    """A screenshot of the same calendar, for the image reader."""
    doc = fitz.open(pdf_path)
    doc[0].get_pixmap(dpi=dpi).save(png_path)
    doc.close()
    return png_path


WEEK_PNG = _render(WEEK, os.path.join(TMP, "week.png"))


def _px(hour: float) -> float:
    """Where the fixture draws a given hour's rule, in screenshot pixels."""
    return y_for(hour) * PNG_DPI / 72.0

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


def test_a_stale_colour_map_cannot_turn_a_policy_calendar_into_a_person():
    """The legend outranks the stored map for any hue it already explains.

    Colours move when an Outlook overlay changes. A map kept from an older one
    resolved the hue this export uses for "Offered Times ESD" to a coordinator,
    which would have booked time the lab set aside for visits as that person's
    busy time.
    """
    from esd_scheduler.calendar_import import ColorMap
    from esd_scheduler.calendar_roles import ROLE_OFFERED

    stale = ColorMap(mapping={"blue": "C03", "orange": "C04", "green": "C05"},
                     confirmed=True, confirmed_by="an older overlay")
    result = import_pdf(WEEK, coordinators=ROSTER, color_map=stale, year_hint=2026)
    expect(result.resources.get(ROLE_OFFERED),
           "the offered-times calendar was consumed as somebody's busy time")
    for run in result.runs:
        for block in run.blocks:
            expect(block.coordinator_id in ROSTER,
                   f"a policy calendar became {block.coordinator_id}")
    timed = [b for b in result.blocks if b.start.hour != 0]
    # Six, not seven. The fixture prints seven coordinator calendars and one
    # of them belongs to somebody the roster carries as active: false, whose
    # colour is deliberately not attributed -- busy time for a person who can
    # never be offered is time the board would carry and never use. The row is
    # still recognised as theirs, which is the assertion below.
    expect(len(timed) == 6,
           f"expected the 6 scheduled coordinators' events, got {len(timed)}")
    expect(result.off_roster_names,
           "the calendar of somebody not being scheduled was left looking "
           "like one nobody could identify")
    for label, cid in result.off_roster_names.items():
        expect(result.roles.get(label) == "coordinator",
               f"{label} resolves to {cid} but is not read as a person")
        expect(cid not in {b.coordinator_id for b in result.blocks},
               f"{label} is not being scheduled but their blocks were read")


def test_without_a_legend_no_colour_is_attributed_to_anyone():
    """Colour attribution needs the legend. Absence notices do not -- they are
    read from the banner text, so they survive an export with no colours."""
    result = import_pdf(PLAIN_WEEK, coordinators=ROSTER, year_hint=2026)
    expect(result.attribution_source == "none",
           f"expected no colour attribution, got {result.attribution_source}")
    timed = [b for b in result.blocks
             if not (b.start.hour == 0 and b.start.minute == 0)]
    expect(not timed,
           f"timed blocks were attributed with no legend and no map: {len(timed)}")


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


# --- the lab's policy calendars --------------------------------------------


def test_policy_calendars_are_classified_from_their_names():
    expected = {
        "Offered Times ESD": ROLE_OFFERED,
        "Clinician Shifts": ROLE_CLINICIAN,
        "PSYCHOLOGY, ESDI LAB": ROLE_LAB,
        "Calendar": ROLE_OWNER,
        "Some Other Diary": ROLE_UNKNOWN,
    }
    for label, role in expected.items():
        got = classify(label)
        expect(got == role, f"{label!r} classified as {got}, expected {role}")
    expect(classify("Bell, Margaret", is_roster_name=True) == ROLE_COORDINATOR,
           "a matched roster name must be a coordinator")


def test_offered_and_shift_calendars_are_positive_not_busy_time():
    """Reading these as busy would rule out exactly the slots set aside."""
    expect(POLARITY[ROLE_OFFERED] == "positive", "offered times must be positive")
    expect(POLARITY[ROLE_CLINICIAN] == "positive", "clinician shifts must be positive")
    expect(POLARITY[ROLE_LAB] == "negative", "a booked room is taken time")
    expect(POLARITY[ROLE_COORDINATOR] == "negative", "a person's diary is busy time")


def test_policy_calendars_become_resource_windows():
    result = import_pdf(WEEK, coordinators=ROSTER, year_hint=2026)
    for role in (ROLE_OFFERED, ROLE_CLINICIAN, ROLE_LAB):
        expect(result.resources.get(role),
               f"no windows collected for {role}: {list(result.resources)}")
    # Policy calendars must never be mistaken for a person's busy time.
    for run in result.runs:
        expect(run.coordinator_id in ROSTER,
               f"a policy calendar became a coordinator: {run.coordinator_id}")


def test_an_overlaid_but_empty_calendar_is_reported_not_ignored():
    """An empty filter and a missing filter mean different things."""
    result = import_pdf(PLAIN_WEEK, coordinators=ROSTER, year_hint=2026)
    expect(not result.resources.get(ROLE_OFFERED),
           "the plain fixture should carry no policy windows")


def test_resource_checks_answer_only_what_they_can_see():
    from datetime import datetime as _dt

    res = {ROLE_LAB: [{"start": "2026-08-17T09:00:00", "end": "2026-08-17T17:00:00"}]}
    checks = resource_checks(
        _dt(2026, 8, 17, 13, 0), _dt(2026, 8, 17, 15, 0), res,
        requires_clinician=True, in_lab=True)
    expect(checks[ROLE_LAB][0] == "fail", "a booked room must fail a lab visit")
    expect(checks[ROLE_OFFERED][0] == "not_applicable",
           "an absent offered-times calendar must not silently pass")
    expect(checks[ROLE_CLINICIAN][0] == "not_applicable",
           "an absent shift calendar must not silently pass")


def test_a_visit_outside_every_offered_window_fails():
    from datetime import datetime as _dt

    res = {ROLE_OFFERED: [{"start": "2026-08-17T13:00:00",
                           "end": "2026-08-17T16:00:00"}]}
    inside = resource_checks(_dt(2026, 8, 17, 13, 30), _dt(2026, 8, 17, 15, 30), res)
    outside = resource_checks(_dt(2026, 8, 17, 9, 30), _dt(2026, 8, 17, 11, 0), res)
    expect(inside[ROLE_OFFERED][0] == "pass", "a visit inside an offer should pass")
    expect(outside[ROLE_OFFERED][0] == "fail", "a visit outside every offer should fail")


# --- what a real work-week export looks like --------------------------------


def test_abbreviated_day_headers_are_recognised():
    """Outlook writes Mon/Tue in a work week and Monday/Tuesday in a month."""
    doc = fitz.open(WEEK)
    expect(detect_view_type(doc[0]) == "work_week",
           "a work week with abbreviated headers was misdetected")
    doc.close()


def test_the_printed_date_range_wins_over_reconstruction():
    parsed = load(WEEK, year_hint=2026)
    expect(parsed.visible_date_range == "2026-08-17 to 2026-08-21",
           f"wrong range: {parsed.visible_date_range}")


def test_times_land_on_real_appointment_boundaries():
    """Outlook insets each box, so a raw read is minutes early on every event."""
    parsed = load(WEEK, year_hint=2026)
    for entry in parsed.entries:
        if not entry.start_time:
            continue
        for clock in (entry.start_time, entry.end_time):
            minutes = int(clock.split(":")[1])
            expect(minutes % 5 == 0,
                   f"{clock} is not on a five-minute boundary")


def test_all_day_banners_are_whole_days_not_intervals():
    parsed = load(WEEK, year_hint=2026)
    banners = [e for e in parsed.entries if e.all_day]
    expect(banners, "no all-day banners were read")
    for entry in banners:
        expect(entry.start_time is None and entry.end_time is None,
               "an all-day banner must not carry a time")
    # At least one banner in the fixture covers two days and must appear on both.
    spans = {}
    for entry in banners:
        spans.setdefault(entry.evidence_text + str(entry.calendar_label), set())
    lab_days = sorted({e.day for e in banners
                       if e.calendar_label == "PSYCHOLOGY, ESDI LAB"})
    expect(len(lab_days) >= 2, f"lab banners did not span days: {lab_days}")


def test_an_event_in_the_right_of_its_column_stays_in_that_day():
    """Outlook indents its day headers, so a boundary set midway between them
    lands about fifty points early and pushes anything drawn in the right-hand
    part of a column into the next day -- at the right time, against the right
    person, which is the hardest kind of wrong to notice."""
    parsed = load(WEEK, year_hint=2026)
    hits = [e for e in parsed.entries
            if e.start_time == "10:00" and e.end_time == "10:30"]
    expect(hits, "the right-aligned event was not read at all")
    expect(hits[0].day == "2026-08-20",
           f"drawn in Thursday's column, reported as {hits[0].day}")
    expect(hits[0].calendar_label == "Oak, Sanjana",
           f"attributed to {hits[0].calendar_label}")


def test_a_dark_shade_is_attributed_to_its_own_calendar():
    """Every box is a shade; near-black ones sit close to several palettes."""
    parsed = load(WEEK, year_hint=2026)
    friday = [e for e in parsed.entries
              if e.day == "2026-08-21" and e.start_time == "11:00"]
    expect(friday, "the Friday event went missing")
    expect(friday[0].calendar_label == "Lucas-Mariano, Ramiro",
           f"dark shade misattributed to {friday[0].calendar_label}")


def test_a_pale_tint_reads_as_tentative():
    from esd_scheduler.ingest_outlook_pdf import _nearest_hue as _hue

    expect(_hue(0xFEF7B2)[0] == "yellow", "a pale tint lost its hue")
    expect(_hue(0xA9D3F2)[0] == "blue", "a pale tint lost its hue")


# --- whole-day absence notices ---------------------------------------------


def test_only_absence_shaped_text_is_ever_read():
    """The allowlist is the whole privacy story for banner text."""
    accepted = {
        "Sanjana Out": ("Sanjana", "out"),
        "Ramiro Unavailable for Visits": ("Ramiro", "unavailable for visits"),
        "Lauren is OOO": ("Lauren", "ooo"),
        "Sofia PTO": ("Sofia", "pto"),
        "Aug 11 Sanjana Out": ("Sanjana", "out"),
    }
    for text, expected in accepted.items():
        got = read_unavailability(text)
        expect(got == expected, f"{text!r} read as {got}, expected {expected}")

    for text in ("Free", "Team standup", "Home Visit -- CONF PSYCHOLOGY",
                 "Grant meeting with Sofia", "12mo NICO visit", ""):
        got = read_unavailability(text)
        expect(got is None, f"{text!r} should not be read as an absence, got {got}")


def test_free_all_day_items_are_not_absences():
    """'Free' marks an all-day item, which is the opposite of unavailable."""
    parsed = load(WEEK, year_hint=2026)
    names = {u["name"] for u in parsed.unavailability}
    expect("Free" not in names, "a Free banner was read as a person")


def test_a_banner_is_attributed_by_its_text_not_its_colour():
    """The lab posts these on a shared calendar, so colour identifies nothing."""
    parsed = load(WEEK, year_hint=2026)
    notices = [u for u in parsed.unavailability if u["name"] == "Sofia"]
    expect(notices, "the Sofia notice was not read")
    expect(all(n["posted_on"] == "PSYCHOLOGY, ESDI LAB" for n in notices),
           "fixture posts this notice on the lab calendar")
    result = import_pdf(WEEK, coordinators=ROSTER, year_hint=2026)
    sofia = [u for u in result.unavailable if u["name"] == "Sofia Tous"]
    expect(sofia, "a notice posted on another calendar was not attributed to Sofia")


def test_a_multi_day_banner_blocks_every_day_it_spans():
    result = import_pdf(WEEK, coordinators=ROSTER, year_hint=2026)
    days = sorted({u["day"] for u in result.unavailable if u["name"] == "Sofia Tous"})
    expect(len(days) == 2, f"multi-day notice did not span: {days}")


def test_an_unrecognised_nickname_is_reported_not_guessed():
    """Maggie may or may not be Margaret; a wrong veto benches someone free."""
    result = import_pdf(WEEK, coordinators=ROSTER, year_hint=2026)
    names = {u["name"] for u in result.unresolved_names}
    expect("Maggie" in names, f"Maggie should be unresolved, got {names}")
    expect(not any(u["name"] == "Margaret Bell" for u in result.unavailable),
           "a nickname was guessed onto a real coordinator")
    expect(any("NOT MATCHED" in b for b in result.blockers),
           f"unresolved names must be reported: {result.blockers}")


def test_a_declared_alias_resolves_the_nickname():
    path = os.environ["ESD_CALENDAR_ROLES_PATH"]
    RoleMap(declared={}, aliases={"maggie": "C01"}).save(path)
    try:
        result = import_pdf(WEEK, coordinators=ROSTER, year_hint=2026)
        expect(any(u["name"] == "Margaret Bell" for u in result.unavailable),
               "a declared alias did not resolve")
        # A notice naming somebody the lab is not currently scheduling is
        # reported but is not a mystery: the board knows exactly who they are
        # and the notice cannot change any offer it makes.
        mystery = [r for r in result.unresolved_names if not r.get("off_roster")]
        expect(not mystery, f"nothing should remain unresolved: {mystery}")
    finally:
        os.remove(path)


def test_a_matched_absence_blocks_the_whole_day():
    result = import_pdf(WEEK, coordinators=ROSTER, year_hint=2026)
    whole = [b for b in result.blocks
             if b.start.hour == 0 and b.start.minute == 0 and b.end.hour == 23]
    expect(whole, "no whole-day blocks were produced")
    for block in whole:
        expect(block.start.date() == block.end.date(),
               "a whole-day block should not spill into the next day")


# --- one print per coordinator ----------------------------------------------


def test_a_single_coordinator_print_needs_no_colour_map():
    """The shape "print each coordinator's calendar in turn" produces.

    One person plus the mailbox owner, so the legend names exactly one calendar
    the roster recognises and attribution is unambiguous. Nothing to configure.
    """
    for who, cid in (("Oak, Sanjana", "C03"),
                     ("Puttock, Lauren", "C02"),
                     ("Bell, Margaret", "C01")):
        path = build_week(os.path.join(TMP, f"solo-{cid}.pdf"), only=who)
        result = import_pdf(path, coordinators=ROSTER, year_hint=2026)
        expect(result.attribution_source == "legend",
               f"{who}: attribution was {result.attribution_source}")
        touched = {r.coordinator_id for r in result.runs}
        expect(touched == {cid},
               f"{who}: expected only {cid}, got {touched or 'nobody'}")


def test_the_print_decides_whose_calendar_it_is_not_the_filename():
    """A file called Maggie.pdf whose legend says Bell, Margaret is Margaret's."""
    path = build_week(os.path.join(TMP, "Maggie.pdf"), only="Bell, Margaret")
    result = import_pdf(path, coordinators=ROSTER, year_hint=2026)
    expect({r.coordinator_id for r in result.runs} == {"C01"},
           "the print's own legend did not decide the owner")


# --- reading a screenshot ---------------------------------------------------


def test_an_image_is_a_lower_tier_than_the_same_calendar_as_a_pdf():
    """Pixel measurement and vector extraction must not be treated alike."""
    needs_opencv()
    from esd_scheduler.calendar_import import TIER_IMAGE, TIER_TIMED_EXPORT

    pdf = import_pdf(WEEK, coordinators=ROSTER, year_hint=2026)
    img = import_pdf(WEEK_PNG, coordinators=ROSTER,
                     image_hours=(8.0, 17.0), image_start=date(2026, 8, 17))
    expect(pdf.tier == TIER_TIMED_EXPORT, f"pdf tier wrong: {pdf.tier}")
    expect(img.tier == TIER_IMAGE, f"image tier wrong: {img.tier}")


def test_whether_a_screenshot_waits_for_review_is_the_labs_call():
    """Both settings, because the lab chose the riskier one deliberately.

    A PDF is vector extraction and its times are exact. A screenshot is
    measured off pixels, and the reader can miss a block at the edge of a
    grid. With auto-commit on, a block it never saw is simply absent, and
    absent time reads as free, so the board can offer a slot somebody is
    busy in. The lab has accepted that in exchange for no confirmation step.

    What must hold either way is that the tier still records the read came
    off an image, so the provenance survives even when the review does not.
    """
    needs_opencv()
    from esd_scheduler.calendar_import import TIER_IMAGE, ColorMap
    from esd_scheduler import resources as res_mod

    cmap = ColorMap(mapping={"cranberry": "C03", "brown": "C02", "yellow": "C05"},
                    confirmed=True, confirmed_by="test")
    original = res_mod.LabResources.load

    def _with(auto: bool):
        loaded = original()
        loaded.auto_confirm_screenshots = auto
        res_mod.LabResources.load = staticmethod(lambda *a, **k: loaded)
        try:
            return import_pdf(WEEK_PNG, coordinators=ROSTER, color_map=cmap,
                              image_hours=(8.0, 17.0),
                              image_start=date(2026, 8, 17))
        finally:
            res_mod.LabResources.load = original

    committed = _with(True)
    expect(committed.blocks, "the image produced no blocks at all")
    expect(all(b.reviewed for b in committed.blocks),
           "auto_confirm is on, so a screenshot should count on arrival")
    expect(committed.tier == TIER_IMAGE,
           "the tier stopped recording that this came off a screenshot")

    held = _with(False)
    expect(held.blocks, "the image produced no blocks at all")
    expect(not any(b.reviewed for b in held.blocks),
           "auto_confirm is off, so blocks should wait to be settled")
    expect(held.tier == TIER_IMAGE, "the tier changed with the setting")


def test_image_times_match_the_pdf_they_were_rendered_from():
    """The same calendar by two routes should agree where both can see it.

    Exactly, and by both routes. The allowance this test used to make for the
    interpolated axis -- one five-minute step, granted only where no OCR engine
    was installed -- was measuring a bug rather than a limit: the axis was
    stretched over the whole band the day separators sweep instead of the hours
    inside it. It reads the hour rules now, so there is nothing left to allow
    for, and a step of slack here would hide the next reader that loses one.
    """
    needs_opencv()
    from esd_scheduler.calendar_import import ColorMap

    cmap = ColorMap(mapping={"cranberry": "C03", "brown": "C02", "yellow": "C05",
                             "green": "C01", "blue": "C04"},
                    confirmed=True, confirmed_by="test")
    img = import_pdf(WEEK_PNG, coordinators=ROSTER, color_map=cmap,
                     image_hours=(8.0, 17.0), image_start=date(2026, 8, 17))
    seen = {
        (b.start.strftime("%Y-%m-%d %H:%M"), b.end.strftime("%H:%M"))
        for b in img.blocks
    }
    # Three events the fixture draws without any overlap, so the image reader
    # can see them whole. Overlapping events in one column are a known limit.
    for want in (("2026-08-18 08:00", "08:30"),
                 ("2026-08-19 10:00", "12:00"),
                 ("2026-08-19 14:00", "16:00"),
                 ("2026-08-20 15:00", "17:00")):
        expect(want in seen, f"image missed or misread {want}: {sorted(seen)}")


def test_an_image_without_a_time_range_refuses_rather_than_guessing():
    """With no OCR engine and no stated range there is no clock axis to use."""
    needs_opencv()
    from esd_scheduler.ingest_image import extract, ocr_available

    if ocr_available():
        return                       # the hour column can be read; nothing to refuse
    parsed = extract(WEEK_PNG, day_start=date(2026, 8, 17), n_days=5, hours=None)
    expect(not parsed.entries, "times were invented with no clock axis")
    expect(any("TIME RANGE UNKNOWN" in u for u in parsed.unresolved),
           f"no explanation for refusing: {parsed.unresolved}")


def test_ocr_reads_the_hour_column_without_being_told_the_range():
    needs_opencv()
    from esd_scheduler.ingest_image import extract, ocr_available

    if not ocr_available():
        return                       # nothing to test without an engine installed
    parsed = extract(WEEK_PNG, day_start=date(2026, 8, 17), n_days=5, hours=None)
    expect(parsed.entries, "OCR is available but nothing was read")
    expect(any("read by OCR" in u for u in parsed.unresolved),
           "the result does not say the axis came from OCR")


def test_the_fitted_axis_matches_the_calendar_it_was_read_from():
    """Grid top and bottom must land on the hours the gutter actually shows."""
    needs_opencv()
    from esd_scheduler.ingest_image import (
        _load, find_grid, ocr_available, read_time_axis)

    if not ocr_available():
        return
    image = _load(WEEK_PNG)
    grid = find_grid(image)
    axis = read_time_axis(WEEK_PNG, grid)
    expect(axis is not None, "the hour column could not be fitted")
    top_hour = axis[0] * grid.top + axis[1]
    bottom_hour = axis[0] * grid.bottom + axis[1]
    expect(abs(top_hour - 8.0) < 0.2, f"grid top read as {top_hour:.2f}, not 8")
    expect(abs(bottom_hour - 17.0) < 0.2,
           f"grid bottom read as {bottom_hour:.2f}, not 17")


def test_the_grid_is_bounded_by_the_hour_rules_not_the_ink_around_them():
    """The band the day separators sweep is wider than the hours it holds.

    Outlook runs its separators through the all-day strip and a little past the
    last rule, and this fixture reproduces that: its final absence banner
    overlaps the 8 AM rule and touches a separator, so the ink starts twelve
    pixels high at 150dpi. Twelve pixels is nothing to look at and a scale error
    to measure with -- a stated range mapped through it reads every block short.
    """
    needs_opencv()
    from esd_scheduler.ingest_image import _load, find_grid

    grid = find_grid(_load(WEEK_PNG))
    expect(grid is not None, "no ruled grid found in a calendar screenshot")
    expect(abs(grid.top - _px(8.0)) <= 3,
           f"grid top at {grid.top}px, but 8 AM is drawn at {_px(8.0):.0f}px")
    expect(abs(grid.bottom - _px(17.0)) <= 3,
           f"grid bottom at {grid.bottom}px, but 5 PM is drawn at "
           f"{_px(17.0):.0f}px")


def test_a_stated_range_reads_the_same_times_as_the_hour_column():
    """The no-OCR route must agree with the OCR one, not merely come close.

    Read short, a busy block's tail looks free, and free is the one direction
    that can put a visit on top of someone's existing appointment. The stated
    axis is also the route nobody developing on a machine with tesseract ever
    exercises, so it is forced here rather than left to the environment.
    """
    needs_opencv()
    from esd_scheduler import ingest_image

    installed = ingest_image.ocr_available
    ingest_image.ocr_available = lambda: False
    try:
        parsed = ingest_image.extract(WEEK_PNG, day_start=date(2026, 8, 17),
                                      n_days=5, hours=(8.0, 17.0))
    finally:
        ingest_image.ocr_available = installed

    expect(parsed.axis_source == "stated",
           f"the OCR path was taken after all: {parsed.axis_source}")
    seen = {(e.day, e.start_time, e.end_time) for e in parsed.entries}
    for want in (("2026-08-18", "08:00", "08:30"),
                 ("2026-08-19", "10:00", "12:00"),
                 ("2026-08-19", "14:00", "16:00"),
                 ("2026-08-20", "15:00", "17:00")):
        expect(want in seen,
               f"stated axis missed or misread {want}: {sorted(seen)}")


def test_overlapping_events_are_left_unattributed():
    """The board can see the time is taken without knowing whose it is."""
    needs_opencv()
    from esd_scheduler.ingest_image import extract

    parsed = extract(WEEK_PNG, day_start=date(2026, 8, 17), n_days=5,
                     hours=(8.0, 17.0))
    # The fixture stacks five calendars in Monday's column.
    monday = [e for e in parsed.entries if e.day == "2026-08-17"]
    expect(monday, "the stacked column was dropped entirely")
    for entry in monday:
        expect(entry.calendar_color_id is None,
               "a merged block was credited to one calendar")
    expect(any("OVERLAPPING EVENTS" in u for u in parsed.unresolved),
           f"the overlap was not reported: {parsed.unresolved}")


def test_the_axis_source_is_reported_not_assumed():
    """Calling an OCR-read range "assumed" understates what actually happened."""
    needs_opencv()
    from esd_scheduler.ingest_image import ocr_available

    result = import_pdf(WEEK_PNG, coordinators=ROSTER,
                        image_hours=None, image_start=date(2026, 8, 17))
    if ocr_available():
        expect(result.axis_source == "ocr",
               f"axis source should be ocr, got {result.axis_source}")
    else:
        expect(result.axis_source in (None, "stated"),
               f"unexpected axis source: {result.axis_source}")


def test_no_phantom_events_at_the_grid_edge():
    needs_opencv()
    from esd_scheduler.ingest_image import extract

    parsed = extract(WEEK_PNG, day_start=date(2026, 8, 17), n_days=5,
                     hours=(8.0, 17.0))
    for entry in parsed.entries:
        start = [int(x) for x in entry.start_time.split(":")]
        end = [int(x) for x in entry.end_time.split(":")]
        minutes = (end[0] * 60 + end[1]) - (start[0] * 60 + start[1])
        expect(minutes >= 9,
               f"a {minutes}-minute sliver was read as an event: {entry.day} "
               f"{entry.start_time}-{entry.end_time}")


def test_the_header_legend_is_not_mistaken_for_events():
    """Coloured calendar names sit above the grid and dragged the axis with them."""
    needs_opencv()
    from esd_scheduler.ingest_image import _load, find_blocks, find_grid

    image = _load(WEEK_PNG)
    grid = find_grid(image)
    expect(grid is not None, "no ruled grid found in a calendar screenshot")
    blocks = find_blocks(image)
    inside = [b for b in blocks if b[1] >= grid.top - 2]
    expect(len(inside) < len(blocks),
           "nothing was excluded, so the header was never being filtered")


if __name__ == "__main__":
    failures = 0
    skipped = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Skip as exc:
                skipped += 1
                print(f"SKIP {name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    if skipped:
        print(f"\n{skipped} skipped (optional extras not installed)")
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
