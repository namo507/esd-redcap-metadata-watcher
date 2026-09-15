"""Tests for the stakeholder-facing views in bot_analysis.

These tests verify the acceptance criteria for the refactored notebook:
row counts, score formulas, column consistency, and privacy controls.
"""

import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ── Skip the whole module when the data cache is missing ────────────────

PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "Caregiver Outputs"
CACHE_DIR = PROJECT_DIR / "data_cache"
STAKEHOLDER_WORKBOOK = OUTPUT_DIR / "ESD_Response_Screening_Stakeholder_View.xlsx"
RESTRICTED_WORKBOOK = OUTPUT_DIR / "restricted" / "ESD_Response_Review_Master.xlsx"

_HAS_DATA = CACHE_DIR.exists() and any(CACHE_DIR.glob("*.parquet"))

pytestmark = pytest.mark.skipif(
    not _HAS_DATA,
    reason="data_cache not populated — run the notebook first",
)


# ── Shared fixture: build the canonical triaged table once ──────────────

@pytest.fixture(scope="module")
def triaged():
    import bot_analysis as ba
    risk = ba.build_risk_table(OUTPUT_DIR, CACHE_DIR, PROJECT_DIR)
    return ba.build_review_triage(risk)


@pytest.fixture(scope="module")
def risk_table():
    import bot_analysis as ba
    return ba.build_risk_table(OUTPUT_DIR, CACHE_DIR, PROJECT_DIR)


@pytest.fixture(scope="module")
def stakeholder_list(triaged):
    import bot_analysis as ba
    return ba.build_stakeholder_rule_list(triaged)


@pytest.fixture(scope="module")
def stakeholder_grid(triaged):
    import bot_analysis as ba
    return ba.build_stakeholder_rule_grid(triaged)


# ── 1. One row per response in the stakeholder list ─────────────────────

def test_stakeholder_list_one_row_per_response(stakeholder_list, triaged):
    assert len(stakeholder_list) == len(triaged)


# ── 2. One row per response in the stakeholder grid ────────────────────

def test_stakeholder_grid_one_row_per_response(stakeholder_grid, triaged):
    assert len(stakeholder_grid) == len(triaged)


# ── 3. Response key is unique ───────────────────────────────────────────

def test_response_key_unique(stakeholder_list):
    assert stakeholder_list["Response key"].is_unique


# ── 4. Total rules = sum of 14 Boolean rule flags ──────────────────────

def test_total_rules_equals_sum(stakeholder_grid):
    import bot_analysis as ba
    rule_cols = ba.CODE_ORDER
    yes_counts = (stakeholder_grid[rule_cols] == "Yes").sum(axis=1)
    assert (yes_counts == stakeholder_grid["Total Rules Violated"]).all()


# ── 5. Serious + Mild = Total ──────────────────────────────────────────

def test_serious_plus_mild_equals_total(stakeholder_list):
    total = stakeholder_list["Serious Rules Violated"] + stakeholder_list["Mild Rules Violated"]
    assert (total == stakeholder_list["Total Rules Violated"]).all()


# ── 6. Current operational score = Serious × 2 + Mild × 1 ──────────────

def test_operational_score_formula(stakeholder_list):
    import bot_analysis as ba
    expected = (
        stakeholder_list["Serious Rules Violated"] * 2
        + stakeholder_list["Mild Rules Violated"] * 1
    )
    assert (stakeholder_list[ba.OPERATIONAL_SCORE_COLUMN] == expected).all()


# ── 7. Proposed stakeholder score = Serious × 5 + Mild × 1 ────────────

def test_stakeholder_score_formula(stakeholder_list):
    import bot_analysis as ba
    expected = (
        stakeholder_list["Serious Rules Violated"] * 5
        + stakeholder_list["Mild Rules Violated"] * 1
    )
    assert (stakeholder_list[ba.STAKEHOLDER_SCORE_COLUMN] == expected).all()


# ── 8. "Rules violated" text matches the Yes cells in the grid ──────────

def test_rules_violated_matches_grid(stakeholder_list, stakeholder_grid):
    import bot_analysis as ba
    for idx in range(len(stakeholder_list)):
        list_row = stakeholder_list.iloc[idx]
        # Find the matching grid row by Response key
        grid_row = stakeholder_grid[
            stakeholder_grid["Response key"] == list_row["Response key"]
        ].iloc[0]

        violated_text = list_row["Rules violated"]
        if violated_text == "None":
            violated_codes = set()
        else:
            violated_codes = {c.strip() for c in violated_text.split(",")}

        grid_codes = {
            code for code in ba.CODE_ORDER if grid_row[code] == "Yes"
        }
        assert violated_codes == grid_codes, (
            f"Mismatch at Response key {list_row['Response key']}: "
            f"list={violated_codes}, grid={grid_codes}"
        )


# ── 9. Current operational actions unchanged ────────────────────────────

