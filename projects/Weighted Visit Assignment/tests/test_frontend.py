"""Tests for the parts of the page that no Python test was watching.

These are cheap text checks over the shipped frontend, not a browser. They
exist because two bugs got as far as a running board without any test going
red: the router defaulted to a section that had been deleted, so a visit with
no URL hash rendered a page with every section hidden, and two unrelated
components shared a class name, so whichever rule came last in the stylesheet
silently resized the other.

Run:  python3 tests/test_frontend.py
"""

from __future__ import annotations

import collections
import os
import re


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, "frontend")


def read(*parts):
    with open(os.path.join(FRONTEND, *parts), encoding="utf-8") as fh:
        return fh.read()


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def section_names():
    core = read("js", "core.js")
    m = re.search(r"const SECTION_NAMES = \[(.*?)\]", core, re.S)
    expect(m, "SECTION_NAMES is not declared in core.js")
    return re.findall(r'"([^"]+)"', m.group(1))


def test_every_section_has_markup_and_hero_copy():
    """A named section the page cannot draw is a blank screen, not an error."""
    core = read("js", "core.js")
    html = read("index.html")
    names = section_names()
    expect(names, "no sections are declared")
    for name in names:
        expect(f'id="sec-{name}"' in html,
               f"SECTION_NAMES has {name!r} but index.html has no sec-{name}")
        expect(re.search(rf"^\s*{name}:\s*\[", core, re.M),
               f"SECTION_NAMES has {name!r} but SECTIONS has no hero copy for it")
        expect(f'data-section="{name}"' in html,
               f"SECTION_NAMES has {name!r} but no nav button points at it")


def test_the_route_falls_back_to_a_section_that_exists():
    """Arriving with no hash is the common case, so the default must resolve.

    Defaulting to a name that is not in SECTIONS throws while destructuring
    the hero copy, which happens before any section is shown. The whole board
    renders empty and the console stays silent because boot catches it.
    """
    core = read("js", "core.js")
    m = re.search(r"section: SECTION_NAMES\.includes\(section\) \? section : (.+?),",
                  core)
    expect(m, "parseRoute no longer has a recognisable fallback")
    fallback = m.group(1).strip()
    names = section_names()
    literal = fallback.strip('"')
    expect(fallback == "SECTION_NAMES[0]" or literal in names,
           f"parseRoute falls back to {fallback}, which is not one of {names}")


def test_the_initial_state_starts_on_a_real_section():
    core = read("js", "core.js")
    m = re.search(r"section:\s*\"([^\"]+)\"", core)
    expect(m, "the initial state no longer names a section")
    expect(m.group(1) in section_names(),
           f"state starts on section {m.group(1)!r}, which is not a real section")


def test_no_class_is_defined_twice_on_its_own():
    """One class, one owner.

    A class given a second standalone rule is almost always two components
    that happened to pick the same short name. The properties merge rather
    than conflict, so nothing looks broken in the stylesheet while both
    components render wrong. Grouped selectors are left alone: sharing one
    rule between several classes is deliberate, and so is an override inside
    a media query.
    """
    css = strip_comments(read("styles.css"))
    counts = collections.Counter()
    depth, cut = 0, 0
    for m in re.finditer(r"[{}]", css):
        if m.group(0) == "{":
            if depth == 0:
                selector = css[cut:m.start()].strip()
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                if re.fullmatch(r"\.[A-Za-z0-9_-]+", selector):
                    counts[selector] += 1
                cut = m.start() + 1
    repeated = {k: v for k, v in counts.items() if v > 1}
    expect(not repeated,
           f"these classes are each defined more than once on their own, so one "
           f"component is silently restyling another: {repeated}")


def test_confirming_a_mapping_redraws_every_section():
    """A mapping change moves whose time is whose, so nothing may lag.

    Correcting who a calendar belongs to changes availability, which changes
    who is eligible, which changes the ranking. If one section redraws and
    another does not, the board shows two answers at once. redrawEverything
    is the single place that knows what "everything" is, and this checks it
    still names every section the page draws.
    """
    js = read("js", "calendars.js")
    expect("function redrawEverything" in js,
           "there is no single place that redraws after a mapping change")
    body = js[js.index("function redrawEverything"):]
    body = body[:body.index("\n}")]
    for drawer in ("drawKpis", "drawQueue", "drawTeam", "drawSync",
                   "drawReadTable", "selectVisit"):
        expect(drawer in body,
               f"redrawEverything does not call {drawer}, so that part of the "
               f"page would keep showing the previous read")


def test_the_visit_view_opens_rather_than_answering_first():
    """Picking a visit must not print the recommendation before the reasoning.

    The old view rendered the ranked pairs, the individual ranking and the
    exclusions in one pass. That is the thing the tree replaced, so the
    renderer it used has to be gone rather than merely unreferenced -- dead
    code is an invitation to wire it back in.
    """
    js = read("js", "assign.js")
    expect("function pairSection" not in js,
           "the static pair list is still in assign.js; the tree replaced it")
    expect("drawMindmap(d, assigned)" in js,
           "drawDetail no longer renders the decision tree")


