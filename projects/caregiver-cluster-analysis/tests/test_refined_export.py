"""Real Excel-engine parity and mutation checks with synthetic, non-PII data."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import openpyxl
import pandas as pd
import pytest

import refined_export as export
from test_refined_screening import response, score


def synthetic_bundle():
    scored = score([response()])
    sources = SimpleNamespace(
        registry={4797: {"display_label": "Synthetic study (PID 4797)", "sheet_name": "Study 1 - CAN (PID 4797)"}},
        exclusions=pd.DataFrame(columns=["project_id", "record_id", "response_key", "exclusion_reason",
                                         "matched_text_fields", "archive_clone", "prelaunch_protocol", "contains_test_word"]),
        provenance=pd.DataFrame([{"project_id": 4797, "study_name": "Synthetic", "raw_records": 1,
                                  "included_records": 1, "excluded_records": 0, "source_mapping_review_records": 0}]),
        field_mapping=pd.DataFrame([{"project_id": 4797, "source_field": "synthetic"}]),
        folding_audit=pd.DataFrame(columns=["project_id", "canonical_field"]),
    )
    payload, summary = export.build_export_payload(sources, scored)
    return payload, scored, summary


def require_engines():
    engine = shutil.which("soffice") or shutil.which("libreoffice")
    try:
        modules = export._artifact_modules()
    except RuntimeError:
        pytest.skip("Codex artifact-tool runtime unavailable")
    if not engine or not shutil.which("node"):
        pytest.skip("Native LibreOffice and Node required for engine mutation check")
    return engine, modules


def test_source_reconciliation_does_not_accept_invented_source_totals():
    payload, scored, summary = synthetic_bundle()
    assert summary.iloc[-1]["Source records"] == 1
    assert len(payload["sheets"]) == 9
    assert scored.iloc[0]["Recommended action"] == "Pay now"


def test_native_recalculation_and_atomic_failure_protect_existing_output(tmp_path):
    require_engines()
    payload, scored, _ = synthetic_bundle()
    target = tmp_path / "review.xlsx"
    result = export.write_refined_workbook(target, payload, scored, render=False)
    assert result["native_recalculation"] == "PASS"
    original = target.read_bytes()
    broken = copy.deepcopy(payload)
    audit = next(s for s in broken["sheets"] if s["name"] == "Rule Audit")
    audit["rows"][0][audit["headers"].index("Thoughts and feelings minutes")] = 1
    with pytest.raises(RuntimeError, match="Formula mismatch"):
        export.write_refined_workbook(target, broken, scored, render=False)
    assert target.read_bytes() == original


def test_changed_evidence_recalculates_flags_score_action_and_exposes_disagreements(tmp_path):
    engine, modules = require_engines()
    payload, _, _ = synthetic_bundle()
    audit = next(s for s in payload["sheets"] if s["name"] == "Rule Audit")
    for column, value in {"Family Information minutes": 1, "Thoughts and feelings minutes": 1,
                          "R1 availability": "Not evaluable"}.items():
        audit["rows"][0][audit["headers"].index(column)] = value
    # The record-level Python decisions remain unchanged. Excel must detect drift.
    changes = {"R1 result": "FLAG", "R2 result": "FLAG", "R3 result": "FLAG",
               "R1 parity": "ERROR", "R2 parity": "ERROR", "R3 parity": "ERROR",
               "Score from Excel": 5, "Score parity": "ERROR",
               "Action from Excel": "Check by hand", "Decision parity": "ERROR"}
    for column, value in changes.items():
        cell = export.excel_column(audit["headers"].index(column)) + "2"
        audit["expected"][cell] = value
    (tmp_path / "node_modules").symlink_to(modules, target_is_directory=True)
    shutil.copy2(export.PROJECT_DIR / "refined_workbook.mjs", tmp_path / "builder.mjs")
    (tmp_path / "payload.json").write_text(json.dumps(payload, allow_nan=False))
    authored = tmp_path / "authored.xlsx"
    run = subprocess.run([shutil.which("node"), str(tmp_path / "builder.mjs"),
                          str(tmp_path / "payload.json"), str(authored)], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-1000:]
    calculated = tmp_path / "calculated"
    calculated.mkdir()
    run = subprocess.run([engine, f"-env:UserInstallation={(tmp_path / 'profile').as_uri()}", "--headless",
                          "--convert-to", "xlsx", "--outdir", str(calculated), str(authored)],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0 and (calculated / authored.name).exists()
    workbook = openpyxl.load_workbook(calculated / authored.name, data_only=True, read_only=True)
    for column, value in changes.items():
        cell = export.excel_column(audit["headers"].index(column)) + "2"
        assert workbook["Rule Audit"][cell].value == value
    assert workbook["All Records"]["D2"].value == "Pay now"
    workbook.close()
