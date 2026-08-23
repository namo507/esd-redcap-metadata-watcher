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
