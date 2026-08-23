"""Read a photographed or screenshotted calendar.

A PDF print is the better input and this module says so: its event boxes are
vector rectangles and its hour column is vector text, so times come out exact.
An image has neither. What it has is geometry -- coloured blocks arranged on a
grid -- and geometry is enough to recover *approximately* when each block sits,
provided something establishes what the top and bottom of the grid mean in
clock terms.

So the pipeline is deliberately split:

* **Blocks and columns** come from the pixels, via OpenCV. This part is
  reliable: a calendar event is a saturated rectangle on a pale ground.
* **The time axis** needs the hour labels read. If an OCR engine is installed
  this module uses it; if not, the caller supplies the visible range, which is
  one thing a person can see at a glance.

Everything produced here is marked uncertain and lands in the review queue,
whatever route it took. Vector extraction is measurement; this is inference,
and the difference should be visible to whoever is trusting the result.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from .ingest_outlook_pdf import PdfEntry, PdfIngestResult, _nearest_hue, match_colour

# A block has to be at least this tall and wide to be an event rather than a
# rule, a border or compression noise.
MIN_BLOCK_W = 14
MIN_BLOCK_H = 8
# Saturation floor for "this is a coloured event, not the page".
MIN_SAT = 30
MIN_VAL = 25


def ocr_available() -> bool:
    """Whether an OCR engine is installed and usable."""
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:      # noqa: BLE001 - any failure means "not usable"
        return False


@dataclass
class ImageGrid:
    """What the pixels said about the calendar's shape."""

    left: int
    top: int
    right: int
    bottom: int
    columns: List[Tuple[int, int]] = field(default_factory=list)

    @property
    def width(self) -> int:
        return max(1, self.right - self.left)

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.top)


def _load(path: str):
    import cv2

    image = cv2.imread(path, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("That image could not be opened.")
    return image


def find_blocks(image) -> List[Tuple[int, int, int, int, Tuple[int, int, int]]]:
    """Coloured event rectangles, as (x, y, w, h, rgb).

    Threshold on saturation rather than edges: a calendar is mostly white with
    a few strongly coloured blocks, and that is a far more stable signal than
    trying to find every line on a screenshot that may be scaled, compressed or
    photographed at an angle.
    """
    import cv2
    import numpy as np

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([0, MIN_SAT, MIN_VAL]),
                       np.array([179, 255, 255]))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < MIN_BLOCK_W or h < MIN_BLOCK_H:
            continue
        # A solid block fills its bounding box; a word does not. Without this
        # test the coloured calendar names in the header come through as
        # "events", and being at the top of the page they drag the time axis
        # with them.
        if cv2.contourArea(contour) < 0.6 * w * h:
            continue
        # Median colour inside, which ignores text drawn on top of a block.
        patch = image[y:y + h, x:x + w].reshape(-1, 3)
        b, g, r = np.median(patch, axis=0)
        rgb = (int(r), int(g), int(b))
        if min(rgb) > 235 or max(rgb) < 18:
            continue          # page or ink, not a calendar colour
        # How many different calendars are inside this block. Events stacked in
        # one column merge into a single contour, and taking the median then
        # names whichever covered the most pixels -- crediting a whole day to
        # one person when five calendars are involved. Counting distinct hues
        # catches that, and ignores the text drawn on top because letters are
        # neutral and match no calendar colour.
        out.append((x, y, w, h, rgb, _hue_shares(patch)))
    return out


