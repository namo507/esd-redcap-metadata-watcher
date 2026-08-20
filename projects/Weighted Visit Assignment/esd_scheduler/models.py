"""Domain objects.

Deliberately plain dataclasses: the engine is meant to be readable by a
coordinator, not only by an engineer. Nothing here talks to a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Protocol:
    """A study arm and the credentials it demands.

    Adding a third study is a data change (one more Protocol row), never a code
    change. NICO and NANO differ on ADOS and consent responsibilities.
    """

    name: str
    required_credentials: FrozenSet[str]
    checkpoint_sequence: Tuple[str, ...] = ()

    @staticmethod
    def default_table() -> Dict[str, "Protocol"]:
        return {
            "NICO": Protocol(
                name="NICO",
                required_credentials=frozenset({"ADOS", "CONSENT", "DRIVING"}),
                checkpoint_sequence=("6mo", "12mo", "18mo", "24mo"),
            ),
            "NANO": Protocol(
                name="NANO",
                required_credentials=frozenset({"CONSENT", "DRIVING", "EEG"}),
                checkpoint_sequence=("baseline", "3mo", "9mo", "18mo"),
            ),
        }


@dataclass
class Coordinator:
    coordinator_id: str
    name: str
    credentials: Set[str] = field(default_factory=set)
    capacity_hours_week: float = 20.0
    hire_date: Optional[date] = None
    n_completed_visits: int = 0
    attributes: Set[str] = field(default_factory=set)  # e.g. {"spanish", "female"}
    active: bool = True

    # --- Master prompt §7 staff fields --------------------------------------
    tech_trained: bool = False
    van_trained: bool = False
    in_lab_day: Optional[int] = None   # weekday index; cannot go off-site that day
    out_of_hours_count: int = 0        # rolling tally, drives the equity factor
    # Declared working availability, as (weekday, start_hour, end_hour) triples.
    working_blocks: List[Tuple[int, float, float]] = field(default_factory=list)

    def holds(self, required: Iterable[str]) -> bool:
        return set(required).issubset(self.credentials)

    def missing_credentials(self, required: Iterable[str]) -> Set[str]:
        return set(required) - self.credentials


@dataclass
class Family:
    family_id: str
    protocol: str
    zone: int = 0
    # +1 continuity preferred (lab default), -1 fresh face preferred.
    sigma: int = 1
    preferred_coordinators: Set[str] = field(default_factory=set)
    soft_avoid: Set[str] = field(default_factory=set)
    hard_exclusions: Set[str] = field(default_factory=set)
    # Attribute requirements, e.g. {"spanish"}; satisfied ones lift Omega.
    required_attributes: Set[str] = field(default_factory=set)
    pi_hold: bool = False

    # --- Master prompt §7 participant fields --------------------------------
    # NDD cross-collaboration visits may only be taken by staff certified on
    # NDD measures, and they run 60 minutes longer than the base visit length.
    is_ndd_cross_collab: bool = False
    preferred_contact_method: str = "Email"   # Text | Email | Call
    scheduling_notes: str = ""                # free text, never parsed
    drive_time_minutes: float = 0.0           # one way, from the stored address
    van_inaccessible: bool = False
    childcare_needed: bool = False

    @property
    def has_stated_preference(self) -> bool:
        return bool(
            self.preferred_coordinators or self.soft_avoid or self.required_attributes
        )


@dataclass
class Visit:
    """An open visit request awaiting assignment."""

    visit_id: str
    family_id: str
    protocol: str
    checkpoint: str
    window_start: datetime
    window_end: datetime
    duration_hours: float = 2.0
    priority: float = 1.0

    @property
    def window_days(self) -> int:
        return max(1, (self.window_end.date() - self.window_start.date()).days + 1)


@dataclass
class CompletedVisit:
    visit_id: str
    family_id: str
    coordinator_id: str
    when: datetime
    protocol: str
    checkpoint: str
    duration_hours: float = 2.0
    travel_minutes: float = 0.0
    no_show: bool = False
    protocol_deviation: bool = False
    family_satisfaction: Optional[int] = None


@dataclass(frozen=True)
class BusyBlock:
    start: datetime
    end: datetime
    # Microsoft Graph getSchedule vocabulary; oof is a hard block, tentative and
    # workingElsewhere are soft (pass but flag).
    status: str = "busy"  # free | tentative | busy | oof | workingElsewhere

    HARD_STATUSES = ("busy", "oof")

    def is_hard(self) -> bool:
        return self.status in ("busy", "oof")

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.start < end and start < self.end


@dataclass(frozen=True)
class WorkingHours:
    """A coordinator's declared working envelope, as Outlook reports it.

    This is the answer to "does an empty calendar mean available?". It does not.
    An empty slot at 19:00 is outside the working envelope and is not
    availability; an empty slot at 10:00 is. Graph returns this object alongside
    the free/busy view in the same getSchedule call, so it costs nothing extra.
    """

    days_of_week: FrozenSet[int] = frozenset({0, 1, 2, 3, 4})  # Monday = 0
    start_hour: float = 8.0
    end_hour: float = 17.0
    time_zone: Optional[str] = None

    _DAY_INDEX = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }

    @classmethod
    def from_graph(cls, node: Optional[dict]) -> Optional["WorkingHours"]:
        if not node:
            return None
        days = frozenset(
            cls._DAY_INDEX[d.lower()]
            for d in node.get("daysOfWeek", [])
            if d.lower() in cls._DAY_INDEX
        )
        tz = (node.get("timeZone") or {}).get("name")
        return cls(
            days_of_week=days or frozenset({0, 1, 2, 3, 4}),
            start_hour=_hhmmss_to_hours(node.get("startTime"), 8.0),
            end_hour=_hhmmss_to_hours(node.get("endTime"), 17.0),
            time_zone=tz,
        )

    def contains(self, moment: datetime) -> bool:
        if moment.weekday() not in self.days_of_week:
            return False
        hours = moment.hour + moment.minute / 60.0 + moment.second / 3600.0
        return self.start_hour <= hours < self.end_hour

    def covers(self, start: datetime, end: datetime) -> bool:
        """True only when the whole span sits inside the working envelope."""
        if start.date() != end.date():
            return False
        if start.weekday() not in self.days_of_week:
            return False
        s = start.hour + start.minute / 60.0
        e = end.hour + end.minute / 60.0
        return self.start_hour <= s and e <= self.end_hour

    def as_blocks(self) -> List[Tuple[int, float, float]]:
        """Shape expected by Coordinator.working_blocks."""
        return [(d, self.start_hour, self.end_hour) for d in sorted(self.days_of_week)]


def _hhmmss_to_hours(value: Optional[str], default: float) -> float:
    if not value:
        return default
    try:
        parts = value.split(":")
        return int(parts[0]) + int(parts[1]) / 60.0
    except (ValueError, IndexError):
        return default


# Three states, never two. "No evidence" is a distinct answer from "free", and
# treating it as availability is the double-booking failure mode the meeting
# notes flagged repeatedly.
EVIDENCE_CLEAR = "clear"
EVIDENCE_CONFLICT = "conflict"
EVIDENCE_INSUFFICIENT = "insufficient"


@dataclass
class CalendarBlock:
    """One busy block detected from a captured calendar image.

    Unreviewed blocks are evidence of nothing. They are recorded so a human can
    confirm or reject them, and they are excluded from the availability check
    until they are, which is what makes the three-state answer honest.
    """

    coordinator_id: str
    start: datetime
    end: datetime
    reviewed: bool = False
    confirmed: bool = True      # meaningful only once reviewed
    source_hash: str = ""       # the image this came from, for audit
    run_id: str = ""

    def counts_as_evidence(self) -> bool:
        return self.reviewed and self.confirmed


@dataclass
class CalendarSyncRun:
    """One capture of one coordinator's calendar. Never a combined capture."""

    run_id: str
    coordinator_id: str
    captured_at: datetime
    view_type: str              # day | work_week  (month is rejected upstream)
    source_hash: str = ""
    blocks: List[CalendarBlock] = field(default_factory=list)
    auto_committed: bool = False

    @property
    def reviewed_count(self) -> int:
        return sum(1 for b in self.blocks if b.reviewed)

    @property
    def correction_rate(self) -> float:
        """Share of reviewed blocks a human rejected.

        This is the number that eventually justifies auto-commit for one
        person: a theme that has read cleanly for several runs has earned it.
        """
        reviewed = [b for b in self.blocks if b.reviewed]
        if not reviewed:
            return 0.0
        return sum(1 for b in reviewed if not b.confirmed) / len(reviewed)


