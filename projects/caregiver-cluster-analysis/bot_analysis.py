"""Stakeholder-friendly bot analysis helpers for the caregiver study.

This module provides simple, presentation-ready visualisations, plain-English
column names, and a multi-sheet Excel export targeted at non-technical reviewers
(e.g., Dr. Bradshaw).  It reads the pipeline's committed outputs and REDCap
caches without duplicating computation logic.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import yaml
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter


# ── Friendly column names ───────────────────────────────────────────────────

RULE_FRIENDLY_NAMES: dict[str, str] = {
    "rule_R1": "Survey Too Fast (<11.57 min)",
    "rule_R2": "Attitudes Too Fast (<7.85 min)",
    "rule_R3": "Any Section Below Speed Floor",
    "rule_R4": "Flat-Line Responses (Low Variation)",
    "rule_R5": "Duplicate Response Pattern",
    "rule_R6": "Bursty Submission Timing",
    "rule_R7": "Near-Duplicate Open Text",
    "rule_R8": "Illogical Family Info",
    "rule_R9": "Impossible Demographics",
    "rule_R10": "Attention Check Failed",
}

COLUMN_FRIENDLY_NAMES: dict[str, str] = {
    "record_id": "Record ID",
    "tier_label": "Trust Category",
    "tier": "Tier",
    "giftcard_decision": "Payment Decision",
    "decision_reason": "Reason",
    "tier1_hit_count": "Hard Check Violations",
    "tier1_rule_combo": "Hard Rules Triggered",
    "soft_triggered_rules": "Soft Rules Triggered",
    "classification": "Classification",
    "action": "Recommended Action",
    "confirmed_reason": "Reason for Rejection",
    "total_rules_fired": "Total Rules Fired",
    "tier_1_rules_fired": "Hard Rules Fired",
    "soft_rules_fired": "Soft Rules Fired",
    **RULE_FRIENDLY_NAMES,
}

# ── Category colour palette (colourblind-friendly) ──────────────────────────

CATEGORY_COLORS: dict[str, str] = {
    "Confirmed Bot": "#A85D75",       # pink
    "Needs Human Review": "#C67C2D",  # orange
    "Likely Real Caregivers": "#B08A2E",  # gold
    "Real Caregivers": "#1F5A7A",     # blue
}

CATEGORY_ORDER = [
    "Confirmed Bot",
    "Needs Human Review",
    "Likely Real Caregivers",
    "Real Caregivers",
]


PROJECT_LABELS: dict[str, str] = {
    "clean_4797": "Study 1 - Verified (4797)",
    "dirty_4581": "Study 2 - Legacy (4581)",
}

PAYMENT_DECISION_LABELS: dict[str, str] = {
    "auto_eligible": "Cleared for payment now",
    "eligible_low_risk_review": "Low-risk provisional approval",
    "manual_review": "Needs human review",
    "do_not_pay_pending_adjudication": "Confirmed bot / reject",
}

SUBCLASSIFICATION_LABELS: dict[str, str] = {
    "Real Caregiver": "No rule violations",
    "Likely Real Caregiver": "One soft check only",
    "Needs Review — Single Hard Flag": "Single hard-check flag",
    "Needs Review — Multiple Hard Flags": "Multiple hard-check flags",
    "Needs Review — Multiple Soft Flags": "Two or more soft-check flags",
    "Confirmed Bot": "Hard logic contradiction with speed flags",
}

GENDER_LABELS: dict[str, str] = {
    "0": "Prefer not to answer",
    "1": "Man",
    "2": "Woman",
    "3": "Non-binary",
    "4": "Other",
}

RACE_CHECKBOX_LABELS: dict[str, str] = {
    "demo_maternalrace___1": "American Indian/Alaska Native",
    "demo_maternalrace___2": "Asian",
    "demo_maternalrace___3": "Native Hawaiian or Other Pacific Islander",
    "demo_maternalrace___4": "Black or African American",
    "demo_maternalrace___5": "White",
    "demo_maternalrace___6": "Unknown",
    "demo_maternalrace___7": "Other",
}

ETHNICITY_CHECKBOX_LABELS: dict[str, str] = {
    "demo_maternalethnicity___1": "Hispanic/Latino",
    "demo_maternalethnicity___2": "Not Hispanic/Latino",
    "demo_maternalethnicity___3": "Unknown",
    "demo_maternalethnicity___4": "Other",
}

WORKBOOK_EXPORT_COLUMNS = [
    "Study",
    "Record ID",
    "Stakeholder Bucket",
    "Payment Decision",
    "Trust Tier",
    "Reason for Bucket",
    "Hard Rules Triggered",
    "Soft Rules Triggered",
    "All Rules Triggered",
    "Hard Check Violations",
    "Soft Check Violations",
    "Total Rules Fired",
    "Total Survey Time (min)",
    "Attitudes Section Time (min)",
    "Extreme Fast (R1 + R2)",
    "Duplicate Response Pattern",
    "Bursty Submission Timing",
    "Branching / Family Logic Issue",
    "Impossible Demographics",
    "Caregiver Age",
    "Gender",
    "Race",
    "Ethnicity",
    "Status-Quo Cluster",
]

WORKBOOK_GROUP_LABELS: dict[str, str] = {
    "Cleared for payment now": "Pay now",
    "Low-risk provisional approval": "Low-risk approval",
    "Needs human review": "Review",
    "Confirmed bot / reject": "Do not pay",
}

WORKBOOK_GROUP_ORDER = [
    "Pay now",
    "Low-risk approval",
    "Review",
    "Do not pay",
]

WORKBOOK_GROUP_COLORS: dict[str, str] = {
    "Pay now": CATEGORY_COLORS["Real Caregivers"],
    "Low-risk approval": CATEGORY_COLORS["Likely Real Caregivers"],
    "Review": CATEGORY_COLORS["Needs Human Review"],
    "Do not pay": CATEGORY_COLORS["Confirmed Bot"],
}

WORKBOOK_ACTION_LABELS: dict[str, str] = {
    "Pay now": "Send gift card now",
    "Low-risk approval": "Approve after quick check",
    "Review": "Hold for human review",
    "Do not pay": "Do not pay",
}

WORKBOOK_REASON_LABELS: dict[str, str] = {
    "No rule violations": "No issue found",
    "One soft check only": "One mild signal only",
    "Single hard-check flag": "One major issue was found",
    "Multiple hard-check flags": "Multiple major issues were found",
    "Two or more soft-check flags": "Several milder issues were found",
    "Hard logic contradiction with speed flags": "Very strong invalid-response pattern",
}

PAYMENT_READY_COLUMNS = [
    "Study",
    "Record ID",
    "Action group",
    "Action",
    "Why included",
    "Survey time (min)",
    "Attitudes time (min)",
    "Gender",
    "Race",
    "Ethnicity",
]

REVIEW_NEEDED_COLUMNS = [
    "Study",
    "Record ID",
    "Action group",
    "Action",
    "Why flagged",
    "Survey too fast",
    "Attitudes too fast",
    "Both time limits",
    "Bursty",
    "Repeated answers",
    "Family logic",
    "Demographic issue",
    "Survey time (min)",
    "Attitudes time (min)",
]


# ── Data loading ────────────────────────────────────────────────────────────

def load_config(project_dir: Path) -> dict:
    with (project_dir / "config.yaml").open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_master_summary(output_dir: Path) -> pd.DataFrame:
    """Load the pre-computed master summary with 4 categories."""
    df = pd.read_csv(output_dir / "table_43_master_summary.csv")
    return df


def load_detailed_breakdown(output_dir: Path) -> pd.DataFrame:
    """Load the detailed sub-category breakdown."""
    df = pd.read_csv(output_dir / "table_44_detailed_subcategory_breakdown.csv")
    return df


def load_confirmed_bots(output_dir: Path) -> pd.DataFrame:
    """Load confirmed bot records with friendly column names."""
    df = pd.read_csv(output_dir / "table_45_confirmed_bot_records.csv")
    return df.rename(columns=COLUMN_FRIENDLY_NAMES)


def load_record_flags(output_dir: Path) -> pd.DataFrame:
    return pd.read_parquet(output_dir / "record_flags.parquet")


def load_rule_definitions(output_dir: Path) -> pd.DataFrame:
    df = pd.read_csv(output_dir / "table_14_fraud_rule_definitions.csv")
    df["Friendly Name"] = df["rule"].map(
        {k.replace("rule_", ""): v for k, v in RULE_FRIENDLY_NAMES.items()}
    )
    return df


def load_dirty_records(cache_dir: Path) -> pd.DataFrame:
    """Load the dirty 4581 REDCap cache (latest parquet)."""
    return _load_project_records(cache_dir, 4581, "dirty_4581")


def load_record_classification(output_dir: Path) -> pd.DataFrame:
    """Load the row-level stakeholder classification table."""
    df = pd.read_csv(output_dir / "table_41_distinct_record_classification.csv")
    df["record_id"] = df["record_id"].astype(str)
    return df


def _load_project_records(cache_dir: Path, project_id: int, source_project: str) -> pd.DataFrame:
    matches = sorted(cache_dir.glob(f"{project_id}_record_*.parquet"))
    if not matches:
        raise FileNotFoundError(f"No {project_id} record cache found.")
    df = pd.read_parquet(matches[-1]).copy()
    df["record_id"] = df["record_id"].astype(str)
    df["source_project"] = source_project
    return df


def load_combined_records(cache_dir: Path) -> pd.DataFrame:
    """Load the latest REDCap record caches for both studies."""
    return pd.concat(
        [
            _load_project_records(cache_dir, 4797, "clean_4797"),
            _load_project_records(cache_dir, 4581, "dirty_4581"),
        ],
        ignore_index=True,
        sort=False,
    )


# ── Classification helper ───────────────────────────────────────────────────

def classify_records(
    record_flags: pd.DataFrame,
    confirmed_bot_ids: set[str],
) -> pd.DataFrame:
    """Assign each record to one of 4 stakeholder categories.

    Categories:
        - Confirmed Bot (6 identified records)
        - Needs Human Review (Tier 1/2 + ≥2 soft flags)
        - Likely Real Caregivers (Tier 3, one soft flag only)
        - Real Caregivers (Tier 4, no flags)
    """
    df = record_flags.copy()
    df["record_id_str"] = df["record_id"].astype(str)

    conditions = [
        df["record_id_str"].isin(confirmed_bot_ids),
        df["tier"].isin([1, 2]),
        df["tier"] == 3,
        df["tier"] == 4,
    ]
    choices = [
        "Confirmed Bot",
        "Needs Human Review",
        "Likely Real Caregivers",
        "Real Caregivers",
    ]
    df["Category"] = np.select(conditions, choices, default="Needs Human Review")
    df.drop(columns=["record_id_str"], inplace=True)
    return df


# ── Timing helpers ──────────────────────────────────────────────────────────

def compute_timing(
    dirty_records: pd.DataFrame,
    record_flags: pd.DataFrame,
) -> pd.DataFrame:
    """Merge timing data from REDCap cache into the classified flags."""
    time_fields = ["get_time_fif", "get_time_val", "get_time_tfa", "get_time_demo"]
    rec = dirty_records[["record_id"] + time_fields].copy()
    rec["record_id"] = rec["record_id"].astype(str)

    # Convert timing fields to numeric minutes
    for col in time_fields:
        rec[col] = pd.to_numeric(rec[col], errors="coerce")

    rec["Total Survey Time (min)"] = rec[time_fields].sum(axis=1)
    rec["Attitudes Section Time (min)"] = rec["get_time_tfa"]

    flags = record_flags.copy()
    flags["record_id"] = flags["record_id"].astype(str)

    merged = flags.merge(
        rec[["record_id", "Total Survey Time (min)", "Attitudes Section Time (min)"]],
        on="record_id",
        how="left",
    )
    return merged


def _normalise_rule_text(value: object) -> str:
    text = str(value).strip()
    if not text or text.lower() == "none" or text.lower() == "nan":
        return "None"
    return ", ".join(part.strip() for part in text.split(",") if part.strip())


def _yes_no(series: pd.Series) -> pd.Series:
    return np.where(series.fillna(False).astype(bool), "Yes", "No")


def _format_checkbox_selection(
    frame: pd.DataFrame,
    mapping: dict[str, str],
    other_flag: Optional[str] = None,
    other_text_col: Optional[str] = None,
) -> pd.Series:
    def build_value(row: pd.Series) -> str:
        selections: list[str] = []
        for column, label in mapping.items():
            if str(row.get(column, "")).strip() == "1":
                selections.append(label)
        if other_flag and other_text_col and str(row.get(other_flag, "")).strip() == "1":
            other_text = str(row.get(other_text_col, "")).strip()
            if other_text:
                selections = [
                    f"Other: {other_text}" if entry == "Other" else entry
                    for entry in selections
                ]
        return "; ".join(selections) if selections else "Not answered"

    return frame.apply(build_value, axis=1)


def _format_gender(series: pd.Series) -> pd.Series:
    formatted = series.astype("string").str.strip().map(GENDER_LABELS)
    return formatted.fillna("Not answered")


def build_stakeholder_record_view(
    output_dir: Path,
    cache_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """Build a stakeholder-friendly record-level export with timing and demographics."""
    if cache_dir is None:
        cache_dir = output_dir.parent / "data_cache"

    classification = load_record_classification(output_dir)
    record_flags = load_record_flags(output_dir).copy()
    record_flags["record_id"] = record_flags["record_id"].astype(str)
    records = load_combined_records(cache_dir)

    merged = classification.merge(
        record_flags,
        on=["source_project", "project_id", "record_id", "tier", "tier_label"],
        how="left",
        validate="one_to_one",
    ).merge(
        records,
        on=["source_project", "record_id"],
        how="left",
        validate="one_to_one",
    )

    time_fields = ["get_time_fif", "get_time_val", "get_time_tfa", "get_time_demo"]
    for column in time_fields + ["age_check_demo"]:
        merged[column] = pd.to_numeric(merged[column], errors="coerce")

    merged["Total Survey Time (min)"] = merged[time_fields].sum(axis=1, min_count=1).round(2)
    merged["Attitudes Section Time (min)"] = merged["get_time_tfa"].round(2)
    merged["Caregiver Age"] = merged["age_check_demo"].where(
        merged["age_check_demo"].between(18, 100)
    ).round(1)
    merged["Gender"] = _format_gender(merged["demo_gender"])
    merged["Race"] = _format_checkbox_selection(
        merged,
        RACE_CHECKBOX_LABELS,
        other_flag="demo_maternalrace___7",
        other_text_col="demo_race_other",
    )
    merged["Ethnicity"] = _format_checkbox_selection(
        merged,
        ETHNICITY_CHECKBOX_LABELS,
        other_flag="demo_maternalethnicity___4",
        other_text_col="demo_ethnicity_other",
    )
    merged["Stakeholder Bucket"] = merged["classification"].map(
        {
            "Confirmed Bot": "Confirmed bot / reject",
            "Needs Review": "Needs human review",
            "Likely Real Caregiver": "Low-risk provisional approval",
            "Real Caregiver": "Cleared for payment now",
        }
    ).fillna(merged["classification"])
    merged["Record ID"] = merged["record_id"].astype(str)
    merged["Trust Tier"] = merged["tier_label"]
    merged["Payment Decision"] = merged["giftcard_decision"].map(PAYMENT_DECISION_LABELS).fillna(
        merged["giftcard_decision"]
    )
    merged["Reason for Bucket"] = merged["sub_classification"].map(SUBCLASSIFICATION_LABELS).fillna(
        merged["sub_classification"]
    )
    merged["Hard Rules Triggered"] = merged["tier1_triggered"].map(_normalise_rule_text)
    merged["Soft Rules Triggered"] = merged["soft_triggered"].map(_normalise_rule_text)
    merged["All Rules Triggered"] = merged["all_rules_triggered"].map(_normalise_rule_text)
    merged["Hard Check Violations"] = merged["tier1_hit_count"].fillna(0).astype(int)
    merged["Soft Check Violations"] = merged["suspicion_rule_count"].fillna(0).astype(int)
    merged["Total Rules Fired"] = merged["total_rules_fired"].fillna(0).astype(int)
    merged["Full survey too fast"] = _yes_no(merged["rule_R1"])
    merged["Attitudes section too fast"] = _yes_no(merged["rule_R2"])
    merged["Broke both time limits"] = _yes_no(merged["rule_R1"] & merged["rule_R2"])
    merged["Duplicate Response Pattern"] = _yes_no(merged["rule_R5"])
    merged["Bursty Submission Timing"] = _yes_no(merged["rule_R6"])
    merged["Branching / Family Logic Issue"] = _yes_no(merged["rule_R8"])
    merged["Impossible Demographics"] = _yes_no(merged["rule_R9"])
    merged["Repeated answer pattern"] = merged["Duplicate Response Pattern"]
    merged["Bursty submissions"] = merged["Bursty Submission Timing"]
    merged["Family answers did not line up"] = merged["Branching / Family Logic Issue"]
    merged["Demographic answers did not line up"] = merged["Impossible Demographics"]
    merged["Extreme Fast (R1 + R2)"] = _yes_no(merged["rule_R1"] & merged["rule_R2"])
    merged["Status-Quo Cluster"] = merged["cluster_status_quo"].fillna("Not clustered")
    merged["Study"] = merged["source_project"].map(PROJECT_LABELS).fillna(merged["source_project"])
    merged["Category"] = merged["classification"].map(
        {
            "Confirmed Bot": "Confirmed Bot",
            "Needs Review": "Needs Human Review",
            "Likely Real Caregiver": "Likely Real Caregivers",
            "Real Caregiver": "Real Caregivers",
        }
    ).fillna(merged["classification"])

    stakeholder_records = merged[
        WORKBOOK_EXPORT_COLUMNS
        + [
            "Full survey too fast",
            "Attitudes section too fast",
            "Broke both time limits",
            "Repeated answer pattern",
            "Bursty submissions",
            "Family answers did not line up",
            "Demographic answers did not line up",
            "Category",
            "source_project",
            "classification",
        ]
    ].copy()
    stakeholder_records["Record ID"] = stakeholder_records["Record ID"].astype(str)
    return stakeholder_records


def _workflow_counts(stakeholder_records: pd.DataFrame) -> dict[str, int]:
    dirty = stakeholder_records.loc[stakeholder_records["source_project"].eq("dirty_4581")].copy()
    hard_failed = dirty["Trust Tier"].eq("Confirmed invalid")
    confirmed_bots = dirty["classification"].eq("Confirmed Bot")
    review_records = dirty["classification"].eq("Needs Review")
    extreme_fast = dirty["Extreme Fast (R1 + R2)"].eq("Yes")
    other_hard = dirty[[
        "Duplicate Response Pattern",
        "Branching / Family Logic Issue",
        "Impossible Demographics",
    ]].eq("Yes").any(axis=1)

    return {
        "study2_total": int(len(dirty)),
        "hard_failed": int(hard_failed.sum()),
        "hard_review": int((review_records & hard_failed).sum()),
        "confirmed_bots": int(confirmed_bots.sum()),
        "soft_review": int((review_records & ~hard_failed).sum()),
        "low_risk": int(dirty["classification"].eq("Likely Real Caregiver").sum()),
        "cleared": int(dirty["classification"].eq("Real Caregiver").sum()),
        "extreme_fast": int(extreme_fast.sum()),
        "extreme_fast_only_speed": int((extreme_fast & ~other_hard).sum()),
    }


def build_workflow_summary(stakeholder_records: pd.DataFrame) -> pd.DataFrame:
    """Summarise the current Study 2 screening workflow in plain English."""
    counts = _workflow_counts(stakeholder_records)
    rows = [
        {
            "Step": "Study 2 raw responses",
            "Study 2 Count": counts["study2_total"],
            "How to read it": "Starting pool for the legacy 4581 review.",
        },
        {
            "Step": "Hard-check failures (R1, R2, R5, R8, R9)",
            "Study 2 Count": counts["hard_failed"],
            "How to read it": "Records that triggered at least one hard check.",
        },
        {
            "Step": "Needs human review from hard checks",
            "Study 2 Count": counts["hard_review"],
            "How to read it": "Hard-check records that are still reviewable rather than fully rejected.",
        },
        {
            "Step": "Confirmed bots / reject",
            "Study 2 Count": counts["confirmed_bots"],
            "How to read it": "Strongest evidence concentration; do not pay unless adjudication reverses it.",
        },
        {
            "Step": "Soft-check review only (no hard check)",
            "Study 2 Count": counts["soft_review"],
            "How to read it": "Two or more soft checks, but no hard-check trigger.",
        },
        {
            "Step": "Low-risk provisional approval",
            "Study 2 Count": counts["low_risk"],
            "How to read it": "Exactly one soft check and no hard checks.",
        },
        {
            "Step": "Cleared for payment now",
            "Study 2 Count": counts["cleared"],
            "How to read it": "Tier 4 pass with no hard or soft screen trigger.",
        },
        {
            "Step": "Extreme fast: broke both R1 and R2",
            "Study 2 Count": counts["extreme_fast"],
            "How to read it": "Cross-cut subset; these records are inside the hard-check path.",
        },
        {
            "Step": "Extreme fast with no other hard check",
            "Study 2 Count": counts["extreme_fast_only_speed"],
            "How to read it": "Fast on both timing rules, but no duplicate or logic hard-check signal.",
        },
    ]
    return pd.DataFrame(rows)


def build_overview_summary(stakeholder_records: pd.DataFrame) -> pd.DataFrame:
    """Create a concise stakeholder count table across both studies."""
    rows: list[dict[str, object]] = []
    for label, mask, interpretation in [
        (
            "Cleared for payment now",
            stakeholder_records["Payment Decision"].eq("Cleared for payment now"),
            "Tier 4 pass. No screening rule fired.",
        ),
        (
            "Low-risk provisional approval",
            stakeholder_records["Payment Decision"].eq("Low-risk provisional approval"),
            "One soft check only. Provisionally safe but still separate from fully clear records.",
        ),
        (
            "Needs human review",
            stakeholder_records["Payment Decision"].eq("Needs human review"),
            "Manual adjudication queue.",
        ),
        (
            "Confirmed bot / reject",
            stakeholder_records["Payment Decision"].eq("Confirmed bot / reject"),
            "Strongest evidence concentration. Hold payment.",
        ),
        (
            "Extreme fast (R1 + R2)",
            stakeholder_records["Extreme Fast (R1 + R2)"].eq("Yes"),
            "Cross-cut subset of the hard-check path; not a separate payment bucket.",
        ),
    ]:
        subset = stakeholder_records.loc[mask]
        rows.append(
            {
                "Stakeholder Group": label,
                "Study 1 (4797)": int(subset["source_project"].eq("clean_4797").sum()),
                "Study 2 (4581)": int(subset["source_project"].eq("dirty_4581").sum()),
                "All Records": int(len(subset)),
                "Interpretation": interpretation,
            }
        )

    r1r2_only = stakeholder_records.loc[
        stakeholder_records["Extreme Fast (R1 + R2)"].eq("Yes")
        & stakeholder_records[[
            "Duplicate Response Pattern",
            "Branching / Family Logic Issue",
            "Impossible Demographics",
        ]].ne("Yes").all(axis=1)
    ]
    rows.append(
        {
            "Stakeholder Group": "Extreme fast with no other hard check",
            "Study 1 (4797)": int(r1r2_only["source_project"].eq("clean_4797").sum()),
            "Study 2 (4581)": int(r1r2_only["source_project"].eq("dirty_4581").sum()),
            "All Records": int(len(r1r2_only)),
            "Interpretation": "Broke both timing rules, but no duplicate or logic hard-check signal.",
        }
    )

    return pd.DataFrame(rows)


def format_workflow_pipeline(stakeholder_records: pd.DataFrame) -> str:
    """Render a compact ASCII workflow diagram with live counts."""
    counts = _workflow_counts(stakeholder_records)
    return "\n".join(
        [
            "┌──────────────────────────────────────────────┐",
            f"│ {counts['study2_total']:>4} Study 2 responses (Project 4581)     │",
            "└──────────────────────┬───────────────────────┘",
            "                       │",
            "                       ▼",
            "┌──────────────────────────────────────────────┐",
            f"│ Hard checks: R1, R2, R5, R8, R9  -> {counts['hard_failed']:>4} │",
            "└───────────────┬──────────────────┬───────────┘",
            "                │                  │",
            "                ▼                  ▼",
            f"      Needs review from hard   Confirmed bot / reject",
            f"      checks: {counts['hard_review']:>4}            {counts['confirmed_bots']:>4}",
            "",
            "┌──────────────────────────────────────────────┐",
            "│ No hard check -> apply soft checks (R3, R4,  │",
            "│ R6, R7)                                      │",
            "└───────────────┬──────────────────┬───────────┘",
            "                │                  │",
            "                ▼                  ▼",
            f"      Needs human review      Low-risk provisional",
            f"      (>=2 soft flags): {counts['soft_review']:>4}     approval: {counts['low_risk']:>4}",
            "",
            f"Cleared for payment now (Tier 4 pass): {counts['cleared']}",
            f"Cross-cut timing note: {counts['extreme_fast']} broke both R1 and R2; {counts['extreme_fast_only_speed']} had no other hard check.",
        ]
    )


def build_demographic_comparison(stakeholder_records: pd.DataFrame) -> pd.DataFrame:
    """Compare the 223 fully cleared records with the 1,048 review records."""
    compare = stakeholder_records.loc[
        stakeholder_records["Payment Decision"].isin(
            ["Cleared for payment now", "Needs human review"]
        )
    ].copy()
    group_map = {
        "Cleared for payment now": "Cleared for payment (223)",
        "Needs human review": "Needs human review (1048)",
    }
    compare["Comparison Group"] = compare["Payment Decision"].map(group_map)

    def share_row(metric: str, mask: pd.Series) -> dict[str, object]:
        valid = compare.loc[mask.index]
        rows: dict[str, object] = {"Metric": metric}
        pct_values: dict[str, float] = {}
        for group in group_map.values():
            subset = valid.loc[valid["Comparison Group"].eq(group)]
            denom = int(len(subset))
            num = int(mask.loc[subset.index].sum())
            pct = (num / denom * 100) if denom else np.nan
            pct_values[group] = pct
            rows[group] = f"{num}/{denom} ({pct:.1f}%)" if denom else "0/0"
        rows["Difference"] = (
            f"{pct_values[group_map['Cleared for payment now']] - pct_values[group_map['Needs human review']]:.1f} pp"
        )
        return rows

    cleared_label = group_map["Cleared for payment now"]
    review_label = group_map["Needs human review"]
    cleared_age = compare.loc[compare["Comparison Group"].eq(cleared_label), "Caregiver Age"].dropna()
    review_age = compare.loc[compare["Comparison Group"].eq(review_label), "Caregiver Age"].dropna()
    cleared_total = int(compare["Comparison Group"].eq(cleared_label).sum())
    review_total = int(compare["Comparison Group"].eq(review_label).sum())
    rows: list[dict[str, object]] = [
        {
            "Metric": "Caregiver age available",
            cleared_label: f"{len(cleared_age)}/{cleared_total} records",
            review_label: f"{len(review_age)}/{review_total} records",
            "Difference": "Study 2 age comparison is not interpretable from the current cache.",
        }
    ]

    rows.extend(
        [
            share_row("Women", compare["Gender"].eq("Woman")),
            share_row("Hispanic/Latino", compare["Ethnicity"].str.contains("Hispanic/Latino", na=False)),
            share_row("White", compare["Race"].str.contains("White", na=False)),
            share_row(
                "Black or African American",
                compare["Race"].str.contains("Black or African American", na=False),
            ),
            share_row(
                "American Indian/Alaska Native",
                compare["Race"].str.contains("American Indian/Alaska Native", na=False),
            ),
        ]
    )

    return pd.DataFrame(rows)


def build_stakeholder_views(
    output_dir: Path,
    cache_dir: Optional[Path] = None,
) -> dict[str, object]:
    """Load the stakeholder-ready tables, records, and derived summaries."""
    stakeholder_records = build_stakeholder_record_view(output_dir, cache_dir)
    master_summary = load_master_summary(output_dir)
    detailed_breakdown = load_detailed_breakdown(output_dir)
    confirmed_bots = load_confirmed_bots(output_dir)
    record_flags = load_record_flags(output_dir)

    return {
        "master_summary": master_summary,
        "detailed_breakdown": detailed_breakdown,
        "confirmed_bots": confirmed_bots,
        "record_flags": record_flags,
        "stakeholder_records": stakeholder_records,
        "cleared_records": stakeholder_records.loc[
            stakeholder_records["Payment Decision"].eq("Cleared for payment now")
        ].copy(),
        "low_risk_records": stakeholder_records.loc[
            stakeholder_records["Payment Decision"].eq("Low-risk provisional approval")
        ].copy(),
        "review_records": stakeholder_records.loc[
            stakeholder_records["Payment Decision"].eq("Needs human review")
        ].copy(),
        "confirmed_bot_records": stakeholder_records.loc[
            stakeholder_records["Payment Decision"].eq("Confirmed bot / reject")
        ].copy(),
        "extreme_fast_records": stakeholder_records.loc[
            stakeholder_records["Extreme Fast (R1 + R2)"].eq("Yes")
        ].copy(),
        "overview_summary": build_overview_summary(stakeholder_records),
        "workflow_summary": build_workflow_summary(stakeholder_records),
        "workflow_diagram": format_workflow_pipeline(stakeholder_records),
        "demographic_comparison": build_demographic_comparison(stakeholder_records),
        "study_action_summary": build_workbook_study_action_summary(stakeholder_records),
        "review_signal_summary": build_workbook_review_signal_summary(stakeholder_records),
        "flag_strength_summary": build_workbook_flag_strength_summary(stakeholder_records),
    }


def build_compact_workbook_records(stakeholder_records: pd.DataFrame) -> pd.DataFrame:
    """Create a simplified record table for the workbook export."""
    df = stakeholder_records.copy()
    df["Action group"] = df["Payment Decision"].map(WORKBOOK_GROUP_LABELS).fillna(
        df["Payment Decision"]
    )
    df["Action"] = df["Action group"].map(WORKBOOK_ACTION_LABELS).fillna(
        df["Action group"]
    )
    short_reason = df["Reason for Bucket"].map(WORKBOOK_REASON_LABELS).fillna(df["Reason for Bucket"])
    df["Why included"] = short_reason
    df["Why flagged"] = short_reason
    df["Survey too fast"] = df["Full survey too fast"]
    df["Attitudes too fast"] = df["Attitudes section too fast"]
    df["Both time limits"] = df["Broke both time limits"]
    df["Bursty"] = df["Bursty submissions"]
    df["Repeated answers"] = df["Repeated answer pattern"]
    df["Family logic"] = df["Family answers did not line up"]
    df["Demographic issue"] = df["Demographic answers did not line up"]
    df["Survey time (min)"] = df["Total Survey Time (min)"]
    df["Attitudes time (min)"] = df["Attitudes Section Time (min)"]
    return df


def build_workbook_action_summary(stakeholder_records: pd.DataFrame) -> pd.DataFrame:
    """Summarize the four stakeholder action groups in plain language."""
    compact = build_compact_workbook_records(stakeholder_records)
    total_records = len(compact)
    rows = []
    for group in WORKBOOK_GROUP_ORDER:
        subset = compact.loc[compact["Action group"].eq(group)]
        rows.append(
            {
                "Action group": group,
                "Records": int(len(subset)),
                "Share of all responses": f"{len(subset) / total_records * 100:.1f}%",
                "What to do": WORKBOOK_ACTION_LABELS[group],
            }
        )
    return pd.DataFrame(rows)


def build_workbook_study_action_summary(stakeholder_records: pd.DataFrame) -> pd.DataFrame:
    """Show how the four action groups split within each study."""
    compact = build_compact_workbook_records(stakeholder_records)
    rows = []
    for study in [PROJECT_LABELS["clean_4797"], PROJECT_LABELS["dirty_4581"]]:
        study_records = compact.loc[compact["Study"].eq(study)].copy()
        total = len(study_records)
        for group in WORKBOOK_GROUP_ORDER:
            group_records = study_records.loc[study_records["Action group"].eq(group)]
            rows.append(
                {
                    "Study": study,
                    "Action group": group,
                    "Records": int(len(group_records)),
                    "Share within study (%)": round(len(group_records) / total * 100, 1) if total else 0.0,
                    "What to do": WORKBOOK_ACTION_LABELS[group],
                }
            )
    return pd.DataFrame(rows)


def build_workbook_key_metrics(stakeholder_records: pd.DataFrame) -> pd.DataFrame:
    """Return a concise set of meeting-ready metrics."""
    counts = _workflow_counts(stakeholder_records)
    total_records = len(stakeholder_records)
    review_total = int(
        stakeholder_records["Payment Decision"].isin(
            ["Needs human review", "Confirmed bot / reject"]
        ).sum()
    )
    rows = [
        {
            "Metric": "All responses reviewed",
            "Value": f"{total_records:,}",
            "Why it matters": "Both studies combined.",
        },
        {
            "Metric": "Study 2 responses",
            "Value": f"{counts['study2_total']:,}",
            "Why it matters": "Main focus of the stakeholder bot review.",
        },
        {
            "Metric": "Need manual review or do not pay",
            "Value": f"{review_total:,}",
            "Why it matters": "Highest-priority records for next-step review.",
        },
        {
            "Metric": "Broke both time limits",
            "Value": f"{counts['extreme_fast']:,}",
            "Why it matters": "Records under both speed cutoffs.",
        },
        {
            "Metric": "Broke both time limits only",
            "Value": f"{counts['extreme_fast_only_speed']:,}",
            "Why it matters": "These 276 had no other major issue flagged.",
        },
    ]
    return pd.DataFrame(rows)


def build_workbook_timing_summary(stakeholder_records: pd.DataFrame) -> pd.DataFrame:
    """Summarize Study 2 timing by decision group."""
    compact = build_compact_workbook_records(stakeholder_records)
    study2 = compact.loc[compact["source_project"].eq("dirty_4581")].copy()

    rows = []
    for group in WORKBOOK_GROUP_ORDER:
        subset = study2.loc[study2["Action group"].eq(group)]
        total_time = subset["Survey time (min)"].dropna()
        attitudes_time = subset["Attitudes time (min)"].dropna()
        rows.append(
            {
                "Action group": group,
                "Study 2 records": int(len(subset)),
                "Median full survey time": f"{total_time.median():.2f} min" if len(total_time) else "n/a",
                "Median attitudes time": f"{attitudes_time.median():.2f} min" if len(attitudes_time) else "n/a",
            }
        )
    return pd.DataFrame(rows)


def build_workbook_issue_summary(stakeholder_records: pd.DataFrame) -> pd.DataFrame:
    """Count the most visible Study 2 issues in plain language."""
    compact = build_compact_workbook_records(stakeholder_records)
    study2 = compact.loc[compact["source_project"].eq("dirty_4581")].copy()
    total = len(study2)

    issue_specs = [
        ("Full survey too fast", "Full survey too fast"),
        ("Attitudes section too fast", "Attitudes section too fast"),
        ("Broke both time limits", "Broke both time limits"),
        ("Bursty submissions", "Bursty submissions"),
        ("Repeated answer pattern", "Repeated answer pattern"),
        ("Family answers did not line up", "Family answers did not line up"),
        ("Demographic answers did not line up", "Demographic answers did not line up"),
    ]

    rows = []
    for label, column in issue_specs:
        count = int(study2[column].eq("Yes").sum())
        rows.append(
            {
                "Issue seen in Study 2": label,
                "Records": count,
                "Share of Study 2": f"{count / total * 100:.1f}%",
            }
        )
    return pd.DataFrame(rows)


def build_workbook_review_signal_summary(stakeholder_records: pd.DataFrame) -> pd.DataFrame:
    """Summarize the clearest signals inside the Study 2 review queue."""
    compact = build_compact_workbook_records(stakeholder_records)
    review_queue = compact.loc[
        compact["source_project"].eq("dirty_4581")
        & compact["Action group"].isin(["Review", "Do not pay"])
    ].copy()
    total = len(review_queue)

    signal_specs = [
        (
            "Bursty timing",
            "Bursty",
            "Common background signal; useful when it appears with other issues.",
        ),
        (
            "Attitudes section too fast",
            "Attitudes too fast",
            "Shows where speed problems are most common in the questionnaire.",
        ),
        (
            "Full survey too fast",
            "Survey too fast",
            "Signals unusually short full-survey completion.",
        ),
        (
            "Broke both timing limits",
            "Both time limits",
            "Stronger timing concern because both cutoffs were crossed.",
        ),
        (
            "Family logic did not line up",
            "Family logic",
            "Less common, but more substantive than timing alone.",
        ),
    ]

    rows = []
    for label, column, interpretation in signal_specs:
        count = int(review_queue[column].eq("Yes").sum())
        if count == 0:
            continue
        rows.append(
            {
                "Signal in Study 2 review queue": label,
                "Records": count,
                "Share of review queue (%)": round(count / total * 100, 1) if total else 0.0,
                "How to read it": interpretation,
            }
        )
    return pd.DataFrame(rows)


def build_workbook_flag_strength_summary(stakeholder_records: pd.DataFrame) -> pd.DataFrame:
    """Group flagged Study 2 records by how many major issues they carry."""
    compact = build_compact_workbook_records(stakeholder_records)
    review_queue = compact.loc[
        compact["source_project"].eq("dirty_4581")
        & compact["Action group"].isin(["Review", "Do not pay"])
    ].copy()
    total = len(review_queue)

    rows = []
    for label, mask, interpretation in [
        (
            "Soft-only review",
            review_queue["Hard Check Violations"].eq(0),
            "No major issue; these records entered review because softer signals piled up.",
        ),
        (
            "One major issue",
            review_queue["Hard Check Violations"].eq(1),
            "One strong signal is present, but not enough for an outright rejection.",
        ),
        (
            "Two major issues",
            review_queue["Hard Check Violations"].eq(2),
            "Two strong signals on the same record; this is the clearest review cluster.",
        ),
        (
            "Three or more major issues",
            review_queue["Hard Check Violations"].ge(3),
            "Very concentrated concern; this is the most severe tail of the queue.",
        ),
    ]:
        count = int(mask.sum())
        rows.append(
            {
                "Flag pattern in Study 2 queue": label,
                "Records": count,
                "Share of review queue (%)": round(count / total * 100, 1) if total else 0.0,
                "How to read it": interpretation,
            }
        )
    return pd.DataFrame(rows)


def build_workbook_decision_guide(stakeholder_records: pd.DataFrame) -> pd.DataFrame:
    """Explain the four workbook decision groups without jargon."""
    compact = build_compact_workbook_records(stakeholder_records)
    rows = []
    for group in WORKBOOK_GROUP_ORDER:
        count = int(compact["Action group"].eq(group).sum())
        explanation = {
            "Pay now": "No problem was found by the screen.",
            "Low-risk approval": "Only one softer issue was found.",
            "Review": "A reviewer should check the record before payment.",
            "Do not pay": "Strongest evidence of invalid participation.",
        }[group]
        rows.append(
            {
                "Action group": group,
                "Current records": count,
                "What it means": explanation,
            }
        )
    return pd.DataFrame(rows)


def build_workbook_workflow_summary(stakeholder_records: pd.DataFrame) -> pd.DataFrame:
    """Condense the workflow into a short stakeholder table."""
    counts = _workflow_counts(stakeholder_records)
    rows = [
        {
            "Step": "Study 2 responses received",
            "Count": counts["study2_total"],
            "What it means": "Starting pool for the main bot review.",
        },
        {
            "Step": "Major issue found",
            "Count": counts["hard_failed"],
            "What it means": "At least one strong timing, duplicate, or logic concern.",
        },
        {
            "Step": "Do not pay",
            "Count": counts["confirmed_bots"],
            "What it means": "Strongest evidence concentration.",
        },
        {
            "Step": "Manual review",
            "Count": counts["hard_review"] + counts["soft_review"],
            "What it means": "Hold for human review before payment.",
        },
        {
            "Step": "Low-risk approval",
            "Count": counts["low_risk"],
            "What it means": "One softer issue, but no major issue.",
        },
        {
            "Step": "Pay now",
            "Count": counts["cleared"],
            "What it means": "No issue found in Study 2.",
        },
    ]
    return pd.DataFrame(rows)


def build_workbook_demographic_snapshot(stakeholder_records: pd.DataFrame) -> pd.DataFrame:
    """Return a small, plain-language demographic comparison table."""
    demo = build_demographic_comparison(stakeholder_records).copy()
    demo.columns = [
        "Demographic signal",
        "Cleared now (223)",
        "Review queue (1048)",
        "Difference",
    ]
    return demo


def build_payment_ready_sheet(stakeholder_records: pd.DataFrame) -> pd.DataFrame:
    """Create the compact payment-ready record sheet."""
    compact = build_compact_workbook_records(stakeholder_records)
    payment_ready = compact.loc[
        compact["Action group"].isin(["Pay now", "Low-risk approval"]),
        PAYMENT_READY_COLUMNS,
    ].copy()
    payment_ready["Action group"] = pd.Categorical(
        payment_ready["Action group"],
        categories=["Pay now", "Low-risk approval"],
        ordered=True,
    )
    payment_ready = payment_ready.sort_values(["Action group", "Study", "Record ID"])
    payment_ready["Action group"] = payment_ready["Action group"].astype(str)
    return payment_ready


def build_review_needed_sheet(stakeholder_records: pd.DataFrame) -> pd.DataFrame:
    """Create the compact review-focused record sheet."""
    compact = build_compact_workbook_records(stakeholder_records)
    review_needed = compact.loc[
        compact["Action group"].isin(["Review", "Do not pay"]),
        REVIEW_NEEDED_COLUMNS,
    ].copy()
    review_needed["Action group"] = pd.Categorical(
        review_needed["Action group"],
        categories=["Do not pay", "Review"],
        ordered=True,
    )
    review_needed = review_needed.sort_values(["Action group", "Study", "Record ID"])
    review_needed["Action group"] = review_needed["Action group"].astype(str)
    return review_needed


# ── Plotting functions ──────────────────────────────────────────────────────

def _apply_stakeholder_style(ax: plt.Axes) -> None:
    """Apply clean, large-label formatting for stakeholder presentation."""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelsize=11)
    ax.xaxis.label.set_size(12)
    ax.yaxis.label.set_size(12)
    ax.title.set_size(14)
    ax.title.set_weight("bold")


def plot_category_summary(master_summary: pd.DataFrame, ax: Optional[plt.Axes] = None) -> plt.Figure:
    """Simple bar chart showing record count per category."""
    # Exclude the Total row
    df = master_summary[master_summary["classification"] != "Total"].copy()

    # Map to ordered categories
    cat_map = {
        "Confirmed Bots": "Confirmed Bot",
        "Needs Human Review": "Needs Human Review",
        "Likely Real Caregivers": "Likely Real Caregivers",
        "Real Caregivers": "Real Caregivers",
    }
    df["cat"] = df["classification"].map(cat_map).fillna(df["classification"])

    if ax is None:
        fig, ax = plt.subplots(figsize=(10, 5))
    else:
        fig = ax.get_figure()

    # Plot bars
    colors = [CATEGORY_COLORS.get(c, "#6B7280") for c in df["cat"]]
    bars = ax.barh(df["classification"], df["total_n"], color=colors, edgecolor="white", linewidth=0.5)

    # Add count labels
    for bar, val in zip(bars, df["total_n"]):
        ax.text(bar.get_width() + 8, bar.get_y() + bar.get_height() / 2,
                f"{val:,}", va="center", fontsize=12, fontweight="bold")

    ax.set_xlabel("Number of Records")
    ax.set_title("Record Classification Summary (All Projects)")
    ax.invert_yaxis()
    _apply_stakeholder_style(ax)
    fig.tight_layout()
    return fig


def plot_completion_time_histogram(
    timed_flags: pd.DataFrame,
    time_col: str = "Total Survey Time (min)",
    threshold_min: float = 11.57,
    title: str = "Survey Completion Time Distribution",
    ax: Optional[plt.Axes] = None,
) -> plt.Figure:
    """Histogram of completion times, colour-coded by category."""
    df = timed_flags.dropna(subset=[time_col]).copy()
    # Cap at reasonable range for visibility
    df = df[df[time_col] <= 120]

    if ax is None:
        fig, ax = plt.subplots(figsize=(11, 5))
    else:
        fig = ax.get_figure()

    for cat in CATEGORY_ORDER:
        subset = df[df["Category"] == cat]
        if len(subset) == 0:
            continue
        ax.hist(
            subset[time_col],
            bins=50,
            alpha=0.65,
            label=f"{cat} (n={len(subset)})",
            color=CATEGORY_COLORS.get(cat, "#6B7280"),
            edgecolor="white",
            linewidth=0.3,
        )

    ax.axvline(threshold_min, color="#DC2626", linestyle="--", linewidth=2,
               label=f"Threshold: {threshold_min} min")
    ax.set_xlabel("Time (minutes)")
    ax.set_ylabel("Number of Records")
    ax.set_title(title)
    ax.legend(frameon=False, fontsize=10)
    _apply_stakeholder_style(ax)
    fig.tight_layout()
    return fig


def plot_rule_violation_frequency(
    record_flags: pd.DataFrame,
    project_filter: Optional[str] = None,
    ax: Optional[plt.Axes] = None,
) -> plt.Figure:
    """Horizontal bar chart: which rules fire most often."""
    df = record_flags.copy()
    if project_filter:
        df = df[df["source_project"] == project_filter]

    rule_cols = [c for c in df.columns if c.startswith("rule_R")]
    counts = df[rule_cols].sum().sort_values(ascending=True)
    labels = [RULE_FRIENDLY_NAMES.get(r, r) for r in counts.index]

    if ax is None:
        fig, ax = plt.subplots(figsize=(10, 5.5))
    else:
        fig = ax.get_figure()

    bars = ax.barh(labels, counts.values, color="#1F5A7A", edgecolor="white", linewidth=0.5)
    for bar, val in zip(bars, counts.values):
        ax.text(bar.get_width() + 5, bar.get_y() + bar.get_height() / 2,
                str(int(val)), va="center", fontsize=11)

    project_label = f" ({project_filter})" if project_filter else ""
    ax.set_xlabel("Number of Records Flagged")
    ax.set_title(f"Rule Violation Frequency{project_label}")
    _apply_stakeholder_style(ax)
    fig.tight_layout()
    return fig


def plot_tier_distribution(
    record_flags: pd.DataFrame,
    ax: Optional[plt.Axes] = None,
) -> plt.Figure:
    """Stacked horizontal bar: tier breakdown per project."""
    tier_order = ["Confirmed invalid", "High suspicion", "Uncertain", "Pass"]
    tier_colors = {
        "Confirmed invalid": "#A85D75",
        "High suspicion": "#C67C2D",
        "Uncertain": "#B08A2E",
        "Pass": "#1F5A7A",
    }

    pivot = (
        record_flags.groupby(["source_project", "tier_label"])
        .size()
        .reset_index(name="n")
        .pivot(index="source_project", columns="tier_label", values="n")
        .fillna(0)
    )
    # Normalise to percentages
    pivot_pct = pivot.div(pivot.sum(axis=1), axis=0) * 100
    pivot_pct = pivot_pct.reindex(columns=tier_order, fill_value=0)

    if ax is None:
        fig, ax = plt.subplots(figsize=(10, 4))
    else:
        fig = ax.get_figure()

    left = pd.Series(0.0, index=pivot_pct.index)
    for label in tier_order:
        ax.barh(pivot_pct.index, pivot_pct[label], left=left,
                color=tier_colors[label], label=label, edgecolor="white", linewidth=0.5)
        left += pivot_pct[label]

    ax.set_xlabel("% of Records")
    ax.set_title("Trust Tier Distribution by Project")
    ax.legend(frameon=False, loc="lower right", fontsize=10)
    _apply_stakeholder_style(ax)
    fig.tight_layout()
    return fig


def plot_detailed_breakdown(
    detailed: pd.DataFrame,
    ax: Optional[plt.Axes] = None,
) -> plt.Figure:
    """Horizontal bar chart of the detailed sub-category breakdown."""
    df = detailed[detailed["classification"] != "Total"].copy()

    if ax is None:
        fig, ax = plt.subplots(figsize=(12, 6))
    else:
        fig = ax.get_figure()

    # Build colour list
    cat_colour_map = {
        "Real Caregiver": "#1F5A7A",
        "Likely Real": "#B08A2E",
        "Needs Review": "#C67C2D",
        "Confirmed Bot": "#A85D75",
    }
    colors = [cat_colour_map.get(c, "#6B7280") for c in df["classification"]]

    bars = ax.barh(df["sub_category_reason"], df["total_n"], color=colors,
                   edgecolor="white", linewidth=0.5)
    for bar, val in zip(bars, df["total_n"]):
        ax.text(bar.get_width() + 5, bar.get_y() + bar.get_height() / 2,
                str(int(val)), va="center", fontsize=10)

    ax.set_xlabel("Number of Records")
    ax.set_title("Detailed Record Breakdown by Sub-Category")
    ax.invert_yaxis()
    _apply_stakeholder_style(ax)
    fig.tight_layout()
    return fig


def plot_rule_overlap_heatmap(
    record_flags: pd.DataFrame,
    project_filter: str = "dirty_4581",
    ax: Optional[plt.Axes] = None,
) -> plt.Figure:
    """Simple co-violation heatmap for the hard-check rules."""
    df = record_flags.loc[record_flags["source_project"].eq(project_filter)].copy()
    rule_cols = ["rule_R1", "rule_R2", "rule_R5", "rule_R8", "rule_R9"]
    matrix = df[rule_cols].astype(int).T @ df[rule_cols].astype(int)
    matrix.index = [RULE_FRIENDLY_NAMES[col] for col in matrix.index]
    matrix.columns = [RULE_FRIENDLY_NAMES[col] for col in matrix.columns]

    if ax is None:
        fig, ax = plt.subplots(figsize=(9.8, 7.0))
    else:
        fig = ax.get_figure()

    sns.heatmap(
        matrix,
        annot=True,
        fmt="d",
        cmap="Blues",
        linewidths=0.5,
        square=True,
        cbar_kws={"label": "Co-flagged records"},
        ax=ax,
    )
    ax.set_title("Hard-check overlap in Study 2")
    _apply_stakeholder_style(ax)
    fig.tight_layout()
    return fig


def plot_clear_vs_review_demographics(
    stakeholder_records: pd.DataFrame,
    axes: Optional[tuple[plt.Axes, plt.Axes]] = None,
) -> plt.Figure:
    """Compare cleared-for-payment records with the human-review queue."""
    compare = stakeholder_records.loc[
        stakeholder_records["Payment Decision"].isin(
            ["Cleared for payment now", "Needs human review"]
        )
    ].copy()
    compare["Comparison Group"] = compare["Payment Decision"].map(
        {
            "Cleared for payment now": "Cleared for payment",
            "Needs human review": "Needs human review",
        }
    )
    cleared_group = "Cleared for payment"
    review_group = "Needs human review"

    if axes is None:
        fig, axes = plt.subplots(1, 2, figsize=(15, 5.5))
    else:
        fig = axes[0].get_figure()

    age_ax, share_ax = axes
    age_counts = pd.DataFrame(
        [
            {
                "Comparison Group": cleared_group,
                "Records with usable age": int(
                    compare.loc[compare["Comparison Group"].eq(cleared_group), "Caregiver Age"].notna().sum()
                ),
                "Total records": int(compare["Comparison Group"].eq(cleared_group).sum()),
            },
            {
                "Comparison Group": review_group,
                "Records with usable age": int(
                    compare.loc[compare["Comparison Group"].eq(review_group), "Caregiver Age"].notna().sum()
                ),
                "Total records": int(compare["Comparison Group"].eq(review_group).sum()),
            },
        ]
    )
    sns.barplot(
        data=age_counts,
        x="Comparison Group",
        y="Records with usable age",
        hue="Comparison Group",
        order=[cleared_group, review_group],
        palette={
            cleared_group: CATEGORY_COLORS["Real Caregivers"],
            review_group: CATEGORY_COLORS["Needs Human Review"],
        },
        dodge=False,
        ax=age_ax,
    )
    if age_ax.legend_ is not None:
        age_ax.legend_.remove()
    for index, row in age_counts.iterrows():
        age_ax.text(
            index,
            row["Records with usable age"] + max(age_counts["Records with usable age"].max() * 0.03, 1),
            f"{int(row['Records with usable age'])}/{int(row['Total records'])}",
            ha="center",
            fontsize=10,
        )
    age_ax.text(
        0.5,
        0.96,
        "Study 2 currently lacks usable caregiver-age data,\nso this panel shows age coverage instead of age distribution.",
        ha="center",
        va="top",
        transform=age_ax.transAxes,
        fontsize=10,
    )
    age_ax.set_title("Caregiver age coverage")
    age_ax.set_xlabel("")
    age_ax.set_ylabel("Records with usable age")
    _apply_stakeholder_style(age_ax)

    share_rows: list[dict[str, object]] = []
    for metric, mask in [
        ("Women", compare["Gender"].eq("Woman")),
        ("Hispanic/Latino", compare["Ethnicity"].str.contains("Hispanic/Latino", na=False)),
        ("White", compare["Race"].str.contains("White", na=False)),
        (
            "Black or African American",
            compare["Race"].str.contains("Black or African American", na=False),
        ),
        (
            "American Indian/Alaska Native",
            compare["Race"].str.contains("American Indian/Alaska Native", na=False),
        ),
    ]:
        for group in [cleared_group, review_group]:
            subset = compare.loc[compare["Comparison Group"].eq(group)]
            pct = mask.loc[subset.index].mean() * 100 if len(subset) else np.nan
            share_rows.append({"Metric": metric, "Group": group, "Percent": pct})

    share_df = pd.DataFrame(share_rows)
    sns.barplot(
        data=share_df,
        x="Percent",
        y="Metric",
        hue="Group",
        palette={
            cleared_group: CATEGORY_COLORS["Real Caregivers"],
            review_group: CATEGORY_COLORS["Needs Human Review"],
        },
        ax=share_ax,
    )
    share_ax.set_title("Demographic shares: cleared vs review")
    share_ax.set_xlabel("Percent of records in each group")
    share_ax.set_ylabel("")
    share_ax.legend(frameon=False)
    _apply_stakeholder_style(share_ax)
    fig.tight_layout()
    return fig


def plot_workbook_action_summary(
    stakeholder_records: pd.DataFrame,
    ax: Optional[plt.Axes] = None,
) -> plt.Figure:
    """Bar chart of the four decision groups for the workbook dashboard."""
    summary = build_workbook_action_summary(stakeholder_records)
    if ax is None:
        fig, ax = plt.subplots(figsize=(7.5, 4.8))
    else:
        fig = ax.get_figure()

    bars = ax.barh(
        summary["Action group"],
        summary["Records"],
        color=[WORKBOOK_GROUP_COLORS[group] for group in summary["Action group"]],
        edgecolor="white",
        linewidth=0.5,
    )
    for bar, value in zip(bars, summary["Records"]):
        ax.text(
            bar.get_width() + max(summary["Records"]) * 0.01,
            bar.get_y() + bar.get_height() / 2,
            f"{int(value):,}",
            va="center",
            fontsize=10,
            fontweight="bold",
        )
    ax.set_xlabel("Records")
    ax.set_title("How the records are grouped")
    ax.invert_yaxis()
    _apply_stakeholder_style(ax)
    fig.tight_layout()
    return fig


def plot_workbook_study_action_comparison(
    stakeholder_records: pd.DataFrame,
    ax: Optional[plt.Axes] = None,
) -> plt.Figure:
    """Compare the four action groups across Study 1 and Study 2."""
    summary = build_workbook_study_action_summary(stakeholder_records)

    if ax is None:
        fig, ax = plt.subplots(figsize=(10.5, 5.2))
    else:
        fig = ax.get_figure()

    sns.barplot(
        data=summary,
        x="Study",
        y="Records",
        hue="Action group",
        order=[PROJECT_LABELS["clean_4797"], PROJECT_LABELS["dirty_4581"]],
        hue_order=WORKBOOK_GROUP_ORDER,
        palette=WORKBOOK_GROUP_COLORS,
        ax=ax,
    )
    for container in ax.containers:
        labels = [f"{int(bar.get_height()):,}" if bar.get_height() > 0 else "" for bar in container]
        ax.bar_label(container, labels=labels, padding=3, fontsize=9)

    ax.set_title("How action groups differ by study")
    ax.set_xlabel("")
    ax.set_ylabel("Records")
    ax.legend(frameon=False, title="")
    _apply_stakeholder_style(ax)
    fig.tight_layout()
    return fig


def plot_workbook_timing_histogram(
    stakeholder_records: pd.DataFrame,
    time_col: str,
    threshold_min: float,
    title: str,
    ax: Optional[plt.Axes] = None,
) -> plt.Figure:
    """Histogram of Study 2 timing using workbook-friendly action groups."""
    compact = build_compact_workbook_records(stakeholder_records)
    study2 = compact.loc[compact["source_project"].eq("dirty_4581")].copy()
    study2 = study2.dropna(subset=[time_col])
    study2 = study2.loc[study2[time_col] <= 120].copy()

    if ax is None:
        fig, ax = plt.subplots(figsize=(7.5, 4.8))
    else:
        fig = ax.get_figure()

    for group in WORKBOOK_GROUP_ORDER:
        subset = study2.loc[study2["Action group"].eq(group), time_col].dropna()
        if len(subset) == 0:
            continue
        ax.hist(
            subset,
            bins=30,
            alpha=0.65,
            label=f"{group} (n={len(subset)})",
            color=WORKBOOK_GROUP_COLORS[group],
            edgecolor="white",
            linewidth=0.3,
        )
    ax.axvline(
        threshold_min,
        color="#DC2626",
        linestyle="--",
        linewidth=2,
        label=f"Time limit: {threshold_min} min",
    )
    ax.set_xlabel("Minutes")
    ax.set_ylabel("Records")
    ax.set_title(title)
    ax.legend(frameon=False, fontsize=8)
    _apply_stakeholder_style(ax)
    fig.tight_layout()
    return fig


def plot_workbook_review_signal_summary(
    stakeholder_records: pd.DataFrame,
    ax: Optional[plt.Axes] = None,
) -> plt.Figure:
    """Show the most common signals inside the Study 2 review queue."""
    summary = build_workbook_review_signal_summary(stakeholder_records)

    if ax is None:
        fig, ax = plt.subplots(figsize=(9.0, 4.8))
    else:
        fig = ax.get_figure()

    colors = sns.color_palette("blend:#DCEAF2,#1F5A7A", n_colors=len(summary))
    bars = ax.barh(
        summary["Signal in Study 2 review queue"],
        summary["Share of review queue (%)"],
        color=colors,
        edgecolor="white",
        linewidth=0.5,
    )
    for bar, count, pct in zip(bars, summary["Records"], summary["Share of review queue (%)"]):
        ax.text(
            bar.get_width() + 1,
            bar.get_y() + bar.get_height() / 2,
            f"{int(count):,} ({pct:.1f}%)",
            va="center",
            fontsize=10,
        )

    ax.set_xlabel("Percent of Study 2 review + reject queue")
    ax.set_ylabel("")
    ax.set_title("What shows up most often in the flagged queue")
    ax.invert_yaxis()
    _apply_stakeholder_style(ax)
    fig.tight_layout()
    return fig


def plot_workbook_timing_comparison(
    stakeholder_records: pd.DataFrame,
    axes: Optional[tuple[plt.Axes, plt.Axes]] = None,
) -> plt.Figure:
    """Compare Study 2 timing for payment-ready versus flagged records."""
    compact = build_compact_workbook_records(stakeholder_records)
    study2 = compact.loc[compact["source_project"].eq("dirty_4581")].copy()
    study2["Timing comparison group"] = study2["Action group"].map(
        {
            "Pay now": "Payment-ready",
            "Low-risk approval": "Payment-ready",
            "Review": "Review or reject",
            "Do not pay": "Review or reject",
        }
    )
    palette = {
        "Payment-ready": WORKBOOK_GROUP_COLORS["Pay now"],
        "Review or reject": WORKBOOK_GROUP_COLORS["Review"],
    }

    if axes is None:
        fig, axes = plt.subplots(1, 2, figsize=(14, 5.0))
    else:
        fig = axes[0].get_figure()

    for ax, time_col, threshold, title in [
        (axes[0], "Survey time (min)", 11.57, "Study 2 full survey time"),
        (axes[1], "Attitudes time (min)", 7.85, "Study 2 attitudes section time"),
    ]:
        plotting = study2.dropna(subset=[time_col]).copy()
        plotting = plotting.loc[plotting[time_col] <= 120]
        for label in ["Payment-ready", "Review or reject"]:
            subset = plotting.loc[plotting["Timing comparison group"].eq(label), time_col]
            if len(subset) == 0:
                continue
            ax.hist(
                subset,
                bins=28,
                alpha=0.65,
                label=f"{label} (n={len(subset)})",
                color=palette[label],
                edgecolor="white",
                linewidth=0.3,
            )
        ax.axvline(
            threshold,
            color="#DC2626",
            linestyle="--",
            linewidth=2,
            label=f"Time limit: {threshold} min",
        )
        ax.set_xlabel("Minutes")
        ax.set_ylabel("Records")
        ax.set_title(title)
        ax.legend(frameon=False, fontsize=9)
        _apply_stakeholder_style(ax)

    fig.suptitle(
        "Simple timing comparison: payment-ready vs flagged",
        fontsize=15,
        fontweight="bold",
        y=1.03,
    )
    fig.tight_layout()
    return fig


def build_workbook_dashboard_figure(stakeholder_records: pd.DataFrame) -> plt.Figure:
    """Create one compact dashboard figure with a bar chart and two histograms."""
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.2))
    plot_workbook_action_summary(stakeholder_records, ax=axes[0])
    plot_workbook_timing_histogram(
        stakeholder_records,
        time_col="Survey time (min)",
        threshold_min=11.57,
        title="Study 2 full survey time",
        ax=axes[1],
    )
    plot_workbook_timing_histogram(
        stakeholder_records,
        time_col="Attitudes time (min)",
        threshold_min=7.85,
        title="Study 2 attitudes time",
        ax=axes[2],
    )
    fig.suptitle("Stakeholder bot-analysis dashboard", fontsize=16, fontweight="bold", y=1.03)
    fig.tight_layout()
    return fig


def _save_figure_image(fig: plt.Figure) -> str:
    """Write a temporary PNG for workbook embedding and close the figure."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
        fig.savefig(temp_file.name, dpi=170, bbox_inches="tight", facecolor="white")
        image_path = temp_file.name
    plt.close(fig)
    return image_path


