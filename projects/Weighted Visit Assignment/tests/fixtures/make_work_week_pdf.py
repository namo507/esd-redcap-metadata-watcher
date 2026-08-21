"""Generate a synthetic Outlook-style work-week PDF.

The lab's real exports cannot be committed -- they carry event titles for
everyone overlaid on the page -- so this redraws the same geometry Outlook
draws: a printed date range, abbreviated day headers, an hour gutter, an
all-day banner band, and event boxes whose height is their duration and whose
fill is a shade of their calendar's colour.

It reproduces the two things that make the real file readable at all: the
legend hidden in the header (each calendar's name drawn in its own colour) and
the policy calendars overlaid alongside the people.
"""
from __future__ import annotations

import fitz

GUTTER_X = 11.0
COL_X = [33.8, 147.5, 261.3, 375.0, 488.7]
COL_W = 113.7
TOP_Y = 152.7          # y of the 8 AM label
PX_PER_HOUR = 70.5
BANNER_TOP = 110.0
BANNER_H = 8.0
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]

# Calendar name -> its colour, exactly as Outlook prints the header.
LEGEND = [
    ("Calendar", "469DF5"),
    ("Bell, Margaret", "D03337"),
    ("Puttock, Lauren", "8E562E"),
    ("Oak, Sanjana", "BE0077"),
    ("Tous, Sofia", "038286"),
    ("Soto, Morgan", "FDE300"),
    ("Lucas-Mariano, Ramiro", "69787D"),
    ("Clinician Shifts", "F6620C"),
    ("PSYCHOLOGY, ESDI LAB", "00CC6A"),
    ("Offered Times ESD", "0078D4"),
]
COLOUR = dict(LEGEND)

# (day index, start hour, end hour, calendar name, shade factor)
# The shade factor is how Outlook darkens a box; 1.0 is the raw colour.
EVENTS = [
    (0, 9.0, 10.0, "Bell, Margaret", 0.3),
    (0, 13.5, 15.0, "Tous, Sofia", 0.3),
    (1, 8.0, 8.5, "Oak, Sanjana", 0.3),
    (2, 10.0, 12.0, "Puttock, Lauren", 0.3),
    (3, 15.0, 17.0, "Soto, Morgan", 0.3),
    (4, 11.0, 11.5, "Lucas-Mariano, Ramiro", 0.3),
    # Policy calendars.
    (0, 13.0, 16.0, "Offered Times ESD", 0.3),
    (1, 13.0, 16.0, "Offered Times ESD", 0.3),
    (0, 9.0, 17.0, "Clinician Shifts", 0.3),
    (0, 9.0, 11.0, "PSYCHOLOGY, ESDI LAB", 0.3),
    (2, 14.0, 16.0, "PSYCHOLOGY, ESDI LAB", 0.3),
]

# (day index, span in days, calendar the banner is posted on, banner text)
# The lab posts absence notices on a shared calendar, so the banner's colour
# says nothing about who it concerns -- only the text does. "Free" and an
# ordinary subject are included to prove neither is read as an absence.
BANNERS = [
    (0, 1, "Oak, Sanjana", "Free"),
    (1, 2, "PSYCHOLOGY, ESDI LAB", "Sofia Unavailable for Visits"),
    (3, 1, "PSYCHOLOGY, ESDI LAB", "Maggie Unavailable for Visits"),
    (2, 1, "PSYCHOLOGY, ESDI LAB", "Ramiro Out"),
    (4, 1, "Bell, Margaret", "Grant meeting with Sofia"),
]


def y_for(hour: float) -> float:
    return TOP_Y + (hour - 8.0) * PX_PER_HOUR


def rgb(hexc: str, factor: float = 1.0):
    return tuple(int(hexc[i:i + 2], 16) / 255 * factor for i in (0, 2, 4))


def build(path: str, first_day: int = 17, month: int = 8, year: int = 2026,
          coloured_legend: bool = True, with_policy: bool = True) -> str:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)

    page.insert_text(
        (13.8, 20.5),
        f"{month}/{first_day}/{year} to {month}/{first_day + 4}/{year}",
        fontsize=11, color=(0.25, 0.25, 0.25))

    x, y = 30.3, 38.4
    for label, hexc in LEGEND:
        if not with_policy and label in (
                "Clinician Shifts", "PSYCHOLOGY, ESDI LAB", "Offered Times ESD"):
            continue
        if x + 6.2 * len(label) > 596:
            x, y = 13.8, y + 14.5
        page.insert_text((x, y), label + ",", fontsize=11,
                         color=rgb(hexc) if coloured_legend else (0.25, 0.25, 0.25))
        x += 6.2 * len(label) + 10

    for hour in range(8, 18):
        label = f"{(hour - 1) % 12 + 1} {'AM' if hour < 12 else 'PM'}"
        page.insert_text((GUTTER_X, y_for(hour)), label, fontsize=5.3)

    for i, name in enumerate(DAYS):
        page.insert_text((COL_X[i], 87.0), name, fontsize=5.3)
        page.insert_text((COL_X[i], 96.6), str(first_day + i), fontsize=7.1)

    for row, (day, span, label, text) in enumerate(BANNERS):
        if not with_policy and label == "PSYCHOLOGY, ESDI LAB":
            continue
        top = BANNER_TOP + row * (BANNER_H + 1.3)
        page.draw_rect(
            fitz.Rect(COL_X[day] - 4, top, COL_X[day] - 4 + span * COL_W - 8,
                      top + BANNER_H),
            color=None, fill=rgb(COLOUR[label], 0.3))
        page.insert_text((COL_X[day] + 2, top + 6.2), text, fontsize=6.2)

    for day, start, end, label, factor in EVENTS:
        if not with_policy and label in (
                "Clinician Shifts", "PSYCHOLOGY, ESDI LAB", "Offered Times ESD"):
            continue
        page.draw_rect(
            fitz.Rect(COL_X[day] - 4, y_for(start), COL_X[day] + COL_W - 12,
                      y_for(end)),
            color=None, fill=rgb(COLOUR[label], factor))

    doc.save(path)
    doc.close()
    return path


if __name__ == "__main__":
    print(build("/tmp/work_week_sample.pdf"))
