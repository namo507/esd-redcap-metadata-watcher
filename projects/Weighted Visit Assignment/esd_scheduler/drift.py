"""Weekly drift detection.

A note on Gini, because it is the metric everyone asks for first: it is badly
biased and very high variance below about ten units, and ESD has four to eight
coordinators. Reported alone it will bounce week to week and mean nothing. So
the headline fairness numbers here are the coefficient of variation and the
load-imbalance ratio, with Gini reported alongside and a bootstrap interval
attached so nobody reads a swing from 0.11 to 0.19 as a real change.

The permutation test answers the question that actually matters when the numbers
look uneven: is the *algorithm* unfair, or are the *constraints* unfair? Usually
it is the constraints, and that is a different conversation with the PI.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .config import EngineConfig
from .store import AuditStore


# ---------------------------------------------------------------------------
# Inequality measures
# ---------------------------------------------------------------------------


def coefficient_of_variation(values: Sequence[float]) -> float:
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return 0.0
    mean = statistics.fmean(vals)
    if mean == 0:
        return 0.0
    return statistics.pstdev(vals) / mean


def gini(values: Sequence[float]) -> float:
    vals = sorted(v for v in values if v is not None and v >= 0)
    n = len(vals)
    if n == 0 or sum(vals) == 0:
        return 0.0
    cumulative = sum((i + 1) * v for i, v in enumerate(vals))
    return (2 * cumulative) / (n * sum(vals)) - (n + 1) / n


def gini_bootstrap_ci(
    values: Sequence[float], iterations: int = 2000, seed: int = 7, alpha: float = 0.05
) -> Tuple[float, float, float]:
    """(point estimate, lower, upper). Wide intervals here are the honest answer."""
    vals = [v for v in values if v is not None]
    point = gini(vals)
    if len(vals) < 3:
        return point, 0.0, 1.0
    rng = random.Random(seed)
    draws = []
    for _ in range(iterations):
        sample = [rng.choice(vals) for _ in vals]
        draws.append(gini(sample))
    draws.sort()
    lo = draws[int(alpha / 2 * len(draws))]
    hi = draws[min(len(draws) - 1, int((1 - alpha / 2) * len(draws)))]
    return point, lo, hi


def imbalance_ratio(values: Sequence[float]) -> float:
    """max / mean. Reads directly as "the busiest person carries 1.6x the average"."""
    vals = [v for v in values if v is not None]
    if not vals:
        return 0.0
    mean = statistics.fmean(vals)
    if mean == 0:
        return 0.0
    return max(vals) / mean


def population_stability_index(
    baseline: Sequence[float], current: Sequence[float], bins: int = 10
) -> float:
    """PSI between two score distributions. >0.10 investigate, >0.25 act."""
    base = [v for v in baseline if v is not None]
    curr = [v for v in current if v is not None]
    if len(base) < 2 or len(curr) < 2:
        return 0.0
    lo, hi = 0.0, 1.0
    width = (hi - lo) / bins
    total = 0.0
    for i in range(bins):
        left = lo + i * width
        right = left + width
        b = sum(1 for v in base if left <= v < right or (i == bins - 1 and v == right))
        c = sum(1 for v in curr if left <= v < right or (i == bins - 1 and v == right))
        pb = max(b / len(base), 1e-4)
        pc = max(c / len(curr), 1e-4)
        total += (pc - pb) * math.log(pc / pb)
    return total


def permutation_cv_test(
    observed_loads: Dict[str, float],
    feasible_sets: Sequence[Sequence[str]],
    iterations: int = 1000,
    seed: int = 11,
) -> float:
    """p-value for "the observed spread is worse than random feasible assignment".

    Randomly assigns each visit to one of the coordinators who was actually
    feasible for it, then compares the coefficient of variation. A high p-value
    means the imbalance is coming from who was eligible, not from the scoring.
    """
    if not feasible_sets:
        return 1.0
    rng = random.Random(seed)
    observed = coefficient_of_variation(list(observed_loads.values()))
    worse = 0
    ids = list(observed_loads)
    for _ in range(iterations):
        loads = {cid: 0.0 for cid in ids}
        for pool in feasible_sets:
            usable = [c for c in pool if c in loads]
            if usable:
                loads[rng.choice(usable)] += 1
        if coefficient_of_variation(list(loads.values())) >= observed:
            worse += 1
    return worse / iterations


# ---------------------------------------------------------------------------
# Weekly report object
# ---------------------------------------------------------------------------


@dataclass
class FairnessRow:
    coordinator_id: str
    coordinator_name: str = ""
    visits: int = 0
    burden_hours: float = 0.0
    travel_minutes: float = 0.0
    capacity_hours: float = 0.0
    utilization: float = 0.0


@dataclass
class DriftReport:
    period_start: datetime
    period_end: datetime
    n_runs: int = 0
    n_assigned: int = 0
    n_unfilled: int = 0

    fairness: List[FairnessRow] = field(default_factory=list)
    cv_visits: float = 0.0
    cv_utilization: float = 0.0
    cv_travel: float = 0.0
    gini_utilization: Tuple[float, float, float] = (0.0, 0.0, 1.0)
    imbalance: float = 0.0
    permutation_p: float = 1.0

    review_band_rate: float = 0.0
    tie_rate: float = 0.0
    override_rate: float = 0.0        # human overrode the ranking
    system_veto_rate: float = 0.0     # engine itself declined rank 1
    override_by_class: Dict[str, int] = field(default_factory=dict)
    override_by_code: Dict[str, int] = field(default_factory=dict)
    top1_acceptance: float = 0.0
    top3_hit_rate: float = 0.0

    psi_final_score: float = 0.0
    mean_top1: float = 0.0
    mean_gap: float = 0.0
    boundary_saturation_rate: float = 0.0
    low_stability_rate: float = 0.0

    cold_start_share: float = 0.0
    cold_start_capacity_share: float = 0.0
    pool_starvation_rate: float = 0.0

    calendar_success_rate: float = 1.0
    median_cache_age_s: Optional[float] = None
    provisional_rate: float = 0.0
    write_time_conflicts: int = 0

    surprise_counts: Dict[str, int] = field(default_factory=dict)
    rag: Dict[str, str] = field(default_factory=dict)


def _rag(value: float, amber: float, red: float, higher_is_worse: bool = True) -> str:
    if higher_is_worse:
        if value >= red:
            return "RED"
        if value >= amber:
            return "AMBER"
        return "GREEN"
    if value <= red:
        return "RED"
    if value <= amber:
        return "AMBER"
    return "GREEN"


def weekly_drift(
    store: AuditStore,
    cfg: EngineConfig,
    period_start: datetime,
    period_end: datetime,
    baseline_start: Optional[datetime] = None,
    baseline_end: Optional[datetime] = None,
) -> DriftReport:
    runs = store.runs_between(period_start, period_end)
    run_ids = [r["run_id"] for r in runs]
    cands = store.candidates_for(run_ids)
    outcomes = {o["run_id"]: o for o in store.outcomes_for(run_ids)}

    rep = DriftReport(period_start=period_start, period_end=period_end, n_runs=len(runs))

    by_run: Dict[str, List] = defaultdict(list)
    for c in cands:
        by_run[c["run_id"]].append(c)

    # --- fairness ----------------------------------------------------------
    loads: Dict[str, FairnessRow] = {}
    feasible_sets: List[List[str]] = []
    for run in runs:
        rows = by_run.get(run["run_id"], [])
        feasible_sets.append([r["coordinator_id"] for r in rows if r["l1_pass"]])
        # Seed every coordinator who was eligible at least once, so someone who
        # received zero visits shows up as a zero row. A missing row reads as
        # "not on the team"; a zero row is the fairness signal.
        for r in rows:
            if r["l1_pass"]:
                row = loads.setdefault(
                    r["coordinator_id"],
                    FairnessRow(
                        coordinator_id=r["coordinator_id"],
                        coordinator_name=r["coordinator_name"] or r["coordinator_id"],
                    ),
                )
                row.capacity_hours = r["capacity_hours"] or row.capacity_hours
        outcome = outcomes.get(run["run_id"])
        if not outcome or not outcome["assigned_coordinator_id"]:
            rep.n_unfilled += 1
            continue
        rep.n_assigned += 1
        cid = outcome["assigned_coordinator_id"]
        chosen = next((r for r in rows if r["coordinator_id"] == cid), None)
        row = loads.setdefault(
            cid,
            FairnessRow(
                coordinator_id=cid,
                coordinator_name=(chosen["coordinator_name"] if chosen else "") or cid,
            ),
        )
        row.visits += 1
        if chosen:
            row.travel_minutes += chosen["travel_minutes"] or 0.0
            row.burden_hours += run["visit_duration_hr"] or 0.0
            row.capacity_hours = chosen["capacity_hours"] or row.capacity_hours
    for row in loads.values():
        row.utilization = (
            row.burden_hours / row.capacity_hours if row.capacity_hours else 0.0
        )
    rep.fairness = sorted(loads.values(), key=lambda r: r.coordinator_id)

    rep.cv_visits = coefficient_of_variation([r.visits for r in rep.fairness])
    rep.cv_utilization = coefficient_of_variation([r.utilization for r in rep.fairness])
    rep.cv_travel = coefficient_of_variation([r.travel_minutes for r in rep.fairness])
    rep.gini_utilization = gini_bootstrap_ci([r.utilization for r in rep.fairness])
    rep.imbalance = imbalance_ratio([r.utilization for r in rep.fairness])
    rep.permutation_p = permutation_cv_test(
        {r.coordinator_id: float(r.visits) for r in rep.fairness}, feasible_sets
    )

    # --- decision quality --------------------------------------------------
    n = max(1, len(runs))
    review_band = sum(
        1 for run in runs if any(r["review_band_flag"] and r["rank_position"] == 1
                                 for r in by_run.get(run["run_id"], []))
    )
    ties = sum(
        1 for run in runs if any(r["tie_break_applied"] for r in by_run.get(run["run_id"], []))
    )
    rep.review_band_rate = review_band / n
    rep.tie_rate = ties / n

    decided = [o for o in outcomes.values() if o["assigned_coordinator_id"]]
    if decided:
        rep.override_rate = sum(1 for o in decided if o["was_override"]) / len(decided)
        rep.system_veto_rate = sum(
            1 for o in decided if o["override_reason_class"] == "system"
        ) / len(decided)
        rep.top1_acceptance = sum(1 for o in decided if o["assigned_rank"] == 1) / len(decided)
        rep.top3_hit_rate = sum(
            1 for o in decided if (o["assigned_rank"] or 99) <= cfg.top_k
        ) / len(decided)
        rep.override_by_class = dict(
            Counter(o["override_reason_class"] for o in decided if o["was_override"])
        )
        rep.override_by_code = dict(
            Counter(o["override_reason_code"] for o in decided if o["was_override"])
        )
        rep.provisional_rate = sum(1 for o in decided if o["is_provisional"]) / len(decided)
        rep.write_time_conflicts = sum(1 for o in outcomes.values() if o["write_time_conflict"])

    tops = [
        r["final_score"]
        for r in cands
        if r["rank_position"] == 1 and r["l1_pass"] and r["final_score"] is not None
    ]
    gaps = [
        r["gap_to_next"]
        for r in cands
        if r["rank_position"] == 1 and r["gap_to_next"] is not None
    ]
    rep.mean_top1 = statistics.fmean(tops) if tops else 0.0
    rep.mean_gap = statistics.fmean(gaps) if gaps else 0.0

    feasible_scores = [r["final_score"] for r in cands if r["l1_pass"]]
    # Share of *decisions* in which at least one criterion was inert: every
    # feasible candidate pinned to the same boundary value, so that criterion
    # contributed nothing to the ranking. Counting individual candidates at a
    # boundary instead would flag every coordinator who has simply never met the
    # family, which is normal and correct.
    inert_runs = 0
    for run in runs:
        pool = [r for r in by_run.get(run["run_id"], []) if r["l1_pass"]]
        if len(pool) < 2:
            continue
        for column in ("phi_continuity", "psi_burden_relief", "omega_preference"):
            values = {r[column] for r in pool}
            if len(values) == 1 and values.pop() in (0.0, 1.0):
                inert_runs += 1
                break
    rep.boundary_saturation_rate = inert_runs / n
    stabilities = [
        r["selection_stability"]
        for r in cands
        if r["rank_position"] == 1 and r["selection_stability"] is not None
    ]
    rep.low_stability_rate = (
        sum(1 for s in stabilities if s < 0.6) / len(stabilities) if stabilities else 0.0
    )

    cold = [r for r in cands if r["l1_pass"] and r["is_cold_start"]]
    assigned_cold = sum(
        1
        for o in decided
        for r in by_run.get(o["run_id"], [])
        if r["coordinator_id"] == o["assigned_coordinator_id"] and r["is_cold_start"]
    )
    rep.cold_start_share = assigned_cold / max(1, len(decided))
    cold_capacity = sum(r["capacity_hours"] or 0.0 for r in cold)
    all_capacity = sum(r["capacity_hours"] or 0.0 for r in cands if r["l1_pass"])
    rep.cold_start_capacity_share = cold_capacity / all_capacity if all_capacity else 0.0

    rep.pool_starvation_rate = sum(1 for run in runs if run["pool_starvation"]) / n

    # --- distribution shift -------------------------------------------------
    if baseline_start and baseline_end:
        base_runs = store.runs_between(baseline_start, baseline_end)
        base_cands = store.candidates_for([r["run_id"] for r in base_runs])
        base_scores = [r["final_score"] for r in base_cands if r["l1_pass"]]
        rep.psi_final_score = population_stability_index(base_scores, feasible_scores)

    # --- calendar SLO -------------------------------------------------------
    syncs = store.query(
        "SELECT * FROM calendar_sync_log WHERE started_at >= ? AND started_at < ?",
        (period_start.isoformat(timespec="seconds"), period_end.isoformat(timespec="seconds")),
    )
    if syncs:
        rep.calendar_success_rate = sum(1 for s in syncs if s["success"]) / len(syncs)
    ages = [r["calendar_cache_age_s"] for r in cands if r["calendar_cache_age_s"] is not None]
    rep.median_cache_age_s = statistics.median(ages) if ages else None

    # --- surprises ----------------------------------------------------------
    counter: Counter = Counter()
    for run in runs:
        import json as _json

        for code in _json.loads(run["surprise_codes"] or "[]"):
            counter[code.split(":")[0]] += 1
    rep.surprise_counts = dict(counter)

    # --- RAG ----------------------------------------------------------------
    rep.rag = {
        "cv_utilization": _rag(rep.cv_utilization, cfg.cv_amber, cfg.cv_red),
        "psi_final_score": _rag(rep.psi_final_score, cfg.psi_investigate, cfg.psi_act),
        "top3_hit_rate": _rag(
            rep.top3_hit_rate, cfg.top3_hit_rate_target, cfg.top3_hit_rate_target - 0.15,
            higher_is_worse=False,
        ),
        "calendar_success_rate": _rag(
            rep.calendar_success_rate, 0.98, 0.90, higher_is_worse=False
        ),
        "pool_starvation_rate": _rag(rep.pool_starvation_rate, 0.10, 0.25),
    }
    return rep
