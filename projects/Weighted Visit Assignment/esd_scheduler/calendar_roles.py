"""What each overlaid calendar *means* to the scheduler.

An Outlook overlay mixes two kinds of calendar that must never be treated the
same way. A person's calendar is a list of times they are **not** free. A
resource or policy calendar is often the opposite: "Offered Times ESD" is a list
of times a visit **may** be booked, and "Clinician Shifts" is a list of times
cover **exists**. Reading a positive calendar as if it were a busy list inverts
the scheduler silently -- it would rule out exactly the slots the lab set aside
for visits -- so polarity is declared here rather than inferred at the point of
use.

Roles are guessed from the calendar's name and can be corrected in
``config/calendar-roles.json``. Guessing is safe because a wrong guess only ever
downgrades a calendar to "unknown", which is inert.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

ROLE_COORDINATOR = "coordinator"
ROLE_OFFERED = "offered_window"
ROLE_CLINICIAN = "clinician_shift"
ROLE_LAB = "lab_space"
ROLE_OWNER = "owner"
ROLE_UNKNOWN = "unknown"

# positive: an entry marks time that is ALLOWED or COVERED.
# negative: an entry marks time that is TAKEN.
POLARITY = {
    ROLE_COORDINATOR: "negative",
    ROLE_OFFERED: "positive",
    ROLE_CLINICIAN: "positive",
    ROLE_LAB: "negative",
    ROLE_OWNER: "ignored",
    ROLE_UNKNOWN: "ignored",
}

ROLE_LABEL = {
    ROLE_COORDINATOR: "Coordinator",
    ROLE_OFFERED: "Offered times",
    ROLE_CLINICIAN: "Clinician shifts",
    ROLE_LAB: "ESDI lab room",
    ROLE_OWNER: "Export owner",
    ROLE_UNKNOWN: "Unclassified",
}

ROLE_MEANING = {
    ROLE_OFFERED: "a visit may only be offered inside these blocks",
    ROLE_CLINICIAN: "a clinician is on shift inside these blocks",
    ROLE_LAB: "the lab room is already booked inside these blocks",
    ROLE_COORDINATOR: "this person is busy inside these blocks",
    ROLE_OWNER: "the mailbox this calendar was printed from",
    ROLE_UNKNOWN: "not classified, so it affects nothing",
}

_PATTERNS: Sequence[Tuple[str, str]] = (
    (r"offered\s*times", ROLE_OFFERED),
    (r"\boffer(ing)?s?\b.*\besd\b", ROLE_OFFERED),
    (r"clinician\s*shift", ROLE_CLINICIAN),
    (r"\bshifts?\b.*clinic", ROLE_CLINICIAN),
    (r"esdi\s*lab", ROLE_LAB),
    (r"\blab\b.*\broom\b", ROLE_LAB),
    (r"^calendar$", ROLE_OWNER),
)

CONFIG_PATH = os.path.join("config", "calendar-roles.json")


def _config_path() -> str:
    return os.environ.get("ESD_CALENDAR_ROLES_PATH", CONFIG_PATH)


@dataclass
class RoleMap:
    """Declared roles, plus whatever the names imply for anything undeclared.

    ``aliases`` maps a first name the lab uses on its banners to a coordinator
    id. It exists because a nickname is not derivable: "Maggie" may or may not
    be Margaret, and marking the wrong person unavailable is a hard veto on
    someone who was free. Nothing here is guessed -- an unlisted name stays
    unresolved and is reported.
    """

    declared: Dict[str, str] = field(default_factory=dict)
    aliases: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "RoleMap":
        path = path or _config_path()
        if not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls(
            declared={
                str(k): str(v) for k, v in (raw.get("roles") or {}).items()
                if v in POLARITY
            },
            aliases={
                str(k).strip().lower(): str(v)
                for k, v in (raw.get("name_aliases") or {}).items() if v
            },
        )

    def save(self, path: Optional[str] = None) -> None:
        path = path or _config_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "_comment": (
                        "What each overlaid Outlook calendar means. Roles are guessed "
                        "from the calendar name; declare one here to override the "
                        "guess. Polarity matters: offered_window and clinician_shift "
                        "mark time that is ALLOWED, while coordinator and lab_space "
                        "mark time that is TAKEN. Valid roles: "
                        + ", ".join(sorted(POLARITY))
                    ),
                    "roles": self.declared,
                    "name_aliases": self.aliases,
                },
                fh,
                indent=2,
            )

    def role_of(self, label: str, is_roster_name: bool = False) -> str:
        if label in self.declared:
            return self.declared[label]
        return classify(label, is_roster_name)


def classify(label: str, is_roster_name: bool = False) -> str:
    """Best guess at a calendar's role from its printed name.

    A name that matched the roster is a person, whatever it looks like;
    otherwise the name patterns decide, and anything unrecognised stays
    ``unknown`` so it cannot quietly affect a decision.
    """
    if is_roster_name:
        return ROLE_COORDINATOR
    text = " ".join(label.lower().replace(",", " ").split())
    for pattern, role in _PATTERNS:
        if re.search(pattern, text):
            return role
    return ROLE_UNKNOWN


@dataclass
class Interval:
    start: datetime
    end: datetime

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.start < end and start < self.end

    def contains(self, start: datetime, end: datetime) -> bool:
        return self.start <= start and end <= self.end

    def to_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


def merge(intervals: Sequence[Interval]) -> List[Interval]:
    """Union of overlapping intervals, so containment tests are simple."""
    out: List[Interval] = []
    for iv in sorted(intervals, key=lambda i: i.start):
        if out and iv.start <= out[-1].end:
            out[-1] = Interval(out[-1].start, max(out[-1].end, iv.end))
        else:
            out.append(Interval(iv.start, iv.end))
    return out


def covered_by(intervals: Sequence[Interval], start: datetime, end: datetime) -> bool:
    """Whether one window sits wholly inside the union of ``intervals``."""
    for iv in merge(intervals):
        if iv.contains(start, end):
            return True
    return False


def any_overlap(intervals: Sequence[Interval], start: datetime, end: datetime) -> bool:
    return any(iv.overlaps(start, end) for iv in intervals)