# ── Pipeline text diagram ───────────────────────────────────────────────────

WORKFLOW_PIPELINE = """
┌──────────────────────────────────┐
│  1,779 Raw Responses             │
│  (Study 2 — Project 4581)        │
└───────────────┬──────────────────┘
                │
                ▼
┌──────────────────────────────────┐
│  STEP 1: Apply Hard Checks       │
│  (Tier 1 Rules)                   │
│                                   │
│  R1: Full survey < 11.57 min      │
│  R2: Attitudes section < 7.85 min │
│  R5: Duplicate response pattern   │
│  R8: Illogical family info        │
│  R9: Impossible demographics      │
└───────────────┬──────────────────┘
                │
        ┌───────┴───────┐
        │ Any Hard      │
        │ Check Failed? │
        └──┬─────────┬──┘
        YES│         │NO
           ▼         ▼
   ┌───────────┐  ┌──────────────────────────────┐
   │ FLAGGED   │  │  STEP 2: Apply Soft Checks    │
   │ (Tier 1)  │  │  (Tier 2 / 3 / 4 Rules)       │
   │           │  │                                │
   │ 508       │  │  R3: Section below speed floor  │
   │ records   │  │  R4: Flat-line responses        │
   └─────┬─────┘  │  R6: Bursty submission timing   │
         │        │  R7: Near-duplicate open text    │
         │        └──────────────┬─────────────────┘
         │               ┌──────┴──────┐
         │               │ ≥2 Soft     │
         │               │ Flags?      │
         │               └──┬──────┬───┘
         │               YES│      │NO
         │                  ▼      ▼
         │         ┌──────────┐  ┌─────────────────────────┐
         │         │ Tier 2:  │  │ Tier 3/4:               │
         │         │ Needs    │  │ Likely Real / Real       │
         │         │ Review   │  │ Caregivers               │
         │         │ (563)    │  │ (708 = 679 likely + 70   │
         │         └──────────┘  │  real from dirty)        │
         │                       └─────────────────────────┘
         ▼
   ┌──────────────────────┐
   │ ≥3 hard hits OR      │
   │ R8/R9 + speed flag?  │
   └──┬────────────┬──────┘
   YES│            │NO
      ▼            ▼
┌───────────┐  ┌─────────────────┐
│ Confirmed │  │ Single / double │
│ Bot (6)   │  │ hard flag →     │
│           │  │ Needs Review    │
│ REJECT    │  │ (502)           │
└───────────┘  └─────────────────┘
"""


