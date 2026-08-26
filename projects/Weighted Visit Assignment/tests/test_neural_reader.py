"""Tests for the optional neural reader.

It is optional, off by default, and its only job is to replace a *measured*
time with a *stated* one where an event prints its own. So the properties
worth pinning are not "is it accurate" -- the harness scores that -- but
"can selecting it make anything worse", which is the risk of adding a second
reader at all.

Run:  python3 tests/test_neural_reader.py
"""

from __future__ import annotations

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "automation"))
os.environ.setdefault("ESD_MODE", "demo")

from esd_scheduler import ingest_image_neural as nn  # noqa: E402


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def needs(module):
    return importlib.util.find_spec(module) is not None


# --- reading a time out of whatever Outlook printed -------------------------


def test_the_shapes_outlook_actually_prints():
    for text, want in (
        ("9:00 AM - 10:00 AM", (9.0, 10.0)),
        ("09:00-10:00", (9.0, 10.0)),
        ("Team sync 13:30 - 14:30", (13.5, 14.5)),
        ("1 - 2:30 PM", (13.0, 14.5)),          # meridiem printed once, at the end
        ("11 - 1", (11.0, 13.0)),               # crosses noon without saying so
        ("2 – 3 PM", (14.0, 15.0)),             # en dash
        ("10 to 11:15 AM", (10.0, 11.25)),
    ):
        got = nn.read_span(text)
        expect(got == want, f"{text!r} read as {got}, expected {want}")


def test_nonsense_is_refused_rather_than_guessed():
    """A misread that becomes a time is worse than one that becomes nothing.

    These are the shapes OCR actually produces from a squeezed calendar box:
    a stray number, an impossible clock, a span longer than a working day.
    Any of them turning into a confident time would overwrite a measurement
    that was probably right.
    """
    for text in ("", "Check in - Namit/Jessie", "99:99 - 100:00", "25 - 26",
                 "9:00", "1:70 - 2:00", "9 - 9"):
        got = nn.read_span(text)
        expect(got == (None, None),
               f"{text!r} produced a time {got} rather than refusing")


def test_a_span_longer_than_a_working_day_is_refused():
    expect(nn.read_span("8:00 AM - 11:00 PM") == (None, None),
           "a fifteen-hour span was accepted as one event's time")


# --- the property that matters: it cannot make things worse -----------------


def test_selecting_it_never_loses_a_block():
    """The optional path is allowed to be no better. Not worse.

    It only ever overwrites the time on a block the geometry pass already
    found, so the block count must be identical either way. If selecting a
    reader could drop a block, the setting would be a trap.
    """
    if not (needs("fitz") and needs("cv2")):
        print("SKIP test_selecting_it_never_loses_a_block")
        return
    import ocr_accuracy

    report = ocr_accuracy.run_report()
    classical, neural = report.get("pixel", {}), report.get("neural", {})
    if "skipped" in classical or "refused" in classical:
        print("SKIP test_selecting_it_never_loses_a_block (no pixel read)")
        return
    expect("error" not in neural, f"the neural pass raised: {neural.get('error')}")
    expect(neural.get("matched") == classical.get("matched"),
           f"classical matched {classical.get('matched')} blocks and neural "
           f"matched {neural.get('matched')}; selecting a reader changed how "
           f"many blocks exist")
    expect(neural.get("worst_error_min", 0) <= classical.get("worst_error_min", 0),
           f"the neural pass made the worst error worse: "
           f"{neural.get('worst_error_min')} vs {classical.get('worst_error_min')}")


def test_it_says_when_it_did_nothing():
    """Silence would read as "it worked". It has to report the difference.

    On this lab's prints it corrects nothing, because Outlook's work-week view
    puts the event title in the box and the time in the row position. A pass
    that ran, found text and changed no times must say so, or somebody will
    believe they turned something on that is helping.
    """
    if not (needs("fitz") and needs("cv2")):
        print("SKIP test_it_says_when_it_did_nothing")
        return
    import ocr_accuracy

    report = ocr_accuracy.run_report().get("neural", {})
    if "skipped" in report or "refused" in report or "error" in report:
        print("SKIP test_it_says_when_it_did_nothing")
        return
    pass_report = report.get("pass_report") or {}
    expect(pass_report, "the neural pass reported nothing about what it did")
    expect("used" in pass_report and "corrected" in pass_report,
           f"the pass report does not say whether it ran or what it changed: "
           f"{pass_report}")


def test_it_refuses_clearly_when_its_engine_is_missing():
    """Selecting a reader that cannot run must say why, not fail silently."""
    ok, why = nn.available()
    if ok:
        expect(why == "", "an available reader gave a reason it is not")
    else:
        expect(why and ("tesseract" in why.lower() or "opencv" in why.lower()),
               f"the reason a reader is unavailable names nothing to install: "
               f"{why!r}")


def test_the_default_is_still_the_measured_reader():
    """Optional means off. The exact reader stays in charge unless asked."""
    from esd_scheduler.resources import LabResources
    expect(LabResources().image_reader == "classical",
           "a board with no config defaults to something other than geometry")
    expect(LabResources.load().image_reader == "classical",
           "the shipped config no longer defaults to the measured reader")


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
