"""The NANO study's participants, as the board's dropdowns need them.

The board's own data is coordinators, calendars and weights. The participants
and their anchor dates live in REDCap, and this is the join: it turns the
cached export into the two lists a scheduler picks from -- which family, and
which of that family's time points -- and hands the chosen window to the same
engine everything else uses.

Nothing here calls REDCap. The sync writes a cache and this reads it, so a
scheduling screen never depends on a study API being up, and the token is not
touched by anything a browser can reach.

WHAT IS AND IS NOT SENT TO A BROWSER. A participant ID and a visit window,
because choosing a visit is impossible without them. Never a date of birth or
a due date: those are what the windows were *derived from*, the derivation
happens here, and a screen that never receives them cannot leak them.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from esd_scheduler import redcap

#: The order a scheduler cares about. A window that has closed is the one
#: somebody needs to be told about; one three months out is not.
STATE_ORDER = {"missed": 0, "open": 1, "upcoming": 2, "done": 3}

STATE_LABEL = {
    "missed": "Window closed",
    "open": "Open now",
    "upcoming": "Not open yet",
    "done": "Already done",
    "no anchor date on file": "No anchor date",
}


def _families() -> List[redcap.NanoFamily]:
    cached = redcap.cached_families()
    out = []
    for row in cached.get("families") or []:
        out.append(redcap.NanoFamily(
            family_id=row.get("family_id", ""),
            participant_status=row.get("participant_status", "TD"),
            birth_date=row.get("birth_date"),
            due_date=row.get("due_date"),
            unenrolled=bool(row.get("unenrolled")),
            completed=list(row.get("completed") or []),
        ))
    return out


def summary(today: Optional[date] = None) -> dict:
    """Every family, with the one window that matters, for the first dropdown.

    Sorted by how urgent the next window is rather than by id: a list of two
    hundred participants in numerical order is a list nobody can act on.
    """
    today = today or date.today()
    cached = redcap.cached_families()
    rows = []
    for fam in _families():
        window = redcap.next_window(fam, today)
        rows.append({
            "family_id": fam.family_id,
            "participant_status": fam.participant_status,
            "completed_count": len(fam.completed),
            "next": window,
            "state": (window or {}).get("status", "none"),
            "state_label": STATE_LABEL.get((window or {}).get("status", ""), ""),
            # Deliberately no birth_date and no due_date. See the module note.
        })
    rows.sort(key=lambda r: (STATE_ORDER.get(r["state"], 9),
                             (r["next"] or {}).get("window_start") or "",
                             r["family_id"]))
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {
        "fetched_at": cached.get("fetched_at"),
        "reason": cached.get("reason"),
        "count": len(rows),
        "counts_by_state": counts,
        "families": rows,
    }


def family_windows(family_id: str, today: Optional[date] = None) -> dict:
    """Every checkpoint for one family, for the second dropdown.

    Returned whole rather than filtered to the open one, because a scheduler
    catching up on a missed 9m needs to see it is missed, and one booking
    ahead needs to see what is not open yet. The states are labelled here so
    the page does not have to know the vocabulary.
    """
    today = today or date.today()
    fam = next((f for f in _families() if f.family_id == family_id), None)
    if fam is None:
        return {"family_id": family_id, "found": False, "windows": []}
    windows = redcap.visit_windows(fam, today)
    for window in windows:
        window["state_label"] = STATE_LABEL.get(window["status"], window["status"])
        window["selectable"] = bool(window["window_start"]) and not window["done"]
    return {
        "family_id": fam.family_id,
        "found": True,
        "participant_status": fam.participant_status,
        "completed": sorted(fam.completed, key=lambda c: int(
            "".join(ch for ch in c if ch.isdigit()) or 0)),
        "windows": windows,
    }
