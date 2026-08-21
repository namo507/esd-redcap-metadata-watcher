"""Turn an uploaded Outlook PDF print into calendar evidence, at its true tier.

An upload is only worth what its export type can support, and the two types the
lab actually produces are worth very different things:

  * **Work week / day print** (tier 2). A time gutter is on the page, so an
    event box's height *is* its duration. These yield real intervals, become
    reviewable ``CalendarBlock`` rows, and may veto a candidate.
  * **Month grid** (tier 3). Outlook prints a start time and nothing else: no
    end times, and cells silently truncate at about seven rows with no "+N
    more" marker, cutting afternoons first. A month grid may inform a
    coordinator's day-level load. It may never confirm availability and may
    never veto, because both would require intervals it does not contain.

The overlay problem. These exports stack several people's calendars on one
page, and the only thing distinguishing them is the colour chip — the header
lists the calendar names as plain text with no swatches. So attribution needs a
one-time hue -> coordinator map, confirmed by a human. Until that map is
confirmed, entries are parsed and counted but attributed to nobody, because a
guessed attribution is worse than an absent one: it moves the wrong person's
workload.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from .ingest_outlook_pdf import (
    VIEW_DAY,
    VIEW_MONTH,
    VIEW_WORK_WEEK,
    DaySignal,
    PdfIngestResult,
    day_signals,
    joint_day_pressure,
    load,
)
from datetime import date, timedelta
from .calendar_roles import (
    POLARITY,
    ROLE_CLINICIAN,
    ROLE_COORDINATOR,
    ROLE_LAB,
    ROLE_LABEL,
    ROLE_MEANING,
    ROLE_OFFERED,
    ROLE_OWNER,
    ROLE_UNKNOWN,
    Interval,
    RoleMap,
    merge,
)
from .models import CalendarBlock, CalendarSyncRun

TIER_GRAPH = 1
TIER_TIMED_EXPORT = 2
TIER_MONTH_GRID = 3
TIER_IMAGE = 4

TIER_RULES = {
    TIER_GRAPH: "may confirm availability and may veto",
    TIER_TIMED_EXPORT: "may veto once reviewed; intervals are real",
    TIER_MONTH_GRID: "advisory load signal only; may never confirm or veto",
    TIER_IMAGE: "measured in pixels, so every block needs confirming first",
}

def _color_map_path() -> str:
    """Where the confirmed legend lives; overridable so tests never touch config/."""
    return os.environ.get(
        "ESD_COLOR_MAP_PATH", os.path.join("config", "calendar-colors.json")
    )


COLOR_MAP_PATH = _color_map_path()


# ---------------------------------------------------------------------------
# The hue -> coordinator map
# ---------------------------------------------------------------------------


@dataclass
class ColorMap:
    """Which overlaid calendar each colour chip belongs to.

    ``confirmed`` is the gate. An unconfirmed map is treated as no map at all
    rather than as a hint, so nobody's workload moves on a guess.
    """

    mapping: Dict[str, str] = field(default_factory=dict)   # hue -> coordinator_id
    confirmed: bool = False
    confirmed_by: str = ""
    confirmed_at: Optional[str] = None

    @classmethod
    def load(cls, path: Optional[str] = None) -> "ColorMap":
        path = path or _color_map_path()
        if not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls(
            mapping={k: v for k, v in (raw.get("map") or {}).items() if v},
            confirmed=bool(raw.get("confirmed")),
            confirmed_by=raw.get("confirmed_by", ""),
            confirmed_at=raw.get("confirmed_at"),
        )

    def save(self, path: Optional[str] = None) -> None:
        path = path or _color_map_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "_comment": (
                        "Maps an Outlook calendar colour chip to a coordinator id. "
                        "Outlook prints no legend, so this cannot be derived from the "
                        "PDF and must be confirmed by someone who can see the live "
                        "Outlook overlay. Until 'confirmed' is true, uploads parse but "
                        "attribute to nobody."
                    ),
                    "confirmed": self.confirmed,
                    "confirmed_by": self.confirmed_by,
                    "confirmed_at": self.confirmed_at,
                    "map": self.mapping,
                },
                fh,
                indent=2,
            )

    def resolve(self, hue: Optional[str]) -> Optional[str]:
        if hue is None or not self.confirmed:
            return None
        return self.mapping.get(hue)


def suggest_roster_matches(
    calendar_names: Sequence[str], coordinators: Dict[str, object]
) -> Dict[str, Optional[str]]:
    """Match printed calendar labels to roster ids by name, never by position.

    Outlook prints 'Bell, Margaret'; the roster says 'Margaret Bell'. That is a
    safe normalisation. The *order* of the header labels is not safe: it does
    not track the colour order, so pairing them by index would invent an
    attribution. Unmatched labels come back as None for a human to resolve.
    """
    by_norm = {}
    for cid, coord in coordinators.items():
        by_norm[_norm_name(getattr(coord, "name", str(coord)))] = cid

    out: Dict[str, Optional[str]] = {}
    for label in calendar_names:
        clean = label.strip()
        if "," in clean:
            surname, _, forename = clean.partition(",")
            candidate = f"{forename.strip()} {surname.strip()}"
        else:
            candidate = clean
        out[label] = by_norm.get(_norm_name(candidate))
    return out


def _norm_name(name: str) -> str:
    return " ".join(name.lower().replace("-", " ").split())


# ---------------------------------------------------------------------------
# Import result
# ---------------------------------------------------------------------------


AVAIL_BUSY = "busy"
AVAIL_LIGHT = "light"
AVAIL_OPEN = "open"
AVAIL_UNKNOWN = "unknown"

# A day with this many committed items is treated as spoken for. A home visit
# runs two to four hours including travel, so a couple of fixed commitments is
# usually enough to rule the day out even when the gaps are not visible here.
BUSY_ITEMS = 3
LIGHT_ITEMS = 1


@dataclass
class DayAvailability:
    """One coordinator's load on one day, as far as a month grid can show it."""

    day: str
    weekday: int
    state: str
    busy: int = 0
    tentative: int = 0
    named: int = 0
    earliest: Optional[str] = None
    latest: Optional[str] = None
    truncated: bool = False

    @property
    def items(self) -> int:
        return self.busy + self.tentative + self.named

    def to_dict(self) -> dict:
        return {
            "day": self.day, "weekday": self.weekday, "state": self.state,
            "busy": self.busy, "tentative": self.tentative, "named": self.named,
            "items": self.items, "earliest": self.earliest, "latest": self.latest,
            "truncated": self.truncated,
        }


