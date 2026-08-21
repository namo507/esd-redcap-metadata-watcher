"""The ten hard gates (Master prompt §2).

These are the rules that keep the wrong person out of a visit, so they get
tested as rules rather than through the UI. The NDD cases matter most: a
substitution there is a clinical error, not a scheduling inconvenience.

Run:  python3 tests/test_constraints.py
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from esd_scheduler.constraints import (
    NANO_KIT_CEILING,
    ROUTE_MANUAL,
    ROUTE_REMOTE,
    GateResult,
    ReliabilityMatrix,
    check_candidate,
    evidence_state,
    offer_window,
    route_visit,
    visit_duration_hours,
)
from esd_scheduler.models import (
    EVIDENCE_CLEAR,
    EVIDENCE_CONFLICT,
    EVIDENCE_INSUFFICIENT,
    BusyBlock,
    CalendarSnapshot,
    Coordinator,
    Family,
    LabState,
    Visit,
    WorkingHours,
)

MON = datetime(2026, 8, 17, 9, 0)     # a Monday
FRI = datetime(2026, 8, 21, 9, 0)


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def a_visit(checkpoint="12mo", protocol="NICO", start=MON, hours=2.0):
    return Visit("V1", "F1", protocol, checkpoint, start, start + timedelta(days=3), hours)


def a_state(coordinator, blocks=(), working=True, sync_ok=True):
    st = LabState()
    st.coordinators[coordinator.coordinator_id] = coordinator
    st.calendars[coordinator.coordinator_id] = CalendarSnapshot(
        coordinator_id=coordinator.coordinator_id, provider="mock", fetched_at=MON,
        blocks=list(blocks), sync_ok=sync_ok,
        working_hours=WorkingHours() if working else None,
    )
    return st


def matrix_with(**staff):
    m = ReliabilityMatrix(
        assessments=["CSBS_9_12m", "Bayley_9_12m", "NDD_cross_collab"],
        staff=staff,
        requirements={"NICO": {"12mo": ["CSBS_9_12m"]}},
        confirmed=True,
    )
    return m


# ---------------------------------------------------------------------------
# 1. NDD override
# ---------------------------------------------------------------------------


def test_ndd_visit_excludes_everyone_not_certified():
    m = matrix_with(C1={"NDD_cross_collab": "RELIABLE", "CSBS_9_12m": "RELIABLE"},
                    C2={"CSBS_9_12m": "RELIABLE"})
    fam = Family("F1", "NICO", is_ndd_cross_collab=True)
    c2 = Coordinator("C2", "Not certified")
    r = check_candidate(c2, a_visit(), fam, a_state(c2), MON, matrix=m)
    expect(not r.passed, "an uncertified coordinator took an NDD visit")
    expect(r.gate == "ndd_override", f"wrong gate fired: {r.gate}")


def test_ndd_visit_with_nobody_certified_escalates_rather_than_substituting():
    """The failure mode worth guarding: quietly picking the next best person."""
    m = matrix_with(C1={"CSBS_9_12m": "RELIABLE"})
    fam = Family("F1", "NICO", is_ndd_cross_collab=True)
    routing = route_visit(a_visit(), fam, m)
    expect(not routing.automated, "NDD visit entered the pipeline with nobody certified")
    expect(routing.escalate, "should be an escalation, not a silent skip")
    expect("substituting" in (routing.reason or ""), "reason must say why nobody was picked")


def test_ndd_adds_an_hour_to_the_conflict_window():
    """The extension is baked in once, at construction, so scoring and the
    conflict window cannot disagree with the display."""
    from esd_scheduler.constraints import apply_visit_duration

    fam = Family("F1", "NICO", is_ndd_cross_collab=True)
    plain = Family("F2", "NICO")

    ndd = apply_visit_duration(a_visit(hours=2.0), fam)
    expect(ndd.duration_hours == 3.0, f"NDD visit not extended: {ndd.duration_hours}")
    expect(visit_duration_hours(ndd, fam) == 3.0, "helper disagrees with the field")

    # Applying twice must not stack: exactly one owner of the number.
    again = apply_visit_duration(ndd, fam)
    expect(again.duration_hours == 3.0, f"extension applied twice: {again.duration_hours}")

    normal = apply_visit_duration(a_visit(hours=2.0), plain)
    expect(normal.duration_hours == 2.0, "non-NDD visit was extended")


# ---------------------------------------------------------------------------
# 2 & 3. Reliability and the in-training buddy rule
# ---------------------------------------------------------------------------


def test_not_signed_off_is_excluded():
    m = matrix_with(C1={}, C2={"CSBS_9_12m": "RELIABLE"})
    c1 = Coordinator("C1", "Untrained")
    r = check_candidate(c1, a_visit(), Family("F1", "NICO"), a_state(c1), MON, matrix=m)
    expect(not r.passed and r.gate == "reliability", f"expected reliability gate, got {r.gate}")


def test_in_training_passes_only_when_a_signed_off_partner_exists():
    m = matrix_with(C1={"CSBS_9_12m": "IN_TRAINING"}, C2={"CSBS_9_12m": "RELIABLE"})
    trainee = Coordinator("C1", "Trainee")
    partner = Coordinator("C2", "Signed off")
    st = a_state(trainee)
    st.coordinators["C2"] = partner

    ok = check_candidate(trainee, a_visit(), Family("F1", "NICO"), st, MON,
                         matrix=m, pool=[trainee, partner])
    expect(ok.passed, f"trainee with a partner was excluded: {ok.reason}")

    alone = check_candidate(trainee, a_visit(), Family("F1", "NICO"), st, MON,
                            matrix=m, pool=[trainee])
    expect(not alone.passed, "trainee was allowed to go solo")
    expect(alone.gate == "in_training_unpaired", f"wrong gate: {alone.gate}")


def test_an_unconfirmed_matrix_does_not_exclude_the_whole_roster():
    """Until the matrix is confirmed against the manual, reliability cannot be
    enforced — enforcing an empty matrix would leave nobody eligible for
    anything, which reads as a broken board rather than missing data."""
    m = ReliabilityMatrix(assessments=["CSBS_9_12m"], staff={"C1": {}},
                          requirements={"NICO": {"12mo": ["CSBS_9_12m"]}},
                          confirmed=False)
    c1 = Coordinator("C1", "Anyone")
    r = check_candidate(c1, a_visit(), Family("F1", "NICO"), a_state(c1), MON, matrix=m)
    expect(r.passed, "unconfirmed matrix excluded a candidate")


# ---------------------------------------------------------------------------
# 4, 5. Lab day and Friday
# ---------------------------------------------------------------------------


def test_nobody_goes_off_site_on_their_lab_day():
    m = matrix_with(C1={"CSBS_9_12m": "RELIABLE"})
    c1 = Coordinator("C1", "Lab Monday", in_lab_day=0)
    slot = (MON, MON + timedelta(hours=2))
    r = check_candidate(c1, a_visit(), Family("F1", "NICO"), a_state(c1), MON,
                        slot=slot, matrix=m)
    expect(not r.passed and r.gate == "lab_day", f"lab day not enforced: {r.gate}")


def test_friday_needs_an_explicit_override():
    m = matrix_with(C1={"CSBS_9_12m": "RELIABLE"})
    c1 = Coordinator("C1", "Anyone")
    slot = (FRI, FRI + timedelta(hours=2))
    st = a_state(c1)
    blocked = check_candidate(c1, a_visit(start=FRI), Family("F1", "NICO"), st, FRI,
                              slot=slot, matrix=m)
    expect(not blocked.passed and blocked.gate == "friday", "Friday was allowed silently")

    allowed = check_candidate(c1, a_visit(start=FRI), Family("F1", "NICO"), st, FRI,
                              slot=slot, matrix=m, allow_friday=True)
    expect(allowed.passed, f"logged Friday override was refused: {allowed.reason}")


# ---------------------------------------------------------------------------
# 6. Three-state evidence
# ---------------------------------------------------------------------------


def test_evidence_has_three_states_and_absence_is_not_free():
    c1 = Coordinator("C1", "Anyone")
    slot = (MON, MON + timedelta(hours=2))

    clear = a_state(c1)
    expect(evidence_state("C1", clear, *slot, MON) == EVIDENCE_CLEAR, "clear misread")

    busy = a_state(c1, blocks=[BusyBlock(MON, MON + timedelta(hours=3), "busy")])
    expect(evidence_state("C1", busy, *slot, MON) == EVIDENCE_CONFLICT, "conflict misread")

    none = a_state(c1, sync_ok=False)
    expect(evidence_state("C1", none, *slot, MON) == EVIDENCE_INSUFFICIENT,
           "a failed sync was treated as availability")

    missing = LabState()
    missing.coordinators["C1"] = c1
    expect(evidence_state("C1", missing, *slot, MON) == EVIDENCE_INSUFFICIENT,
           "no calendar at all was treated as availability")


def test_insufficient_evidence_fails_the_gate_with_a_useful_reason():
    m = matrix_with(C1={"CSBS_9_12m": "RELIABLE"})
    c1 = Coordinator("C1", "Anyone")
    st = a_state(c1, sync_ok=False)
    slot = (MON, MON + timedelta(hours=2))
    r = check_candidate(c1, a_visit(), Family("F1", "NICO"), st, MON, slot=slot, matrix=m)
    expect(not r.passed and r.gate == "insufficient_evidence", f"got {r.gate}")
    expect("Sync calendars" in (r.reason or ""), f"unhelpful reason: {r.reason}")
    expect(r.evidence == EVIDENCE_INSUFFICIENT, "evidence state not reported")


# ---------------------------------------------------------------------------
# 8, 9, 10. Windows and routing
# ---------------------------------------------------------------------------


def test_36_month_never_enters_the_pipeline():
    r = route_visit(a_visit(checkpoint="36mo"), Family("F1", "NICO"))
    expect(not r.automated and r.route == ROUTE_MANUAL, f"36mo routed to {r.route}")


def test_24_month_goes_remote():
    r = route_visit(a_visit(checkpoint="24mo"), Family("F1", "NICO"))
    expect(not r.automated and r.route == ROUTE_REMOTE, f"24mo routed to {r.route}")


def test_a_date_outside_the_age_window_is_rejected_before_staffing():
    ideal = date(2026, 8, 17)
    inside = route_visit(a_visit("12mo", start=datetime(2026, 8, 20, 9)),
                         Family("F1", "NICO"), ideal_date=ideal)
    expect(inside.automated, f"a date inside the window was rejected: {inside.reason}")

    outside = route_visit(a_visit("12mo", start=datetime(2026, 11, 1, 9)),
                          Family("F1", "NICO"), ideal_date=ideal)
    expect(not outside.automated, "a date months outside the window was accepted")
    expect(outside.escalate, "an out-of-window date should escalate")


def test_offer_window_shrinks_with_drive_time():
    near = Family("F1", "NICO", drive_time_minutes=0)
    far = Family("F2", "NICO", drive_time_minutes=60)
    v = a_visit(hours=2.0)
    n_open, n_close = offer_window(v, near)
    f_open, f_close = offer_window(v, far)
    expect(f_open > n_open, "a long drive did not push the earliest start later")
    expect(f_close < n_close, "a long drive did not pull the latest start earlier")


def test_the_shipped_matrix_matches_the_manual():
    """Transcribed from the manual's Clinical Assessment Reliability chart.

    Pinned because this file decides who may run a visit at all, and a silent
    edit to it is a clinical claim about a real person.
    """
    from esd_scheduler.constraints import ReliabilityMatrix

    m = ReliabilityMatrix.load()
    expect(m.confirmed, "the shipped matrix should be confirmed from the manual")

    # Sanjana: reliable on both CSBS bands, both Bayley bands and Orientation;
    # in training on ADOS.
    for a in ("CSBS_6m", "CSBS_9_12m", "Bayley_3m", "Bayley_9_12m",
              "Orientation_1_3m"):
        expect(m.is_reliable("C03", a), f"Sanjana should be reliable on {a}")
    expect(m.is_in_training("C03", "ADOS"), "Sanjana is in training on ADOS")

    # Lauren is in training on Bayley (3m), not reliable -- the difference the
    # chart draws and the gate depends on.
    expect(m.is_in_training("C02", "Bayley_3m"),
           "Lauren is in training on Bayley (3m)")
    expect(not m.is_reliable("C02", "Bayley_3m"),
           "Lauren is not yet reliable on Bayley (3m)")

    # Ramiro holds exactly one.
    expect(m.is_reliable("C06", "Bayley_9_12m"), "Ramiro: Bayley (9-12m)")
    expect(not m.is_reliable("C06", "CSBS_9_12m"), "Ramiro: not CSBS")

    # Maggie is in training only, so cannot run a visit solo.
    expect(m.is_in_training("C01", "CSBS_6m"), "Maggie is in training on CSBS")
    expect(not m.is_reliable("C01", "CSBS_6m"), "Maggie is not yet reliable")


def test_only_the_manuals_clinicians_can_run_a_nano_9m():
    """9m needs CSBS (9-12m) and Bayley (9-12m). The chart decides who that is."""
    from esd_scheduler.constraints import ReliabilityMatrix

    m = ReliabilityMatrix.load()
    needed = m.required_for("NANO", "9m")
    expect(set(needed) == {"CSBS_9_12m", "Bayley_9_12m"},
           f"9m requirements read as {needed}")
    able = {c for c in ("C01", "C02", "C03", "C04", "C05", "C06", "C07")
            if all(m.is_reliable(c, a) for a in needed)}
    expect(able == {"C02", "C03", "C07"},
           f"expected Lauren, Sanjana and Makenzie; got {sorted(able)}")


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