def print_workflow_pipeline() -> None:
    """Print the screening workflow as a text diagram."""
    print(WORKFLOW_PIPELINE)


# ── Excel export ────────────────────────────────────────────────────────────

def _friendly_rename(df: pd.DataFrame) -> pd.DataFrame:
    """Rename columns using friendly names, keeping unmapped columns as-is."""
    return df.rename(columns=COLUMN_FRIENDLY_NAMES)


def _autosize_sheet(
    worksheet,
    wrap_text: bool = False,
    freeze_panes: Optional[str] = "A2",
    apply_filter: bool = True,
) -> None:
    for column_cells in worksheet.columns:
        column_letter = get_column_letter(column_cells[0].column)
        max_length = max(len(str(cell.value)) if cell.value is not None else 0 for cell in column_cells)
        worksheet.column_dimensions[column_letter].width = min(max(max_length + 2, 12), 42)
    if freeze_panes:
        worksheet.freeze_panes = freeze_panes
    if apply_filter:
        worksheet.auto_filter.ref = worksheet.dimensions
    if wrap_text:
        for row in worksheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(
                    horizontal=cell.alignment.horizontal,
                    vertical=cell.alignment.vertical,
                    text_rotation=cell.alignment.text_rotation,
                    wrap_text=True,
                    shrink_to_fit=cell.alignment.shrink_to_fit,
                    indent=cell.alignment.indent,
                )