@dataclass
class PersonMonth:
    """A month of day-level availability for one coordinator."""

    coordinator_id: Optional[str]
    name: str
    hue: Optional[str]
    label: str
    days: List[DayAvailability] = field(default_factory=list)

    def _count(self, state: str) -> int:
        return sum(1 for d in self.days if d.state == state)

    def to_dict(self) -> dict:
        working = [d for d in self.days if d.weekday < 5]
        return {
            "coordinator_id": self.coordinator_id,
            "name": self.name,
            "hue": self.hue,
            "label": self.label,
            "days": [d.to_dict() for d in self.days],
            "busy_days": self._count(AVAIL_BUSY),
            "light_days": self._count(AVAIL_LIGHT),
            "open_days": self._count(AVAIL_OPEN),
            "unknown_days": self._count(AVAIL_UNKNOWN),
            "working_days": len(working),
            "open_working_days": sum(
                1 for d in working if d.state == AVAIL_OPEN),
            "total_items": sum(d.items for d in self.days),
        }


def month_availability(
    parsed: PdfIngestResult,
    attributed: Dict[str, Optional[str]],
    coordinators: Optional[Dict[str, object]] = None,
) -> List[PersonMonth]:
    """Who looks busy, and on which days, for the month this export covers.

    This is the honest ceiling of a month grid: day-level load per person. It
    cannot say "free at 2pm" because no end time is printed. Where a day cell
    hit the grid's row limit, a person with nothing showing is reported as
    ``unknown`` rather than ``open`` — the rows that would have proved otherwise
    may simply have been cut off the page.
    """
    signals = day_signals(parsed)
    truncated = set(parsed.saturated_cells) | set(parsed.overflow_cells)
    all_days = _month_days(parsed)

    by_hue: Dict[str, Dict[str, DaySignal]] = {}
    for sig in signals:
        if sig.calendar_color_id:
            by_hue.setdefault(sig.calendar_color_id, {})[sig.day] = sig

    names = {}
    if coordinators:
        names = {cid: getattr(c, "name", cid) for cid, c in coordinators.items()}

    out: List[PersonMonth] = []
    for hue, cid in sorted(attributed.items(), key=lambda kv: (kv[1] is None, kv[0])):
        if hue == "unknown":
            continue
        label = names.get(cid) if cid else None
        person = PersonMonth(
            coordinator_id=cid,
            name=label or f"Unmatched calendar ({hue})",
            hue=hue,
            label=label or hue,
        )
        seen = by_hue.get(hue, {})
        for day in all_days:
            sig = seen.get(day.isoformat())
            iso = day.isoformat()
            if sig is None:
                state = AVAIL_UNKNOWN if iso in truncated else AVAIL_OPEN
                person.days.append(DayAvailability(
                    day=iso, weekday=day.weekday(), state=state,
                    truncated=iso in truncated))
                continue
            items = sig.busy_count + sig.tentative_count + sig.named_events
            if items >= BUSY_ITEMS:
                state = AVAIL_BUSY
            elif items >= LIGHT_ITEMS:
                state = AVAIL_LIGHT
            else:
                state = AVAIL_OPEN
            person.days.append(DayAvailability(
                day=iso, weekday=day.weekday(), state=state,
                busy=sig.busy_count, tentative=sig.tentative_count,
                named=sig.named_events, earliest=sig.earliest_start,
                latest=sig.latest_start, truncated=iso in truncated))
        out.append(person)

    out.sort(key=lambda p: (p.coordinator_id is None, p.name))
    return out


