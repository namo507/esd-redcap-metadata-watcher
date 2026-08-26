"""Tests for holding a pixel read until somebody settles it.

`screenshot_uploads.auto_confirm` decides whether blocks measured off pixels
count the moment they are uploaded or wait to be confirmed. The setting was
flipped both ways during development and the held path was never exercised,
which is the worst state for it to be in: turning the gate on would have sent
every screenshot into a queue nobody had proved worked.

What has to hold, and what these check:

* an upload with the gate on applies nothing and queues everything;
* confirming a block puts that exact interval into that person's calendar;
* rejecting one leaves the calendar alone -- the failure that would make the
  gate worse than useless, because it would look like review while committing
  everything anyway;
* the queue names people, including somebody the lab is not scheduling. A row
  reading "C06" tells a scheduler nothing.

Run:  python3 tests/test_review_gate.py
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures"))

FIRST_DAY, MONTH, YEAR = 17, 8, 2026


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def needs(module):
    return importlib.util.find_spec(module) is not None


def _held_board(auto_confirm=False):
    """A board whose config is a copy, with the review gate set as asked."""
    tmp = tempfile.mkdtemp(prefix="review-gate-")
    for name in ("engine.json", "roster.json", "lab-resources.json"):
        shutil.copy(os.path.join(ROOT, "config", name), os.path.join(tmp, name))
    path = os.path.join(tmp, "lab-resources.json")
    with open(path, encoding="utf-8") as fh:
        lab = json.load(fh)
    lab.setdefault("screenshot_uploads", {})["auto_confirm"] = auto_confirm
    with open(path, encoding="utf-8") as fh:
        pass
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(lab, fh, indent=2)

    os.environ["ESD_ENGINE_PATH"] = os.path.join(tmp, "engine.json")
    os.environ["ESD_ROSTER_PATH"] = os.path.join(tmp, "roster.json")
    os.environ["ESD_RESOURCES_PATH"] = path
    os.environ["ESD_MODE"] = "demo"
    # The colour map is *written* below, so it has to point at the temporary
    # copy too. Without this the suite overwrites the lab's real confirmed
    # map with a fixture's, which is a test quietly editing production data.
    os.environ["ESD_COLOR_MAP_PATH"] = os.path.join(tmp, "calendar-colors.json")

    import fitz
    import make_work_week_pdf as fx
    from backend.session import LabSession
    from esd_scheduler.calendar_import import ColorMap, import_pdf

    pdf = os.path.join(tmp, "week.pdf")
    fx.build(pdf, first_day=FIRST_DAY, month=MONTH, year=YEAR)
    doc = fitz.open(pdf)
    png = os.path.join(tmp, "week.png")
    doc[0].get_pixmap(dpi=150).save(png)
    doc.close()

    session = LabSession(db_path=os.path.join(tmp, "board.db"))

    # A screenshot carries no legend, so it needs a confirmed colour map. The
    # PDF's own legend supplies one, which is the real workflow.
    result = import_pdf(pdf, coordinators=session.state.coordinators,
                        year_hint=YEAR)
    mapping = {}
    for label, hue in (result.legend or {}).items():
        if "," in label:
            surname, _, forename = label.partition(",")
            label = f"{forename.strip()} {surname.strip()}"
        cid = session.roster_config.resolve(label)
        if cid:
            mapping[hue] = cid
    ColorMap(mapping=mapping, confirmed=True,
             confirmed_by="review gate test").save(
                 os.environ["ESD_COLOR_MAP_PATH"])

    with open(png, "rb") as fh:
        upload = session.upload_calendar_pdf("week.png", fh.read())
    return session, upload


def _intervals(session, cid):
    cal = session.state.calendars.get(cid)
    return {(b.start, b.end) for b in (cal.blocks if cal else [])}


def test_the_gate_applies_nothing_and_queues_everything():
    if not (needs("fitz") and needs("cv2")):
        print("SKIP test_the_gate_applies_nothing_and_queues_everything")
        return
    session, upload = _held_board(auto_confirm=False)
    expect(upload["block_count"] > 0,
           "the fixture screenshot produced no blocks to review")
    expect(upload["applied_blocks"] == 0,
           f"the gate is on but {upload['applied_blocks']} blocks were applied "
           f"without anybody confirming them")
    pending = session.imports()["pending_review"]
    expect(len(pending) == upload["block_count"],
           f"{upload['block_count']} blocks were read but {len(pending)} are "
           f"waiting to be settled; the rest are in neither place")


def test_confirming_a_block_puts_that_exact_time_in_the_calendar():
    if not (needs("fitz") and needs("cv2")):
        print("SKIP test_confirming_a_block_puts_that_exact_time_in_the_calendar")
        return
    session, _ = _held_board(auto_confirm=False)
    row = next(p for p in session.imports()["pending_review"])
    cid = row["coordinator_id"]
    span = (dt.datetime.fromisoformat(row["start"]),
            dt.datetime.fromisoformat(row["end"]))

    if span in _intervals(session, cid):
        # The demo seeds random busy time, so a coincidence is possible. Pick
        # a row whose interval is not already there, or the test proves
        # nothing about what confirming did.
        row = next((p for p in session.imports()["pending_review"]
                    if (dt.datetime.fromisoformat(p["start"]),
                        dt.datetime.fromisoformat(p["end"]))
                    not in _intervals(session, p["coordinator_id"])), None)
        expect(row is not None,
               "every pending block already existed in a calendar, so this "
               "test cannot tell confirming apart from doing nothing")
        cid = row["coordinator_id"]
        span = (dt.datetime.fromisoformat(row["start"]),
                dt.datetime.fromisoformat(row["end"]))

    session.review_import_block(row["block_id"], True, "tester")
    expect(span in _intervals(session, cid),
           f"{row['coordinator']} was confirmed busy {span[0]}-{span[1]} and "
           f"the board still has them free")


def test_rejecting_a_block_leaves_the_calendar_alone():
    """The failure that would make the gate worse than not having one."""
    if not (needs("fitz") and needs("cv2")):
        print("SKIP test_rejecting_a_block_leaves_the_calendar_alone")
        return
    session, _ = _held_board(auto_confirm=False)
    row = next((p for p in session.imports()["pending_review"]
                if (dt.datetime.fromisoformat(p["start"]),
                    dt.datetime.fromisoformat(p["end"]))
                not in _intervals(session, p["coordinator_id"])), None)
    expect(row is not None, "no pending block was absent from the calendar, "
                            "so a rejection cannot be told from a no-op")
    cid = row["coordinator_id"]
    span = (dt.datetime.fromisoformat(row["start"]),
            dt.datetime.fromisoformat(row["end"]))

    session.review_import_block(row["block_id"], False, "tester")
    expect(span not in _intervals(session, cid),
           f"a block that was rejected was applied anyway: {row['coordinator']} "
           f"{span[0]}-{span[1]}")


def test_the_queue_names_people_rather_than_ids():
    """Including somebody the lab is not scheduling.

    `state.coordinators` holds only active people, so their id fell through
    raw. A row reading "C06" tells a scheduler nothing and looks like corrupt
    data; the roster still knows the name, and "not scheduled" is exactly why
    the block is worth rejecting.
    """
    if not (needs("fitz") and needs("cv2")):
        print("SKIP test_the_queue_names_people_rather_than_ids")
        return
    session, _ = _held_board(auto_confirm=False)
    for row in session.imports()["pending_review"]:
        expect(row["coordinator"] != row["coordinator_id"],
               f"the review queue shows the raw id {row['coordinator_id']} "
               f"instead of a name")


def test_pending_review_keeps_its_two_shapes_straight():
    """One key, two shapes, and the page reads both.

    `pending_review` is a **count** on an upload response and on
    `last_import`, and a **list of blocks** on `imports()`. Every caller is
    right about which it is getting today, and nothing says so anywhere, so
    unifying them would silently turn a truthy count into a truthy empty list
    -- the "To confirm" tab would show a badge over nothing, or the upload
    toast would claim blocks are in effect while they sit in a queue.

    If a later change does unify them, this fails and names the callers to fix:
    the tab count in calendars.js reads `.length`, and the upload toast and
    the import card read it as a number.
    """
    if not (needs("fitz") and needs("cv2")):
        print("SKIP test_pending_review_keeps_its_two_shapes_straight")
        return
    session, upload = _held_board(auto_confirm=False)

    expect(isinstance(upload.get("pending_review"), int),
           f"an upload response should carry a count, got "
           f"{type(upload.get('pending_review')).__name__}")
    expect(isinstance(session.imports().get("pending_review"), list),
           f"imports() should carry the blocks themselves, got "
           f"{type(session.imports().get('pending_review')).__name__}")
    expect(upload["pending_review"] == len(session.imports()["pending_review"]),
           "the count and the list disagree about how much is waiting")


def test_with_the_gate_off_a_screenshot_counts_at_once():
    """The other setting still works, so the flag is a real choice."""
    if not (needs("fitz") and needs("cv2")):
        print("SKIP test_with_the_gate_off_a_screenshot_counts_at_once")
        return
    session, upload = _held_board(auto_confirm=True)
    expect(upload["applied_blocks"] > 0,
           "with the gate off nothing was applied, so the setting does nothing")
    expect(not session.imports()["pending_review"],
           "with the gate off blocks were still queued for review")


def test_re_applying_an_import_replaces_it_rather_than_stacking_another_copy():
    """Every upload re-applies all confirmed blocks. They must not accumulate.

    The code meant to drop previously applied import blocks and keep the rest,
    filtering on a `source` marker of "pdf_import". Nothing ever set that
    marker -- BusyBlock had no such field -- so the filter matched nothing and
    each re-apply appended a second copy of the same busy time. Three uploads
    in a row left one event on the calendar three times.

    Free/busy is unaffected, which is why it went unnoticed. The burden
    criterion is not: it reads committed hours off these blocks, so a
    duplicated calendar makes somebody look progressively busier than they
    are, and the ranking moves.
    """
    if not (needs("fitz") and needs("cv2")):
        print("SKIP test_re_applying_an_import_replaces_it_rather_than_stacking")
        return
    import collections

    session, _ = _held_board(auto_confirm=True)
    before = {cid: len(cal.blocks)
              for cid, cal in session.state.calendars.items()}

    # Apply the very same confirmed blocks again, which is what the next
    # upload does.
    session._apply_confirmed_blocks()
    session._apply_confirmed_blocks()

    for cid, cal in session.state.calendars.items():
        counts = collections.Counter((b.start, b.end) for b in cal.blocks
                                     if getattr(b, "source", "") == "pdf_import")
        repeated = {k: n for k, n in counts.items() if n > 1}
        expect(not repeated,
               f"{cid} has the same imported block more than once after "
               f"re-applying: {list(repeated)[:2]}")
        expect(len(cal.blocks) == before.get(cid, 0),
               f"{cid} went from {before.get(cid)} blocks to {len(cal.blocks)} "
               f"without anything new being read")


def test_the_read_table_counts_this_print_and_not_every_print():
    """The blocks column sits beside "calendar as printed".

    It was counting every block the board had ever confirmed for that person,
    so a print of twelve events reported forty-six once a few uploads had
    accumulated. The check is an invariant rather than a fixed number: the
    per-person counts have to add up to what this import said it read.

    Run against a PDF, because that is what the table describes. A screenshot
    carries no printed legend, so it produces no rows at all -- see
    `test_a_screenshot_has_no_legend_to_build_a_table_from` below.
    """
    if not needs("fitz"):
        print("SKIP test_the_read_table_counts_this_print_and_not_every_print")
        return
    import tempfile as _tf
    import make_work_week_pdf as fx
    from backend.session import LabSession

    tmp = _tf.mkdtemp(prefix="read-table-")
    os.environ["ESD_COLOR_MAP_PATH"] = os.path.join(tmp, "colours.json")
    pdf = os.path.join(tmp, "week.pdf")
    fx.build(pdf, first_day=FIRST_DAY, month=MONTH, year=YEAR)
    session = LabSession(db_path=os.path.join(tmp, "board.db"))

    for _ in range(2):          # twice: a table counting history would double
        with open(pdf, "rb") as fh:
            upload = session.upload_calendar_pdf("week.pdf", fh.read())

    table = session.read_table()
    counted = sum(r["blocks"] for r in table["rows"]
                  if isinstance(r["blocks"], int))
    expect(counted == upload["block_count"],
           f"the table counts {counted} blocks for a print that read "
           f"{upload['block_count']}")


def test_a_screenshot_names_the_people_its_colours_were_matched_to():
    """A screenshot prints no header, so every colour reaches the fallback.

    That fallback said "unnamed blue calendar -- Not recognised" and asked
    somebody to identify it, while the confirmed colour map had already put
    those very blocks on the right people. The board was asking a question it
    had the answer to, about time it had already committed.

    Colour is weaker evidence than a printed name, which is why the row says
    where the match came from. But it is evidence, and pretending otherwise
    sent a scheduler to map a calendar that was already mapped.
    """
    if not (needs("fitz") and needs("cv2")):
        print("SKIP test_a_screenshot_names_the_people_its_colours_were_matched_to")
        return
    session, upload = _held_board(auto_confirm=True)
    expect(upload["block_count"] > 0,
           "the screenshot produced no blocks, so this proves nothing")

    table = session.read_table()
    named = [r for r in table["rows"] if r["coordinator_id"]]
    expect(named,
           f"the screenshot attributed {upload['block_count']} blocks to "
           f"people, and the table names none of them: "
           f"{[r['label'] for r in table['rows']]}")
    counted = sum(r["blocks"] for r in named if isinstance(r["blocks"], int))
    expect(counted == upload["block_count"],
           f"the table accounts for {counted} of {upload['block_count']} "
           f"blocks the screenshot produced")
    for row in named:
        expect("colour" in row["meaning"],
               f"row {row['label']!r} does not say the match came from a "
               f"colour rather than a printed name")


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
