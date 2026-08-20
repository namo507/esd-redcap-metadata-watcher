"""Generate a synthetic Outlook-style month-grid PDF.

The lab's real export cannot be a test fixture: a month grid prints event
titles for every overlaid calendar, so committing one would publish six
colleagues' meeting subjects in a public repo — the exact disclosure this
project exists to prevent. This redraws the same geometry with invented events
so the month reader stays covered.
"""
from __future__ import annotations

import calendar as _cal
from datetime import date

import fitz

COL_X = [30.0, 112.0, 194.0, 276.0, 358.0, 440.0, 522.0]
HEADER_Y = 76.0
ROW_TOP = 96.0
ROW_H = 108.0
DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

HUES = ["0F6CBD", "038387", "0078D4", "F7630C", "00CC6A", "FDE300", "69797E"]


def rgb(hexc: str):
    return tuple(int(hexc[i:i + 2], 16) / 255 for i in (0, 2, 4))


def build(path: str, year: int = 2026, month: int = 8, events_per_day: int = 3) -> str:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)

    page.insert_text((30, 40), f"{_cal.month_name[month]} {year}", fontsize=14)
    page.insert_text((190, 40), "Calendar, Bell, Margaret, Puttock, Lauren", fontsize=11)

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

            for k in range(events_per_day if day.month == month else 1):
                ey = y + 14 + k * 13
                hue = HUES[(day.day + k) % len(HUES)]
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