def _style_sheet_heading(worksheet, cell: str, text: str, size: int = 14) -> None:
    worksheet[cell] = text
    worksheet[cell].font = Font(size=size, bold=True)
    worksheet[cell].alignment = Alignment(wrap_text=True)


def _style_sheet_note(worksheet, cell: str, text: str) -> None:
    worksheet[cell] = text
    worksheet[cell].alignment = Alignment(wrap_text=True)


def export_stakeholder_excel(
    output_dir: Path,
    cache_dir: Optional[Path] = None,
    excel_filename: str = "ESD_Bot_Analysis_Stakeholder_Summary.xlsx",
) -> Path:
    """Write a compact stakeholder workbook with action-oriented sheets and simple extra views."""
    views = build_stakeholder_views(output_dir, cache_dir)
    stakeholder_records = views["stakeholder_records"]
    dashboard_summary = build_workbook_action_summary(stakeholder_records)
    key_metrics = build_workbook_key_metrics(stakeholder_records)
    timing_summary = build_workbook_timing_summary(stakeholder_records)
    review_sheet = build_review_needed_sheet(stakeholder_records)
    payment_sheet = build_payment_ready_sheet(stakeholder_records)
    issue_summary = build_workbook_issue_summary(stakeholder_records)
    decision_guide = build_workbook_decision_guide(stakeholder_records)
    workflow_summary = build_workbook_workflow_summary(stakeholder_records)
    demographic_snapshot = build_workbook_demographic_snapshot(stakeholder_records)
    study_action_summary = views["study_action_summary"]
    review_signal_summary = views["review_signal_summary"]
    flag_strength_summary = views["flag_strength_summary"]

    excel_path = output_dir / excel_filename
    temp_images: list[str] = []

    try:
        with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
            dashboard_summary.to_excel(writer, sheet_name="Summary", index=False, startrow=3, startcol=0)
            key_metrics.to_excel(writer, sheet_name="Summary", index=False, startrow=3, startcol=6)
            timing_summary.to_excel(writer, sheet_name="Summary", index=False, startrow=10, startcol=0)

            payment_sheet.to_excel(writer, sheet_name="Pay Now", index=False, startrow=2)
            review_sheet.to_excel(writer, sheet_name="Review Queue", index=False, startrow=2)

            decision_guide.to_excel(writer, sheet_name="Screen Guide", index=False, startrow=3, startcol=0)
            issue_summary.to_excel(writer, sheet_name="Screen Guide", index=False, startrow=3, startcol=5)
            workflow_summary.to_excel(writer, sheet_name="Screen Guide", index=False, startrow=12, startcol=0)
            demographic_snapshot.to_excel(writer, sheet_name="Screen Guide", index=False, startrow=12, startcol=5)

            study_action_summary.to_excel(writer, sheet_name="Extra Views", index=False, startrow=3, startcol=0)
            review_signal_summary.to_excel(writer, sheet_name="Extra Views", index=False, startrow=3, startcol=6)
            flag_strength_summary.to_excel(writer, sheet_name="Extra Views", index=False, startrow=12, startcol=6)

            overview_ws = writer.book["Summary"]
            _style_sheet_heading(overview_ws, "A1", "ESD Bot Analysis Summary")
            _style_sheet_note(
                overview_ws,
                "A2",
                "This sheet keeps only the main action counts, timing summaries, and simple charts for stakeholder review.",
            )
            _style_sheet_heading(overview_ws, "A3", "Action groups", size=12)
            _style_sheet_heading(overview_ws, "G3", "Key metrics", size=12)
            _style_sheet_heading(overview_ws, "A10", "Study 2 timing by action group", size=12)

            methods_ws = writer.book["Screen Guide"]
            _style_sheet_heading(methods_ws, "A1", "Screen Guide")
            _style_sheet_note(
                methods_ws,
                "A2",
                "Plain-language guide to the four action groups, the main Study 2 issues, and the simplest workflow and demographic context.",
            )
            _style_sheet_heading(methods_ws, "A3", "Action guide", size=12)
            _style_sheet_heading(methods_ws, "F3", "Most common Study 2 issues", size=12)
            _style_sheet_heading(methods_ws, "A12", "Workflow summary", size=12)
            _style_sheet_heading(methods_ws, "F12", "Cleared now vs review queue", size=12)

            extra_ws = writer.book["Extra Views"]
            _style_sheet_heading(extra_ws, "A1", "Extra Stakeholder Views")
            _style_sheet_note(
                extra_ws,
                "A2",
                "This sheet mirrors the simplest stakeholder add-ons from the notebook: the study split, the flagged-queue mix, and one simple timing comparison.",
            )
            _style_sheet_heading(extra_ws, "A3", "Action groups by study", size=12)
            _style_sheet_heading(extra_ws, "G3", "What is driving the Study 2 flagged queue", size=12)
            _style_sheet_heading(extra_ws, "G12", "How severe is the flagged queue", size=12)

            payment_ws = writer.book["Pay Now"]
            _style_sheet_heading(payment_ws, "A1", "Pay Now")
            _style_sheet_note(
                payment_ws,
                "A2",
                "Use this sheet for records that can be paid now or approved after a quick check.",
            )

            review_ws = writer.book["Review Queue"]
            _style_sheet_heading(review_ws, "A1", "Review Queue")
            _style_sheet_note(
                review_ws,
                "A2",
                "Use this sheet for records that need manual review or should not be paid.",
            )

            dashboard_image = _save_figure_image(build_workbook_dashboard_figure(stakeholder_records))
            temp_images.append(dashboard_image)
            overview_ws.add_image(XLImage(dashboard_image), "A18")

            study_action_image = _save_figure_image(plot_workbook_study_action_comparison(stakeholder_records))
            temp_images.append(study_action_image)
            extra_ws.add_image(XLImage(study_action_image), "A16")

            review_signal_image = _save_figure_image(plot_workbook_review_signal_summary(stakeholder_records))
            temp_images.append(review_signal_image)
            extra_ws.add_image(XLImage(review_signal_image), "J16")

            timing_comparison_image = _save_figure_image(plot_workbook_timing_comparison(stakeholder_records))
            temp_images.append(timing_comparison_image)
            extra_ws.add_image(XLImage(timing_comparison_image), "A45")

            _autosize_sheet(overview_ws, wrap_text=True, freeze_panes=None, apply_filter=False)
            _autosize_sheet(payment_ws, wrap_text=True, freeze_panes="A4")
            _autosize_sheet(review_ws, wrap_text=True, freeze_panes="A4")
            _autosize_sheet(methods_ws, wrap_text=True, freeze_panes=None, apply_filter=False)
            _autosize_sheet(extra_ws, wrap_text=True, freeze_panes=None, apply_filter=False)
            overview_ws.column_dimensions["B"].width = 18
            overview_ws.column_dimensions["C"].width = 18
            overview_ws.column_dimensions["D"].width = 22
            methods_ws.column_dimensions["A"].width = 22
            methods_ws.column_dimensions["F"].width = 28
            extra_ws.column_dimensions["A"].width = 24
            extra_ws.column_dimensions["B"].width = 18
            extra_ws.column_dimensions["G"].width = 28
            extra_ws.column_dimensions["J"].width = 22
            payment_ws.freeze_panes = "A4"
            review_ws.freeze_panes = "A4"
    finally:
        for image_path in temp_images:
            if os.path.exists(image_path):
                os.unlink(image_path)

    return excel_path


