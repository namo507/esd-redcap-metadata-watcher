"""When each family's next visit is due.

The rest of the engine answers "who should take this visit". This module answers
the question that comes before it: *which visit is next, and how late is it?*
A longitudinal study's checkpoints are anchored to a date per family and each
has an acceptance window, so being due is arithmetic, not opinion.

Two rules keep it honest:

* **No anchor, no verdict.** A family with no anchor date gets ``unknown``,
  never ``overdue``. Missing data is not evidence of lateness, and a fabricated
  due date would send someone chasing a family who is perfectly on time.
* **The windows are the lab's, not ours.** ``config/protocol-schedule.json``
  ships provisional offsets so the machinery runs, flagged unconfirmed. Until
  the study team confirms them, every derived date says so.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence

STATUS_COMPLETE = "complete"
STATUS_OVERDUE = "overdue"
STATUS_OPEN = "open"
STATUS_CLOSING = "closing"
STATUS_UPCOMING = "upcoming"
STATUS_UNKNOWN = "unknown"

STATUS_LABEL = {
    STATUS_COMPLETE: "All checkpoints done",
    STATUS_OVERDUE: "Past its window",
    STATUS_OPEN: "In window now",
    STATUS_CLOSING: "Window closing",
    STATUS_UPCOMING: "Not yet due",
    STATUS_UNKNOWN: "No anchor date",
}

# How close to the end of a window counts as "closing".
CLOSING_DAYS = 14

CONFIG_PATH = os.path.join("config", "protocol-schedule.json")


def _config_path() -> str:
    return os.environ.get("ESD_PROTOCOL_SCHEDULE_PATH", CONFIG_PATH)


@dataclass(frozen=True)
class Checkpoint:
    """One scheduled visit in a protocol, as an offset from the family anchor."""

    name: str
    offset_days: int
    window_before: int = 30
    window_after: int = 30

    def target(self, anchor: date) -> date:
        return anchor + timedelta(days=self.offset_days)

    def window(self, anchor: date):
        target = self.target(anchor)
        return (target - timedelta(days=self.window_before),
                target + timedelta(days=self.window_after))

    @property
    def window_days(self) -> int:
        return max(1, self.window_before + self.window_after)


@dataclass
class ProtocolSchedule:
    """Checkpoint timings for every protocol, plus whether anyone confirmed them."""

    checkpoints: Dict[str, List[Checkpoint]] = field(default_factory=dict)
    confirmed: bool = False
    confirmed_by: str = ""
    source: str = "provisional defaults"

    @classmethod
    def load(cls, path: Optional[str] = None) -> "ProtocolSchedule":
        path = path or _config_path()
        if not os.path.exists(path):
            return cls(checkpoints=default_checkpoints())
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        out: Dict[str, List[Checkpoint]] = {}
        for protocol, rows in (raw.get("protocols") or {}).items():
            parsed = []
            for row in rows:
                try:
                    parsed.append(Checkpoint(
                        name=str(row["name"]),
                        offset_days=int(row["offset_days"]),
                        window_before=int(row.get("window_before", 30)),
                        window_after=int(row.get("window_after", 30)),
                    ))
                except (KeyError, TypeError, ValueError):
                    continue
            if parsed:
                out[protocol] = sorted(parsed, key=lambda c: c.offset_days)
        return cls(
            checkpoints=out or default_checkpoints(),
            confirmed=bool(raw.get("confirmed")),
            confirmed_by=raw.get("confirmed_by", ""),
            source=raw.get("source", "config/protocol-schedule.json"),
        )

    def for_protocol(self, protocol: str) -> List[Checkpoint]:
        return self.checkpoints.get(protocol, [])


def default_checkpoints() -> Dict[str, List[Checkpoint]]:
    """Provisional timings, derived from the checkpoint NAMES alone.

    "12mo" plainly means twelve months from the family's anchor; that much is
    readable off the protocol without knowing the study's paperwork. The
    acceptance windows are not readable off anything, so they get a symmetric
    +/- 30 days and the whole table is marked unconfirmed until the study team
    replaces it.
    """
    from .models import Protocol

    out: Dict[str, List[Checkpoint]] = {}
    for name, protocol in Protocol.default_table().items():
        rows = []
        for label in protocol.checkpoint_sequence:
            rows.append(Checkpoint(
                name=label,
                offset_days=months_from_label(label) * 30,
                window_before=30,
                window_after=30,
            ))
        out[name] = sorted(rows, key=lambda c: c.offset_days)
    return out


def months_from_label(label: str) -> int:
    """Months implied by a checkpoint name; baseline and anything odd are zero."""
    digits = "".join(ch for ch in label if ch.isdigit())
    return int(digits) if digits else 0


# ---------------------------------------------------------------------------
# The per-family answer
# ---------------------------------------------------------------------------


@dataclass
class NextVisit:
    """The next checkpoint owed to one family, and how much time is left."""

    family_id: str
    protocol: str
    checkpoint: Optional[str]
    status: str
    target_date: Optional[date] = None
    window_start: Optional[date] = None
    window_end: Optional[date] = None
    days_remaining: Optional[int] = None
    urgency: float = 0.0
    completed: int = 0
    total: int = 0
    anchor: Optional[date] = None
    confirmed_schedule: bool = False

    def to_dict(self) -> dict:
        return {
            "family_id": self.family_id,
            "protocol": self.protocol,
            "checkpoint": self.checkpoint,
            "status": self.status,
            "status_label": STATUS_LABEL.get(self.status, self.status),
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "days_remaining": self.days_remaining,
            "urgency": round(self.urgency, 3),
            "completed": self.completed,
            "total": self.total,
            "confirmed_schedule": self.confirmed_schedule,
        }


def urgency_of(status: str, days_remaining: Optional[int], window_days: int) -> float:
    """Bounded pressure in [0, 1] that rises as the window closes.

    Linear in the fraction of the window still left, pinned to 1.0 once the
    window has passed and 0.0 before it opens. Linear is deliberate: anything
    steeper would need a justification from the study team about how the cost of
    lateness actually grows, and inventing that curve would put a made-up number
    in front of a scheduling decision.
    """
    if status == STATUS_OVERDUE:
        return 1.0
    if status in (STATUS_COMPLETE, STATUS_UNKNOWN, STATUS_UPCOMING):
        return 0.0
    if days_remaining is None:
        return 0.0
    span = max(1, window_days)
    return max(0.0, min(1.0, 1.0 - (days_remaining / span)))


def next_visit_for(
    family,
    history: Sequence,
    now: datetime,
    schedule: Optional[ProtocolSchedule] = None,
) -> NextVisit:
    """Which checkpoint this family owes next, and how urgent it is."""
    schedule = schedule or ProtocolSchedule.load()
    checkpoints = schedule.for_protocol(family.protocol)
    done = {
        v.checkpoint for v in history
        if v.family_id == family.family_id and not getattr(v, "no_show", False)
    }
    anchor = getattr(family, "anchor_date", None)
    if isinstance(anchor, datetime):
        anchor = anchor.date()

    base = NextVisit(
        family_id=family.family_id,
        protocol=family.protocol,
        checkpoint=None,
        status=STATUS_UNKNOWN,
        completed=len([c for c in checkpoints if c.name in done]),
        total=len(checkpoints),
        anchor=anchor,
        confirmed_schedule=schedule.confirmed,
    )

    remaining = [c for c in checkpoints if c.name not in done]
    if not checkpoints:
        return base
    if not remaining:
        base.status = STATUS_COMPLETE
        return base

    nxt = remaining[0]
    base.checkpoint = nxt.name
    if anchor is None:
        # No anchor means no arithmetic. Reporting "overdue" here would invent
        # lateness out of a missing field.
        base.status = STATUS_UNKNOWN
        return base

    start, end = nxt.window(anchor)
    today = now.date()
    base.target_date = nxt.target(anchor)
    base.window_start, base.window_end = start, end
    base.days_remaining = (end - today).days

    if today > end:
        base.status = STATUS_OVERDUE
    elif today < start:
        base.status = STATUS_UPCOMING
        base.days_remaining = (start - today).days
    elif base.days_remaining <= CLOSING_DAYS:
        base.status = STATUS_CLOSING
    else:
        base.status = STATUS_OPEN

    base.urgency = urgency_of(base.status, base.days_remaining, nxt.window_days)
    return base


def upcoming(
    families: Dict[str, object],
    history: Sequence,
    now: datetime,
    schedule: Optional[ProtocolSchedule] = None,
) -> List[NextVisit]:
    """Every family's next owed visit, most urgent first.

    Ordering puts anything actionable above anything that is not: overdue and
    closing windows first, then open ones, with upcoming, unknown and complete
    sinking to the bottom. Within a tier the earlier window wins.
    """
    schedule = schedule or ProtocolSchedule.load()
    rows = [next_visit_for(f, history, now, schedule) for f in families.values()]
    tier = {
        STATUS_OVERDUE: 0, STATUS_CLOSING: 1, STATUS_OPEN: 2,
        STATUS_UPCOMING: 3, STATUS_UNKNOWN: 4, STATUS_COMPLETE: 5,
    }
    rows.sort(key=lambda r: (
        tier.get(r.status, 9),
        -r.urgency,
        r.window_end or date.max,
        r.family_id,
    ))
    return rows
