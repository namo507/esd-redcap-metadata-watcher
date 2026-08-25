"""Tests for the tuning controls.

The controls exist so the numbers get questioned. That only works if turning
one is safe: it must take effect, it must not cost the scheduler the week's
work, and it must refuse anything the board did not itself offer.

Every test runs against copies of the config files in a temporary directory.
A settings test that wrote to config/ would edit the lab's real numbers as a
side effect of running the suite.

Run:  python3 tests/test_settings.py
"""

from __future__ import annotations

import glob
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="esd-settings-")
for _name in ("engine.json", "roster.json", "lab-resources.json"):
    shutil.copy(os.path.join(ROOT, "config", _name), os.path.join(_TMP, _name))
os.environ["ESD_ENGINE_PATH"] = os.path.join(_TMP, "engine.json")
os.environ["ESD_ROSTER_PATH"] = os.path.join(_TMP, "roster.json")
os.environ["ESD_RESOURCES_PATH"] = os.path.join(_TMP, "lab-resources.json")
os.environ["ESD_MODE"] = "demo"
os.environ.setdefault("ESD_DB", os.path.join(_TMP, "settings-test.db"))

from backend import settings                                     # noqa: E402
from backend.session import LabSession                           # noqa: E402


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def fresh():
    return LabSession(db_path=os.path.join(_TMP, "settings-test.db"))


def value_of(session, key):
    for row in settings.catalogue(session)["knobs"]:
        if row["key"] == key:
            return row["value"]
    raise AssertionError(f"no knob called {key}")


# --- the catalogue ----------------------------------------------------------


def test_every_knob_offers_the_value_it_is_actually_set_to():
    """A control showing a different value from the one in force is a lie.

    A config edited by hand can hold a number nobody put on the list. The
    catalogue has to add it rather than drop it, or the page displays a value
    the engine is not using.
    """
    session = fresh()
    for row in settings.catalogue(session)["knobs"]:
        allowed = [o["value"] for o in row["options"]]
        expect(any(settings._same(v, row["value"]) for v in allowed),
               f"{row['key']} is set to {row['value']!r} but that is not one "
               f"of the values it offers: {allowed}")


def test_every_knob_belongs_to_a_group_the_page_can_draw():
    session = fresh()
    cat = settings.catalogue(session)
    groups = {g["id"] for g in cat["groups"]}
    for row in cat["knobs"]:
        expect(row["group"] in groups,
               f"{row['key']} is in group {row['group']!r}, which the page "
               f"never renders, so the control would be invisible")


def test_capacity_is_offered_for_everyone_being_scheduled():
    """Generated from the roster, so a new coordinator needs no code change."""
    session = fresh()
    keys = {r["key"] for r in settings.catalogue(session)["knobs"]}
    for entry in session.roster_config.active:
        expect(f"roster.{entry.id}.capacity_hours_week" in keys,
               f"{entry.name} is being scheduled but has no capacity control")


# --- refusing what was never offered ----------------------------------------


def test_a_value_the_board_never_offered_is_refused():
    session = fresh()
    for key, bad in (("weights.phi", 0.4321),
                     ("lab.tech_kits.NANO", 99),
                     ("engine.top_k", -1)):
        try:
            settings.apply(session, key, bad)
        except settings.SettingError:
            continue
        raise AssertionError(f"{key} accepted {bad!r}, which it never offered")


def test_an_unknown_setting_is_refused():
    session = fresh()
    try:
        settings.apply(session, "engine.there_is_no_such_thing", 1)
    except settings.SettingError:
        return
    raise AssertionError("a setting that does not exist was accepted")


# --- what applying does -----------------------------------------------------


def test_setting_one_weight_keeps_the_four_summing_to_one():
    """The engine refuses a set that does not sum to 1, and rightly.

    A score built from weights summing to 1.2 is not on the same scale as any
    decision already recorded, so the other three rescale in proportion.
    """
    session = fresh()
    for target in (0.6, 0.0, 1.0, 0.25):
        changed = settings.apply(session, "weights.phi", target)
        total = sum(changed.values())
        expect(abs(total - 1.0) < 1e-6,
               f"after setting phi={target} the weights sum to {total}")
        expect(abs(changed["weights.phi"] - target) < 1e-9,
               f"asked for phi={target}, got {changed['weights.phi']}")
        expect(abs(session.cfg.weights.phi - target) < 1e-9,
               "the session is still scoring under the old weights")