def _month_days(parsed: PdfIngestResult) -> List[date]:
    """Every day the grid covers, spill weeks included."""
    days = sorted({e.day for e in parsed.entries})
    if not days:
        return []
    first = date.fromisoformat(days[0])
    last = date.fromisoformat(days[-1])
    out, cur = [], first
    while cur <= last:
        out.append(cur)
        cur += timedelta(days=1)
    return out


@dataclass
class ImportResult:
    import_id: str
    source_file: str
    source_hash: str
    uploaded_at: datetime
    view_type: str
    tier: int
    date_range: str
    calendar_names: List[str] = field(default_factory=list)
    hues_seen: Dict[str, int] = field(default_factory=dict)
    entry_count: int = 0
    runs: List[CalendarSyncRun] = field(default_factory=list)
    day_pressure: List[dict] = field(default_factory=list)
    per_coordinator_days: List[dict] = field(default_factory=list)
    availability: List[dict] = field(default_factory=list)
    legend: Dict[str, str] = field(default_factory=dict)
    attribution_source: str = "none"   # legend | stored_map | none
    matched_names: Dict[str, Optional[str]] = field(default_factory=dict)
    axis_source: Optional[str] = None
    roles: Dict[str, str] = field(default_factory=dict)
    unavailable: List[dict] = field(default_factory=list)
    unresolved_names: List[dict] = field(default_factory=list)
    resources: Dict[str, List[dict]] = field(default_factory=dict)
    all_day: List[dict] = field(default_factory=list)
    unattributed_hues: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def schedulable(self) -> bool:
        """Whether this upload can affect a hard availability gate at all.

        An image can, but only after review -- which is why it is a separate
        tier rather than being folded in with the exact one.
        """
        return self.tier in (TIER_TIMED_EXPORT, TIER_IMAGE)

    @property
    def blocks(self) -> List[CalendarBlock]:
        return [b for run in self.runs for b in run.blocks]

    @property
    def pending_review(self) -> int:
        return sum(1 for b in self.blocks if not b.reviewed)

    def to_dict(self) -> dict:
        return {
            "import_id": self.import_id,
            "source_file": self.source_file,
            "source_hash": self.source_hash,
            "uploaded_at": self.uploaded_at.isoformat(timespec="seconds"),
            "view_type": self.view_type,
            "tier": self.tier,
            "tier_rule": TIER_RULES[self.tier],
            "schedulable": self.schedulable,
            "date_range": self.date_range,
            "calendar_names": self.calendar_names,
            "hues_seen": self.hues_seen,
            "entry_count": self.entry_count,
            "block_count": len(self.blocks),
            "pending_review": self.pending_review,
            "coordinators_touched": sorted({r.coordinator_id for r in self.runs}),
            "day_pressure": self.day_pressure,
            "per_coordinator_days": self.per_coordinator_days,
            "availability": self.availability,
            "legend": self.legend,
            "attribution_source": self.attribution_source,
            "axis_source": self.axis_source,
            "matched_names": self.matched_names,
            "roles": self.roles,
            "role_summary": [
                {
                    "label": label,
                    "role": role,
                    "role_label": ROLE_LABEL.get(role, role),
                    "polarity": POLARITY.get(role, "ignored"),
                    "meaning": ROLE_MEANING.get(role, ""),
                    "coordinator_id": self.matched_names.get(label),
                    "blocks": len(self.resources.get(role, []))
                    if role != ROLE_COORDINATOR else None,
                }
                for label, role in sorted(self.roles.items())
            ],
            "resources": self.resources,
            "all_day": self.all_day,
            "unavailable": self.unavailable,
            "unresolved_names": self.unresolved_names,
            "unattributed_hues": self.unattributed_hues,
            "blockers": self.blockers,
            "notes": self.notes,
        }


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# The import itself
# ---------------------------------------------------------------------------