def test_the_tree_shows_only_the_level_that_was_opened():
    """A branch renders its leaves only while it is the open one."""
    js = read("js", "mindmap.js")
    expect("${open ? " in js or "open ?" in js,
           "the leaf column is rendered unconditionally, so every branch is "
           "always on screen")
    expect("S.mm.branch === id ? null : id" in js,
           "clicking an open branch does not close it, so the tree can only "
           "ever be opened")


def test_the_tree_folds_up_only_when_the_visit_changes():
    """A silent redraw must not close what the scheduler had open.

    selectVisit runs on the auto-refresh and after an assignment. Resetting
    unconditionally would shut the tree under them every few seconds.
    """
    js = read("js", "assign.js")
    expect("if (S.selected !== visitId) resetMindmap();" in js,
           "the tree is reset on every selectVisit, including silent ones")


def test_no_reason_a_pair_fails_is_dropped_on_the_way_to_the_tree():
    """pair_problems explains an empty pair list. Losing it loses the why."""
    js = read("js", "mindmap.js")
    expect("pair_problems" in js,
           "the reasons no pair works are not shown anywhere in the tree")


def test_nobody_is_both_able_to_go_and_ruled_out():
    """The two exclusion lists overlap, and listing both duplicated people.

    `excluded` carries reasons somebody cannot take the clinician seat, and
    `clinician_blocked` carries the same kind of no. Rendering both put two
    coordinators under "ruled out" twice while they were also under "who can
    go" as techs -- they can go, they just cannot run it. Anyone still
    eligible for a seat has to be filtered out of the blocked list.
    """
    js = read("js", "mindmap.js")
    expect("const usable = new Set(mmPeople(d).map((p) => p.coordinator_id));" in js,
           "mmBlocked no longer checks whether the person is still usable")
    expect("if (!id || usable.has(id)) return;" in js,
           "somebody eligible for a seat can still reach the ruled-out list")
    expect("mmClinicianLimit" in js,
           "the reason somebody cannot run a visit is no longer shown where "
           "they are listed as able to go")


def test_the_hero_collapses_without_depending_on_a_frame():
    """The collapse must not need requestAnimationFrame to happen.

    rAF is starved in a hidden or zero-sized document, so routing the toggle
    through it made the header's position depend on whether the page happened
    to be visible when it was scrolled. The handler reads scrollY and sets a
    class, and nothing else -- no element measurement, which is the thing that
    would actually make a scroll handler expensive.
    """
    js = read("js", "core.js")
    watcher = js[js.index("function watchScroll()"):]
    watcher = watcher[:watcher.index("function setSection")]
    expect("passive: true" in watcher, "the scroll listener is not passive")
    expect("requestAnimationFrame" not in watcher,
           "the collapse still waits for a frame that may never come")
    expect("getBoundingClientRect" not in watcher,
           "the scroll handler measures an element on every event")
    css = read("styles.css")
    expect("body.is-scrolled" in css, "nothing responds to the scrolled state")
    expect("prefers-reduced-motion" in css,
           "the hero animates with no reduced-motion escape")


def test_the_tuning_controls_are_drawn_wherever_their_section_is_shown():
    """A card wired into one entry point and not the other renders sometimes.

    That is exactly what happened: `drawSettings` was added to the boot
    render, but arriving on the section through the nav or a route went
    through `setSection`, which did not know about it. The card was correct
    and invisible.
    """
    core = read("js", "core.js")
    logic_line = [l for l in core.splitlines()
                  if 'name === "logic"' in l]
    expect(logic_line, "setSection no longer routes the logic section")
    expect("drawSettings" in logic_line[0],
           "setSection shows the logic section without drawing the tuning "
           "controls, so they render only on some ways in")
    expect("drawSettings" in read("js", "calendars.js"),
           "a settings change would not redraw the rest of the board")


def test_one_person_with_two_names_is_labelled_once():
    """The board must not show the same human under two different names.

    The Outlook export prints one name and the manual uses another. If the
    team card says one and the walkthrough says the other, a reader counts
    two members of staff who do not both exist -- which is exactly the state
    this roster was in. Both names appear together, from the API, so no file
    here has to know which person it applies to.
    """
    team = read("js", "team.js")
    expect("r.alias" in team,
           "the team card shows only one of a person's two names")
    cal = read("js", "calendars.js")
    expect("o.alias" in cal,
           "the read-table dropdown shows only one of a person's two names")


def test_the_read_table_offers_the_roster_rather_than_fixed_names():
    """Adding a coordinator must make them selectable with no code change."""
    js = read("js", "calendars.js")
    block = js[js.index("function drawReadTable"):]
    block = block[:block.index("async function applyReadTable")]
    expect("table.options" in block,
           "the dropdown is not built from the roster the API returned")
    for name in ("Sofia", "Maggie", "Makenzie", "Sanjana", "Lauren",
                 "Morgan", "Ramiro"):
        expect(name not in block,
               f"{name} is written into the read table; the options come from "
               f"the roster, so no name belongs in this file")


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
