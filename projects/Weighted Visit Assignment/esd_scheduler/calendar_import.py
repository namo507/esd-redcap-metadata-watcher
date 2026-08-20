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
from .models import CalendarBlock, CalendarSyncRun

TIER_GRAPH = 1
TIER_TIMED_EXPORT = 2
TIER_MONTH_GRID = 3

TIER_RULES = {
    TIER_GRAPH: "may confirm availability and may veto",
    TIER_TIMED_EXPORT: "may veto once reviewed; intervals are real",
    TIER_MONTH_GRID: "advisory load signal only; may never confirm or veto",
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
    unattributed_hues: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def schedulable(self) -> bool:
        """Whether this upload can affect a hard availability gate at all."""
        return self.tier == TIER_TIMED_EXPORT

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


def import_pdf(
    path: str,
    coordinators: Optional[Dict[str, object]] = None,
    color_map: Optional[ColorMap] = None,
    now: Optional[datetime] = None,
    year_hint: Optional[int] = None,
) -> ImportResult:
    """Parse an uploaded print and emit evidence at whatever tier it earns."""
    now = now or datetime.now()
    color_map = color_map if color_map is not None else ColorMap.load()
    parsed: PdfIngestResult = load(path, year_hint=year_hint)

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

    for entry in parsed.entries:
        hue = entry.calendar_color_id or "unknown"
        result.hues_seen[hue] = result.hues_seen.get(hue, 0) + 1

    attributed = {h: color_map.resolve(h) for h in result.hues_seen}
    result.unattributed_hues = sorted(h for h, cid in attributed.items() if not cid)

    if not color_map.confirmed:
        result.blockers.append(
            "COLOUR MAP NOT CONFIRMED: this export overlays "
            f"{len(result.calendar_names) or len(result.hues_seen)} calendars and Outlook "
            "prints no legend, so no entry can be attributed to a person yet. Confirm "
            "which coordinator owns each colour, then re-upload."
        )
    elif result.unattributed_hues:
        result.blockers.append(
            "UNMAPPED COLOURS: "
            + ", ".join(result.unattributed_hues)
            + " appear in this export but are not in the confirmed colour map. Their "
            "entries are counted but attributed to nobody."
        )

    if tier == TIER_MONTH_GRID:
        _rollup_month(parsed, result, attributed, coordinators)
    else:
        _build_runs(parsed, result, attributed, now)

    return result


def _rollup_month(parsed, result, attributed, coordinators) -> None:
    """Month grid: day-level load only, and say plainly why that is the ceiling."""
    result.notes.append(
        "Month view: no end times are printed, so this upload produces a day-level "
        "load signal and no bookable intervals. Re-print the same range as Work Week "
        "to get times the board can schedule against."
    )
    if parsed.overflow_cells:
        result.blockers.append(
            f"TRUNCATED CELLS: {len(parsed.overflow_cells)} day cells hit the month-view "
            "row limit, so an unknown number of later events are missing from this page. "
            "Afternoons are cut first."
        )

    signals: List[DaySignal] = day_signals(parsed)
    result.day_pressure = joint_day_pressure(signals)

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


def _build_runs(parsed, result, attributed, now) -> None:
    """Timed export: one sync run per coordinator, every block awaiting review.

    Runs stay per-person even though the page was a combined overlay, because
    that is the unit the rest of the system reasons about — and blocks arrive
    unreviewed so an OCR-grade misread cannot silently veto anyone.
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
                reviewed=False,
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
                auto_committed=False,
            )
        )

    if dropped:
        result.notes.append(
            f"{dropped} parsed entries could not become blocks (no owner colour, or no "
            "readable interval) and were left out rather than guessed at."
        )
    result.notes.append(
        f"{len(result.blocks)} blocks are waiting on review. Until each is confirmed it "
        "is evidence of nothing and cannot block an assignment."
    )
