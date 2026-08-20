"""Hard gates. Boolean pass/fail, run in order, short-circuit on first failure.

Master prompt §2. None of these are scores and none of them can be outranked by
one: a candidate that fails any gate is out, and the ranking never sees them.
The order matters because the cheapest and most consequential checks come first,
and because a failure explanation should name the *first* reason a person is
unavailable rather than an arbitrary one.

    1  NDD mandatory override      only NDD-certified staff, else escalate
    2  Assessment reliability      RELIABLE on every assessment the visit needs
    3  In-training buddy           IN_TRAINING only when paired with a RELIABLE
    4  Lab-day exclusion           nobody off-site on their in-lab day
    5  Friday prohibition          lab meetings, needs a logged override
    6  Calendar evidence           clear / conflict / insufficient - three states
    7  NANO kit ceiling            only two tech kits exist
    8  Visit-window boundary       age-specific window from the manual
    9  36-month exclusion          never automated, route to manual
   10  24-month remote bypass      route to the REDCap questionnaire flow

Rules 9 and 10 are *routing* decisions rather than per-candidate gates: they
take the whole visit out of the pipeline, so they are checked before any
candidate is considered.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from .models import (
    EVIDENCE_CLEAR,
    EVIDENCE_CONFLICT,
    EVIDENCE_INSUFFICIENT,
    Coordinator,
    Family,
    LabState,
    Visit,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATRIX_PATH = os.path.join(ROOT, "config", "reliability-matrix.json")

# Master §2 rule 8. Days either side of the ideal date, by checkpoint.
VISIT_WINDOWS: Dict[str, Tuple[int, int]] = {
    "baseline": (-5, 7),
    "1mo": (-5, 7),
    "3mo": (-5, 7),
    "6mo": (-14, 28),
    "9mo": (-14, 28),
    "12mo": (-14, 42),
    "24mo": (-14, 42),
}

NDD_ASSESSMENT = "NDD_cross_collab"
NDD_EXTRA_MINUTES = 60          # Master §6
NANO_KIT_CEILING = 2            # Master §2 rule 7
ROUTE_MANUAL = "manual_36_month"
ROUTE_REMOTE = "remote_24_month"


# ---------------------------------------------------------------------------
# Reliability matrix
# ---------------------------------------------------------------------------


@dataclass
class ReliabilityMatrix:
    """Who is RELIABLE or IN_TRAINING on what, loaded from config.

    Deliberately data, not code. The lab adds a row when someone finishes
    training; nobody edits Python to record it (Master §5.3).
    """

    assessments: List[str] = field(default_factory=list)
    staff: Dict[str, Dict[str, str]] = field(default_factory=dict)
    requirements: Dict[str, Dict[str, List[str]]] = field(default_factory=dict)
    confirmed: bool = False

    @classmethod
    def load(cls, path: Optional[str] = None) -> "ReliabilityMatrix":
        path = path or MATRIX_PATH
        if not os.path.exists(path):
            return cls()
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls(
            assessments=raw.get("assessments", []),
            staff={
                cid: node.get("assessments", {})
                for cid, node in raw.get("staff", {}).items()
            },
            requirements=raw.get("visit_requirements", {}),
            confirmed=bool(raw.get("confirmed")),
        )

    def required_for(self, protocol: str, checkpoint: str) -> List[str]:
        return list(self.requirements.get(protocol, {}).get(checkpoint, []))

    def status(self, coordinator_id: str, assessment: str) -> Optional[str]:
        return self.staff.get(coordinator_id, {}).get(assessment)

    def is_reliable(self, coordinator_id: str, assessment: str) -> bool:
        return self.status(coordinator_id, assessment) == "RELIABLE"

    def is_in_training(self, coordinator_id: str, assessment: str) -> bool:
        return self.status(coordinator_id, assessment) == "IN_TRAINING"

    def reliable_staff(self, assessment: str) -> List[str]:
        return [c for c in self.staff if self.is_reliable(c, assessment)]

    def ndd_certified(self) -> List[str]:
        return self.reliable_staff(NDD_ASSESSMENT)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class GateResult:
    passed: bool
    gate: Optional[str] = None      # machine name of the first failing gate
    reason: Optional[str] = None    # plain language, safe to show a scheduler
    evidence: str = EVIDENCE_CLEAR


@dataclass
class RoutingResult:
    """Whether this visit belongs in the automated pipeline at all."""

    automated: bool
    route: Optional[str] = None
    reason: Optional[str] = None
    escalate: bool = False


# ---------------------------------------------------------------------------
# Visit-level routing (rules 8, 9, 10)
# ---------------------------------------------------------------------------


def route_visit(
    visit: Visit,
    family: Family,
    matrix: Optional[ReliabilityMatrix] = None,
    ideal_date: Optional[date] = None,
) -> RoutingResult:
    """Rules 9, 10 and 8, checked before any candidate is scored."""
    matrix = matrix or ReliabilityMatrix.load()

    # Rule 9: 36-month visits are grad-student-availability dependent in a way
    # the fixed coordinator model cannot represent. Never automate them.
    if visit.checkpoint in ("36mo", "36 month", "36"):
        return RoutingResult(
            False, ROUTE_MANUAL,
            "36-month visits are assigned by hand: who can take them depends on "
            "graduate-student availability the board does not model.",
        )

    # Rule 10: 24-month is a remote questionnaire, not a staffed visit.
    if visit.checkpoint in ("24mo", "24 month", "24"):
        return RoutingResult(
            False, ROUTE_REMOTE,
            "24-month is completed remotely through REDCap. No coordinator or "
            "tech kit is needed.",
        )

    # Rule 8: the requested date must sit inside the manual's age window.
    window = VISIT_WINDOWS.get(visit.checkpoint)
    if window and ideal_date:
        earliest = ideal_date + timedelta(days=window[0])
        latest = ideal_date + timedelta(days=window[1])
        if not (earliest <= visit.window_start.date() <= latest):
            return RoutingResult(
                False, None,
                f"Outside the {visit.checkpoint} window "
                f"({earliest:%-d %b} to {latest:%-d %b}). Move the date before "
                f"looking for staff.",
                escalate=True,
            )

    # Rule 1, visit half: an NDD visit with nobody certified cannot be staffed.
    if family.is_ndd_cross_collab and not matrix.ndd_certified():
        return RoutingResult(
            False, None,
            "This is an NDD cross-collaboration visit and no coordinator is "
            "recorded as certified on NDD measures. Escalation needed: confirm "
            "the reliability matrix rather than substituting someone.",
            escalate=True,
        )

    return RoutingResult(True)


def ndd_extension_hours(family: Family) -> float:
    """Master §6: NDD cross-collaboration visits run 60 minutes longer."""
    return NDD_EXTRA_MINUTES / 60.0 if family.is_ndd_cross_collab else 0.0


def apply_visit_duration(visit: Visit, family: Family) -> Visit:
    """Bake the NDD extension into the Visit, once, at construction.

    §6 requires the *adjusted* duration to feed the conflict window, not the
    static table value. The reliable way to guarantee that is to have exactly
    one owner of the number: if the extension were applied at read time instead,
    every caller that forgot would under-block the visit, and the board would
    double-book the tail of it. Applied here, scoring, slot finding, the
    conflict check and the display all read the same field.
    """
    extra = ndd_extension_hours(family)
    if extra and not getattr(visit, "_ndd_applied", False):
        visit.duration_hours = round(visit.duration_hours + extra, 3)
        setattr(visit, "_ndd_applied", True)
    return visit


def visit_duration_hours(visit: Visit, family: Family) -> float:
    """The duration everything downstream must use.

    Already includes the NDD extension, because apply_visit_duration baked it
    in when the visit was built. Reading it here rather than re-adding keeps a
    single source of truth.
    """
    return visit.duration_hours


def offer_window(visit: Visit, family: Family, lab_open: float = 8.0,
                 lab_close: float = 17.0) -> Tuple[float, float]:
    """Earliest and latest offerable start hour for this family.

    A function of that family's drive time, not a fixed lab-hours assumption:
    a 50-minute drive means the last offerable start is 50 minutes earlier than
    it would be for a family next door (Master §6).
    """
    travel = family.drive_time_minutes / 60.0
    duration = visit_duration_hours(visit, family)
    return (lab_open + travel, max(lab_open + travel, lab_close - travel - duration))


# ---------------------------------------------------------------------------
# Candidate-level gates (rules 1-7)
# ---------------------------------------------------------------------------


def evidence_state(
    coordinator_id: str,
    state: LabState,
    start: datetime,
    end: datetime,
    now: datetime,
) -> str:
    """clear / conflict / insufficient — never a two-state guess.

    Absence of a busy block is not proof of free. If we have no reviewed
    evidence covering this window, the honest answer is "insufficient", and the
    gate fails rather than assuming availability.
    """
    snapshot = state.calendars.get(coordinator_id)
    if snapshot is None or not snapshot.sync_ok:
        return EVIDENCE_INSUFFICIENT
    for block in snapshot.hard_blocks():
        if block.overlaps(start, end):
            return EVIDENCE_CONFLICT
    # Evidence must actually cover the window we are asking about.
    if snapshot.working_hours is not None and not snapshot.working_hours.covers(start, end):
        return EVIDENCE_INSUFFICIENT
    return EVIDENCE_CLEAR


def check_candidate(
    coordinator: Coordinator,
    visit: Visit,
    family: Family,
    state: LabState,
    now: datetime,
    slot: Optional[Tuple[datetime, datetime]] = None,
    matrix: Optional[ReliabilityMatrix] = None,
    pool: Optional[Sequence[Coordinator]] = None,
    allow_friday: bool = False,
) -> GateResult:
    """Run gates 1 through 7 in order and stop at the first failure."""
    matrix = matrix or ReliabilityMatrix.load()
    required = matrix.required_for(visit.protocol, visit.checkpoint)

    # --- 1. NDD mandatory override ------------------------------------------
    if family.is_ndd_cross_collab:
        certified = matrix.ndd_certified()
        if coordinator.coordinator_id not in certified:
            return GateResult(
                False, "ndd_override",
                "NDD visit: only staff certified on NDD measures may take it.",
            )

    # --- 2. Assessment reliability ------------------------------------------
    # Only enforced once the matrix has been confirmed against the manual.
    # An unconfirmed matrix would otherwise exclude the entire roster.
    if matrix.confirmed and required:
        unmet = [a for a in required if not matrix.is_reliable(coordinator.coordinator_id, a)]
        training = [a for a in unmet if matrix.is_in_training(coordinator.coordinator_id, a)]
        blocking = [a for a in unmet if a not in training]
        if blocking:
            return GateResult(
                False, "reliability",
                "Not signed off on " + ", ".join(_pretty(a) for a in blocking) + ".",
            )

        # --- 3. In-training buddy rule --------------------------------------
        if training:
            partners = [
                c for c in (pool or state.active_coordinators())
                if c.coordinator_id != coordinator.coordinator_id
                and all(matrix.is_reliable(c.coordinator_id, a) for a in training)
            ]
            if not partners:
                return GateResult(
                    False, "in_training_unpaired",
                    "Still in training on " + ", ".join(_pretty(a) for a in training)
                    + ", and nobody signed off is free to pair with them.",
                )

    # --- 4. Lab-day exclusion ------------------------------------------------
    if slot and coordinator.in_lab_day is not None:
        if slot[0].weekday() == coordinator.in_lab_day:
            return GateResult(
                False, "lab_day",
                "This is their in-lab day; they cannot be off-site.",
            )

    # --- 5. Friday prohibition ----------------------------------------------
    if slot and slot[0].weekday() == 4 and not allow_friday:
        return GateResult(
            False, "friday",
            "Fridays are held for lab meetings. Needs a logged override.",
        )

    # --- 6. Calendar evidence, three states ---------------------------------
    if slot:
        ev = evidence_state(coordinator.coordinator_id, state, slot[0], slot[1], now)
        if ev == EVIDENCE_CONFLICT:
            return GateResult(False, "calendar_conflict",
                              "Calendar shows a conflict in this window.", ev)
        if ev == EVIDENCE_INSUFFICIENT:
            return GateResult(
                False, "insufficient_evidence",
                "Sync calendars first — no current availability data for this "
                "date.", ev,
            )

    # --- 7. NANO kit ceiling -------------------------------------------------
    if visit.protocol == "NANO" and slot:
        overlapping = _overlapping_nano(state, visit, slot)
        if overlapping >= NANO_KIT_CEILING:
            return GateResult(
                False, "kit_ceiling",
                f"Both NANO tech kits are already out in this window "
                f"({overlapping} visits overlap).",
            )

    return GateResult(True)


def _overlapping_nano(state: LabState, visit: Visit, slot) -> int:
    count = 0
    for pending in state.pending.values():
        for other in pending:
            if other.visit_id == visit.visit_id or other.protocol != "NANO":
                continue
            if other.window_start < slot[1] and slot[0] < other.window_end:
                count += 1
    return count


def _pretty(assessment: str) -> str:
    """Assessment codes are for the matrix; people read words."""
    return (
        assessment.replace("_", " ")
        .replace("CSBS", "CSBS")
        .replace("NDD cross collab", "NDD measures")
        .strip()
    )
