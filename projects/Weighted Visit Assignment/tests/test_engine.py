"""Correctness anchors for the v3 engine.

The reference case is the one that matters: three coordinators, numbers small
enough to check on paper, and totals asserted to three decimals. If it fails,
everything downstream is suspect.

Run with:  python -m pytest tests -q      (or  python tests/test_engine.py)
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from esd_scheduler.config import EngineConfig, WeightVector
from esd_scheduler.demo import build_lab, reference_case
from esd_scheduler.drift import (
    coefficient_of_variation,
    gini,
    imbalance_ratio,
    population_stability_index,
)
from esd_scheduler.engine import commit_assignment, plan_week, score_visit
from esd_scheduler.calendarsync import MockProvider
from esd_scheduler.models import ComponentScores
from esd_scheduler.optimize import (
    Interval,
    dp_trigger_threshold,
    greedy_interval_schedule,
    weighted_interval_schedule,
)
from esd_scheduler.ranking import calibrate_epsilon, selection_stability
from esd_scheduler.scoring import (
    burden_relief_score,
    continuity_score,
    familiarity,
    freshness,
    preference_score,
    prospective_burden,
    ramped_capacity,
)
from esd_scheduler.sensitivity import ahp_weights, criticality, dematel, oat_sensitivity
from esd_scheduler.store import AuditStore

# A Monday at 09:00, so the three-day visit windows always contain working days.
NOW = datetime(2026, 8, 17, 9, 0)
CFG = EngineConfig()


def approx(a, b, tol=1e-3):
    assert abs(a - b) <= tol, f"{a!r} != {b!r} within {tol}"


# ---------------------------------------------------------------------------
# Scoring terms
# ---------------------------------------------------------------------------


def test_familiarity_is_monotone_and_saturating():
    values = [familiarity(k, CFG.kappa_visits) for k in range(0, 8)]
    assert values[0] == 0.0
    assert all(b > a for a, b in zip(values, values[1:]))
    approx(values[1], 0.3935)
    approx(values[3], 0.7769)
    # Saturating: the fifth visit adds far less than the first.
    assert (values[5] - values[4]) < (values[1] - values[0]) / 4


def test_freshness_undefined_is_zero_not_extreme():
    # The v2 cold-start bug: an undefined value must not read as an extreme one.
    assert freshness(None, CFG.tau_days) == 0.0
    approx(freshness(0.0, CFG.tau_days), 1.0)
    assert freshness(200.0, CFG.tau_days) < 0.08


def test_continuity_flips_with_sigma():
    plus = continuity_score(3, 5.0, +1, CFG)
    minus = continuity_score(3, 5.0, -1, CFG)
    approx(plus + minus, 1.0)
    assert plus > minus
    # A never-met coordinator is maximal for a family wanting a fresh face.
    approx(continuity_score(0, None, -1, CFG), 1.0)
    approx(continuity_score(0, None, +1, CFG), 0.0)


def test_new_hire_is_not_maximally_rested():
    """The whole point of the rewrite: an empty calendar plus an empty history
    must not produce a 1.0 on two criteria at once."""
    from esd_scheduler.models import Coordinator

    veteran = Coordinator("V", "V", capacity_hours_week=20.0, n_completed_visits=60)
    newbie = Coordinator("N", "N", capacity_hours_week=20.0, n_completed_visits=0)
    cap_v = ramped_capacity(veteran, CFG)
    cap_n = ramped_capacity(newbie, CFG)
    approx(cap_v, 20.0)
    approx(cap_n, 20.0 * (0 + 1) / (20 + 1))
    # Same zero committed hours, but the new hire saturates far sooner.
    b = prospective_burden(0.0, 2.0, 30.0, CFG)
    assert burden_relief_score(b, cap_n) < burden_relief_score(b, cap_v)
    assert continuity_score(0, None, +1, CFG) == 0.0


def test_missing_family_preference_is_neutral():
    from esd_scheduler.models import Coordinator, Family

    c = Coordinator("C", "C")
    silent = Family("F1", "NICO")
    approx(preference_score(c, silent, CFG), 0.5)
    named = Family("F2", "NICO", preferred_coordinators={"C"})
    approx(preference_score(c, named, CFG), 1.0)
    other = Family("F3", "NICO", preferred_coordinators={"X"})
    approx(preference_score(c, other, CFG), 0.35)
    avoid = Family("F4", "NICO", soft_avoid={"C"})
    approx(preference_score(c, avoid, CFG), 0.0)


def test_burden_merges_workload_and_travel_in_hours():
    # 10 h booked, a 2 h visit, 45 min round trip, gamma = 2
    b = prospective_burden(10.0, 2.0, 45.0, CFG)
    approx(b, 10.0 + 2.0 + 2.0 * 0.75)
    approx(burden_relief_score(b, 20.0), 1 - 13.5 / 20.0)
    # Over capacity clips at zero rather than going negative.
    approx(burden_relief_score(40.0, 20.0), 0.0)


# ---------------------------------------------------------------------------
# The reference case
# ---------------------------------------------------------------------------


def test_reference_case_totals():
    state, visit = reference_case(NOW)
    pool = score_visit(visit, state, CFG, NOW)
    scores = {c.coordinator_id: c.final_score for c in pool.candidates}
    assert set(scores) == {"A", "B", "C"}

    approx(scores["A"], 0.502)
    approx(scores["B"], 0.173)
    approx(scores["C"], 0.246)

    order = [c.coordinator_id for c in pool.candidates]
    assert order == ["A", "C", "B"], order

    # v2 ranked this same table C, A, B. The flip is the fix working: v2's
    # pool-relative workload term handed A a zero and C a free half point, and
    # its recency term rewarded B for having been idle.
    assert pool.candidates[0].components.p == 1.0
    approx(pool.candidates[0].components.psi, 0.0)  # A really is at capacity
    assert not pool.candidates[0].review_band_flag  # 0.256 gap is decisive


def test_every_component_stays_in_unit_interval():
    state, visits = build_lab(NOW)
    for visit in visits:
        pool = score_visit(visit, state, CFG, NOW)
        for cand in pool.candidates:
            c = cand.components
            for name in ("phi", "omega", "psi", "p"):
                v = getattr(c, name)
                assert 0.0 - 1e-9 <= v <= 1.0 + 1e-9, (name, v)
            assert 0.0 <= cand.final_score <= 1.0
            assert abs(sum(cand.contributions.values()) - cand.final_score) < 1e-9


def test_ranking_is_a_permutation_of_the_feasible_pool():
    state, visits = build_lab(NOW)
    for visit in visits:
        pool = score_visit(visit, state, CFG, NOW)
        ids = [c.coordinator_id for c in pool.candidates]
        assert len(ids) == len(set(ids))
        scores = [c.final_score for c in pool.candidates]
        assert scores == sorted(scores, reverse=True)
        rejected_ids = {c.coordinator_id for c in pool.rejected}
        assert not (set(ids) & rejected_ids)


def test_layer1_failures_are_never_rescued_by_score():
    state, visits = build_lab(NOW)
    visit = next(v for v in visits if v.family_id == "F5904")
    pool = score_visit(visit, state, CFG, NOW)
    assert "C04" not in {c.coordinator_id for c in pool.candidates}
    excluded = next(c for c in pool.rejected if c.coordinator_id == "C04")
    assert excluded.feasibility.fail_reason == "family_exclusion"
    assert excluded.final_score == 0.0


def test_adding_hours_never_raises_a_score():
    state, visit = reference_case(NOW)
    before = {c.coordinator_id: c.final_score for c in score_visit(visit, state, CFG, NOW).candidates}
    state.committed_hours["B"] += 4.0
    after = {c.coordinator_id: c.final_score for c in score_visit(visit, state, CFG, NOW).candidates}
    assert after["B"] <= before["B"] + 1e-12
    assert approx(after["A"], before["A"]) is None


def test_determinism():
    state1, visits1 = build_lab(NOW)
    state2, visits2 = build_lab(NOW)
    a = [score_visit(v, state1, CFG, NOW).candidates[0].final_score for v in visits1[:5]]
    b = [score_visit(v, state2, CFG, NOW).candidates[0].final_score for v in visits2[:5]]
    assert a == b


# ---------------------------------------------------------------------------
# Optimisation
# ---------------------------------------------------------------------------


def test_weighted_interval_dp_beats_greedy_on_the_classic_trap():
    base = datetime(2026, 8, 17, 8, 0)
    items = [
        Interval("a", base, base + timedelta(hours=3), 0.60),
        Interval("b", base, base + timedelta(hours=1), 0.40),
        Interval("c", base + timedelta(hours=1), base + timedelta(hours=2), 0.40),
        Interval("d", base + timedelta(hours=2), base + timedelta(hours=3), 0.40),
    ]
    chosen, total = weighted_interval_schedule(items)
    approx(total, 1.20)
    assert [c.key for c in chosen] == ["b", "c", "d"]
    _, greedy_total = greedy_interval_schedule(items)
    approx(greedy_total, 0.60)
    assert total > greedy_total


def test_dp_trigger_threshold_matches_the_derivation():
    # 8 hour day, 2.5 hour travel-inflated visits -> conflicts from the 3rd
    approx(dp_trigger_threshold(8.0, 2.5), 1 + math.sqrt(3.2))
    assert dp_trigger_threshold(8.0, 2.5) < 3.0


def test_mcmf_respects_capacity_and_beats_or_matches_greedy():
    state, visits = build_lab(NOW)
    greedy, optimal, report, _ = plan_week(visits, state, CFG, NOW)
    assert optimal.total_score >= greedy.total_score - 1e-9
    assert report.regret >= -1e-9
    capacity_used = {cid: len(v) for cid, v in optimal.per_coordinator().items()}
    for cid, count in capacity_used.items():
        c = state.coordinators[cid]
        limit = (
            CFG.onboarding_max_visits_week
            if c.n_completed_visits < CFG.n_min_visits
            else max(1, int(c.capacity_hours_week // 2.5))
        )
        assert count <= limit, (cid, count, limit)
    # No coordinator ends up double-booked after the DP repair pass.
    for cid, opts in optimal.per_coordinator().items():
        opts = sorted(opts, key=lambda o: o.slot_start)
        for a, b in zip(opts, opts[1:]):
            assert a.slot_end <= b.slot_start, (cid, a.visit_id, b.visit_id)


# ---------------------------------------------------------------------------
# Ranking, drift, sensitivity
# ---------------------------------------------------------------------------


def test_selection_stability_sums_to_one():
    comps = {
        "A": ComponentScores(phi=0.8, omega=0.5, psi=0.2, p=1.0),
        "B": ComponentScores(phi=0.5, omega=0.5, psi=0.9, p=0.0),
        "C": ComponentScores(phi=0.1, omega=0.5, psi=0.4, p=0.0),
    }
    stability = selection_stability(comps, CFG, samples=500)
    approx(sum(stability.values()), 1.0)
    assert stability["C"] < stability["A"]


def test_epsilon_calibration_returns_a_usable_band():
    state, visits = build_lab(NOW)
    decisions = []
    for v in visits:
        pool = score_visit(v, state, CFG, NOW)
        if len(pool.candidates) >= 2:
            decisions.append({c.coordinator_id: c.components for c in pool.candidates})
    eps, diagnostics = calibrate_epsilon(decisions, CFG, samples=200)
    assert 0.0 < eps <= 0.20
    assert len(diagnostics) == len(decisions)


def test_oat_and_criticality_run_and_report():
    state, visits = build_lab(NOW)
    decisions = [
        {c.coordinator_id: c.components for c in score_visit(v, state, CFG, NOW).candidates}
        for v in visits
    ]
    decisions = [d for d in decisions if len(d) >= 2]
    results = oat_sensitivity(decisions, CFG, 0.05)
    assert len(results) == 8  # four criteria, plus and minus
    assert all(0.0 <= r.reversal_rate <= 1.0 for r in results)
    crit = criticality(decisions, CFG)
    assert set(crit) == {"phi", "omega", "psi", "p"}


def test_ahp_consistency_ratio_on_a_perfect_matrix():
    # Perfectly consistent 3x3 -> CR = 0
    m = [[1, 2, 4], [0.5, 1, 2], [0.25, 0.5, 1]]
    result = ahp_weights(m, ["a", "b", "c"])
    approx(result.consistency_ratio, 0.0, tol=1e-6)
    assert result.acceptable
    approx(result.weights["a"], 4 / 7, tol=1e-6)


def test_dematel_flags_a_pure_effect_criterion():
    # 'recency' is driven by the other two and drives nothing.
    names = ["history", "workload", "recency"]
    z = [
        [0, 1, 4],
        [1, 0, 4],
        [0, 0, 0],
    ]
    res = dematel(z, names)
    assert res.relation["recency"] < 0
    assert res.relation["history"] > 0
    assert "recency" in res.redundant


def test_inequality_measures():
    approx(coefficient_of_variation([5, 5, 5, 5]), 0.0)
    approx(gini([1, 1, 1, 1]), 0.0)
    assert gini([0, 0, 0, 10]) > 0.6
    approx(imbalance_ratio([1, 1, 1, 5]), 5 / 2.0)
    approx(population_stability_index([0.5] * 50, [0.5] * 50), 0.0, tol=1e-6)
    assert population_stability_index([0.1] * 50, [0.9] * 50) > 0.25


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def test_full_cycle_writes_the_whole_pool_to_the_audit_log():
    state, visits = build_lab(NOW)
    provider = MockProvider(blocks=getattr(state, "demo_blocks", {}), clock=lambda: NOW)
    with tempfile.TemporaryDirectory() as tmp:
        store = AuditStore(os.path.join(tmp, "audit.db"))
        store.record_config(CFG)
        visit = visits[0]
        pool = score_visit(visit, state, CFG, NOW)
        run_id, chosen, notes = commit_assignment(
            pool, state, CFG, store, provider=provider, now=NOW
        )
        rows = store.query("SELECT * FROM candidate_score WHERE run_id = ?", (run_id,))
        # Every coordinator considered, not only the winner. This is what makes
        # the conditional-logit weight check possible later.
        assert len(rows) == len(state.coordinators)
        assert sum(r["l1_pass"] for r in rows) == len(pool.candidates)
        outcome = store.query("SELECT * FROM assignment_outcome WHERE run_id = ?", (run_id,))
        assert len(outcome) == 1
        if chosen:
            assert outcome[0]["assigned_coordinator_id"] == chosen.coordinator_id
        store.close()


def test_override_reason_is_forced_into_the_taxonomy():
    state, visits = build_lab(NOW)
    with tempfile.TemporaryDirectory() as tmp:
        store = AuditStore(os.path.join(tmp, "audit.db"))
        pool = score_visit(visits[0], state, CFG, NOW)
        run_id = store.record_pool(pool, CFG, "test")
        store.record_outcome(
            run_id, pool.candidates[1].coordinator_id, 2, human_override=True
        )
        row = store.query("SELECT * FROM assignment_outcome WHERE run_id = ?", (run_id,))[0]
        assert row["was_override"] == 1
        assert row["override_reason_code"] == "other"
        assert row["override_reason_class"] == "external"

        # A rank-2 assignment the engine made for itself is a system veto, not
        # a human override, and must not inflate the override rate.
        run_id2 = store.record_pool(pool, CFG, "test")
        store.record_outcome(run_id2, pool.candidates[1].coordinator_id, 2)
        row2 = store.query("SELECT * FROM assignment_outcome WHERE run_id = ?", (run_id2,))[0]
        assert row2["was_override"] == 0
        assert row2["override_reason_class"] == "system"
        store.close()


def test_weights_must_sum_to_one():
    try:
        WeightVector(0.5, 0.5, 0.5, 0.5).validate()
    except ValueError:
        pass
    else:
        raise AssertionError("expected a ValueError")


def test_perturbation_keeps_the_simplex():
    for name in ("phi", "omega", "psi", "p"):
        for delta in (0.05, -0.05, 0.2):
            w = CFG.weights.perturbed(name, delta)
            w.validate()
            approx(getattr(w, name), max(0.0, min(1.0, getattr(CFG.weights, name) + delta)))


def _travel_state(now):
    """Five coordinators; C03 has done all the long drives lately."""
    from esd_scheduler.models import CompletedVisit, Coordinator, LabState

    state = LabState()
    for i in range(1, 6):
        cid = f"C{i:02d}"
        state.coordinators[cid] = Coordinator(cid, cid, capacity_hours_week=20.0)
    # A month of trips: C03 took the four long ones, everyone else took a short one.
    plan = [("C03", 300.0)] * 4 + [("C01", 20.0), ("C02", 20.0),
                                   ("C04", 20.0), ("C05", 20.0)]
    for n, (cid, minutes) in enumerate(plan):
        state.history.append(
            CompletedVisit(
                visit_id=f"H{n}", family_id="F1", coordinator_id=cid,
                when=now - timedelta(days=3 + n), protocol="NICO",
                checkpoint="12mo", travel_minutes=minutes,
            )
        )
    return state


def _veto_for(state, cid, travel_minutes, now):
    from esd_scheduler.engine import fairness_violations
    from esd_scheduler.models import CandidateScore, FeasibilityResult

    cand = CandidateScore(
        coordinator_id=cid,
        coordinator_name=cid,
        feasibility=FeasibilityResult(coordinator_id=cid, passed=True),
        components=ComponentScores(travel_minutes=travel_minutes, utilization=0.3),
    )
    return fairness_violations(cid, cand, state, CFG, now)


def test_travel_cap_does_not_ratchet_someone_out_of_all_work():
    """Someone over their travel share must still be offered the SHORT trips.

    The first implementation vetoed them from everything, which starved one
    coordinator of all work for a week while the constraint looked correct.
    """
    state = _travel_state(NOW)
    assert "travel_share_cap" in _veto_for(state, "C03", 300.0, NOW)
    assert "travel_share_cap" not in _veto_for(state, "C03", 15.0, NOW)


def test_travel_cap_leaves_everyone_else_alone():
    state = _travel_state(NOW)
    for cid in ("C01", "C02", "C04", "C05"):
        assert "travel_share_cap" not in _veto_for(state, cid, 300.0, NOW)


def test_travel_cap_stays_silent_without_enough_evidence():
    """Two logged trips is not grounds for denying anyone work."""
    from esd_scheduler.models import CompletedVisit, Coordinator, LabState

    state = LabState()
    for i in range(1, 4):
        cid = f"C{i:02d}"
        state.coordinators[cid] = Coordinator(cid, cid, capacity_hours_week=20.0)
    for n in range(2):
        state.history.append(
            CompletedVisit(
                visit_id=f"H{n}", family_id="F1", coordinator_id="C01",
                when=NOW - timedelta(days=2), protocol="NICO",
                checkpoint="12mo", travel_minutes=400.0,
            )
        )
    assert "travel_share_cap" not in _veto_for(state, "C01", 400.0, NOW)


def test_travel_cap_still_fires_when_one_person_holds_all_the_driving():
    """A purely directional rule silently stops firing here: the hog's share is
    already ~1.0, so no further trip can push it higher."""
    from esd_scheduler.models import CompletedVisit, Coordinator, LabState

    state = LabState()
    for i in range(1, 4):
        cid = f"C{i:02d}"
        state.coordinators[cid] = Coordinator(cid, cid, capacity_hours_week=20.0)
    # Eight trips across three people, but C01 drove almost all the distance.
    plan = [("C01", 200.0)] * 6 + [("C02", 3.0), ("C03", 3.0)]
    for n, (cid, minutes) in enumerate(plan):
        state.history.append(
            CompletedVisit(
                visit_id=f"H{n}", family_id="F1", coordinator_id=cid,
                when=NOW - timedelta(days=2 + n), protocol="NICO",
                checkpoint="12mo", travel_minutes=minutes,
            )
        )
    assert "travel_share_cap" in _veto_for(state, "C01", 400.0, NOW)
    assert "travel_share_cap" not in _veto_for(state, "C01", 30.0, NOW)


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