# ══════════════════════════════════════════════════════════════════════════
# Stakeholder screening-evidence views
#
# Everything below answers four questions in plain language: does the screen
# work, what did it catch, how severe is it, and what should we do next.  The
# helpers deliberately re-derive the tier logic from the committed rule flags
# so the notebook can show counterfactuals ("what if we moved the line?")
# without re-running the full pipeline.
# ══════════════════════════════════════════════════════════════════════════

SERIOUS_RULES = ["rule_R1", "rule_R2", "rule_R5", "rule_R8", "rule_R9"]
SUPPORTING_RULES = ["rule_R3", "rule_R4", "rule_R6", "rule_R7"]
SHARED_RULES = SERIOUS_RULES + SUPPORTING_RULES

# Plain-English name for every check, with no rule codes and no statistics
# vocabulary.  These strings are what stakeholders read on every axis label,
# table cell and legend in the notebook.
CHECK_PLAIN_NAMES: dict[str, str] = {
    "rule_R1": "Whole survey too fast",
    "rule_R2": "Attitudes section too fast",
    "rule_R3": "One section rushed",
    "rule_R4": "Same answer repeated down a block",
    "rule_R5": "Answer sheet identical to another response",
    "rule_R6": "Arrived within a minute of two or more others",
    "rule_R7": "Comment nearly identical to another",
    "rule_R8": "Family answers contradict each other",
    "rule_R9": "Age and location cannot both be true",
}

