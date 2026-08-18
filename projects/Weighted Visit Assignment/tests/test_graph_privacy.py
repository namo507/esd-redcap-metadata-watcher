"""Privacy and correctness guards on the Microsoft Graph calendar path.

These encode the promises made to the PI in ESD-Graph-Privacy-RESEARCH-REPORT.md,
so that they cannot lapse quietly in a later refactor:

  * the tool never holds an event subject, location or privacy flag
  * the tool refuses to run on a token that grants more than free/busy reading
  * an empty calendar outside working hours is not availability
  * busy is 2 and free is 0, not the reverse

Run with:  python3 tests/test_graph_privacy.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from esd_scheduler import calendarsync as cs
from esd_scheduler.config import EngineConfig
from esd_scheduler.feasibility import find_slot
from esd_scheduler.models import (
    CalendarSnapshot,
    Coordinator,
    Visit,
    WorkingHours,
)

NOW = datetime(2026, 8, 17, 9, 0)  # a Monday


def _token(scp=None, roles=None):
    """Build an unsigned JWT-shaped token for the scope guard to inspect."""
    claims = {}
    if scp is not None:
        claims["scp"] = scp
    if roles is not None:
        claims["roles"] = roles
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


def expect_raises(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    raise AssertionError(f"expected {exc_type.__name__}")


# ---------------------------------------------------------------------------
# Field stripping
# ---------------------------------------------------------------------------


def test_subject_location_and_privacy_are_stripped():
    """The exact payload from the Microsoft documentation example.

    This is the shape Exchange sends when a calendar is shared at Limited
    details. We must drop the three sensitive fields even though the server
    was willing to give them to us.
    """
    entry = {
        "isPrivate": False,
        "status": "busy",
        "subject": "Let's go for lunch",
        "location": "Harry's Bar",
        "start": {"dateTime": "2026-08-17T12:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-08-17T14:00:00.0000000", "timeZone": "UTC"},
    }
    clean = cs.strip_event_details(entry)
    assert "subject" not in clean
    assert "location" not in clean
    assert "isPrivate" not in clean
    # Everything the scheduler actually needs survives.
    assert clean["status"] == "busy"
    assert clean["start"] and clean["end"]


def test_stripping_is_total_over_the_forbidden_set():
    noisy = {f: "leaked" for f in cs.FORBIDDEN_EVENT_FIELDS}
    noisy["status"] = "tentative"
    clean = cs.strip_event_details(noisy)
    assert set(clean) == {"status"}
    assert "leaked" not in json.dumps(clean)


def test_graph_provider_discards_subjects_end_to_end():
    """A provider fed a subject-bearing response must not surface it anywhere."""
    documented_response = {
        "value": [
            {
                "scheduleId": "adelev@contoso.com",
                "availabilityView": "000220130",
                "scheduleItems": [
                    {
                        "isPrivate": False,
                        "status": "busy",
                        "subject": "Let's go for lunch",
                        "location": "Harry's Bar",
                        "start": {"dateTime": "2026-08-17T12:00:00.0000000"},
                        "end": {"dateTime": "2026-08-17T14:00:00.0000000"},
                    }
                ],
                "workingHours": {
                    "daysOfWeek": ["monday", "tuesday", "wednesday", "thursday", "friday"],
                    "startTime": "08:00:00.0000000",
                    "endTime": "17:00:00.0000000",
                    "timeZone": {"name": "Eastern Standard Time"},
                },
            }
        ]
    }
    provider = cs.GraphProvider(
        token_provider=lambda: _token(scp="Calendars.Read.Shared"),
        mailbox_of={"C01": "adelev@contoso.com"},
    )
    original_post = cs._post_json
    cs._post_json = lambda *a, **k: documented_response
    try:
        snaps = provider.fetch(["C01"], NOW, NOW + timedelta(days=1))
    finally:
        cs._post_json = original_post

    snap = snaps["C01"]
    assert snap.sync_ok
    assert len(snap.blocks) == 1
    block = snap.blocks[0]
    assert block.status == "busy"
    # BusyBlock has no field that could carry a title, and nothing anywhere in
    # the snapshot serialises one.
    assert not hasattr(block, "subject")
    assert "lunch" not in repr(snap).lower()
    assert "harry" not in repr(snap).lower()
    # Working hours came through in the same call.
    assert snap.working_hours is not None
    assert snap.working_hours.start_hour == 8.0
    assert snap.working_hours.end_hour == 17.0


# ---------------------------------------------------------------------------
# Scope guard
# ---------------------------------------------------------------------------


def test_delegated_freebusy_token_is_accepted():
    claims = cs.assert_least_privilege(_token(scp="Calendars.Read.Shared"))
    assert claims["scp"] == "Calendars.Read.Shared"


def test_application_permissions_are_refused():
    """A roles claim means app-only. There is no calendar app role below
    Calendars.Read, which reads subject and body, so we refuse outright."""
    expect_raises(
        cs.ScopeViolation,
        cs.assert_least_privilege,
        _token(roles=["Calendars.Read"]),
    )
    expect_raises(
        cs.ScopeViolation,
        cs.assert_least_privilege,
        _token(scp="Calendars.Read.Shared", roles=["Calendars.ReadWrite"]),
    )


def test_over_broad_delegated_scopes_are_refused():
    for scope in (
        "Calendars.Read",
        "Calendars.ReadWrite",
        "Calendars.Read.Shared Mail.Read",
        "Calendars.ReadBasic",
    ):
        expect_raises(cs.ScopeViolation, cs.assert_least_privilege, _token(scp=scope))


def test_token_with_no_scopes_is_refused():
    expect_raises(cs.ScopeViolation, cs.assert_least_privilege, _token())
    expect_raises(cs.ScopeViolation, cs.assert_least_privilege, "not-a-jwt")


def test_provider_fetch_fails_closed_on_a_bad_token():
    """A scope problem is a privacy problem. It must raise, not degrade into a
    sync_ok=False snapshot that the rest of the engine treats as a bad day."""
    provider = cs.GraphProvider(
        token_provider=lambda: _token(roles=["Calendars.Read"]),
        mailbox_of={"C01": "a@contoso.com"},
    )
    expect_raises(
        cs.ScopeViolation, provider.fetch, ["C01"], NOW, NOW + timedelta(days=1)
    )


# ---------------------------------------------------------------------------
# availabilityView encoding
# ---------------------------------------------------------------------------


def test_availability_view_digits_match_microsoft_not_copilot():
    """Copilot told the lab "busy is zero, tentative one, available three".

    That inverts free and busy. Building on it would book visits into exactly
    the slots people are busy, and the result would still look like a plausible
    schedule. Pin the real encoding.
    """
    assert cs._AVAILABILITY_VIEW["0"] == "free"
    assert cs._AVAILABILITY_VIEW["1"] == "tentative"
    assert cs._AVAILABILITY_VIEW["2"] == "busy"
    assert cs._AVAILABILITY_VIEW["3"] == "oof"
    assert cs._AVAILABILITY_VIEW["0"] != "busy"
    assert cs._AVAILABILITY_VIEW["3"] != "free"


# ---------------------------------------------------------------------------
# Working hours: blank is not free
# ---------------------------------------------------------------------------


def _visit(start_hour: int, end_hour: int) -> Visit:
    return Visit(
        visit_id="V1",
        family_id="F1",
        protocol="NICO",
        checkpoint="12mo",
        window_start=NOW.replace(hour=start_hour, minute=0),
        window_end=NOW.replace(hour=end_hour, minute=0),
        duration_hours=2.0,
    )


def test_empty_calendar_outside_working_hours_is_not_availability():
    """Ellen's question. An 8:30-to-17:00 person with an empty calendar is
    available at 10:00 and is not available at 19:00, and the difference comes
    from workingHours, not from the absence of events."""
    coordinator = Coordinator("C1", "C1", credentials={"ADOS", "CONSENT", "DRIVING"})
    hours = WorkingHours(
        days_of_week=frozenset({0, 1, 2, 3, 4}), start_hour=8.5, end_hour=17.0
    )
    snapshot = CalendarSnapshot(
        coordinator_id="C1", provider="msgraph", fetched_at=NOW,
        blocks=[], working_hours=hours,
    )
    assert find_slot(coordinator, _visit(9, 13), snapshot) is not None
    assert find_slot(coordinator, _visit(18, 22), snapshot) is None


def test_outlook_working_hours_override_the_local_guess():
    """A part-time coordinator who finishes at noon must not be offered a 14:00
    visit just because our configured default said 08:00-17:00."""
    coordinator = Coordinator(
        "C1", "C1", working_blocks=[(d, 8.0, 17.0) for d in range(5)]
    )
    part_time = WorkingHours(
        days_of_week=frozenset({0, 1, 2, 3, 4}), start_hour=8.0, end_hour=12.0
    )
    with_hours = CalendarSnapshot(
        coordinator_id="C1", provider="msgraph", fetched_at=NOW,
        blocks=[], working_hours=part_time,
    )
    without_hours = CalendarSnapshot(
        coordinator_id="C1", provider="msgraph", fetched_at=NOW, blocks=[]
    )
    afternoon = _visit(13, 17)
    assert find_slot(coordinator, afternoon, with_hours) is None
    assert find_slot(coordinator, afternoon, without_hours) is not None


def test_working_hours_exclude_non_working_days():
    saturday = WorkingHours(days_of_week=frozenset({0, 1, 2, 3, 4}))
    assert not saturday.contains(datetime(2026, 8, 22, 10, 0))  # Saturday
    assert saturday.contains(datetime(2026, 8, 21, 10, 0))      # Friday


def test_explicit_free_block_is_distinct_from_an_empty_slot():
    """Grad students mark blocks 'Free'. Graph reports that as a scheduleItem
    with status 'free', which is not the same as no entry at all, and neither
    blocks a visit."""
    from esd_scheduler.models import BusyBlock

    marked_free = BusyBlock(
        start=NOW.replace(hour=13), end=NOW.replace(hour=15), status="free"
    )
    assert not marked_free.is_hard()
    coordinator = Coordinator("C1", "C1")
    snapshot = CalendarSnapshot(
        coordinator_id="C1", provider="msgraph", fetched_at=NOW,
        blocks=[marked_free],
        working_hours=WorkingHours(),
    )
    assert find_slot(coordinator, _visit(13, 17), snapshot) is not None


# ---------------------------------------------------------------------------
# Config guard
# ---------------------------------------------------------------------------


def test_app_only_mode_requires_an_explicit_acknowledgement():
    EngineConfig().validate()  # delegated by default
    expect_raises(ValueError, EngineConfig(graph_auth_mode="application").validate)
    EngineConfig(
        graph_auth_mode="application",
        allow_app_only_ack="PI sign-off 2026-08-18",
    ).validate()


def test_unknown_auth_mode_is_refused():
    expect_raises(ValueError, EngineConfig(graph_auth_mode="app-only").validate)


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
