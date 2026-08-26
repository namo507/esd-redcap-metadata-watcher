"""Tests for the NANO study integration.

None of these call REDCap. A test that needs a study token cannot run in CI,
and a test that needs the internet fails for reasons that have nothing to do
with the code. What is worth pinning is the reasoning applied to a record --
which anchor a window counts from, which ids are refused, what is allowed to
reach a browser -- and all of that runs on a record built here.

The rules under test come from the manual, not from REDCap: a preterm baby's
1m to 24m visits count from the expected due date, everyone's 36m counts from
the birthday, and a participant id is four digits starting with five.

Run:  python3 tests/test_redcap.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ESD_MODE", "demo")

from esd_scheduler import redcap  # noqa: E402


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def _family(**kw):
    base = dict(family_id="5901", participant_status="TD",
                birth_date="2024-06-01", due_date=None)
    base.update(kw)
    return redcap.NanoFamily(**base)


# --- which ids the board will accept ----------------------------------------


def test_only_a_nano_id_is_accepted():
    """The manual's own rule, and the reason it matters.

    A record from another arm, or a test row like "TEST_001", would otherwise
    become a family the board tries to schedule visits for.
    """
    for good in ("5000", "5900", "5999"):
        expect(redcap.NANO_ID.match(good), f"{good} is a valid NANO id")
    for bad in ("6031", "50311", "503", "abc", "", "T0000000001", "5a31"):
        expect(not redcap.NANO_ID.match(bad),
               f"{bad} was accepted as a NANO participant id")


def test_an_event_name_becomes_a_time_point_or_nothing():
    """REDCap writes them several ways, and a guess would staff a visit wrong.

    A checkpoint decides which assessments a visit needs and therefore who may
    run it, so an unreadable event name has to come back empty rather than
    approximately right.
    """
    for event, want in (("9_months_arm_1", "9m"), ("1_month_arm_1", "1m"),
                        ("36_months_arm_1", "36m"), ("visit_9m_arm_1", "9m"),
                        ("12_mo_arm_1", "12m"),
                        ("consent_arm_1", ""), ("caregiver_1_arm_1", ""),
                        ("sibling_arm_1", ""), ("", "")):
        got = redcap._checkpoint_from_event(event)
        expect(got == want, f"{event!r} read as {got!r}, expected {want!r}")


# --- which date a window counts from ----------------------------------------


def test_a_preterm_babys_early_windows_count_from_the_due_date():
    """The manual's rule, and the whole reason both dates are fetched.

    Born 1 June, due 1 July: the 1m visit is a month after the *due* date, not
    a month after the birth. Anchoring on the birthday would place every early
    visit exactly as early as the baby was.
    """
    preterm = _family(participant_status="PT", birth_date="2024-06-01",
                      due_date="2024-07-01")
    windows = {w["checkpoint"]: w for w in redcap.visit_windows(preterm)}
    expect(windows["1m"]["ideal"] == "2024-08-01",
           f"the 1m ideal date is {windows['1m']['ideal']}, and the manual's "
           f"worked example puts it on 2024-08-01")


def test_every_36m_visit_counts_from_the_birthday():
    """"regardless of status" -- so prematurity stops mattering at 36m."""
    preterm = _family(participant_status="PT", birth_date="2024-06-01",
                      due_date="2024-07-01")
    term = _family(participant_status="TD", birth_date="2024-06-01")
    at36 = {f.participant_status: next(
        w for w in redcap.visit_windows(f) if w["checkpoint"] == "36m")
        for f in (preterm, term)}
    expect(at36["PT"]["ideal"] == at36["TD"]["ideal"] == "2027-06-01",
           f"36m lands on {at36['PT']['ideal']} for a preterm baby and "
           f"{at36['TD']['ideal']} for a term one; both should be the birthday")


def test_no_anchor_is_no_window_rather_than_a_guessed_one():
    orphan = _family(birth_date=None, due_date=None)
    for window in redcap.visit_windows(orphan):
        expect(window["window_start"] is None,
               f"a family with no anchor date was given a window: {window}")
        expect("anchor" in window["status"],
               f"the reason is not stated: {window['status']}")


def test_the_next_window_is_the_one_somebody_must_act_on():
    """Open beats upcoming, and a closed window beats both.

    A visit whose window has shut is the one somebody needs to be told about.
    Sorting by date alone would bury it under everything still to come.
    """
    fam = _family(birth_date="2024-01-01")
    today = date(2025, 2, 1)
    window = redcap.next_window(fam, today)
    expect(window is not None, "a family with an anchor has no next window")
    expect(window["status"] in ("open", "missed"),
           f"the next window is {window['status']}, but something is already "
           f"open or overdue at this date")


# --- what reaches a browser -------------------------------------------------


def test_no_date_of_birth_ever_reaches_the_screen():
    """The windows are derived from the anchor dates. The dates stay here.

    A date of birth is an identifier. The board needs the *window*, and a
    screen that never receives the birth date cannot leak it, however the
    rendering code later changes.
    """
    from backend import nano

    # Anchor dates chosen so no derived date can equal them. A first attempt
    # used a due date of 2024-07-01 against a birth date of 2024-06-01, and
    # the *other* family's 1m ideal date was also 2024-07-01 -- the test
    # reported a leak that was two identical strings arriving by arithmetic.
    # 17 June and 23 July are not a whole number of months from anything here.
    tmp = tempfile.mkdtemp(prefix="redcap-test-")
    cache = os.path.join(tmp, "nano-families.json")
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump({"fetched_at": "2026-08-26T00:00:00", "families": [
            _family(family_id="5901", birth_date="2024-06-17").to_dict(),
            _family(family_id="5902", participant_status="PT",
                    birth_date="2024-06-17", due_date="2024-07-23").to_dict(),
        ]}, fh)

    original = redcap.CACHE_DIR
    redcap.CACHE_DIR = tmp
    try:
        summary = nano.summary()
        blob = json.dumps(summary)
        expect(summary["count"] == 2, f"expected 2 families, got {summary['count']}")
        for leak in ("2024-06-17", "2024-07-23", "birth_date", "due_date"):
            expect(leak not in blob,
                   f"{leak!r} is in what the families endpoint sends a browser")

        detail = json.dumps(nano.family_windows("5902"))
        for leak in ("birth_date", "due_date"):
            expect(leak not in detail,
                   f"{leak!r} is in what the family endpoint sends a browser")
    finally:
        redcap.CACHE_DIR = original


def test_an_unenrolled_participant_is_not_offered():
    """Withdrawing from a study has to mean something on the board."""
    from backend import nano

    tmp = tempfile.mkdtemp(prefix="redcap-test-")
    with open(os.path.join(tmp, "nano-families.json"), "w", encoding="utf-8") as fh:
        json.dump({"fetched_at": "2026-08-26T00:00:00", "families": [
            _family(family_id="5901").to_dict(),
        ]}, fh)
    original = redcap.CACHE_DIR
    redcap.CACHE_DIR = tmp
    try:
        expect(nano.summary()["count"] == 1, "the enrolled family is missing")
        expect(not nano.family_windows("5902")["found"],
               "a family that is not in the sync was found anyway")
    finally:
        redcap.CACHE_DIR = original


def test_the_token_is_not_in_the_repository():
    """The one check worth having about a credential.

    A token is a bearer credential for a study's record set. This walks the
    tracked files rather than trusting that .gitignore was right.
    """
    import subprocess

    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT,
                             capture_output=True, text=True).stdout.split()
    for name in tracked:
        if name.endswith(".env") or name.endswith("redcap.env"):
            raise AssertionError(f"{name} is tracked by git and may hold a token")
    hits = subprocess.run(
        ["git", "grep", "-lIE", r"REDCAP_TOKEN[[:space:]]*=[[:space:]]*[A-F0-9]{16}"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()
    expect(not hits, f"a REDCap token looks committed in: {hits}")


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