def _hue_shares(patch) -> Dict[str, float]:
    """Share of a block's pixels belonging to each calendar hue."""
    import numpy as np

    step = max(1, len(patch) // 400)          # a sample is enough, and is fast
    counts: Dict[str, int] = {}
    sampled = patch[::step]
    for b, g, r in sampled:
        hit = _nearest_hue((int(r) << 16) | (int(g) << 8) | int(b))
        if hit:
            counts[hit[0]] = counts.get(hit[0], 0) + 1
    total = max(1, len(sampled))
    return {hue: n / total for hue, n in counts.items()}


def find_grid(image, blocks=None) -> Optional[ImageGrid]:
    """The calendar's ruled area, found from its own grid lines.

    Deriving this from the event blocks instead was the obvious shortcut and it
    is wrong: the topmost coloured thing on an Outlook page is the legend in the
    header, so the time axis ended up anchored to the header and every event
    came out at the top of the day. Lines are what actually define the grid, and
    on a screenshot they survive compression perfectly well.

    Returns None when no ruled grid is visible, which is the honest answer for a
    photograph of a wall planner.
    """
    import cv2
    import numpy as np

    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # A plain light threshold, not an adaptive one. Outlook draws its hour rules
    # in a very pale grey, and adaptive thresholding dropped them while happily
    # keeping the darker rules around the all-day banner -- so the "grid" came
    # out as the banner band, a hundred pixels tall and in the wrong place.
    binary = ((gray <= 245).astype(np.uint8)) * 255

    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, width // 12), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, height // 12)))
    h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
    v_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel)

    def _groups(mask, axis, min_run):
        counts = mask.sum(axis=axis) / 255
        hits = [i for i, c in enumerate(counts) if c >= min_run]
        groups = []
        for i in hits:
            if groups and i - groups[-1][-1] <= 3:
                groups[-1].append(i)
            else:
                groups.append([i])
        return groups

    def _positions(mask, axis, min_run):
        return [int(sum(g) / len(g)) for g in _groups(mask, axis, min_run)]

    cols = _positions(v_lines, 0, height * 0.35)
    if len(cols) < 2:
        return None

    # The day separators sweep the whole ruled band, so their own vertical
    # extent finds it. Horizontal rules were the obvious source and a worse one
    # for this first pass: an all-day band above the grid contributes its own
    # rules, and event blocks contribute edges, so the longest evenly spaced run
    # of them could settle on the wrong stripe of the page entirely.
    ink = v_lines.max(axis=1)
    rows_on = [i for i, v in enumerate(ink) if v > 0]
    if len(rows_on) < 20:
        return None
    top, bottom = rows_on[0], rows_on[-1]
    left, right = cols[0], cols[-1]
    if bottom - top < 40 or right - left < 40:
        return None

    # That band is where the clock is drawn; it is not the clock. A separator
    # runs on through the all-day strip above the first hour rule and a little
    # past the last, so the band is taller than the hours it holds -- twelve
    # pixels at 150dpi on the work-week fixture, where a banner overlaps the 8 AM
    # rule and touches a separator. Reading the band's edges as the first and
    # last hour stretches the pixels-per-hour a stated range is mapped through,
    # and every block then reads short: 15:00-17:00 came back as 15:00-16:55,
    # and the missing five minutes look free. The hour rules are what the labels
    # are written against, so once they are in hand they are the honest bounds.
    thin = max(3, height // 300)
    rules = _rule_lattice(
        [int(sum(g) / len(g))
         for g in _groups(h_lines, 1, (right - left) * 0.6) if len(g) <= thin],
        top, bottom)
    if rules:
        top, bottom = rules[0], rules[-1]
    return ImageGrid(left, top, right, bottom, [])


def _rule_lattice(rows: Sequence[int], top: int, bottom: int) -> List[int]:
    """The hour rules bounding the timed area, or [] if they cannot be trusted.

    An hour grid is uniform by construction, so its rules are recognisable as a
    lattice at one pitch and the chrome around them is not. Whole rules do go
    missing: where an hour is busy in every column the blocks fill the row and
    the pale rule drawn under them stops standing out. A gap of two or three
    pitches is therefore allowed, while anything landing off the pitch is not an
    hour rule and disqualifies the reading rather than bending it.

    The lattice also has to reach both ends of the band the separators sweep.
    A rule missed at the very top or the very bottom sits on the pitch like any
    other absence and would pass the test above, but it would shorten the axis
    by a whole hour -- the same failure this exists to remove, an order of
    magnitude larger. Unreached ends mean the rules are refused and the caller
    keeps the band, which is approximate rather than wrong.
    """
    inside = [r for r in rows if top - 4 <= r <= bottom + 4]
    if len(inside) < 4:
        return []
    gaps = [b - a for a, b in zip(inside, inside[1:])]
    pitch = min(gaps)
    if pitch <= 4:
        return []
    tolerance = max(3.0, pitch * 0.18)
    if any(abs(g - round(g / pitch) * pitch) > tolerance for g in gaps):
        return []
    if inside[0] - top > pitch / 2 or bottom - inside[-1] > pitch / 2:
        return []
    return inside


DAY_WORDS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN",
             "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY",
             "SATURDAY", "SUNDAY")


