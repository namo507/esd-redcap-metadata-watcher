"""Tests for the unattended half: the inbox sweep and the audit summary.

These run the real CLI commands against a temporary tree, because the failure
mode that matters is operational -- a file that is imported twice, or one that
is filed away after failing to parse.

Run:  python3 tests/test_automation.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures"))

from make_work_week_pdf import build as build_week  # noqa: E402

TMP = tempfile.mkdtemp(prefix="esd-auto-")
INBOX = os.path.join(TMP, "inbox")
PROCESSED = os.path.join(TMP, "processed")
DB = os.path.join(TMP, "audit.db")
os.makedirs(INBOX, exist_ok=True)


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "esd_scheduler", *args],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ,
             "ESD_CALENDAR_ROLES_PATH": os.path.join(TMP, "roles.json"),
             "ESD_COLOR_MAP_PATH": os.path.join(TMP, "colors.json")},
    )


def test_an_empty_inbox_is_not_an_error():
    out = run("import-inbox", "--inbox", INBOX, "--processed", PROCESSED, "--db", DB)
    expect(out.returncode == 0, f"empty inbox failed: {out.stderr}")
    expect("inbox empty" in out.stdout, f"unexpected output: {out.stdout}")


def test_a_dropped_pdf_is_imported_and_filed_away():
    build_week(os.path.join(INBOX, "week.pdf"))
    out = run("import-inbox", "--inbox", INBOX, "--processed", PROCESSED, "--db", DB)
    expect(out.returncode == 0, f"import failed: {out.stderr}")
    expect("work_week" in out.stdout, f"did not report the view: {out.stdout}")
    expect(not [f for f in os.listdir(INBOX) if f.endswith(".pdf")],
           "the PDF was left in the inbox")
    expect([f for f in os.listdir(PROCESSED) if f.endswith(".pdf")],
           "the PDF was not filed into the processed folder")


def test_the_same_file_is_not_imported_twice():
    """Filing the original away is what makes the sweep safe to re-run."""
    before = len(os.listdir(PROCESSED))
    out = run("import-inbox", "--inbox", INBOX, "--processed", PROCESSED, "--db", DB)
    expect(out.returncode == 0, f"second sweep failed: {out.stderr}")
    expect("inbox empty" in out.stdout, "a second sweep found something to do")
    expect(len(os.listdir(PROCESSED)) == before, "the processed folder grew")


def test_an_unreadable_file_is_left_in_place():
    """A file that failed to parse has to stay inspectable, not be filed away."""
    bad = os.path.join(INBOX, "broken.pdf")
    with open(bad, "wb") as fh:
        fh.write(b"%PDF-1.4 this is not really a pdf")
    out = run("import-inbox", "--inbox", INBOX, "--processed", PROCESSED, "--db", DB)
    expect(out.returncode != 0, "a broken file should be reported as a failure")
    expect("FAILED" in out.stdout, f"no failure line: {out.stdout}")
    expect(os.path.exists(bad), "a file that failed to parse was filed away anyway")
    os.remove(bad)


def test_audit_reports_what_was_imported():
    out = run("audit", "--db", DB)
    expect(out.returncode == 0, f"audit failed: {out.stderr}")
    for heading in ("CALENDAR IMPORTS", "EVIDENCE", "DECISIONS"):
        expect(heading in out.stdout, f"audit missing {heading}: {out.stdout}")
    expect("blocks recorded" in out.stdout, "audit did not count evidence")


def test_audit_on_an_empty_store_still_reports():
    empty = os.path.join(TMP, "empty.db")
    out = run("audit", "--db", empty)
    expect(out.returncode == 0, f"audit on an empty store failed: {out.stderr}")
    expect("none recorded" in out.stdout, f"unexpected output: {out.stdout}")


if __name__ == "__main__":
    failures = 0
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                try:
                    fn()
                    print(f"PASS {name}")
                except Exception as exc:  # noqa: BLE001
                    failures += 1
                    print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
