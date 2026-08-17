"""Layer 3: ranking, the calibrated review band, ties and surprise detection.

The v2 review band was a hardcoded 0.05. That number asserts something testable:
that a gap below it is inside the noise of the weights themselves. So test it.
``calibrate_epsilon`` samples weight vectors around the elicited ones and picks
the smallest band whose enclosed decisions flip no more than
``reversal_tolerance`` of the time. The band becomes a measured quantity instead
of a guess, and the same machinery reports a per-decision selection stability so
genuinely ambiguous cases route themselves to a human.
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from .config import EngineConfig, WeightVector
from .models import CandidateScore, ComponentScores, RankedPool, Visit

CRITERIA = ("phi", "omega", "psi", "p")


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def rank(candidates: List[CandidateScore], cfg: EngineConfig) -> List[CandidateScore]:
    """Sort descending by score, then fill in rank, gap and shortlist flags.

    Ties in the raw score are ordered by coordinator id first so the sort is
    deterministic; the *deliberate* tie-break happens in ``break_tie``.
    """
    ordered = sorted(
        candidates, key=lambda c: (-c.final_score, c.coordinator_id)
    )
    for i, cand in enumerate(ordered):
        cand.rank_position = i + 1
        cand.gap_to_next = (
            ordered[i].final_score - ordered[i + 1].final_score
            if i + 1 < len(ordered)
            else None
        )
        cand.in_shortlist = i < cfg.top_k
        cand.review_band_flag = False
    if len(ordered) >= 2:
        gap = ordered[0].final_score - ordered[1].final_score
        if gap < cfg.epsilon_review_band:
            for cand in ordered[:2]:
                cand.review_band_flag = True
    return ordered


def break_tie(
    ordered: List[CandidateScore],
    sigma: int,
    cfg: EngineConfig,
    seed: Optional[int] = None,
    exact_tie_tol: float = 1e-9,
) -> List[CandidateScore]:
    """Resolve an exact tie at the top.

    Order of rules:
      1. protocol continuity (the PI's rater-consistency claim wins first)
      2. family-history direction, per sigma
      3. uniform random, with the seed logged

    Rule 3 is not a cop-out. Inside a true tie the alternatives are indifferent
    by the lab's own stated criteria, so randomising is defensible on equipoise
    grounds and it yields unconfounded data on whether continuity actually moves
    outcomes. Log the seed and the tie becomes a free experiment.
    """
    if len(ordered) < 2:
        return ordered
    top = ordered[0].final_score
    tied = [c for c in ordered if abs(c.final_score - top) <= exact_tie_tol]
    if len(tied) < 2:
        return ordered

    rule = "protocol_continuity"
    best = [c for c in tied if c.components.p >= max(t.components.p for t in tied)]
    if len(best) > 1:
        rule = "family_history"
        if sigma >= 0:
            target = max(c.components.k_prior_visits for c in best)
        else:
            target = min(c.components.k_prior_visits for c in best)
        best = [c for c in best if c.components.k_prior_visits == target]
    if len(best) > 1:
        rule = "random"
        used_seed = cfg.rng_seed if seed is None else seed
        rng = random.Random(used_seed)
        winner = rng.choice(sorted(best, key=lambda c: c.coordinator_id))
        for c in tied:
            c.tie_break_seed = used_seed
        best = [winner]

    winner = best[0]
    winner.tie_break_applied = True
    winner.tie_break_rule = rule
    for c in tied:
        if c is not winner:
            c.tie_break_applied = True
            c.tie_break_rule = rule
    reordered = [winner] + [c for c in ordered if c is not winner]
    for i, cand in enumerate(reordered):
        cand.rank_position = i + 1
        cand.in_shortlist = i < cfg.top_k
    return reordered


# ---------------------------------------------------------------------------
# Weight-uncertainty Monte Carlo
# ---------------------------------------------------------------------------


def _dirichlet(mean: Sequence[float], concentration: float, rng: random.Random):
    """Dirichlet draw centred on ``mean``, using gamma variates (stdlib only)."""
    draws = []
    for m in mean:
        alpha = max(1e-6, m * concentration)
        draws.append(rng.gammavariate(alpha, 1.0))
    total = sum(draws) or 1.0
    return [d / total for d in draws]


def selection_stability(
    components: Dict[str, ComponentScores], cfg: EngineConfig, samples: Optional[int] = None
) -> Dict[str, float]:
    """P(each coordinator is top-1) under weight uncertainty.

    A decision where the leader wins 55% of the simplex mass is not the same
    decision as one where they win 99%, even if both have the same point gap.
    Low stability is the honest trigger for human review.
    """
    if not components:
        return {}
    ids = sorted(components)
    if len(ids) == 1:
        return {ids[0]: 1.0}
    rng = random.Random(cfg.rng_seed)
    n = samples or cfg.mc_samples
    mean = [getattr(cfg.weights, c) for c in CRITERIA]
    wins = {cid: 0 for cid in ids}
    for _ in range(n):
        w = _dirichlet(mean, cfg.mc_concentration, rng)
        best_id, best_val = None, float("-inf")
        for cid in ids:
            comp = components[cid]
            val = sum(w[j] * getattr(comp, CRITERIA[j]) for j in range(len(CRITERIA)))
            if val > best_val:
                best_id, best_val = cid, val
        wins[best_id] += 1
    return {cid: wins[cid] / n for cid in ids}


def calibrate_epsilon(
    decisions: Sequence[Dict[str, ComponentScores]],
    cfg: EngineConfig,
    grid: Sequence[float] = (0.02, 0.03, 0.04, 0.05, 0.07, 0.10, 0.15, 0.20),
    samples: int = 400,
) -> Tuple[float, List[Dict[str, float]]]:
    """Smallest band epsilon such that decisions *outside* it flip rarely.

    For each historical decision we compute the point ranking and the Monte
    Carlo probability that the point leader is not the leader. A band is
    acceptable when, among decisions whose top-two gap exceeds it, the mean flip
    probability is at or under ``cfg.reversal_tolerance``. Anything inside the
    band is what we are already sending to a human, so it does not have to be
    stable.
    """
    diagnostics: List[Dict[str, float]] = []
    for comps in decisions:
        if len(comps) < 2:
            continue
        totals = {}
        for cid, comp in comps.items():
            totals[cid] = sum(
                getattr(cfg.weights, c) * getattr(comp, c) for c in CRITERIA
            )
        ordered = sorted(totals.items(), key=lambda kv: -kv[1])
        gap = ordered[0][1] - ordered[1][1]
        stability = selection_stability(comps, cfg, samples=samples)
        diagnostics.append(
            {"gap": gap, "flip_prob": 1.0 - stability.get(ordered[0][0], 0.0)}
        )

    if not diagnostics:
        return cfg.epsilon_review_band, diagnostics

    for eps in grid:
        outside = [d for d in diagnostics if d["gap"] >= eps]
        if not outside:
            continue
        mean_flip = sum(d["flip_prob"] for d in outside) / len(outside)
        if mean_flip <= cfg.reversal_tolerance:
            return eps, diagnostics
    return max(grid), diagnostics


# ---------------------------------------------------------------------------
# Surprise detection
# ---------------------------------------------------------------------------


def detect_surprises(
    pool: RankedPool,
    cfg: EngineConfig,
    chosen_rank: Optional[int] = None,
    previous_scores: Optional[Dict[str, float]] = None,
    previous_inputs: Optional[Dict[str, Tuple]] = None,
) -> List[str]:
    """Fire the codes that make an "unexpected scoring outcome" a detected event.

    The meeting notes asked to debrief unexpected outcomes. Anecdote does not
    scale, so each of these is a rule that fires on its own and lands in the
    weekly report whether or not anyone remembered to mention it.
    """
    codes: List[str] = []
    cands = pool.candidates

    if pool.pool_starvation:
        codes.append("POOL_STARVATION")
    if not cands:
        codes.append("NO_FEASIBLE_CANDIDATE")
        return codes

    if chosen_rank is not None and chosen_rank > 1:
        codes.append("HUMAN_OVERRODE_TOP")
    if cands[0].final_score < cfg.weak_best_score:
        codes.append("WEAK_BEST_OPTION")
    if len(cands) >= 2 and cands[0].final_score - cands[1].final_score < pool.epsilon_used:
        codes.append("INSIDE_REVIEW_BAND")
    if cands[0].selection_stability is not None and cands[0].selection_stability < 0.6:
        codes.append("LOW_SELECTION_STABILITY")

    # Saturation only matters when a criterion has stopped *discriminating*.
    # One coordinator at phi = 0 is normal and correct: they have never met the
    # family. Every feasible coordinator pinned to the same boundary means the
    # criterion contributed nothing to this decision, which is the thing worth
    # knowing.
    if len(cands) >= 2:
        for name in ("phi", "psi", "omega"):
            values = {getattr(c.components, name) for c in cands}
            if len(values) == 1 and values.pop() in (0.0, 1.0):
                codes.append(f"CRITERION_INERT:{name}")
    if any(c.components.utilization > cfg.utilization_hard_cap for c in cands[:1]):
        codes.append("TOP_PICK_OVER_CAPACITY")
    if any("availability_unverified" in c.feasibility.soft_flags for c in cands[:1]):
        codes.append("UNVERIFIED_AVAILABILITY")

    # Same pair, same inputs, materially different score: a code regression or a
    # normalisation artefact, not a real preference change.
    if previous_scores and previous_inputs:
        for cand in cands:
            cid = cand.coordinator_id
            if cid not in previous_scores or cid not in previous_inputs:
                continue
            now_inputs = (
                cand.components.k_prior_visits,
                round(cand.components.committed_hours, 3),
                round(cand.components.travel_minutes, 3),
            )
            if now_inputs == previous_inputs[cid]:
                if abs(cand.final_score - previous_scores[cid]) > 0.15:
                    codes.append(f"UNEXPLAINED_SCORE_SHIFT:{cid}")

    return sorted(set(codes))


def waterfall(
    chosen: CandidateScore, suggested: CandidateScore
) -> List[Tuple[str, float]]:
    """Per-criterion contribution difference, chosen minus suggested.

    Read straight into the debrief: "you picked B over A; A led on continuity
    +0.11, B led on burden relief +0.14, net -0.03".
    """
    return [
        (name, chosen.contributions.get(name, 0.0) - suggested.contributions.get(name, 0.0))
        for name in CRITERIA
    ]
