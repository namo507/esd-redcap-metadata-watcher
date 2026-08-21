"""Orchestration: score one visit, plan a week, commit an assignment.

This is the file a coordinator should be able to read top to bottom and see the
whole policy: filter, score, rank, check fairness constraints, re-check the
calendar at the last possible moment, then write everything down.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from . import __version__
from .calendarsync import CalendarProvider, write_time_recheck
from .config import EngineConfig
from .feasibility import eligible_pool, evaluate, find_slot
from .models import (
    CandidateScore,
    ComponentScores,
    Coordinator,
    FeasibilityResult,
    LabState,
    RankedPool,
    Visit,
)
from .optimize import (
    AssignmentPlan,
    Option,
    RegretReport,
    greedy_plan,
    mcmf_plan,
    regret,
)
from .ranking import break_tie, detect_surprises, rank, selection_stability
from .scoring import score_candidate, weighted_total
from .store import AuditStore


# ---------------------------------------------------------------------------
# Scoring one visit
# ---------------------------------------------------------------------------


def _to_candidate(
    res: FeasibilityResult,
    coordinator: Coordinator,
    visit: Visit,
    state: LabState,
    cfg: EngineConfig,
    now: datetime,
    scored: bool,
) -> CandidateScore:
    cand = CandidateScore(
        coordinator_id=coordinator.coordinator_id,
        coordinator_name=coordinator.name,
        feasibility=res,
    )
    if not scored:
        # Rejected candidates still get their raw inputs recorded, because
        # "would they have won if the credential record were right?" is a
        # question the override log has to be able to answer.
        cand.components = score_candidate(
            coordinator, visit, state.families[visit.family_id], state, cfg, now
        )
        cand.final_score = 0.0
        cand.contributions = {"phi": 0.0, "omega": 0.0, "psi": 0.0, "p": 0.0}
        return cand
    cand.components = score_candidate(
        coordinator, visit, state.families[visit.family_id], state, cfg, now
    )
    cand.final_score, cand.contributions = weighted_total(cand.components, cfg)
    return cand


def score_visit(
    visit: Visit,
    state: LabState,
    cfg: EngineConfig,
    now: Optional[datetime] = None,
    previous_scores: Optional[Dict[str, float]] = None,
    previous_inputs: Optional[Dict[str, Tuple]] = None,
) -> RankedPool:
    """Layers 0-3 for a single visit. Returns the whole pool, ranked."""
    now = now or datetime.now()
    family = state.families[visit.family_id]
    passed, rejected = eligible_pool(visit, state, cfg, now)

    pool = RankedPool(
        visit=visit,
        scored_at=now,
        candidates=[],
        rejected=[],
        family_sigma=family.sigma,
        epsilon_used=cfg.epsilon_review_band,
        weight_vector_id=cfg.weight_vector_id,
        config_fingerprint=cfg.fingerprint(),
    )

    # Pool starvation: re-admit calendar-unavailable candidates rather than
    # returning nothing, but every one comes back flagged and needing a human.
    if not passed:
        pool.pool_starvation = True
        for res in rejected:
            if res.fail_reason and res.fail_reason.startswith("calendar_unavailable"):
                coordinator = state.coordinators[res.coordinator_id]
                travel = state.travel(coordinator.coordinator_id, family.family_id)
                slot = find_slot(coordinator, visit, None, travel_minutes=travel)
                if slot is None:
                    continue
                res.passed = True
                res.provisional = True
                res.slot_start, res.slot_end = slot
                res.soft_flags = tuple(
                    sorted(set(res.soft_flags) | {"availability_unverified"})
                )
                res.fail_reason = None
                passed.append(res)
        rejected = [r for r in rejected if not r.passed]

    if not passed:
        pool.needs_manual_scheduling = True

    for res in passed:
        pool.candidates.append(
            _to_candidate(
                res, state.coordinators[res.coordinator_id], visit, state, cfg, now, True
            )
        )
    for res in rejected:
        pool.rejected.append(
            _to_candidate(
                res, state.coordinators[res.coordinator_id], visit, state, cfg, now, False
            )
        )

    pool.candidates = rank(pool.candidates, cfg)
    pool.candidates = break_tie(pool.candidates, family.sigma, cfg)

    if len(pool.candidates) >= 2:
        stability = selection_stability(
            {c.coordinator_id: c.components for c in pool.candidates}, cfg
        )
        for cand in pool.candidates:
            cand.selection_stability = stability.get(cand.coordinator_id)
    elif pool.candidates:
        pool.candidates[0].selection_stability = 1.0

    pool.surprise_codes = detect_surprises(
        pool, cfg, previous_scores=previous_scores, previous_inputs=previous_inputs
    )
    return pool


# ---------------------------------------------------------------------------
# Fairness constraints (constraints, not criteria)
# ---------------------------------------------------------------------------


def fairness_violations(
    coordinator_id: str,
    candidate: CandidateScore,
    state: LabState,
    cfg: EngineConfig,
    now: datetime,
) -> List[str]:
    """Equity is a constraint, not a scoring term.

    Encoding "nobody should always draw the long drives" as a weighted criterion
    makes it tradeable against everything else, which is exactly what fairness is
    supposed to prevent. So it lives here, as a veto.
    """
    out: List[str] = []
    if candidate.components.utilization > cfg.utilization_hard_cap:
        out.append("over_capacity")

    # Travel equity. The rule, in one sentence a coordinator can act on:
    #
    #   when you are over your share of the driving, you keep getting work,
    #   but you stop getting the long drives.
    #
    # The obvious implementation - veto anyone already over their share - is a
    # ratchet. Every visit is refused, including the short ones that would bring
    # their average back down, and the only way out is to wait for the rolling
    # window to move. In the pilot run that starved one coordinator of all work
    # for a week while the constraint sat there looking correct.
    #
    # A purely directional test (does this push the share up?) is no better: if
    # one person holds nearly all the recent travel their share is already ~1.0
    # and no further trip can raise it, so the cap silently stops firing exactly
    # when it is most needed. Comparing the trip against the team's typical trip
    # avoids both failure modes.
    team_travel = {
        c.coordinator_id: state.rolling_travel_minutes(c.coordinator_id, now)
        for c in state.active_coordinators()
    }
    total_travel = sum(team_travel.values())
    total_capacity = sum(c.capacity_hours_week for c in state.active_coordinators())
    recent = [
        h for h in state.history
        if h.travel_minutes > 0 and (now - h.when).days <= 28
    ]
    trips = [h.travel_minutes for h in recent]
    drivers = {h.coordinator_id for h in recent}
    # Not enough evidence to call anyone a travel hog yet. Stay silent rather
    # than veto on noise; the drift report still shows the raw travel spread.
    enough_evidence = (
        len(trips) >= cfg.travel_cap_min_trips
        and len(drivers) >= cfg.travel_cap_min_coordinators
    )
    if total_travel > 0 and total_capacity > 0 and trips and enough_evidence:
        coordinator = state.coordinators[coordinator_id]
        capacity_share = coordinator.capacity_hours_week / total_capacity
        if capacity_share > 0:
            this_trip = candidate.components.travel_minutes
            mine = team_travel.get(coordinator_id, 0.0)
            prospective_share = (mine + this_trip) / (total_travel + this_trip)
            typical_trip = sum(trips) / len(trips)
            over_cap = prospective_share > cfg.travel_share_cap * capacity_share
            is_a_long_drive = this_trip > typical_trip
            if over_cap and is_a_long_drive:
                out.append("travel_share_cap")
    return out


# ---------------------------------------------------------------------------
# Weekly planning
# ---------------------------------------------------------------------------


def build_options(
    visits: Sequence[Visit],
    state: LabState,
    cfg: EngineConfig,
    now: datetime,
) -> Tuple[List[Option], Dict[str, RankedPool]]:
    """Score every visit and flatten the feasible pairs into optimiser options."""
    options: List[Option] = []
    pools: Dict[str, RankedPool] = {}
    for visit in visits:
        pool = score_visit(visit, state, cfg, now)
        pools[visit.visit_id] = pool
        for cand in pool.candidates:
            slot_start = cand.feasibility.slot_start
            slot_end = cand.feasibility.slot_end
            if slot_start is None or slot_end is None:
                continue
            options.append(
                Option(
                    visit_id=visit.visit_id,
                    coordinator_id=cand.coordinator_id,
                    day=slot_start.date(),
                    score=cand.final_score,
                    slot_start=slot_start,
                    slot_end=slot_end,
                    duration_hours=visit.duration_hours,
                )
            )
    return options, pools


def weekly_capacity_visits(state: LabState, cfg: EngineConfig) -> Dict[str, int]:
    """Convert hour capacity into a visit count cap for the flow network."""
    out: Dict[str, int] = {}
    for c in state.active_coordinators():
        if c.n_completed_visits < cfg.n_min_visits:
            out[c.coordinator_id] = cfg.onboarding_max_visits_week
        else:
            out[c.coordinator_id] = max(1, int(c.capacity_hours_week // 2.5))
    return out


def plan_week(
    visits: Sequence[Visit],
    state: LabState,
    cfg: EngineConfig,
    now: Optional[datetime] = None,
) -> Tuple[AssignmentPlan, AssignmentPlan, RegretReport, Dict[str, RankedPool]]:
    """Run greedy and the optimiser side by side and measure the gap.

    The optimiser runs in shadow from day one. It computes, it logs, and it
    changes nothing until the measured regret says it has earned production.
    """
    now = now or datetime.now()
    options, pools = build_options(visits, state, cfg, now)
    visit_ids = [v.visit_id for v in visits]
    capacity = weekly_capacity_visits(state, cfg)

    greedy = greedy_plan(options, visit_ids, capacity)
    optimal = mcmf_plan(
        options,
        visit_ids,
        capacity,
        unfilled_penalty=cfg.unfilled_penalty,
        max_rounds=cfg.max_flow_repair_rounds,
    )
    report = regret(greedy, optimal, cfg.regret_escalation_threshold)
    return greedy, optimal, report, pools


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


def commit_assignment(
    pool: RankedPool,
    state: LabState,
    cfg: EngineConfig,
    store: AuditStore,
    provider: Optional[CalendarProvider] = None,
    chosen_coordinator_id: Optional[str] = None,
    override_reason_code: Optional[str] = None,
    override_reason_text: Optional[str] = None,
    overridden_by: Optional[str] = None,
    now: Optional[datetime] = None,
    tech_id: Optional[str] = None,
) -> Tuple[str, Optional[CandidateScore], List[str]]:
    """Write the decision down, re-checking the calendar at the last moment.

    ``tech_id`` is the second person on the visit. The manual staffs every visit
    with a clinician and a tech, so recording only one of them would leave the
    audit log unable to answer who actually went.

    Returns (run_id, chosen candidate or None, notes).
    """
    now = now or datetime.now()
    notes: List[str] = []
    run_id = store.record_pool(pool, cfg, code_version=__version__)

    if not pool.candidates:
        store.record_outcome(run_id, None, None, decided_at=now)
        notes.append("no feasible candidate; routed to manual scheduling")
        return run_id, None, notes

    ordered = list(pool.candidates)
    if chosen_coordinator_id:
        ordered = [c for c in ordered if c.coordinator_id == chosen_coordinator_id] + [
            c for c in ordered if c.coordinator_id != chosen_coordinator_id
        ]

    write_conflict = False
    for cand in ordered:
        violations = fairness_violations(cand.coordinator_id, cand, state, cfg, now)
        if violations and not chosen_coordinator_id:
            notes.append(f"{cand.coordinator_id} skipped: {'/'.join(violations)}")
            continue

        if (
            cfg.require_write_time_recheck
            and provider is not None
            and cand.feasibility.slot_start
            and cand.feasibility.slot_end
        ):
            ok, detail = write_time_recheck(
                provider,
                cand.coordinator_id,
                cand.feasibility.slot_start,
                cand.feasibility.slot_end,
            )
            if not ok:
                write_conflict = True
                notes.append(f"{cand.coordinator_id} failed write-time recheck: {detail}")
                continue

        store.record_outcome(
            run_id,
            cand.coordinator_id,
            cand.rank_position,
            human_override=bool(chosen_coordinator_id) and cand.rank_position != 1,
            override_reason_code=override_reason_code,
            override_reason_text=override_reason_text,
            overridden_by=overridden_by,
            is_provisional=cand.feasibility.provisional,
            write_time_conflict=write_conflict,
            assigned_tech_id=tech_id,
            decided_at=now,
        )
        state.pending.setdefault(cand.coordinator_id, []).append(pool.visit)
        if cand.feasibility.provisional:
            notes.append(
                "assignment is PROVISIONAL: availability unverified, "
                "hold the family notification until a human confirms"
            )
        return run_id, cand, notes

    store.record_outcome(
        run_id, None, None, write_time_conflict=write_conflict, decided_at=now
    )
    notes.append("every candidate was vetoed; routed to manual scheduling")
    return run_id, None, notes
