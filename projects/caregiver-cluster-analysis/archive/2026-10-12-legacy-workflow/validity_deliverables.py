"""Publish the respondent-validity review tables after source and privacy checks.

The caller owns classification, approval and analytical decisions. This module
does not query REDCap or change a classification. Artifact Tool authors Excel;
openpyxl only reads the finished file for an exact comparison with the inputs.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Iterable, Mapping

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "Caregiver Outputs" / "respondent_validity_review"
CLASSIFICATIONS = (
    "EXCLUDE", "HOLD FOR MANUAL REVIEW", "ELIGIBLE — ADDITIONAL REVIEW NEEDED",
    "PAY NOW", "UNABLE TO CLASSIFY",
)
CLASSIFICATION_COLORS = dict(zip(CLASSIFICATIONS, (
    "FFC7CE", "FCE4D6", "FFF2CC", "C6EFCE", "D9D9D9",
)))
TABLE_TITLES = {
    1: "Input Data and Field Audit", 2: "Bot Variable and Field Inventory",
    3: "Existing Bot Rule Inventory", 4: "Anonymized Manual Record Review",
    5: "Proposed Respondent-Validity Rules", 6: "Rule Review and Approval Matrix",
    7: "Rule-Level Regression Test Cases", 8: "Rule Trigger and Validation Results",
    9: "Classification Decision Matrix", 10: "Current vs. Revised Classification Summary",
    11: "Classification Transition Matrix", 12: "Rule Impact on Final Classification",
    13: "Record-Level Classification Results", 14: "Prioritized Manual Review Queue",
    15: "Bot Misclassification and Root-Cause Analysis",
    16: "Decisions Required from the Research Team",
}
SHEET_NAMES = {
    1: "01 Input audit", 2: "02 Field inventory", 3: "03 Existing rules",
    4: "04 Manual record review", 5: "05 Proposed rules", 6: "06 Approval matrix",
    7: "07 Regression tests", 8: "08 Rule validation", 9: "09 Decision matrix",
    10: "10 Classification summary", 11: "11 Transitions", 12: "12 Rule impact",
    13: "13 Record results", 14: "14 Review queue", 15: "15 Error analysis",
    16: "16 Research decisions",
}
# Exact requested labels form the export contract. Additional analytical columns
# are retained, including PID/name on tables where the prompt omits them.
REQUIRED_COLUMNS = {
    1: "File or dataset|Study PID|Study name|Number of records|Record-ID field|Completion-status field|Email field|Timing fields|Current-classification field|Data dictionary available|Duplicate records|Missing critical fields|Unmapped codes|Study-specific exclusions|Audit notes",
    2: "Variable name|Variable label|Data type|Response options|Missing-value representation|Used by current bot|Existing rule IDs|Candidate use|Normalization needed|Data-quality issue|Recommendation",
    3: "Existing rule ID|Rule name|Plain-language description|Variables used|Exact implemented condition|Intended condition|Missing-value handling|Current severity|Current action|Number triggered|Implementation matches intention|Potential defect|Recommended correction",
    4: "Study PID|Study name|Anonymized record ID|Current classification|Expected classification|Existing rules triggered|Suspicious or contradictory fields|Anonymized evidence|Missed logic|Proposed action|Reviewer confidence|Manual-review notes",
    5: "Candidate rule ID|Rule name|Business rationale|Variables used|Proposed logical condition|Normalization|Missing-data handling|Proposed severity|Proposed action|Number potentially affected|Example trigger|Example non-trigger|False-positive risk|Requires clarification|Approval status",
    6: "Rule ID|Rule name|Proposed severity|Reviewer decision|Requested modification|Final approved condition|Final severity|Final action|Implementation status|Reviewer comments",
    7: "Test ID|Rule ID|Test type|Study PID|Input condition|Expected result|Actual result|Pass or fail|Failure explanation|Code correction|Retest result",
    8: "Rule ID|Rule name|Records evaluated|Records triggered|Trigger percentage|Confirmed correct triggers|False positives|False negatives|Unable to adjudicate|Estimated precision|Estimated recall|Regression-test status|Recommendation",
    9: "Highest rule severity|Additional rules triggered|Missing critical information|Contradiction unresolved|Manual review required|Final classification|Payment status|Explanation",
    10: "Study PID|Study name|Total analyzed|Current Pay Now|Revised Pay Now|Current excluded|Revised excluded|Current manual review|Revised manual review|Classification changed|Classification unchanged|Net Pay Now change",
    11: "Current classification|" + "|".join(CLASSIFICATIONS) + "|Total",
    12: "Rule ID|Rule name|Trigger count|Unique records affected|Records removed from Pay Now|Records added to manual review|Records excluded|Records unaffected because of higher-priority rule|Percentage of analyzed records affected",
    13: "Study PID|Study name|Record ID|Masked email or email hash|Survey completion status|Survey duration|Current classification|Revised classification|Classification changed|Triggered rule IDs|Highest severity|Plain-language reason|Exclusion indicator|Manual-review indicator|Payment indicator|Missing critical data|Reviewer status|Reviewer notes|Bot version|Processing timestamp",
    14: "Review priority|Study PID|Study name|Record ID|Current classification|Proposed classification|Triggered rules|Number of contradictions|Reason for review|Evidence fields|Recommended reviewer action|Final reviewer decision|Reviewer comments",
    15: "Error category|Study PID|Number of affected records|Current behavior|Expected behavior|Root cause|Related rule IDs|Risk level|Recommended correction|Correction status",
    16: "Question ID|Related rule|Decision needed|Why the decision matters|Number of affected records|Available options|Recommended option|Risk if unresolved|Final decision",
}
REQUIRED_COLUMNS = {key: value.split("|") for key, value in REQUIRED_COLUMNS.items()}
AUDIT_COLUMNS = ["Processing step", "Timestamp", "Input file", "Rule version",
                 "Number of records affected", "Output file", "Warnings or errors"]
EMAIL_RE = re.compile(r"(?<![\w*])[-\w.!#$%&'+/=?^`{|}~]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![\w*])")
EXCEL_ERRORS = {"#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!", "#SPILL!", "#CALC!"}


@lru_cache(maxsize=4)
def _private_value_pattern(values: tuple[str, ...]):
    tokens = sorted({str(value).casefold() for value in values if value and str(value).strip()}, key=len, reverse=True)
    return re.compile("|".join(re.escape(value) for value in tokens)) if tokens else None


def assert_presentation_privacy(value: object, *, forbidden_values: Iterable[str] = (), location: str = "output") -> None:
    """Fail closed without returning sensitive strings in exception messages.

    Callers must provide known private values (e.g. names/phones) as well as
    source emails when their evidence is not structurally anonymized. Matching
    short/common tokens is intentionally the caller's responsibility.
    """
    text = str(value)
    if EMAIL_RE.search(text):
        raise ValueError(f"Unmasked email detected in {location}")
    pattern = _private_value_pattern(tuple(forbidden_values))
    if pattern and pattern.search(text.casefold()):
        raise ValueError(f"Restricted source value detected in {location}")


def _json_value(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        if pd.isna(value):
            return None
        return value.isoformat()
    if isinstance(value, (list, tuple, dict, set)):
        return json.dumps(list(value) if isinstance(value, set) else value, ensure_ascii=False, default=str)
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Nonfinite analytical value cannot be exported")
    if isinstance(value, str) and len(value) > 32767:
        raise ValueError("Cell exceeds Excel's text limit; shorten the presentation evidence")
    return value


def _frame_rows(frame: pd.DataFrame) -> list[list[object]]:
    return [[_json_value(value) for value in row] for row in frame.itertuples(index=False, name=None)]


def _assert_frame(frame: pd.DataFrame, required: list[str], name: str, forbidden: tuple[str, ...]) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{name} must be a DataFrame")
    if frame.columns.duplicated().any():
        raise ValueError(f"Duplicate columns in {name}")
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError(f"{name} missing required columns: {sorted(missing)}")
    for column in frame.columns:
        assert_presentation_privacy(column, forbidden_values=forbidden, location=f"{name} header")
    for row_number, row in enumerate(_frame_rows(frame), 1):
        for value in row:
            assert_presentation_privacy(value, forbidden_values=forbidden, location=f"{name} row {row_number}")


def validate_presentation_tables(tables: Mapping[int, pd.DataFrame], *, forbidden_values: Iterable[str] = ()) -> dict:
    """Check the export schema and the record-level invariants independent of Excel."""
    if set(tables) != set(range(1, 17)):
        raise ValueError("Exactly the 16 requested tables are required")
    forbidden = tuple(forbidden_values)
    for number, frame in tables.items():
        _assert_frame(frame, REQUIRED_COLUMNS[number], f"Table {number}", forbidden)
    records = tables[13]
    if records[["Study PID", "Study name", "Record ID"]].isna().any().any():
        raise ValueError("Record-level study identity or record ID is missing")
    if records[["Study PID", "Study name", "Record ID"]].astype(str).apply(lambda col: col.str.strip().eq("")).any().any():
        raise ValueError("Record-level study identity or record ID is blank")
    if records.duplicated(["Study PID", "Record ID"]).any():
        raise ValueError("Duplicate study/record keys in record-level results")
    if (~records["Revised classification"].isin(CLASSIFICATIONS)).any():
        raise ValueError("Unknown revised classification")
    if records["Plain-language reason"].isna().any() or records["Plain-language reason"].astype(str).str.strip().eq("").any():
        raise ValueError("A revised classification is missing its reason")
    pid = records["Study PID"].astype(str).str.replace(r"\.0$", "", regex=True)
    record_id = pd.to_numeric(records["Record ID"], errors="coerce")
    if (pid.eq("5749") & record_id.between(1, 445)).any():
        raise ValueError("PID 5749 archive records 1–445 are in the analytical population")
    queue = tables[14]
    keys = set(map(tuple, records[["Study PID", "Record ID"]].astype(str).to_numpy()))
    queue_keys = list(map(tuple, queue[["Study PID", "Record ID"]].astype(str).to_numpy()))
    if len(set(queue_keys)) != len(queue_keys) or not set(queue_keys) <= keys:
        raise ValueError("Review queue contains duplicate or unknown records")
    if queue["Proposed classification"].eq("PAY NOW").any() or queue["Proposed classification"].eq("EXCLUDE").any():
        raise ValueError("Review queue includes a record without a review classification")
    priority = pd.to_numeric(queue["Review priority"], errors="coerce")
    if len(queue) and (priority.isna().any() or not priority.is_monotonic_increasing):
        raise ValueError("Review queue is not sorted by numeric review priority")
    # A transition matrix may include study blocks; the all-study grand total
    # is supplied by the analysis, not inferred from potentially repeated rows.
    transitions = tables[11]
    matrix_totals = transitions["Current classification"].astype(str).str.casefold().isin(["total", "all records", "grand total"])
    if not matrix_totals.any():
        raise ValueError("Transition matrix has no total row")
    if not (pd.to_numeric(transitions.loc[matrix_totals, "Total"], errors="coerce") == len(records)).any():
        raise ValueError("Transition-matrix grand total does not match record results")
    return {"records": len(records), "review_queue_records": len(queue), "table_rows": {str(n): len(f) for n, f in tables.items()},
            "unique_study_record_keys": True, "presentation_privacy": "PASS", "archive_exclusion": "PASS"}


def _column_width(label: str) -> int:
    if label in {"Study PID", "Record ID"}:
        return 13
    if "classification" in label.lower() or label in {"Final action", "Current action", "Proposed action"}:
        return 34
    if label == "Study name":
        return 38
    if any(word in label.lower() for word in ("condition", "reason", "description", "evidence", "notes", "comment", "modification", "decision", "risk", "correction", "recommendation", "explanation", "options")):
        return 48
    return min(34, max(17, len(label) // 2 + 8))


def build_workbook_payload(tables: Mapping[int, pd.DataFrame], *, table_notes: Mapping[int, str] | None = None) -> dict:
    sheets = []
    for number in range(1, 17):
        frame = tables[number]
        headers = list(frame.columns)
        formats = []
        for label in headers:
            if "percentage" in label.lower():
                formats.append('0.0"%"')  # Analysis supplies percentages on a 0–100 scale.
            elif label in {"Study PID", "Record ID"} or "ID" in label:
                formats.append("@")
            elif pd.api.types.is_numeric_dtype(frame[label]):
                formats.append("#,##0.0" if "duration" in label.lower() else "#,##0")
            else:
                formats.append(None)
        sheets.append({"number": number, "name": SHEET_NAMES[number],
                       "title": f"Table {number}. {TABLE_TITLES[number]}",
                       "headers": headers, "rows": _frame_rows(frame), "headerRow": 5,
                       "widths": [_column_width(label) for label in headers], "formats": formats,
                       "note": (table_notes or {}).get(number, ""),
                       "classifications": {k: "#" + v for k, v in CLASSIFICATION_COLORS.items()}})
    return {"sheets": sheets}


def _artifact_modules() -> Path:
    candidates = [os.environ.get("CODEX_ARTIFACT_NODE_MODULES"),
                  str(Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules")]
    for candidate in candidates:
        if candidate and (Path(candidate) / "@oai/artifact-tool/package.json").is_file():
            return Path(candidate)
    raise RuntimeError("The cached Artifact Tool runtime is unavailable")


def validate_saved_workbook(path: Path, payload: dict, *, forbidden_values: Iterable[str] = ()) -> dict:
    """Compare every stored cell and verify native workbook features read-only."""
    import openpyxl
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)
    checked = 0
    forbidden = tuple(forbidden_values)
    try:
        if workbook.sheetnames != [spec["name"] for spec in payload["sheets"]]:
            raise ValueError("Saved workbook does not contain exactly the 16 requested tabs")
        for spec in payload["sheets"]:
            sheet = workbook[spec["name"]]
            header = spec["headerRow"]
            for r, row in enumerate([spec["headers"], *spec["rows"]], header):
                for c, expected in enumerate(row, 1):
                    cell = sheet.cell(r, c)
                    actual = cell.value
                    expected = None if expected == "" else expected
                    if actual != expected:
                        raise ValueError(f"Saved value differs from input in {sheet.title}!{cell.coordinate}")
                    checked += 1
            if sheet.freeze_panes is None or int(re.sub(r"\D", "", sheet.freeze_panes)) != header + 1:
                raise ValueError(f"Missing frozen header in {sheet.title}")
            if len(sheet.tables) != 1 or next(iter(sheet.tables.values())).autoFilter is None:
                raise ValueError(f"Missing filterable table in {sheet.title}")
            for row in sheet.iter_rows():
                for cell in row:
                    if cell.data_type == "f":
                        raise ValueError(f"Unexpected formula in static analytical table {sheet.title}!{cell.coordinate}")
                    if cell.data_type == "e" or (isinstance(cell.value, str) and cell.value in EXCEL_ERRORS):
                        raise ValueError(f"Invalid Excel value in {sheet.title}!{cell.coordinate}")
                    if cell.value is not None:
                        assert_presentation_privacy(cell.value, forbidden_values=forbidden, location=f"{sheet.title}!{cell.coordinate}")
            for r, row in enumerate(spec["rows"], header + 1):
                for c, value in enumerate(row, 1):
                    if isinstance(value, str) and value in CLASSIFICATION_COLORS:
                        color = sheet.cell(r, c).fill.fgColor.rgb
                        if not isinstance(color, str) or color[-6:].upper() != CLASSIFICATION_COLORS[value]:
                            raise ValueError(f"Missing classification color in {sheet.title}!{sheet.cell(r,c).coordinate}")
        return {"sheets": len(workbook.sheetnames), "cells_compared": checked,
                "saved_cell_parity": "PASS", "frozen_headers": "PASS", "filters": "PASS",
                "classification_colors": "PASS", "workbook_privacy": "PASS", "unexpected_formulas": 0}
    finally:
        workbook.close()


def _csv_safe(frame: pd.DataFrame) -> pd.DataFrame:
    """Quote spreadsheet command prefixes without changing source analytical data."""
    return frame.map(lambda value: "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value)


def export_validity_deliverables(
    tables: Mapping[int, pd.DataFrame], *, output_dir: Path | str | None = None,
    rule_specification: pd.DataFrame | None = None, report_text: str,
    audit_log: pd.DataFrame, forbidden_values: Iterable[str] = (), render: bool = True,
    table_notes: Mapping[int, str] | None = None,
) -> dict:
    """Create the eight requested outputs; publish only after all validations pass.

    The workbook contains analytical results, not the response-level production
    rule engine. No source response is changed. Table percentages use 0–100 units.
    """
    forbidden = tuple(forbidden_values)
    validation = validate_presentation_tables(tables, forbidden_values=forbidden)
    if rule_specification is None:
        raise ValueError("Supply the combined current/proposed/approved/rejected rule specification")
    _assert_frame(rule_specification, ["Rule ID", "Rule name"], "Rule specification", forbidden)
    _assert_frame(audit_log, AUDIT_COLUMNS, "Audit log", forbidden)
    assert_presentation_privacy(report_text, forbidden_values=forbidden, location="Report")
    for number, note in (table_notes or {}).items():
        assert_presentation_privacy(note, forbidden_values=forbidden, location=f"Table {number} note")
    output_dir = Path(output_dir or DEFAULT_OUTPUT_DIR).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    payload = build_workbook_payload(tables, table_notes=table_notes)
    frames = {"record_level_results.csv": tables[13], "manual_review_queue.csv": tables[14],
              "rule_specification.csv": rule_specification, "regression_test_results.csv": tables[7],
              "misclassification_analysis.csv": tables[15], "analysis_audit_log.csv": audit_log}
    names = ["bot_analysis_summary.xlsx", *frames, "bot_analysis_report.md"]
    with tempfile.TemporaryDirectory(prefix=".validity_export_", dir=output_dir) as temporary:
        work = Path(temporary)
        (work / "node_modules").symlink_to(_artifact_modules(), target_is_directory=True)
        shutil.copy2(PROJECT_DIR / "validity_workbook.mjs", work / "builder.mjs")
        (work / "payload.json").write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        workbook_path = work / "bot_analysis_summary.xlsx"
        command = [shutil.which("node") or "node", str(work / "builder.mjs"), str(work / "payload.json"), str(workbook_path)]
        previews = work / "validation_previews"
        if render:
            command.append(str(previews))
        authored = subprocess.run(command, capture_output=True, text=True, timeout=900)
        if authored.returncode:
            # Library diagnostics could contain source strings. The payload has
            # been checked, but do not echo arbitrary subprocess output.
            raise RuntimeError(f"Spreadsheet authoring failed (exit {authored.returncode})")
        validation.update(validate_saved_workbook(workbook_path, payload, forbidden_values=forbidden))
        validation.update({"authoring_engine": "artifact-tool", "recalculated": True,
                           "native_readback": "openpyxl", "formula_engine_check": "Not applicable: values-only analysis",
                           "rendered_tabs": 16 if render else 0})
        for name, frame in frames.items():
            safe = _csv_safe(frame)
            safe.to_csv(work / name, index=False)
            restored = pd.read_csv(work / name, dtype=str, keep_default_na=False)
            if len(restored) != len(frame) or list(restored.columns) != list(frame.columns):
                raise ValueError(f"CSV row/column mismatch in {name}")
            assert_presentation_privacy((work / name).read_text(), forbidden_values=forbidden, location=name)
        (work / "bot_analysis_report.md").write_text(report_text, encoding="utf-8")
        validation["files"] = {name: {"bytes": (work / name).stat().st_size,
                                     "sha256": hashlib.sha256((work / name).read_bytes()).hexdigest()} for name in names}
        # Preserve previous deliverables, while refusing to publish partial
        # results from authoring, privacy, or content-validation failures.
        existing = [name for name in names if (output_dir / name).exists()]
        if existing:
            backup = output_dir / "previous_results" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup.mkdir(parents=True)
            backup.chmod(0o700)
            for name in existing:
                shutil.copy2(output_dir / name, backup / name)
        for name in names:
            os.replace(work / name, output_dir / name)
            (output_dir / name).chmod(0o600)
        preview_paths = []
        if render:
            destination = output_dir / "validation_previews"
            destination.mkdir(exist_ok=True)
            destination.chmod(0o700)
            for preview in sorted(previews.glob("*.png")):
                os.replace(preview, destination / preview.name)
                (destination / preview.name).chmod(0o600)
                preview_paths.append(str(destination / preview.name))
        (output_dir / "deliverable_validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
        (output_dir / "deliverable_validation.json").chmod(0o600)
    return {"files": {name: str(output_dir / name) for name in names}, "validation": validation, "previews": preview_paths}