def is_image(path: str) -> bool:
    """Whether this upload is a picture rather than a PDF."""
    with open(path, "rb") as fh:
        head = fh.read(12)
    return (head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff")
            or head[:4] in (b"GIF8",) or head[8:12] == b"WEBP")


def import_pdf(
    path: str,
    coordinators: Optional[Dict[str, object]] = None,
    color_map: Optional[ColorMap] = None,
    now: Optional[datetime] = None,
    year_hint: Optional[int] = None,
    auto_confirm: bool = True,
    image_hours: Optional[tuple] = None,
    image_start: Optional[date] = None,
    image_days: int = 5,
) -> ImportResult:
    """Parse an uploaded calendar and emit evidence at whatever tier it earns."""
    now = now or datetime.now()
    color_map = color_map if color_map is not None else ColorMap.load()

    if is_image(path):
        from .ingest_image import extract as extract_image

        start = image_start or (now.date() - timedelta(days=now.weekday()))
        parsed = extract_image(path, day_start=start, n_days=image_days,
                               hours=image_hours)
        tier = TIER_IMAGE
        # Never auto-commit an image. The PDF path is exact measurement and has
        # earned committing on import; this is inference off pixels and has not.
        auto_confirm = False
    else:
        parsed = load(path, year_hint=year_hint)
        tier = (
            TIER_TIMED_EXPORT
            if parsed.calendar_view_type in (VIEW_WORK_WEEK, VIEW_DAY)
            else TIER_MONTH_GRID
        )
    result = ImportResult(
        import_id=uuid.uuid4().hex[:12],
        source_file=os.path.basename(path),
        source_hash=file_hash(path),
        uploaded_at=now,
        view_type=parsed.calendar_view_type,
        tier=tier,
        date_range=parsed.visible_date_range,
        calendar_names=list(parsed.selected_calendars),
        entry_count=len(parsed.entries),
        blockers=list(parsed.unresolved),
    )
    result.axis_source = getattr(parsed, "axis_source", None)

    for entry in parsed.entries:
        hue = entry.calendar_color_id or "unknown"
        result.hues_seen[hue] = result.hues_seen.get(hue, 0) + 1

    # Attribution, in order of trustworthiness. The legend Outlook prints into
    # the header — each calendar's name drawn in that calendar's own colour — is
    # evidence from the file itself, so it beats any stored map and needs no
    # human to confirm it. The stored map is the fallback for exports whose
    # header lost its colours.
    result.legend = dict(parsed.legend)
    attributed: Dict[str, Optional[str]] = {}
    role_map = RoleMap.load()
    if parsed.legend and coordinators:
        matches = suggest_roster_matches(list(parsed.legend), coordinators)
        result.matched_names = dict(matches)
        for label, hue in parsed.legend.items():
            role = role_map.role_of(label, is_roster_name=bool(matches.get(label)))
            result.roles[label] = role
            cid = matches.get(label)
            if cid and role == ROLE_COORDINATOR:
                attributed[hue] = cid
        if attributed:
            result.attribution_source = "legend"
    _collect_resources(parsed, result)
    _resolve_unavailability(parsed, result, coordinators, role_map)

    # The stored map only fills gaps the legend left. Letting it answer for a
    # hue the legend already explained is how a policy calendar becomes a
    # person: this file's own fixture has blue as "Offered Times ESD", and a map
    # left over from an older overlay resolved blue to a coordinator, turning
    # time the lab set aside for visits into that coordinator's busy time --
    # precisely the inversion roles exist to stop.
    explained = set(parsed.legend.values())
    for hue in result.hues_seen:
        if hue in explained:
            # Still record it, as belonging to nobody. Dropping the key would
            # lose the row that reports an overlaid calendar the roster does
            # not recognise.
            attributed.setdefault(hue, None)
            continue
        attributed.setdefault(hue, color_map.resolve(hue))
    if result.attribution_source == "none" and any(attributed.values()):
        result.attribution_source = "stored_map"
    result.unattributed_hues = sorted(
        h for h, cid in attributed.items() if not cid and h != "unknown")

    if result.attribution_source == "none":
        result.blockers.append(
            "NOBODY COULD BE IDENTIFIED: this export carries no usable colour "
            "legend and no stored colour map matched, so its entries belong to "
            "nobody. Match the colours by hand under 'Match colours to people'."
        )
    elif result.unattributed_hues:
        unmatched = [
            label for label, hue in result.legend.items()
            if hue in result.unattributed_hues
        ]
        result.notes.append(
            "Not on the roster, so left unattributed: "
            + ", ".join(unmatched or result.unattributed_hues)
            + ". Their entries still count toward how loaded each day looks."
        )

    if tier == TIER_MONTH_GRID:
        _rollup_month(parsed, result, attributed, coordinators)
    else:
        _build_runs(parsed, result, attributed, now, auto_confirm)
    _add_absence_blocks(result, now, auto_confirm)

    return result


