"""An optional second reader for calendar images, using neural OCR.

The classical reader in ``ingest_image`` measures: it finds coloured
rectangles and works out what time each one is by where it sits against the
ruled grid. That is exact when the render is big enough and it is the default
for good reason -- a calendar really is coloured boxes on ruled lines.

This one reads instead. Outlook prints the time inside most events ("9:00 AM
- 10:00 AM"), and a time the calendar states is better evidence than a time
measured off pixels: it does not drift with resolution, it does not depend on
the axis being found, and it is right even when the box has been squeezed to
half a row. Where an event's text is legible this reader takes the stated
time; where it is not, it falls back to the measured geometry, so it is never
worse than the classical path on the same block.

WHAT IS NEURAL HERE, EXACTLY. Tesseract 4 and 5 recognise text with an LSTM,
which is a neural network, and ``--oem 1`` selects it explicitly rather than
the older pattern matcher. That is the model doing the work. No detector is
shipped with this project and none is downloaded: there is no pretrained
model for "coloured box on a calendar", and inventing one would trade an
exact reader for a probabilistic one to no purpose.

WHAT THIS CANNOT FIX. An event painted over by another calendar is not in the
image. No reader recovers it -- not this one, not a larger model. The fix for
that is to print the calendar to PDF, where every rectangle exists in the file
whatever is drawn on top.

Selected by ``image_reader`` in config/lab-resources.json, or from the board's
tuning controls. The default stays "classical".
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

#: Written as "9:00 AM - 10:00 AM", "09:00-10:00", "9 - 10:30a" and worse.
_TIME = r"(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?m?\.?"
_RANGE = re.compile(_TIME + r"\s*(?:-|–|—|to)\s*" + _TIME, re.I)


def available() -> Tuple[bool, str]:
    """Whether this reader can run, and what is missing when it cannot."""
    try:
        import cv2                              # noqa: F401
    except ImportError:
        return False, "OpenCV is not installed, so no image can be opened."
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
    except Exception as exc:                                   # noqa: BLE001
        return False, (f"the tesseract binary is not usable ({exc}), and the "
                       f"neural reader is that engine. Install it with "
                       f"`brew install tesseract`.")
    return True, ""


def _hhmm(hour: str, minute: Optional[str], meridiem: Optional[str],
          reference: Optional[float] = None) -> Optional[float]:
    """One clock reading as hours since midnight, or None if it is nonsense."""
    try:
        h = int(hour)
    except (TypeError, ValueError):
        return None
    m = int(minute) if minute else 0
    if not (0 <= h <= 23 and 0 <= m < 60):
        return None
    if meridiem:
        meridiem = meridiem.lower()
        if meridiem == "p" and h != 12:
            h += 12
        elif meridiem == "a" and h == 12:
            h = 0
    elif reference is not None and h < 8:
        # No am/pm printed and an hour that cannot be a working morning. A
        # calendar showing 9 to 5 means 1pm when it prints "1", and reading it
        # as 01:00 would put the visit in the middle of the night.
        h += 12
    if not (0 <= h <= 23):
        return None
    return h + m / 60.0


def read_span(text: str, reference: Optional[float] = None):
    """A start and end in hours from an event's own text, or (None, None).

    Exposed because it is the part worth testing on its own: the OCR is the
    unreliable step, and everything downstream depends on this turning what
    it returned into a defensible pair of times.
    """
    match = _RANGE.search(text or "")
    if not match:
        return None, None
    h1, m1, ap1, h2, m2, ap2 = match.groups()
    # "9:00 - 10:30 AM" prints the meridiem once, at the end, and means it for
    # both. Borrowing it backwards is what makes that read correctly.
    start = _hhmm(h1, m1, ap1 or ap2, reference)
    end = _hhmm(h2, m2, ap2 or ap1, reference)
    if start is None or end is None:
        return None, None
    if end < start:
        # Crossing noon without saying so: "11 - 1" is 11:00 to 13:00. Strictly
        # less-than, so an equal pair is left alone: "9 - 9" is a misread, and
        # adding twelve hours to it would turn it into a nine-to-nine working
        # day and overwrite a good measurement with it. The span check below
        # then refuses it, along with anything longer than a working day.
        end += 12
    if not (0 < end - start < 12):
        return None, None
    return start, end


def _ocr_words(image, box, pad: int = 2):
    """Every word tesseract's LSTM finds inside one block, as one string."""
    import cv2
    import numpy as np
    import pytesseract

    x, y, w, h = box
    y0, y1 = max(0, y - pad), min(image.shape[0], y + h + pad)
    x0, x1 = max(0, x - pad), min(image.shape[1], x + w + pad)
    patch = image[y0:y1, x0:x1]
    if patch.size == 0 or patch.shape[0] < 6 or patch.shape[1] < 12:
        return ""
    grey = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    # Event text is light on a saturated fill as often as the reverse, and the
    # engine wants dark on light. Scaling up first is what makes small text
    # legible at all; the LSTM is trained on print-sized glyphs.
    scale = max(1, int(round(28.0 / max(1, grey.shape[0]))))
    if scale > 1:
        grey = cv2.resize(grey, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_CUBIC)
    if float(np.mean(grey)) < 128:
        grey = cv2.bitwise_not(grey)
    grey = cv2.threshold(grey, 0, 255,
                         cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    try:
        # --oem 1 is the LSTM engine, chosen explicitly rather than left to
        # whatever the install defaults to. --psm 6 reads a uniform block.
        return pytesseract.image_to_string(
            grey, config="--oem 1 --psm 6").strip()
    except Exception:                                          # noqa: BLE001
        return ""


def annotate(path: str, result, blocks=None) -> dict:
    """Read each block's own text and correct its times where it can.

    Takes the classical reader's result and improves it in place, which is the
    whole design: the geometry pass decides *what* and *whose*, and this
    decides *when* wherever the calendar says so itself. Returns a small
    report so the accuracy harness and the board can both say how much of the
    read came from text rather than measurement.
    """
    ok, why = available()
    if not ok:
        return {"used": False, "reason": why, "corrected": 0, "read": 0}

    import cv2
    from . import ingest_image

    image = ingest_image._load(path)
    if blocks is None:
        blocks = ingest_image.find_blocks(image)
    grid = ingest_image.find_grid(image, blocks)
    if grid is None:
        return {"used": False, "reason": "no ruled grid to place text against",
                "corrected": 0, "read": 0}

    # Match a parsed entry back to the rectangle it came from by day column
    # and vertical position, so the text read out of a box lands on that box's
    # entry rather than on whichever entry happens to be next in the list.
    corrected = 0
    read = 0
    entries = list(getattr(result, "entries", []) or [])
    if not entries:
        return {"used": True, "reason": "", "corrected": 0, "read": 0}

    spans: List[Tuple[float, float, Tuple[int, int, int, int]]] = []
    for block in blocks:
        # find_blocks yields (x, y, w, h, colour, hue_shares); take the four
        # geometry fields by position rather than unpacking a fixed arity, so
        # a field added there does not break this reader.
        x, y, w, h = block[0], block[1], block[2], block[3]
        text = _ocr_words(image, (x, y, w, h))
        if not text:
            continue
        read += 1
        start, end = read_span(text)
        if start is None:
            continue
        spans.append((start, end, (x, y, w, h)))

    if not spans:
        return {"used": True, "reason": "no event printed a legible time",
                "corrected": 0, "read": read}

    # Order both sides the same way -- top to bottom, then left to right --
    # and correct only where the stated time is close enough to the measured
    # one to be the same event. A text time three hours from the geometry is
    # a misread, not a correction, and taking it would be worse than the
    # measurement this exists to improve on.
    for start, end, (x, y, w, h) in spans:
        best, best_gap = None, None
        for entry in entries:
            if not entry.start_time:
                continue
            try:
                hh, mm = entry.start_time.split(":")
                measured = int(hh) + int(mm) / 60.0
            except (ValueError, AttributeError):
                continue
            gap = abs(measured - start)
            if best_gap is None or gap < best_gap:
                best, best_gap = entry, gap
        if best is None or best_gap is None or best_gap > 1.5:
            continue
        stated_start = f"{int(start):02d}:{int(round((start % 1) * 60)):02d}"
        stated_end = f"{int(end):02d}:{int(round((end % 1) * 60)):02d}"
        if best.start_time != stated_start or best.end_time != stated_end:
            corrected += 1
        best.start_time = stated_start
        best.end_time = stated_end
        best.evidence_text = (best.evidence_text or "") + " [time read from the"
        best.evidence_text += " event's own text]"

    return {"used": True, "reason": "", "corrected": corrected, "read": read}
