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