def test_a_weight_change_moves_the_scores():
    """A setting that saves and changes nothing is the same as a broken one."""
    session = fresh()
    visit = session.order[0]

    def top_scores():
        out = session.candidates(visit)
        rows = out.get("candidates") or out.get("pairings") or []
        return [round(r.get("score", 0), 4) for r in rows[:5]]

    settings.apply(session, "weights.psi", 1.0)
    burden = top_scores()
    settings.apply(session, "weights.phi", 1.0)
    continuity = top_scores()
    expect(burden != continuity,
           f"the ranking scored the same under opposite weights: {burden}")


def test_a_capacity_change_reaches_the_coordinator_in_play():
    """Capacity is copied onto the Coordinator at build time.

    Re-reading the roster alone would leave the old number in force everywhere
    it is actually used, which is the difference between the file changing and
    the board changing.
    """
    session = fresh()
    settings.apply(session, "roster.C05.capacity_hours_week", 10)
    expect(session.roster_config.by_id()["C05"].capacity_hours_week == 10,
           "the roster still holds the old capacity")
    expect(session.state.coordinators["C05"].capacity_hours_week == 10,
           "the coordinator the engine actually asks still holds the old "
           "capacity")


def test_a_lab_limit_change_reaches_the_gate():
    """The limits are cached per process, so the cache has to be dropped.

    The cache is warmed deliberately before the change. Without that this
    test passed whether or not the cache was ever cleared -- the first read
    happened after the write, so it loaded the new file by accident and the
    assertion could not fail. A test that cannot fail is not a test, and this
    one was guarding the exact bug where an edit lands in the file and has no
    effect until the process restarts.
    """
    from esd_scheduler import constraints
    session = fresh()
    warm = constraints._resources().kit_ceiling("NANO")
    expect(warm is not None, "the fixture has no NANO tech-kit ceiling to test")

    settings.apply(session, "lab.tech_kits.NANO", 4)
    expect(constraints._resources().kit_ceiling("NANO") == 4,
           "the gate is still enforcing the old tech-kit ceiling, so the "
           "change landed in the file and nowhere else")
    settings.apply(session, "lab.tech_kits.NANO", 2)


def test_changing_a_setting_does_not_cost_the_week_s_work():
    """The whole point of not calling reset().

    If nudging a weight threw away the uploaded calendar and the assignments,
    nobody would nudge a weight, and the controls would be worse than useless
    -- they would be a trap.
    """
    session = fresh()
    prints = sorted(glob.glob(os.path.join(ROOT, "data", "uploads", "*.pdf")))
    if not prints:
        print("SKIP test_changing_a_setting_does_not_cost_the_week_s_work "
              "(no upload on file to test with)")
        return
    with open(prints[-1], "rb") as fh:
        session.upload_calendar_pdf(os.path.basename(prints[-1]), fh.read())
    before = session.last_import
    expect(before, "the fixture upload did not register")
    activity = len(session.activity)

    settings.apply(session, "weights.phi", 0.35)

    expect(session.last_import is before,
           "changing a setting threw away the uploaded calendar")
    expect(len(session.activity) >= activity,
           "changing a setting threw away the activity log")


def test_the_lab_file_keeps_its_comments_through_an_edit():
    """lab-resources.json explains itself. Rewriting it must not strip that."""
    import json
    session = fresh()
    settings.apply(session, "lab.working_hours.grace_minutes", 15)
    with open(os.environ["ESD_RESOURCES_PATH"], encoding="utf-8") as fh:
        raw = json.load(fh)
    expect("_comment" in raw,
           "the explanation at the top of lab-resources.json was thrown away")
    expect("_comment" in (raw.get("screenshot_uploads") or {}),
           "the note explaining what auto-confirm costs was thrown away")
    expect(raw["working_hours"]["grace_minutes"] == 15, "the edit did not land")


def test_the_frontend_names_no_setting():
    """Adding a knob must be a backend change and nothing else."""
    with open(os.path.join(ROOT, "frontend", "js", "settings.js"),
              encoding="utf-8") as fh:
        js = fh.read()
    for key in ("epsilon_review_band", "tech_kits", "capacity_hours_week",
                "weights.phi", "grace_minutes"):
        expect(key not in js,
               f"{key} is written into settings.js; the catalogue carries the "
               f"names so the page does not have to")


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
    shutil.rmtree(_TMP, ignore_errors=True)
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
