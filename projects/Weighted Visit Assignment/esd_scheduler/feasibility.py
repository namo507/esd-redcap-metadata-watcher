"""Layer 1: hard eligibility.

Seven predicates, ANDed. A candidate who fails any of them is out, and no score
can rescue them. Every failure records *which* predicate fired first, because
"why was Kali not offered?" has to be answerable in one line.

    W    date window match      visit window overlaps declared working hours
    A    open slot              a free block of the visit's length exists in it
    X    no calendar clash      nothing hard-booked over that block
    E    no family conflict     not on the family's hard exclusion list
    K    credential match       Req(protocol) is a subset of Cred(coordinator)
    Cal  calendar fresh         Layer 0 freshness gate
    Ramp onboarding cap         new hires capped at q visits/week

Ramp is deliberately a constraint rather than a score adjustment. "Do not
overload a new hire in week one" is a policy, and policies belong where they
cannot be traded away against a good continuity score.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from .calendarsync import EXPIRED, FRESH, STALE, SYNC_FAILED, classify_freshness
from .config import EngineConfig
from .models import (
    BusyBlock,
    CalendarSnapshot,
    Coordinator,
    Family,
    FeasibilityResult,
    LabState,
    Visit,
)


def _working_intervals(
    coordinator: Coordinator, window_start: datetime, window_end: datetime
) -> List[Tuple[datetime, datetime]]:
    """Expand the coordinator's weekly working pattern across the visit window."""
    if not coordinator.working_blocks:
        # No declared pattern: assume standard weekday business hours.
        pattern = [(d, 8.0, 17.0) for d in range(5)]
    else:
        pattern = coordinator.working_blocks

    out: List[Tuple[datetime, datetime]] = []
    day = window_start.replace(hour=0, minute=0, second=0, microsecond=0)
    last = window_end.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= last:
        for weekday, start_h, end_h in pattern:
            if day.weekday() != weekday:
                continue
            s = day + timedelta(hours=start_h)
            e = day + timedelta(hours=end_h)
            s = max(s, window_start)
            e = min(e, window_end)
            if e > s:
                out.append((s, e))
        day += timedelta(days=1)
    return out


def _subtract_blocks(
    intervals: Sequence[Tuple[datetime, datetime]], blocks: Sequence[BusyBlock]
) -> List[Tuple[datetime, datetime]]:
    """Remove busy blocks from working intervals, returning what is left free."""
    free = list(intervals)
    for block in blocks:
        nxt: List[Tuple[datetime, datetime]] = []
        for s, e in free:
            if block.end <= s or block.start >= e:
                nxt.append((s, e))
                continue
            if block.start > s:
                nxt.append((s, min(block.start, e)))
            if block.end < e:
                nxt.append((max(block.end, s), e))
        free = [(s, e) for s, e in nxt if e > s]
    return free


def find_slot(
    coordinator: Coordinator,
    visit: Visit,
    snapshot: Optional[CalendarSnapshot],
    travel_minutes: float = 0.0,
) -> Optional[Tuple[datetime, datetime]]:
    """Earliest free block long enough for the visit plus its round trip.

    The travel buffer is part of the slot, not an afterthought. Without it the
    engine will happily schedule two visits forty minutes apart across town.
    """
    need = timedelta(hours=visit.duration_hours) + timedelta(minutes=travel_minutes)
    working = _working_intervals(coordinator, visit.window_start, visit.window_end)
    if not working:
        return None
    hard = snapshot.hard_blocks() if snapshot else []
    for s, e in sorted(_subtract_blocks(working, hard)):
        if e - s >= need:
            return s, s + need
    return None


