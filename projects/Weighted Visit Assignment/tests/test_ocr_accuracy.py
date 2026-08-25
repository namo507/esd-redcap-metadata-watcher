"""Tests that the calendar reader reads the times it was given.

Every other test here asks whether the reader ran. These ask whether it was
right, which is the question the schedule rests on: a block read half an hour
late offers a family a slot somebody is sitting in.

Ground truth is the fixture generator's own event list, so the true time of
every rectangle is known before the reader sees the page.

Run:  python3 tests/test_ocr_accuracy.py
"""

from __future__ import annotations

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "automation"))
os.environ.setdefault("ESD_MODE", "demo")


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def needs(module):
    return importlib.util.find_spec(module) is not None


def report(dpi=150):
    import ocr_accuracy
    return ocr_accuracy.run_report(dpi=dpi)


def test_the_scorer_notices_a_start_read_late():
    """Check the oracle, not only the thing it judges.

    Every other test here trusts `score` to tell truth from error. If it
    always reported zero, all of them would pass while the reader drifted.

    The reading below is wrong in the *start* only -- same end -- so the
    assertion cannot be satisfied by the end error standing in for it. An
    earlier version of this test moved both edges, and a mutation that
    discarded the start error entirely still passed it.
    """
    import ocr_accuracy

    truth = [{"day": 0, "start": 9.0, "end": 10.0, "label": "A",
              "coordinator_id": "C01"}]
    late_start = [{"day": 0, "start": 9.5, "end": 10.0,
                   "coordinator_id": "C01"}]

    out = ocr_accuracy.score(late_start, truth, tolerance=15.0)
    expect(out["worst_error_min"] == 30.0,
           f"a block whose start was read 30 minutes late scored "
           f"{out['worst_error_min']} minutes out")
    expect(not out["within_tolerance"],
           "30 minutes of drift passed a 15 minute tolerance")


def test_the_scorer_notices_an_end_read_early():
    """The same, for the other edge. An end read early frees time that is busy."""
    import ocr_accuracy

    truth = [{"day": 0, "start": 9.0, "end": 10.0, "label": "A",
              "coordinator_id": "C01"}]
    short = [{"day": 0, "start": 9.0, "end": 9.5, "coordinator_id": "C01"}]

    out = ocr_accuracy.score(short, truth, tolerance=15.0)
    expect(out["worst_error_min"] == 30.0,
           f"a block whose end was read 30 minutes early scored "
           f"{out['worst_error_min']} minutes out")


def test_the_scorer_reports_what_was_never_read_and_what_was_invented():
    import ocr_accuracy

    truth = [{"day": 0, "start": 9.0, "end": 10.0, "label": "A",
              "coordinator_id": "C01"},
             {"day": 1, "start": 13.0, "end": 14.0, "label": "B",
              "coordinator_id": "C02"}]
    reading = [{"day": 2, "start": 8.0, "end": 9.0, "coordinator_id": "C03"}]

    out = ocr_accuracy.score(reading, truth, tolerance=15.0)
    expect(len(out["missed"]) == 2,
           f"two unread blocks were reported as {out['missed']}")
    expect(len(out["spurious"]) == 1,
           f"the invented block was not reported: {out['spurious']}")


def test_the_scorer_matches_on_the_person_before_the_time():
    """Two people whose blocks were read onto each other.

    Matching on time first would pair each reading with the nearest event
    whoever it belonged to, and report zero error for a page where both
    calendars landed on the wrong person -- a total attribution failure
    scored as a perfect read.

    The case is built so the two answers differ: C01 is drawn at 9 and read at
    14, C02 is drawn at 14 and read at 9. Matching by person gives five hours
    of error twice. Matching by time gives none at all.
    """
    import ocr_accuracy

    truth = [{"day": 0, "start": 9.0, "end": 10.0, "label": "A",
              "coordinator_id": "C01"},
             {"day": 0, "start": 14.0, "end": 15.0, "label": "B",
              "coordinator_id": "C02"}]
    crossed = [{"day": 0, "start": 14.0, "end": 15.0, "coordinator_id": "C01"},
               {"day": 0, "start": 9.0, "end": 10.0, "coordinator_id": "C02"}]

    out = ocr_accuracy.score(crossed, truth, tolerance=15.0)
    expect(out["worst_error_min"] == 300.0,
           f"two calendars read onto the wrong people scored "
           f"{out['worst_error_min']} minutes out; the scorer matched on time "
           f"rather than on whose calendar it is")
    expect(not out["missed"] and not out["spurious"],
           "both blocks were present, so neither is missing nor invented")


def test_a_pdf_is_read_to_the_exact_minute():
    """Vector extraction measures nothing, so its error must be exactly zero.

    Not "close". The rectangles are read out of the file, so any drift at all
    is a parsing bug rather than a tolerance, and a threshold with slack in it
    would hide one.
    """
    if not needs("fitz"):
        print("SKIP test_a_pdf_is_read_to_the_exact_minute (no PyMuPDF)")
        return
    v = report()["vector"]
    expect(not v["missed"], f"the PDF reader did not find {v['missed']}")
    expect(not v["spurious"], f"the PDF reader invented {v['spurious']}")
    expect(v["worst_error_min"] == 0.0,
           f"a PDF is read from the file, so the error must be 0; it was "
           f"{v['worst_error_min']} minutes")


def test_a_screenshot_at_a_usable_size_is_also_exact():
    """The pixel path may lose a block. It may not misplace one."""
    if not (needs("fitz") and needs("cv2")):
        print("SKIP test_a_screenshot_at_a_usable_size_is_also_exact "
              "(needs PyMuPDF and OpenCV)")
        return
    p = report()["pixel"]
    expect("error" not in p, f"the image reader raised: {p.get('error')}")
    expect("refused" not in p,
           "a 150 dpi render was refused; the resolution floor is too high")
    expect(p["within_tolerance"],
           f"blocks were placed {p['worst_error_min']} minutes out, over the "
           f"{p['tolerance_min']} minute tolerance")
    expect(not p["missed"],
           f"blocks visible on the page were not found: {p['missed']}")


def test_an_image_too_small_to_measure_is_refused_not_guessed():
    """The failure this guard exists for was measured, not imagined.

    At 63 pixels per hour the reader placed a block 135 minutes from where it
    was drawn. With screenshot auto-commit on, that becomes busy time nobody
    checked while the real slot reads free -- the exact inversion the evidence
    tiers exist to prevent. Refusing is the answer this module already gives
    for an unreadable hour column, and a resolution too low to measure is the
    same failure by another route.
    """
    if not (needs("fitz") and needs("cv2")):
        print("SKIP test_an_image_too_small_to_measure_is_refused_not_guessed")
        return
    p = report(dpi=72)["pixel"]
    expect("refused" in p,
           f"a 72 dpi render was read rather than refused, and this reader has "
           f"been measured putting a block 135 minutes out at that size: {p}")
    expect("pixels per hour" in p["refused"],
           "the refusal does not say what was wrong with the image")


def test_the_floor_is_below_a_resolution_that_works():
    """A guard set too high refuses images it could have read correctly."""
    if not (needs("fitz") and needs("cv2")):
        print("SKIP test_the_floor_is_below_a_resolution_that_works")
        return
    from esd_scheduler import ingest_image
    p = report(dpi=96)["pixel"]
    expect("refused" not in p,
           f"96 dpi was refused, but it reads every visible block exactly; "
           f"the floor of {ingest_image.MIN_PIXELS_PER_HOUR} px/hour is too "
           f"high")


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
