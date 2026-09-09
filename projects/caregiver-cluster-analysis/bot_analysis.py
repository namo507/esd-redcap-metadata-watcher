"""Reader-friendly bot analysis helpers for the caregiver study.

This module provides simple, presentation-ready visualisations, plain-English
column names, and a multi-sheet Excel export targeted at non-technical readers
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
    "clean_4797": "Study 1 - verified caregiver sample",
    "dirty_4581": "Study 2 - online recruitment sample",
}

PAYMENT_DECISION_LABELS: dict[str, str] = {
    "auto_eligible": "Cleared for payment now",
    "eligible_low_risk_review": "Low-risk provisional approval",
    "manual_review": "Needs human review",
    "do_not_pay_pending_adjudication": "Confirmed bot / reject",
}

# The keys below are the exact strings written by the pipeline into
# table_41_distinct_record_classification.csv, punctuation included.  Do not
# retype them: the values on the right are what a reader actually sees.
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
    "Review Category",
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


def load_branching_audit(output_dir: Path) -> pd.DataFrame:
    df = pd.read_csv(output_dir / "table_34_branching_logic_audit.csv")
    df["record_id"] = df["record_id"].astype(str)
    return df


def load_dirty_records(cache_dir: Path) -> pd.DataFrame:
    """Load the dirty 4581 REDCap cache (latest parquet)."""
    return _load_project_records(cache_dir, 4581, "dirty_4581")


def load_record_classification(output_dir: Path) -> pd.DataFrame:
    """Load the row-level record classification table."""
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
    """Assign each record to one of 4 review categories.

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