def _resolve_unavailability(parsed, result, coordinators, role_map) -> None:
    """Turn "X unavailable for visits" banners into named, whole-day absences.

    A banner is posted on whichever calendar the lab keeps them on, so its
    colour identifies nothing; the name in the text is the only evidence of who
    it concerns. Matching is on the exact first name, plus any alias the lab has
    declared. Nicknames are never inferred: "Maggie" is a plausible Margaret and
    a hard veto on the wrong person is worse than an unresolved one, so an
    unmatched name is reported for a human instead.
    """
    if not parsed.unavailability:
        return

    by_first: Dict[str, List[str]] = {}
    for cid, coord in (coordinators or {}).items():
        name = getattr(coord, "name", "")
        first = name.split()[0].lower() if name.split() else ""
        if first:
            by_first.setdefault(first, []).append(cid)

    unresolved: Dict[str, dict] = {}
    for note in parsed.unavailability:
        token = note["name"].strip()
        key = token.lower()
        cid = role_map.aliases.get(key)
        if cid is None:
            hits = by_first.get(key, [])
            cid = hits[0] if len(hits) == 1 else None
        if cid is None:
            row = unresolved.setdefault(
                token, {"name": token, "days": [], "reason": (
                    "ambiguous: more than one coordinator has this first name"
                    if len(by_first.get(key, [])) > 1
                    else "no coordinator on the roster has this first name")})
            row["days"].append(note["day"])
            continue
        result.unavailable.append({
            "coordinator_id": cid,
            "name": getattr(coordinators[cid], "name", cid),
            "day": note["day"],
            "status": note["status"],
        })

    result.unresolved_names = sorted(unresolved.values(), key=lambda r: r["name"])
    if result.unresolved_names:
        listed = ", ".join(
            f"{r['name']} ({len(r['days'])} day{'s' if len(r['days']) != 1 else ''})"
            for r in result.unresolved_names
        )
        result.blockers.append(
            "UNAVAILABILITY NOTICES NOT MATCHED TO ANYONE: " + listed + ". These "
            "days are NOT blocked, because guessing which coordinator a nickname "
            "refers to would take someone off the board who is actually free. Add "
            "the name to 'name_aliases' in config/calendar-roles.json."
        )