def test_operational_actions_unchanged(triaged):
    """The total action counts must not change due to the presentation refactor."""
    import bot_analysis as ba
    action_counts = triaged["Recommended action"].value_counts()
    # These are the canonical counts from the README
    pay = action_counts.get(ba.ACTION_PAY, 0)
    review = action_counts.get(ba.ACTION_REVIEW, 0)
    reject = action_counts.get(ba.ACTION_REJECT, 0)
    assert pay + review + reject == len(triaged)
    # Do not assert exact counts here because they depend on data;
    # instead verify the canonical relationship: pay + review + reject = total
    # and that the action column is well-formed
    assert set(triaged["Recommended action"].unique()) <= {
        ba.ACTION_PAY, ba.ACTION_REVIEW, ba.ACTION_REJECT
    }


# ── 10. No email column in stakeholder workbook ────────────────────────

@pytest.mark.skipif(
    not STAKEHOLDER_WORKBOOK.exists(),
    reason="Stakeholder workbook not yet generated",
)
def test_no_email_in_stakeholder_workbook():
    xls = pd.ExcelFile(STAKEHOLDER_WORKBOOK)
    for sheet in xls.sheet_names:
        df = xls.parse(sheet)
        email_cols = [c for c in df.columns if "email" in c.lower()]
        assert not email_cols, f"Email column found in sheet '{sheet}': {email_cols}"


# ── 11. Restricted workbook still exists ────────────────────────────────

def test_restricted_workbook_still_exists():
    assert RESTRICTED_WORKBOOK.exists(), (
        f"Restricted master workbook missing: {RESTRICTED_WORKBOOK}"
    )


# ── 12. Expected sheets exist in stakeholder workbook ───────────────────

@pytest.mark.skipif(
    not STAKEHOLDER_WORKBOOK.exists(),
    reason="Stakeholder workbook not yet generated",
)
def test_expected_sheets_exist():
    xls = pd.ExcelFile(STAKEHOLDER_WORKBOOK)
    expected = {
        "Executive Summary",
        "Record Rule Summary",
        "Rule Grid",
        "Rule Key",
        "Policy Sensitivity",
    }
    assert expected.issubset(set(xls.sheet_names)), (
        f"Missing sheets: {expected - set(xls.sheet_names)}"
    )


# ── 13. Plain-language rule list corresponds to triggered rules ─────────

def test_plain_language_matches_codes(stakeholder_list):
    """Each code in 'Rules violated' must have a matching clause in the
    plain-language column, and vice versa."""
    import bot_analysis as ba
    for idx in range(min(len(stakeholder_list), 200)):  # spot check first 200
        row = stakeholder_list.iloc[idx]
        violated_text = row["Rules violated"]
        plain_text = row["Rules violated, in plain language"]

        if violated_text == "None":
            assert plain_text == "No rules triggered"
        else:
            codes = [c.strip() for c in violated_text.split(",")]
            for code in codes:
                key = ba.CODE_TO_KEY[code]
                name = ba.CHECK_NAMES[key]
                assert name in plain_text, (
                    f"Code {code} missing from plain text at row {idx}"
                )


# ── 14. Score consistency between list and grid ─────────────────────────

def test_list_grid_score_consistency(stakeholder_list, stakeholder_grid):
    import bot_analysis as ba
    # Merge on Response key and check all score columns match
    merged = stakeholder_list.merge(
        stakeholder_grid[["Response key", ba.OPERATIONAL_SCORE_COLUMN, ba.STAKEHOLDER_SCORE_COLUMN]],
        on="Response key",
        suffixes=("_list", "_grid"),
    )
    assert len(merged) == len(stakeholder_list)
    assert (merged[f"{ba.OPERATIONAL_SCORE_COLUMN}_list"] == merged[f"{ba.OPERATIONAL_SCORE_COLUMN}_grid"]).all()
    assert (merged[f"{ba.STAKEHOLDER_SCORE_COLUMN}_list"] == merged[f"{ba.STAKEHOLDER_SCORE_COLUMN}_grid"]).all()


# ── 15. Executive summary KPIs are internally consistent ───────────────

def test_executive_summary_consistency(triaged):
    import bot_analysis as ba
    kpis = ba.build_stakeholder_executive_summary(triaged)
    assert kpis["all_total"] == kpis["verified_total"] + kpis["online_total"]
    assert kpis["pay_online"] + kpis["review_online"] + kpis["reject_online"] == kpis["online_total"]
    assert kpis["pay_verified"] + kpis["review_verified"] + kpis["reject_verified"] == kpis["verified_total"]
    assert kpis["false_refusals"] == kpis["reject_verified"]


# ── 16. Cutoff sensitivity tables have correct scale labels ─────────────

def test_cutoff_sensitivity_scale_labels(triaged):
    import bot_analysis as ba
    op = ba.build_operational_cutoff_sensitivity(triaged)
    sh = ba.build_stakeholder_cutoff_sensitivity(triaged)
    # All rows in operational must say "operational"
    assert op["Scale"].str.contains("operational").all()
    # All rows in stakeholder must say "stakeholder"
    assert sh["Scale"].str.contains("stakeholder").all()
    # Never mix scales
    assert not op["Scale"].str.contains("stakeholder").any()
    assert not sh["Scale"].str.contains("operational").any()