def build_report_record_view(
    output_dir: Path,
    cache_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """Build a reader-friendly record-level export with timing and demographics."""
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
    merged["Review Category"] = merged["classification"].map(
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

    report_records = merged[
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
    report_records["Record ID"] = report_records["Record ID"].astype(str)
    return report_records


def _workflow_counts(report_records: pd.DataFrame) -> dict[str, int]:
    dirty = report_records.loc[report_records["source_project"].eq("dirty_4581")].copy()
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


def build_workflow_summary(report_records: pd.DataFrame) -> pd.DataFrame:
    """Summarise the current Study 2 screening workflow in plain English."""
    counts = _workflow_counts(report_records)
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


def build_overview_summary(report_records: pd.DataFrame) -> pd.DataFrame:
    """Create a concise count table across both studies."""
    rows: list[dict[str, object]] = []
    for label, mask, interpretation in [
        (
            "Cleared for payment now",
            report_records["Payment Decision"].eq("Cleared for payment now"),
            "Tier 4 pass. No screening rule fired.",
        ),
        (
            "Low-risk provisional approval",
            report_records["Payment Decision"].eq("Low-risk provisional approval"),
            "One soft check only. Provisionally safe but still separate from fully clear records.",
        ),
        (
            "Needs human review",
            report_records["Payment Decision"].eq("Needs human review"),
            "Manual adjudication queue.",
        ),
        (
            "Confirmed bot / reject",
            report_records["Payment Decision"].eq("Confirmed bot / reject"),
            "Strongest evidence concentration. Hold payment.",
        ),
        (
            "Extreme fast (R1 + R2)",
            report_records["Extreme Fast (R1 + R2)"].eq("Yes"),
            "Cross-cut subset of the hard-check path; not a separate payment bucket.",
        ),
    ]:
        subset = report_records.loc[mask]
        rows.append(
            {
                "Group": label,
                "Study 1 (4797)": int(subset["source_project"].eq("clean_4797").sum()),
                "Study 2 (4581)": int(subset["source_project"].eq("dirty_4581").sum()),
                "All Records": int(len(subset)),
                "Interpretation": interpretation,
            }
        )

    r1r2_only = report_records.loc[
        report_records["Extreme Fast (R1 + R2)"].eq("Yes")
        & report_records[[
            "Duplicate Response Pattern",
            "Branching / Family Logic Issue",
            "Impossible Demographics",
        ]].ne("Yes").all(axis=1)
    ]
    rows.append(
        {
            "Group": "Extreme fast with no other hard check",
            "Study 1 (4797)": int(r1r2_only["source_project"].eq("clean_4797").sum()),
            "Study 2 (4581)": int(r1r2_only["source_project"].eq("dirty_4581").sum()),
            "All Records": int(len(r1r2_only)),
            "Interpretation": "Broke both timing rules, but no duplicate or logic hard-check signal.",
        }
    )

    return pd.DataFrame(rows)


def format_workflow_pipeline(report_records: pd.DataFrame) -> str:
    """Render a compact ASCII workflow diagram with live counts."""
    counts = _workflow_counts(report_records)
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


def build_demographic_comparison(report_records: pd.DataFrame) -> pd.DataFrame:
    """Compare the 223 fully cleared records with the 1,048 review records."""
    compare = report_records.loc[
        report_records["Payment Decision"].isin(
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


def build_report_views(
    output_dir: Path,
    cache_dir: Optional[Path] = None,
) -> dict[str, object]:
    """Load the report-ready tables, records, and derived summaries."""
    report_records = build_report_record_view(output_dir, cache_dir)
    master_summary = load_master_summary(output_dir)
    detailed_breakdown = load_detailed_breakdown(output_dir)
    confirmed_bots = load_confirmed_bots(output_dir)
    record_flags = load_record_flags(output_dir)

    return {
        "master_summary": master_summary,
        "detailed_breakdown": detailed_breakdown,
        "confirmed_bots": confirmed_bots,
        "record_flags": record_flags,
        "report_records": report_records,
        "cleared_records": report_records.loc[
            report_records["Payment Decision"].eq("Cleared for payment now")
        ].copy(),
        "low_risk_records": report_records.loc[
            report_records["Payment Decision"].eq("Low-risk provisional approval")
        ].copy(),
        "review_records": report_records.loc[
            report_records["Payment Decision"].eq("Needs human review")
        ].copy(),
        "confirmed_bot_records": report_records.loc[
            report_records["Payment Decision"].eq("Confirmed bot / reject")
        ].copy(),
        "extreme_fast_records": report_records.loc[
            report_records["Extreme Fast (R1 + R2)"].eq("Yes")
        ].copy(),
        "overview_summary": build_overview_summary(report_records),
        "workflow_summary": build_workflow_summary(report_records),
        "workflow_diagram": format_workflow_pipeline(report_records),
        "demographic_comparison": build_demographic_comparison(report_records),
        "study_action_summary": build_workbook_study_action_summary(report_records),
        "review_signal_summary": build_workbook_review_signal_summary(report_records),
        "flag_strength_summary": build_workbook_flag_strength_summary(report_records),
    }


def build_notebook_views(
    output_dir: Path,
    cache_dir: Optional[Path] = None,
) -> dict[str, object]:
    """Notebook-facing entry point for every reader-visible table."""
    return build_report_views(output_dir, cache_dir)


def build_compact_workbook_records(report_records: pd.DataFrame) -> pd.DataFrame:
    """Create a simplified record table for the workbook export."""
    df = report_records.copy()
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


def build_workbook_action_summary(report_records: pd.DataFrame) -> pd.DataFrame:
    """Summarize the four action groups in plain language."""
    compact = build_compact_workbook_records(report_records)
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


def build_workbook_study_action_summary(report_records: pd.DataFrame) -> pd.DataFrame:
    """Show how the four action groups split within each study."""
    compact = build_compact_workbook_records(report_records)
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


def build_workbook_key_metrics(report_records: pd.DataFrame) -> pd.DataFrame:
    """Return a concise set of meeting-ready metrics."""
    counts = _workflow_counts(report_records)
    total_records = len(report_records)
    review_total = int(
        report_records["Payment Decision"].isin(
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
            "Why it matters": "Main focus of this notebook.",
        },
        {
            "Metric": "Held for review or do not pay",
            "Value": f"{review_total:,}",
            "Why it matters": "Responses that still need a person to decide payment.",
        },
        {
            "Metric": "Broke both time limits",
            "Value": f"{counts['extreme_fast']:,}",
            "Why it matters": "Responses that were faster than the verified sample on both timing views.",
        },
        {
            "Metric": "Broke both time limits only",
            "Value": f"{counts['extreme_fast_only_speed']:,}",
            "Why it matters": "These responses were below both timing lines without another strong questionnaire mismatch.",
        },
    ]
    return pd.DataFrame(rows)


def build_workbook_timing_summary(report_records: pd.DataFrame) -> pd.DataFrame:
    """Summarize Study 2 timing by decision group."""
    compact = build_compact_workbook_records(report_records)
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


def build_workbook_issue_summary(report_records: pd.DataFrame) -> pd.DataFrame:
    """Count the most visible Study 2 issues in plain language."""
    compact = build_compact_workbook_records(report_records)
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


def build_workbook_review_signal_summary(report_records: pd.DataFrame) -> pd.DataFrame:
    """Summarize the clearest signals inside the Study 2 review queue."""
    compact = build_compact_workbook_records(report_records)
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
                "Pattern in the Study 2 review pile": label,
                "Records": count,
                "Share of review pile (%)": round(count / total * 100, 1) if total else 0.0,
                "How to read it": interpretation,
            }
        )
    return pd.DataFrame(rows)


def build_workbook_flag_strength_summary(report_records: pd.DataFrame) -> pd.DataFrame:
    """Group flagged Study 2 records by how many major issues they carry."""
    compact = build_compact_workbook_records(report_records)
    review_queue = compact.loc[
        compact["source_project"].eq("dirty_4581")
        & compact["Action group"].isin(["Review", "Do not pay"])
    ].copy()
    total = len(review_queue)

    rows = []
    for label, mask, interpretation in [
        (
            "Timing or arrival only",
            review_queue["Hard Check Violations"].eq(0),
            "No strong contradiction; these were held because milder timing or arrival patterns piled up.",
        ),
        (
            "One strong questionnaire concern",
            review_queue["Hard Check Violations"].eq(1),
            "One stronger issue is present, but not enough on its own for a do-not-pay decision.",
        ),
        (
            "Two strong concerns",
            review_queue["Hard Check Violations"].eq(2),
            "Two stronger issues show up on the same record; this is the clearest part of the hold pile.",
        ),
        (
            "Three or more strong concerns",
            review_queue["Hard Check Violations"].ge(3),
            "Very concentrated concern; this is the strongest end of the hold pile.",
        ),
    ]:
        count = int(mask.sum())
        rows.append(
            {
                "Pattern inside the Study 2 hold pile": label,
                "Records": count,
                "Share of hold pile (%)": round(count / total * 100, 1) if total else 0.0,
                "How to read it": interpretation,
            }
        )
    return pd.DataFrame(rows)


def build_workbook_decision_guide(report_records: pd.DataFrame) -> pd.DataFrame:
    """Explain the four workbook decision groups without jargon."""
    compact = build_compact_workbook_records(report_records)
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


def build_workbook_workflow_summary(report_records: pd.DataFrame) -> pd.DataFrame:
    """Condense the workflow into a short summary table."""
    counts = _workflow_counts(report_records)
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


def build_workbook_demographic_snapshot(report_records: pd.DataFrame) -> pd.DataFrame:
    """Return a small, plain-language demographic comparison table."""
    demo = build_demographic_comparison(report_records).copy()
    demo.columns = [
        "Demographic signal",
        "Cleared now (223)",
        "Review queue (1048)",
        "Difference",
    ]
    return demo


def build_payment_ready_sheet(report_records: pd.DataFrame) -> pd.DataFrame:
    """Create the compact payment-ready record sheet."""
    compact = build_compact_workbook_records(report_records)
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


def build_review_needed_sheet(report_records: pd.DataFrame) -> pd.DataFrame:
    """Create the compact review-focused record sheet."""
    compact = build_compact_workbook_records(report_records)
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

def _apply_chart_style(ax: plt.Axes) -> None:
    """Apply clean, large-label formatting shared by every chart in the notebook."""
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
    _apply_chart_style(ax)
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
    _apply_chart_style(ax)
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
    _apply_chart_style(ax)
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
    _apply_chart_style(ax)
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
    _apply_chart_style(ax)
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
    _apply_chart_style(ax)
    fig.tight_layout()
    return fig


def plot_clear_vs_review_demographics(
    report_records: pd.DataFrame,
    axes: Optional[tuple[plt.Axes, plt.Axes]] = None,
) -> plt.Figure:
    """Compare cleared-for-payment records with the human-review queue."""
    compare = report_records.loc[
        report_records["Payment Decision"].isin(
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
    _apply_chart_style(age_ax)

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
    _apply_chart_style(share_ax)
    fig.tight_layout()
    return fig


def plot_workbook_action_summary(
    report_records: pd.DataFrame,
    ax: Optional[plt.Axes] = None,
) -> plt.Figure:
    """Bar chart of the four decision groups for the workbook dashboard."""
    summary = build_workbook_action_summary(report_records)
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
    ax.set_title("How responses were grouped for payment")
    ax.invert_yaxis()
    _apply_chart_style(ax)
    fig.tight_layout()
    return fig


def plot_workbook_study_action_comparison(
    report_records: pd.DataFrame,
    ax: Optional[plt.Axes] = None,
) -> plt.Figure:
    """Compare the four action groups across Study 1 and Study 2."""
    summary = build_workbook_study_action_summary(report_records)

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

    ax.set_title("How payment decisions differ between the two studies")
    ax.set_xlabel("")
    ax.set_ylabel("Records")
    ax.legend(frameon=False, title="")
    _apply_chart_style(ax)
    fig.tight_layout()
    return fig


def plot_workbook_timing_histogram(
    report_records: pd.DataFrame,
    time_col: str,
    threshold_min: float,
    title: str,
    ax: Optional[plt.Axes] = None,
) -> plt.Figure:
    """Histogram of Study 2 timing using workbook-friendly action groups."""
    compact = build_compact_workbook_records(report_records)
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
    _apply_chart_style(ax)
    fig.tight_layout()
    return fig


def plot_workbook_review_signal_summary(
    report_records: pd.DataFrame,
    ax: Optional[plt.Axes] = None,
) -> plt.Figure:
    """Show the most common signals inside the Study 2 review queue."""
    summary = build_workbook_review_signal_summary(report_records)

    if ax is None:
        fig, ax = plt.subplots(figsize=(9.0, 4.8))
    else:
        fig = ax.get_figure()

    colors = sns.color_palette("blend:#DCEAF2,#1F5A7A", n_colors=len(summary))
    bars = ax.barh(
        summary["Pattern in the Study 2 review pile"],
        summary["Share of review pile (%)"],
        color=colors,
        edgecolor="white",
        linewidth=0.5,
    )
    for bar, count, pct in zip(bars, summary["Records"], summary["Share of review pile (%)"]):
        ax.text(
            bar.get_width() + 1,
            bar.get_y() + bar.get_height() / 2,
            f"{int(count):,} ({pct:.1f}%)",
            va="center",
            fontsize=10,
        )

    ax.set_xlabel("Percent of the Study 2 hold + do-not-pay pile")
    ax.set_ylabel("")
    ax.set_title("What shows up most often in the review pile")
    ax.invert_yaxis()
    _apply_chart_style(ax)
    fig.tight_layout()
    return fig


def plot_workbook_timing_comparison(
    report_records: pd.DataFrame,
    axes: Optional[tuple[plt.Axes, plt.Axes]] = None,
) -> plt.Figure:
    """Compare Study 2 timing for payment-ready versus flagged records."""
    compact = build_compact_workbook_records(report_records)
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
        _apply_chart_style(ax)

    fig.suptitle("Simple timing comparison: payment-ready vs held", fontsize=15, fontweight="bold", y=1.03)
    fig.tight_layout()
    return fig


def build_workbook_dashboard_figure(report_records: pd.DataFrame) -> plt.Figure:
    """Create one compact dashboard figure with a bar chart and two histograms."""
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.2))
    plot_workbook_action_summary(report_records, ax=axes[0])
    plot_workbook_timing_histogram(
        report_records,
        time_col="Survey time (min)",
        threshold_min=11.57,
        title="Study 2 full survey time",
        ax=axes[1],
    )
    plot_workbook_timing_histogram(
        report_records,
        time_col="Attitudes time (min)",
        threshold_min=7.85,
        title="Study 2 attitudes time",
        ax=axes[2],
    )
    fig.suptitle("Caregiver response screen dashboard", fontsize=16, fontweight="bold", y=1.03)
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
│  (Study 2, Project 4581)         │
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


def export_report_excel(
    output_dir: Path,
    cache_dir: Optional[Path] = None,
    excel_filename: str = "ESD_Bot_Analysis_Simple_Summary.xlsx",
) -> Path:
    """Write a compact workbook with action-oriented sheets and simple extra views."""
    views = build_report_views(output_dir, cache_dir)
    report_records = views["report_records"]
    dashboard_summary = build_workbook_action_summary(report_records)
    key_metrics = build_workbook_key_metrics(report_records)
    timing_summary = build_workbook_timing_summary(report_records)
    review_sheet = build_review_needed_sheet(report_records)
    payment_sheet = build_payment_ready_sheet(report_records)
    issue_summary = build_workbook_issue_summary(report_records)
    decision_guide = build_workbook_decision_guide(report_records)
    workflow_summary = build_workbook_workflow_summary(report_records)
    demographic_snapshot = build_workbook_demographic_snapshot(report_records)
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
            _style_sheet_heading(overview_ws, "A1", "ESD response screen summary")
            _style_sheet_note(
                overview_ws,
                "A2",
                "This sheet keeps the main payment groups, timing summaries, and simple charts in one place.",
            )
            _style_sheet_heading(overview_ws, "A3", "Action groups", size=12)
            _style_sheet_heading(overview_ws, "G3", "Key metrics", size=12)
            _style_sheet_heading(overview_ws, "A10", "Study 2 timing by action group", size=12)

            methods_ws = writer.book["Screen Guide"]
            _style_sheet_heading(methods_ws, "A1", "How the screen groups responses")
            _style_sheet_note(
                methods_ws,
                "A2",
                "Plain-language guide to the payment groups, the main Study 2 patterns, and the simplest workflow and demographic context.",
            )
            _style_sheet_heading(methods_ws, "A3", "Action guide", size=12)
            _style_sheet_heading(methods_ws, "F3", "Most common Study 2 issues", size=12)
            _style_sheet_heading(methods_ws, "A12", "Workflow summary", size=12)
            _style_sheet_heading(methods_ws, "F12", "Cleared now vs review queue", size=12)

            extra_ws = writer.book["Extra Views"]
            _style_sheet_heading(extra_ws, "A1", "Extra views")
            _style_sheet_note(
                extra_ws,
                "A2",
                "This sheet keeps the study split as a table, then uses charts only for the hold-pile mix and timing comparison.",
            )
            _style_sheet_heading(extra_ws, "A3", "Action groups by study", size=12)
            _style_sheet_heading(extra_ws, "G3", "What is driving the Study 2 hold pile", size=12)
            _style_sheet_heading(extra_ws, "G12", "How severe is the hold pile", size=12)

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

            dashboard_image = _save_figure_image(build_workbook_dashboard_figure(report_records))
            temp_images.append(dashboard_image)
            overview_ws.add_image(XLImage(dashboard_image), "A18")

            review_signal_image = _save_figure_image(plot_workbook_review_signal_summary(report_records))
            temp_images.append(review_signal_image)
            extra_ws.add_image(XLImage(review_signal_image), "A16")

            timing_comparison_image = _save_figure_image(plot_workbook_timing_comparison(report_records))
            temp_images.append(timing_comparison_image)
            extra_ws.add_image(XLImage(timing_comparison_image), "A42")

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


def export_simple_excel(
    output_dir: Path,
    cache_dir: Optional[Path] = None,
    excel_filename: str = "ESD_Bot_Analysis_Simple_Summary.xlsx",
) -> Path:
    """Notebook-facing name for the exported workbook."""
    return export_report_excel(output_dir, cache_dir, excel_filename)


# ══════════════════════════════════════════════════════════════════════════
# Screening-evidence views
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
# vocabulary.  These strings are what readers see on every axis label,
# table cell and legend in the notebook.
CHECK_PLAIN_NAMES: dict[str, str] = {
    "rule_R1": "Whole survey too fast",
    "rule_R2": "Thoughts, Feelings, and Attitudes section too fast",
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

CHECK_SURVEY_AREAS: dict[str, str] = {
    "rule_R1": "Whole survey timing",
    "rule_R2": "Thoughts, Feelings, and Attitudes timing",
    "rule_R3": "Section timing across Family Information, Values, Thoughts/Feelings/Attitudes, or Demographics",
    "rule_R4": "Repeated ratings within a survey block",
    "rule_R5": "Full answer pattern across the questionnaire",
    "rule_R6": "Response arrival time",
    "rule_R7": "Open-text comment",
    "rule_R8": "Family Information",
    "rule_R9": "Demographics",
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
    ``build_report_record_view``: that column sums the four section times
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
    """Show that every line was read off the verified caregivers, not chosen."""
    definitions = load_rule_definitions(output_dir).set_index("rule")

    def threshold(rule: str) -> float:
        return float(definitions.loc[rule, "threshold"])

    section_floors = json.loads(str(definitions.loc["R3", "threshold"]))
    straightline = json.loads(str(definitions.loc["R4", "threshold"]))
    section_names = {
        "feat_time_fif": "Family Information section rushed",
        "feat_time_val": "Values section rushed",
        "feat_time_tfa": "Attitudes section rushed",
        "feat_time_demo": "Demographics section rushed",
    }

    rows = [
        {
            "What we check": "Whole survey too fast",
            "The line we drew": _minutes_to_words(threshold("R1")),
            "How that line was worked out": "Smallest whole-survey time among the verified caregivers",
        },
        {
            "What we check": "Thoughts, Feelings, and Attitudes section too fast",
            "The line we drew": _minutes_to_words(threshold("R2")),
            "How that line was worked out": "Smallest section time among the verified caregivers",
        },
    ]
    for field, label in section_names.items():
        rows.append(
            {
                "What we check": label,
                "The line we drew": _minutes_to_words(float(section_floors[field])),
                "How that line was worked out": "1st percentile of verified caregiver times on that section",
            }
        )
    lowest_block = min(straightline.values())
    highest_block = max(straightline.values())
    rows.append(
        {
            "What we check": "Same answer repeated down a rating block",
            "The line we drew": (
                f"Answers spread by {lowest_block:.2f} to {highest_block:.2f} "
                "points or less, depending on the block"
            ),
            "How that line was worked out": (
                "1st percentile of the verified caregivers' answer spread, "
                "worked out separately for each rating block"
            ),
        }
    )
    rows.append(
        {
            "What we check": "Arrived within a minute of two or more others",
            "The line we drew": f"3 or more sign-ups inside {threshold('R6'):.0f} seconds",
            "How that line was worked out": (
                "The verified caregivers' 1st percentile gap was 3 seconds, which was "
                "too tight to be useful, so a preset 60-second floor was applied instead. "
                "This is the one line not read straight off the verified caregivers."
            ),
        }
    )

    table = pd.DataFrame(rows)
    table["Whose answers set it"] = "The 131 verified caregivers who finished every section"
    return table


def build_threshold_check_table(
    output_dir: Path,
    cache_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """Recompute each timing line from the raw verified answers and compare.

    This is the audit step.  The published lines live in a committed CSV; this
    function goes back to the cached REDCap answers, redoes the arithmetic, and
    prints both numbers next to each other so a reader can see they agree.
    """
    if cache_dir is None:
        cache_dir = output_dir.parent / "data_cache"

    records = load_combined_records(cache_dir).copy()
    for column in TIME_FIELDS:
        records[column] = pd.to_numeric(records[column], errors="coerce")
    totals = records[TIME_FIELDS].sum(axis=1, min_count=len(TIME_FIELDS))
    verified = records[records["source_project"].eq("clean_4797") & totals.notna()]
    verified_totals = verified[TIME_FIELDS].sum(axis=1)

    definitions = load_rule_definitions(output_dir).set_index("rule")
    section_floors = json.loads(str(definitions.loc["R3", "threshold"]))

    checks = [
        (
            "Whole survey too fast",
            "smallest whole-survey time",
            float(definitions.loc["R1", "threshold"]),
            float(verified_totals.min()),
        ),
        (
            "Attitudes section too fast",
            "smallest attitudes time",
            float(definitions.loc["R2", "threshold"]),
            float(verified["get_time_tfa"].min()),
        ),
    ]
    section_pairs = [
        ("Family Information section rushed", "get_time_fif", "feat_time_fif"),
        ("Values section rushed", "get_time_val", "feat_time_val"),
        ("Attitudes section rushed", "get_time_tfa", "feat_time_tfa"),
        ("Demographics section rushed", "get_time_demo", "feat_time_demo"),
    ]
    for label, column, key in section_pairs:
        checks.append(
            (
                label,
                "1st percentile of verified times",
                float(section_floors[key]),
                float(verified[column].quantile(0.01)),
            )
        )

    rows = []
    for label, method, published, recomputed in checks:
        rows.append(
            {
                "What we check": label,
                "How the line is worked out": method,
                "Line used in the report (minutes)": round(published, 3),
                "Line recomputed here (minutes)": round(recomputed, 3),
                "Do they match": "Yes" if abs(published - recomputed) < 0.005 else "No",
            }
        )
    table = pd.DataFrame(rows)
    table.attrs["verified_n"] = int(len(verified))
    return table


# ── 2. How fast can a real caregiver finish? ────────────────────────────────

def build_speed_reference_table(screen_inputs: pd.DataFrame) -> pd.DataFrame:
    """Companion table for the speed histograms, counts before percentages."""
    rows = []
    specs = [
        ("whole survey", "Survey minutes", TOTAL_TIME_LIMIT_MIN, "Timed end to end"),
        ("Thoughts, Feelings, and Attitudes section", "Attitudes minutes", ATTITUDES_TIME_LIMIT_MIN, None),
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
    """Side-by-side percent histogram with a catch-all final bar and the limit marked.

    The two groups are drawn as neighbouring bars, never overlaid.  Overlaid
    semi-transparent bars read as a stacked chart, so a reader cannot tell how
    tall either group's bar actually is.

    Survey times run past 1,300 minutes because people leave the form open, so
    the axis is clipped and the long tail is collected into one labelled bar
    rather than dropped.
    """
    edges = np.arange(0, ceiling + step, step)
    centres = edges[:-1] + step / 2
    # A visible gap keeps the catch-all bar from reading as just another bin.
    extra = ceiling + step * 1.6
    positions = np.append(centres, extra)
    bar_width = step * 0.40

    heights_by_group = {}
    for offset, (group, colour) in zip(
        (-bar_width / 2, bar_width / 2), STUDY_COLORS.items()
    ):
        frame = screen_inputs[screen_inputs["Group"].eq(group)]
        if gate is not None:
            frame = frame[frame[gate]]
        values = frame[column].dropna()
        counts, _ = np.histogram(values.clip(upper=ceiling - 1e-9), bins=edges)
        over = int((values >= ceiling).sum())
        heights = np.append(counts, over) / len(values) * 100
        heights_by_group[group] = heights
        under = int((values < limit).sum())
        ax.bar(
            positions + offset,
            heights,
            bar_width,
            color=colour,
            label=f"{group}: {len(values):,} timed, {under:,} under the line",
        )

    tallest = max(h.max() for h in heights_by_group.values())
    ax.set_ylim(0, tallest * 1.22)

    # Shade the region that the rule treats as too fast.
    ax.axvspan(0, limit, color="#A85D75", alpha=0.12, zorder=0)
    ax.axvline(limit, color="#A85D75", linestyle="--", linewidth=2, zorder=3)
    ax.text(
        limit,
        tallest * 1.20,
        f" Line: {_minutes_to_words(limit)}",
        color="#A85D75",
        fontsize=10,
        fontweight="bold",
        va="top",
        ha="left",
    )
    ax.set_title(title)
    ax.set_xlabel("Minutes taken to finish")
    ax.set_ylabel("Share of that group (%)")
    ax.set_xlim(-step, extra + step)
    # Drop any tick that would collide with the catch-all label.
    ticks = [e for e in edges[::2] if e < ceiling - step]
    ax.set_xticks(ticks + [extra])
    ax.set_xticklabels([f"{int(e)}" for e in ticks] + [f"{int(ceiling)}+"], fontsize=9)
    handles, labels = ax.get_legend_handles_labels()
    handles.append(plt.Rectangle((0, 0), 1, 1, color="#A85D75", alpha=0.30))
    labels.append("Shaded: faster than every verified caregiver")
    ax.legend(
        handles,
        labels,
        frameon=False,
        fontsize=10,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.19),
        ncol=1,
    )
    _apply_chart_style(ax)


def plot_speed_comparison(screen_inputs: pd.DataFrame) -> plt.Figure:
    """Two histograms showing verified caregivers never fall below the speed lines."""
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.6))
    _clipped_histogram(
        axes[0],
        screen_inputs,
        "Survey minutes",
        "Timed end to end",
        TOTAL_TIME_LIMIT_MIN,
        ceiling=90,
        step=5,
        title="Whole survey (all four sections)",
    )
    _clipped_histogram(
        axes[1],
        screen_inputs,
        "Attitudes minutes",
        None,
        ATTITUDES_TIME_LIMIT_MIN,
        ceiling=45,
        step=2.5,
        title="Thoughts, Feelings, and Attitudes section",
    )
    fig.suptitle(
        "No verified caregiver finished faster than the line. Many online sign-ups did.",
        fontsize=14,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    return fig


# ── 3. What happens if we move the speed line ───────────────────────────────

def build_cutoff_sensitivity_table(
    screen_inputs: pd.DataFrame,
    column: str = "Attitudes minutes",
    gate: Optional[str] = None,
    current: float = ATTITUDES_TIME_LIMIT_MIN,
    candidates: Optional[list[float]] = None,
) -> pd.DataFrame:
    """How many responses each candidate line would catch, in both groups."""
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
            note = "Today's setting, and the loosest line that still catches no verified caregiver"
        elif wrong == 0:
            note = "Catches no verified caregiver, but holds fewer online sign-ups"
        else:
            note = f"Pulls {wrong} verified caregiver(s) into the hold pile"
        rows.append(
            {
                "Where we draw the line": f"{cutoff:g} minutes"
                + (" (today's setting)" if cutoff == current else ""),
                f"Online sign-ups caught (of {len(online_values):,})": caught,
                f"Verified caregivers caught by mistake (of {len(verified_values):,})": wrong,
                "What this setting means": note,
            }
        )
    return pd.DataFrame(rows)


def plot_cutoff_sensitivity(sensitivity: pd.DataFrame) -> plt.Figure:
    """Two stacked panels: what a line catches, and who it catches by mistake."""
    caught_col = [c for c in sensitivity.columns if c.startswith("Online sign-ups")][0]
    wrong_col = [c for c in sensitivity.columns if c.startswith("Verified caregivers")][0]
    current = sensitivity["Where we draw the line"].str.contains("today")
    # Short axis labels: the full sentence belongs in the table, not on the ticks.
    labels = (
        sensitivity["Where we draw the line"]
        .str.replace(" (today's setting)", "\n(today)", regex=False)
        .str.replace(" minutes", "", regex=False)
    )

    panels = [
        (
            caught_col,
            ["#A85D75" if flag else "#C67C2D" for flag in current],
            "Online sign-ups\ncaught",
            "Moving the attitudes line: online sign-ups it would catch",
        ),
        (
            wrong_col,
            ["#A85D75" if flag else "#1F5A7A" for flag in current],
            "Verified caregivers\ncaught by mistake",
            "Moving the attitudes line: verified caregivers it would catch by mistake",
        ),
    ]

    fig, axes = plt.subplots(2, 1, figsize=(11.5, 7.6), sharex=True)
    for ax, (column, palette, ylabel, title) in zip(axes, panels):
        tallest = max(sensitivity[column].max(), 1)
        bars = ax.bar(labels, sensitivity[column], color=palette)
        # Zero bars carry the good news here, so every value is labelled.
        for bar, value in zip(bars, sensitivity[column]):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + tallest * 0.03,
                f"{int(value):,}",
                ha="center",
                fontsize=10,
                fontweight="bold",
            )
        ax.set_ylabel(ylabel)
        ax.set_ylim(0, tallest * 1.20)
        ax.set_title(title)
        _apply_chart_style(ax)

    axes[1].set_xlabel("Where we draw the line (minutes)")
    axes[1].text(
        0.02,
        0.94,
        "The zero at today's setting is partly built in:\n"
        "the line was placed at the fastest verified caregiver.",
        transform=axes[1].transAxes,
        ha="left",
        va="top",
        fontsize=9.5,
        color="#A85D75",
        fontweight="bold",
    )
    fig.tight_layout()
    return fig


# ── 4. Has any check ever flagged a verified caregiver? ─────────────────────

def build_wrong_flag_table(screen_inputs: pd.DataFrame) -> pd.DataFrame:
    """How often each check fires on people we know are real.

    The denominator changes by check on purpose.  The two whole-timing checks
    can only fire on a response that has all four sections timed, so scoring
    them against all 177 verified caregivers would understate how often they
    fire.  Every other check can fire on any response, so it is scored against
    all 177.
    """
    verified = screen_inputs[screen_inputs["Group"].eq("Caregivers we verified")]
    timed = verified[verified["Timed end to end"]]
    online = screen_inputs[screen_inputs["Group"].eq("Online sign-ups")]

    rows = []
    for rule in SHARED_RULES:
        needs_full_timing = rule in ("rule_R1", "rule_R2")
        pool = timed if needs_full_timing else verified
        total = len(pool)
        hits = int(pool[rule].sum())
        online_hits = int(online[rule].sum())

        if needs_full_timing:
            reading = (
                "Zero here is partly built in: this line was placed at the fastest "
                "verified caregiver, so treat it as consistent, not as proof."
            )
        elif hits == 0 and online_hits == 0:
            reading = "This pattern has not shown up in either study."
        elif hits == 0:
            reading = (
                "It never fired on a verified caregiver, and it fired on "
                f"{online_hits:,} online sign-ups."
            )
        else:
            reading = f"This pattern showed up in {hits} of {total} people we know are real."

        if hits == 0:
            advice = "Strong enough to act on with a quick spot check"
        elif online_hits == 0:
            advice = (
                "Review this check. It has never fired on an online sign-up, "
                "only on people we know are real"
            )
        else:
            advice = "Use it to sort the queue, never on its own"

        rows.append(
            {
                "Pattern we look for": CHECK_PLAIN_NAMES[rule],
                "Where it shows up": CHECK_SURVEY_AREAS[rule],
                "How seriously we treat it": CHECK_SEVERITY[rule],
                "Verified caregivers this check could fire on": total,
                "Verified caregivers it fired on": hits,
                "What that tells us": reading,
                "How to use it": advice,
            }
        )
    table = pd.DataFrame(rows)
    return table.sort_values(
        "Verified caregivers it fired on", ascending=False
    ).reset_index(drop=True)


# ── 4b. What the two groups actually answered, side by side ─────────────────

# One real rating block from the questionnaire, used for the worked example of
# the repeated-answer check.  Every item here is answered on the same 1-to-5
# scale, and this is the exact block the published check reads, so the spread
# worked out below reproduces the published line of 0.82 exactly.
RATING_BLOCK_FIELDS: list[str] = [
    "tfa_angry",
    "tfa_comfortable",
    "tfa_disgusted",
    "tfa_future",
    "tfa_happy",
    "tfa_mult_kids_odds",
    "tfa_sad",
    "tfa_scared",
    "tfa_surprised",
    "tfa_test_today",
    "tfa_trusting",
]

RATING_BLOCK_LABELS: dict[str, str] = {
    "tfa_angry": "Angry",
    "tfa_comfortable": "Comfortable",
    "tfa_disgusted": "Disgusted",
    "tfa_future": "How sure the test would need to be",
    "tfa_happy": "Happy",
    "tfa_mult_kids_odds": "Odds a younger sibling is also autistic",
    "tfa_sad": "Sad",
    "tfa_scared": "Scared",
    "tfa_surprised": "Surprised",
    "tfa_test_today": "When you would want the test done",
    "tfa_trusting": "Trusting",
}

RATING_BLOCK_SPREAD_LIMIT = 0.82  # published line for this block, rounded for display

SECTION_TIME_LABELS: dict[str, str] = {
    "get_time_fif": "Family Information",
    "get_time_val": "Values",
    "get_time_tfa": "Thoughts, Feelings, and Attitudes",
    "get_time_demo": "Demographics",
}


def _typical_verified_id(
    screen_inputs: pd.DataFrame,
    cache_dir: Path,
    column: str = "Survey minutes",
) -> str:
    """The verified caregiver sitting closest to the middle of their own group.

    Picked by rule rather than by hand so the comparison cannot be accused of
    reaching for a flattering example.  The record must have all four sections
    timed and every question in the rating block answered, otherwise the
    side-by-side comparison would have blanks in it.
    """
    verified = screen_inputs[
        screen_inputs["Group"].eq("Caregivers we verified") & screen_inputs["Timed end to end"]
    ].set_index("record_id")

    records = load_combined_records(cache_dir).copy()
    records["record_id"] = records["record_id"].astype(str)
    answers = records[records["source_project"].eq("clean_4797")].set_index("record_id")
    complete = answers[RATING_BLOCK_FIELDS].apply(pd.to_numeric, errors="coerce").notna().all(axis=1)

    values = verified[column].dropna()
    eligible = values.loc[[i for i in values.index if complete.get(i, False)]]
    middle = values.median()
    return str((eligible - middle).abs().idxmin())


def build_answer_comparison(
    screen_inputs: pd.DataFrame,
    cache_dir: Path,
    flagged_id: str = "296",
    verified_id: Optional[str] = None,
) -> pd.DataFrame:
    """The same eleven questions, answered by one flagged response and one caregiver.

    ``flagged_id`` defaults to the online sign-up with the flattest answers in
    this block.  ``verified_id`` defaults to the verified caregiver whose whole
    survey time is closest to the verified median.
    """
    records = load_combined_records(cache_dir).copy()
    records["record_id"] = records["record_id"].astype(str)
    online = records[records["source_project"].eq("dirty_4581")].set_index("record_id")
    verified_records = records[records["source_project"].eq("clean_4797")].set_index("record_id")

    if verified_id is None:
        verified_id = _typical_verified_id(screen_inputs, cache_dir)

    flagged_answers = pd.to_numeric(online.loc[flagged_id, RATING_BLOCK_FIELDS], errors="coerce")
    verified_answers = pd.to_numeric(
        verified_records.loc[verified_id, RATING_BLOCK_FIELDS], errors="coerce"
    )

    table = pd.DataFrame(
        {
            "Question in this block": [RATING_BLOCK_LABELS[f] for f in RATING_BLOCK_FIELDS],
            f"Online sign-up {flagged_id} answered": flagged_answers.values,
            f"Verified caregiver {verified_id} answered": verified_answers.values,
        }
    )
    table.attrs["flagged_id"] = flagged_id
    table.attrs["verified_id"] = verified_id
    table.attrs["flagged_spread"] = float(flagged_answers.std(ddof=1))
    table.attrs["verified_spread"] = float(verified_answers.std(ddof=1))
    table.attrs["limit"] = RATING_BLOCK_SPREAD_LIMIT
    return table


def plot_answer_comparison(comparison: pd.DataFrame) -> plt.Figure:
    """Two columns of real answers, drawn on the same 1-to-5 scale."""
    flagged_id = comparison.attrs["flagged_id"]
    verified_id = comparison.attrs["verified_id"]
    flagged_column = f"Online sign-up {flagged_id} answered"
    verified_column = f"Verified caregiver {verified_id} answered"

    labels = comparison["Question in this block"]
    y = np.arange(len(labels))[::-1]
    height = 0.38

    fig, ax = plt.subplots(figsize=(11.5, 6.2))
    ax.barh(
        y + height / 2,
        comparison[verified_column],
        height,
        color=STUDY_COLORS["Caregivers we verified"],
        label=(
            f"Verified caregiver {verified_id}: answers spread by "
            f"{comparison.attrs['verified_spread']:.2f}"
        ),
    )
    ax.barh(
        y - height / 2,
        comparison[flagged_column],
        height,
        color=STUDY_COLORS["Online sign-ups"],
        label=(
            f"Online sign-up {flagged_id}: answers spread by "
            f"{comparison.attrs['flagged_spread']:.2f}"
        ),
    )
    for offset, column in ((height / 2, verified_column), (-height / 2, flagged_column)):
        for index, value in zip(y, comparison[column]):
            if pd.notna(value):
                ax.text(value + 0.07, index + offset, f"{int(value)}", va="center", fontsize=9)

    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_xlim(0, 5.6)
    ax.set_xticks([1, 2, 3, 4, 5])
    ax.set_xlabel("Answer given (1 = not at all, 5 = a lot)")
    ax.set_title("The same eleven questions, as each response actually answered them")
    ax.legend(
        frameon=False,
        fontsize=10,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.13),
        ncol=1,
    )
    _apply_chart_style(ax)
    fig.tight_layout()
    return fig


def build_section_time_comparison(
    screen_inputs: pd.DataFrame,
    output_dir: Path,
    cache_dir: Optional[Path] = None,
    flagged_id: str = "1026",
    verified_id: Optional[str] = None,
) -> pd.DataFrame:
    """Section-by-section minutes for one flagged response and one caregiver."""
    if cache_dir is None:
        cache_dir = output_dir.parent / "data_cache"

    definitions = load_rule_definitions(output_dir).set_index("rule")
    floors = json.loads(str(definitions.loc["R3", "threshold"]))
    floor_lookup = {
        "get_time_fif": float(floors["feat_time_fif"]),
        "get_time_val": float(floors["feat_time_val"]),
        "get_time_tfa": float(floors["feat_time_tfa"]),
        "get_time_demo": float(floors["feat_time_demo"]),
    }

    records = load_combined_records(cache_dir).copy()
    records["record_id"] = records["record_id"].astype(str)
    for column in TIME_FIELDS:
        records[column] = pd.to_numeric(records[column], errors="coerce")
    online = records[records["source_project"].eq("dirty_4581")].set_index("record_id")
    verified_records = records[records["source_project"].eq("clean_4797")].set_index("record_id")

    if verified_id is None:
        verified_id = _typical_verified_id(screen_inputs, cache_dir)

    rows = []
    for column in TIME_FIELDS:
        flagged_value = float(online.loc[flagged_id, column])
        verified_value = float(verified_records.loc[verified_id, column])
        rows.append(
            {
                "Survey section": SECTION_TIME_LABELS[column],
                f"Online sign-up {flagged_id} (minutes)": round(flagged_value, 2),
                f"Verified caregiver {verified_id} (minutes)": round(verified_value, 2),
                "Rushed-section line (minutes)": round(floor_lookup[column], 2),
                "Was this section rushed": "Yes" if flagged_value < floor_lookup[column] else "No",
            }
        )

    table = pd.DataFrame(rows)
    flagged_total = float(online.loc[flagged_id, TIME_FIELDS].sum())
    verified_total = float(verified_records.loc[verified_id, TIME_FIELDS].sum())
    table.loc[len(table)] = {
        "Survey section": "Whole survey",
        f"Online sign-up {flagged_id} (minutes)": round(flagged_total, 2),
        f"Verified caregiver {verified_id} (minutes)": round(verified_total, 2),
        "Rushed-section line (minutes)": TOTAL_TIME_LIMIT_MIN,
        "Was this section rushed": "Yes" if flagged_total < TOTAL_TIME_LIMIT_MIN else "No",
    }
    table.attrs["flagged_id"] = flagged_id
    table.attrs["verified_id"] = verified_id
    return table


def plot_section_time_comparison(comparison: pd.DataFrame) -> plt.Figure:
    """Section times for the two responses, with the rushed-section line marked."""
    flagged_id = comparison.attrs["flagged_id"]
    verified_id = comparison.attrs["verified_id"]
    flagged_column = f"Online sign-up {flagged_id} (minutes)"
    verified_column = f"Verified caregiver {verified_id} (minutes)"

    sections = comparison[comparison["Survey section"].ne("Whole survey")]
    labels = sections["Survey section"]
    x = np.arange(len(labels))
    width = 0.36

    fig, ax = plt.subplots(figsize=(11.5, 5.8))
    ax.bar(
        x - width / 2,
        sections[verified_column],
        width,
        color=STUDY_COLORS["Caregivers we verified"],
        label=f"Verified caregiver {verified_id}",
    )
    ax.bar(
        x + width / 2,
        sections[flagged_column],
        width,
        color=STUDY_COLORS["Online sign-ups"],
        label=f"Online sign-up {flagged_id}",
    )
    tallest = max(sections[verified_column].max(), sections[flagged_column].max())
    for offset, column in ((-width / 2, verified_column), (width / 2, flagged_column)):
        for index, value in zip(x, sections[column]):
            ax.text(
                index + offset,
                value + tallest * 0.035,
                f"{value:g}",
                ha="center",
                fontsize=10,
                fontweight="bold",
            )

    for index, floor in zip(x, sections["Rushed-section line (minutes)"]):
        ax.plot(
            [index - 0.46, index + 0.46],
            [floor, floor],
            color="#A85D75",
            linestyle="--",
            linewidth=2,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_xlabel("Survey section")
    ax.set_ylabel("Minutes spent on the section")
    ax.set_ylim(0, tallest * 1.28)
    ax.set_title("Minutes spent on each section, against the rushed-section line")
    handles, legend_labels = ax.get_legend_handles_labels()
    handles.append(plt.Line2D([0], [0], color="#A85D75", linestyle="--", linewidth=2))
    legend_labels.append("Rushed-section line for that section")
    ax.legend(handles, legend_labels, frameon=False, fontsize=10, loc="upper left")
    _apply_chart_style(ax)
    fig.tight_layout()
    return fig


def build_family_contradiction_example(
    output_dir: Path,
    cache_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """The child-count contradiction, shown as the answers the response gave.

    This is the easiest Family Information contradiction to read: the response
    says how many children it has, then ticks more age bands than it has
    children.  One child sits in exactly one age band, so the two answers
    cannot both be true.
    """
    if cache_dir is None:
        cache_dir = output_dir.parent / "data_cache"

    audit = load_branching_audit(output_dir)
    audit = audit[audit["family"].eq("child_age_band_count_exceeds_child_count")].copy()
    if audit.empty:
        return pd.DataFrame()
    audit["record_id"] = audit["record_id"].astype(str)

    band_labels = {
        "fif_childrens_ages___1": "Under 1 year",
        "fif_childrens_ages___2": "1 to 2 years",
        "fif_childrens_ages___3": "3 to 5 years",
        "fif_childrens_ages___4": "6 to 10 years",
        "fif_childrens_ages___5": "11 to 14 years",
        "fif_childrens_ages___6": "15 to 17 years",
        "fif_childrens_ages___7": "18 years or older",
    }

    rows = []
    for record_id in audit["record_id"].head(4):
        observed = json.loads(audit.loc[audit["record_id"].eq(record_id), "observed_values"].iloc[0])
        children = int(float(observed["fif_num_children"]))
        ticked = [
            label for field, label in band_labels.items() if str(observed.get(field, "0")) == "1"
        ]
        rows.append(
            {
                "Response": record_id,
                "Children reported": children,
                "Age bands ticked": len(ticked),
                "Which age bands": ", ".join(ticked),
                "Why this cannot be true": (
                    f"{len(ticked)} age bands need at least {len(ticked)} children, "
                    f"but the response reported {children}"
                ),
            }
        )
    return pd.DataFrame(rows)


# ── 5. How one response becomes one decision ────────────────────────────────

def build_decision_rule_table() -> pd.DataFrame:
    """The whole decision rule on one small page."""
    rows = []
    for rule in SERIOUS_RULES + SUPPORTING_RULES:
        rows.append(
            {
                "What we look for": CHECK_PLAIN_NAMES[rule],
                "Where it appears": CHECK_SURVEY_AREAS[rule],
                "How seriously we treat it": CHECK_SEVERITY[rule],
                "How it affects payment": (
                    "Any one of these on its own moves the response to review"
                    if CHECK_SEVERITY[rule] == "Serious"
                    else "Two or more of these together move the response to review"
                ),
            }
        )
    return pd.DataFrame(rows)


def _format_short_time(value: object) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return "not timed"
    total_seconds = int(round(float(numeric) * 60))
    minutes, seconds = divmod(total_seconds, 60)
    if minutes == 0:
        return f"{seconds} sec"
    return f"{minutes}m {seconds:02d}s"


def _plural_count(value: object, singular: str, plural: Optional[str] = None) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        noun = plural or f"{singular}s"
        return f"{noun} not answered"
    count = int(numeric)
    word = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {word}"


def _count_phrase(count: int, singular: str, plural: str) -> str:
    word = singular if count == 1 else plural
    return f"{count} {word}"


def _branching_note(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "Family Information answers lined up."

    family = str(rows[0].get("family", ""))
    if family == "prenatal_not_tested_reason_without_prenatal_no":
        return "Marked prenatal screening as yes, but still gave reasons for not doing prenatal screening."
    if family == "prenatal_testing_mutually_exclusive_pair":
        return "Filled prenatal follow-up items that should never appear together."
    if family == "prenatal_tested_reason_without_prenatal_yes":
        return "Gave prenatal testing reasons without first marking prenatal screening as yes."
    if family == "earlier_diagnosis_yes_reason_without_yes_gate":
        return "Gave earlier-diagnosis reasons without first marking that earlier diagnosis happened."
    if family == "earlier_diagnosis_no_reason_without_no_gate":
        return "Gave earlier-diagnosis reasons from the no-branch without first marking that branch."
    if family == "child_age_band_count_exceeds_child_count":
        return "Ticked more child age bands than the number of children it reported."
    return "Family Information follow-up answers did not match the earlier item."


def _family_snapshot(record: Optional[pd.Series], branching_rows: list[dict[str, object]]) -> str:
    if record is None:
        return _branching_note(branching_rows)

    pieces = [
        _plural_count(record.get("fif_num_children"), "child", "children"),
        _plural_count(record.get("fif_num_autistic"), "autistic child", "autistic children"),
    ]
    pregnancy = str(record.get("fif_pregnant", "")).strip()
    if pregnancy == "1":
        pieces.append("pregnancy item marked yes")
    elif pregnancy == "0":
        pieces.append("pregnancy item marked no")
    pieces.append(_branching_note(branching_rows))
    return "; ".join(pieces)


def _tfa_snapshot(record: Optional[pd.Series], low_variation: bool) -> str:
    if record is None:
        return "Questionnaire timing and answer-pattern details are shown in the next column."

    item_labels = [
        ("happy", "tfa_happy"),
        ("scared", "tfa_scared"),
        ("difficult", "tfa_difficult"),
        ("doable", "tfa_doable"),
    ]
    item_bits = []
    for label, column in item_labels:
        value = str(record.get(column, "")).strip()
        if value:
            item_bits.append(f"{label}={value}")

    if item_bits:
        summary = "Example item ratings: " + ", ".join(item_bits)
    else:
        summary = "Example item ratings were recorded"

    if low_variation:
        return summary + "; answers changed very little across one rating block."
    return summary + "; answers moved across the scale rather than repeating one option."


def _section_rush_text(record: Optional[pd.Series], section_limits: dict[str, float]) -> Optional[str]:
    if record is None:
        return None

    section_labels = {
        "get_time_fif": "Family Information",
        "get_time_val": "Values",
        "get_time_tfa": "Thoughts/Feelings/Attitudes",
        "get_time_demo": "Demographics",
    }
    limit_lookup = {
        "get_time_fif": float(section_limits["feat_time_fif"]),
        "get_time_val": float(section_limits["feat_time_val"]),
        "get_time_tfa": float(section_limits["feat_time_tfa"]),
        "get_time_demo": float(section_limits["feat_time_demo"]),
    }
    hits = []
    for column, label in section_labels.items():
        value = pd.to_numeric(pd.Series([record.get(column)]), errors="coerce").iloc[0]
        if pd.notna(value) and value < limit_lookup[column]:
            hits.append(
                f"{label} { _format_short_time(value) } versus { _format_short_time(limit_lookup[column]) }"
            )
    if not hits:
        return None
    return "Section below the verified-caregiver floor: " + "; ".join(hits)


def _timing_snapshot(
    record: pd.Series,
    details: Optional[pd.Series],
    section_limits: dict[str, float],
) -> str:
    pieces = [
        f"Whole survey { _format_short_time(record['Survey minutes']) }",
        f"Thoughts/Feelings/Attitudes { _format_short_time(record['Attitudes minutes']) }",
    ]
    rush_text = _section_rush_text(details, section_limits)
    if rush_text:
        pieces.append(rush_text)
    if bool(record["rule_R6"]):
        pieces.append("Arrived inside a 1-minute cluster of sign-ups")
    return "; ".join(pieces)


def _decision_summary(record: pd.Series) -> str:
    serious = int(record["Serious checks"])
    supporting = int(record["Supporting checks"])
    decision = str(record["Payment Decision"])
    if decision == "Cleared for payment now":
        return "Nothing in the timings or answer pattern stood out, so this response stays in the pay-now group."
    if decision == "Low-risk provisional approval":
        return "Only one milder pattern showed up, so this response needs just a quick check before payment."
    if decision == "Confirmed bot / reject":
        return "Very fast timing plus answer-pattern or Family Information problems put this response in the strongest concern group."
    if serious and supporting:
        serious_text = _count_phrase(serious, "strong pattern", "strong patterns")
        supporting_text = _count_phrase(supporting, "supporting pattern", "supporting patterns")
        return f"{serious_text} and {supporting_text} showed up, so this response is held for review."
    if serious:
        serious_text = _count_phrase(serious, "strong pattern", "strong patterns")
        return f"{serious_text} showed up, so this response is held for review."
    supporting_text = _count_phrase(supporting, "milder pattern", "milder patterns")
    return f"{supporting_text} showed up together, so this response is held for review."


def build_worked_examples(
    screen_inputs: pd.DataFrame,
    output_dir: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    record_ids: Optional[list[str]] = None,
    verified_id: Optional[str] = None,
) -> pd.DataFrame:
    """Real responses walked end to end through the decision rule.

    The first row is a verified caregiver, included as the control: without it
    the table shows only online sign-ups and there is nothing to read the four
    flagged responses against.
    """
    if record_ids is None:
        record_ids = ["1779", "1243", "1276", "1355", "1026"]

    online = screen_inputs[screen_inputs["Group"].eq("Online sign-ups")].set_index("record_id")
    verified = screen_inputs[screen_inputs["Group"].eq("Caregivers we verified")].set_index("record_id")
    detail_lookup: Optional[pd.DataFrame] = None
    section_limits = {
        "feat_time_fif": 0.0,
        "feat_time_val": 0.0,
        "feat_time_tfa": 0.0,
        "feat_time_demo": 0.0,
    }
    branching_lookup: dict[str, list[dict[str, object]]] = {}

    if output_dir is not None:
        if cache_dir is None:
            cache_dir = output_dir.parent / "data_cache"
        definitions = load_rule_definitions(output_dir).set_index("rule")
        section_limits = json.loads(str(definitions.loc["R3", "threshold"]))
        details = load_combined_records(cache_dir)
        details = details.loc[details["source_project"].eq("dirty_4581")].copy()
        details["record_id"] = details["record_id"].astype(str)
        detail_lookup = details.set_index("record_id")
        branching = load_branching_audit(output_dir)
        branching = branching.loc[branching["source_project"].eq("dirty_4581")].copy()
        if not branching.empty:
            branching_lookup = (
                branching.groupby("record_id")
                .apply(lambda frame: frame.to_dict("records"), include_groups=False)
                .to_dict()
            )

    verified_detail: Optional[pd.DataFrame] = None
    if output_dir is not None and cache_dir is not None:
        if verified_id is None:
            verified_id = _typical_verified_id(screen_inputs, cache_dir)
        clean = load_combined_records(cache_dir)
        clean = clean.loc[clean["source_project"].eq("clean_4797")].copy()
        clean["record_id"] = clean["record_id"].astype(str)
        verified_detail = clean.set_index("record_id")

    rows = []
    if verified_detail is not None and verified_id in verified.index:
        control = verified.loc[verified_id]
        control_details = verified_detail.loc[verified_id]
        rows.append(
            {
                "Response": f"{verified_id} (verified caregiver)",
                "Family Information": _family_snapshot(control_details, []),
                "Thoughts, Feelings, and Attitudes": _tfa_snapshot(
                    control_details, bool(control["rule_R4"])
                ),
                "Timing and arrival": _timing_snapshot(control, control_details, section_limits),
                "Why this response landed here": (
                    "This is the control row. It is a caregiver we verified, so it shows what "
                    "the same four sections look like when the response is known to be real."
                ),
                "Decision": control["Payment Decision"],
            }
        )

    for record_id in record_ids:
        record = online.loc[record_id]
        details = detail_lookup.loc[record_id] if detail_lookup is not None and record_id in detail_lookup.index else None
        branching_rows = branching_lookup.get(record_id, [])
        rows.append(
            {
                "Response": record_id,
                "Family Information": _family_snapshot(details, branching_rows),
                "Thoughts, Feelings, and Attitudes": _tfa_snapshot(details, bool(record["rule_R4"])),
                "Timing and arrival": _timing_snapshot(record, details, section_limits),
                "Why this response landed here": _decision_summary(record),
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
    out = pd.DataFrame({"Number of patterns on one response": table.index})
    for group in STUDY_COLORS:
        share = table[group] / table[group].sum() * 100
        out[group] = table[group].values
        out[f"{group} (% of group)"] = share.round(1).values
    return out


def plot_checks_set_off(screen_inputs: pd.DataFrame) -> plt.Figure:
    """Grouped bars showing two checks is the ceiling for verified caregivers."""
    table = build_checks_set_off_table(screen_inputs)
    x = np.arange(len(table))
    width = 0.38

    fig, ax = plt.subplots(figsize=(11.5, 6))
    for offset, (group, colour) in zip((-width / 2, width / 2), STUDY_COLORS.items()):
        total = table[group].sum()
        share = table[f"{group} (% of group)"]
        bars = ax.bar(x + offset, share, width, color=colour, label=f"{group} ({total:,})")
        # Both numbers are shown because a zero bar is the point of this chart.
        for bar, pct, count in zip(bars, share, table[group]):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 1.5,
                f"{pct:.1f}%\n{int(count):,}",
                ha="center",
                fontsize=9,
                fontweight="bold",
            )

    ceiling = 2
    past = int(
        screen_inputs[
            screen_inputs["Group"].eq("Online sign-ups")
            & screen_inputs["Checks set off"].gt(ceiling)
        ].shape[0]
    )
    online_total = int(screen_inputs["Group"].eq("Online sign-ups").sum())
    ax.axvline(ceiling + 0.5, color="#A85D75", linestyle="--", linewidth=2)
    ax.text(
        ceiling + 0.68,
        76,
        "No verified caregiver set off\nmore than two checks.\n"
        f"{past:,} online sign-ups ({past / online_total * 100:.1f}%)\nare past this line.",
        color="#A85D75",
        fontsize=10,
        fontweight="bold",
        va="top",
    )

    ax.set_xticks(x)
    ax.set_xticklabels(table["Number of patterns on one response"])
    ax.set_xlabel("Number of unusual patterns found on one response")
    ax.set_ylabel("Share of that group (%)")
    ax.set_ylim(0, 100)
    ax.set_title("How many unusual patterns each response showed")
    ax.legend(frameon=False, fontsize=10, loc="upper right")
    _apply_chart_style(ax)
    fig.tight_layout()
    return fig


# ── 7. When the responses arrived ───────────────────────────────────────────

# The burst check asks how close together sign-ups arrived, so these buckets
# measure the gap to the previous sign-up.  A per-day bar chart cannot answer
# that question: 100 verified caregivers also arrived on a single day, spread
# calmly across it, and a daily chart makes that look like the same behaviour.
ARRIVAL_GAP_BUCKETS = [
    ("Under 1 minute", 0.0, 60.0),
    ("1 to 5 minutes", 60.0, 300.0),
    ("5 to 30 minutes", 300.0, 1800.0),
    ("30 minutes or more", 1800.0, float("inf")),
]


def _arrival_gaps(frame: pd.DataFrame) -> pd.Series:
    """Seconds between each sign-up and the one before it, within one study."""
    stamps = frame["Arrived"].dropna().sort_values()
    gaps = stamps.diff().dt.total_seconds().dropna()
    return gaps[gaps.ge(0)]


def build_arrival_gap_table(screen_inputs: pd.DataFrame) -> pd.DataFrame:
    """How long each sign-up waited behind the previous one, in both studies."""
    rows = []
    gaps_by_group = {
        group: _arrival_gaps(screen_inputs[screen_inputs["Group"].eq(group)])
        for group in STUDY_COLORS
    }
    for label, low, high in ARRIVAL_GAP_BUCKETS:
        row = {"Gap to the sign-up before it": label}
        for group, gaps in gaps_by_group.items():
            hits = int(((gaps >= low) & (gaps < high)).sum())
            row[f"{group} (count)"] = hits
            row[f"{group} (% of group)"] = round(hits / len(gaps) * 100, 1)
        rows.append(row)

    return pd.DataFrame(rows)


def build_arrival_tables(screen_inputs: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Arrival-gap buckets, daily counts, and what the surge day produced."""
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
    online_gaps = _arrival_gaps(online)
    verified_gaps = _arrival_gaps(verified)

    return {
        "gap_table": build_arrival_gap_table(screen_inputs),
        "online_by_day": online_frame,
        "verified_by_day": verified_frame,
        "surge_crosstab": crosstab,
        "surge_facts": {
            "surge_day": surge_day,
            "surge_n": int(len(surge)),
            "online_n": int(len(online)),
            "busiest_hours": busiest,
            "busiest_n": int(busiest.sum()),
            "median_gap_seconds": float(online_gaps.median()),
            "verified_median_gap_minutes": float(verified_gaps.median() / 60),
            "online_under_minute_pct": float(online_gaps.lt(60).mean() * 100),
            "verified_under_minute_pct": float(verified_gaps.lt(60).mean() * 100),
        },
    }


def plot_arrival_pattern(screen_inputs: pd.DataFrame) -> plt.Figure:
    """Gap to the previous sign-up, which is exactly what the burst check reads."""
    table = build_arrival_gap_table(screen_inputs)
    labels = table["Gap to the sign-up before it"]
    x = np.arange(len(labels))
    width = 0.38

    fig, ax = plt.subplots(figsize=(11.5, 5.6))
    for offset, (group, colour) in zip((-width / 2, width / 2), STUDY_COLORS.items()):
        share = table[f"{group} (% of group)"]
        counts = table[f"{group} (count)"]
        total = int(counts.sum())
        bars = ax.bar(x + offset, share, width, color=colour, label=f"{group} ({total:,} gaps)")
        for bar, pct, count in zip(bars, share, counts):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 1.5,
                f"{pct:.1f}%\n{int(count):,}",
                ha="center",
                fontsize=9,
                fontweight="bold",
            )

    ax.axvspan(-0.5, 0.5, color="#A85D75", alpha=0.10, zorder=0)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Gap between a sign-up and the one before it")
    ax.set_ylabel("Share of that group's sign-ups (%)")
    ax.set_ylim(0, 118)
    ax.set_title("How closely sign-ups followed one another")
    handles, legend_labels = ax.get_legend_handles_labels()
    handles.append(plt.Rectangle((0, 0), 1, 1, color="#A85D75", alpha=0.30))
    legend_labels.append("Shaded: the range the burst check reads")
    ax.legend(handles, legend_labels, frameon=False, fontsize=10, loc="upper right")
    _apply_chart_style(ax)
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
                "Pattern": CHECK_PLAIN_NAMES[rule],
                "Where it shows up": CHECK_SURVEY_AREAS[rule],
                "How seriously we treat it": CHECK_SEVERITY[rule],
                "Online sign-ups showing it": fired,
                "Times it was the only issue on the response": only_concern,
                "Responses released if we removed it": released,
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values("Online sign-ups showing it", ascending=False)
        .reset_index(drop=True)
    )


def plot_check_impact(impact: pd.DataFrame) -> plt.Figure:
    """Three side-by-side bars per pattern.  Never stacked, because the three
    numbers overlap: a response can be counted in more than one of them."""
    frame = impact.iloc[::-1]
    y = np.arange(len(frame))
    height = 0.26

    series = [
        (height, "Online sign-ups showing it", "#C67C2D", "Responses showing the pattern"),
        (
            0.0,
            "Responses released if we removed it",
            "#B08A2E",
            "Responses that would be released if this check were switched off",
        ),
        (
            -height,
            "Times it was the only issue on the response",
            "#1F5A7A",
            "Responses where this was the only pattern found",
        ),
    ]

    fig, ax = plt.subplots(figsize=(12, 6.6))
    widest = frame["Online sign-ups showing it"].max()
    for offset, column, colour, label in series:
        ax.barh(y + offset, frame[column], height, color=colour, label=label)
        for index, value in enumerate(frame[column]):
            ax.text(
                value + widest * 0.012,
                index + offset,
                f"{int(value):,}",
                va="center",
                fontsize=9,
            )

    labels = [
        f"{name}\n({severity.lower()})"
        for name, severity in zip(frame["Pattern"], frame["How seriously we treat it"])
    ]
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlabel("Online sign-ups")
    ax.set_xlim(0, widest * 1.14)
    ax.set_title("Which patterns are doing most of the holding")
    ax.legend(frameon=False, fontsize=10, loc="lower right")
    _apply_chart_style(ax)
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
    """Every check and how often it fired, as a share of each group.

    Counts alone cannot be compared here: there are 1,779 online sign-ups and
    177 verified caregivers, so the shares are what make the two columns read
    against each other.
    """
    online = screen_inputs[screen_inputs["Group"].eq("Online sign-ups")]
    verified = screen_inputs[screen_inputs["Group"].eq("Caregivers we verified")]

    rows = []
    for rule in SHARED_RULES:
        online_hits = int(online[rule].sum())
        verified_hits = int(verified[rule].sum())
        rows.append(
            {
                "Pattern": CHECK_PLAIN_NAMES[rule],
                "Where it shows up": CHECK_SURVEY_AREAS[rule],
                "How seriously we treat it": CHECK_SEVERITY[rule],
                f"Online sign-ups showing it (of {len(online):,})": online_hits,
                "Share of online sign-ups (%)": round(online_hits / len(online) * 100, 1),
                f"Verified caregivers showing it (of {len(verified):,})": verified_hits,
                "Share of verified caregivers (%)": round(verified_hits / len(verified) * 100, 1),
            }
        )
    table = pd.DataFrame(rows)
    return table.sort_values("Share of online sign-ups (%)", ascending=False).reset_index(drop=True)


def plot_all_checks_frequency(screen_inputs: pd.DataFrame) -> plt.Figure:
    """Side-by-side shares so the two groups can be read against each other.

    Plotting online counts on their own invites the wrong comparison, because
    the online sample is ten times larger.  Shares put both groups on one
    scale, and the gap between the pair of bars is the evidence.
    """
    table = build_all_checks_frequency_table(screen_inputs).iloc[::-1]
    online_column = "Share of online sign-ups (%)"
    verified_column = "Share of verified caregivers (%)"
    online_count = [c for c in table.columns if c.startswith("Online sign-ups showing")][0]
    verified_count = [c for c in table.columns if c.startswith("Verified caregivers showing")][0]

    y = np.arange(len(table))
    height = 0.38

    fig, ax = plt.subplots(figsize=(12, 6.4))
    ax.barh(
        y + height / 2,
        table[verified_column],
        height,
        color=STUDY_COLORS["Caregivers we verified"],
        label="Caregivers we verified",
    )
    ax.barh(
        y - height / 2,
        table[online_column],
        height,
        color=STUDY_COLORS["Online sign-ups"],
        label="Online sign-ups",
    )
    for offset, share_column, count_column in (
        (height / 2, verified_column, verified_count),
        (-height / 2, online_column, online_count),
    ):
        for index, (share, count) in enumerate(zip(table[share_column], table[count_column])):
            ax.text(
                share + 1.5,
                index + offset,
                f"{share:.1f}%  ({int(count):,})",
                va="center",
                fontsize=9,
            )

    labels = [
        f"{name}\n({severity.lower()})"
        for name, severity in zip(table["Pattern"], table["How seriously we treat it"])
    ]
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlabel("Share of that group showing the pattern (%)")
    ax.set_xlim(0, 118)
    ax.set_title("How often each pattern showed up, in each group")
    ax.legend(frameon=False, fontsize=10, loc="lower right")
    _apply_chart_style(ax)
    fig.tight_layout()
    return fig


# ══════════════════════════════════════════════════════════════════════════
# Weighted risk score, spot checks, and the master record export
#
# Everything below turns the pass/fail checks above into a points total per
# response, adds the checks the study team asked for after the last meeting
# (a maybe-rushed band, email address, and time of day), and writes one
# spreadsheet that lists every response with the exact checks it broke.
# ══════════════════════════════════════════════════════════════════════════

REDCAP_CONTENTS = ("record", "metadata", "instrument")

# Time of day treated as overnight, in the survey's own clock.
OVERNIGHT_START_HOUR = 0
OVERNIGHT_END_HOUR = 5

# Wider arrival window used only for the spot-check list, never for points.
# The bar is set at ten because no verified caregiver arrived in a cluster
# that dense, so anything at or above it separates the two studies cleanly.
WIDE_BURST_SECONDS = 120
WIDE_BURST_MIN_OTHERS = 10

# Bootstrap settings for the uncertainty band around each time limit.
TIME_BAND_REPEATS = 4000
TIME_BAND_SEED = 20260725

SECTION_TIME_LABELS: dict[str, str] = {
    "get_time_fif": "Family Information minutes",
    "get_time_val": "Values minutes",
    "get_time_tfa": "Thoughts and feelings minutes",
    "get_time_demo": "Demographics minutes",
}

SECTION_TIMESTAMP_FIELDS: dict[str, str] = {
    "get_time_fif": "family_information_form_timestamp",
    "get_time_val": "values_timestamp",
    "get_time_tfa": "tfa_timestamp",
    "get_time_demo": "demographics_timestamp",
}

# Every scored check, in the order a reader should meet them.  "Serious" is
# worth 2 points and "Mild" is worth 1, exactly as agreed in the meeting.
SCORED_CHECKS: list[dict[str, object]] = [
    {
        "key": "check_survey_definitely_rushed",
        "name": "Whole survey finished faster than any verified caregiver",
        "weight": 2,
        "severity": "Serious",
        "area": "Whole survey timing",
        "source": "rule_R1",
    },
    {
        "key": "check_survey_possibly_rushed",
        "name": "Whole survey time sits inside the uncertainty band",
        "weight": 1,
        "severity": "Mild",
        "area": "Whole survey timing",
        "source": "uncertainty band",
    },
    {
        "key": "check_attitudes_definitely_rushed",
        "name": "Thoughts and feelings section faster than any verified caregiver",
        "weight": 2,
        "severity": "Serious",
        "area": "Thoughts and feelings timing",
        "source": "rule_R2",
    },
    {
        "key": "check_attitudes_possibly_rushed",
        "name": "Thoughts and feelings time sits inside the uncertainty band",
        "weight": 1,
        "severity": "Mild",
        "area": "Thoughts and feelings timing",
        "source": "uncertainty band",
    },
    {
        "key": "check_section_rushed",
        "name": "One of the four sections finished below its own speed line",
        "weight": 1,
        "severity": "Mild",
        "area": "Section timing",
        "source": "rule_R3",
    },
    {
        "key": "check_repeated_answers",
        "name": "The same answer repeated down a rating block",
        "weight": 1,
        "severity": "Mild",
        "area": "Rating blocks",
        "source": "rule_R4",
    },
    {
        "key": "check_identical_answer_sheet",
        "name": "Answer sheet identical to another response",
        "weight": 2,
        "severity": "Serious",
        "area": "Whole questionnaire",
        "source": "rule_R5",
    },
    {
        "key": "check_burst_arrival",
        "name": "Arrived within a minute of two or more other sign-ups",
        "weight": 1,
        "severity": "Mild",
        "area": "Arrival time",
        "source": "rule_R6",
    },
    {
        "key": "check_duplicate_comment",
        "name": "Written comment nearly identical to another response",
        "weight": 1,
        "severity": "Mild",
        "area": "Open text",
        "source": "rule_R7",
    },
    {
        "key": "check_family_contradiction",
        "name": "Family answers contradict each other",
        "weight": 2,
        "severity": "Serious",
        "area": "Family Information",
        "source": "rule_R8",
    },
    {
        "key": "check_impossible_demographics",
        "name": "Age and location cannot both be true",
        "weight": 2,
        "severity": "Serious",
        "area": "Demographics",
        "source": "rule_R9",
    },
    {
        "key": "check_throwaway_email",
        "name": "Throwaway or temporary email address",
        "weight": 2,
        "severity": "Serious",
        "area": "Email address",
        "source": "email domain list",
    },
    {
        "key": "check_no_email",
        "name": "No email address on a finished form",
        "weight": 1,
        "severity": "Mild",
        "area": "Email address",
        "source": "email address field",
    },
    {
        "key": "check_overnight",
        "name": "Started between midnight and five in the morning",
        "weight": 1,
        "severity": "Mild",
        "area": "Arrival time",
        "source": "sign-up timestamp",
    },
]

CHECK_KEYS = [str(check["key"]) for check in SCORED_CHECKS]
CHECK_NAMES = {str(c["key"]): str(c["name"]) for c in SCORED_CHECKS}
CHECK_WEIGHTS = {str(c["key"]): int(c["weight"]) for c in SCORED_CHECKS}
CHECK_SEVERITY_BY_KEY = {str(c["key"]): str(c["severity"]) for c in SCORED_CHECKS}

# Checks that describe a contradiction rather than a pace.  A university email
# address never clears one of these on its own.
CONTRADICTION_CHECKS = [
    "check_identical_answer_sheet",
    "check_family_contradiction",
    "check_impossible_demographics",
    "check_throwaway_email",
]

REJECT_SCORE = 3          # score at or above this is a do-not-pay
REVIEW_SCORE = 1          # score at or above this, but below reject, is a hand check

ACTION_PAY = "Pay now"
ACTION_REVIEW = "Check by hand"
ACTION_REJECT = "Do not pay"
ACTION_ORDER = [ACTION_PAY, ACTION_REVIEW, ACTION_REJECT]


# ── Pulling both studies from the survey system ─────────────────────────────

def refresh_redcap_cache(
    project_dir: Path,
    cache_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """Pull answers, field list, and section list for both studies.

    A dated copy that already exists is reused after its checksum is verified,
    so running the notebook twice on the same day makes no extra calls.
    """
    import hashlib
    from datetime import datetime, timezone

    import requests
    from dotenv import load_dotenv

    if cache_dir is None:
        cache_dir = project_dir / "data_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    config = load_config(project_dir)["redcap"]
    load_dotenv(project_dir.parents[1] / ".env", override=False)
    api_url = os.environ.get(config["api_url_env"], "").strip()
    if not api_url:
        raise RuntimeError(f"Missing {config['api_url_env']} in the repository .env file.")

    date_text = datetime.now(timezone.utc).date().isoformat()
    pulled_at = datetime.now(timezone.utc).isoformat()

    def file_digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    rows: list[dict[str, object]] = []
    for source_name, source_config in config["projects"].items():
        token = os.environ.get(source_config["token_env"], "").strip()
        if not token:
            raise RuntimeError(
                f"Missing {source_config['token_env']} in the repository .env file. "
                "Access keys are never written into the code."
            )
        project_id = int(source_config["project_id"])
        for content in REDCAP_CONTENTS:
            target = cache_dir / f"{project_id}_{content}_{date_text}.parquet"
            sidecar = target.with_suffix(target.suffix + ".sha256")
            if target.exists() and sidecar.exists():
                saved = json.loads(sidecar.read_text(encoding="utf-8"))
                if saved.get("sha256") != file_digest(target):
                    raise RuntimeError(f"Saved copy does not match its checksum: {target.name}")
                frame = pd.read_parquet(target)
                origin = "Saved copy from today, checksum verified"
            else:
                payload = {
                    "token": token,
                    "content": content,
                    "format": "json",
                    "returnFormat": "json",
                }
                if content == "record":
                    payload.update(
                        {
                            "type": "flat",
                            "rawOrLabel": "raw",
                            "rawOrLabelHeaders": "raw",
                            "exportCheckboxLabel": "false",
                            "exportSurveyFields": "true",
                            "exportDataAccessGroups": "false",
                        }
                    )
                response = requests.post(
                    api_url, data=payload, timeout=int(config["request_timeout_seconds"])
                )
                response.raise_for_status()
                frame = pd.DataFrame(response.json())
                if frame.empty:
                    raise RuntimeError(f"The survey system returned no {content} rows for {project_id}.")
                frame.to_parquet(target, index=False)
                sidecar.write_text(
                    json.dumps(
                        {"file": target.name, "sha256": file_digest(target), "pulled_at_utc": pulled_at},
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                origin = "Pulled live today"
            rows.append(
                {
                    "Study": PROJECT_LABELS[source_name],
                    "Project number": project_id,
                    "What was pulled": {
                        "record": "Every answer, including survey timing fields",
                        "metadata": "Field list and branching rules",
                        "instrument": "Section list",
                    }[content],
                    "Rows": len(frame),
                    "Where it came from": origin,
                    "Saved as": target.name,
                }
            )
    return pd.DataFrame(rows)


def check_redcap_audit_access(project_dir: Path) -> pd.DataFrame:
    """Ask the survey system for its audit trail and report what came back.

    The meeting asked us to work from the survey system's own logs.  This
    reports plainly whether the read-only keys can reach them, so the answer
    is on the page rather than in someone's memory.
    """
    import requests
    from dotenv import load_dotenv

    config = load_config(project_dir)["redcap"]
    load_dotenv(project_dir.parents[1] / ".env", override=False)
    api_url = os.environ.get(config["api_url_env"], "").strip()

    requests_to_make = [
        ("Audit trail (who changed what, and when)", "log"),
        ("Survey invitation list", "participantList"),
    ]
    rows: list[dict[str, object]] = []
    for source_name, source_config in config["projects"].items():
        token = os.environ.get(source_config["token_env"], "").strip()
        for label, content in requests_to_make:
            payload = {"token": token, "content": content, "format": "json", "returnFormat": "json"}
            if content == "participantList":
                payload["instrument"] = "eligibility"
            try:
                response = requests.post(api_url, data=payload, timeout=60)
                if response.status_code == 200:
                    outcome = "Available"
                    detail = f"{len(response.json()):,} rows returned"
                else:
                    outcome = "Not available to this key"
                    detail = str(response.json().get("error", response.text))[:160]
            except Exception as error:  # noqa: BLE001 - reported, never raised
                outcome = "Could not be reached"
                detail = str(error)[:160]
            rows.append(
                {
                    "Study": PROJECT_LABELS[source_name],
                    "What we asked for": label,
                    "Result": outcome,
                    "What came back": detail,
                }
            )
    return pd.DataFrame(rows)


# ── Timing, read straight from the survey system's own fields ───────────────

def build_timing_detail(cache_dir: Path) -> pd.DataFrame:
    """Per-response timing, taken from the survey system's timing fields.

    The survey records how long each of the four sections took and stamps the
    moment each section was handed in.  Those stamps are the per-response
    audit trail we can reach with a read-only key, so they carry the work the
    system-wide audit log would otherwise do.
    """
    records = load_combined_records(cache_dir).copy()
    records["record_id"] = records["record_id"].astype(str)

    frame = records[["source_project", "record_id"]].copy()
    frame["Started"] = pd.to_datetime(records["eligibility_timestamp"], errors="coerce")

    for field, label in SECTION_TIME_LABELS.items():
        frame[label] = pd.to_numeric(records[field], errors="coerce").round(2)

    stamps = pd.DataFrame(
        {
            field: pd.to_datetime(records[column], errors="coerce")
            for field, column in SECTION_TIMESTAMP_FIELDS.items()
        }
    )
    frame["Last section handed in"] = stamps.max(axis=1)
    frame["Sections with a recorded time"] = (
        records[list(SECTION_TIME_LABELS)].apply(pd.to_numeric, errors="coerce").notna().sum(axis=1)
    )
    frame["Whole survey minutes"] = (
        records[list(SECTION_TIME_LABELS)]
        .apply(pd.to_numeric, errors="coerce")
        .sum(axis=1, min_count=len(SECTION_TIME_LABELS))
        .round(2)
    )
    minutes_open = (frame["Last section handed in"] - frame["Started"]).dt.total_seconds() / 60
    frame["Minutes from sign-up to last section"] = minutes_open.round(2)
    frame["Started at hour"] = frame["Started"].dt.hour
    frame["Day started"] = frame["Started"].dt.date
    return frame


TIMING_PREVIEW_COLUMNS = [
    "Study",
    "Record ID",
    "Started",
    "Last section handed in",
    "Family Information minutes",
    "Values minutes",
    "Thoughts and feelings minutes",
    "Demographics minutes",
    "Whole survey minutes",
    "Sections with a recorded time",
]


def build_timing_preview(timing_detail: pd.DataFrame, rows: int = 6) -> pd.DataFrame:
    """The timing record with reader-facing column names, for a short look."""
    frame = timing_detail.copy()
    frame["Study"] = frame["source_project"].map(PROJECT_LABELS)
    frame["Record ID"] = frame["record_id"]
    return frame[TIMING_PREVIEW_COLUMNS].head(rows).reset_index(drop=True)


def build_time_limit_bands(
    output_dir: Path,
    cache_dir: Optional[Path] = None,
    repeats: int = TIME_BAND_REPEATS,
    seed: int = TIME_BAND_SEED,
) -> pd.DataFrame:
    """Put an uncertainty band around every time limit.

    Each limit was read off 131 verified caregivers.  A different 131 real
    caregivers would have produced a slightly different limit.  Drawing 131
    caregivers back out of that group at random, thousands of times over,
    shows how far the limit could reasonably move.  Below the band is
    definitely rushed.  Inside the band is possibly rushed.
    """
    if cache_dir is None:
        cache_dir = output_dir.parent / "data_cache"

    records = load_combined_records(cache_dir).copy()
    for field in TIME_FIELDS:
        records[field] = pd.to_numeric(records[field], errors="coerce")
    totals = records[TIME_FIELDS].sum(axis=1, min_count=len(TIME_FIELDS))
    verified = records[records["source_project"].eq("clean_4797") & totals.notna()].copy()
    verified["_total"] = verified[TIME_FIELDS].sum(axis=1)

    generator = np.random.default_rng(seed)

    def band(values: pd.Series, statistic) -> tuple[float, float]:
        clean = values.dropna().to_numpy(dtype=float)
        draws = generator.integers(0, len(clean), size=(repeats, len(clean)))
        spread = statistic(clean[draws])
        return float(np.percentile(spread, 2.5)), float(np.percentile(spread, 97.5))

    smallest = lambda block: block.min(axis=1)  # noqa: E731
    percentile = lambda block: np.quantile(block, 0.01, axis=1)  # noqa: E731

    definitions = load_rule_definitions(output_dir).set_index("rule")
    section_floors = json.loads(str(definitions.loc["R3", "threshold"]))

    specs = [
        ("Whole survey", "_total", "Fastest verified caregiver", smallest, TOTAL_TIME_LIMIT_MIN),
        ("Thoughts and feelings section", "get_time_tfa", "Fastest verified caregiver", smallest, ATTITUDES_TIME_LIMIT_MIN),
        ("Family Information section", "get_time_fif", "Slowest 1 in 100 verified caregivers", percentile, section_floors["feat_time_fif"]),
        ("Values section", "get_time_val", "Slowest 1 in 100 verified caregivers", percentile, section_floors["feat_time_val"]),
        ("Demographics section", "get_time_demo", "Slowest 1 in 100 verified caregivers", percentile, section_floors["feat_time_demo"]),
    ]

    rows = []
    for label, column, method, statistic, published in specs:
        low, high = band(verified[column], statistic)
        rows.append(
            {
                "Part of the survey": label,
                "How the limit was set": method,
                "Limit in use (minutes)": round(float(published), 2),
                "Limit in use": _minutes_to_words(float(published)),
                "Band runs from (minutes)": round(low, 2),
                "Band runs to (minutes)": round(high, 2),
                "Below the band": "Definitely rushed",
                "Inside the band": "Possibly rushed",
            }
        )
    table = pd.DataFrame(rows)
    table.attrs["verified_n"] = int(len(verified))
    table.attrs["repeats"] = int(repeats)
    table.attrs["survey_band"] = (
        float(table.loc[0, "Band runs from (minutes)"]),
        float(table.loc[0, "Band runs to (minutes)"]),
    )
    table.attrs["attitudes_band"] = (
        float(table.loc[1, "Band runs from (minutes)"]),
        float(table.loc[1, "Band runs to (minutes)"]),
    )
    return table


# ── Email address and time of day ───────────────────────────────────────────

def _email_series(records: pd.DataFrame) -> pd.Series:
    """The best available email address for each response, lower-cased."""
    columns = [c for c in ("demo_email", "email_elig") if c in records.columns]
    result = pd.Series("", index=records.index, dtype="object")
    for column in columns:
        value = records[column].astype(str).str.strip().str.lower()
        value = value.where(~value.isin({"nan", "none", "<na>"}), "")
        result = result.where(result.ne(""), value)
    return result


def _email_domain(emails: pd.Series) -> pd.Series:
    return emails.str.extract(r"@([^@\s]+)$", expand=False).fillna("")


# ── The master record table ─────────────────────────────────────────────────

def build_risk_table(
    output_dir: Path,
    cache_dir: Optional[Path] = None,
    project_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """One row per response, with every check, its points, and what to do.

    This is the table the meeting asked for.  It carries the record number,
    the timing, a yes or no for every check, the exact checks that were
    broken, the points those checks add up to, and the recommended action.
    """
    if cache_dir is None:
        cache_dir = output_dir.parent / "data_cache"
    if project_dir is None:
        project_dir = output_dir.parent

    screen = build_screen_inputs(output_dir, cache_dir)
    timing = build_timing_detail(cache_dir)
    bands = build_time_limit_bands(output_dir, cache_dir)
    survey_low, survey_high = bands.attrs["survey_band"]
    attitudes_low, attitudes_high = bands.attrs["attitudes_band"]

    records = load_combined_records(cache_dir).copy()
    records["record_id"] = records["record_id"].astype(str)
    emails = _email_series(records)
    finished = pd.to_numeric(records.get("demographics_complete"), errors="coerce").fillna(0)
    email_frame = pd.DataFrame(
        {
            "source_project": records["source_project"],
            "record_id": records["record_id"],
            "Email address": emails,
            "Email domain": _email_domain(emails),
            "_finished_demographics": finished.eq(2),
        }
    )

    frame = (
        screen.merge(timing, on=["source_project", "record_id"], how="left", validate="one_to_one")
        .merge(email_frame, on=["source_project", "record_id"], how="left", validate="one_to_one")
    )

    throwaway = {
        str(domain).strip().lower()
        for domain in load_config(project_dir)["fraud_rules"].get("disposable_email_domains", [])
    }

    # ── The fourteen scored checks ──────────────────────────────────────────
    frame["check_survey_definitely_rushed"] = frame["rule_R1"].astype(bool)
    frame["check_survey_possibly_rushed"] = (
        frame["Timed end to end"]
        & frame["Survey minutes"].ge(survey_low)
        & frame["Survey minutes"].lt(survey_high)
    )
    frame["check_attitudes_definitely_rushed"] = frame["rule_R2"].astype(bool)
    frame["check_attitudes_possibly_rushed"] = (
        frame["Attitudes minutes"].notna()
        & frame["Attitudes minutes"].ge(attitudes_low)
        & frame["Attitudes minutes"].lt(attitudes_high)
    )
    frame["check_section_rushed"] = frame["rule_R3"].astype(bool)
    frame["check_repeated_answers"] = frame["rule_R4"].astype(bool)
    frame["check_identical_answer_sheet"] = frame["rule_R5"].astype(bool)
    frame["check_burst_arrival"] = frame["rule_R6"].astype(bool)
    frame["check_duplicate_comment"] = frame["rule_R7"].astype(bool)
    frame["check_family_contradiction"] = frame["rule_R8"].astype(bool)
    frame["check_impossible_demographics"] = frame["rule_R9"].astype(bool)
    frame["check_throwaway_email"] = frame["Email domain"].isin(throwaway)
    frame["check_no_email"] = frame["_finished_demographics"] & frame["Email address"].eq("")
    frame["check_overnight"] = frame["Started at hour"].between(
        OVERNIGHT_START_HOUR, OVERNIGHT_END_HOUR - 1
    ).fillna(False)

    for key in CHECK_KEYS:
        frame[key] = frame[key].fillna(False).astype(bool)

    # ── Points, count, and the exact list of what was broken ────────────────
    frame["Risk score"] = sum(frame[key].astype(int) * CHECK_WEIGHTS[key] for key in CHECK_KEYS)
    frame["Checks broken"] = frame[CHECK_KEYS].sum(axis=1).astype(int)
    frame["Serious checks broken"] = frame[
        [k for k in CHECK_KEYS if CHECK_SEVERITY_BY_KEY[k] == "Serious"]
    ].sum(axis=1).astype(int)
    frame["Mild checks broken"] = frame[
        [k for k in CHECK_KEYS if CHECK_SEVERITY_BY_KEY[k] == "Mild"]
    ].sum(axis=1).astype(int)

    broken_matrix = frame[CHECK_KEYS].to_numpy()
    names = np.array([CHECK_NAMES[key] for key in CHECK_KEYS])
    weights = np.array([CHECK_WEIGHTS[key] for key in CHECK_KEYS])
    frame["Which checks were broken"] = [
        "; ".join(names[row]) if row.any() else "No checks broken" for row in broken_matrix
    ]
    frame["Points from each check"] = [
        "; ".join(f"{n} ({w} point{'s' if w != 1 else ''})" for n, w in zip(names[row], weights[row]))
        if row.any()
        else "No checks broken"
        for row in broken_matrix
    ]

    # ── Timing verdicts in words ────────────────────────────────────────────
    def verdict(values: pd.Series, low: float, high: float, gate: pd.Series) -> pd.Series:
        out = pd.Series("Normal", index=values.index, dtype="object")
        out[values.ge(low) & values.lt(high)] = "Possibly rushed"
        out[values.lt(low)] = "Definitely rushed"
        out[~gate | values.isna()] = "No time recorded"
        return out

    frame["Whole survey speed"] = verdict(
        frame["Survey minutes"], survey_low, survey_high, frame["Timed end to end"]
    )
    frame["Thoughts and feelings speed"] = verdict(
        frame["Attitudes minutes"], attitudes_low, attitudes_high, frame["Attitudes minutes"].notna()
    )

    # ── Spot checks ─────────────────────────────────────────────────────────
    frame["University email address"] = np.where(
        frame["Email domain"].str.endswith(".edu"), "Yes", "No"
    )
    frame["Started overnight"] = np.where(frame["check_overnight"], "Yes", "No")
    frame["Sign-ups within two minutes"] = _neighbours_within(frame, WIDE_BURST_SECONDS)
    frame["Arrived in a tight cluster"] = np.where(
        frame["Sign-ups within two minutes"].ge(WIDE_BURST_MIN_OTHERS), "Yes", "No"
    )
    domain_counts = frame.loc[frame["Email domain"].ne(""), "Email domain"].value_counts()
    rare_domains = set(domain_counts[domain_counts.eq(1)].index)
    frame["Email domain seen only once"] = np.where(
        frame["Email domain"].isin(rare_domains), "Yes", "No"
    )

    # ── Recommended action ──────────────────────────────────────────────────
    action = pd.Series(ACTION_PAY, index=frame.index, dtype="object")
    action[frame["Risk score"].ge(REVIEW_SCORE)] = ACTION_REVIEW
    action[frame["Risk score"].ge(REJECT_SCORE)] = ACTION_REJECT
    frame["Action before email check"] = action

    has_contradiction = frame[CONTRADICTION_CHECKS].any(axis=1)
    cleared_by_email = (
        frame["University email address"].eq("Yes")
        & ~has_contradiction
        & action.ne(ACTION_PAY)
    )
    frame["Cleared by university email"] = np.where(cleared_by_email, "Yes", "No")
    frame["Recommended action"] = np.where(cleared_by_email, ACTION_PAY, action)

    reason = pd.Series("", index=frame.index, dtype="object")
    reason[frame["Recommended action"].eq(ACTION_PAY)] = "No checks broken"
    reason[frame["Checks broken"].gt(0) & frame["Recommended action"].eq(ACTION_PAY)] = (
        "Points were added, but a university email address cleared it"
    )
    reason[frame["Recommended action"].eq(ACTION_REVIEW)] = (
        "One or two points, which is not enough to refuse payment on its own"
    )
    reason[frame["Recommended action"].eq(ACTION_REJECT)] = (
        f"{REJECT_SCORE} points or more"
    )
    frame["Why this action"] = reason

    confirmed = set(load_confirmed_bots(output_dir)["Record ID"].astype(str))
    frame["Named in the earlier confirmed list"] = np.where(
        frame["source_project"].eq("dirty_4581") & frame["record_id"].isin(confirmed), "Yes", "No"
    )

    frame["Record ID"] = frame["record_id"]
    return frame


def _neighbours_within(frame: pd.DataFrame, seconds: int) -> pd.Series:
    """How many other sign-ups in the same study landed within N seconds."""
    counts = pd.Series(0, index=frame.index, dtype=int)
    for _, block in frame.groupby("source_project"):
        arrived = block["Started"].dropna().sort_values()
        if arrived.empty:
            continue
        stamps = arrived.to_numpy(dtype="datetime64[s]").astype("int64")
        window = int(seconds)
        left = np.searchsorted(stamps, stamps - window, side="left")
        right = np.searchsorted(stamps, stamps + window, side="right")
        counts.loc[arrived.index] = (right - left) - 1
    return counts


# ── Reader-facing summaries of the score ────────────────────────────────────

MASTER_EXPORT_COLUMNS = [
    "Study",
    "Record ID",
    "Email address",
    "University email address",
    "Started",
    "Last section handed in",
    "Started overnight",
    "Family Information minutes",
    "Values minutes",
    "Thoughts and feelings minutes",
    "Demographics minutes",
    "Whole survey minutes",
    "Sections with a recorded time",
    "Minutes from sign-up to last section",
    "Whole survey speed",
    "Thoughts and feelings speed",
]

MASTER_TAIL_COLUMNS = [
    "Which checks were broken",
    "Points from each check",
    "Checks broken",
    "Serious checks broken",
    "Mild checks broken",
    "Risk score",
    "Recommended action",
    "Why this action",
    "Review plan",
    "Why this plan",
    "Final plan",
    "Cleared by university email",
    "Sign-ups within two minutes",
    "Arrived in a tight cluster",
    "Email domain seen only once",
    "Named in the earlier confirmed list",
]


def build_master_export(risk_table: pd.DataFrame) -> pd.DataFrame:
    """The full record list, in the column order the meeting asked for."""
    frame = risk_table.copy()
    if "Review plan" not in frame.columns:
        frame = build_review_triage(frame)
    for key in CHECK_KEYS:
        frame[CHECK_NAMES[key]] = np.where(frame[key], "Yes", "No")
    check_columns = [CHECK_NAMES[key] for key in CHECK_KEYS]
    ordered = MASTER_EXPORT_COLUMNS + check_columns + MASTER_TAIL_COLUMNS
    export = frame[ordered].copy()
    for column in ("Started", "Last section handed in"):
        export[column] = pd.to_datetime(export[column], errors="coerce").dt.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    export = export.sort_values(
        ["Study", "Risk score", "Record ID"], ascending=[True, False, True]
    ).reset_index(drop=True)
    return export


def build_action_summary_table(risk_table: pd.DataFrame) -> pd.DataFrame:
    """How many responses land in each action, by study."""
    counts = pd.crosstab(risk_table["Recommended action"], risk_table["Group"])
    counts = counts.reindex(ACTION_ORDER).fillna(0).astype(int)
    counts = counts.reindex(
        columns=["Caregivers we verified", "Online sign-ups"], fill_value=0
    )
    table = counts.reset_index()
    table.columns = ["Recommended action", "Verified caregivers", "Online sign-ups"]
    table["All responses"] = table["Verified caregivers"] + table["Online sign-ups"]
    total = int(table["All responses"].sum())
    table["Share of all responses"] = (table["All responses"] / total * 100).round(1).astype(str) + "%"
    table["What this means"] = table["Recommended action"].map(
        {
            ACTION_PAY: "No points. Send the gift card.",
            ACTION_REVIEW: "One or two points. A person should look before paying.",
            ACTION_REJECT: f"{REJECT_SCORE} points or more. Refuse payment.",
        }
    )
    return table


def build_score_distribution_table(risk_table: pd.DataFrame) -> pd.DataFrame:
    """Every points total, and how many responses sit on it."""
    counts = pd.crosstab(risk_table["Risk score"], risk_table["Group"])
    counts = counts.reindex(
        columns=["Caregivers we verified", "Online sign-ups"], fill_value=0
    )
    table = counts.reset_index()
    table.columns = ["Risk score", "Verified caregivers", "Online sign-ups"]
    table["All responses"] = table["Verified caregivers"] + table["Online sign-ups"]
    table["Recommended action"] = np.where(
        table["Risk score"] >= REJECT_SCORE,
        ACTION_REJECT,
        np.where(table["Risk score"] >= REVIEW_SCORE, ACTION_REVIEW, ACTION_PAY),
    )
    return table


def build_check_weight_table() -> pd.DataFrame:
    """The full list of checks, what each is worth, and where it comes from."""
    rows = []
    for check in SCORED_CHECKS:
        rows.append(
            {
                "Check": check["name"],
                "Where in the survey": check["area"],
                "How seriously we treat it": check["severity"],
                "Points it adds": check["weight"],
                "Where the line came from": {
                    "rule_R1": "Fastest of the 131 verified caregivers",
                    "rule_R2": "Fastest of the 131 verified caregivers",
                    "rule_R3": "Slowest 1 in 100 verified caregivers, section by section",
                    "rule_R4": "Slowest 1 in 100 verified caregivers, block by block",
                    "rule_R5": "Exact match on the full answer sheet",
                    "rule_R6": "Sixty seconds, set by hand, not read off the caregivers",
                    "rule_R7": "Written comments at least 90 percent alike",
                    "rule_R8": "Two answers on the same page that cannot both be true",
                    "rule_R9": "Age and location that cannot both be true",
                    "uncertainty band": "Redrawing the verified caregivers 4,000 times over",
                    "email domain list": "Throwaway email services named in the settings file",
                    "email address field": "Finished form with the email box left empty",
                    "sign-up timestamp": "Sign-up between midnight and five in the morning",
                }[str(check["source"])],
            }
        )
    table = pd.DataFrame(rows)
    return table


def build_check_frequency_table(risk_table: pd.DataFrame) -> pd.DataFrame:
    """How often each check fired in each group, counts before shares."""
    verified = risk_table[risk_table["Group"].eq("Caregivers we verified")]
    online = risk_table[risk_table["Group"].eq("Online sign-ups")]
    rows = []
    for key in CHECK_KEYS:
        verified_hits = int(verified[key].sum())
        online_hits = int(online[key].sum())
        rows.append(
            {
                "Check": CHECK_NAMES[key],
                "How seriously we treat it": CHECK_SEVERITY_BY_KEY[key],
                "Points it adds": CHECK_WEIGHTS[key],
                "Verified caregivers": f"{verified_hits} of {len(verified):,}",
                "Online sign-ups": f"{online_hits:,} of {len(online):,}",
                "Share of verified caregivers": f"{verified_hits / len(verified) * 100:.1f}%",
                "Share of online sign-ups": f"{online_hits / len(online) * 100:.1f}%",
            }
        )
    return pd.DataFrame(rows)


def build_score_simulation_table(risk_table: pd.DataFrame) -> pd.DataFrame:
    """What happens to both groups if the refusal line moves.

    The verified caregivers are people we know are real, so anything the line
    catches there is a response we would have refused by mistake.

    These counts are points only.  The university email rule is applied after
    the points are totalled, so at the line in use this table can name one or
    two more responses than the action summary does.
    """
    verified = risk_table[risk_table["Group"].eq("Caregivers we verified")]
    online = risk_table[risk_table["Group"].eq("Online sign-ups")]
    rows = []
    for cut in range(1, 8):
        refused_online = int((online["Risk score"] >= cut).sum())
        refused_verified = int((verified["Risk score"] >= cut).sum())
        rows.append(
            {
                "Refuse payment at this many points or more": cut,
                "Online sign-ups refused": f"{refused_online:,}",
                "Share of online sign-ups refused": f"{refused_online / len(online) * 100:.1f}%",
                "Verified caregivers refused by mistake": refused_verified,
                "Share of verified caregivers refused by mistake": f"{refused_verified / len(verified) * 100:.1f}%",
                "In use": "Yes" if cut == REJECT_SCORE else "",
            }
        )
    return pd.DataFrame(rows)


def build_time_simulation_table(risk_table: pd.DataFrame) -> pd.DataFrame:
    """What the whole-survey time limit catches as it moves."""
    verified = risk_table[
        risk_table["Group"].eq("Caregivers we verified") & risk_table["Timed end to end"]
    ]
    online = risk_table[
        risk_table["Group"].eq("Online sign-ups") & risk_table["Timed end to end"]
    ]
    rows = []
    for limit in (8, 10, TOTAL_TIME_LIMIT_MIN, 13.2, 15, 18, 20):
        caught_online = int((online["Survey minutes"] < limit).sum())
        caught_verified = int((verified["Survey minutes"] < limit).sum())
        rows.append(
            {
                "Whole survey limit (minutes)": round(float(limit), 2),
                "Whole survey limit": _minutes_to_words(float(limit)),
                "Online sign-ups below it": f"{caught_online:,} of {len(online):,}",
                "Verified caregivers below it": f"{caught_verified} of {len(verified):,}",
                "Note": (
                    "The limit in use"
                    if abs(limit - TOTAL_TIME_LIMIT_MIN) < 0.01
                    else "Top of the uncertainty band"
                    if abs(limit - 13.2) < 0.01
                    else ""
                ),
            }
        )
    return pd.DataFrame(rows)


def build_spot_check_table(risk_table: pd.DataFrame) -> pd.DataFrame:
    """The four quick eyeball lists the meeting asked for, as counts."""
    online = risk_table[risk_table["Group"].eq("Online sign-ups")]
    rows = [
        {
            "Spot check": "University email address",
            "What it is": "The email ends in .edu, so a real person almost certainly holds it",
            "Online sign-ups": int(online["University email address"].eq("Yes").sum()),
            "All responses": int(risk_table["University email address"].eq("Yes").sum()),
            "How we use it": "Clears the response for payment unless a contradiction check fired",
        },
        {
            "Spot check": "Started overnight",
            "What it is": "Sign-up between midnight and five in the morning",
            "Online sign-ups": int(online["Started overnight"].eq("Yes").sum()),
            "All responses": int(risk_table["Started overnight"].eq("Yes").sum()),
            "How we use it": "Adds one point",
        },
        {
            "Spot check": "Arrived in a tight cluster",
            "What it is": "Ten or more other sign-ups landed within two minutes",
            "Online sign-ups": int(online["Arrived in a tight cluster"].eq("Yes").sum()),
            "All responses": int(risk_table["Arrived in a tight cluster"].eq("Yes").sum()),
            "How we use it": "Listed for eyeballing only, adds no points",
        },
        {
            "Spot check": "Email domain seen only once",
            "What it is": "No other response used that email provider",
            "Online sign-ups": int(online["Email domain seen only once"].eq("Yes").sum()),
            "All responses": int(risk_table["Email domain seen only once"].eq("Yes").sum()),
            "How we use it": "Listed for eyeballing only, adds no points",
        },
    ]
    return pd.DataFrame(rows)


def mask_email(address: str) -> str:
    """Show the shape of an address without printing it in full.

    Anything displayed in the notebook is saved inside the notebook file, and
    that file is kept in version control.  Full addresses belong only in the
    spreadsheet, which is not.
    """
    text = str(address or "")
    if "@" not in text:
        return ""
    name, _, domain = text.partition("@")
    head = name[0] if name else ""
    return f"{head}***@{domain}"


def build_university_email_list(
    risk_table: pd.DataFrame,
    show_full_address: bool = False,
) -> pd.DataFrame:
    """Every response with a university email address, for quick verification."""
    columns = [
        "Study",
        "Record ID",
        "Email address",
        "Whole survey minutes",
        "Which checks were broken",
        "Risk score",
        "Action before email check",
        "Recommended action",
        "Cleared by university email",
    ]
    frame = risk_table[risk_table["University email address"].eq("Yes")][columns].copy()
    if not show_full_address:
        frame["Email address"] = frame["Email address"].map(mask_email)
    return frame.sort_values(["Study", "Risk score"], ascending=[True, False]).reset_index(drop=True)


def build_email_override_effect(risk_table: pd.DataFrame) -> pd.DataFrame:
    """What the university email rule actually changed."""
    holds_university = risk_table["University email address"].eq("Yes")
    would_hold = risk_table["Action before email check"].ne(ACTION_PAY)
    rows = [
        {
            "Question": "Responses with a university email address",
            "Count": int(holds_university.sum()),
        },
        {
            "Question": "Of those, how many the points alone would have held",
            "Count": int((holds_university & would_hold).sum()),
        },
        {
            "Question": "Of those, how many the university email rule cleared",
            "Count": int(risk_table["Cleared by university email"].eq("Yes").sum()),
        },
        {
            "Question": "Of those, how many stayed held because a contradiction check fired",
            "Count": int(
                (holds_university & would_hold & risk_table["Cleared by university email"].eq("No")).sum()
            ),
        },
    ]
    return pd.DataFrame(rows)


def build_false_alarm_summary(risk_table: pd.DataFrame) -> pd.DataFrame:
    """What the score does to people we already know are real."""
    verified = risk_table[risk_table["Group"].eq("Caregivers we verified")]
    counts = verified["Recommended action"].value_counts().reindex(ACTION_ORDER).fillna(0).astype(int)
    table = counts.reset_index()
    table.columns = ["Recommended action", "Verified caregivers"]
    table["Share of the 177 verified caregivers"] = (
        table["Verified caregivers"] / len(verified) * 100
    ).round(1).astype(str) + "%"
    return table


def build_wrongly_refused_detail(risk_table: pd.DataFrame) -> pd.DataFrame:
    """The verified caregivers the score would refuse, named and explained."""
    columns = [
        "Record ID",
        "Whole survey minutes",
        "Thoughts and feelings minutes",
        "Whole survey speed",
        "Thoughts and feelings speed",
        "Which checks were broken",
        "Risk score",
        "Recommended action",
    ]
    frame = risk_table[
        risk_table["Group"].eq("Caregivers we verified")
        & risk_table["Recommended action"].eq(ACTION_REJECT)
    ][columns]
    return frame.sort_values("Risk score", ascending=False).reset_index(drop=True)


def build_top_reasons_table(risk_table: pd.DataFrame) -> pd.DataFrame:
    """The check combinations that account for the most refused responses."""
    refused = risk_table[
        risk_table["Group"].eq("Online sign-ups")
        & risk_table["Recommended action"].eq(ACTION_REJECT)
    ]
    counts = refused["Which checks were broken"].value_counts().head(12)
    table = counts.reset_index()
    table.columns = ["Checks broken together", "Online sign-ups"]
    table["Share of refused online sign-ups"] = (
        table["Online sign-ups"] / len(refused) * 100
    ).round(1).astype(str) + "%"
    return table


# ── The workbook ────────────────────────────────────────────────────────────

MASTER_WORKBOOK_NAME = "ESD_Response_Review_Master.xlsx"

READ_ME_ROWS = [
    ("Read me first", "What this file is, and what each tab holds"),
    ("Summary", "How many responses land in each action, and what the points look like"),
    ("All responses", "Every response, with a yes or no for every check and the points it scored"),
    ("Pay now", "Responses with no points against them"),
    ("Check by hand", "Responses with one or two points, which a person should look at"),
    ("Do not pay", "Responses with three points or more"),
    ("Checks and points", "Every check, what it is worth, and how often it fired in each group"),
    ("Time limits", "The time limits, the uncertainty band around each one, and what they catch"),
    ("If the line moves", "What changes if we refuse payment at a different number of points"),
    ("Spot checks", "University emails, overnight sign-ups, tight arrival clusters, one-off email providers"),
    ("Handling the held pile", "How the check-by-hand responses split, and what each group needs"),
    ("Rule key", "What every rule code means, what it is worth, and how often it fired"),
    ("Rules grid", "One row per response, with a Yes or No column for every rule"),
    ("Rules list", "One row per response, with the rules written into a single cell"),
    ("Read by hand", "The short list a person opens one at a time, and the random sample to work through"),
    ("Where the data came from", "The pull from the survey system, and what its audit trail would give us"),
]


def _write_block(writer, sheet: str, frame: pd.DataFrame, row: int, col: int = 0) -> int:
    frame.to_excel(writer, sheet_name=sheet, index=False, startrow=row, startcol=col)
    return row + len(frame) + 3


def export_master_workbook(
    output_dir: Path,
    cache_dir: Optional[Path] = None,
    project_dir: Optional[Path] = None,
    destination: Optional[Path] = None,
    filename: str = MASTER_WORKBOOK_NAME,
    source_table: Optional[pd.DataFrame] = None,
    audit_table: Optional[pd.DataFrame] = None,
) -> Path:
    """Write the one spreadsheet the meeting asked for.

    The file carries email addresses, so it is written to a folder that is
    kept out of version control.
    """
    if cache_dir is None:
        cache_dir = output_dir.parent / "data_cache"
    if project_dir is None:
        project_dir = output_dir.parent
    if destination is None:
        destination = output_dir / "restricted"
    destination.mkdir(parents=True, exist_ok=True)

    risk_table = build_review_triage(build_risk_table(output_dir, cache_dir, project_dir))
    master = build_master_export(risk_table)

    action_summary = build_action_summary_table(risk_table)
    false_alarms = build_false_alarm_summary(risk_table)
    score_spread = build_score_distribution_table(risk_table)
    top_reasons = build_top_reasons_table(risk_table)
    weights = build_check_weight_table()
    frequency = build_check_frequency_table(risk_table)
    bands = build_time_limit_bands(output_dir, cache_dir).drop(columns=["Limit in use (minutes)"])
    time_simulation = build_time_simulation_table(risk_table)
    score_simulation = build_score_simulation_table(risk_table)
    spot_checks = build_spot_check_table(risk_table)
    university = build_university_email_list(risk_table, show_full_address=True)
    override_effect = build_email_override_effect(risk_table)
    wrongly_refused = build_wrongly_refused_detail(risk_table)
    triage_summary = build_triage_summary(risk_table)
    arrival_by_day = build_arrival_by_day_table(risk_table)
    triage_time = build_triage_time_comparison(risk_table)
    triage_mix = build_triage_check_mix(risk_table)
    final_plan = build_final_plan_summary(risk_table)
    sample_plan = build_sample_plan_table(risk_table)
    read_list = build_read_by_hand_list(risk_table)
    sample_list = build_sample_to_read(risk_table)
    rules_grid = build_rules_grid(risk_table)
    rules_list = build_rules_list(risk_table)
    rule_key = build_rule_key_table(risk_table)
    layout_choice = build_layout_choice_table()
    scale_comparison = build_scale_comparison(risk_table)
    heavy_cutoffs = build_heavy_cutoff_table(risk_table)

    read_me = pd.DataFrame(READ_ME_ROWS, columns=["Tab", "What it holds"])
    action_meaning = pd.DataFrame(
        [
            {
                "Recommended action": ACTION_PAY,
                "Points": "0",
                "What to do": "Send the gift card.",
            },
            {
                "Recommended action": ACTION_REVIEW,
                "Points": "1 or 2",
                "What to do": "A person reads the response before any payment goes out.",
            },
            {
                "Recommended action": ACTION_REJECT,
                "Points": f"{REJECT_SCORE} or more",
                "What to do": "Refuse payment and keep the response out of the analysis.",
            },
        ]
    )
    scoring_rule = pd.DataFrame(
        [
            {"Rule": "A serious check adds 2 points."},
            {"Rule": "A mild check adds 1 point."},
            {"Rule": "The points are added up across every check a response broke."},
            {"Rule": f"{REJECT_SCORE} points or more means do not pay."},
            {"Rule": "1 or 2 points means a person checks it by hand."},
            {"Rule": "0 points means pay now."},
            {
                "Rule": "A university email address clears a held response, unless one of the "
                "contradiction checks fired."
            },
        ]
    )

    excel_path = destination / filename
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        row = _write_block(writer, "Read me first", read_me, 4)
        row = _write_block(writer, "Read me first", action_meaning, row + 1)
        row = _write_block(writer, "Read me first", scoring_rule, row + 1)
        _write_block(writer, "Read me first", build_master_column_guide(master), row + 1)

        row = _write_block(writer, "Summary", action_summary, 4)
        row = _write_block(writer, "Summary", false_alarms, row + 1)
        row = _write_block(writer, "Summary", score_spread, row + 1)
        _write_block(writer, "Summary", top_reasons, row + 1)

        master.to_excel(writer, sheet_name="All responses", index=False, startrow=2)
        for sheet, action in (
            ("Pay now", ACTION_PAY),
            ("Check by hand", ACTION_REVIEW),
            ("Do not pay", ACTION_REJECT),
        ):
            master[master["Recommended action"].eq(action)].to_excel(
                writer, sheet_name=sheet, index=False, startrow=2
            )

        row = _write_block(writer, "Checks and points", weights, 4)
        _write_block(writer, "Checks and points", frequency, row + 1)

        row = _write_block(writer, "Time limits", bands, 4)
        _write_block(writer, "Time limits", time_simulation, row + 1)

        row = _write_block(writer, "If the line moves", score_simulation, 4)
        _write_block(writer, "If the line moves", wrongly_refused, row + 1)

        row = _write_block(writer, "Spot checks", spot_checks, 4)
        row = _write_block(writer, "Spot checks", override_effect, row + 1)
        _write_block(writer, "Spot checks", university, row + 1)

        row = _write_block(writer, "Handling the held pile", triage_summary, 4)
        row = _write_block(writer, "Handling the held pile", arrival_by_day, row + 1)
        row = _write_block(writer, "Handling the held pile", triage_time, row + 1)
        row = _write_block(writer, "Handling the held pile", final_plan, row + 1)
        _write_block(writer, "Handling the held pile", triage_mix, row + 1)

        row = _write_block(writer, "Rule key", rule_key, 4)
        row = _write_block(writer, "Rule key", layout_choice, row + 1)
        row = _write_block(writer, "Rule key", scale_comparison, row + 1)
        _write_block(writer, "Rule key", heavy_cutoffs, row + 1)

        rules_grid.to_excel(writer, sheet_name="Rules grid", index=False, startrow=2)
        rules_list.to_excel(writer, sheet_name="Rules list", index=False, startrow=2)

        row = _write_block(writer, "Read by hand", read_list, 4)
        row = _write_block(writer, "Read by hand", sample_plan, row + 1)
        _write_block(writer, "Read by hand", sample_list, row + 1)

        if source_table is None:
            source_table = pd.DataFrame([{"Note": "No pull record was passed in."}])
        if audit_table is None:
            audit_table = pd.DataFrame([{"Note": "The audit trail was not queried."}])
        row = _write_block(writer, "Where the data came from", source_table, 4)
        _write_block(writer, "Where the data came from", audit_table, row + 1)

        book = writer.book
        headings = {
            "Read me first": (
                "Caregiver survey response review",
                "One row per response, the exact checks each one broke, the points those checks "
                "add up to, and what we recommend doing about it.",
            ),
            "Summary": (
                "Summary",
                "Action groups first, then what the score does to caregivers we already know are "
                "real, then the full spread of points.",
            ),
            "All responses": (
                "All responses",
                "Every response from both studies. Each check has its own yes or no column, and "
                "the checks a response broke are also written out in one cell.",
            ),
            "Pay now": ("Pay now", "No checks broken. Nothing is holding these up."),
            "Check by hand": (
                "Check by hand",
                "One or two points. Not enough to refuse payment on its own.",
            ),
            "Do not pay": (
                "Do not pay",
                f"{REJECT_SCORE} points or more. Read the checks column before acting on any single row.",
            ),
            "Checks and points": (
                "Checks and points",
                "What every check is worth, where its line came from, and how often it fired in "
                "each of the two studies.",
            ),
            "Time limits": (
                "Time limits",
                "Each limit came from the 131 verified caregivers who finished all four sections. "
                "Below the band is definitely rushed. Inside the band is possibly rushed.",
            ),
            "If the line moves": (
                "If the line moves",
                "The cost of moving the refusal line, measured against caregivers we know are real. "
                "These counts are points only, before the university email rule is applied, so at "
                "the line in use they can name one or two more responses than the Summary tab.",
            ),
            "Spot checks": (
                "Spot checks",
                "Quick lists to eyeball. Only the overnight check adds points.",
            ),
            "Handling the held pile": (
                "Handling the held pile",
                "The check-by-hand pile is too big to read one response at a time, so it is "
                "split by the kind of evidence against each response. The arrival check is set "
                "aside when judging one person, because every arrival flag in the study falls "
                "on a single day and 97 of every 100 sign-ups that day carry it.",
            ),
            "Rule key": (
                "Rule key",
                "R1 to R9 are the nine rules the study already names. E1 to E5 were added after "
                "the September review meeting and are kept on a separate letter so nobody "
                "mistakes one for the other. Two scoring scales are carried side by side.",
            ),
            "Rules grid": (
                "Rules grid",
                "One row per response, a Yes or No in every rule column. Use this one to filter "
                "on a single rule or to count how often two rules travel together. Sort or "
                "filter on Response key, never on Record ID alone: record numbers restart in "
                "each study, so 348 of them appear twice.",
            ),
            "Rules list": (
                "Rules list",
                "The same responses with the rules written into one cell, in codes and again in "
                "plain words. Easier to read down a page. Harder to filter on one rule.",
            ),
            "Read by hand": (
                "Read by hand",
                "The short list to open one at a time, then the random sample drawn from the "
                "group that is too large to read in full. Fill in the last three columns as "
                "you go.",
            ),
            "Where the data came from": (
                "Where the data came from",
                "Both studies were pulled from the survey system for this run.",
            ),
        }
        for sheet, (title, note) in headings.items():
            worksheet = book[sheet]
            _style_sheet_heading(worksheet, "A1", title)
            _style_sheet_note(worksheet, "A2", note)

        for sheet in (
            "All responses",
            "Pay now",
            "Check by hand",
            "Do not pay",
            "Rules grid",
            "Rules list",
        ):
            _autosize_sheet(book[sheet], wrap_text=False, freeze_panes="C4", apply_filter=True)
            book[sheet].auto_filter.ref = book[sheet].dimensions
        for sheet in (
            "Read me first",
            "Summary",
            "Checks and points",
            "Time limits",
            "If the line moves",
            "Spot checks",
            "Handling the held pile",
            "Rule key",
            "Read by hand",
            "Where the data came from",
        ):
            _autosize_sheet(book[sheet], wrap_text=True, freeze_panes=None, apply_filter=False)

    return excel_path


NOTEBOOK_PREVIEW_COLUMNS = [
    "Study",
    "Record ID",
    "Whole survey minutes",
    "Whole survey speed",
    "Thoughts and feelings speed",
    "Which checks were broken",
    "Checks broken",
    "Risk score",
    "Recommended action",
]


def build_master_preview(master_export: pd.DataFrame, rows: int = 8) -> pd.DataFrame:
    """A short, address-free look at the spreadsheet, safe to leave on the page."""
    online = master_export[master_export["Study"].eq(PROJECT_LABELS["dirty_4581"])]
    return online[NOTEBOOK_PREVIEW_COLUMNS].head(rows).reset_index(drop=True)


def build_master_column_guide(master_export: pd.DataFrame) -> pd.DataFrame:
    """Name every column in the spreadsheet and say what it holds."""
    descriptions = {
        "Study": "Which of the two studies the response came from",
        "Record ID": "The response number in the survey system",
        "Email address": "The address given on the demographics page, for spot checking",
        "University email address": "Yes when the address ends in .edu",
        "Started": "When the sign-up screen was submitted",
        "Last section handed in": "When the last of the four sections was submitted",
        "Started overnight": "Yes when the sign-up landed between midnight and five in the morning",
        "Family Information minutes": "Minutes spent on the Family Information section",
        "Values minutes": "Minutes spent on the Values section",
        "Thoughts and feelings minutes": "Minutes spent on the Thoughts, Feelings, and Attitudes section",
        "Demographics minutes": "Minutes spent on the Demographics section",
        "Whole survey minutes": "The four section times added together, blank unless all four were timed",
        "Sections with a recorded time": "How many of the four sections have a time at all",
        "Minutes from sign-up to last section": "Clock time between the sign-up and the last section",
        "Whole survey speed": "Definitely rushed, possibly rushed, normal, or no time recorded",
        "Thoughts and feelings speed": "Definitely rushed, possibly rushed, normal, or no time recorded",
        "Which checks were broken": "The exact checks this response broke, written out",
        "Points from each check": "The same list, with the points each check added",
        "Checks broken": "How many checks fired on this response",
        "Serious checks broken": "How many of those were serious",
        "Mild checks broken": "How many of those were mild",
        "Risk score": "The points added up",
        "Recommended action": "Pay now, check by hand, or do not pay",
        "Why this action": "The reason in one line",
        "Review plan": "For the held pile only, which of the five review groups this response falls into",
        "Why this plan": "Why that review group, in one line",
        "Final plan": "The single column to work from: the action, or the review group for held responses",
        "Cleared by university email": "Yes when a .edu address moved a held response to pay now",
        "Sign-ups within two minutes": "How many other sign-ups in the same study landed within two minutes",
        "Arrived in a tight cluster": "Yes when ten or more other sign-ups landed within two minutes",
        "Email domain seen only once": "Yes when no other response used that email provider",
        "Named in the earlier confirmed list": "Yes for the six responses the earlier review already called bots",
    }
    rows = []
    for column in master_export.columns:
        rows.append(
            {
                "Column": column,
                "What it holds": descriptions.get(
                    column, "Yes or No for this one check"
                ),
            }
        )
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════
# Second look at the check-by-hand pile
#
# The points system leaves about a thousand responses in the middle.  Reading
# a thousand responses one at a time is not work anyone can actually do, so
# this section sorts them by the kind of evidence against them and says what
# to do with each group.
# ══════════════════════════════════════════════════════════════════════════

# The arrival check describes the traffic on a given day, not the person who
# filled the survey in.  Every other check describes what one person did.
CHANNEL_CHECKS = ["check_burst_arrival"]
PERSON_CHECKS = [key for key in CHECK_KEYS if key not in CHANNEL_CHECKS]

PLAN_RELEASE_ARRIVAL = "Release, arrival timing only"
PLAN_RELEASE_SLOW = "Release, slower than a typical verified caregiver"
PLAN_READ_ALL = "Read every one by hand"
PLAN_READ_SAMPLE = "Read a random sample by hand"
PLAN_COMPLETION = "Hold until the completion rule is set"
PLAN_PAY = "Pay now"
PLAN_REJECT = "Do not pay"

TRIAGE_ORDER = [
    PLAN_RELEASE_ARRIVAL,
    PLAN_RELEASE_SLOW,
    PLAN_READ_SAMPLE,
    PLAN_READ_ALL,
    PLAN_COMPLETION,
]

TRIAGE_REASONS: dict[str, str] = {
    PLAN_RELEASE_ARRIVAL: (
        "The only check against this response was arrival timing, and that check is about "
        "the day rather than the person."
    ),
    PLAN_RELEASE_SLOW: (
        "Only mild checks fired, and this response spent longer on the survey than the "
        "typical verified caregiver, so speed is not in question."
    ),
    PLAN_READ_SAMPLE: (
        "Only mild checks fired, but this response finished faster than the typical "
        "verified caregiver, so a person should read a sample of this group."
    ),
    PLAN_READ_ALL: (
        "A serious check fired that describes what this person answered, not when they "
        "arrived."
    ),
    PLAN_COMPLETION: (
        "No section of this response has a recorded time, so no timing check can reach it. "
        "This is a completion question, not a bot question."
    ),
}


def verified_median_minutes(risk_table: pd.DataFrame) -> float:
    """The whole-survey time of the typical verified caregiver."""
    verified = risk_table[risk_table["Group"].eq("Caregivers we verified")]
    return float(verified["Survey minutes"].median())


def build_arrival_by_day_table(risk_table: pd.DataFrame) -> pd.DataFrame:
    """Show that every arrival flag falls on one day.

    This is the whole argument for setting the arrival check aside when
    deciding about one person.  If almost everyone who signed up that day
    carries the flag, the flag cannot tell one of them from another.
    """
    online = risk_table[risk_table["Group"].eq("Online sign-ups")].copy()
    online["Day"] = pd.to_datetime(online["Started"], errors="coerce").dt.date
    grouped = online.groupby("Day").agg(
        signups=("record_id", "size"),
        flagged=("check_burst_arrival", "sum"),
    )
    table = grouped.reset_index()
    table.columns = ["Day", "Sign-ups that day", "Arrived in a burst"]
    table["Share of that day"] = (
        table["Arrived in a burst"] / table["Sign-ups that day"] * 100
    ).round(1).astype(str) + "%"
    verified = risk_table[risk_table["Group"].eq("Caregivers we verified")]
    table.loc[len(table)] = [
        "All verified caregivers",
        len(verified),
        int(verified["check_burst_arrival"].sum()),
        f"{verified['check_burst_arrival'].mean() * 100:.1f}%",
    ]
    return table


def build_review_triage(risk_table: pd.DataFrame) -> pd.DataFrame:
    """Sort the check-by-hand pile by the kind of evidence against each response."""
    frame = risk_table.copy()
    median_minutes = verified_median_minutes(frame)

    frame["Points from the person"] = sum(
        frame[key].astype(int) * CHECK_WEIGHTS[key] for key in PERSON_CHECKS
    )
    frame["Points from arrival timing"] = sum(
        frame[key].astype(int) * CHECK_WEIGHTS[key] for key in CHANNEL_CHECKS
    )
    serious_person = [k for k in PERSON_CHECKS if CHECK_SEVERITY_BY_KEY[k] == "Serious"]
    frame["Serious check about the person"] = frame[serious_person].any(axis=1)
    frame["Slower than a typical verified caregiver"] = frame["Survey minutes"].ge(median_minutes)

    plan = pd.Series("", index=frame.index, dtype="object")
    held = frame["Recommended action"].eq(ACTION_REVIEW)

    plan[frame["Recommended action"].eq(ACTION_PAY)] = PLAN_PAY
    plan[frame["Recommended action"].eq(ACTION_REJECT)] = PLAN_REJECT
    plan[held & frame["Points from the person"].eq(0)] = PLAN_RELEASE_ARRIVAL
    plan[held & frame["Points from the person"].ge(1)] = PLAN_READ_SAMPLE
    plan[
        held
        & frame["Points from the person"].ge(1)
        & frame["Slower than a typical verified caregiver"]
    ] = PLAN_RELEASE_SLOW
    # A response with nothing timed cannot be judged on speed, so it becomes a
    # completion question.  A serious check about the person outranks that,
    # because there is something to read either way.
    plan[held & frame["Sections timed"].eq(0)] = PLAN_COMPLETION
    plan[held & frame["Serious check about the person"]] = PLAN_READ_ALL

    frame["Review plan"] = plan
    reason = frame["Review plan"].map(TRIAGE_REASONS).fillna(frame["Why this action"])
    # The sampling group holds two different situations, so say which is which
    # rather than describing every one of them as fast.
    unfinished = (
        frame["Review plan"].eq(PLAN_READ_SAMPLE) & ~frame["Timed end to end"]
    )
    reason[unfinished] = (
        "Only mild checks fired, but the survey was never finished, so there is no "
        "whole-survey time to compare against the verified caregivers."
    )
    frame["Why this plan"] = reason
    frame["Final plan"] = np.where(held, frame["Review plan"], frame["Recommended action"])
    frame.attrs["median_minutes"] = median_minutes
    return frame


def build_triage_summary(triaged: pd.DataFrame) -> pd.DataFrame:
    """How the check-by-hand pile splits, and what each group costs to work."""
    held = triaged[
        triaged["Group"].eq("Online sign-ups") & triaged["Recommended action"].eq(ACTION_REVIEW)
    ]
    rows = []
    for plan in TRIAGE_ORDER:
        block = held[held["Review plan"].eq(plan)]
        if block.empty:
            continue
        rows.append(
            {
                "Review plan": plan,
                "Online sign-ups": len(block),
                "Share of the held pile": f"{len(block) / len(held) * 100:.1f}%",
                "Middle survey time (minutes)": (
                    round(float(block["Survey minutes"].median()), 2)
                    if block["Survey minutes"].notna().any()
                    else "no time recorded"
                ),
                "Responses a person actually reads": (
                    0
                    if plan in {PLAN_RELEASE_ARRIVAL, PLAN_RELEASE_SLOW, PLAN_COMPLETION}
                    else len(block)
                    if plan == PLAN_READ_ALL
                    else recommended_sample_size(len(block))
                ),
                "Why": TRIAGE_REASONS[plan],
            }
        )
    table = pd.DataFrame(rows)
    total_read = int(pd.to_numeric(table["Responses a person actually reads"]).sum())
    table.attrs["held_total"] = len(held)
    table.attrs["total_read"] = total_read
    return table


def recommended_sample_size(group_size: int, confidence: float = 0.95, ceiling: float = 0.05) -> int:
    """How many to read so a clean sample rules out more than `ceiling` bad.

    If a person reads this many at random and finds none that are fake, we can
    say at 95 percent confidence that no more than 5 in 100 of the group is
    fake.  The arithmetic is the plain binomial one and takes no account of the
    group being finite, so it errs on the side of reading more.
    """
    if group_size <= 0:
        return 0
    needed = int(np.ceil(np.log(1 - confidence) / np.log(1 - ceiling)))
    return int(min(needed, group_size))


def build_sample_plan_table(triaged: pd.DataFrame) -> pd.DataFrame:
    """What each possible sample size buys, for the group that needs sampling."""
    held = triaged[
        triaged["Group"].eq("Online sign-ups") & triaged["Review plan"].eq(PLAN_READ_SAMPLE)
    ]
    group_size = len(held)
    rows = []
    for sample in (20, 30, 45, 59, 75, 100, 150, group_size):
        if sample > group_size:
            continue
        ceiling = 1 - 0.05 ** (1 / sample)
        rows.append(
            {
                "Responses read at random": sample,
                "Hours at 8 minutes each": round(sample * 8 / 60, 1),
                "If none of them are fake, at most this share of the group is fake": f"{ceiling * 100:.1f}%",
                "Which is at most this many responses": int(np.ceil(ceiling * group_size)),
                "Suggested": "Yes" if sample == recommended_sample_size(group_size) else "",
            }
        )
    table = pd.DataFrame(rows).drop_duplicates(subset=["Responses read at random"])
    table.attrs["group_size"] = group_size
    return table


def build_triage_check_mix(triaged: pd.DataFrame) -> pd.DataFrame:
    """The exact check combinations inside each review plan."""
    held = triaged[
        triaged["Group"].eq("Online sign-ups") & triaged["Recommended action"].eq(ACTION_REVIEW)
    ]
    counts = (
        held.groupby(["Review plan", "Which checks were broken"])
        .size()
        .reset_index(name="Online sign-ups")
    )
    counts["Review plan"] = pd.Categorical(counts["Review plan"], TRIAGE_ORDER, ordered=True)
    return counts.sort_values(["Review plan", "Online sign-ups"], ascending=[True, False]).reset_index(
        drop=True
    )


def build_triage_time_comparison(triaged: pd.DataFrame) -> pd.DataFrame:
    """Each review plan next to the caregivers we know are real."""
    median_minutes = verified_median_minutes(triaged)
    verified = triaged[triaged["Group"].eq("Caregivers we verified")]
    held = triaged[
        triaged["Group"].eq("Online sign-ups") & triaged["Recommended action"].eq(ACTION_REVIEW)
    ]
    refused = triaged[
        triaged["Group"].eq("Online sign-ups") & triaged["Recommended action"].eq(ACTION_REJECT)
    ]

    def summarise(label: str, block: pd.DataFrame) -> dict[str, object]:
        minutes = block["Survey minutes"].dropna()
        attitudes = block["Attitudes minutes"].dropna()
        return {
            "Group": label,
            "Responses": len(block),
            "Middle whole-survey time (minutes)": round(float(minutes.median()), 2) if len(minutes) else "no time recorded",
            "Middle thoughts and feelings time (minutes)": round(float(attitudes.median()), 2) if len(attitudes) else "no time recorded",
            "Against the typical verified caregiver": (
                "no time recorded"
                if not len(minutes)
                else "slower"
                if minutes.median() >= median_minutes
                else "faster"
            ),
        }

    rows = [summarise("Caregivers we verified", verified)]
    for plan in TRIAGE_ORDER:
        block = held[held["Review plan"].eq(plan)]
        if not block.empty:
            rows.append(summarise(plan, block))
    rows.append(summarise("Online sign-ups we refuse", refused))
    return pd.DataFrame(rows)


def build_final_plan_summary(triaged: pd.DataFrame) -> pd.DataFrame:
    """One table with the end state for every response in both studies."""
    counts = pd.crosstab(triaged["Final plan"], triaged["Group"])
    counts = counts.reindex(columns=["Caregivers we verified", "Online sign-ups"], fill_value=0)
    order = [PLAN_PAY] + TRIAGE_ORDER + [PLAN_REJECT]
    counts = counts.reindex([p for p in order if p in counts.index])
    table = counts.reset_index()
    table.columns = ["Final plan", "Verified caregivers", "Online sign-ups"]
    table["All responses"] = table["Verified caregivers"] + table["Online sign-ups"]
    table["Someone reads it"] = table["Final plan"].map(
        {
            PLAN_PAY: "No",
            PLAN_RELEASE_ARRIVAL: "No",
            PLAN_RELEASE_SLOW: "No",
            PLAN_READ_SAMPLE: "A sample of them",
            PLAN_READ_ALL: "Yes, every one",
            PLAN_COMPLETION: "No, this needs a completion rule first",
            PLAN_REJECT: "No",
        }
    )
    return table


def build_read_by_hand_list(triaged: pd.DataFrame) -> pd.DataFrame:
    """The responses a person genuinely has to open, one by one."""
    columns = [
        "Study",
        "Record ID",
        "Whole survey minutes",
        "Thoughts and feelings minutes",
        "Which checks were broken",
        "Risk score",
        "Review plan",
        "Why this plan",
    ]
    frame = triaged[triaged["Review plan"].eq(PLAN_READ_ALL)][columns]
    return frame.sort_values(["Study", "Record ID"]).reset_index(drop=True)


def build_sample_to_read(triaged: pd.DataFrame, seed: int = TIME_BAND_SEED) -> pd.DataFrame:
    """A drawn-in-advance random sample of the group that needs sampling."""
    pool = triaged[
        triaged["Group"].eq("Online sign-ups") & triaged["Review plan"].eq(PLAN_READ_SAMPLE)
    ]
    size = recommended_sample_size(len(pool))
    columns = [
        "Study",
        "Record ID",
        "Whole survey minutes",
        "Thoughts and feelings minutes",
        "Which checks were broken",
        "Risk score",
    ]
    sample = pool.sample(n=size, random_state=seed)[columns].copy()
    sample = sample.sort_values("Record ID").reset_index(drop=True)
    sample.insert(0, "Read in this order", range(1, len(sample) + 1))
    sample["Real caregiver"] = ""
    sample["Notes"] = ""
    sample["Who read it"] = ""
    return sample


# ══════════════════════════════════════════════════════════════════════════
# Two layouts for the rules a response broke
#
# One row per response either way.  The grid gives every rule its own Yes/No
# column, which suits filtering and sorting.  The list writes the rules into
# a single cell, which suits reading.  Both carry the same totals so they can
# never disagree.
# ══════════════════════════════════════════════════════════════════════════

# Short codes.  R1 to R9 are the nine rules the study team already names.
# E1 to E5 are the checks added after the September review meeting, kept on a
# separate letter so nobody mistakes one for the other.
RULE_CODES: dict[str, str] = {
    "check_survey_definitely_rushed": "R1",
    "check_attitudes_definitely_rushed": "R2",
    "check_section_rushed": "R3",
    "check_repeated_answers": "R4",
    "check_identical_answer_sheet": "R5",
    "check_burst_arrival": "R6",
    "check_duplicate_comment": "R7",
    "check_family_contradiction": "R8",
    "check_impossible_demographics": "R9",
    "check_survey_possibly_rushed": "E1",
    "check_attitudes_possibly_rushed": "E2",
    "check_throwaway_email": "E3",
    "check_no_email": "E4",
    "check_overnight": "E5",
}

CODE_ORDER = ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9",
              "E1", "E2", "E3", "E4", "E5"]
CODE_TO_KEY = {code: key for key, code in RULE_CODES.items()}

# The scale drawn on the meeting whiteboard: a mild rule counts 1, a serious
# rule counts 5.  The agreed scale from the meeting notes counts a serious
# rule 2.  Both are carried so the difference can be seen rather than argued.
HEAVY_SERIOUS_POINTS = 5
HEAVY_MILD_POINTS = 1

SCORE_HEAVY_COLUMN = "Scoring (Mild = 1, Serious = 5)"
SCORE_AGREED_COLUMN = "Scoring (Mild = 1, Serious = 2)"


def _response_key(frame: pd.DataFrame) -> pd.Series:
    """A key that is unique across both studies.

    Record numbers restart in each study, so 348 numbers appear twice across
    the 1,956 responses.  Anything keyed on the record number alone would put
    two different people on one row.
    """
    return frame["project_id"].astype(str) + "-" + frame["record_id"].astype(str)


def _heavy_score(frame: pd.DataFrame) -> pd.Series:
    serious = [k for k in CHECK_KEYS if CHECK_SEVERITY_BY_KEY[k] == "Serious"]
    mild = [k for k in CHECK_KEYS if CHECK_SEVERITY_BY_KEY[k] == "Mild"]
    return (
        frame[serious].sum(axis=1) * HEAVY_SERIOUS_POINTS
        + frame[mild].sum(axis=1) * HEAVY_MILD_POINTS
    ).astype(int)


def _rules_common(triaged: pd.DataFrame) -> pd.DataFrame:
    frame = triaged.copy()
    frame["Study"] = frame["source_project"].map(PROJECT_LABELS)
    frame["Record ID"] = frame["record_id"]
    frame["Response key"] = _response_key(frame)
    frame["Total Rules Violated"] = frame["Checks broken"]
    frame["Serious Rules Violated"] = frame["Serious checks broken"]
    frame["Mild Rules Violated"] = frame["Mild checks broken"]
    frame[SCORE_HEAVY_COLUMN] = _heavy_score(frame)
    frame[SCORE_AGREED_COLUMN] = frame["Risk score"]
    return frame


def build_rules_grid(triaged: pd.DataFrame) -> pd.DataFrame:
    """One column per rule, Yes or No in every cell.

    Best when someone wants to filter on one rule, sort by it, or count how
    often two rules travel together.
    """
    frame = _rules_common(triaged)
    for code in CODE_ORDER:
        frame[code] = np.where(frame[CODE_TO_KEY[code]], "Yes", "No")
    columns = (
        ["Study", "Record ID", "Response key"]
        + CODE_ORDER
        + [
            "Total Rules Violated",
            "Serious Rules Violated",
            "Mild Rules Violated",
            SCORE_HEAVY_COLUMN,
            SCORE_AGREED_COLUMN,
            "Recommended action",
            "Final plan",
        ]
    )
    grid = frame[columns].copy()
    return grid.sort_values(
        ["Study", SCORE_HEAVY_COLUMN, "Record ID"], ascending=[True, False, True]
    ).reset_index(drop=True)


def build_rules_list(triaged: pd.DataFrame) -> pd.DataFrame:
    """The rules written into one cell, in codes and again in plain words.

    Best for reading down a page, or pasting a single row into an email.
    """
    frame = _rules_common(triaged)
    matrix = frame[[CODE_TO_KEY[c] for c in CODE_ORDER]].to_numpy()
    codes = np.array(CODE_ORDER)
    names = np.array([CHECK_NAMES[CODE_TO_KEY[c]] for c in CODE_ORDER])

    frame["Rules violated"] = [
        ", ".join(codes[row]) if row.any() else "None" for row in matrix
    ]
    frame["Rules violated, in plain words"] = [
        "; ".join(names[row]) if row.any() else "No rules broken" for row in matrix
    ]
    frame["Total Number of Rules"] = frame["Total Rules Violated"]
    columns = [
        "Study",
        "Record ID",
        "Response key",
        "Rules violated",
        "Rules violated, in plain words",
        "Total Number of Rules",
        "Serious Rules Violated",
        "Mild Rules Violated",
        SCORE_HEAVY_COLUMN,
        SCORE_AGREED_COLUMN,
        "Recommended action",
        "Final plan",
    ]
    listing = frame[columns].copy()
    return listing.sort_values(
        ["Study", SCORE_HEAVY_COLUMN, "Record ID"], ascending=[True, False, True]
    ).reset_index(drop=True)


def build_rule_key_table(triaged: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """What every code means, what it is worth, and how often it fired."""
    rows = []
    for code in CODE_ORDER:
        key = CODE_TO_KEY[code]
        check = next(c for c in SCORED_CHECKS if str(c["key"]) == key)
        severity = CHECK_SEVERITY_BY_KEY[key]
        row = {
            "Code": code,
            "What it means": CHECK_NAMES[key],
            "Where in the survey": check["area"],
            "Mild or serious": severity,
            "Points on the 1 and 5 scale": (
                HEAVY_SERIOUS_POINTS if severity == "Serious" else HEAVY_MILD_POINTS
            ),
            "Points on the 1 and 2 scale": CHECK_WEIGHTS[key],
            "Added after the meeting": "Yes" if code.startswith("E") else "No",
        }
        if triaged is not None:
            verified = triaged[triaged["Group"].eq("Caregivers we verified")]
            online = triaged[triaged["Group"].eq("Online sign-ups")]
            row["Verified caregivers it fired on"] = f"{int(verified[key].sum())} of {len(verified):,}"
            row["Online sign-ups it fired on"] = f"{int(online[key].sum()):,} of {len(online):,}"
        rows.append(row)
    return pd.DataFrame(rows)


def build_scale_comparison(triaged: pd.DataFrame) -> pd.DataFrame:
    """The same responses on both scoring scales, side by side."""
    frame = _rules_common(triaged)
    rows = []
    for label, mask in (
        ("No rules broken", frame["Total Rules Violated"].eq(0)),
        ("Mild rules only", frame["Serious Rules Violated"].eq(0) & frame["Mild Rules Violated"].ge(1)),
        ("Exactly one serious rule", frame["Serious Rules Violated"].eq(1)),
        ("Two or more serious rules", frame["Serious Rules Violated"].ge(2)),
    ):
        block = frame[mask]
        if block.empty:
            continue
        rows.append(
            {
                "Kind of response": label,
                "Responses": len(block),
                "Lowest score on the 1 and 5 scale": int(block[SCORE_HEAVY_COLUMN].min()),
                "Highest score on the 1 and 5 scale": int(block[SCORE_HEAVY_COLUMN].max()),
                "Lowest score on the 1 and 2 scale": int(block[SCORE_AGREED_COLUMN].min()),
                "Highest score on the 1 and 2 scale": int(block[SCORE_AGREED_COLUMN].max()),
            }
        )
    return pd.DataFrame(rows)


def build_heavy_cutoff_table(triaged: pd.DataFrame) -> pd.DataFrame:
    """Where the refusal line would sit on the 1 and 5 scale.

    The line of 3 points from the meeting notes belongs to the 1 and 2 scale.
    Carrying that same number over to the 1 and 5 scale would make any single
    serious rule an automatic refusal, so the line has to be chosen again.
    """
    frame = _rules_common(triaged)
    verified = frame[frame["Group"].eq("Caregivers we verified")]
    online = frame[frame["Group"].eq("Online sign-ups")]
    rows = []
    for cut in (3, 5, 6, 7, 8, 10, 12):
        refused_online = int((online[SCORE_HEAVY_COLUMN] >= cut).sum())
        refused_verified = int((verified[SCORE_HEAVY_COLUMN] >= cut).sum())
        rows.append(
            {
                "Refuse payment at this many points or more": cut,
                "Online sign-ups refused": f"{refused_online:,}",
                "Verified caregivers refused by mistake": refused_verified,
                "Share of verified caregivers refused by mistake": f"{refused_verified / len(verified) * 100:.1f}%",
                "What this line does": {
                    3: "Refuses any three mild rules, and any single serious rule",
                    5: "Refuses any single serious rule on its own",
                    6: "Refuses a serious rule once anything else joins it",
                    7: "Refuses a serious rule with two mild ones, or two serious rules",
                    8: "Refuses a serious rule with three mild ones, or two serious rules",
                    10: "Refuses two serious rules",
                    12: "Refuses two serious rules with two mild ones",
                }[cut],
                "Note": {
                    3: "Same number as the line in use, but a much harsher line on this scale",
                    5: "",
                    6: "",
                    7: "The lowest line here that refuses no verified caregiver",
                    8: "",
                    10: "",
                    12: "",
                }[cut],
            }
        )
    return pd.DataFrame(rows)


def build_layout_choice_table() -> pd.DataFrame:
    """A short note on which of the two layouts to hand to whom."""
    return pd.DataFrame(
        [
            {
                "Layout": "Rules grid",
                "What one row looks like": "A Yes or No in fourteen separate columns",
                "Best for": "Filtering on one rule, sorting by it, counting how often two rules appear together",
                "Watch out for": "Fourteen narrow columns, so it is wide to read on paper",
            },
            {
                "Layout": "Rules list",
                "What one row looks like": "One cell reading R1, R5, and a second cell spelling those out",
                "Best for": "Reading down a page, or pasting one response into an email",
                "Watch out for": "Cannot be filtered on a single rule without splitting the cell first",
            },
        ]
    )
