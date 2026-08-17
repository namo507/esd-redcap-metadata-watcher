"""Ingest an Outlook calendar PDF export into structured availability signals.

This is a **degraded, secondary** source and the module is written to keep it
that way. An Outlook *month* view print shows a start time and a status word per
event and nothing else: no end times, no durations, and no legend mapping the
per-calendar colour to a person. Anything this module cannot see, it refuses to
invent.

What is genuinely recoverable from the PDF:

  * the grid geometry, so every event maps to a specific calendar date
  * the event's start time, when printed
  * the status word Outlook printed (Busy / Tentative / Free) or the event title
  * a per-event colour chip, which identifies *which of the overlaid calendars*
    an event belongs to, as an opaque colour id

What is not recoverable, and is therefore emitted as an explicit gap:

  * end times, hence real intervals
  * the colour -> person mapping, absent a legend in the export
  * anything hidden behind a month-view "+N more" overflow

Use: seeding and cross-checking the live Microsoft Graph feed, not replacing it.
``feasibility.py`` consumes ``CalendarSnapshot`` objects with real intervals;
this module deliberately produces a different, weaker type so a day-level
signal can never be mistaken for a verified free/busy block.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from typing import Dict, List, Optional, Sequence, Tuple

# Outlook's category palette. Saturated = the event is shown solid (busy);
# the pale tint of the same hue is how Outlook draws tentative / free.
HUE_FAMILIES: Dict[str, Dict[str, str]] = {
    "grey":     {"solid": "69797E", "pale": "CDD6D8"},
    "blue":     {"solid": "0078D4", "pale": "A9D3F2"},
    "navy":     {"solid": "0F6CBD", "pale": "AACDEB"},
    "teal":     {"solid": "038387", "pale": "9BD9DB"},
    "teal_lt":  {"solid": "038387", "pale": "C7EBEC"},
    "green":    {"solid": "00CC6A", "pale": "A8F0CD"},
    "green_lt": {"solid": "00CC6A", "pale": "CFF7E4"},
    "yellow":   {"solid": "FDE300", "pale": "FEF7B2"},
    "orange":   {"solid": "F7630C", "pale": "FDCFB5"},
    "orange2":  {"solid": "F6620C", "pale": "FDCFB5"},
}
_COLOR_TO_HUE: Dict[str, Tuple[str, str]] = {}
for _hue, _v in HUE_FAMILIES.items():
    _COLOR_TO_HUE.setdefault(_v["solid"], (_hue.split("_")[0], "solid"))
    _COLOR_TO_HUE.setdefault(_v["pale"], (_hue.split("_")[0], "pale"))

STATUS_WORDS = ("Busy", "Tentative", "Free", "Out of office", "Working elsewhere")
TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)$", re.I)
DAYNUM_RE = re.compile(r"^(?:([A-Z][a-z]{2})\s+)?(\d{1,2})$")
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


# ---------------------------------------------------------------------------
# Extracted types
# ---------------------------------------------------------------------------


@dataclass
class PdfEntry:
    """One printed row inside one day cell. Deliberately not an interval."""

    day: str                       # ISO date
    start_time: Optional[str]      # "09:30" or None for all-day rows
    status_label: str              # busy | tentative | free | named_event | unknown
    event_title: Optional[str]
    calendar_color_id: Optional[str]   # "blue", "teal", ... opaque, not a person
    participant: Optional[str]     # None until a legend maps colour -> person
    confidence_score: float
    evidence_text: str
    end_time: Optional[str] = None     # never populated by a month view
    uncertain: bool = True


@dataclass
class PdfIngestResult:
    source_file: str
    source_platform: str = "Outlook Calendar"
    calendar_view_type: str = "unknown"
    visible_date_range: str = ""
    selected_calendars: List[str] = field(default_factory=list)
    timezone: Optional[str] = None
    entries: List[PdfEntry] = field(default_factory=list)
    overflow_cells: List[str] = field(default_factory=list)
    unresolved: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entries"] = [asdict(e) for e in self.entries]
        return d


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _hex_of(fill) -> Optional[str]:
    if not fill:
        return None
    try:
        return "%02X%02X%02X" % tuple(int(round(c * 255)) for c in fill)
    except (TypeError, ValueError):
        return None


def _parse_time(token: str) -> Optional[str]:
    m = TIME_RE.match(token.strip())
    if not m:
        return None
    hour = int(m.group(1)) % 12
    minute = int(m.group(2) or 0)
    if m.group(3).upper() == "PM":
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def extract(path: str, year_hint: Optional[int] = None) -> PdfIngestResult:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyMuPDF is required to ingest Outlook PDF exports") from exc

    doc = fitz.open(path)
    result = PdfIngestResult(source_file=os.path.basename(path))
    page = doc[0]

    # --- header: title, calendar list ---------------------------------------
    spans = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip():
                    spans.append(
                        {
                            "x": span["bbox"][0],
                            "y": span["bbox"][1],
                            "x1": span["bbox"][2],
                            "size": span["size"],
                            "text": span["text"],
                        }
                    )
    spans.sort(key=lambda s: (round(s["y"], 1), s["x"]))

    title_spans = [s for s in spans if s["size"] > 9]
    month_year = title_spans[0]["text"].strip() if title_spans else ""
    result.visible_date_range = month_year
    year = year_hint
    month = None
    parts = month_year.split()
    if len(parts) == 2 and parts[0][:3] in MONTHS:
        month = MONTHS[parts[0][:3]]
        if parts[1].isdigit():
            year = int(parts[1])

    # The calendar list is the run of large spans after the title.
    names_raw = "".join(s["text"] for s in title_spans[1:]).strip()
    result.selected_calendars = _split_calendar_names(names_raw)

    weekday_headers = [s for s in spans if s["text"].strip() in (
        "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")]
    result.calendar_view_type = "month" if len(weekday_headers) == 7 else "unknown"

    # --- grid: columns from weekday headers, rows from day-number cells ------
    columns = sorted(s["x"] for s in weekday_headers)
    if not columns:
        result.unresolved.append("no weekday header row found; cannot build a grid")
        return result
    col_width = (columns[-1] - columns[0]) / max(1, len(columns) - 1)

    # Walk the grid in reading order. Outlook labels only the first cell of each
    # month ("Jul 26", "Aug 1", "Sep 1"); every bare number inherits the month
    # most recently labelled. Assigning bare numbers to the view's own month
    # silently misdates the whole leading spill week.
    raw_cells: List[Tuple[float, float, Optional[str], int]] = []
    for s in spans:
        if s["size"] > 9 or s["y"] < weekday_headers[0]["y"]:
            continue
        m = DAYNUM_RE.match(s["text"].strip())
        if not m or not any(abs(s["x"] - c) < 3 for c in columns):
            continue
        raw_cells.append((s["y"], s["x"], m.group(1), int(m.group(2))))
    raw_cells.sort(key=lambda c: (round(c[0], 0), c[1]))

    day_cells: List[Tuple[float, float, date]] = []
    cur_month, cur_year, prev_num = None, year, None
    for y, x, mon_txt, num in raw_cells:
        if mon_txt and mon_txt in MONTHS:
            new_month = MONTHS[mon_txt]
            # Only December -> January rolls the year forward.
            if cur_month == 12 and new_month == 1:
                cur_year = (cur_year or 0) + 1
            cur_month = new_month
        elif cur_month is None:
            # Grid opened on a bare number: it belongs to the month before the
            # view's own if the sequence is still counting up from late days.
            cur_month = month
        elif prev_num is not None and num < prev_num - 1:
            # An unlabelled rollover should not happen in an Outlook export.
            # Flag it rather than guessing which month the cell belongs to.
            result.unresolved.append(
                f"UNLABELLED MONTH ROLLOVER at day {num}: cell date is unverified")
        prev_num = num
        if not (cur_year and cur_month):
            continue
        try:
            day_cells.append((x, y, date(cur_year, cur_month, num)))
        except ValueError:
            continue

    if not day_cells:
        result.unresolved.append("no day-number cells recognised")
        return result

    row_tops = sorted({round(y, 0) for _, y, _ in day_cells})
    # Collapse near-duplicate row tops.
    merged_rows: List[float] = []
    for y in row_tops:
        if not merged_rows or y - merged_rows[-1] > 6:
            merged_rows.append(y)
    row_height = (
        (merged_rows[-1] - merged_rows[0]) / max(1, len(merged_rows) - 1)
        if len(merged_rows) > 1 else 100.0
    )

    cell_date: Dict[Tuple[int, int], date] = {}
    for x, y, d in day_cells:
        ci = min(range(len(columns)), key=lambda i: abs(columns[i] - x))
        ri = min(range(len(merged_rows)), key=lambda i: abs(merged_rows[i] - y))
        cell_date[(ri, ci)] = d

    # --- colour chips --------------------------------------------------------
    chips: List[Tuple[float, float, str]] = []  # (x, y_center, hex)
    for drawing in page.get_drawings():
        hexc = _hex_of(drawing.get("fill"))
        if hexc is None or hexc not in _COLOR_TO_HUE:
            continue
        r = drawing["rect"]
        # The per-event strip is a narrow vertical bar roughly 2.7 x 10.6 pt.
        if r.width > 6 or not (6 < r.height < 16):
            continue
        chips.append((r.x0, (r.y0 + r.y1) / 2, hexc))

    # --- event rows ----------------------------------------------------------
    rows: Dict[Tuple[float, int], List[dict]] = defaultdict(list)
    for s in spans:
        if s["size"] > 9 or s["y"] <= weekday_headers[0]["y"] + 4:
            continue
        if DAYNUM_RE.match(s["text"].strip()) and any(
            abs(s["x"] - c) < 3 for c in columns
        ):
            continue
        ci = min(range(len(columns)), key=lambda i: abs(columns[i] - s["x"]))
        if s["x"] < columns[ci] - 4 or s["x"] > columns[ci] + col_width:
            continue
        rows[(round(s["y"], 0), ci)].append(s)

    for (y, ci), parts_in_row in sorted(rows.items()):
        ri = min(range(len(merged_rows)), key=lambda i: abs(merged_rows[i] - y))
        if merged_rows[ri] > y:
            ri = max(0, ri - 1)
        day = cell_date.get((ri, ci))
        if day is None:
            continue

        parts_in_row.sort(key=lambda s: s["x"])
        raw = "".join(p["text"] for p in parts_in_row).strip()
        if not raw:
            continue

        # "+3 more" style overflow: month view is hiding events in this cell.
        if re.match(r"^\+?\s*\d+\s+more$", raw, re.I):
            result.overflow_cells.append(day.isoformat())
            continue

        tokens = raw.split(" ", 1)
        start = _parse_time(tokens[0]) if tokens else None
        if start is None and len(tokens) > 1:
            start = _parse_time(" ".join(raw.split(" ")[:2]))
            if start:
                tokens = [" ".join(raw.split(" ")[:2]), " ".join(raw.split(" ")[2:])]
        remainder = (tokens[1].strip() if len(tokens) > 1 else "").strip()
        if start is None:
            remainder = raw

        label, title = _classify(remainder)

        chip_hue = _nearest_chip(chips, parts_in_row[0]["x"], y)
        hue, tone = chip_hue if chip_hue else (None, None)
        # Outlook draws tentative and free as a pale tint of the same hue. Where
        # the printed word and the tint disagree, the printed word wins: it is
        # the literal evidence.
        if label == "unknown" and tone == "pale":
            label = "tentative"
        elif label == "unknown" and tone == "solid":
            label = "busy"

        confidence = 0.85 if label in ("busy", "tentative", "free") else 0.6
        if start is None:
            confidence -= 0.15
        if hue is None:
            confidence -= 0.15

        result.entries.append(
            PdfEntry(
                day=day.isoformat(),
                start_time=start,
                status_label=label,
                event_title=title,
                calendar_color_id=hue,
                participant=None,
                confidence_score=round(max(0.1, confidence), 2),
                evidence_text=raw[:60],
            )
        )

    result.unresolved = [
        "END TIMES NOT VISIBLE: an Outlook month view prints only a start time, "
        "so no entry here can be turned into a real interval.",
        "NO COLOUR LEGEND IN THE EXPORT: the header names the overlaid calendars "
        "as plain comma-separated text with no swatches, so colour ids cannot be "
        "mapped to people from this file alone.",
        "ALL-DAY 'Free' ROWS ARE AMBIGUOUS: Outlook prints these for a day with a "
        "free-marked all-day item, which is not the same as an empty day.",
    ]
    if result.overflow_cells:
        result.unresolved.append(
            f"MONTH-VIEW OVERFLOW on {len(result.overflow_cells)} day(s): "
            "events are hidden behind a '+N more' link and are not in this file."
        )
    return result


def _split_calendar_names(raw: str) -> List[str]:
    """Rejoin 'Bell, Margaret' style surname-comma-forename pairs.

    Outlook appends private-use icon glyphs (shared-calendar badges) to the
    label; strip them so the name is a clean join key against the roster.
    """
    raw = "".join(ch for ch in raw if ch.isprintable() and not 0xE000 <= ord(ch) <= 0xF8FF)
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    names, i = [], 0
    while i < len(tokens):
        if i + 1 < len(tokens) and " " not in tokens[i] and tokens[i] != "Calendar":
            names.append(f"{tokens[i]}, {tokens[i + 1]}")
            i += 2
        else:
            names.append(tokens[i])
            i += 1
    return names


def _classify(remainder: str) -> Tuple[str, Optional[str]]:
    stripped = remainder.strip()
    for word in STATUS_WORDS:
        if stripped.lower() == word.lower():
            return word.lower().replace(" ", "_"), None
    if not stripped:
        return "unknown", None
    return "named_event", stripped


def _nearest_chip(chips, x, y, x_tol=14.0, y_tol=7.0):
    best, best_d = None, 1e9
    for cx, cy, hexc in chips:
        if abs(cy - y) > y_tol or not (-x_tol <= x - cx <= x_tol):
            continue
        d = abs(cy - y) + abs(x - cx) * 0.1
        if d < best_d:
            best, best_d = _COLOR_TO_HUE[hexc], d
    return best


# ---------------------------------------------------------------------------
# Day-level rollup (the only aggregation this source can honestly support)
# ---------------------------------------------------------------------------


@dataclass
class DaySignal:
    day: str
    calendar_color_id: Optional[str]
    busy_count: int = 0
    tentative_count: int = 0
    free_markers: int = 0
    named_events: int = 0
    earliest_start: Optional[str] = None
    latest_start: Optional[str] = None
    density: float = 0.0
    confidence: float = 0.0


def day_signals(result: PdfIngestResult) -> List[DaySignal]:
    """Per (day, calendar) counts. Density is a *load proxy*, not utilisation.

    Because no end time is visible, this cannot say how much of a day is
    consumed. It says how many committed items were printed, which is a usable
    prior for "how busy does this person look" and nothing more.
    """
    buckets: Dict[Tuple[str, Optional[str]], DaySignal] = {}
    for e in result.entries:
        key = (e.day, e.calendar_color_id)
        sig = buckets.setdefault(key, DaySignal(day=e.day, calendar_color_id=e.calendar_color_id))
        if e.status_label == "busy":
            sig.busy_count += 1
        elif e.status_label == "tentative":
            sig.tentative_count += 1
        elif e.status_label == "free":
            sig.free_markers += 1
        elif e.status_label == "named_event":
            sig.named_events += 1
            sig.busy_count += 1  # a titled event is a commitment
        if e.start_time:
            if sig.earliest_start is None or e.start_time < sig.earliest_start:
                sig.earliest_start = e.start_time
            if sig.latest_start is None or e.start_time > sig.latest_start:
                sig.latest_start = e.start_time
    for sig in buckets.values():
        # Busy counts full, tentative counts half: the ranking rule is
        # Busy > Tentative > Free.
        sig.density = round(sig.busy_count + 0.5 * sig.tentative_count, 2)
        total = sig.busy_count + sig.tentative_count + sig.free_markers
        sig.confidence = round(min(0.75, 0.35 + 0.05 * total), 2)
    return sorted(buckets.values(), key=lambda s: (s.day, s.calendar_color_id or ""))


def joint_day_pressure(signals: Sequence[DaySignal]) -> List[dict]:
    """Total committed load per day across every overlaid calendar."""
    agg: Dict[str, dict] = {}
    for sig in signals:
        node = agg.setdefault(
            sig.day,
            {"day": sig.day, "calendars_seen": 0, "busy": 0, "tentative": 0,
             "named": 0, "density": 0.0},
        )
        node["calendars_seen"] += 1
        node["busy"] += sig.busy_count
        node["tentative"] += sig.tentative_count
        node["named"] += sig.named_events
        node["density"] = round(node["density"] + sig.density, 2)
    return sorted(agg.values(), key=lambda n: n["day"])


def to_json(result: PdfIngestResult, indent: int = 2) -> str:
    payload = result.to_dict()
    payload["day_signals"] = [asdict(s) for s in day_signals(result)]
    payload["joint_day_pressure"] = joint_day_pressure(day_signals(result))
    return json.dumps(payload, indent=indent, sort_keys=False)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m esd_scheduler.ingest_outlook_pdf FILE.pdf")
    print(to_json(extract(sys.argv[1])))
