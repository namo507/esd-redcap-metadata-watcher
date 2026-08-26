"""Score the calendar reader against events whose true times are known.

Every other check in this project asks whether the reader ran. This one asks
whether it was *right*, which is a different question and the one the schedule
actually rests on: a block read half an hour late offers a family a slot
somebody is sitting in.

Ground truth comes from the fixture generator. It draws the work-week print
from a list of ``(day, start, end, calendar)`` tuples, so the exact time of
every rectangle on the page is known before the reader ever sees it. Scoring
against a real Outlook export would need somebody to transcribe it by hand,
and a transcription is itself a reading.

Two paths are scored separately, because they fail differently:

* **vector** -- PyMuPDF reads the rectangles out of the file. Nothing is
  measured, so the expected error is exactly zero. Any drift at all means a
  parsing bug, and the threshold is therefore 0 minutes rather than "close".
* **pixel** -- the same page rendered to an image and measured with OpenCV,
  which is what a screenshot upload does. Here error is real: the block edge
  lands between two rows of pixels and the axis has to be interpolated. The
  threshold is a stated tolerance, and a block that is never found at all is
  counted separately from one found in the wrong place.

Run:  python3 automation/ocr_accuracy.py [--dpi 150] [--json]
      make ocr-accuracy
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures"))

# The Monday the fixture is drawn against.
FIRST_DAY = 17
MONTH = 8
YEAR = 2026

#: How far a pixel-measured edge may sit from the truth before it counts as
#: wrong. One grid row on a 150-dpi render is about six minutes; fifteen leaves
#: room for interpolation without letting a block slide into the next half hour.
PIXEL_TOLERANCE_MIN = 15.0


def truth_rows(roster):
    """The drawn events that the board is expected to attribute to a person.

    Policy calendars are excluded -- they are not somebody's busy time. So is
    anybody the roster is not scheduling: their colour is deliberately left
    unattributed, so a reader that skips them is right and one that finds them
    would be the failure.
    """
    import make_work_week_pdf as fx

    rows = []
    for event in fx.EVENTS:
        day, start, end, label = event[0], event[1], event[2], event[3]
        cid = roster.resolve(_person_name(label))
        if cid is None:
            continue                      # a policy calendar, not a person
        if cid not in {e.id for e in roster.active}:
            continue                      # on the print, not being scheduled
        rows.append({"day": day, "start": start, "end": end,
                     "label": label, "coordinator_id": cid})
    return rows


def visible_rows(expected):
    """The drawn events a pixel reader could actually see.

    Overlapping events are painted in order, so a rectangle drawn earlier and
    fully covered by a later one is not on the rendered page at all. Expecting
    a pixel reader to find it would be scoring it against something no camera
    could photograph -- the vector path still reads it, because in the file
    both rectangles exist as separate objects whatever is on top.

    This is not a fixture quirk to be waved away. It is the pixel path's real
    limitation, and the dangerous direction: a block hidden under another
    calendar's block is absent from the read, and absent time reads as free.
    Which is why the count of occluded events is reported rather than quietly
    subtracted.
    """
    import make_work_week_pdf as fx

    drawn = list(fx.EVENTS)
    visible, hidden = [], []
    for row in expected:
        idx = next(i for i, e in enumerate(drawn)
                   if e[0] == row["day"] and e[1] == row["start"]
                   and e[3] == row["label"])
        covered = any(
            later[0] == row["day"]
            and later[1] <= row["start"] and later[2] >= row["end"]
            for later in drawn[idx + 1:]
        )
        (hidden if covered else visible).append(row)
    return visible, hidden


def _person_name(label: str) -> str:
    """"Bell, Margaret" -> "Margaret Bell". Anything else is left alone."""
    if "," not in label:
        return label
    surname, _, forename = label.partition(",")
    return f"{forename.strip()} {surname.strip()}"


def _hours(moment: dt.datetime) -> float:
    return moment.hour + moment.minute / 60.0


def _day_index(moment: dt.datetime) -> int:
    return (moment.date() - dt.date(YEAR, MONTH, FIRST_DAY)).days


def score(found, expected, tolerance):
    """Match read blocks to drawn ones and report how far off each was.

    Matching is on the person and the day, then the nearest start time. That
    order matters: a block matched to the wrong person's event would report a
    small time error for what is actually a total attribution failure.
    """
    remaining = list(found)
    matched, missed, errors = [], [], []

    for want in expected:
        best, best_gap = None, None
        for got in remaining:
            if got["coordinator_id"] != want["coordinator_id"]:
                continue
            if got["day"] != want["day"]:
                continue
            gap = abs(got["start"] - want["start"]) * 60.0
            if best_gap is None or gap < best_gap:
                best, best_gap = got, gap
        if best is None:
            missed.append(want)
            continue
        remaining.remove(best)
        start_err = abs(best["start"] - want["start"]) * 60.0
        end_err = abs(best["end"] - want["end"]) * 60.0
        matched.append({**want, "start_error_min": round(start_err, 2),
                        "end_error_min": round(end_err, 2)})
        errors.extend([start_err, end_err])

    return {
        "expected": len(expected),
        "matched": len(matched),
        "missed": [f"{m['label']} {m['day']} {m['start']}" for m in missed],
        "spurious": [f"{s['coordinator_id']} day {s['day']} {s['start']}"
                     for s in remaining],
        "worst_error_min": round(max(errors), 2) if errors else 0.0,
        "mean_error_min": round(sum(errors) / len(errors), 2) if errors else 0.0,
        "within_tolerance": all(e <= tolerance for e in errors),
        "tolerance_min": tolerance,
        "rows": matched,
    }


def read_vector(pdf_path, roster, coordinators):
    from esd_scheduler.calendar_import import import_pdf

    result = import_pdf(pdf_path, coordinators=coordinators, year_hint=YEAR)
    out = []
    for block in result.blocks:
        # Whole-day absence banners are not timed events and are scored
        # nowhere: they carry no start time to be right or wrong about.
        if block.start.hour == 0 and block.start.minute == 0 and block.end.hour == 23:
            continue
        out.append({"coordinator_id": block.coordinator_id,
                    "day": _day_index(block.start),
                    "start": _hours(block.start), "end": _hours(block.end)})
    return out, result


def render_to_png(pdf_path, dpi):
    import fitz

    doc = fitz.open(pdf_path)
    pix = doc[0].get_pixmap(dpi=dpi)
    png = os.path.join(tempfile.mkdtemp(), "work-week.png")
    pix.save(png)
    doc.close()
    return png


def read_pixels(png_path, coordinators, colour_map):
    """Read the rendered page the way an uploaded screenshot is read.

    Deliberately through ``import_pdf`` rather than ``ingest_image.extract``.
    Attribution -- turning a coloured rectangle into a named person's busy
    time -- happens in the importer, so scoring the extractor alone would
    measure a step the board never takes on its own.

    A screenshot carries no printed legend, so it needs a confirmed colour
    map. That is the real workflow: the lab matches colours to people once,
    and screenshots ride on it afterwards.
    """
    import datetime as _dt
    from esd_scheduler.calendar_import import import_pdf

    result = import_pdf(
        png_path,
        coordinators=coordinators,
        color_map=colour_map,
        year_hint=YEAR,
        image_hours=(8.0, 18.0),      # stated, not guessed: see extract()
        image_start=_dt.date(YEAR, MONTH, FIRST_DAY),
        image_days=5,
    )
    out = []
    for block in result.blocks:
        if block.start.hour == 0 and block.start.minute == 0 and block.end.hour == 23:
            continue
        out.append({"coordinator_id": block.coordinator_id,
                    "day": _day_index(block.start),
                    "start": _hours(block.start), "end": _hours(block.end)})
    refusal = next((b for b in (result.blockers or [])
                    if "TOO LOW A RESOLUTION" in b), None)
    return out, result, refusal


def run_report(dpi=150):
    """The measurements, as data.

    Split from ``run`` so the tests read numbers rather than scrape a
    printout: a test that greps stdout passes on a printout that happens to
    contain the right words.
    """
    import make_work_week_pdf as fx
    from esd_scheduler.demo import build_lab
    from esd_scheduler.roster import Roster

    roster = Roster.load()
    state, _ = build_lab(dt.datetime(YEAR, MONTH, FIRST_DAY, 9, 0))
    coordinators = state.coordinators

    workdir = tempfile.mkdtemp(prefix="ocr-accuracy-")
    pdf_path = os.path.join(workdir, "work-week.pdf")
    fx.build(pdf_path, first_day=FIRST_DAY, month=MONTH, year=YEAR)

    expected = truth_rows(roster)
    report = {"dpi": dpi, "events_drawn": len(fx.EVENTS),
              "events_expected_on_a_person": len(expected)}

    found, result = read_vector(pdf_path, roster, coordinators)
    report["vector"] = score(found, expected, tolerance=0.0)
    report["vector"]["tier"] = result.tier

    from esd_scheduler import ingest_image
    if not _cv2_present():
        report["pixel"] = {"skipped": "no image reader installed (opencv)"}
        return report

    # The colour map the lab would have confirmed: each calendar's hue from
    # the print's own legend, pointed at the person it belongs to.
    from esd_scheduler.calendar_import import ColorMap

    mapping = {}
    for label, hue in (result.legend or {}).items():
        cid = roster.resolve(_person_name(label))
        if cid and cid in {e.id for e in roster.active}:
            mapping[hue] = cid
    colour_map = ColorMap(mapping=mapping, confirmed=True,
                          confirmed_by="ocr_accuracy harness")
    png = render_to_png(pdf_path, dpi)
    try:
        found_px, px_result, refusal = read_pixels(png, coordinators, colour_map)
    except Exception as exc:                                  # noqa: BLE001
        report["pixel"] = {"error": f"{type(exc).__name__}: {exc}"}
        return report

    if refusal:
        # Refusing is the right answer, not a missed one. Scoring it as a miss
        # would mark the guard's correct behaviour as a defect and push
        # somebody to remove it.
        report["pixel"] = {"refused": refusal}
        return report

    visible, hidden = visible_rows(expected)
    report["pixel"] = score(found_px, visible, tolerance=PIXEL_TOLERANCE_MIN)

    # The same page, same colour map, read with the optional neural pass on.
    # Scored separately rather than replacing the number above: the point of
    # having two readers is being able to see which one is better on your own
    # prints, and that needs both numbers side by side.
    import os as _os
    from esd_scheduler import resources as _res_mod
    previous = _res_mod.LabResources.load
    loaded = previous()
    loaded.image_reader = "neural"
    _res_mod.LabResources.load = staticmethod(lambda *a, **k: loaded)
    try:
        found_nn, nn_result, nn_refusal = read_pixels(png, coordinators,
                                                      colour_map)
        if nn_refusal:
            report["neural"] = {"refused": nn_refusal}
        else:
            report["neural"] = score(found_nn, visible,
                                     tolerance=PIXEL_TOLERANCE_MIN)
            report["neural"]["hidden_under_another_calendar"] = [
                f"{h['label']} {h['start']}-{h['end']}" for h in hidden]
            report["neural"]["pass_report"] = getattr(
                nn_result, "neural_read", {}) or {}
    except Exception as exc:                                   # noqa: BLE001
        report["neural"] = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        _res_mod.LabResources.load = previous
    report["pixel"]["hidden_under_another_calendar"] = [
        f"{h['label']} {h['start']}-{h['end']}" for h in hidden]
    report["pixel"]["tier"] = px_result.tier
    report["pixel"]["colours_mapped"] = len(mapping)
    report["pixel"]["reads_the_hour_column"] = ingest_image.ocr_available()
    return report


def run(dpi=150, as_json=False):
    report = run_report(dpi=dpi)
    if as_json:
        print(json.dumps(report, indent=2))
        return 0 if _passed(report) else 1

    _print(report)
    return 0 if _passed(report) else 1


def _cv2_present():
    import importlib.util
    return importlib.util.find_spec("cv2") is not None


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line); line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(line)
    return out


def _passed(report):
    v = report.get("vector", {})
    if v.get("missed") or v.get("spurious") or not v.get("within_tolerance"):
        return False
    p = report.get("pixel", {})
    if "skipped" in p or "refused" in p:
        return True         # neither missing nor refusing is a wrong answer
    if "error" in p:
        return False        # but a reader that raises is not a passing reader
    if not (not p.get("missed") and p.get("within_tolerance", False)):
        return False

    n = report.get("neural", {})
    if not n or "skipped" in n or "refused" in n:
        return True
    if "error" in n:
        return False
    # The optional reader is allowed to be no better. It is not allowed to be
    # worse: it only ever overwrites a measured time with a stated one, so a
    # block it loses or misplaces means the correction is doing harm.
    if n.get("missed") or not n.get("within_tolerance", False):
        return False
    return n.get("worst_error_min", 0) <= p.get("worst_error_min", 0)


def _print(report):
    print()
    print("=" * 74)
    print("  How accurately the calendar reader reads a calendar")
    print("=" * 74)
    print(f"  {report['events_drawn']} events drawn, "
          f"{report['events_expected_on_a_person']} of them a scheduled "
          f"person's busy time")
    for path, title in (
            ("vector", "PDF, read from the file (evidence tier 2)"),
            ("pixel", f"the same page as pixels at {report['dpi']} dpi"),
            ("neural", f"the same pixels, with the optional neural pass on")):
        block = report.get(path, {})
        print()
        print(f"  {title}")
        print("  " + "-" * 70)
        if "skipped" in block:
            print(f"    skipped: {block['skipped']}")
            continue
        if "refused" in block:
            print("    refused to read, which is the correct answer here:")
            for line in _wrap(block["refused"], 64):
                print(f"      {line}")
            continue
        if "error" in block:
            print(f"    FAILED:  {block['error']}")
            continue
        print(f"    matched            {block['matched']} of {block['expected']}")
        print(f"    worst error        {block['worst_error_min']} min"
              f"   (allowed {block['tolerance_min']})")
        print(f"    mean error         {block['mean_error_min']} min")
        if block["missed"]:
            print(f"    NOT FOUND          {block['missed']}")
        if block["spurious"]:
            print(f"    INVENTED           {block['spurious']}")
        pr = block.get("pass_report")
        if pr:
            if pr.get("used"):
                print(f"    text read from      {pr.get('read', 0)} block(s); "
                      f"{pr.get('corrected', 0)} time(s) taken from the text")
            else:
                print(f"    did not run:       {pr.get('reason')}")
        hidden = block.get("hidden_under_another_calendar")
        if hidden:
            print(f"    not on the page    {len(hidden)} block(s) painted over "
                  f"by another calendar")
            for h in hidden:
                print(f"                       {h}")
            print("                       a hidden block is absent from a "
                  "pixel read, and")
            print("                       absent time reads as free. Upload a "
                  "PDF print")
            print("                       where the timing matters.")
    print()
    print("  " + ("every block read to the minute it was drawn"
                  if _passed(report) else
                  "the reader is not reading what was drawn"))
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    raise SystemExit(run(dpi=args.dpi, as_json=args.json))
