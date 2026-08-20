"""Generate a synthetic Outlook-style work-week PDF.

The lab's own exports are month grids, so the work-week reader would otherwise
ship untested. This draws the geometry Outlook draws — an hour gutter, day
columns, and filled event boxes whose height is their duration — so the
positional time maths is checked against known answers.
"""
from __future__ import annotations

import fitz

GUTTER_X = 34.0
COL_X = [86.0, 190.0, 294.0, 398.0, 502.0]
COL_W = 96.0
TOP_Y = 96.0          # y of 8 AM
PX_PER_HOUR = 46.0
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

# Header labels drawn in their calendar's colour, exactly as Outlook prints them.
LEGEND = [
    ("Bell, Margaret", "0F6CBD"),
    ("Puttock, Lauren", "038387"),
    ("Oak, Sanjana", "0078D4"),
    ("Tous, Sofia", "F7630C"),
    ("Soto, Morgan", "00CC6A"),
    ("Lucas-Mariano, Ramiro", "FDE300"),
]

# (day index, start hour, end hour, hex fill)
EVENTS = [
    (0, 9.0, 10.0, "0F6CBD"),
    (0, 13.5, 15.0, "038387"),
    (1, 8.0, 8.5, "0078D4"),
    (2, 10.0, 12.0, "F7630C"),
    (3, 15.0, 17.0, "00CC6A"),
    (4, 11.0, 11.5, "FEF7B2"),   # pale -> tentative
]


def y_for(hour: float) -> float:
    return TOP_Y + (hour - 8.0) * PX_PER_HOUR


def rgb(hexc: str):
    return tuple(int(hexc[i:i + 2], 16) / 255 for i in (0, 2, 4))


def build(path: str, first_day: int = 17, month: str = "Aug",
          coloured_legend: bool = True) -> str:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)

    page.insert_text((40, 40), "August 2026", fontsize=14)
    # Outlook prints each overlaid calendar's name in that calendar's own
    # colour. That is the only legend the export carries, so the fixture has to
    # reproduce it or the attribution path goes untested.
    x, y = 150.0, 40.0
    for label, hexc in LEGEND:
        if x + 6.2 * len(label) > 596:   # wrap rather than run off the page
            x, y = 150.0, y + 14.0
        # coloured_legend=False reproduces an export whose header lost its
        # colours, which is the only case where hand-matching is still needed.
        page.insert_text((x, y), label + ",", fontsize=11,
                         color=rgb(hexc) if coloured_legend else (0.25, 0.25, 0.25))
        x += 6.2 * len(label) + 10

    for hour in range(8, 18):
        label = f"{(hour - 1) % 12 + 1} {'AM' if hour < 12 else 'PM'}"
        page.insert_text((GUTTER_X, y_for(hour) + 3), label, fontsize=7)

    for i, name in enumerate(DAYS):
        page.insert_text((COL_X[i], 70), name, fontsize=8)
        page.insert_text((COL_X[i], 84), f"{month} {first_day + i}", fontsize=7)

    for day, start, end, hexc in EVENTS:
        rect = fitz.Rect(COL_X[day] + 2, y_for(start), COL_X[day] + COL_W, y_for(end))
        page.draw_rect(rect, color=None, fill=rgb(hexc))

    doc.save(path)
    doc.close()
    return path


if __name__ == "__main__":
    print(build("/tmp/work_week_sample.pdf"))
