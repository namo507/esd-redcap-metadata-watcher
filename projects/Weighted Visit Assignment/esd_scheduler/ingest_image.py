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
MIN_SAT = 60
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
        out.append((x, y, w, h, rgb))
    return out


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
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                   cv2.THRESH_BINARY_INV, 15, 10)

    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, width // 12), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, height // 12)))
    h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
    v_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel)

    def _positions(mask, axis, min_run):
        counts = mask.sum(axis=axis) / 255
        hits = [i for i, c in enumerate(counts) if c >= min_run]
        groups, out = [], []
        for i in hits:
            if groups and i - groups[-1][-1] <= 3:
                groups[-1].append(i)
            else:
                groups.append([i])
        for g in groups:
            out.append(int(sum(g) / len(g)))
        return out

    rows = _positions(h_lines, 1, width * 0.45)
    cols = _positions(v_lines, 0, height * 0.35)
    if len(rows) < 2 or len(cols) < 2:
        return None

    top, bottom = rows[0], rows[-1]
    left, right = cols[0], cols[-1]
    return ImageGrid(left, top, right, bottom, [])


def read_hours(path: str) -> Optional[Tuple[float, float]]:
    """Read the first and last hour label, when an OCR engine is installed."""
    if not ocr_available():
        return None
    import re

    import pytesseract
    from PIL import Image

    text = pytesseract.image_to_string(Image.open(path))
    found = []
    for match in re.finditer(r"\b(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\b", text, re.I):
        hour = int(match.group(1)) % 12
        if match.group(3).upper() == "PM":
            hour += 12
        found.append(hour + int(match.group(2) or 0) / 60.0)
    if len(found) < 2:
        return None
    return min(found), max(found)


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

    if hours is None:
        hours = read_hours(path)
    if hours is None:
        result.unresolved.append(
            "TIME RANGE UNKNOWN: no OCR engine is installed, so the hour column "
            "could not be read. Say which hours the image covers and it can be "
            "read, or print the calendar to PDF, where the times are exact.")
        return result

    grid = find_grid(image, blocks)
    if grid is None:
        result.unresolved.append(
            "NO GRID FOUND: this image has no ruled calendar grid, so there is "
            "nothing to measure block positions against. A screenshot of the "
            "calendar view works; a photograph of a wall planner does not.")
        return result

    span = max(0.5, hours[1] - hours[0])
    # Even division rather than clustered line positions. A calendar's day
    # columns are equal by construction, and reading them off detected lines
    # split every rule into two columns because a drawn line is several pixels
    # wide.
    step = grid.width / max(1, n_days)
    columns = [(grid.left + i * step, grid.left + (i + 1) * step)
               for i in range(n_days)]

    # Only blocks inside the ruled area are events. The header legend sits above
    # it and is exactly what dragged the time axis wrong before.
    inside = [
        b for b in blocks
        if b[1] >= grid.top - 2 and b[1] + b[3] <= grid.bottom + 2
        and b[0] >= grid.left - 2 and b[0] + b[2] <= grid.right + 2
    ]
    if not inside:
        result.unresolved.append(
            "NO EVENTS INSIDE THE GRID: coloured areas were found, but all of "
            "them sit outside the ruled calendar area.")
        return result

    for x, y, w, h, rgb in sorted(inside, key=lambda b: (b[1], b[0])):
        centre = x + w / 2
        ci = min(range(len(columns)),
                 key=lambda i: abs((columns[i][0] + columns[i][1]) / 2 - centre))
        day = day_start + timedelta(days=ci)
        start = hours[0] + (y - grid.top) / grid.height * span
        end = hours[0] + (y + h - grid.top) / grid.height * span
        if end - start < 0.08:
            continue
        rgb_int = (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
        label = None
        if legend:
            hit = match_colour(rgb_int, legend)
            label = hit[0] if hit else None
        hue = _nearest_hue(rgb_int)
        result.entries.append(PdfEntry(
            day=day.isoformat(),
            start_time=_clock(start),
            end_time=_clock(end),
            status_label="busy",
            event_title=None,
            calendar_color_id=hue[0] if hue else None,
            participant=None,
            confidence_score=0.45,
            evidence_text=f"image block {w}x{h}px",
            uncertain=True,
            calendar_label=label,
        ))

    result.unresolved.append(
        "READ FROM AN IMAGE: block positions were measured in pixels and the "
        "clock axis was interpolated, so every time here is approximate. Each "
        "entry needs confirming before it counts. A PDF print of the same "
        "calendar is read exactly and needs none of this.")
    return result


def _clock(hours: float) -> str:
    hours = max(0.0, min(23.99, hours))
    # Snap to five minutes: pixel measurement is not precise enough to justify
    # printing a time to the minute.
    total = int(round(hours * 60 / 5.0)) * 5
    return f"{total // 60:02d}:{total % 60:02d}"
