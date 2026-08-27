"""Staffing a visit with two people: one clinician and one tech.

The manual is unambiguous -- "2 staff members are needed per visit: 1 clinician
AND 1 tech" -- and the clinician "must be able to reliably/independently admin
all the assessments needed for that visit age". So the unit being scheduled is a
*pair*, and ranking individuals answers a question the lab never asks.

Two decisions worth stating rather than burying, because both are choices:

**Which criteria combine, and how.** The four criteria split cleanly by who
experiences them. Continuity, family preference and protocol continuity are
about the family: the pair delivers them if *either* member does, so they take
the better of the two. Burden relief is about the lab: a visit consumes both
people's week, so it takes the mean. Nothing is re-weighted -- the same vector
applies to the combined components.

**Who can be the clinician.** Exactly the people the reliability chart says are
signed off on every assessment that visit needs. Where the chart lists nothing
for a visit, that is reported rather than filled in: the pair is still offered,
flagged as unverified, because the chart not covering a visit is not the same
as anybody being allowed to run it.
"""

# -------------------------------------------------------------------------
# STEP 8 OF 9  --  TWO PEOPLE AND A SLOT
#
#   before  a scored list of individuals
#   here    the manual staffs a visit with one clinician and one tech, so
#           the pair is the thing being chosen. Finds a slot both can
#           make
#   after   session.py hands the ranked pairs to the screen
#
#   worked example
#     family 5901, 9m, window 25 Jul - 5 Sep:
#       Lauren Puttock + Sanjana Oak    0.263   Mon 9:00 AM   best match
#       Lauren Puttock + Margaret Bell  0.201   Wed 9:00 AM
#     the slot chooser honours Fridays, in-lab days and university holidays
#     itself, because it picks its own slot rather than using a gate's
# -------------------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from .constraints import EVIDENCE_CLEAR, evidence_state

SLOT_STEP_MINUTES = 30


@dataclass
class Pair:
    """One clinician and one tech, and how good a pairing they are."""

    clinician_id: str
    clinician_name: str
    tech_id: str
    tech_name: str
    score: float
    components: Dict[str, float] = field(default_factory=dict)
    contributions: Dict[str, float] = field(default_factory=dict)
    slot_start: Optional[datetime] = None
    slot_end: Optional[datetime] = None
    van_capable: bool = False
    # How many of the two are approved to drive the van. The manual wants at
    # least one and prefers both, and a visit with two takes the van outright,
    # so the count matters rather than just whether anybody can.
    van_trained_count: int = 0
    vehicle: Optional[str] = None
    vehicle_reason: str = ""
    out_of_hours: bool = False
    clinician_verified: bool = True
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "clinician_id": self.clinician_id,
            "clinician": self.clinician_name,
            "tech_id": self.tech_id,
            "tech": self.tech_name,
            "score": round(self.score, 3),
            "components": {k: round(v, 3) for k, v in self.components.items()},
            "contributions": {k: round(v, 4) for k, v in self.contributions.items()},
            "slot": (self.slot_start.strftime("%a %-I:%M %p")
                     if self.slot_start else None),
            "slot_start": self.slot_start.isoformat() if self.slot_start else None,
            "van_capable": self.van_capable,
            "van_trained_count": self.van_trained_count,
            "vehicle": self.vehicle,
            "vehicle_reason": self.vehicle_reason,
            "out_of_hours": self.out_of_hours,
            "clinician_verified": self.clinician_verified,
            "notes": list(self.notes),
        }


