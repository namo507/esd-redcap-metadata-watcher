"""Tests for staffing a visit with a clinician and a tech.

The manual's rule is that every visit needs both, and that the clinician must
be reliable in every assessment the visit age requires. The properties worth
pinning are the ones that would let a pair through that the lab would not:
a clinician who is not signed off, the same person filling both seats, a slot
only one of them can make, and a Friday.

Run:  python3 tests/test_pairing.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from esd_scheduler.config import load_config  # noqa: E402
from esd_scheduler.constraints import ReliabilityMatrix  # noqa: E402
from esd_scheduler.demo import build_lab  # noqa: E402
from esd_scheduler.engine import score_visit  # noqa: E402
from esd_scheduler.pairing import clinicians_for, rank_pairs  # noqa: E402
from esd_scheduler.roster import Roster  # noqa: E402

NOW = datetime(2026, 8, 17, 9, 0)
CFG = load_config()


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def _pairs_for(index=0, protocol=None):
    state, visits = build_lab(NOW)
    matrix = ReliabilityMatrix.load()
    roster = Roster.load()
    chosen = [v for v in visits if protocol is None or v.protocol == protocol]
    visit = chosen[index]
    pool = score_visit(visit, state, CFG, NOW)
    pairs, problems = rank_pairs(visit, state, CFG.weights, matrix,
                                 pool.candidates, NOW, roster=roster)
    return visit, state, matrix, pairs, problems


def test_a_visit_is_staffed_by_two_different_people():
    _, _, _, pairs, _ = _pairs_for()
    expect(pairs, "no pairing was produced at all")
    for p in pairs:
        expect(p.clinician_id != p.tech_id,
               "the same person was put in both seats")


def test_the_clinician_is_signed_off_on_everything_the_visit_needs():
    """The manual's non-negotiable rule, checked on the pair rather than the
    individual: a tech does not need the certification, a clinician does."""
    visit, _, matrix, pairs, _ = _pairs_for(protocol="NANO")
    required = matrix.required_for(visit.protocol, visit.checkpoint)
    expect(required, f"{visit.checkpoint} lists no requirements to test against")
    for p in pairs:
        for assessment in required:
            expect(matrix.is_reliable(p.clinician_id, assessment),
                   f"{p.clinician_name} is not signed off on {assessment} "
                   f"but was offered as clinician for {visit.checkpoint}")


def test_a_tech_need_not_be_signed_off():
    """Otherwise the rule collapses into needing two clinicians."""
    visit, _, matrix, pairs, _ = _pairs_for(protocol="NANO")
    required = matrix.required_for(visit.protocol, visit.checkpoint)
    techs_without = [
        p for p in pairs
        if not all(matrix.is_reliable(p.tech_id, a) for a in required)
    ]
    expect(techs_without,
           "every offered tech was also a clinician, so the tech seat is not "
           "actually open to uncertified staff")


def test_no_pair_is_offered_a_friday():
    """Fridays are lab meeting days; the manual allows one only with approval."""
    for index in range(3):
        _, _, _, pairs, _ = _pairs_for(index)
        for p in pairs:
            expect(p.slot_start is None or p.slot_start.weekday() != 4,
                   f"a pair was offered {p.slot_start:%A %H:%M}")


def test_both_people_are_free_in_the_slot_offered():
    from esd_scheduler.constraints import EVIDENCE_CLEAR, evidence_state

    visit, state, _, pairs, _ = _pairs_for()
    for p in pairs[:5]:
        for who in (p.clinician_id, p.tech_id):
            verdict = evidence_state(who, state, p.slot_start, p.slot_end, NOW)
            expect(verdict == EVIDENCE_CLEAR,
                   f"{who} is {verdict} at the slot the pair was offered")


def test_an_unchartered_visit_reports_that_rather_than_guessing():
    """A visit the chart says nothing about is not a visit anyone may run."""
    state, visits = build_lab(NOW)
    matrix = ReliabilityMatrix.load()
    nico = next(v for v in visits if v.protocol == "NICO")
    ids = [c.coordinator_id for c in state.active_coordinators()]
    _, verified = clinicians_for(nico, matrix, ids)
    expect(not verified,
           "NICO is not in the manual, so its clinician cannot be verified")
    pool = score_visit(nico, state, CFG, NOW)
    pairs, problems = rank_pairs(nico, state, CFG.weights, matrix,
                                 pool.candidates, NOW)
    expect(any("unverified" in x for x in problems),
           f"the unverified clinician was not reported: {problems}")
    for p in pairs:
        expect(not p.clinician_verified, "an unverified pair claimed otherwise")


def test_mirrored_pairs_collapse_when_the_roles_cannot_be_told_apart():
    state, visits = build_lab(NOW)
    matrix = ReliabilityMatrix.load()
    nico = next(v for v in visits if v.protocol == "NICO")
    pool = score_visit(nico, state, CFG, NOW)
    pairs, _ = rank_pairs(nico, state, CFG.weights, matrix, pool.candidates, NOW)
    seen = set()
    for p in pairs:
        key = frozenset((p.clinician_id, p.tech_id))
        expect(key not in seen,
               "the same two people were offered in both orders on a visit "
               "where the chart cannot say who the clinician is")
        seen.add(key)


def test_burden_is_shared_and_family_facing_criteria_take_the_better_half():
    """The combination rule, checked rather than described."""
    visit, state, matrix, pairs, _ = _pairs_for()
    pool = score_visit(visit, state, CFG, NOW)
    by_id = {c.coordinator_id: c for c in pool.candidates}
    for p in pairs[:5]:
        a, b = by_id[p.clinician_id].components, by_id[p.tech_id].components
        for key in ("phi", "omega", "p"):
            expect(abs(p.components[key] - max(getattr(a, key), getattr(b, key))) < 1e-9,
                   f"{key} should be the better of the two")
        expect(abs(p.components["psi"]
                   - (getattr(a, "psi") + getattr(b, "psi")) / 2) < 1e-9,
               "burden should be shared, not taken from one of them")


def test_a_tech_is_not_held_to_the_clinicians_assessment_bar():
    """The manual puts the assessment requirement on one seat, not both.

    "Clinician must be able to reliably/independently admin all the
    assessments needed for that visit age" -- and it asks nothing of the tech.
    The assessment gate was dropping people from the candidate pool outright,
    and because pairing draws both seats from that pool, a tech had to hold
    every assessment the clinician did.

    Found on real prints: a 9m visit with a signed-off clinician free and a
    perfectly good tech free alongside her came back "no clinician and tech
    are free at the same time".
    """
    from esd_scheduler.constraints import check_candidate

    state, visits = build_lab(NOW)
    matrix = ReliabilityMatrix.load()
    visit = next((v for v in visits
                  if matrix.required_for(v.protocol, v.checkpoint)), None)
    expect(visit is not None,
           "no visit on the board requires an assessment, so the two seats "
           "cannot be told apart here")
    family = state.families[visit.family_id]
    required = matrix.required_for(visit.protocol, visit.checkpoint)

    short = next(
        (c for c in state.coordinators.values()
         if any(not matrix.is_reliable(c.coordinator_id, a) for a in required)),
        None)
    expect(short is not None,
           "everybody is signed off on everything, so the seats cannot differ")

    as_clinician = check_candidate(short, visit, family, state, NOW,
                                   matrix=matrix, seat="clinician")
    as_tech = check_candidate(short, visit, family, state, NOW,
                              matrix=matrix, seat="tech")
    expect(not as_clinician.passed and as_clinician.gate == "reliability",
           f"{short.name} should fail the clinician seat on assessments, got "
           f"{as_clinician.gate!r}")
    expect(as_tech.passed or as_tech.gate != "reliability",
           f"{short.name} was refused the tech seat for an assessment the "
           f"manual only asks of the clinician ({as_tech.reason})")


def test_the_slot_chooser_honours_a_lab_day_and_a_holiday():
    """It picks its own slot, so every day-rule has to be applied here.

    The gates run against each candidate's provisional slot; this function
    chooses a different one. Fridays were already handled that way, and the
    same reasoning was never applied to in-lab days or university holidays --
    so a pair could be offered on somebody's lab day, or on Thanksgiving.
    """
    from datetime import timedelta
    from esd_scheduler.pairing import _common_slot

    state, visits = build_lab(NOW)
    visit = next(iter(visits))
    people = [c for c in state.coordinators.values()][:2]
    expect(len(people) == 2, "need two coordinators to pair")
    a, b = people[0].coordinator_id, people[1].coordinator_id

    # Give one of them an in-lab day and clear both calendars, so the only
    # thing that can rule a day out is the rule under test.
    for cid in (a, b):
        snap = state.calendars.get(cid)
        if snap:
            snap.blocks = []
    lab_day = 1                                   # Tuesday
    state.coordinators[a].in_lab_day = lab_day
    state.coordinators[b].in_lab_day = None

    visit.window_start = datetime(2026, 8, 18, 9, 0)      # a Tuesday
    visit.window_end = datetime(2026, 8, 18, 17, 0)
    start, _ = _common_slot(state, visit, a, b, NOW, 2.0)
    expect(start is None,
           f"a slot was offered on {state.coordinators[a].name}'s in-lab day: "
           f"{start}")

    # Thanksgiving 2026, with nobody in the lab that day.
    state.coordinators[a].in_lab_day = None
    visit.window_start = datetime(2026, 11, 26, 9, 0)
    visit.window_end = datetime(2026, 11, 26, 17, 0)
    start, _ = _common_slot(state, visit, a, b, NOW, 2.0)
    expect(start is None,
           f"a slot was offered on a university holiday: {start}")

    # A plain Wednesday with nothing in the way still works, or the rules
    # above are just refusing everything.
    visit.window_start = datetime(2026, 8, 19, 9, 0)
    visit.window_end = datetime(2026, 8, 19, 17, 0)
    start, _ = _common_slot(state, visit, a, b, NOW, 2.0)
    expect(start is not None,
           "no slot on an ordinary open Wednesday with both calendars clear")



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
