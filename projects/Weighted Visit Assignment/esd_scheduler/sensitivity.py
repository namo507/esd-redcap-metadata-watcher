"""Weight validation and sensitivity analysis.

Three independent routes to the same four numbers, which is the point: if AHP,
DEMATEL and the humans' revealed behaviour all land in the same neighbourhood,
the weights are real. If they disagree, that disagreement is the finding.

  ahp_weights          stated preference, Saaty 1-9 pairwise, with a consistency check
  dematel              interdependence structure, and the redundancy diagnostic
  conditional_logit    revealed preference, fitted from what schedulers actually did

Plus the sensitivity suite the pilot needs before anyone trusts a ranking:
one-at-a-time perturbation, per-decision criticality, and the Monte Carlo over
the weight simplex that also calibrates the review band.

Restructuring the criteria first is what makes this cheap. Five correlated
criteria need ten pairwise comparisons and consistency is usually poor. Four
non-redundant criteria need six, and three of those are near-automatic. That is
a twenty minute meeting instead of an afternoon.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .config import EngineConfig, WeightVector
from .models import ComponentScores
from .ranking import CRITERIA

# Saaty's random consistency index by matrix order.
RANDOM_INDEX = {1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32, 8: 1.41}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _score(components: ComponentScores, weights: WeightVector) -> float:
    return sum(getattr(weights, c) * getattr(components, c) for c in CRITERIA)


def _ranking(decision: Dict[str, ComponentScores], weights: WeightVector) -> List[str]:
    return [
        cid
        for cid, _ in sorted(
            ((cid, _score(comp, weights)) for cid, comp in decision.items()),
            key=lambda kv: (-kv[1], kv[0]),
        )
    ]


def kendall_tau(a: Sequence[str], b: Sequence[str]) -> float:
    """Rank correlation between two orderings of the same items."""
    pos_b = {x: i for i, x in enumerate(b)}
    items = [x for x in a if x in pos_b]
    n = len(items)
    if n < 2:
        return 1.0
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            si = pos_b[items[i]] - pos_b[items[j]]
            if si < 0:
                concordant += 1
            elif si > 0:
                discordant += 1
    total = n * (n - 1) / 2
    return (concordant - discordant) / total if total else 1.0


# ---------------------------------------------------------------------------
# One-at-a-time perturbation
# ---------------------------------------------------------------------------


@dataclass
class OATResult:
    criterion: str
    delta: float
    reversal_rate: float
    mean_tau: float


def oat_sensitivity(
    decisions: Sequence[Dict[str, ComponentScores]],
    cfg: EngineConfig,
    delta: float = 0.05,
) -> List[OATResult]:
    """Nudge each weight by +/- delta, renormalise the rest, replay every decision.

    Rule of thumb for reading it: a reversal rate above about 15% on a single
    0.05 nudge means the weights are not identified at the precision the ranking
    is being presented with. The right response is a wider review band and more
    decisions routed to a human, not a more confident-looking number.
    """
    usable = [d for d in decisions if len(d) >= 2]
    out: List[OATResult] = []
    if not usable:
        return out
    base = [(_ranking(d, cfg.weights)) for d in usable]

    for criterion in CRITERIA:
        for signed in (delta, -delta):
            perturbed = cfg.weights.perturbed(criterion, signed)
            flips = 0
            taus: List[float] = []
            for d, base_order in zip(usable, base):
                new_order = _ranking(d, perturbed)
                if new_order[0] != base_order[0]:
                    flips += 1
                taus.append(kendall_tau(base_order, new_order))
            out.append(
                OATResult(
                    criterion=criterion,
                    delta=signed,
                    reversal_rate=flips / len(usable),
                    mean_tau=statistics.fmean(taus),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Criticality (Triantaphyllou-Sanchez)
# ---------------------------------------------------------------------------


def criticality(
    decisions: Sequence[Dict[str, ComponentScores]],
    cfg: EngineConfig,
    max_delta: float = 0.5,
    tolerance: float = 1e-4,
) -> Dict[str, List[float]]:
    """Smallest |change| in each weight that flips the top choice, per decision.

    The distribution of these is the honest answer to "how solid is this
    ranking?". If the median flip distance is below the review band, the top two
    should be presented as a tie band rather than as first and second.
    """
    out: Dict[str, List[float]] = {c: [] for c in CRITERIA}
    for decision in decisions:
        if len(decision) < 2:
            continue
        leader = _ranking(decision, cfg.weights)[0]
        for criterion in CRITERIA:
            found = math.inf
            for sign in (1, -1):
                lo, hi = 0.0, max_delta
                # Does the maximum nudge flip it at all?
                if _ranking(decision, cfg.weights.perturbed(criterion, sign * hi))[0] == leader:
                    continue
                while hi - lo > tolerance:
                    mid = (lo + hi) / 2
                    if _ranking(decision, cfg.weights.perturbed(criterion, sign * mid))[0] == leader:
                        lo = mid
                    else:
                        hi = mid
                found = min(found, hi)
            if found < math.inf:
                out[criterion].append(found)
    return out


def criticality_summary(crit: Dict[str, List[float]]) -> Dict[str, Dict[str, float]]:
    summary: Dict[str, Dict[str, float]] = {}
    for criterion, values in crit.items():
        if not values:
            summary[criterion] = {"n": 0, "median": float("nan"), "p10": float("nan")}
            continue
        ordered = sorted(values)
        summary[criterion] = {
            "n": len(ordered),
            "median": statistics.median(ordered),
            "p10": ordered[max(0, int(0.10 * len(ordered)) - 1)],
        }
    return summary


# ---------------------------------------------------------------------------
# AHP
# ---------------------------------------------------------------------------


@dataclass
class AHPResult:
    weights: Dict[str, float]
    lambda_max: float
    consistency_index: float
    consistency_ratio: float
    acceptable: bool


def ahp_weights(matrix: Sequence[Sequence[float]], names: Sequence[str]) -> AHPResult:
    """Principal eigenvector by power iteration, plus Saaty's consistency ratio.

    CR < 0.10 is the usual acceptance bar. Above it, hand the matrix back to the
    respondent rather than repairing it silently; an inconsistent respondent is
    telling you the criteria are not comparable in the form you asked.
    """
    n = len(matrix)
    if n == 0 or any(len(row) != n for row in matrix):
        raise ValueError("matrix must be square")
    vector = [1.0 / n] * n
    lambda_max = float(n)
    for _ in range(500):
        nxt = [sum(matrix[i][j] * vector[j] for j in range(n)) for i in range(n)]
        total = sum(nxt) or 1.0
        nxt = [v / total for v in nxt]
        if max(abs(a - b) for a, b in zip(nxt, vector)) < 1e-12:
            vector = nxt
            break
        vector = nxt
    weighted_sum = [sum(matrix[i][j] * vector[j] for j in range(n)) for i in range(n)]
    lambda_max = statistics.fmean(
        ws / v for ws, v in zip(weighted_sum, vector) if v > 0
    )
    ci = (lambda_max - n) / (n - 1) if n > 1 else 0.0
    ri = RANDOM_INDEX.get(n, 1.45)
    cr = ci / ri if ri > 0 else 0.0
    return AHPResult(
        weights={names[i]: vector[i] for i in range(n)},
        lambda_max=lambda_max,
        consistency_index=ci,
        consistency_ratio=cr,
        acceptable=cr < 0.10,
    )


def aggregate_judgments(matrices: Sequence[Sequence[Sequence[float]]]):
    """Geometric mean of the judgments (AIJ), not the arithmetic mean of weights.

    The geometric mean preserves the reciprocity of a comparison matrix; the
    arithmetic mean does not, and averaging individually-derived weight vectors
    quietly discards that structure.
    """
    if not matrices:
        raise ValueError("no matrices")
    n = len(matrices[0])
    out = [[1.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            product = 1.0
            for m in matrices:
                product *= m[i][j]
            out[i][j] = product ** (1.0 / len(matrices))
    return out


# ---------------------------------------------------------------------------
# DEMATEL
# ---------------------------------------------------------------------------


@dataclass
class DematelResult:
    total_relation: List[List[float]]
    prominence: Dict[str, float]  # r + c, importance
    relation: Dict[str, float]    # r - c, cause (>0) vs effect (<0)
    weights: Dict[str, float]
    redundant: List[str]


def dematel(
    direct: Sequence[Sequence[float]], names: Sequence[str], effect_threshold: float = -0.25
) -> DematelResult:
    """T = N (I - N)^-1, with N the row-max-normalised direct influence matrix.

    Use prominence (r + c) as the weight basis and relation (r - c) as the
    redundancy diagnostic: a criterion with strongly negative r - c is an
    *effect* driven by the others and should not carry independent weight. That
    is the empirical test for the v2 question of whether recency was anything
    more than a lagging shadow of family history and workload.
    """
    n = len(direct)
    row_sums = [sum(row) for row in direct]
    scale = max(row_sums) or 1.0
    N = [[direct[i][j] / scale for j in range(n)] for i in range(n)]

    # (I - N)^-1 by Gauss-Jordan on the augmented matrix.
    A = [[(1.0 if i == j else 0.0) - N[i][j] for j in range(n)] + [1.0 if i == k else 0.0 for k in range(n)] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(A[r][col]))
        if abs(A[pivot][col]) < 1e-12:
            raise ValueError("(I - N) is singular")
        A[col], A[pivot] = A[pivot], A[col]
        pv = A[col][col]
        A[col] = [x / pv for x in A[col]]
        for r in range(n):
            if r == col:
                continue
            factor = A[r][col]
            if factor:
                A[r] = [x - factor * y for x, y in zip(A[r], A[col])]
    inv = [row[n:] for row in A]
    T = [[sum(N[i][k] * inv[k][j] for k in range(n)) for j in range(n)] for i in range(n)]

    r = [sum(T[i]) for i in range(n)]
    c = [sum(T[i][j] for i in range(n)) for j in range(n)]
    prominence = {names[i]: r[i] + c[i] for i in range(n)}
    relation = {names[i]: r[i] - c[i] for i in range(n)}
    total = sum(prominence.values()) or 1.0
    weights = {k: v / total for k, v in prominence.items()}
    redundant = [k for k, v in relation.items() if v <= effect_threshold]
    return DematelResult(T, prominence, relation, weights, redundant)


# ---------------------------------------------------------------------------
# Revealed preference: conditional logit
# ---------------------------------------------------------------------------


@dataclass
class LogitResult:
    beta: Dict[str, float]
    normalized_weights: Dict[str, float]
    log_likelihood: float
    n_decisions: int
    converged: bool


def conditional_logit(
    decisions: Sequence[Tuple[Dict[str, ComponentScores], str]],
    iterations: int = 500,
    learning_rate: float = 0.5,
) -> LogitResult:
    """Fit McFadden's conditional logit to what schedulers actually chose.

        P(c chosen | pool) = exp(b'z_c) / sum_c' exp(b'z_c')

    Normalised betas are the weights the humans are really using. This is the
    best long-run answer to the weight question, and it is free *provided the
    whole feasible pool was logged*. Logging only the winner makes it permanently
    impossible, which is why the audit schema does it the other way.

    Needs roughly 50-100 decisions with three or more alternatives before the
    estimates settle.
    """
    usable = [(d, chosen) for d, chosen in decisions if len(d) >= 2 and chosen in d]
    k = len(CRITERIA)
    beta = [0.0] * k
    if not usable:
        return LogitResult({c: 0.0 for c in CRITERIA}, {c: 1.0 / k for c in CRITERIA}, 0.0, 0, False)

    ll = float("-inf")
    converged = False
    for step in range(iterations):
        grad = [0.0] * k
        total_ll = 0.0
        for pool, chosen in usable:
            ids = sorted(pool)
            utils = []
            for cid in ids:
                comp = pool[cid]
                utils.append(sum(beta[j] * getattr(comp, CRITERIA[j]) for j in range(k)))
            m = max(utils)
            exps = [math.exp(u - m) for u in utils]
            denom = sum(exps)
            probs = [e / denom for e in exps]
            idx = ids.index(chosen)
            total_ll += math.log(max(probs[idx], 1e-12))
            for j in range(k):
                expected = sum(
                    probs[i] * getattr(pool[ids[i]], CRITERIA[j]) for i in range(len(ids))
                )
                grad[j] += getattr(pool[chosen], CRITERIA[j]) - expected
        beta = [b + learning_rate * g / len(usable) for b, g in zip(beta, grad)]
        if abs(total_ll - ll) < 1e-9:
            converged = True
            ll = total_ll
            break
        ll = total_ll

    # Normalise to a comparable weight vector: shift to non-negative, scale to 1.
    lo = min(beta)
    shifted = [b - min(0.0, lo) for b in beta]
    total = sum(shifted) or 1.0
    return LogitResult(
        beta={CRITERIA[j]: beta[j] for j in range(k)},
        normalized_weights={CRITERIA[j]: shifted[j] / total for j in range(k)},
        log_likelihood=ll,
        n_decisions=len(usable),
        converged=converged,
    )


# ---------------------------------------------------------------------------
# Redundancy diagnostics
# ---------------------------------------------------------------------------


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    if n < 3:
        return 0.0

    def ranks(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        out = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    ra, rb = ranks(list(a)), ranks(list(b))
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else 0.0


def redundancy_matrix(rows: Sequence[ComponentScores]) -> Dict[Tuple[str, str], float]:
    """Pairwise Spearman across criterion values. |rho| > 0.6 is a merge candidate.

    Run this on the first hundred logged candidate rows. It is the empirical
    version of the argument that v2's family history and recency were measuring
    one construct twice, and it will say so in a number the PI can see.
    """
    out: Dict[Tuple[str, str], float] = {}
    for i, a in enumerate(CRITERIA):
        for b in CRITERIA[i + 1 :]:
            out[(a, b)] = spearman(
                [getattr(r, a) for r in rows], [getattr(r, b) for r in rows]
            )
    return out