CHECK_SEVERITY: dict[str, str] = {
    **{rule: "Serious" for rule in SERIOUS_RULES},
    **{rule: "Supporting" for rule in SUPPORTING_RULES},
}

STUDY_SHORT_NAMES: dict[str, str] = {
    "clean_4797": "Caregivers we verified",
    "dirty_4581": "Online sign-ups",
}

STUDY_COLORS: dict[str, str] = {
    "Caregivers we verified": "#1F5A7A",
    "Online sign-ups": "#C67C2D",
}

TIME_FIELDS = ["get_time_fif", "get_time_val", "get_time_tfa", "get_time_demo"]

TOTAL_TIME_LIMIT_MIN = 11.57
ATTITUDES_TIME_LIMIT_MIN = 7.85


def _minutes_to_words(minutes: float) -> str:
    """Render 11.57 as '11 minutes 34 seconds' so it is never read as a clock time."""
    whole = int(minutes)
    seconds = int(round((minutes - whole) * 60))
    if seconds == 60:
        whole, seconds = whole + 1, 0
    if whole == 0:
        return f"{seconds} seconds"
    minute_word = "minute" if whole == 1 else "minutes"
    if seconds == 0:
        return f"{whole} {minute_word}"
    return f"{whole} {minute_word} {seconds} seconds"


