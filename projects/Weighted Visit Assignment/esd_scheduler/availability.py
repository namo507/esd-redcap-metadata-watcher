"""Who is free, slot by slot, once every coordinator's calendar is in.

This is the join the per-coordinator sync exists to produce. Each upload
answers "when is this one person busy"; the board needs the other question --
"for this slot, who could go" -- and that is only answerable once the
individual answers are combined.

The three-state rule from ``constraints.evidence_state`` carries through
unchanged, and it is the whole reason this is trustworthy: a coordinator whose
calendar has not been synced is **unknown**, never free. A grid that quietly
promoted "no data" to "available" would look complete and send someone to a
family at a time nobody had checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence

from .constraints import (
    EVIDENCE_CLEAR,
    EVIDENCE_CONFLICT,
    EVIDENCE_INSUFFICIENT,
    evidence_state,
)

DEFAULT_SLOT_MINUTES = 30
DEFAULT_DAY_START = 8.0
DEFAULT_DAY_END = 18.0


@dataclass
class Slot:
    """One time block, and where each coordinator stands in it."""

    start: datetime
    end: datetime
    free: List[str] = field(default_factory=list)
    busy: List[str] = field(default_factory=list)
    unknown: List[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """Share of the team this slot has an actual answer for."""
        total = len(self.free) + len(self.busy) + len(self.unknown)
        return 0.0 if not total else (len(self.free) + len(self.busy)) / total

    def to_dict(self, names: Optional[Dict[str, str]] = None) -> dict:
        names = names or {}
        return {
            "start": self.start.isoformat(timespec="minutes"),
            "end": self.end.isoformat(timespec="minutes"),
            "label": f"{self.start:%H:%M}",
            "free": [names.get(c, c) for c in self.free],
            "busy": [names.get(c, c) for c in self.busy],
            "unknown": [names.get(c, c) for c in self.unknown],
            "free_ids": list(self.free),
            "n_free": len(self.free),
            "coverage": round(self.coverage, 3),
        }


def day_grid(
    state,
    day: date,
    now: datetime,
    slot_minutes: int = DEFAULT_SLOT_MINUTES,
    start_hour: float = DEFAULT_DAY_START,
    end_hour: float = DEFAULT_DAY_END,
    coordinators: Optional[Sequence[str]] = None,
) -> List[Slot]:
    """Free / busy / unknown for every coordinator, slot by slot, for one day."""
    ids = list(coordinators) if coordinators is not None else [
        c.coordinator_id for c in state.active_coordinators()
    ]
    cursor = datetime.combine(day, datetime.min.time()) + timedelta(hours=start_hour)
    finish = datetime.combine(day, datetime.min.time()) + timedelta(hours=end_hour)
    step = timedelta(minutes=slot_minutes)

    slots: List[Slot] = []
    while cursor < finish:
        slot = Slot(start=cursor, end=cursor + step)
        for cid in ids:
            verdict = evidence_state(cid, state, slot.start, slot.end, now)
            if verdict == EVIDENCE_CLEAR:
                slot.free.append(cid)
            elif verdict == EVIDENCE_CONFLICT:
                slot.busy.append(cid)
            else:
                slot.unknown.append(cid)
        slots.append(slot)
        cursor += step
    return slots


def week_grid(
    state,
    monday: date,
    now: datetime,
    days: int = 5,
    **kwargs,
) -> List[dict]:
    """The same, across a working week."""
    out = []
    names = {c.coordinator_id: c.name for c in state.active_coordinators()}
    for offset in range(days):
        day = monday + timedelta(days=offset)
        slots = day_grid(state, day, now, **kwargs)
        out.append({
            "day": day.isoformat(),
            "label": day.strftime("%a %-d %b"),
            "slots": [s.to_dict(names) for s in slots],
            "best": max((len(s.free) for s in slots), default=0),
        })
    return out


def coverage_report(state, now: datetime, fresh_minutes: float = 90.0) -> dict:
    """Whose calendar the board actually has, and how current it is.

    The point of a per-coordinator sync is knowing when it is *incomplete*. A
    missing person is not a gap in a report, it is somebody who will be shown as
    unavailable for every slot until their calendar arrives, so the board says
    plainly who is still outstanding.
    """
    rows = []
    for coord in sorted(state.active_coordinators(), key=lambda c: c.name):
        snapshot = state.calendars.get(coord.coordinator_id)
        if snapshot is None or not snapshot.sync_ok:
            rows.append({
                "coordinator_id": coord.coordinator_id, "name": coord.name,
                "state": "missing", "age_minutes": None, "blocks": 0,
            })
            continue
        age = (now - snapshot.fetched_at).total_seconds() / 60.0
        rows.append({
            "coordinator_id": coord.coordinator_id,
            "name": coord.name,
            "state": "current" if age <= fresh_minutes else "stale",
            "age_minutes": round(age),
            "blocks": len(snapshot.blocks),
        })
    counted = {k: sum(1 for r in rows if r["state"] == k)
               for k in ("current", "stale", "missing")}
    return {
        "rows": rows,
        "counts": counted,
        "complete": counted["missing"] == 0 and counted["stale"] == 0,
        "outstanding": [r["name"] for r in rows if r["state"] != "current"],
    }