def read_day_columns(path: str, grid: "ImageGrid") -> List[float]:
    """Left edge of each day column, from the weekday headers above the grid.

    The obvious source is the vertical rules between columns, and on a real
    Outlook screenshot they are simply not there to find: line detection
    returns the edges of event blocks instead, and dividing the grid evenly
    between two of those put several events one column early -- a Thursday
    appointment reported as Wednesday, with the right time and the right person.

    The day headers are unambiguous and OCR reads them easily, so they anchor
    the columns instead. Each header is left-aligned in its own column, so its
    x position is that column's start.
    """
    if not ocr_available():
        return []
    import pytesseract
    from PIL import Image

    image = Image.open(path).convert("L")
    top = max(0, grid.top - 140)
    band = image.crop((0, top, image.width, min(image.height, grid.top + 20)))
    scale = 3
    band = band.resize((band.width * scale, band.height * scale), Image.LANCZOS)
    data = pytesseract.image_to_data(
        band, config="--psm 6", output_type=pytesseract.Output.DICT)

    xs = []
    for i, raw in enumerate(data["text"]):
        word = (raw or "").strip().strip(",.").upper()
        if word in DAY_WORDS:
            xs.append(data["left"][i] / scale)
    xs.sort()
    # Collapse a header that OCR split across two boxes.
    merged: List[float] = []
    for x in xs:
        if merged and x - merged[-1] < 30:
            continue
        merged.append(x)
    return merged


def read_time_axis(path: str, grid: "ImageGrid"):
    """Fit pixel y to clock hours by reading the hour column, if OCR is installed.

    Three things make this work where a naive whole-page OCR does not:

    * **Only the gutter is read.** A calendar is full of numbers -- dates,
      column headers, text inside events -- and scanning all of them and taking
      the smallest and largest looked reasonable and returned 1 AM to 12 PM for
      a page whose gutter runs 8 AM to 5 PM.
    * **The strip is upscaled first.** Hour labels are around ten pixels tall at
      screen resolution, which is below what Tesseract reads reliably.
    * **Positions are fitted, not just values.** Each recognised label carries a
      y coordinate, so the axis comes from a least-squares fit through them.
      That tolerates a label being misread or missed entirely, where taking the
      first and last does not.

    Returns ``(slope, intercept, first_hour, last_hour)``, or None. The hour
    range matters as much as the fit: the column separators often run through
    an all-day band above the timed grid and past its last rule, so the labels
    are the only thing that says which part of the page is actually clock time.
    """
    if not ocr_available():
        return None
    import re

    import numpy as np
    import pytesseract
    from PIL import Image

    strip_right = max(24, grid.left)
    image = Image.open(path).convert("L")
    if strip_right < 12 or grid.height < 20:
        return None
    strip = image.crop((0, max(0, grid.top - 40), strip_right,
                        min(image.height, grid.bottom + 40)))
    scale = 4
    strip = strip.resize((strip.width * scale, strip.height * scale),
                         Image.LANCZOS)

    data = pytesseract.image_to_data(
        strip, config="--psm 6", output_type=pytesseract.Output.DICT)

    label = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([AP])M?$", re.I)
    points = []
    words = data["text"]
    for i, raw in enumerate(words):
        text = (raw or "").strip().replace(" ", "")
        if not text:
            continue
        # Tesseract often splits "8 AM" into two boxes; stitch a bare number to
        # the meridiem that follows it on the same line.
        combined = text
        if combined.isdigit() and i + 1 < len(words):
            nxt = (words[i + 1] or "").strip()
            if nxt.upper() in ("AM", "PM", "A", "P"):
                combined = combined + nxt
        m = label.match(combined)
        if not m:
            continue
        hour = int(m.group(1)) % 12
        if m.group(3).upper() == "P":
            hour += 12
        clock = hour + int(m.group(2) or 0) / 60.0
        y_top = data["top"][i] / scale + max(0, grid.top - 40)
        y_mid = y_top + data["height"][i] / (2 * scale)
        points.append((y_mid, clock))

    if len(points) < 3:
        return None
    points.sort()
    clocks = [c for _, c in points]
    # A clock axis only ever goes forwards. Anything else is a misread.
    if any(b < a for a, b in zip(clocks, clocks[1:])):
        points = _longest_rising(points)
        if len(points) < 3:
            return None

    ys = np.array([p[0] for p in points], dtype=float)
    hs = np.array([p[1] for p in points], dtype=float)
    slope, intercept = np.polyfit(ys, hs, 1)
    if slope <= 0:
        return None
    predicted = slope * ys + intercept
    if float(np.max(np.abs(predicted - hs))) > 1.0:
        return None                 # the fit does not describe the labels
    return float(slope), float(intercept), float(hs.min()), float(hs.max())