def _collect_resources(parsed, result) -> None:
    """Bucket the non-person calendars into the windows the gates consult.

    Only calendars with a declared or recognised role contribute. An
    unclassified calendar is left out entirely rather than folded into
    someone's busy time, because guessing its polarity wrong is worse than
    ignoring it.
    """
    buckets: Dict[str, List[Interval]] = {}
    for entry in parsed.entries:
        label = entry.calendar_label
        if not label:
            continue
        role = result.roles.get(label)
        if role in (None, ROLE_COORDINATOR, ROLE_OWNER, ROLE_UNKNOWN):
            continue
        if entry.all_day:
            result.all_day.append({
                "day": entry.day, "label": label, "role": role,
                "role_label": ROLE_LABEL.get(role, role),
            })
            continue
        if not entry.start_time or not entry.end_time:
            continue
        try:
            start = datetime.fromisoformat(f"{entry.day}T{entry.start_time}")
            end = datetime.fromisoformat(f"{entry.day}T{entry.end_time}")
        except ValueError:
            continue
        if end > start:
            buckets.setdefault(role, []).append(Interval(start, end))

    for role, intervals in buckets.items():
        result.resources[role] = [iv.to_dict() for iv in merge(intervals)]

    # Say plainly when a named calendar is present but contributes nothing, so
    # an empty filter is never mistaken for a filter that is switched off.
    for label, role in result.roles.items():
        if role in (ROLE_OFFERED, ROLE_CLINICIAN, ROLE_LAB) and not buckets.get(role):
            result.notes.append(
                f"'{label}' is overlaid on this export but has no events in this "
                f"range, so the {ROLE_LABEL.get(role, role).lower()} filter has "
                "nothing to apply here."
            )


def _rollup_month(parsed, result, attributed, coordinators) -> None:
    """Month grid: day-level load only, and say plainly why that is the ceiling."""
    result.notes.append(
        "Month view: no end times are printed, so this upload produces a day-level "
        "load signal and no bookable intervals. Re-print the same range as Work Week "
        "to get times the board can schedule against."
    )
    signals: List[DaySignal] = day_signals(parsed)
    result.day_pressure = joint_day_pressure(signals)
    result.availability = [
        p.to_dict() for p in month_availability(parsed, attributed, coordinators)
    ]

    names = {}
    if coordinators:
        names = {cid: getattr(c, "name", cid) for cid, c in coordinators.items()}
    per: Dict[str, dict] = {}
    for sig in signals:
        cid = attributed.get(sig.calendar_color_id or "unknown")
        key = cid or f"unattributed:{sig.calendar_color_id or 'unknown'}"
        node = per.setdefault(
            key,
            {
                "coordinator_id": cid,
                "label": names.get(cid, key) if cid else f"Unassigned colour ({sig.calendar_color_id})",
                "hue": sig.calendar_color_id,
                "days": 0,
                "busy": 0,
                "tentative": 0,
                "named": 0,
            },
        )
        node["days"] += 1
        node["busy"] += sig.busy_count
        node["tentative"] += sig.tentative_count
        node["named"] += sig.named_events
    result.per_coordinator_days = sorted(
        per.values(), key=lambda n: (-n["busy"], n["label"])
    )


