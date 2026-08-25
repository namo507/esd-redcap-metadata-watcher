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

# Checkpoint names sort by age, not alphabetically: "12m" is after "9m" and
# "3m" is before both. Every range comparison goes through this order.
CHECKPOINT_ORDER = ["1m", "2m", "3m", "6m", "9m", "12m", "24m", "36m", "48m"]


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

    # --- what this person is, and what that lets them do -------------------
    #
    # Roles are a list because people hold several: a coordinator who is also
    # a clinician and can tech is three of them. Anything the board asks about
    # a person -- can they be the clinician, can they tech, do they need
    # checking with first -- is answered from here rather than from their name.
    #
    #   coordinator    schedules visits, has an in-lab day
    #   clinician      can be the clinician on a visit, within solo_from/to
    #   tech           can be the tech on a visit
    #   grad_student   goes on visits on their own calendar's time, and the
    #                  manual says to check with them before offering one
    roles: List[str] = field(default_factory=list)

    # The manual's "Visits Can Do Solo" column: the checkpoint range this
    # person can be THE clinician for. Somebody with no range is not a
    # clinician however many assessments they are reliable in, which is the
    # case for Ramiro (Bayley 9-12m only) and Maggie (still training).
    solo_from: Optional[str] = None
    solo_to: Optional[str] = None

    # Emma's row reads "*only schedule for 36m visits*". A hard allow-list
    # that overrides the range when present.
    only_checkpoints: List[str] = field(default_factory=list)

    # Grad students: "double check with them PRIOR to offering the visit to
    # families". The board can rank them, but it has to say this out loud.
    confirm_before_offering: bool = False

    @property
    def first_name(self) -> str:
        return (self.manual_name or self.name).split()[0]

    @property
    def first_names(self) -> tuple:
        """Every first name this person goes by, deduplicated.

        Somebody can be printed on the calendar under one name and written in
        the manual under another -- the same human, two documents. Both have
        to lead here, or a notice using the name that happens not to be the
        preferred one silently matches nobody.
        """
        seen = []
        for source in (self.manual_name, self.name):
            if not source or not source.split():
                continue
            first = source.split()[0].lower()
            if first not in seen:
                seen.append(first)
        return tuple(seen)


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
                roles=[str(r).strip().lower() for r in (row.get("roles") or [])],
                solo_from=row.get("solo_from"),
                solo_to=row.get("solo_to"),
                only_checkpoints=[str(c) for c in (row.get("only_checkpoints") or [])],
                confirm_before_offering=bool(row.get("confirm_before_offering")),
            ))
        return cls(entries=out, confirmed=bool(raw.get("confirmed")))

    @property
    def active(self) -> List[RosterEntry]:
        return [e for e in self.entries if e.active]

    def with_role(self, role: str) -> List[RosterEntry]:
        """Everyone active who holds a role. The board asks this, not a name."""
        role = role.strip().lower()
        return [e for e in self.active if role in e.roles]

    def can_be_clinician_for(self, entry: RosterEntry, checkpoint: str,
                             order: Optional[List[str]] = None) -> bool:
        """Whether this person can be THE clinician at this checkpoint.

        Three things have to hold, and they are all the manual's:

        * they hold the clinician role at all;
        * if their row names specific checkpoints, this is one of them --
          Emma's "only schedule for 36m visits";
        * otherwise the checkpoint sits inside their "Visits Can Do Solo"
          range. Somebody with no range can never be the clinician, however
          many assessments they are reliable in. Being signed off on one
          assessment is not the same as being able to run the whole visit.

        Reliability in the specific assessments is a separate check, in the
        reliability matrix. This is about the visit as a whole.
        """
        if "clinician" not in entry.roles:
            return False
        if entry.only_checkpoints:
            return checkpoint in entry.only_checkpoints
        sequence = order or CHECKPOINT_ORDER
        if checkpoint not in sequence:
            # A checkpoint this order does not name cannot be placed inside or
            # outside anybody's range. Declining to judge leaves the decision
            # to the assessment chart, which is the check that does apply.
            # Answering False instead would quietly empty the whole pool for a
            # protocol that simply names its timepoints differently.
            return True
        if not entry.solo_from or not entry.solo_to:
            return False
        try:
            lo, hi, at = (sequence.index(entry.solo_from),
                          sequence.index(entry.solo_to),
                          sequence.index(checkpoint))
        except ValueError:
            return True
        return lo <= at <= hi

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
        firsts = [e for e in self.entries if want in e.first_names]
        return firsts[0].id if len(firsts) == 1 else None