def build_screen_inputs(
    output_dir: Path,
    cache_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """One tidy record-level frame behind every screening-evidence view.

    The survey total here is deliberately stricter than the one in
    ``build_stakeholder_record_view``: that column sums the four section times
    with ``min_count=1``, so a half-answered record gets a small partial total
    that never triggered the speed check.  Charting the partial total would put
    verified caregivers below a line that no verified caregiver actually
    crossed, so timing views use ``Timed end to end`` to select records and
    ``Survey minutes`` for the value.
    """
    if cache_dir is None:
        cache_dir = output_dir.parent / "data_cache"

    flags = load_record_flags(output_dir).copy()
    flags["record_id"] = flags["record_id"].astype(str)

    records = load_combined_records(cache_dir).copy()
    records["record_id"] = records["record_id"].astype(str)
    for column in TIME_FIELDS:
        records[column] = pd.to_numeric(records[column], errors="coerce")

    keep = ["source_project", "record_id", "eligibility_timestamp"] + TIME_FIELDS
    frame = flags.merge(
        records[keep], on=["source_project", "record_id"], how="left", validate="one_to_one"
    )

    sections_timed = frame[TIME_FIELDS].notna().sum(axis=1)
    frame["Sections timed"] = sections_timed
    frame["Timed end to end"] = sections_timed.eq(len(TIME_FIELDS))
    frame["Survey minutes"] = frame[TIME_FIELDS].sum(axis=1, min_count=len(TIME_FIELDS)).round(2)
    frame["Attitudes minutes"] = frame["get_time_tfa"].round(2)
    frame["Arrived"] = pd.to_datetime(frame["eligibility_timestamp"], errors="coerce")

    frame["Group"] = frame["source_project"].map(STUDY_SHORT_NAMES)
    frame["Study"] = frame["source_project"].map(PROJECT_LABELS)
    frame["Checks set off"] = frame[SHARED_RULES].sum(axis=1).astype(int)
    frame["Serious checks"] = frame[SERIOUS_RULES].sum(axis=1).astype(int)
    frame["Supporting checks"] = frame[SUPPORTING_RULES].sum(axis=1).astype(int)
    frame["Trust Tier"] = frame["tier_label"]
    frame["Payment Decision"] = frame["tier_label"].map(
        {
            "Pass": "Cleared for payment now",
            "Uncertain": "Low-risk provisional approval",
            "High suspicion": "Needs human review",
            "Confirmed invalid": "Needs human review",
        }
    )
    confirmed = set(load_confirmed_bots(output_dir)["Record ID"].astype(str))
    is_bot = frame["source_project"].eq("dirty_4581") & frame["record_id"].isin(confirmed)
    frame.loc[is_bot, "Payment Decision"] = "Confirmed bot / reject"
    return frame


def _decide(serious: pd.Series, supporting: pd.Series) -> pd.Series:
    """Re-apply the published rule: any serious check, or two supporting ones, holds a payment."""
    decision = pd.Series("Cleared for payment now", index=serious.index)
    decision[supporting.eq(1)] = "Low-risk provisional approval"
    decision[supporting.ge(2)] = "Needs human review"
    decision[serious.ge(1)] = "Needs human review"
    return decision


# ── 1. Where every time limit came from ─────────────────────────────────────

def build_limit_origin_table(output_dir: Path) -> pd.DataFrame:
    """Show that every cutoff was read off the verified caregivers, not chosen."""
    definitions = load_rule_definitions(output_dir).set_index("rule")

    def threshold(rule: str) -> float:
        return float(definitions.loc[rule, "threshold"])

    section_floors = json.loads(str(definitions.loc["R3", "threshold"]))
    section_names = {
        "feat_time_fif": "Family information section rushed",
        "feat_time_val": "Values section rushed",
        "feat_time_tfa": "Attitudes section rushed",
        "feat_time_demo": "Demographics section rushed",
    }

    rows = [
        {
            "What we check": "Whole survey too fast",
            "The line we drew": _minutes_to_words(threshold("R1")),
            "How that line was worked out": "The fastest verified caregiver who finished every section",
        },
        {
            "What we check": "Attitudes section too fast",
            "The line we drew": _minutes_to_words(threshold("R2")),
            "How that line was worked out": "The fastest verified caregiver on that section",
        },
    ]
    for field, label in section_names.items():
        rows.append(
            {
                "What we check": label,
                "The line we drew": _minutes_to_words(float(section_floors[field])),
                "How that line was worked out": "The fastest 1 in 100 verified caregivers on that section",
            }
        )
    rows.append(
        {
            "What we check": "Same answer repeated down a block",
            "The line we drew": "Answers varied less than 0.78 on a 1-4 block",
            "How that line was worked out": "The 1 in 100 verified caregivers whose answers varied least",
        }
    )
    rows.append(
        {
            "What we check": "Arrived within a minute of two or more others",
            "The line we drew": "3 or more sign-ups inside 60 seconds",
            "How that line was worked out": "The 1 in 100 shortest gaps between verified caregiver sign-ups",
        }
    )

    table = pd.DataFrame(rows)
    table["Whose answers set it"] = "The 131 verified caregivers who finished every section"
    return table


# ── 2. How fast can a real caregiver finish? ────────────────────────────────

def build_speed_reference_table(screen_inputs: pd.DataFrame) -> pd.DataFrame:
    """Companion table for the speed histograms, counts before percentages."""
    rows = []
    specs = [
        ("whole survey", "Survey minutes", TOTAL_TIME_LIMIT_MIN, "Timed end to end"),
        ("attitudes section", "Attitudes minutes", ATTITUDES_TIME_LIMIT_MIN, None),
    ]
    for label, column, limit, gate in specs:
        for group in ("Caregivers we verified", "Online sign-ups"):
            frame = screen_inputs[screen_inputs["Group"].eq(group)]
            if gate is not None:
                frame = frame[frame[gate]]
            values = frame[column].dropna()
            under = int((values < limit).sum())
            rows.append(
                {
                    "Group": f"{group} - {label}",
                    "Responses we could time": f"{len(values):,}",
                    "Middle time (minutes)": round(values.median(), 2),
                    "Fastest time (minutes)": round(values.min(), 2),
                    "Faster than the line": f"{under:,} of {len(values):,}",
                }
            )
    return pd.DataFrame(rows)


def _clipped_histogram(
    ax: plt.Axes,
    screen_inputs: pd.DataFrame,
    column: str,
    gate: Optional[str],
    limit: float,
    ceiling: float,
    step: float,
    title: str,
) -> None:
    """Percent-of-group histogram with a catch-all final bar and the limit marked.

    Survey times run to 1,383 minutes because people leave the form open, so
    the axis is clipped and the tail is collected into one honest bar rather
    than dropped.
    """
    edges = np.arange(0, ceiling + step, step)
    centres = edges[:-1] + step / 2
    # A visible gap keeps the catch-all bar from reading as just another bin.
    extra = ceiling + step * 1.4

    for group, colour in STUDY_COLORS.items():
        frame = screen_inputs[screen_inputs["Group"].eq(group)]
        if gate is not None:
            frame = frame[frame[gate]]
        values = frame[column].dropna()
        counts, _ = np.histogram(values.clip(upper=ceiling - 1e-9), bins=edges)
        over = int((values >= ceiling).sum())
        heights = np.append(counts, over) / len(values) * 100
        ax.bar(
            np.append(centres, extra),
            heights,
            width=step * 0.9,
            color=colour,
            alpha=0.65,
            label=f"{group} ({len(values):,})",
        )

    ax.axvline(limit, color="#A85D75", linestyle="--", linewidth=2)
    ax.text(
        limit,
        ax.get_ylim()[1] * 0.94,
        f"  the fastest verified caregiver\n  ({_minutes_to_words(limit)})",
        color="#A85D75",
        fontsize=9,
        fontweight="bold",
        va="top",
        bbox=dict(facecolor="white", edgecolor="none", alpha=0.8, pad=2),
    )
    ax.set_title(title)
    ax.set_xlabel("Minutes taken")
    ax.set_ylabel("Share of that group (%)")
    # Drop any tick that would collide with the catch-all label.
    ticks = [e for e in edges[::2] if e < ceiling - step]
    ax.set_xticks(ticks + [extra])
    ax.set_xticklabels(
        [f"{int(e)}" for e in ticks] + [f"{int(ceiling)}+"], fontsize=9
    )
    ax.legend(frameon=False, fontsize=9)
    _apply_stakeholder_style(ax)


def plot_speed_comparison(screen_inputs: pd.DataFrame) -> plt.Figure:
    """Two histograms showing verified caregivers never fall below the speed lines."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    _clipped_histogram(
        axes[0],
        screen_inputs,
        "Survey minutes",
        "Timed end to end",
        TOTAL_TIME_LIMIT_MIN,
        ceiling=90,
        step=5,
        title="Whole survey: how long it took",
    )
    _clipped_histogram(
        axes[1],
        screen_inputs,
        "Attitudes minutes",
        None,
        ATTITUDES_TIME_LIMIT_MIN,
        ceiling=45,
        step=2.5,
        title="Attitudes section: how long it took",
    )
    fig.suptitle(
        "Nobody we verified finished faster than the line - many online sign-ups did",
        fontsize=14,
        fontweight="bold",
    )
    fig.tight_layout()
    return fig


# ── 3. What happens if we move the speed line ───────────────────────────────

def build_cutoff_sensitivity_table(
    screen_inputs: pd.DataFrame,
    column: str = "Attitudes minutes",
    gate: Optional[str] = None,
    current: float = ATTITUDES_TIME_LIMIT_MIN,
    candidates: Optional[list[float]] = None,
) -> pd.DataFrame:
    """How many responses each candidate cutoff would catch, in both groups."""
    if candidates is None:
        candidates = [4, 5, 6, 7, current, 9, 10, 12, 15]

    verified = screen_inputs[screen_inputs["Group"].eq("Caregivers we verified")]
    online = screen_inputs[screen_inputs["Group"].eq("Online sign-ups")]
    if gate is not None:
        verified, online = verified[verified[gate]], online[online[gate]]
    verified_values = verified[column].dropna()
    online_values = online[column].dropna()

    rows = []
    for cutoff in candidates:
        caught = int((online_values < cutoff).sum())
        wrong = int((verified_values < cutoff).sum())
        if cutoff == current:
            note = "Today's setting - the last one that catches nobody we know is real"
        elif wrong == 0:
            note = "Catches nobody we know is real, but holds fewer sign-ups"
        else:
            note = f"Moves {wrong} caregiver(s) we know are real into the hold pile"
        rows.append(
            {
                "Where we draw the line": f"{cutoff:g} minutes"
                + (" (today's setting)" if cutoff == current else ""),
                f"Online sign-ups caught (out of {len(online_values):,})": caught,
                f"Caregivers we know are real caught by mistake (out of {len(verified_values):,})": wrong,
                "What this setting means": note,
            }
        )
    return pd.DataFrame(rows)


def plot_cutoff_sensitivity(sensitivity: pd.DataFrame) -> plt.Figure:
    """Two stacked panels: what a cutoff catches, and who it catches by mistake."""
    caught_col = [c for c in sensitivity.columns if c.startswith("Online sign-ups")][0]
    wrong_col = [c for c in sensitivity.columns if c.startswith("Caregivers we know")][0]
    current = sensitivity["Where we draw the line"].str.contains("today")
    # Short axis labels: the full sentence belongs in the table, not on the ticks.
    labels = (
        sensitivity["Where we draw the line"]
        .str.replace(" (today's setting)", "\n(today)", regex=False)
        .str.replace(" minutes", "", regex=False)
    )
    colours = ["#A85D75" if flag else "#C67C2D" for flag in current]

    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    for ax, column, palette, ylabel in (
        (axes[0], caught_col, colours, caught_col),
        (
            axes[1],
            wrong_col,
            ["#A85D75" if flag else "#1F5A7A" for flag in current],
            wrong_col,
        ),
    ):
        bars = ax.bar(labels, sensitivity[column], color=palette)
        # Zero bars carry the good news here, so every value is labelled.
        for bar, value in zip(bars, sensitivity[column]):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(sensitivity[column]) * 0.02,
                f"{int(value):,}",
                ha="center",
                fontsize=10,
                fontweight="bold",
            )
        ax.set_ylabel("\n".join(ylabel.split(" (out of ")[0].split(" caught")[0:1]) + "\ncaught")
        ax.set_ylim(0, max(sensitivity[column]) * 1.18 + 1)
        _apply_stakeholder_style(ax)

    axes[0].set_title("Moving the attitudes-section line: what it would catch")
    axes[1].set_title("Moving the attitudes-section line: who it would catch by mistake")
    axes[1].set_xlabel("Where we draw the line (minutes)")
    fig.tight_layout()
    return fig


# ── 4. Has any check ever flagged a verified caregiver? ─────────────────────

def build_wrong_flag_table(screen_inputs: pd.DataFrame) -> pd.DataFrame:
    """How often each check fires on people we know are real."""
    verified = screen_inputs[
        screen_inputs["Group"].eq("Caregivers we verified") & screen_inputs["Timed end to end"]
    ]
    total = len(verified)

    rows = []
    for rule in SHARED_RULES:
        hits = int(verified[rule].sum())
        if rule in ("rule_R1", "rule_R2"):
            reading = (
                "Zero here is partly guaranteed - this line was set at the fastest "
                "verified caregiver - so treat it as consistent, not as proof."
            )
        elif hits == 0:
            reading = "This check has never fired on anyone, in either study."
        else:
            reading = f"Fires on {hits} of {total} people we know are real."

        online_hits = int(
            screen_inputs.loc[screen_inputs["Group"].eq("Online sign-ups"), rule].sum()
        )
        if hits == 0:
            advice = "Strong enough to act on with a quick spot check"
        elif online_hits == 0:
            advice = (
                "Review this check - it has never fired on an online sign-up, "
                "only on people we know are real"
            )
        else:
            advice = "Use it to sort the queue, never on its own"

        rows.append(
            {
                "The check": CHECK_PLAIN_NAMES[rule],
                "How seriously we treat it": CHECK_SEVERITY[rule],
                f"Verified caregivers it flagged (out of {total})": hits,
                "What that tells us": reading,
                "What to do with it": advice,
            }
        )
    table = pd.DataFrame(rows)
    count_col = [c for c in table.columns if c.startswith("Verified caregivers")][0]
    return table.sort_values(count_col, ascending=False).reset_index(drop=True)


# ── 5. How one response becomes one decision ────────────────────────────────

def build_decision_rule_table() -> pd.DataFrame:
    """The whole decision rule on one small page."""
    rows = []
    for rule in SERIOUS_RULES + SUPPORTING_RULES:
        rows.append(
            {
                "What the check found": CHECK_PLAIN_NAMES[rule],
                "How seriously we treat it": CHECK_SEVERITY[rule],
                "What it takes to hold a payment": (
                    "Any one of these on its own holds the payment"
                    if CHECK_SEVERITY[rule] == "Serious"
                    else "Two or more of these together hold the payment"
                ),
            }
        )
    return pd.DataFrame(rows)


def build_worked_examples(
    screen_inputs: pd.DataFrame,
    record_ids: Optional[list[str]] = None,
) -> pd.DataFrame:
    """Five real responses walked end to end through the decision rule."""
    if record_ids is None:
        record_ids = ["1779", "1243", "1276", "1355", "1026"]

    online = screen_inputs[screen_inputs["Group"].eq("Online sign-ups")].set_index("record_id")
    rows = []
    for record_id in record_ids:
        record = online.loc[record_id]
        fired = [CHECK_PLAIN_NAMES[r] for r in SHARED_RULES if bool(record[r])]
        serious = int(record["Serious checks"])
        supporting = int(record["Supporting checks"])
        if serious == 0 and supporting == 0:
            reading = "No concerns"
        elif serious == 0 and supporting == 1:
            reading = "One supporting check on its own"
        elif serious == 0:
            reading = f"{supporting} supporting checks together"
        else:
            reading = f"{serious} serious and {supporting} supporting checks"
        rows.append(
            {
                "Response": record_id,
                "Whole survey (minutes)": record["Survey minutes"],
                "Attitudes section (minutes)": record["Attitudes minutes"],
                "Checks it set off": "; ".join(fired) if fired else "None",
                "How the rule reads it": reading,
                "Decision": record["Payment Decision"],
            }
        )
    return pd.DataFrame(rows)


# ── 6. How many checks each response set off ────────────────────────────────

def build_checks_set_off_table(screen_inputs: pd.DataFrame) -> pd.DataFrame:
    """Counts and shares of how many checks each response tripped."""
    table = (
        pd.crosstab(screen_inputs["Checks set off"], screen_inputs["Group"])
        .reindex(columns=list(STUDY_COLORS), fill_value=0)
        .reindex(range(0, int(screen_inputs["Checks set off"].max()) + 1), fill_value=0)
    )
    out = pd.DataFrame({"Number of checks set off": table.index})
    for group in STUDY_COLORS:
        share = table[group] / table[group].sum() * 100
        out[group] = table[group].values
        out[f"{group} (% of group)"] = share.round(1).values
    return out


def plot_checks_set_off(screen_inputs: pd.DataFrame) -> plt.Figure:
    """Grouped bars showing two checks is the ceiling for verified caregivers."""
    table = build_checks_set_off_table(screen_inputs)
    x = np.arange(len(table))
    width = 0.4

    fig, ax = plt.subplots(figsize=(11, 5.5))
    for offset, (group, colour) in zip((-width / 2, width / 2), STUDY_COLORS.items()):
        total = table[group].sum()
        bars = ax.bar(
            x + offset,
            table[f"{group} (% of group)"],
            width,
            color=colour,
            label=f"{group} ({total:,})",
        )
        # Zero bars are the point of this chart, so label them too.
        for bar, count in zip(bars, table[group]):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 1.2,
                f"{int(count):,}",
                ha="center",
                fontsize=9,
                fontweight="bold",
            )

    ceiling = 2
    past = int(screen_inputs[
        screen_inputs["Group"].eq("Online sign-ups") & screen_inputs["Checks set off"].gt(ceiling)
    ].shape[0])
    online_total = int(screen_inputs["Group"].eq("Online sign-ups").sum())
    ax.axvline(ceiling + 0.5, color="#A85D75", linestyle="--", linewidth=2)
    ax.text(
        ceiling + 0.62,
        70,
        f"No caregiver we verified has ever\nset off more than two checks.\n"
        f"{past:,} online sign-ups ({past / online_total * 100:.1f}%)\nare past this line.",
        color="#A85D75",
        fontsize=10,
        fontweight="bold",
        va="top",
    )

    ax.set_xticks(x)
    ax.set_xticklabels(table["Number of checks set off"])
    ax.set_xlabel("Number of checks set off")
    ax.set_ylabel("Share of that group (%)")
    ax.set_ylim(0, 95)
    ax.set_title("How many checks each response set off")
    ax.legend(frameon=False)
    _apply_stakeholder_style(ax)
    fig.tight_layout()
    return fig


# ── 7. When the responses arrived ───────────────────────────────────────────

def build_arrival_tables(screen_inputs: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Daily arrival counts per study, plus what the surge day produced."""
    online = screen_inputs[screen_inputs["Group"].eq("Online sign-ups")].copy()
    verified = screen_inputs[screen_inputs["Group"].eq("Caregivers we verified")].copy()

    online_days = online["Arrived"].dt.date.value_counts().sort_index()
    surge_day = online_days.idxmax()

    verified_days = verified["Arrived"].dt.date.value_counts().sort_index()
    top_days = verified_days.nlargest(4).sort_index()
    rest = int(verified_days.sum() - top_days.sum())
    verified_frame = pd.DataFrame(
        {
            "When it arrived": [d.strftime("%d %b") for d in top_days.index]
            + [f"all {len(verified_days) - len(top_days)} other days put together"],
            "Responses": list(top_days.values) + [rest],
        }
    )

    online_frame = pd.DataFrame(
        {
            "When it arrived": [d.strftime("%d %b") for d in online_days.index],
            "Responses": online_days.values,
        }
    )

    online["Surge"] = np.where(
        online["Arrived"].dt.date.eq(surge_day),
        f"Surge day ({surge_day.strftime('%d %b')})",
        "The three days before it",
    )
    order = [
        "Cleared for payment now",
        "Low-risk provisional approval",
        "Needs human review",
        "Confirmed bot / reject",
    ]
    crosstab = (
        pd.crosstab(online["Surge"], online["Payment Decision"])
        .reindex(columns=order, fill_value=0)
        .reindex(["The three days before it", f"Surge day ({surge_day.strftime('%d %b')})"])
        .reset_index()
        .rename(columns={"Surge": "When it arrived"})
    )
    crosstab.insert(1, "Responses", crosstab[order].sum(axis=1))

    surge = online[online["Arrived"].dt.date.eq(surge_day)]
    busiest = surge["Arrived"].dt.hour.value_counts().nlargest(4).sort_index()
    gaps = surge.sort_values("Arrived")["Arrived"].diff().dt.total_seconds().dropna()
    verified_gaps = (
        verified.sort_values("Arrived")["Arrived"].diff().dt.total_seconds().dropna()
    )

    return {
        "online_by_day": online_frame,
        "verified_by_day": verified_frame,
        "surge_crosstab": crosstab,
        "surge_facts": {
            "surge_day": surge_day,
            "surge_n": int(len(surge)),
            "online_n": int(len(online)),
            "busiest_hours": busiest,
            "busiest_n": int(busiest.sum()),
            "median_gap_seconds": float(gaps.median()),
            "verified_median_gap_minutes": float(verified_gaps.median() / 60),
        },
    }


def plot_arrival_pattern(screen_inputs: pd.DataFrame) -> plt.Figure:
    """Two bar panels on their own scales - a shared axis would erase Study 1."""
    tables = build_arrival_tables(screen_inputs)
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    for ax, frame, group in (
        (axes[0], tables["online_by_day"], "Online sign-ups"),
        (axes[1], tables["verified_by_day"], "Caregivers we verified"),
    ):
        bars = ax.bar(frame["When it arrived"], frame["Responses"], color=STUDY_COLORS[group])
        for bar, value in zip(bars, frame["Responses"]):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(frame["Responses"]) * 0.02,
                f"{int(value):,}",
                ha="center",
                fontsize=10,
                fontweight="bold",
            )
        ax.set_title(f"{group}, by day ({int(frame['Responses'].sum()):,} in total)")
        ax.set_ylabel("Responses")
        ax.set_ylim(0, max(frame["Responses"]) * 1.15)
        ax.tick_params(axis="x", rotation=30)
        _apply_stakeholder_style(ax)

    fig.suptitle(
        "The two panels use different scales - read each against its own total",
        fontsize=11,
    )
    fig.tight_layout()
    return fig


