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
from datetime import date, datetime, time, timedelta
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
def _hue_family(key: str) -> str:
    """Collapse palette variants to one family: teal_lt and orange2 are teal, orange."""
    return re.sub(r"(_lt|\d+)$", "", key)


_COLOR_TO_HUE: Dict[str, Tuple[str, str]] = {}
for _hue, _v in HUE_FAMILIES.items():
    _COLOR_TO_HUE.setdefault(_v["solid"], (_hue_family(_hue), "solid"))
    _COLOR_TO_HUE.setdefault(_v["pale"], (_hue_family(_hue), "pale"))

# Fewest rows an Outlook month cell can show before it is credible that the
# cell ran out of room rather than the day simply being quiet.
MIN_CELL_ROWS = 5

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
    # days whose cell stopped at the grid's row ceiling, so the count is a floor
    saturated_cells: List[str] = field(default_factory=list)
    # calendar label -> hue, read from the colour Outlook prints each name in
    legend: Dict[str, str] = field(default_factory=dict)
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
    result.legend = extract_legend(page)

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

    result.saturated_cells = _saturated_cells(result)

    result.unresolved = [
        "END TIMES NOT VISIBLE: an Outlook month view prints only a start time, "
        "so no entry here can be turned into a real interval.",
        "ALL-DAY 'Free' ROWS ARE AMBIGUOUS: Outlook prints these for a day with a "
        "free-marked all-day item, which is not the same as an empty day.",
    ]
    if not result.legend:
        result.unresolved.append(
            "NO COLOUR LEGEND RECOVERED: Outlook normally prints each calendar's "
            "name in that calendar's own colour, but this file's header carries no "
            "usable colours, so entries cannot be attributed to people from it."
        )
    if result.overflow_cells:
        result.unresolved.append(
            f"MONTH-VIEW OVERFLOW on {len(result.overflow_cells)} day(s): "
            "events are hidden behind a '+N more' link and are not in this file."
        )
    if result.saturated_cells:
        result.unresolved.append(
            f"CELLS AT THE ROW LIMIT on {len(result.saturated_cells)} day(s): the "
            "month grid fits a fixed number of rows and prints no marker when it "
            "cuts the rest, so these days are a floor, not a full count. An empty "
            "afternoon on one of them is not evidence of free time."
        )
    return result


def _saturated_cells(result: "PdfIngestResult") -> List[str]:
    """Days whose cell hit the grid's row ceiling and was silently cut.

    Outlook's month print does not render a '+N more' marker: it simply stops
    drawing. The tell is that many cells stop at exactly the same count, which
    is a layout ceiling rather than a coincidence about everyone's diaries. Days
    at that ceiling are reported as a floor on the true load, so nothing
    downstream reads a gap there as availability.
    """
    per_day: Dict[str, int] = {}
    for entry in result.entries:
        per_day[entry.day] = per_day.get(entry.day, 0) + 1
    if len(per_day) < 4:
        return []
    ceiling = max(per_day.values())
    at_ceiling = [d for d, n in per_day.items() if n == ceiling]
    # Two things have to hold before this is read as a layout limit rather than
    # a coincidence: the cell must hold enough rows to plausibly be full (a
    # three-item day is just a quiet day), and several days must stop at exactly
    # the same number, which diaries do not do on their own.
    if ceiling < MIN_CELL_ROWS or len(at_ceiling) < 3:
        return []
    return sorted(at_ceiling)


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
# The legend Outlook hides in plain sight
# ---------------------------------------------------------------------------


def _nearest_hue(rgb_int: int, tolerance: int = 40):
    """Closest calendar hue to a printed colour, or None if nothing is close.

    Outlook renders each overlaid calendar's name in that calendar's own colour,
    but the printed value is a rounded approximation of the palette entry
    (#0E6BBD for a #0F6CBD calendar). Exact matching therefore finds nothing;
    nearest-with-a-ceiling finds the right one without inventing a match for a
    colour that is simply not in the palette.
    """
    r, g, b = (rgb_int >> 16) & 0xFF, (rgb_int >> 8) & 0xFF, rgb_int & 0xFF
    best, best_d = None, None
    for hexc, (hue, tone) in _COLOR_TO_HUE.items():
        if tone != "solid":
            continue
        rr, gg, bb = int(hexc[0:2], 16), int(hexc[2:4], 16), int(hexc[4:6], 16)
        d = ((r - rr) ** 2 + (g - gg) ** 2 + (b - bb) ** 2) ** 0.5
        if best_d is None or d < best_d:
            best, best_d = hue, d
    if best_d is not None and best_d <= tolerance:
        return best, best_d
    return None


