"""Every combination of the hard gates, enumerated rather than sampled.

The board refuses a coordinator for one of a fixed set of reasons, and those
reasons are checked in a fixed order. Two things then have to be true and are
easy to lose in a refactor:

* **No gate is dead.** Each one must be able to veto on its own, or it is a rule
  the lab believes in and the code no longer enforces.
* **The order is the documented order.** When several gates would fail at once,
  the reason shown has to be the highest-priority one, every time. A board that
  reports whichever gate happened to be evaluated first gives a different
  explanation for the same situation on different days.

This module builds a case for every point in the cross product of the factors
below and reports what the gates did, so both properties are checked against the
whole surface instead of a handful of examples.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from .constraints import (
    FILTER_CLINICIAN,
    FILTER_LAB,
    FILTER_OFFERED,
    ReliabilityMatrix,
    check_candidate,
    resource_checks,
)
from .models import (
    BusyBlock,
    CalendarSnapshot,
    Coordinator,
    Family,
    LabState,
    Visit,
    WorkingHours,
)

# Gate order as check_candidate runs them. The index is the priority: when more
# than one would fail, the lowest index is the reason the board must give.
GATE_ORDER: Tuple[str, ...] = (
    "ndd_override",
    "reliability",
    "in_training_unpaired",
    "lab_day",
    "friday",
    "calendar_conflict",
    "insufficient_evidence",
    "kit_ceiling",
)

# Each factor is one thing that can be true or false about a candidate, paired
# with the veto it should provoke when set. None means "provokes nothing".
FACTORS: Dict[str, Tuple[object, ...]] = {
    # The reliability gates only run once the matrix is confirmed against the
    # manual, so an unconfirmed matrix is one of the states to enumerate: it is
    # how the system actually ships, and the gates being inert there is the
    # intended behaviour rather than a hole.
    "matrix": ("unconfirmed", "confirmed_ok", "confirmed_unreliable",
               "confirmed_training"),
    "ndd_visit": (False, True),
    "on_lab_day": (False, True),
    "on_friday": (False, True),
    "evidence": ("clear", "conflict", "insufficient"),
    "nano_at_ceiling": (False, True),
}


@dataclass
class Scenario:
    """One point in the cross product, and what the gates said about it."""

    factors: Dict[str, object]
    passed: bool
    reason: Optional[str]
    message: str = ""
    expected_reasons: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def expected_first(self) -> Optional[str]:
        """Highest-priority veto these factors should have produced."""
        ranked = sorted(self.expected_reasons,
                        key=lambda r: GATE_ORDER.index(r)
                        if r in GATE_ORDER else len(GATE_ORDER))
        return ranked[0] if ranked else None

    def to_dict(self) -> dict:
        return {
            "factors": dict(self.factors),
            "passed": self.passed,
            "reason": self.reason,
            "expected": self.expected_first,
            "message": self.message,
        }


ASSESSMENT = "ADOS"


def _matrix_for(mode: str) -> ReliabilityMatrix:
    """A reliability matrix in one of the four states that change the gates."""
    if mode == "unconfirmed":
        return ReliabilityMatrix(
            assessments=[ASSESSMENT], staff={}, requirements={}, confirmed=False)

    status = {
        "confirmed_ok": "RELIABLE",
        "confirmed_unreliable": "NOT_RELIABLE",
        "confirmed_training": "IN_TRAINING",
    }[mode]
    return ReliabilityMatrix(
        assessments=[ASSESSMENT],
        staff={
            "CSC": {ASSESSMENT: status},
            # A signed-off colleague exists only in the in-training case, so the
            # buddy rule has somebody to pair with and does not veto for the
            # wrong reason.
            "CBUD": {ASSESSMENT: "RELIABLE"} if mode != "confirmed_training"
                    else {ASSESSMENT: "NOT_RELIABLE"},
        },
        requirements={"NICO": {"12mo": [ASSESSMENT]},
                      "NANO": {"3mo": [ASSESSMENT]}},
        confirmed=True,
    )


def _build_case(factors: Dict[str, object], now: datetime):
    """A minimal lab in which exactly the requested factors are true."""
    state = LabState()
    matrix = _matrix_for(factors.get("matrix", "unconfirmed"))

    family = Family(
        family_id="FSC",
        protocol="NANO" if factors.get("nano_at_ceiling") else "NICO",
        sigma=1,
        is_ndd_cross_collab=bool(factors.get("ndd_visit")),
    )
    state.families["FSC"] = family

    # Tuesday by default so the Friday gate stays out of play; Friday when the
    # scenario asks for it.
    weekday = 4 if factors.get("on_friday") else 1
    start = (now + timedelta(days=(weekday - now.weekday()) % 7)).replace(
        hour=10, minute=0, second=0, microsecond=0)
    visit = Visit(
        visit_id="VSC",
        family_id="FSC",
        protocol=family.protocol,
        checkpoint="12mo" if family.protocol == "NICO" else "3mo",
        window_start=start,
        window_end=start + timedelta(hours=6),
        duration_hours=2.0,
        location="lab",
    )

    coord = Coordinator(
        coordinator_id="CSC",
        name="Scenario Coordinator",
        credentials={"ADOS", "CONSENT", "DRIVING", "EEG"},
        capacity_hours_week=20.0,
        n_completed_visits=40,
    )
    coord.in_lab_day = start.weekday() if factors.get("on_lab_day") else None
    state.coordinators["CSC"] = coord

    slot = (start, start + timedelta(hours=2))

    evidence = factors.get("evidence")
    blocks: List[BusyBlock] = []
    if evidence == "conflict":
        blocks.append(BusyBlock(start=slot[0], end=slot[1], status="busy"))
    if evidence == "insufficient":
        snapshot = CalendarSnapshot(
            coordinator_id="CSC", provider="mock", fetched_at=now,
            blocks=[], sync_ok=False, error_code="unreachable")
    else:
        snapshot = CalendarSnapshot(
            coordinator_id="CSC", provider="mock", fetched_at=now,
            blocks=blocks, sync_ok=True,
            working_hours=WorkingHours())
    state.calendars["CSC"] = snapshot

    if factors.get("nano_at_ceiling") and family.protocol == "NANO":
        # Two NANO visits already overlapping this slot puts the kit at its
        # ceiling, which is the condition gate 7 exists to catch.
        for i in range(2):
            fid = f"FK{i}"
            state.families[fid] = Family(family_id=fid, protocol="NANO")
            other = Visit(
                visit_id=f"VK{i}", family_id=fid, protocol="NANO",
                checkpoint="3mo", window_start=slot[0], window_end=slot[1],
                duration_hours=2.0)
            state.pending.setdefault("CSC", []).append(other)

    return state, visit, family, coord, slot, matrix


def _expected(factors: Dict[str, object], matrix: ReliabilityMatrix) -> Tuple[str, ...]:
    """Which vetoes these factors ought to provoke."""
    out: List[str] = []
    if factors.get("ndd_visit") and "CSC" not in matrix.ndd_certified():
        out.append("ndd_override")
    mode = factors.get("matrix")
    if mode == "confirmed_unreliable":
        out.append("reliability")
    if mode == "confirmed_training":
        out.append("in_training_unpaired")
    if factors.get("on_lab_day"):
        out.append("lab_day")
    if factors.get("on_friday"):
        out.append("friday")
    if factors.get("evidence") == "conflict":
        out.append("calendar_conflict")
    if factors.get("evidence") == "insufficient":
        out.append("insufficient_evidence")
    if factors.get("nano_at_ceiling"):
        out.append("kit_ceiling")
    return tuple(out)


def enumerate_scenarios(now: Optional[datetime] = None) -> List[Scenario]:
    """Run every combination of the factors and record what the gates did."""
    now = now or datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    keys = list(FACTORS)
    out: List[Scenario] = []
    for values in itertools.product(*(FACTORS[k] for k in keys)):
        factors = dict(zip(keys, values))
        state, visit, family, coord, slot, matrix = _build_case(factors, now)
        result = check_candidate(coord, visit, family, state, now, slot=slot,
                                 matrix=matrix, pool=[coord])
        out.append(Scenario(
            factors=factors,
            passed=result.passed,
            reason=None if result.passed else result.gate,
            message=result.reason or "",
            expected_reasons=_expected(factors, matrix),
        ))
    return out


def decision_table(now: Optional[datetime] = None) -> List[dict]:
    """The enumeration as plain rows, for a report or an export."""
    return [s.to_dict() for s in enumerate_scenarios(now)]


def coverage(now: Optional[datetime] = None) -> Dict[str, int]:
    """How many scenarios each veto actually fired in.

    A zero here is the interesting number: it means a rule the lab believes in
    is never reachable, which is indistinguishable from having deleted it.
    """
    counts = {gate: 0 for gate in GATE_ORDER}
    for scenario in enumerate_scenarios(now):
        if scenario.reason in counts:
            counts[scenario.reason] += 1
    return counts


def resource_matrix() -> List[dict]:
    """Every combination of the three policy calendars against one window.

    Included because these are the newest gates and the easiest to get
    backwards: two of them mark time that is ALLOWED and one marks time that is
    TAKEN.
    """
    day = datetime(2026, 8, 17)
    window = (day.replace(hour=13), day.replace(hour=15))
    offered = [{"start": day.replace(hour=12).isoformat(),
                "end": day.replace(hour=16).isoformat()}]
    shifts = [{"start": day.replace(hour=9).isoformat(),
               "end": day.replace(hour=17).isoformat()}]
    booked = [{"start": day.replace(hour=14).isoformat(),
               "end": day.replace(hour=15).isoformat()}]

    rows = []
    for has_offered, has_shift, has_lab, needs_clin, in_lab in itertools.product(
            (False, True), repeat=5):
        resources = {}
        if has_offered:
            resources[FILTER_OFFERED] = offered
        if has_shift:
            resources[FILTER_CLINICIAN] = shifts
        if has_lab:
            resources[FILTER_LAB] = booked
        checks = resource_checks(window[0], window[1], resources,
                                 requires_clinician=needs_clin, in_lab=in_lab)
        rows.append({
            "offered_calendar": has_offered,
            "shift_calendar": has_shift,
            "lab_calendar": has_lab,
            "requires_clinician": needs_clin,
            "in_lab": in_lab,
            "offered": checks[FILTER_OFFERED][0],
            "clinician": checks[FILTER_CLINICIAN][0],
            "lab": checks[FILTER_LAB][0],
        })
    return rows
