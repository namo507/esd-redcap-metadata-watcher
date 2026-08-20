"""Generate a synthetic Outlook-style month-grid PDF.

The lab's real export cannot be a test fixture: a month grid prints event
titles for every overlaid calendar, so committing one would publish six
colleagues' meeting subjects in a public repo — the exact disclosure this
project exists to prevent. This redraws the same geometry with invented events
so the month reader stays covered.
"""
from __future__ import annotations

import calendar as _cal
import random
from datetime import date

import fitz

COL_X = [30.0, 112.0, 194.0, 276.0, 358.0, 440.0, 522.0]
HEADER_Y = 76.0
ROW_TOP = 96.0
ROW_H = 108.0
DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Outlook prints each overlaid calendar's name in that calendar's own colour.
# That is the only legend the export carries, so the fixture reproduces it.
LEGEND = [
    ("Calendar", "0F6CBD"),
    ("Bell, Margaret", "038387"),
    ("Puttock, Lauren", "0078D4"),
    ("Oak, Sanjana", "F7630C"),
    ("Tous, Sofia", "00CC6A"),
    ("Soto, Morgan", "FDE300"),
    ("Lucas-Mariano, Ramiro", "69797E"),
]
HUES = [hexc for _, hexc in LEGEND]


def rgb(hexc: str):
    return tuple(int(hexc[i:i + 2], 16) / 255 for i in (0, 2, 4))


def build(path: str, year: int = 2026, month: int = 8, events_per_day: int = 5,
          vary: bool = False, seed: int = 7) -> str:
    """Draw a month grid.

    ``vary`` gives each calendar an uneven, deterministic load, which is what a
    demo grid needs to look like anything. Tests leave it off: they rely on a
    predictable row count to exercise the cut-off-cell detection.
    """
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)

    page.insert_text((30, 40), f"{_cal.month_name[month]} {year}", fontsize=14)
    x, y = 150.0, 40.0
    for label, hexc in LEGEND:
        if x + 6.2 * len(label) > 596:
            x, y = 150.0, y + 14.0
        page.insert_text((x, y), label + ",", fontsize=11, color=rgb(hexc))
        x += 6.2 * len(label) + 10

    for i, name in enumerate(DAYS):
        page.insert_text((COL_X[i], HEADER_Y), name, fontsize=8)

    weeks = _cal.Calendar(firstweekday=6).monthdatescalendar(year, month)
    label_month = None
    for ri, week in enumerate(weeks):
        for ci, day in enumerate(week):
            x, y = COL_X[ci], ROW_TOP + ri * ROW_H
            if day.month != label_month:
                text = f"{MONTH_ABBR[day.month]} {day.day}"
                label_month = day.month
            else:
                text = str(day.day)
            page.insert_text((x, y), text, fontsize=7)

            rng = random.Random((seed, day.toordinal()))
            n = events_per_day if day.month == month else 1
            if vary:
                n = rng.choice([0, 1, 2, 3, 3, 4, 5, 6]) if day.weekday() < 5 else 0
            for k in range(n):
                ey = y + 14 + k * 13
                hue = (rng.choice(HUES) if vary
                       else HUES[(day.day + k) % len(HUES)])
                page.draw_rect(
                    fitz.Rect(x, ey - 8, x + 2.7, ey + 2.6),
                    color=None, fill=rgb(hue),
                )
                hour = 9 + k * 2
                label = "Busy" if k % 3 else ("Tentative" if k else "Busy")
                page.insert_text((x + 6, ey), f"{hour}:00 AM  {label}", fontsize=6)

    doc.save(path)
    doc.close()
    return path


if __name__ == "__main__":
    print(build("/tmp/month_sample.pdf"))
