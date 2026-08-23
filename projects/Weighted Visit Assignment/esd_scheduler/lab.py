"""Building a lab state from the roster, with nothing invented.

The demo in ``demo.py`` and a real board need the same coordinators: the roster
in ``config/roster.json`` is the single source for who exists, what they are
signed off on and how many hours they can take. What differs is everything
around them. The demo invents families, visits, busy calendars and committed
hours so the engine has something awkward to chew on. A real board starts with
none of that and gets it from uploaded calendars and entered visits.

Keeping the coordinator half here means the two cannot drift: add somebody to
the roster and they appear in both.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Tuple

from .models import Coordinator, LabState, Visit
from .roster import Roster


def populate_coordinators(state: LabState, now: datetime) -> None:
    """Put every active roster member into ``state``.

    Hire date is the one value derived rather than read: the engine uses it to
    tell a genuinely new coordinator from an experienced one, and the roster
    records experience as a completed-visit count instead of a date. Somebody
    with no completed visits reads as recently hired, which is what makes the
    cold-start path apply to them.
    """
    roster = Roster.load()
    for entry in roster.active:
        state.coordinators[entry.id] = Coordinator(
            coordinator_id=entry.id,
            name=entry.name,
            credentials=set(entry.credentials),
            capacity_hours_week=entry.capacity_hours_week,
            n_completed_visits=entry.completed_visits,
            attributes=set(entry.attributes),
            hire_date=(now - timedelta(
                days=900 if entry.completed_visits else 9)).date(),
            working_blocks=[(d, 9.0, 17.0) for d in range(5)],
            tech_trained=entry.tech_trained,
            van_trained=entry.van_trained,
            in_lab_day=entry.in_lab_day,
            out_of_hours_count=entry.out_of_hours_count,
        )


def build_live(now: datetime) -> Tuple[LabState, List[Visit]]:
    """A board with the real roster and nothing else.

    No families, no visits, no busy time. Those arrive from the two things a
    coordinator actually does: uploading a calendar print and entering a visit.
    Committed hours come from the roster where it states them and are zero
    otherwise, because a made-up starting workload would tilt the burden term
    on a board meant to be real.

    Nobody gets a calendar snapshot here, and that is the point. A missing
    snapshot classifies as expired, so every coordinator starts as *unknown*
    rather than free, and the freshness gate holds them back until a calendar
    has actually been read for them. Uploading one is what creates it.
    """
    state = LabState()
    populate_coordinators(state, now)

    by_id = Roster.load().by_id()
    for cid in state.coordinators:
        entry = by_id.get(cid)
        stated = entry.committed_hours if entry else None
        state.committed_hours[cid] = float(stated) if stated is not None else 0.0

    return state, []
