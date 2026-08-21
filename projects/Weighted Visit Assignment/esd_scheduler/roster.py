"""The people the board schedules, as data.

Adding, removing or changing a coordinator should never mean editing Python.
The roster lives in ``config/roster.json`` and is loaded here; a row with
``active: false`` disappears from scheduling while the history that references
them stays intact, which is what makes "remove somebody" safe on a system whose
whole point is an audit trail.

Clinical reliability is deliberately not in this file. It lives in the
reliability matrix, transcribed from the lab's manual, and that is the only
place that decides who may run a visit. Saying it in two places is how the two
end up disagreeing.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

CONFIG_PATH = os.path.join("config", "roster.json")


def _config_path() -> str:
    return os.environ.get("ESD_ROSTER_PATH", CONFIG_PATH)


@dataclass
class RosterEntry:
    """One coordinator, exactly as the config file describes them."""

    id: str
    name: str
    manual_name: Optional[str] = None
    active: bool = True
    capacity_hours_week: float = 20.0
    completed_visits: int = 0
    zone: int = 1
    attributes: List[str] = field(default_factory=list)
    credentials: List[str] = field(default_factory=lambda: ["CONSENT", "DRIVING"])
    van_trained: bool = False
    tech_trained: bool = False
    can_tech: bool = True
    in_lab_day: Optional[int] = None
    out_of_hours_count: int = 0
    committed_hours: Optional[float] = None

    @property
    def first_name(self) -> str:
        return (self.manual_name or self.name).split()[0]


@dataclass
class Roster:
    entries: List[RosterEntry] = field(default_factory=list)
    confirmed: bool = False

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Roster":
        path = path or _config_path()
        if not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        out = []
        for row in raw.get("coordinators", []):
            if not row.get("id") or not row.get("name"):
                continue          # a row without both is not a person
            out.append(RosterEntry(
                id=str(row["id"]),
                name=str(row["name"]),
                manual_name=row.get("manual_name"),
                active=bool(row.get("active", True)),
                capacity_hours_week=float(row.get("capacity_hours_week", 20.0)),
                completed_visits=int(row.get("completed_visits", 0)),
                zone=int(row.get("zone", 1)),
                attributes=list(row.get("attributes") or []),
                credentials=list(row.get("credentials")
                                 or ["CONSENT", "DRIVING"]),
                van_trained=bool(row.get("van_trained")),
                tech_trained=bool(row.get("tech_trained")),
                can_tech=bool(row.get("can_tech", True)),
                in_lab_day=row.get("in_lab_day"),
                out_of_hours_count=int(row.get("out_of_hours_count", 0)),
                committed_hours=(float(row["committed_hours"])
                                 if row.get("committed_hours") is not None else None),
            ))
        return cls(entries=out, confirmed=bool(raw.get("confirmed")))

    @property
    def active(self) -> List[RosterEntry]:
        return [e for e in self.entries if e.active]

    def by_id(self) -> Dict[str, RosterEntry]:
        return {e.id: e for e in self.entries}

    def resolve(self, name: str) -> Optional[str]:
        """Coordinator id for a name as the manual writes it, or None.

        Matches the full name, the manual's name, or a unique first name. A
        first name shared by two people resolves to neither: half a match is
        not a match when the result decides whose calendar something lands on.
        """
        want = " ".join(name.lower().replace("-", " ").split())
        for entry in self.entries:
            for candidate in (entry.name, entry.manual_name):
                if candidate and " ".join(
                        candidate.lower().replace("-", " ").split()) == want:
                    return entry.id
        firsts = [e for e in self.entries if e.first_name.lower() == want]
        return firsts[0].id if len(firsts) == 1 else None