def extract_legend(page, header_fraction: float = 0.18) -> Dict[str, str]:
    """Map each overlaid calendar's name to its colour, read from the print.

    The header looks like plain text, but Outlook colours every calendar label
    with that calendar's own colour. That makes the legend recoverable from the
    file itself, so nobody has to hand-match colours to people and no
    attribution rests on a guess.

    Labels are collected across the whole header band rather than one text line,
    because a long roster wraps. The colour match is what identifies a legend
    entry: the view's title is drawn in a neutral grey that is nowhere near a
    calendar hue, so it drops out on its own.

    Returns ``{calendar_label: hue}`` with raw header fragments as keys
    ("Bell, Margaret"); joining those to a roster is the caller's job.
    """
    # The legend sits above the weekday header row. Bounding it there matters:
    # grid cells carry coloured text too, and without this the "legend" would
    # swallow event titles — both wrong and a needless copy of private detail.
    weekday_tops = [
        span["bbox"][1]
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        if span["text"].strip() in WEEKDAY_NAMES
    ]
    cutoff = min(weekday_tops) - 2 if weekday_tops else page.rect.height * header_fraction
    found: Dict[str, str] = {}
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["bbox"][1] > cutoff:
                    continue
                label = _clean_label(span["text"])
                if not label or label.isdigit():
                    continue
                match = _nearest_hue(span.get("color", 0))
                if match is None:
                    continue
                found.setdefault(label, match[0])
    return found


def _clean_label(raw: str) -> str:
    """Strip Outlook's private-use badge glyphs from a calendar label."""
    out = "".join(
        ch for ch in raw
        if ch.isprintable() and not 0xE000 <= ord(ch) <= 0xF8FF
    ).strip().strip(",").strip()
    return out


# ---------------------------------------------------------------------------
# View detection and the work-week / day parser
# ---------------------------------------------------------------------------

WEEKDAY_NAMES = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday")
HOUR_LABEL = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)$", re.I)

VIEW_MONTH = "month"
VIEW_WORK_WEEK = "work_week"
VIEW_DAY = "day"
VIEW_UNKNOWN = "unknown"


def _find_gutter(spans, grid_left: Optional[float] = None):
    """Locate the hour column of a time-gridded view, or return None.

    A month grid is full of hour labels too — every event prints its start
    time — so "there are hour labels" is not the test. A real gutter is a
    column of labels whose clock time rises strictly with y, spans several
    hours, and sits left of the day columns. A month grid fails all three:
    its times repeat down the page as each week restarts the clock.
    """
    by_x = {}
    for s in spans:
        m = HOUR_LABEL.match(s["text"].strip())
        if not m:
            continue
        hour = int(m.group(1)) % 12
        if m.group(3).upper() == "PM":
            hour += 12
        clock = hour + int(m.group(2) or 0) / 60.0
        y = (s["bbox"][1] + s["bbox"][3]) / 2
        by_x.setdefault(round(s["bbox"][0] / 4) * 4, []).append((y, clock))

    best = None
    for x, points in by_x.items():
        if len(points) < 5:
            continue
        if grid_left is not None and x > grid_left + 4:
            continue           # inside the grid: these are event start times
        points.sort()
        clocks = [c for _, c in points]
        if any(b <= a for a, b in zip(clocks, clocks[1:])):
            continue           # repeats or goes backwards: not a clock axis
        if clocks[-1] - clocks[0] < 4:
            continue
        if best is None or len(points) > len(best[1]):
            best = (x, points)
    return best


def _fit(points):
    """Least squares y -> clock hour, with the fit quality that earned it."""
    n = len(points)
    mean_y = sum(p[0] for p in points) / n
    mean_h = sum(p[1] for p in points) / n
    denom = sum((p[0] - mean_y) ** 2 for p in points)
    if denom == 0:
        return None
    slope = sum((p[0] - mean_y) * (p[1] - mean_h) for p in points) / denom
    intercept = mean_h - slope * mean_y
    ss_res = sum((h - (slope * y + intercept)) ** 2 for y, h in points)
    ss_tot = sum((h - mean_h) ** 2 for _, h in points)
    r2 = 1.0 if ss_tot == 0 else 1 - ss_res / ss_tot
    return slope, intercept, r2


def _page_spans(page):
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip():
                    out.append(span)
    return out


def detect_view_type(page) -> str:
    """Month grid, work-week, or single day.

    The distinction decides what the export is worth. A month grid prints a
    start time and nothing else, so it can never support a time-level conflict
    check; a work-week prints a time gutter, so an event's height *is* its
    duration. Erring permissive here is how a board starts trusting a
    start-time-only export as if it carried intervals.
    """
    spans = _page_spans(page)
    weekday_headers = [s for s in spans if s["text"].strip() in WEEKDAY_NAMES]
    grid_left = min((s["bbox"][0] for s in weekday_headers), default=None)

    gutter = _find_gutter(spans, grid_left)
    if gutter is not None:
        fit = _fit(gutter[1])
        if fit and fit[2] >= 0.98:
            return VIEW_WORK_WEEK if len(weekday_headers) >= 2 else VIEW_DAY

    if len(weekday_headers) == 7:
        return VIEW_MONTH
    return VIEW_UNKNOWN


def _gutter_scale(spans, grid_left=None):
    gutter = _find_gutter(spans, grid_left)
    if gutter is None:
        return None
    fit = _fit(gutter[1])
    if fit is None or fit[2] < 0.98:
        return None
    return fit[0], fit[1]