def clinicians_for(visit, matrix, ids: Sequence[str],
                   roster=None) -> Tuple[List[str], bool]:
    """Who may run this visit's assessments, and whether the chart covered it.

    Returns ``(ids, verified)``. ``verified`` is False when the chart lists no
    requirements for the visit, which means the answer is "the chart does not
    say" rather than "anyone will do".

    Two separate questions, both from the manual, and passing one is not
    passing the other:

    * **Can they run the assessments?** The reliability chart answers that,
      assessment by assessment.
    * **Can they run the visit at all?** The chart's first column is headed
      "Visits Can Do Solo" and prints an age range beside some names and
      nothing beside others. Ramiro is reliable in Bayley 9-12m and still has
      no range, so he is a tech on a 9m visit and never its clinician.

    The roster is optional so existing callers keep working; when it is given,
    the second question is asked too.
    """
    required = matrix.required_for(visit.protocol, visit.checkpoint)
    if required:
        ids = [c for c in ids if all(matrix.is_reliable(c, a) for a in required)]
    if roster is not None:
        by_id = roster.by_id()
        ids = [c for c in ids
               if c in by_id
               and roster.can_be_clinician_for(by_id[c], visit.checkpoint)]
    return list(ids), bool(required)


def _combine(clin_components, tech_components, weights) -> Tuple[dict, dict, float]:
    """Merge two candidates' criteria into the pair's, then score once."""
    combined = {}
    for key in ("phi", "omega", "p"):
        combined[key] = max(getattr(clin_components, key),
                            getattr(tech_components, key))
    combined["psi"] = (getattr(clin_components, "psi")
                       + getattr(tech_components, "psi")) / 2.0
    contributions = {k: combined[k] * getattr(weights, k) for k in combined}
    return combined, contributions, sum(contributions.values())


def _common_slot(state, visit, a: str, b: str, now: datetime,
                 duration_hours: float,
                 day_start: float = 9.0, day_end: float = 17.0,
                 allow_friday: bool = False):
    """Earliest slot in the visit window where BOTH are demonstrably free.

    Uses the same three-state evidence rule as everything else, so a slot only
    counts when both calendars actually say clear. "No evidence" fails here
    exactly as it fails a single-coordinator check -- pairing two unknowns does
    not add up to a known.
    """
    step = timedelta(minutes=SLOT_STEP_MINUTES)
    length = timedelta(hours=duration_hours)
    cursor = visit.window_start
    limit = visit.window_end

    # Whichever day this picks has to be legal for both people. The gates run
    # against each candidate's own provisional slot; this function chooses a
    # different one, so every rule that depends on *which day* it is has to be
    # honoured here too or it is silently skipped. Fridays were already
    # handled that way. In-lab days and university holidays were not, so a
    # pair could be offered on somebody's lab day or on Thanksgiving.
    from .resources import LabResources
    _res = LabResources.load()
    lab_days = set()
    for cid in (a, b):
        coord = (state.coordinators or {}).get(cid)
        day = getattr(coord, "in_lab_day", None)
        if day is not None:
            lab_days.add(day)

    while cursor + length <= limit:
        workday = cursor.weekday() < 5 and (allow_friday or cursor.weekday() != 4)
        if workday and cursor.weekday() in lab_days:
            workday = False              # one of them is in the lab that day
        if workday:
            shut = _res.closed_on(cursor)
            if shut and "holiday" in shut:
                workday = False          # the manual allows no exceptions
        if workday:
            hour = cursor.hour + cursor.minute / 60.0
            finish = hour + duration_hours
            if hour >= day_start and finish <= day_end:
                end = cursor + length
                if (evidence_state(a, state, cursor, end, now) == EVIDENCE_CLEAR
                        and evidence_state(b, state, cursor, end, now)
                        == EVIDENCE_CLEAR):
                    return cursor, end
        cursor += step
    return None, None


