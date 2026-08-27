"""Layer 2: the weighted score.

Four criteria, each a standalone function of primitives so each can be tested
on paper:

    Phi    continuity index   = familiarity(k) * freshness(delta), direction by sigma
    Omega  family preference  = explicit request / avoid / attribute match
    Psi    burden relief      = 1 - utilisation of prospective burden vs capacity
    P      protocol continuity = did this person run the previous checkpoint

Two design rules run through all of it:

1.  Absent data is neutral, never extreme. A coordinator with no history is not
    maximally rested, and a family with no stated preference does not prefer
    everyone equally at 1.0. This is what caused v2 to flood new hires in week
    one, and it is fixed by definition rather than by imputation.

2.  Normalisation is against capacity, not against the busiest teammate. Pool
    relative normalisation makes the same 30-minute travel difference worth
    three times as much on a day when the pool happens to be tight, and it is
    what made new coordinators score 1.0 on workload.
"""

# -------------------------------------------------------------------------
# STEP 7 OF 9  --  HOW GOOD A FIT
#
#   before  the candidates that passed every hard gate
#   here    four criteria, each 0 to 1: continuity, family preference,
#           burden relief, protocol continuity. Weighted and added
#   after   ranking.py sorts them and flags the close calls
#
#   worked example
#     S = 0.45*phi + 0.15*omega + 0.30*psi + 0.10*p
#     family 5901, 9m, Lauren Puttock:
#       knows the family  0.000 x 0.45 = 0.000
#       family's choice   0.500 x 0.15 = 0.075
#       has room          0.665 x 0.30 = 0.200
#       did the last one  0.000 x 0.10 = 0.000   total 0.275
# -------------------------------------------------------------------------

from __future__ import annotations

import math
from typing import Optional

from .config import EngineConfig
from .models import ComponentScores, Coordinator, Family, LabState, Visit


# ---------------------------------------------------------------------------
# Phi: continuity index (v2 family history + v2 recency, merged)
# ---------------------------------------------------------------------------


def familiarity(k: int, kappa: float) -> float:
    """Saturating familiarity in [0, 1). Monotone increasing in k.

    1 - exp(-k / kappa). With kappa = 2: k=0 -> 0.00, k=1 -> 0.39, k=3 -> 0.78,
    k=5 -> 0.92. Saturating rather than linear because the fifth visit with a
    family adds far less rapport than the first.
    """
    if k <= 0:
        return 0.0
    return 1.0 - math.exp(-float(k) / kappa)


def freshness(days_since: Optional[float], tau: float) -> float:
    """Exponential decay of relationship freshness, exp(-delta / tau).

    ``days_since`` is None when the pair have never met. That case is undefined,
    not stale, so we return 0.0 and let the product below zero out cleanly.
    """
    if days_since is None:
        return 0.0
    return math.exp(-max(0.0, days_since) / tau)


def continuity_raw(k: int, days_since: Optional[float], cfg: EngineConfig) -> float:
    """R = familiarity(k) * freshness(delta), in [0, 1]."""
    return familiarity(k, cfg.kappa_visits) * freshness(days_since, cfg.tau_days)


def continuity_score(
    k: int, days_since: Optional[float], sigma: int, cfg: EngineConfig
) -> float:
    """Phi. sigma = +1 the family wants a familiar face, -1 a fresh one.

    Flipping at the *index* level rather than at the familiarity level means the
    fresh-face case also correctly rewards a long gap since the last contact,
    not just a low visit count.
    """
    r = continuity_raw(k, days_since, cfg)
    return r if sigma >= 0 else 1.0 - r


# ---------------------------------------------------------------------------
# Omega: family preference
# ---------------------------------------------------------------------------


def preference_score(
    coordinator: Coordinator, family: Family, cfg: EngineConfig
) -> float:
    """Omega in [0, 1].

    Hard exclusions never reach here; they are a Layer 1 filter and cannot be
    outscored. This term carries the *soft* end of family preference: who they
    asked for, who they would rather not have again, and attribute requirements
    such as a Spanish-speaking coordinator.
    """
    if coordinator.coordinator_id in family.preferred_coordinators:
        base = cfg.pref_named
    elif coordinator.coordinator_id in family.soft_avoid:
        base = cfg.pref_soft_avoid
    elif family.preferred_coordinators:
        # The family named someone, and it was not this person.
        base = cfg.pref_other_named
    else:
        # No preference on record. Neutral, so missing data cannot move a ranking.
        base = cfg.pref_neutral

    if family.required_attributes:
        satisfied = family.required_attributes & coordinator.attributes
        share = len(satisfied) / len(family.required_attributes)
        base += cfg.pref_attribute_bonus * (share - 0.5) * 2.0

    return min(1.0, max(0.0, base))


# ---------------------------------------------------------------------------
# Psi: burden relief (v2 workload + v2 travel, merged)
# ---------------------------------------------------------------------------


