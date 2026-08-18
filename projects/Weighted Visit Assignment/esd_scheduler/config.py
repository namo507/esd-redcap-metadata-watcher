"""Versioned engine configuration.

Every number the PI might want to retune lives here, and every scoring run
records which config version produced it. Weights change only at a version
boundary, never mid-week.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WeightVector:
    """The four Layer 2 criterion weights. Must sum to 1.

    v3 collapses the v2 five-term score into four non-redundant criteria:

        phi   continuity            (v2 family-history + v2 recency, merged)
        omega family preference     (explicit preferred / avoid / attributes)
        psi   burden relief         (v2 workload + v2 travel, merged)
        p     protocol continuity   (same rater for the previous checkpoint)
    """

    phi: float = 0.45
    omega: float = 0.15
    psi: float = 0.30
    p: float = 0.10

    def validate(self) -> None:
        total = self.phi + self.omega + self.psi + self.p
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"weights must sum to 1.0, got {total!r}")
        for name, value in asdict(self).items():
            if value < 0:
                raise ValueError(f"weight {name} must be non-negative, got {value!r}")

    def as_dict(self) -> Dict[str, float]:
        return asdict(self)

    def perturbed(self, name: str, delta: float) -> "WeightVector":
        """Nudge one weight by ``delta`` and renormalise the others in proportion.

        This is the one-at-a-time perturbation used by the sensitivity suite.
        Renormalising the *others* (rather than rescaling everything) keeps the
        perturbation interpretable: only the named criterion's share changed by
        the requested amount.
        """
        current = self.as_dict()
        if name not in current:
            raise KeyError(name)
        target = min(max(current[name] + delta, 0.0), 1.0)
        rest_total = sum(v for k, v in current.items() if k != name)
        new = {}
        for k, v in current.items():
            if k == name:
                new[k] = target
            elif rest_total <= 0:
                # Degenerate: all remaining mass was on the perturbed criterion.
                new[k] = (1.0 - target) / (len(current) - 1)
            else:
                new[k] = v * (1.0 - target) / rest_total
        return WeightVector(**new)


# ---------------------------------------------------------------------------
# Engine configuration
# ---------------------------------------------------------------------------


@dataclass
class EngineConfig:
    """All tunable parameters, versioned as one object."""

    # --- identity -----------------------------------------------------------
    weight_vector_id: str = "v3.0-analyst-provisional"
    elicitation_method: str = "analyst"  # analyst | ahp | fuzzy_ahp | dematel | cond_logit
    consistency_ratio: Optional[float] = None
    n_respondents: int = 0
    effective_from: str = "2026-08-17"
    approved_by: str = "pending PI sign-off"

    weights: WeightVector = field(default_factory=WeightVector)

    # --- Phi: continuity index ---------------------------------------------
    # R = (1 - exp(-k / kappa)) * exp(-delta_days / tau)
    kappa_visits: float = 2.0  # familiarity saturation: 1 visit -> 0.39, 3 -> 0.78
    tau_days: float = 75.0     # freshness half-life-ish decay constant

    # --- Omega: family preference ------------------------------------------
    pref_named: float = 1.00        # coordinator is explicitly requested
    pref_neutral: float = 0.50      # family has no preference on record
    pref_other_named: float = 0.35  # family named someone else (not this person)
    pref_soft_avoid: float = 0.00   # soft avoid; hard exclusions are Layer 1
    pref_attribute_bonus: float = 0.25  # per satisfied attribute requirement

    # --- Psi: burden relief -------------------------------------------------
    # B = committed_hours + visit_duration + gamma * travel_minutes / 60
    gamma_travel: float = 2.0  # burden-equivalence: 1 travel hr == gamma clinic hr
    default_capacity_hours: float = 20.0

    # --- cold start ---------------------------------------------------------
    n_min_visits: int = 20      # end of the capacity ramp (= 4 * m_prior)
    m_prior_visits: float = 5.0  # shrinkage strength for *estimated* parameters
    ramp_n0: float = 1.0        # smoothing so a brand-new hire is not at zero capacity
    onboarding_max_visits_week: int = 2

    # --- Layer 3 ------------------------------------------------------------
    top_k: int = 3
    epsilon_review_band: float = 0.05  # overwritten by calibrate_epsilon()
    epsilon_calibrated: bool = False
    reversal_tolerance: float = 0.10   # target P(top-1 flips) for the band
    # "The best available option is weak." Absolute thresholds do not survive a
    # change of criteria, so set this to the 10th percentile of the observed
    # top-1 distribution after pilot week 1; the debrief prints the mean top-1
    # score for exactly that purpose.
    weak_best_score: float = 0.20
    mc_samples: int = 2000
    mc_concentration: float = 60.0     # Dirichlet concentration; lower = more spread
    rng_seed: int = 20260817

    # --- Layer 0: calendar staleness (seconds) ------------------------------
    stale_hard_72h: int = 15 * 60
    stale_soft_72h: int = 60 * 60
    stale_hard_14d: int = 4 * 3600
    stale_soft_14d: int = 24 * 3600
    stale_hard_far: int = 24 * 3600
    stale_soft_far: int = 72 * 3600
    unsyncable_halt_fraction: float = 0.20
    require_write_time_recheck: bool = True

    # Delegated auth is the only mode that satisfies the lab's privacy
    # constraint: the app inherits the signed-in person's view, so at the tenant
    # default sharing level Exchange never sends event subjects. Application-only
    # auth cannot be reduced to free/busy at all, because Microsoft publishes no
    # calendar app role below Calendars.Read, which reads subject and body.
    # Reaching that mode therefore requires an explicit, recorded acknowledgement
    # rather than a config typo.
    graph_auth_mode: str = "delegated"          # delegated | application
    allow_app_only_ack: Optional[str] = None    # who accepted the risk, and when
    display_timezone: str = "America/New_York"  # exports print none; state it once

    # --- optimiser ----------------------------------------------------------
    optimizer_mode: str = "greedy"  # greedy | dp | mcmf | shadow
    shadow_optimizer: bool = True
    regret_escalation_threshold: float = 0.03
    regret_consecutive_weeks: int = 2
    unfilled_penalty: float = 1.0   # Pi, in score units, for leaving a visit unfilled
    max_flow_repair_rounds: int = 5

    # --- fairness constraints (constraints, not criteria) -------------------
    travel_share_cap: float = 1.4   # rolling 4-week travel share vs capacity share
    # Minimum evidence before the travel cap may veto anyone. A constraint that
    # can deny someone work must not fire on two or three logged trips: with a
    # sample that small the "typical trip" is whatever the last person happened
    # to drive. Same principle as refusing to fit the shrinkage prior from a
    # handful of coordinators.
    travel_cap_min_trips: int = 8
    travel_cap_min_coordinators: int = 3
    utilization_hard_cap: float = 1.0

    # --- drift thresholds ---------------------------------------------------
    psi_investigate: float = 0.10
    psi_act: float = 0.25
    top3_hit_rate_target: float = 0.90
    cv_amber: float = 0.25
    cv_red: float = 0.40

    def validate(self) -> None:
        self.weights.validate()
        if self.graph_auth_mode not in ("delegated", "application"):
            raise ValueError(
                f"graph_auth_mode must be 'delegated' or 'application', "
                f"got {self.graph_auth_mode!r}"
            )
        if self.graph_auth_mode == "application" and not self.allow_app_only_ack:
            raise ValueError(
                "graph_auth_mode='application' grants Calendars.Read, which reads "
                "event subjects and bodies for every mailbox in scope. There is no "
                "lesser calendar app role. Set allow_app_only_ack to the name and "
                "date of whoever accepted that, or use delegated auth."
            )
        if self.kappa_visits <= 0 or self.tau_days <= 0:
            raise ValueError("kappa_visits and tau_days must be positive")
        if self.gamma_travel < 0:
            raise ValueError("gamma_travel must be non-negative")
        if not 0 < self.reversal_tolerance < 1:
            raise ValueError("reversal_tolerance must be in (0, 1)")

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        d["weights"] = self.weights.as_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "EngineConfig":
        data = dict(data)
        weights = data.pop("weights", None)
        cfg = cls(**data)
        if weights:
            cfg.weights = WeightVector(**weights)
        cfg.validate()
        return cfg

    def fingerprint(self) -> str:
        """Stable hash of the whole config, logged alongside every decision."""
        blob = json.dumps(self.to_dict(), sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:12]

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True)
            fh.write("\n")


DEFAULT_CONFIG_PATH = os.path.join("config", "engine.json")


def load_config(path: Optional[str] = None) -> EngineConfig:
    """Load config from JSON, falling back to the built-in defaults."""
    path = path or DEFAULT_CONFIG_PATH
    if not os.path.exists(path):
        cfg = EngineConfig()
        cfg.validate()
        return cfg
    with open(path, "r", encoding="utf-8") as fh:
        return EngineConfig.from_dict(json.load(fh))
