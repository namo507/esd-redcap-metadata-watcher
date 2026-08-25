"""The lab's physical limits, as data.

Three of the manual's rules are not about who is best for a visit. They are
about whether the visit can happen at all:

* there are two NANO tech kits, so two NANO visits at once and no more;
* the lab is shut on Fridays and on university holidays, with the manual
  saying "no exceptions to this" about the holidays;
* a visit that runs more than half an hour outside nine-to-five is an
  out-of-hours visit, and those rotate.

They live in ``config/lab-resources.json`` so the lab can change them without
anyone editing Python. Buying a third kit should be a number, not a release.

One deliberate asymmetry. An empty holiday list means *the board has not been
told the holidays*, not *there are none*. The manual gives the rule but not the
dates, so the board says it cannot check rather than scheduling through them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

CONFIG_PATH = os.path.join("config", "lab-resources.json")


def _config_path() -> str:
    return os.environ.get("ESD_RESOURCES_PATH", CONFIG_PATH)


@dataclass
class Vehicle:
    name: str
    requires_trained_driver: bool = False
    prefers: str = ""
    note: str = ""


@dataclass
class LabResources:
    tech_kits: Dict[str, int] = field(default_factory=dict)
    closed_weekdays: List[int] = field(default_factory=list)
    holidays: List[date] = field(default_factory=list)
    start_hour: float = 9.0
    end_hour: float = 17.0
    working_weekdays: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    grace_minutes: int = 30
    vehicles: List[Vehicle] = field(default_factory=list)
    holidays_known: bool = False
    # Whether a screenshot read applies immediately or waits to be confirmed.
    # A PDF is exact and always applies; this is only about pixel reads.
    auto_confirm_screenshots: bool = True
    confirmed: bool = False

    # -- what stops a visit happening ---------------------------------------

    def kit_ceiling(self, protocol: str) -> Optional[int]:
        """How many of this protocol can run at once, or None if unlimited."""
        return self.tech_kits.get((protocol or "").upper())

    def closed_on(self, when) -> Optional[str]:
        """Why the lab is shut that day, or None if it is open.

        Returns the reason rather than a boolean so the board can say "Friday
        is held for lab meetings" instead of "unavailable", which is the
        difference between a rule someone can weigh and a wall.
        """
        day = when.date() if isinstance(when, datetime) else when
        if day in self.holidays:
            return "a university holiday, and the manual allows no exceptions"
        if day.weekday() in self.closed_weekdays:
            return "a Friday, which is held for lab meetings"
        return None

    def is_out_of_hours(self, start: datetime, end: datetime) -> bool:
        """The manual's definition, to the letter.

        "anything that is scheduled to go beyond 30 minutes outside of
        9am-5pm on Monday-Friday". The grace is what stops a visit that runs
        ten minutes late from being counted as an evening shift and pushing
        somebody down the out-of-hours rotation for nothing.
        """
        grace = timedelta(minutes=self.grace_minutes)
        for moment in (start, end):
            if moment.weekday() not in self.working_weekdays:
                return True
        opens = start.replace(hour=int(self.start_hour),
                              minute=int(round((self.start_hour % 1) * 60)),
                              second=0, microsecond=0)
        closes = end.replace(hour=int(self.end_hour),
                             minute=int(round((self.end_hour % 1) * 60)),
                             second=0, microsecond=0)
        return start < opens - grace or end > closes + grace

    def vehicle_for(self, drive_minutes: float, van_trained_on_visit: int,
                    van_inaccessible: bool = False,
                    van_taken: bool = False) -> tuple:
        """Which vehicle this visit should take, and why.

        The manual's rules, in the order it gives them. Returns
        ``(vehicle_name_or_None, reason)``; None means neither will do, which
        is a real answer when nobody on the visit can drive the van and the
        rental is already out.
        """
        van = next((v for v in self.vehicles if v.name == "van"), None)
        rental = next((v for v in self.vehicles if v.name == "rental"), None)

        if van_inaccessible:
            return ((rental.name if rental else None),
                    "the family's home is marked van inaccessible")
        if van and not van_taken and van_trained_on_visit >= 2:
            return (van.name,
                    "both staff are van-trained, so this visit takes the van")
        if van and not van_taken and van_trained_on_visit >= 1:
            return (van.name, "one staff member is approved to drive the van")
        if rental:
            reason = ("the van is already out" if van_taken
                      else "nobody on this visit is approved to drive the van")
            return (rental.name, reason)
        return (None, "no vehicle is configured that this visit can take")

    def out_of_hours_turn(self, counts: Dict[str, int]) -> Dict[str, bool]:
        """Whose turn it is for an out-of-hours visit, by the manual's rule.

        "Once working an out-of-hours visit, the person should not be
        scheduled for another one until all of the other clinicians/techs have
        gone on one unless absolutely necessary."

        That is a rotation, not a running total. Somebody on two is not
        "slightly ahead" of somebody on one, they are out until the cycle
        restarts. The lab tracks it as a checklist on a Slack canvas and
        unchecks every name once everyone has gone, which is exactly a minimum
        over the counts: whoever is on the lowest count is up, everyone else
        waits.

        ``counts`` maps person to how many they have worked this cycle.
        Returns the same keys mapped to whether they are up.
        """
        if not counts:
            return {}
        floor = min(counts.values())
        return {person: n <= floor for person, n in counts.items()}

    # -- loading -------------------------------------------------------------

    @classmethod
    def load(cls, path: Optional[str] = None) -> "LabResources":
        path = path or _config_path()
        if not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)

        holidays = []
        for text in raw.get("holidays") or []:
            try:
                holidays.append(date.fromisoformat(str(text)))
            except ValueError:
                continue          # a date nobody can read is not a holiday

        hours = raw.get("working_hours") or {}
        return cls(
            tech_kits={str(k).upper(): int(v)
                       for k, v in (raw.get("tech_kits") or {}).items()},
            closed_weekdays=[int(d) for d in (raw.get("closed_weekdays") or [])],
            holidays=holidays,
            start_hour=float(hours.get("start_hour", 9.0)),
            end_hour=float(hours.get("end_hour", 17.0)),
            working_weekdays=[int(d) for d in (hours.get("weekdays")
                                               or [0, 1, 2, 3, 4])],
            grace_minutes=int(hours.get("grace_minutes", 30)),
            vehicles=[
                Vehicle(name=str(v.get("name", "")),
                        requires_trained_driver=bool(v.get("requires_trained_driver")),
                        prefers=str(v.get("prefers", "")),
                        note=str(v.get("note", "")))
                for v in (raw.get("vehicles") or []) if v.get("name")
            ],
            holidays_known=bool(holidays),
            auto_confirm_screenshots=bool(
                (raw.get("screenshot_uploads") or {}).get("auto_confirm", True)),
            confirmed=bool(raw.get("confirmed")),
        )