def ramped_capacity(
    coordinator: Coordinator, cfg: EngineConfig, capacity_hours: Optional[float] = None
) -> float:
    """Effective weekly capacity during onboarding.

    Cap(t) = Cap_full * min(1, (n + n0) / (N_min + n0))

    This replaces v2's proposed "borrow the team median until N_min visits".
    Borrowing a median for workload is factually wrong: a new hire's calendar
    really is empty, and imputing median hours starves someone who needs work.
    Ramping capacity instead is honest about the thing that *is* lower early on,
    which is throughput, and it decays smoothly with no cliff at N_min.
    """
    full = capacity_hours if capacity_hours is not None else coordinator.capacity_hours_week
    if full <= 0:
        return 1e-6
    n = max(0, coordinator.n_completed_visits)
    ramp = min(1.0, (n + cfg.ramp_n0) / (cfg.n_min_visits + cfg.ramp_n0))
    return max(1e-6, full * ramp)


def prospective_burden(
    committed_hours: float,
    visit_duration_hours: float,
    travel_minutes: float,
    cfg: EngineConfig,
) -> float:
    """B = committed + duration + gamma * travel/60, all in hours.

    Two things v2 got wrong and this fixes:

    * v2 held workload (a stock: hours already booked) and travel (a marginal
      cost: this trip) as two separate normalised terms. Adding the candidate
      visit's own duration and travel makes the metric what it claims to be,
      the burden *after* taking this visit.

    * v2's 0.20 / 0.15 split, under min-max normalisation, implied one hour of
      driving was worth about thirteen hours of clinic work. gamma states the
      exchange rate out loud instead, and is elicited from the team: "how many
      extra minutes of clinic time would you accept to avoid ten minutes of
      driving?", median / 10.
    """
    return committed_hours + visit_duration_hours + cfg.gamma_travel * travel_minutes / 60.0


def burden_relief_score(burden_hours: float, capacity_hours: float) -> float:
    """Psi = 1 - clip(B / Cap, 0, 1). Absolute, not pool-relative."""
    if capacity_hours <= 0:
        return 0.0
    return 1.0 - min(1.0, max(0.0, burden_hours / capacity_hours))


def utilization(burden_hours: float, capacity_hours: float) -> float:
    if capacity_hours <= 0:
        return float("inf")
    return burden_hours / capacity_hours


# ---------------------------------------------------------------------------
# P: protocol / rater continuity
# ---------------------------------------------------------------------------


def protocol_continuity_score(did_previous_checkpoint: bool) -> float:
    """Binary. Kept separate from Phi despite the correlation.

    Phi is a relationship claim owned by the scheduler; P is a measurement
    validity claim owned by the PI (same rater across ADOS checkpoints). They
    need to be able to move independently, and if the PI ever mandates same
    rater, P becomes a Layer 1 constraint without disturbing Phi.
    """
    return 1.0 if did_previous_checkpoint else 0.0


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------


def score_candidate(
    coordinator: Coordinator,
    visit: Visit,
    family: Family,
    state: LabState,
    cfg: EngineConfig,
    now,
) -> ComponentScores:
    """Compute every Layer 2 component for one (coordinator, visit) pair."""
    k = state.n_prior(coordinator.coordinator_id, family.family_id)
    delta = state.days_since_family_contact(
        coordinator.coordinator_id, family.family_id, now
    )
    travel = state.travel(coordinator.coordinator_id, family.family_id)
    committed = state.committed(coordinator.coordinator_id)
    capacity = ramped_capacity(coordinator, cfg)
    burden = prospective_burden(committed, visit.duration_hours, travel, cfg)

    return ComponentScores(
        phi=continuity_score(k, delta, family.sigma, cfg),
        omega=preference_score(coordinator, family, cfg),
        psi=burden_relief_score(burden, capacity),
        p=protocol_continuity_score(
            state.did_previous_checkpoint(
                coordinator.coordinator_id,
                family.family_id,
                visit.protocol,
                visit.checkpoint,
            )
        ),
        phi_raw_R=continuity_raw(k, delta, cfg),
        k_prior_visits=k,
        days_since_family_contact=delta,
        committed_hours=committed,
        capacity_hours=capacity,
        utilization=utilization(burden, capacity),
        travel_minutes=travel,
        burden_hours=burden,
        n_c_total_visits=coordinator.n_completed_visits,
        is_cold_start=coordinator.n_completed_visits < cfg.n_min_visits,
    )


def weighted_total(components: ComponentScores, cfg: EngineConfig):
    """Return (final_score, per-criterion weighted contributions).

    The contributions are what powers the override waterfall in the weekly
    debrief: "you picked B over A; A led on continuity +0.11, B led on burden
    relief +0.14". That is the mechanism that turns "that felt wrong" into a
    weight adjustment instead of an anecdote.
    """
    w = cfg.weights
    contributions = {
        "phi": w.phi * components.phi,
        "omega": w.omega * components.omega,
        "psi": w.psi * components.psi,
        "p": w.p * components.p,
    }
    total = sum(contributions.values())
    # Every component is in [0, 1] and the weights are a convex combination, so
    # the total is in [0, 1] by construction. Guard against float drift only.
    total = min(1.0, max(0.0, total))
    return total, contributions
