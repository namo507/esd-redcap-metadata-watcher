"""Tests that the maps in the READMEs still match the tree they describe.

A map is worth having only while it is true. The failure mode is quiet: add a
module, forget the README, and the next person reads a list that is missing the
thing they were looking for and concludes it does not exist. These checks are
the cheapest way to keep that from happening, and they fail with the name of
whatever was added.

Run:  python3 tests/test_docs.py
"""

from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def all_listed(names, text, where, why):
    missing = sorted(n for n in names if n not in text)
    expect(not missing, f"{where} does not mention {missing}. {why}")


def test_every_engine_module_is_on_the_map():
    modules = {
        f for f in os.listdir(os.path.join(ROOT, "esd_scheduler"))
        if f.endswith(".py") and not f.startswith("__")
    }
    all_listed(modules, read("esd_scheduler", "README.md"),
               "esd_scheduler/README.md",
               "Add it under the layer it belongs to.")


def test_every_test_file_is_on_the_map():
    tests = {
        f for f in os.listdir(os.path.join(ROOT, "tests"))
        if f.startswith("test_") and f.endswith(".py")
    }
    # The map names them without the extension, which reads better in a list.
    stems = {f[:-3] for f in tests}
    all_listed(stems, read("README.md"), "the map in README.md",
               "Add a line saying what it guards.")


def test_every_config_file_is_explained():
    files = {
        f for f in os.listdir(os.path.join(ROOT, "config"))
        if f.endswith(".json")
    }
    all_listed(files, read("config", "README.md"), "config/README.md",
               "Say what it decides and whether it is safe to edit by hand.")


def test_every_endpoint_is_documented():
    """An undocumented route is one nobody outside this file knows exists."""
    import re
    server = read("backend", "server.py")
    routes = set(re.findall(r'@(?:get|post)\("([^"]+)"\)', server))
    expect(routes, "no routes could be read out of backend/server.py")
    all_listed(routes, read("backend", "README.md"), "backend/README.md",
               "Add it to the table for reads, changes or calendars.")


def test_every_backend_module_is_documented():
    modules = {
        f for f in os.listdir(os.path.join(ROOT, "backend"))
        if f.endswith(".py") and not f.startswith("__")
    }
    all_listed(modules, read("backend", "README.md"), "backend/README.md",
               "Add a row saying what it is for.")


def test_every_frontend_file_is_documented():
    """The README described one app.js long after it became six files."""
    root = os.path.join(ROOT, "frontend")
    names = {f for f in os.listdir(root) if f.endswith((".js", ".css", ".html"))}
    names |= {f for f in os.listdir(os.path.join(root, "js")) if f.endswith(".js")}
    all_listed(names, read("frontend", "README.md"), "frontend/README.md",
               "Add it to the file list with one line on what it draws.")


def test_every_test_file_runs_in_make_test():
    """A test nobody runs is not a test."""
    makefile = read("Makefile")
    tests = sorted(
        f for f in os.listdir(os.path.join(ROOT, "tests"))
        if f.startswith("test_") and f.endswith(".py")
    )
    missing = [f for f in tests if f"tests/{f}" not in makefile]
    expect(not missing,
           f"these test files exist but `make test` never runs them: {missing}")


def test_the_scheduled_jobs_are_documented():
    """Every job run.sh accepts should be listed in its own header."""
    script = read("automation", "run.sh")
    body = script.split("set -euo pipefail", 1)
    expect(len(body) == 2, "run.sh no longer has a header to read")
    header, rest = body
    jobs = set()
    for line in rest.splitlines():
        stripped = line.strip()
        if stripped.endswith(")") and "|" not in stripped and stripped[:-1].isalpha():
            jobs.add(stripped[:-1])
    expect(jobs, "no jobs could be read out of run.sh")
    all_listed(jobs, header, "the header of automation/run.sh",
               "Say when it runs and what it does.")


def test_the_simulation_still_runs_and_still_teaches():
    """A walkthrough nobody runs rots into a description of an older engine.

    This runs it and checks the substance is present, not the wording: the
    manual's preterm example, the tie band, the staffing rule and the remote
    checkpoint. Any of those disappearing means the walk stopped covering
    something it claims to cover.
    """
    import contextlib
    import io
    import sys

    sys.path.insert(0, ROOT)
    from esd_scheduler import simulate

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = simulate.run()
    out = buffer.getvalue()
    expect(code == 0, f"the simulation exited {code}")

    for required, why in (
        ("2026-08-01", "the manual's preterm example: 1m lands on 1 August"),
        ("2029-06-01", "36m landing on the third birthday"),
        ("clinician", "the one clinician, one tech rule"),
        ("no solo range", "somebody who is signed off but cannot run a visit"),
        ("out of hours", "the out-of-hours definition"),
        ("van", "the vehicle decision"),
        ("pairings offered", "the remote 24m checkpoint"),
    ):
        expect(required in out, f"the walkthrough no longer shows {why}")

    # The scored total has to be the board's own, not a number typed in here.
    expect("the board says" in out,
           "the score breakdown no longer checks itself against the board")


def test_the_pipeline_check_still_holds_every_seam():
    """The end-to-end run, executed rather than described.

    Unit tests prove each piece alone. This is the one that proves an upload
    changes what the board reports, that the recommendation and the assign
    route agree, and that undoing puts it back. Those seams have parted in
    this codebase before, so the check runs in CI rather than by hand.
    """
    import contextlib
    import io
    import sys

    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "automation"))
    import smoke_pipeline

    smoke_pipeline.FAILURES.clear()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = smoke_pipeline.run()
    out = buffer.getvalue()
    expect(code == 0,
           "the pipeline check reported parted seams:\n"
           + "\n".join(smoke_pipeline.FAILURES)
           + "\n" + out[-800:])
    expect("every seam held" in out, "the run did not reach its own conclusion")


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