def _longest_rising(points):
    """Longest run of labels whose clock time keeps increasing."""
    best, run = [], [points[0]]
    for prev, cur in zip(points, points[1:]):
        if cur[1] > prev[1]:
            run.append(cur)
        else:
            if len(run) > len(best):
                best = run
            run = [cur]
    return best if len(best) > len(run) else run


def extract(
    path: str,
    day_start: date,
    n_days: int = 5,
    hours: Optional[Tuple[float, float]] = None,
    legend: Optional[Dict[str, int]] = None,
) -> PdfIngestResult:
    """Read a calendar image into entries, all flagged uncertain.

    ``hours`` is the visible clock range. If it is not supplied and no OCR
    engine is installed, the whole thing stops rather than assuming one: a
    guessed axis would silently shift every event by hours, which is worse than
    refusing.
    """
    result = PdfIngestResult(source_file=os.path.basename(path))
    result.calendar_view_type = "image"

    image = _load(path)
    blocks = find_blocks(image)
    if not blocks:
        result.unresolved.append(
            "NO EVENT BLOCKS FOUND: nothing in this image looks like a coloured "
            "calendar entry. If the calendar is printed in black and white there "
            "is nothing here to read.")
        return result

    grid = find_grid(image, blocks)
    if grid is None:
        result.unresolved.append(
            "NO GRID FOUND: this image has no ruled calendar grid, so there is "
            "nothing to measure block positions against. A screenshot of the "
            "calendar view works; a photograph of a wall planner does not.")
        return result

    # The hour column, read directly, beats a stated range: it maps every pixel
    # rather than assuming the grid's top and bottom edges are the first and
    # last labels.
    axis = read_time_axis(path, grid)
    if axis is None and hours is None:
        result.unresolved.append(
            "TIME RANGE UNKNOWN: the hour column could not be read"
            + ("" if ocr_available() else " because no OCR engine is installed")
            + ". Say which hours the image covers, or print the calendar to PDF, "
              "where the times are exact.")
        return result
    span = max(0.5, (hours[1] - hours[0]) if hours else 1.0)
    # Columns from the day headers where OCR can read them, falling back to an
    # even division of the grid otherwise.
    starts = read_day_columns(path, grid)
    if len(starts) >= 2:
        pitch = (starts[-1] - starts[0]) / max(1, len(starts) - 1)
        columns = [(starts[i], starts[i] + pitch) for i in range(len(starts))]
        n_days = len(starts)
    else:
        step = grid.width / max(1, n_days)
        columns = [(grid.left + i * step, grid.left + (i + 1) * step)
                   for i in range(n_days)]

    # Only blocks inside the ruled area are events. The header legend sits above
    # it and is exactly what dragged the time axis wrong before.
    # Overlap the grid rather than sit strictly inside it. An event box is
    # commonly drawn a few pixels over the rule that bounds its column, and
    # requiring containment silently dropped a whole day's column.
    pad = max(6, int(grid.width * 0.01))
    inside = [
        b for b in blocks
        if b[1] >= grid.top - pad and b[1] + b[3] <= grid.bottom + pad
        and grid.left <= b[0] + b[2] / 2 <= grid.right
    ]
    if not inside:
        result.unresolved.append(
            "NO EVENTS INSIDE THE GRID: coloured areas were found, but all of "
            "them sit outside the ruled calendar area.")
        return result

    # Same correction the PDF reader makes: an event box is inset inside its
    # slot, so every edge reads a few minutes early by a constant amount. Fit
    # that constant off the blocks themselves rather than hardcoding it.
    from .ingest_outlook_pdf import _calibrate_offset

    raw = []
    for x, y, w, h, rgb, shares in sorted(inside, key=lambda b: (b[1], b[0])):
        if not (grid.top <= y + h / 2 <= grid.bottom):
            continue
        if axis is not None:
            midpoint = axis[0] * (y + h / 2) + axis[1]
            if not (axis[2] - 0.25 <= midpoint <= axis[3] + 1.25):
                continue
            a, b_ = axis[0] * y + axis[1], axis[0] * (y + h) + axis[1]
        else:
            a = hours[0] + (y - grid.top) / grid.height * span
            b_ = hours[0] + (y + h - grid.top) / grid.height * span
        raw.append((x, y, w, h, rgb, shares, a, b_))
    shift = _calibrate_offset([t for r in raw for t in (r[6], r[7])])
    result.axis_source = "ocr" if axis is not None else "stated"

    mixed = 0
    for x, y, w, h, rgb, shares, start, end in raw:
        # An event lives in one day column. Something spanning several is
        # chrome -- a header bar or the all-day strip -- and one of those came
        # through as a ten-minute appointment.
        pitch = (columns[0][1] - columns[0][0]) if columns else grid.width
        if w > pitch * 1.35:
            continue
        centre = x + w / 2
        ci = min(range(len(columns)),
                 key=lambda i: abs((columns[i][0] + columns[i][1]) / 2 - centre))
        day = day_start + timedelta(days=ci)
        start += shift
        end += shift
        if end - start < 0.15:      # under nine minutes: chrome, not an event
            continue
        rgb_int = (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
        # Two or more calendars each holding a real share of the block means
        # the board can see the time is taken but not whose it is.
        blended = len([h for h in shares.values() if h >= 0.18]) > 1
        label = None
        hue = None
        if not blended:
            if legend:
                hit = match_colour(rgb_int, legend)
                label = hit[0] if hit else None
            hue = _nearest_hue(rgb_int)
        else:
            mixed += 1
        result.entries.append(PdfEntry(
            day=day.isoformat(),
            start_time=_clock(start),
            end_time=_clock(end),
            status_label="busy",
            event_title=None,
            calendar_color_id=hue[0] if hue else None,
            participant=None,
            confidence_score=0.2 if blended else 0.45,
            evidence_text=(f"image block {w}x{h}px"
                           + (" (several calendars overlap here)" if blended else "")),
            uncertain=True,
            calendar_label=label,
        ))

    if mixed:
        result.unresolved.append(
            f"OVERLAPPING EVENTS on {mixed} block(s): more than one calendar's "
            "colour appears inside the same block, so the board can tell that "
            "the time is taken but not whose it is. Those blocks are left "
            "unattributed rather than credited to whichever colour covered the "
            "most pixels.")

    how = ("the hour column was read by OCR" if axis is not None
           else "the clock axis was interpolated from the range you gave")
    result.unresolved.append(
        "READ FROM AN IMAGE: block positions were measured in pixels and "
        + how + ", so every time here is approximate. Each entry needs "
        "confirming before it counts. A PDF print of the same calendar is read "
        "exactly and needs none of this.")
    return result


def _clock(hours: float) -> str:
    hours = max(0.0, min(23.99, hours))
    # Snap to five minutes: pixel measurement is not precise enough to justify
    # printing a time to the minute.
    total = int(round(hours * 60 / 5.0)) * 5
    return f"{total // 60:02d}:{total % 60:02d}"