def rank_pairs(
    visit,
    state,
    weights,
    matrix,
    survivors,
    now: datetime,
    duration_hours: Optional[float] = None,
    roster=None,
    limit: int = 12,
    allow_friday: bool = False,
    tech_only=(),
) -> Tuple[List[Pair], List[str]]:
    """Every workable clinician/tech pairing, best first.

    ``survivors`` are the candidates that already passed the Layer 1 gates, so
    this never has to re-litigate eligibility -- it only has to decide roles,
    find a slot both can make, and combine the scores.

    ``tech_only`` are people the assessment gate turned down. That gate is
    about the clinician's seat: the manual requires the *clinician* to be able
    to administer everything the visit needs and asks nothing of the tech. So
    they can fill the second seat, and never the first -- ``clinicians_for``
    is asked only about the survivors below.
    """
    by_id = {c.coordinator_id: c for c in survivors}
    tech_extra = [c for c in tech_only if c.coordinator_id not in by_id]
    by_id.update({c.coordinator_id: c for c in tech_extra})
    # Only survivors are considered for the clinician seat.
    ids = [c.coordinator_id for c in survivors]
    problems: List[str] = []

    # The lab's physical limits, and the family this visit belongs to. Both
    # are needed to say which vehicle the pair should take: the van wants a
    # trained driver, and some homes it cannot reach at all.
    from .resources import LabResources
    res = LabResources.load()
    family = (state.families or {}).get(visit.family_id)

    clinician_ids, verified = clinicians_for(visit, matrix, ids, roster=roster)
    if not clinician_ids:
        # Two different ways to have nobody, and a scheduler needs to know
        # which: an assessment gap is closed by training, a missing solo range
        # is closed by asking whoever keeps the chart.
        signed_off, _ = clinicians_for(visit, matrix, ids)
        if signed_off and roster is not None:
            problems.append(
                f"{len(signed_off)} eligible person(s) are signed off on this "
                f"visit's assessments, but none of them can run a "
                f"{visit.checkpoint} visit on their own. The manual's "
                f"'Visits Can Do Solo' column decides that, not the "
                f"assessment list.")
        else:
            problems.append(
                "Nobody eligible is signed off on every assessment this visit "
                "needs, so it cannot be staffed as the manual requires.")
        return [], problems
    if not verified:
        problems.append(
            "The reliability chart lists no assessments for this visit, so which "
            "of these people counts as its clinician is unverified.")

    tech_ids = ids + [c.coordinator_id for c in tech_extra]
    tech_ok = set(tech_ids)
    if roster is not None:
        entries = roster.by_id()
        tech_ok = {c for c in tech_ids
                   if c not in entries or entries[c].can_tech}
    if not tech_ok:
        problems.append("Nobody eligible is marked as able to run the tech side.")
        return [], problems

    duration = duration_hours or getattr(visit, "duration_hours", 2.0)
    pairs: List[Pair] = []
    for clinician in clinician_ids:
        for tech in tech_ok:
            if tech == clinician:
                continue
            start, end = _common_slot(state, visit, clinician, tech, now, duration,
                                      allow_friday=allow_friday)
            if start is None:
                continue
            combined, contributions, score = _combine(
                by_id[clinician].components, by_id[tech].components, weights)
            coord_c = state.coordinators.get(clinician)
            coord_t = state.coordinators.get(tech)
            trained = sum(
                1 for c in (coord_c, coord_t)
                if getattr(c, "van_trained", False))
            vehicle, vehicle_reason = res.vehicle_for(
                drive_minutes=getattr(family, "drive_time_minutes", 0.0) or 0.0,
                van_trained_on_visit=trained,
                van_inaccessible=bool(getattr(family, "van_inaccessible", False)),
            )
            pairs.append(Pair(
                clinician_id=clinician,
                clinician_name=by_id[clinician].coordinator_name,
                tech_id=tech,
                tech_name=by_id[tech].coordinator_name,
                score=score,
                components=combined,
                contributions=contributions,
                slot_start=start,
                slot_end=end,
                van_capable=bool(getattr(coord_c, "van_trained", False)
                                 or getattr(coord_t, "van_trained", False)),
                van_trained_count=trained,
                vehicle=vehicle,
                vehicle_reason=vehicle_reason,
                out_of_hours=bool(start and end and res.is_out_of_hours(start, end)),
                clinician_verified=verified,
            ))

    if not verified:
        # With no chart entry there is nothing to tell the two roles apart, so
        # offering both orderings of the same two people is false precision.
        seen = set()
        deduped = []
        for pair in pairs:
            key = frozenset((pair.clinician_id, pair.tech_id))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(pair)
        pairs = deduped

    pairs.sort(key=lambda p: (-p.score, p.slot_start or datetime.max,
                              p.clinician_id, p.tech_id))
    if not pairs:
        problems.append(
            "No clinician and tech are free at the same time inside this "
            "visit's window.")
    return pairs[:limit], problems