def _add_absence_blocks(result, now, auto_confirm: bool) -> None:
    """Give every matched absence a whole-day block, so the gate simply sees it.

    "Unavailable for visits" is a statement about the entire day, not a meeting,
    so it becomes a midnight-to-midnight block rather than anything narrower.
    Feeding it through the same evidence path as every other block means the
    existing availability gate rejects the day without needing a special case,
    and the block can be rejected afterwards like any other.
    """
    if not result.unavailable:
        return
    by_coord: Dict[str, List[CalendarBlock]] = {}
    for absence in result.unavailable:
        try:
            day = datetime.fromisoformat(absence["day"])
        except ValueError:
            continue
        by_coord.setdefault(absence["coordinator_id"], []).append(
            CalendarBlock(
                coordinator_id=absence["coordinator_id"],
                start=day.replace(hour=0, minute=0),
                end=day.replace(hour=23, minute=59),
                reviewed=auto_confirm,
                confirmed=True,
                source_hash=result.source_hash,
                run_id="",
            )
        )

    existing = {run.coordinator_id: run for run in result.runs}
    for cid, blocks in sorted(by_coord.items()):
        run = existing.get(cid)
        if run is None:
            run = CalendarSyncRun(
                run_id=f"{result.import_id}-{cid}",
                coordinator_id=cid,
                captured_at=now,
                view_type=result.view_type,
                source_hash=result.source_hash,
                blocks=[],
                auto_committed=auto_confirm,
            )
            result.runs.append(run)
        for block in blocks:
            block.run_id = run.run_id
        run.blocks.extend(blocks)

    result.notes.append(
        f"{len(result.unavailable)} whole-day absence notice(s) were read off the "
        "all-day banners and block those days outright."
    )


def _build_runs(parsed, result, attributed, now, auto_confirm: bool = True) -> None:
    """Timed export: one sync run per coordinator, with real intervals.

    Runs stay per-person even though the page was a combined overlay, because
    that is the unit the rest of the system reasons about.

    These blocks commit on import. That is safe *here* in a way it would not be
    for the image-capture path: a PDF's event boxes are vector rectangles and
    its gutter is vector text, so the times are read exactly rather than guessed
    at by OCR. Nothing is inferred, so there is nothing for a human to correct
    before it counts. Any block can still be rejected afterwards.
    """
    by_coord: Dict[str, List[CalendarBlock]] = {}
    dropped = 0
    for entry in parsed.entries:
        cid = attributed.get(entry.calendar_color_id or "unknown")
        if cid is None:
            dropped += 1
            continue
        if not entry.start_time or not entry.end_time:
            dropped += 1
            continue
        try:
            start = datetime.fromisoformat(f"{entry.day}T{entry.start_time}")
            end = datetime.fromisoformat(f"{entry.day}T{entry.end_time}")
        except ValueError:
            dropped += 1
            continue
        if end <= start:
            dropped += 1
            continue
        by_coord.setdefault(cid, []).append(
            CalendarBlock(
                coordinator_id=cid,
                start=start,
                end=end,
                reviewed=auto_confirm,
                confirmed=True,
                source_hash=result.source_hash,
                run_id="",
            )
        )

    for cid, blocks in sorted(by_coord.items()):
        run_id = f"{result.import_id}-{cid}"
        for b in blocks:
            b.run_id = run_id
        result.runs.append(
            CalendarSyncRun(
                run_id=run_id,
                coordinator_id=cid,
                captured_at=now,
                view_type=parsed.calendar_view_type,
                source_hash=result.source_hash,
                blocks=blocks,
                auto_committed=auto_confirm,
            )
        )

    if dropped:
        result.notes.append(
            f"{dropped} parsed entries could not become blocks (no owner colour, or no "
            "readable interval) and were left out rather than guessed at."
        )
    if auto_confirm:
        result.notes.append(
            f"{len(result.blocks)} blocks were read exactly off the PDF and are already "
            "in effect. Reject any that are wrong and the board updates immediately."
        )
    else:
        result.notes.append(
            f"{len(result.blocks)} blocks are waiting on review. Until each is confirmed "
            "it is evidence of nothing and cannot block an assignment."
        )
