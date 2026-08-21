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
