"""Exporter regressions use synthetic data, never participant source records."""
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validity_deliverables import (
    AUDIT_COLUMNS, CLASSIFICATIONS, REQUIRED_COLUMNS, assert_presentation_privacy,
    build_workbook_payload, export_validity_deliverables, validate_presentation_tables,
    validate_saved_workbook, _csv_safe,
)


def synthetic_tables():
    tables = {n: pd.DataFrame([{column: "Not available" for column in columns}])
              for n, columns in REQUIRED_COLUMNS.items()}
    records = []
    for index, label in enumerate(CLASSIFICATIONS):
        row = {column: "Not available" for column in REQUIRED_COLUMNS[13]}
        row.update({"Study PID": "9999", "Study name": "Synthetic test study",
                    "Record ID": str(index + 1), "Revised classification": label,
                    "Current classification": "PAY NOW", "Plain-language reason": "Synthetic fixture",
                    "Triggered rule IDs": "SYNTHETIC", "Masked email or email hash": "sha256:000000"})
        records.append(row)
    tables[13] = pd.DataFrame(records)
    queue = []
    for priority, record in enumerate([records[1], records[2], records[4]], 1):
        row = {column: "Not available" for column in REQUIRED_COLUMNS[14]}
        row.update({"Review priority": priority, "Study PID": "9999", "Study name": "Synthetic test study",
                    "Record ID": record["Record ID"], "Proposed classification": record["Revised classification"]})
        queue.append(row)
    tables[14] = pd.DataFrame(queue)
    tables[11] = pd.DataFrame([
        {"Current classification": "PAY NOW", **dict.fromkeys(CLASSIFICATIONS, 1), "Total": 5},
        {"Current classification": "Total", **dict.fromkeys(CLASSIFICATIONS, 1), "Total": 5},
    ])
    return tables


def export_args(tables):
    return dict(tables=tables, rule_specification=pd.DataFrame([{"Rule ID": "SYNTHETIC", "Rule name": "Fixture"}]),
                report_text="Synthetic test only. No participant records.",
                audit_log=pd.DataFrame([{key: "Not available" for key in AUDIT_COLUMNS}]))


def test_schema_requires_all_sixteen_tables_and_all_prompt_columns():
    tables = synthetic_tables()
    assert validate_presentation_tables(tables)["records"] == 5
    with pytest.raises(ValueError, match="16 requested tables"):
        validate_presentation_tables({key: frame for key, frame in tables.items() if key != 16})
    tables[4] = tables[4].drop(columns="Anonymized evidence")
    with pytest.raises(ValueError, match="Anonymized evidence"):
        validate_presentation_tables(tables)


@pytest.mark.parametrize("text", ["person@example.org", "Evidence includes PERSON+tag@example.org in an answer"])
def test_emails_rejected_without_echoing_private_value(text):
    with pytest.raises(ValueError, match="Unmasked email") as error:
        assert_presentation_privacy(text)
    assert "example.org" not in str(error.value)


def test_known_private_values_rejected_and_masked_linkage_allowed():
    with pytest.raises(ValueError, match="Restricted source value"):
        assert_presentation_privacy("Evidence for TEST PERSON", forbidden_values=["Test Person"])
    assert_presentation_privacy("sha256:123abc")
    assert_presentation_privacy("a***@example.org")


def test_archive_and_duplicate_keys_rejected():
    tables = synthetic_tables()
    tables[13].loc[0, "Study PID"] = "5749"
    with pytest.raises(ValueError, match="archive records"):
        validate_presentation_tables(tables)
    tables = synthetic_tables()
    tables[13].loc[1, "Record ID"] = "1"
    with pytest.raises(ValueError, match="Duplicate study/record"):
        validate_presentation_tables(tables)


def test_review_queue_excludes_payments_and_requires_sorted_priority():
    tables = synthetic_tables()
    tables[14].loc[0, "Proposed classification"] = "PAY NOW"
    with pytest.raises(ValueError, match="without a review classification"):
        validate_presentation_tables(tables)
    tables = synthetic_tables()
    tables[14] = tables[14].iloc[::-1]
    with pytest.raises(ValueError, match="sorted"):
        validate_presentation_tables(tables)


def test_csv_formula_prefixes_are_escaped_without_mutating_analysis():
    source = pd.DataFrame({"Evidence": ["=SUM(A1:A2)", "-2 typed answer", "@text", "+formula", "Safe"], "Count": [-2, 0, 1, 2, 3]})
    safe = _csv_safe(source)
    assert safe["Evidence"].tolist() == ["'=SUM(A1:A2)", "'-2 typed answer", "'@text", "'+formula", "Safe"]
    assert safe["Count"].tolist() == source["Count"].tolist()
    assert source.loc[0, "Evidence"] == "=SUM(A1:A2)"


def test_privacy_failure_preserves_previous_deliverable(tmp_path):
    original = tmp_path / "bot_analysis_summary.xlsx"
    original.write_bytes(b"Existing deliverable")
    tables = synthetic_tables()
    tables[4].loc[0, "Anonymized evidence"] = "private@example.org"
    with pytest.raises(ValueError, match="Unmasked email"):
        export_validity_deliverables(**export_args(tables), output_dir=tmp_path)
    assert original.read_bytes() == b"Existing deliverable"
    assert list(tmp_path.iterdir()) == [original]


def test_sixteen_tab_export_native_readback_and_previews(tmp_path):
    tables = synthetic_tables()
    # An empty optional question table still needs a filterable native tab.
    tables[16] = tables[16].iloc[0:0]
    result = export_validity_deliverables(**export_args(tables), output_dir=tmp_path)
    assert len(result["files"]) == 8
    assert len(result["previews"]) == 16
    assert all(Path(path).is_file() for path in result["files"].values())
    assert all(Path(path).read_bytes().startswith(b"\x89PNG") for path in result["previews"])
    assert result["validation"]["saved_cell_parity"] == "PASS"
    assert result["validation"]["classification_colors"] == "PASS"
    assert result["validation"]["frozen_headers"] == "PASS"
    assert validate_saved_workbook(Path(result["files"]["bot_analysis_summary.xlsx"]), build_workbook_payload(tables))["sheets"] == 16
    restored = pd.read_csv(result["files"]["record_level_results.csv"], dtype=str)
    assert restored["Record ID"].tolist() == tables[13]["Record ID"].tolist()
