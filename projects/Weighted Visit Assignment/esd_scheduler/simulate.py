"""A narrated run of the whole scheduling workflow, on invented data.

    make simulate

Every number printed here comes from the real engine. Nothing is described
that was not actually computed: the script builds a board, enters families,
uploads a calendar and asks for a decision, then prints what came back and the
reason attached to it. If a rule changes, this output changes with it, which is
why it is worth reading rather than a diagram that has to be kept in step by
hand.

The families are invented and the dates are fixed, so two runs print the same
thing. The people are the real roster, because who can run which visit is the
part worth showing.

The walk follows the order a decision is actually made:

    1  an empty board          nobody is free until a calendar says so
    2  the protocol clock      when each family is due, and from which date
    3  reading a calendar      unknown becomes free or busy
    4  Layer 1, hard gates     who cannot go, and why
    5  Layer 2, the score      four criteria, weighted
    6  Layer 3, the ranking    is the winner clear enough to recommend
    7  staffing                one clinician, one tech, and a vehicle
    8  the physical limits     kits, closed days, out of hours
    9  committing              what lands in the audit log
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timedelta

WIDTH = 78


# ---------------------------------------------------------------- presentation

def step(number: int, title: str) -> None:
    print()
    print("=" * WIDTH)
    print(f"  STEP {number}.  {title}")
    print("=" * WIDTH)


def say(text: str = "") -> None:
    """A line of explanation, wrapped."""
    import textwrap
    if not text:
        print()
        return
    for line in textwrap.wrap(text, WIDTH - 2):
        print(f" {line}")


def show(label: str, value: str = "") -> None:
    print(f"   {label:<34} {value}")


def quote(text: str) -> None:
    """Something the manual says, so the rule can be checked against it."""
    import textwrap
    print()
    for line in textwrap.wrap(text, WIDTH - 8):
        print(f"     | {line}")
    print()


# ------------------------------------------------------------------ the script

def label_for(entry) -> str:
    """How to write a person's name once, when they have two.

    The Outlook export prints one name and the manual uses another for the
    same human. Either alone leaves a reader matching them up by guesswork,
    so both appear together wherever the walkthrough names somebody.
    """
    if entry.manual_name and entry.manual_name.lower() not in entry.name.lower():
        return f"{entry.name} ({entry.manual_name})"
    return entry.name


def run() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, here)
    sys.path.insert(0, os.path.join(here, "tests", "fixtures"))
    os.environ["ESD_MODE"] = "live"

    from backend.session import LabSession
    from esd_scheduler.resources import LabResources
    from esd_scheduler.roster import Roster
    from esd_scheduler.schedule import ProtocolSchedule, anchor_for

    tmp = tempfile.mkdtemp(prefix="esd-sim-")
    session = LabSession(db_path=os.path.join(tmp, "simulation.db"))
    resources = LabResources.load()
    roster = Roster.load()

    print()
    print("  ESD Visitboard: how one visit gets staffed")
    print("  " + "-" * (WIDTH - 4))
    say("Invented families, the real roster, and the real engine. Every "
        "figure below was computed, not written into this script.")

    # -- 1 ------------------------------------------------------------------
    step(1, "An empty board")
    say("A live board starts with the people and nothing else. No families, "
        "no visits, and no calendars.")
    print()
    show("coordinators on the roster", str(len(session.state.coordinators)))
    show("families", str(len(session.state.families)))
    show("visits", str(len(session.visits)))
    show("where busy time came from", session.health()["calendar_source"])
    say()
    say("That last line matters. It says 'none', not 'nobody is busy'. A "
        "coordinator whose calendar has not been read is unknown, and unknown "
        "is never treated as free. Watch what that does in step 4.")

    # -- 2 ------------------------------------------------------------------
    step(2, "The protocol clock: when is each family due?")
    say("Two families, born on the same day. One arrived a month early.")

    families = [
        dict(family_id="5001", participant_status="PT",
             birth_date="2026-06-01", due_date="2026-07-01",
             label="born 1 Jun, one month premature, due 1 Jul"),
        dict(family_id="5002", participant_status="TD",
             birth_date="2026-06-01", due_date=None,
             label="born 1 Jun, term"),
    ]
    for fam in families:
        show(fam["family_id"], fam["label"])

    quote("For PT participants, the ideal date for 1m-12m visits will be "
          "based on their expected due date to adjust for their prematurity "
          "... This will change at the 36m visit, where all participants "
          "(regardless of status) will be scheduled on their 3rd birthday.")

    say("So the date a checkpoint counts from depends on the checkpoint. One "
        "stored anchor cannot say both of those things.")
    print()
    schedule = ProtocolSchedule.load()
    checkpoints = {c.name: c for c in schedule.for_protocol("NANO")}

    class _Fam:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    print(f"   {'checkpoint':<12}{'5001 preterm':<16}{'5002 term':<16}")
    print(f"   {'-' * 12}{'-' * 16}{'-' * 16}")
    for name in ("1m", "3m", "12m", "36m"):
        row = []
        for fam in families:
            obj = _Fam(
                family_id=fam["family_id"],
                participant_status=fam["participant_status"],
                birth_date=date.fromisoformat(fam["birth_date"]),
                due_date=(date.fromisoformat(fam["due_date"])
                          if fam["due_date"] else None),
                anchor_date=None,
            )
            row.append(str(checkpoints[name].target(anchor_for(obj, name))))
        print(f"   {name:<12}{row[0]:<16}{row[1]:<16}")
    say()
    say("The preterm baby's early visits sit a month later. By 36m both land "
        "on the same day, which is their third birthday.")

    # -- 3 ------------------------------------------------------------------
    step(3, "Reading a calendar")
    say("Availability arrives one way: somebody prints an Outlook calendar "
        "and drops it on the board. Here is that print being read.")

    from make_work_week_pdf import build as build_week
    pdf_path = build_week(os.path.join(tmp, "work-week.pdf"))
    with open(pdf_path, "rb") as fh:
        result = session.upload_calendar_pdf("work-week.pdf", fh.read())

    print()
    show("view", result["view_type"])
    show("evidence tier", f"{result['tier']} ({result.get('tier_rule', '')})")
    show("busy blocks read", str(result["block_count"]))
    show("applied to the board", str(result.get("applied_blocks")))
    show("dates covered", str(result.get("date_range")))
    show("where busy time came from", session.health()["calendar_source"])
    say()
    say("A PDF is read exactly, so its blocks count immediately. A "
        "screenshot is measured in pixels instead, files at a lower tier and "
        "waits for someone to confirm it.")

    # -- 4 ------------------------------------------------------------------
    step(4, "Layer 1: who cannot go, and why")
    say("Now a real visit. Family 5001 is due their 9m visit.")

    session.add_visit({
        "family_id": "5001", "protocol": "NANO", "checkpoint": "9m",
        "window_start": "2026-08-20T09:00:00",
        "window_end": "2026-08-23T17:00:00",
        "participant_status": "PT",
        "birth_date": "2026-06-01", "due_date": "2026-07-01",
        "completed_through": "6m",
    })
    visit_id = session.order[0]
    detail = session.candidates(visit_id)

    say()
    say("Layer 1 is boolean. Every gate is a yes or a no, they run in order, "
        "and the first no is the reason. A good score cannot argue with one.")
    print()
    for row in detail.get("excluded", []):
        show(row.get("name", "?"), (row.get("reason") or "")[:38])
    say()
    show("left in the running", str(len(detail.get("candidates", []))))

    # -- 5 ------------------------------------------------------------------
    step(5, "Layer 2: scoring whoever is left")
    cfg = session.cfg
    say("Four criteria, each scored 0 to 1, then weighted and added. The "
        "weights live in config/engine.json and sum to 1.")
    print()
    meanings = {
        "phi": "continuity: the family has seen them before",
        "omega": "family preference: asked for, or asked to avoid",
        "psi": "burden relief: spreading the load",
        "p": "protocol continuity: same rater as last checkpoint",
    }
    for key, weight in cfg.weights.as_dict().items():
        show(f"{key:<6} {weight:>4.2f}", meanings[key])

    pairs = detail.get("pairs") or []
    if pairs:
        top = pairs[0]
        say()
        say(f"For the top pairing, {top['clinician']} with {top['tech']}:")
        print()
        total = 0.0
        for key, weight in cfg.weights.as_dict().items():
            scored = (top.get("components") or {}).get(key, 0.0)
            part = scored * weight
            total += part
            show(f"{key:<6} {scored:>4.2f}  x  {weight:.2f}", f"=  {part:.4f}")
        show("", "-" * 10)
        show("total", f"=  {total:.4f}    (the board says {top['score']})")

    # -- 6 ------------------------------------------------------------------
    step(6, "Layer 3: is the winner clear enough?")
    band = detail.get("review_band")
    say(f"Two scores within {band} of each other are a tie, not a winner. "
        "The board says so rather than picking on a rounding difference.")
    print()
    for pair in pairs[:3]:
        show(f"{pair['clinician']} + {pair['tech']}", f"{pair['score']}")
    say()
    for notice in detail.get("notices", []):
        say(f"[{notice.get('tone', 'info')}] {notice.get('message', '')}")

    # -- 7 ------------------------------------------------------------------
    step(7, "Staffing: one clinician, one tech, and a vehicle")
    quote("2 staff members are needed per visit: 1 clinician AND 1 tech. "
          "Clinician must be able to reliably/independently admin all the "
          "assessments needed for that visit age.")
    say("Two separate questions, and passing one is not passing the other.")
    print()
    # One label per person, everywhere. Somebody printed on the calendar under
    # one name and written in the manual under another is still one person,
    # and showing them as "Makenzie" here and "Morgan Soto" three lines later
    # is how a reader concludes the lab has two staff who do not exist.
    for entry in roster.active:
        can_run = roster.can_be_clinician_for(entry, "9m")
        solo = (f"{entry.solo_from}-{entry.solo_to}"
                if entry.solo_from else "no solo range")
        show(label_for(entry),
             f"{'clinician' if can_run else 'tech only':<12} {solo}")
    say()
    # Worked out from the roster rather than written down, so the point stays
    # true when somebody joins or leaves instead of describing a person the
    # board no longer schedules.
    blocked = [e for e in roster.active
               if not roster.can_be_clinician_for(e, "9m")]
    names = [label_for(e) for e in blocked]
    named = (" and ".join([", ".join(names[:-1]), names[-1]])
             if len(names) > 1 else (names[0] if names else "nobody here"))
    say(f"Both questions are visible here. {named} "
        f"cannot be the clinician on a 9m: the manual prints no solo range "
        f"beside their name, whatever they are signed off on. Somebody can "
        f"hold every assessment a visit needs and still not be able to run "
        f"it, so the board asks about the assessments and about the range, "
        f"and a pass on one is not a pass on the other.")
    if pairs:
        say()
        top = pairs[0]
        show("chosen pair", f"{top['clinician']} + {top['tech']}")
        show("slot", str(top.get("slot")))
        show("vehicle", f"{top.get('vehicle')} - {top.get('vehicle_reason')}")

    # -- 8 ------------------------------------------------------------------
    step(8, "The limits that stop a visit whatever it scores")
    show("NANO tech kits", str(resources.kit_ceiling("NANO")))
    say("   two visits at once, and no third whoever is free")
    print()
    for when, label in ((date(2026, 8, 20), "Thursday"),
                        (date(2026, 8, 21), "Friday")):
        show(f"{label} {when}", resources.closed_on(when) or "open")
    print()
    show("holidays on file", str(len(resources.holidays)))
    if not resources.holidays_known:
        say("   none yet, and that is recorded as 'not told' rather than "
            "'there are none'. The manual gives the rule without the dates.")
    print()
    for lo, hi, label in (
        (datetime(2026, 8, 20, 10), datetime(2026, 8, 20, 12), "Thu 10:00-12:00"),
        (datetime(2026, 8, 20, 16), datetime(2026, 8, 20, 18), "Thu 16:00-18:00"),
        (datetime(2026, 8, 22, 10), datetime(2026, 8, 22, 12), "Sat 10:00-12:00"),
    ):
        show(label, "out of hours" if resources.is_out_of_hours(lo, hi) else "normal")
    say()
    say("An out-of-hours visit puts that person to the back of a rotation: "
        "nobody works a second one until everyone has worked one.")

    # -- 9 ------------------------------------------------------------------
    step(9, "Committing the decision")
    if pairs:
        top = pairs[0]
        session.assign(visit_id, top["clinician_id"], tech_id=top["tech_id"])
        say(f"Assigned {top['clinician']} and {top['tech']} to visit "
            f"{visit_id}, family 5001.")
        print()
        summary = session.visit_summary(visit_id)
        show("status", str(summary.get("status")))
        rows = session.store.query(
            "SELECT weight_vector_id, config_fingerprint FROM scoring_run "
            "ORDER BY scored_at DESC LIMIT 1")
        if rows:
            show("scored under weights", rows[0][0])
            show("config fingerprint", rows[0][1])
        say()
        say("The weight set is recorded with the decision, and its id carries "
            "the config fingerprint. Change a weight later and this run still "
            "points at the numbers that produced it.")

    # -- and the one that needs nobody --------------------------------------
    step(10, "The visit that needs nobody")
    session.add_visit({
        "family_id": "5002", "protocol": "NANO", "checkpoint": "24m",
        "window_start": "2026-08-20T09:00:00",
        "window_end": "2026-08-23T17:00:00",
        "participant_status": "TD", "birth_date": "2026-06-01",
    })
    remote_id = [v for v in session.order if v != visit_id][0]
    remote = session.candidates(remote_id)
    quote("For the 24m timepoint of NANO visits, we do not see participants "
          "for an in-person visit.")
    show("pairings offered", str(len(remote.get("pairs") or [])))
    for notice in remote.get("notices", []):
        say(notice.get("message", ""))
    say()
    say("No staff, no vehicle, and no tech kit spent. Before this rule was "
        "read out of the manual the board offered six workable pairings for "
        "it.")

    print()
    print("=" * WIDTH)
    say("That is the whole path: a calendar in, a decision out, and a reason "
        "attached to every step of it.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