@dataclass
class CalendarSnapshot:
    """What we last heard from Outlook / Google for one coordinator."""

    coordinator_id: str
    provider: str  # msgraph | google | manual | mock
    fetched_at: datetime
    blocks: List[BusyBlock] = field(default_factory=list)
    sync_ok: bool = True
    error_code: Optional[str] = None
    working_hours: Optional[WorkingHours] = None

    def age_seconds(self, now: datetime) -> float:
        return max(0.0, (now - self.fetched_at).total_seconds())

    def hard_blocks(self) -> List[BusyBlock]:
        return [b for b in self.blocks if b.is_hard()]

    def soft_blocks(self) -> List[BusyBlock]:
        return [b for b in self.blocks if not b.is_hard() and b.status != "free"]


# ---------------------------------------------------------------------------
# The world the engine scores against
# ---------------------------------------------------------------------------


@dataclass
class LabState:
    """Everything the engine needs, assembled by the caller from REDCap/Excel."""

    coordinators: Dict[str, Coordinator] = field(default_factory=dict)
    families: Dict[str, Family] = field(default_factory=dict)
    protocols: Dict[str, Protocol] = field(default_factory=Protocol.default_table)
    history: List[CompletedVisit] = field(default_factory=list)
    calendars: Dict[str, CalendarSnapshot] = field(default_factory=dict)
    # Round-trip travel minutes, keyed (coordinator_id, family_id).
    travel_minutes: Dict[Tuple[str, str], float] = field(default_factory=dict)
    travel_source: Dict[Tuple[str, str], str] = field(default_factory=dict)
    # Hours already committed in the current period, keyed coordinator_id.
    committed_hours: Dict[str, float] = field(default_factory=dict)
    # Visits already provisionally assigned in this run (so batch runs stay honest).
    pending: Dict[str, List[Visit]] = field(default_factory=dict)

    # -- derived views -------------------------------------------------------

    def travel(self, coordinator_id: str, family_id: str, default: float = 45.0) -> float:
        return self.travel_minutes.get((coordinator_id, family_id), default)

    def prior_visits(self, coordinator_id: str, family_id: str) -> List[CompletedVisit]:
        return [
            h
            for h in self.history
            if h.coordinator_id == coordinator_id and h.family_id == family_id
        ]

    def n_prior(self, coordinator_id: str, family_id: str) -> int:
        return len(self.prior_visits(coordinator_id, family_id))

    def days_since_family_contact(
        self, coordinator_id: str, family_id: str, now: datetime
    ) -> Optional[float]:
        """Days since this coordinator last saw this family.

        Returns None when they have never met. None means *undefined*, and the
        continuity index never evaluates it in that case, because familiarity is
        already zero. This is the v2 cold-start bug fixed at the source: an
        undefined value must not be silently treated as an extreme one.
        """
        visits = self.prior_visits(coordinator_id, family_id)
        if not visits:
            return None
        last = max(v.when for v in visits)
        return max(0.0, (now - last).total_seconds() / 86400.0)

    def did_previous_checkpoint(
        self, coordinator_id: str, family_id: str, protocol: str, checkpoint: str
    ) -> bool:
        """True when this coordinator ran the family's immediately prior checkpoint
        in the same protocol arm. This is a rater-consistency claim (ADOS), which
        is why it survives as its own criterion rather than folding into Phi."""
        proto = self.protocols.get(protocol)
        if proto is None or checkpoint not in proto.checkpoint_sequence:
            return False
        idx = proto.checkpoint_sequence.index(checkpoint)
        if idx == 0:
            return False
        previous = proto.checkpoint_sequence[idx - 1]
        return any(
            h.coordinator_id == coordinator_id
            and h.family_id == family_id
            and h.protocol == protocol
            and h.checkpoint == previous
            for h in self.history
        )

    def committed(self, coordinator_id: str) -> float:
        base = self.committed_hours.get(coordinator_id, 0.0)
        extra = sum(v.duration_hours for v in self.pending.get(coordinator_id, []))
        return base + extra

    def visits_this_week(self, coordinator_id: str, now: datetime) -> int:
        week_start = now - timedelta(days=now.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        done = sum(
            1
            for h in self.history
            if h.coordinator_id == coordinator_id and h.when >= week_start
        )
        return done + len(self.pending.get(coordinator_id, []))

    def rolling_travel_minutes(
        self, coordinator_id: str, now: datetime, days: int = 28
    ) -> float:
        cutoff = now - timedelta(days=days)
        return sum(
            h.travel_minutes
            for h in self.history
            if h.coordinator_id == coordinator_id and h.when >= cutoff
        )

    def active_coordinators(self) -> List[Coordinator]:
        return [c for c in self.coordinators.values() if c.active]


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class FeasibilityResult:
    coordinator_id: str
    window_match: bool = False
    open_slot: bool = False
    no_calendar_clash: bool = False
    no_family_conflict: bool = False
    credential_match: bool = False
    calendar_fresh: bool = False
    ramp_ok: bool = False
    passed: bool = False
    fail_reason: Optional[str] = None
    calendar_status: str = "unknown"
    calendar_cache_age_s: Optional[float] = None
    missing_credentials: FrozenSet[str] = frozenset()
    provisional: bool = False  # stale-but-usable calendar
    slot_start: Optional[datetime] = None
    slot_end: Optional[datetime] = None
    soft_flags: Tuple[str, ...] = ()


@dataclass
class ComponentScores:
    phi: float = 0.0
    omega: float = 0.5
    psi: float = 0.0
    p: float = 0.0
    # raw inputs kept for replay and for the override waterfall
    phi_raw_R: float = 0.0
    k_prior_visits: int = 0
    days_since_family_contact: Optional[float] = None
    committed_hours: float = 0.0
    capacity_hours: float = 0.0
    utilization: float = 0.0
    travel_minutes: float = 0.0
    burden_hours: float = 0.0
    n_c_total_visits: int = 0
    is_cold_start: bool = False


@dataclass
class CandidateScore:
    coordinator_id: str
    coordinator_name: str
    feasibility: FeasibilityResult
    components: ComponentScores = field(default_factory=ComponentScores)
    contributions: Dict[str, float] = field(default_factory=dict)
    final_score: float = 0.0
    rank_position: Optional[int] = None
    gap_to_next: Optional[float] = None
    review_band_flag: bool = False
    in_shortlist: bool = False
    selection_stability: Optional[float] = None
    tie_break_applied: bool = False
    tie_break_rule: Optional[str] = None
    tie_break_seed: Optional[int] = None


@dataclass
class RankedPool:
    visit: Visit
    scored_at: datetime
    candidates: List[CandidateScore]          # ranked, feasible only
    rejected: List[CandidateScore]            # failed Layer 1, kept for the audit log
    family_sigma: int = 1
    needs_manual_scheduling: bool = False
    pool_starvation: bool = False
    halt_reason: Optional[str] = None
    epsilon_used: float = 0.05
    weight_vector_id: str = ""
    config_fingerprint: str = ""
    surprise_codes: List[str] = field(default_factory=list)

    @property
    def top(self) -> Optional[CandidateScore]:
        return self.candidates[0] if self.candidates else None

    def shortlist(self, k: int = 3) -> List[CandidateScore]:
        return self.candidates[:k]

    def all_rows(self) -> List[CandidateScore]:
        """Every coordinator considered, feasible or not.

        The whole pool is logged, not only the winner. Without this the
        conditional-logit weight check and every rank-reversal analysis become
        impossible after the fact, and they cost nothing to enable up front.
        """
        return list(self.candidates) + list(self.rejected)