def extract_work_week(path: str, year_hint: Optional[int] = None) -> "PdfIngestResult":
    """Parse a work-week or day print into real intervals.

    An event box's top and bottom edges are its start and end once the time
    gutter gives us pixels-to-hours, which is the whole reason this view is
    worth asking for: it yields the durations a month grid cannot.
    """
    import fitz

    doc = fitz.open(path)
    page = doc[0]
    result = PdfIngestResult(source_file=os.path.basename(path))
    result.calendar_view_type = detect_view_type(page)
    # The same header trick works here: Outlook prints each overlaid calendar's
    # name in that calendar's colour whatever the view.
    result.legend = extract_legend(page)
    result.selected_calendars = list(result.legend)

    spans = _page_spans(page)
    headers_all = [s for s in spans if s["text"].strip() in WEEKDAY_NAMES]
    grid_left = min((s["bbox"][0] for s in headers_all), default=None)
    scale = _gutter_scale(spans, grid_left)
    if scale is None:
        result.unresolved.append(
            "NO TIME GUTTER FOUND: could not map the page to clock hours, so no "
            "interval can be read. Re-export as Work Week with the time column "
            "visible.")
        return result
    slope, intercept = scale

    # Day columns from the weekday headers, plus their dates if printed.
    headers = sorted(
        (s for s in spans if s["text"].strip() in WEEKDAY_NAMES),
        key=lambda s: s["bbox"][0])
    if not headers:
        result.unresolved.append("NO DAY COLUMNS FOUND in a time-gridded view.")
        return result
    columns = [s["bbox"][0] for s in headers]
    col_width = (
        (columns[-1] - columns[0]) / max(1, len(columns) - 1)
        if len(columns) > 1 else page.rect.width - columns[0])

    dates = _column_dates(spans, columns, headers, year_hint)
    result.visible_date_range = (
        f"{min(dates.values()).isoformat()} to {max(dates.values()).isoformat()}"
        if dates else "unknown")

    # Event boxes: filled rectangles in a calendar hue, tall enough to be an
    # event rather than a rule or a header band.
    for drawing in page.get_drawings():
        hexc = _hex_of(drawing.get("fill"))
        if hexc is None or hexc not in _COLOR_TO_HUE:
            continue
        r = drawing["rect"]
        if r.height < 4 or r.width < 8:
            continue
        ci = min(range(len(columns)), key=lambda i: abs(columns[i] - r.x0))
        if not (columns[ci] - 6 <= r.x0 <= columns[ci] + col_width):
            continue
        day = dates.get(ci)
        if day is None:
            continue
        start_h = slope * r.y0 + intercept
        end_h = slope * r.y1 + intercept
        if end_h <= start_h:
            continue
        hue, tone = _COLOR_TO_HUE[hexc]
        result.entries.append(
            PdfEntry(
                day=day.isoformat(),
                start_time=_hours_to_clock(start_h),
                end_time=_hours_to_clock(end_h),
                status_label="tentative" if tone == "pale" else "busy",
                event_title=None,
                calendar_color_id=hue,
                participant=None,
                confidence_score=0.8,
                evidence_text=f"{_hours_to_clock(start_h)}-{_hours_to_clock(end_h)}",
                uncertain=False,
            )
        )

    if not result.entries:
        result.unresolved.append(
            "NO EVENT BLOCKS DETECTED. If the calendar really is empty this is "
            "correct; otherwise check the export used a colour theme.")
    return result


def _column_dates(spans, columns, headers, year_hint):
    """Dates printed under each weekday header, if the export shows them."""
    out = {}
    header_y = headers[0]["bbox"][3]
    for s in spans:
        if s["bbox"][1] > header_y + 40 or s["bbox"][1] < header_y - 20:
            continue
        m = re.match(r"^([A-Z][a-z]{2})?\s*(\d{1,2})$", s["text"].strip())
        if not m:
            continue
        ci = min(range(len(columns)), key=lambda i: abs(columns[i] - s["bbox"][0]))
        month = MONTHS.get(m.group(1)) if m.group(1) else None
        if month and year_hint:
            try:
                out[ci] = date(year_hint, month, int(m.group(2)))
            except ValueError:
                pass
    if not out and year_hint:
        # No dates printed: fall back to the current week, which is what an
        # undated work-week export means in practice.
        monday = date.today() - timedelta(days=date.today().weekday())
        for i in range(len(columns)):
            out[i] = monday + timedelta(days=i)
    return out


def _hours_to_clock(hours: float) -> str:
    hours = max(0.0, min(23.999, hours))
    h = int(hours)
    m = int(round((hours - h) * 60))
    if m == 60:
        h, m = h + 1, 0
    return f"{h:02d}:{m:02d}"


def load(path: str, year_hint: Optional[int] = None) -> "PdfIngestResult":
    """Parse any Outlook PDF print, choosing the right reader for its view."""
    import fitz

    doc = fitz.open(path)
    view = detect_view_type(doc[0])
    doc.close()
    if view in (VIEW_WORK_WEEK, VIEW_DAY):
        return extract_work_week(path, year_hint)
    return extract(path, year_hint)


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