def evaluate(
    coordinator: Coordinator,
    visit: Visit,
    family: Family,
    state: LabState,
    cfg: EngineConfig,
    now: datetime,
) -> FeasibilityResult:
    """Run the seven predicates in order and stop at the first failure."""
    res = FeasibilityResult(coordinator_id=coordinator.coordinator_id)
    protocol = state.protocols.get(visit.protocol)
    required = protocol.required_credentials if protocol else frozenset()
    res.missing_credentials = frozenset(coordinator.missing_credentials(required))

    # --- E: hard family conflict (checked early; it is the most sensitive) ---
    res.no_family_conflict = coordinator.coordinator_id not in family.hard_exclusions
    if not res.no_family_conflict:
        res.fail_reason = "family_exclusion"
        return res

    # --- K: credentials -----------------------------------------------------
    res.credential_match = not res.missing_credentials
    if not res.credential_match:
        res.fail_reason = "missing_credential:" + ",".join(sorted(res.missing_credentials))
        return res

    # --- Ramp: onboarding cap ----------------------------------------------
    is_new = coordinator.n_completed_visits < cfg.n_min_visits
    week_load = state.visits_this_week(coordinator.coordinator_id, now)
    res.ramp_ok = (not is_new) or week_load < cfg.onboarding_max_visits_week
    if not res.ramp_ok:
        res.fail_reason = "onboarding_cap"
        return res

    # --- Cal: Layer 0 freshness gate ---------------------------------------
    horizon_hours = max(0.0, (visit.window_start - now).total_seconds() / 3600.0)
    snapshot = state.calendars.get(coordinator.coordinator_id)
    cls, age = classify_freshness(snapshot, now, horizon_hours, cfg)
    res.calendar_cache_age_s = age
    res.calendar_status = cls
    res.calendar_fresh = cls == FRESH
    res.provisional = cls == STALE
    if cls in (EXPIRED, SYNC_FAILED):
        res.fail_reason = "calendar_unavailable:" + cls
        return res

    # --- W: date window match ----------------------------------------------
    working = _working_intervals(coordinator, visit.window_start, visit.window_end)
    res.window_match = bool(working)
    if not res.window_match:
        res.fail_reason = "no_working_hours_in_window"
        return res

    # --- A + X: open slot with no hard clash --------------------------------
    travel = state.travel(coordinator.coordinator_id, family.family_id)
    slot = find_slot(coordinator, visit, snapshot, travel_minutes=travel)
    res.open_slot = slot is not None
    res.no_calendar_clash = slot is not None
    if slot is None:
        # Distinguish "no free block at all" from "free time exists but the
        # calendar eats it" so the failure is actionable.
        free_ignoring_calendar = find_slot(coordinator, visit, None, travel)
        if free_ignoring_calendar is None:
            res.fail_reason = "no_open_slot"
        else:
            res.open_slot = True
            res.fail_reason = "calendar_clash"
        return res
    res.slot_start, res.slot_end = slot

    # --- soft flags: tentative / working-elsewhere overlap ------------------
    soft: List[str] = []
    if snapshot:
        for block in snapshot.soft_blocks():
            if block.overlaps(*slot):
                soft.append(f"soft_{block.status}")
    if res.provisional:
        soft.append("calendar_stale")
    res.soft_flags = tuple(sorted(set(soft)))

    res.passed = True
    return res


def eligible_pool(
    visit: Visit, state: LabState, cfg: EngineConfig, now: datetime
) -> Tuple[List[FeasibilityResult], List[FeasibilityResult]]:
    """Return (passed, rejected) for every active coordinator."""
    family = state.families[visit.family_id]
    passed: List[FeasibilityResult] = []
    rejected: List[FeasibilityResult] = []
    for coordinator in sorted(state.active_coordinators(), key=lambda c: c.coordinator_id):
        res = evaluate(coordinator, visit, family, state, cfg, now)
        (passed if res.passed else rejected).append(res)
    return passed, rejected


def rescue_pool(
    rejected: Sequence[FeasibilityResult],
) -> List[FeasibilityResult]:
    """Last-resort re-admission when the feasible pool would be empty.

    Only calendar-unavailable candidates are re-admitted, never credential or
    exclusion failures, and each comes back flagged so the coordinator sees an
    explicit "availability unverified" banner and has to confirm by hand. The
    engine never silently assigns against unverified availability.
    """
    out: List[FeasibilityResult] = []
    for res in rejected:
        if res.fail_reason and res.fail_reason.startswith("calendar_unavailable"):
            rescued = FeasibilityResult(**{**res.__dict__})
            rescued.passed = True
            rescued.provisional = True
            rescued.soft_flags = tuple(sorted(set(res.soft_flags) | {"availability_unverified"}))
            rescued.fail_reason = None
            out.append(rescued)
    return out