# ── 8. Which checks are holding money, and where no check can reach ─────────

def build_check_impact_table(screen_inputs: pd.DataFrame) -> pd.DataFrame:
    """For each check: how often it fired, how often it stood alone, and what
    switching it off would release.  The last column is recomputed by re-running
    the published rule without that check, never asserted."""
    online = screen_inputs[screen_inputs["Group"].eq("Online sign-ups")].copy()
    baseline = _decide(online["Serious checks"], online["Supporting checks"])
    held_now = baseline.eq("Needs human review").sum()

    rows = []
    for rule in SHARED_RULES:
        fired = int(online[rule].sum())
        if fired == 0:
            continue
        others = [r for r in SHARED_RULES if r != rule]
        only_concern = int((online[rule] & ~online[others].any(axis=1)).sum())

        serious = online[[r for r in SERIOUS_RULES if r != rule]].sum(axis=1)
        supporting = online[[r for r in SUPPORTING_RULES if r != rule]].sum(axis=1)
        released = int(held_now - _decide(serious, supporting).eq("Needs human review").sum())

        rows.append(
            {
                "The check": CHECK_PLAIN_NAMES[rule],
                "How seriously we treat it": CHECK_SEVERITY[rule],
                "Times it fired on online sign-ups": fired,
                "Times it was the only concern on the response": only_concern,
                "Payments it would release if we switched it off": released,
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values("Times it fired on online sign-ups", ascending=False)
        .reset_index(drop=True)
    )


def plot_check_impact(impact: pd.DataFrame) -> plt.Figure:
    """Paired horizontal bars - fired, versus stood alone.  Never stacked."""
    frame = impact.iloc[::-1]
    y = np.arange(len(frame))
    height = 0.38

    fig, ax = plt.subplots(figsize=(11.5, 5.5))
    ax.barh(
        y + height / 2,
        frame["Times it fired on online sign-ups"],
        height,
        color="#C67C2D",
        label="Times it fired",
    )
    ax.barh(
        y - height / 2,
        frame["Times it was the only concern on the response"],
        height,
        color="#1F5A7A",
        label="Times it was the only concern on the response",
    )
    for offset, column in (
        (height / 2, "Times it fired on online sign-ups"),
        (-height / 2, "Times it was the only concern on the response"),
    ):
        for index, value in enumerate(frame[column]):
            ax.text(value + 20, index + offset, f"{int(value):,}", va="center", fontsize=9)

    ax.set_yticks(y)
    ax.set_yticklabels(frame["The check"])
    ax.set_xlabel("Online sign-ups")
    ax.set_title("Which checks are actually holding payments")
    ax.legend(frameon=False, loc="lower right")
    _apply_stakeholder_style(ax)
    fig.tight_layout()
    return fig


def build_timing_coverage_table(screen_inputs: pd.DataFrame) -> pd.DataFrame:
    """The blind spot: every timing check needs a timing to fire."""
    online = screen_inputs[screen_inputs["Group"].eq("Online sign-ups")].copy()

    def bucket(row: pd.Series) -> str:
        if row["Sections timed"] == 4:
            return "All four sections timed"
        if row["Sections timed"] == 0:
            return "No timing at all"
        if pd.isna(row["Attitudes minutes"]):
            return "Some sections timed, attitudes section missing"
        return "Some sections timed, one other section missing"

    online["What we were able to time"] = online.apply(bucket, axis=1)
    approved = online["Payment Decision"].isin(
        ["Cleared for payment now", "Low-risk provisional approval"]
    )
    order = [
        "All four sections timed",
        "Some sections timed, attitudes section missing",
        "Some sections timed, one other section missing",
        "No timing at all",
    ]
    table = (
        online.assign(Approved=approved)
        .groupby("What we were able to time")
        .agg(
            Responses=("record_id", "size"),
            **{"Approved to pay": ("Approved", "sum")},
        )
        .reindex(order)
        .reset_index()
    )
    table["Held for review or rejected"] = table["Responses"] - table["Approved to pay"]
    return table


# ── 9. Where to start, and what it costs in staff time ──────────────────────

def build_work_plan_table(
    screen_inputs: pd.DataFrame,
    minutes_per_response: int = 8,
) -> pd.DataFrame:
    """Worst-first batches for the review queue, with running coverage."""
    queue = screen_inputs[
        screen_inputs["Group"].eq("Online sign-ups")
        & screen_inputs["Payment Decision"].eq("Needs human review")
    ].copy()

    beat_the_line = queue["rule_R1"] | queue["rule_R2"]
    target = int(beat_the_line.sum())

    batches = [
        ("Set off 5 or 6 checks", queue["Checks set off"].ge(5)),
        ("Set off 4 checks", queue["Checks set off"].eq(4)),
        ("Set off 3 checks", queue["Checks set off"].eq(3)),
        ("Set off 2 checks or fewer", queue["Checks set off"].le(2)),
    ]

    rows, done, found = [], 0, 0
    for label, mask in batches:
        size = int(mask.sum())
        done += size
        found += int((mask & beat_the_line).sum())
        rows.append(
            {
                "Batch, worst first": label,
                "Responses in this batch": size,
                "Responses checked once this batch is done": done,
                "Responses that beat the fastest verified caregiver, found so far":
                    f"{found} of {target}",
                f"Hours of staff time at {minutes_per_response} minutes each":
                    round(done * minutes_per_response / 60),
                "Responses still unchecked": len(queue) - done,
            }
        )
    return pd.DataFrame(rows)


def build_all_checks_frequency_table(screen_inputs: pd.DataFrame) -> pd.DataFrame:
    """Every shared check and how often it fired, in both groups.

    Supersedes ``build_workbook_issue_summary`` for notebook display: that
    table lists only seven signals and its "Repeated answer pattern" row is the
    identical-answer-sheet check, which has never fired, so a reader concludes
    nobody repeated answers while the same-answer-down-a-block check - which
    fired 577 times - appears nowhere.
    """
    online = screen_inputs[screen_inputs["Group"].eq("Online sign-ups")]
    verified = screen_inputs[screen_inputs["Group"].eq("Caregivers we verified")]

    rows = []
    for rule in SHARED_RULES:
        fired = int(online[rule].sum())
        rows.append(
            {
                "The check": CHECK_PLAIN_NAMES[rule],
                "How seriously we treat it": CHECK_SEVERITY[rule],
                f"Online sign-ups it fired on (out of {len(online):,})": fired,
                "Share of online sign-ups": f"{fired / len(online) * 100:.1f}%",
                f"Caregivers we verified it fired on (out of {len(verified):,})":
                    int(verified[rule].sum()),
            }
        )
    count_col = [c for c in pd.DataFrame(rows).columns if c.startswith("Online sign-ups")][0]
    return pd.DataFrame(rows).sort_values(count_col, ascending=False).reset_index(drop=True)


def plot_all_checks_frequency(screen_inputs: pd.DataFrame) -> plt.Figure:
    """Horizontal bars of how often each check fired on online sign-ups."""
    table = build_all_checks_frequency_table(screen_inputs).iloc[::-1]
    count_col = [c for c in table.columns if c.startswith("Online sign-ups")][0]

    fig, ax = plt.subplots(figsize=(11, 5.5))
    colours = [
        "#A85D75" if severity == "Serious" else "#C67C2D"
        for severity in table["How seriously we treat it"]
    ]
    bars = ax.barh(table["The check"], table[count_col], color=colours)
    for bar, value in zip(bars, table[count_col]):
        ax.text(value + 18, bar.get_y() + bar.get_height() / 2, f"{int(value):,}",
                va="center", fontsize=10, fontweight="bold")

    ax.set_xlabel("Online sign-ups it fired on")
    ax.set_title("How often each check fired on the online sign-ups")
    ax.set_xlim(0, table[count_col].max() * 1.12)
    handles = [
        plt.Rectangle((0, 0), 1, 1, color="#A85D75"),
        plt.Rectangle((0, 0), 1, 1, color="#C67C2D"),
    ]
    ax.legend(handles, ["Serious check", "Supporting check"], frameon=False, loc="lower right")
    _apply_stakeholder_style(ax)
    fig.tight_layout()
    return fig
