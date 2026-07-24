"""Generate REDCap-backed recruitment milestone tables for NANO and NICO."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from redcap import Project

from redcap_client import GlobalRequestPacer, sanitize_error

DEFAULT_API_URL = os.getenv("REDCAP_API_URL", "https://redcap.research.sc.edu/api/")
DEFAULT_OUTPUT_DIR = "recruitment_outputs"
ARCHIVE_SUBDIR = "archive"
MIN_REQUEST_INTERVAL_SECONDS = float(
    os.getenv("REDCAP_MIN_REQUEST_INTERVAL_SECONDS", "1.25")
)

STATUS_ON_TARGET = "\u2705"
STATUS_BEHIND = "\u26a0\ufe0f"
STATUS_PENDING = "\U0001f4cb"

MONTH_LABELS = {4: "Apr 1", 8: "Aug 1", 12: "Dec 1"}
PROJECT_ORDER = ("NANO", "NICO")
CATEGORIES = ("Total", "Minority", "Hispanic")
CATEGORY_LABELS = {
    "Total": "Total Recruitment",
    "Minority": "Racial Minority Recruitment",
    "Hispanic": "Hispanic Ethnicity Recruitment",
}


@dataclass(frozen=True)
class ProjectConfig:
    label: str
    grant: str | None
    study_title: str
    record_id: str
    race_field: str
    race_minority_codes: tuple[int, ...]
    race_white_codes: tuple[int, ...]
    race_unknown_codes: tuple[int, ...]
    race_hispanic_codes: tuple[int, ...]
    eth_field: str
    eth_hispanic_codes: tuple[int, ...]
    eth_unknown_codes: tuple[int, ...]
    eth_secondary: tuple[tuple[str, tuple[int, ...]], ...] = ()
    exclusion_flags: tuple[str, ...] = ()
    review_flags: tuple[str, ...] = ()
    hard_exclude_flags: tuple[str, ...] = ()
    dual_field: str | None = None
    date_anchor: str | None = None
    targets_available: bool = False
    milestone_start: dt.date = dt.date(2024, 8, 1)
    milestone_end: dt.date = dt.date(2028, 12, 1)
    milestone_months: tuple[int, ...] = (4, 8, 12)
    historical_actuals: dict[str, list[int]] = field(default_factory=dict)
    previous_targets: dict[str, list[int | None]] = field(default_factory=dict)
    current_targets: dict[str, list[int | None]] = field(default_factory=dict)
    footnote_detail: str = ""


@dataclass(frozen=True)
class TableRow:
    label: str
    kind: str
    values: tuple[Any, ...]


PROJECT_CONFIG: dict[str, ProjectConfig] = {
    "NANO": ProjectConfig(
        label="NANO Study",
        grant="MH132925",
        study_title="The Role of Autonomic Regulation of Attention in the Emergence of ASD",
        record_id="demo_id",
        race_field="fif_childrace",
        race_minority_codes=(1, 2, 3, 4),
        race_white_codes=(5,),
        race_unknown_codes=(6,),
        race_hispanic_codes=(),
        eth_field="fif_childethnicity",
        eth_hispanic_codes=(1,),
        eth_unknown_codes=(3,),
        exclusion_flags=("demo_ineligible", "demo_unenrolled"),
        review_flags=("demo_exclude",),
        milestone_start=dt.date(2024, 8, 1),
        milestone_end=dt.date(2028, 12, 1),
        milestone_months=(4, 8, 12),
        historical_actuals={
            "Total": [63, 108, 128, 151, 172, 219],
            "Minority": [25, 48, 59, 84, 96, 99],
            "Hispanic": [5, 5, 7, 8, 11, 16],
        },
        previous_targets={
            "Total": [90, 110, 130, 150, 170, 190, 200, None, None, None, 160],
            "Minority": [36, 44, 52, 60, 68, 76, 84, None, None, None, 32],
            "Hispanic": [3, 5, 7, 9, 11, 13, 14, None, None, None, 7],
        },
        current_targets={
            "Total": [5, 10, 110, 120, 130, 140, 150, 160, 170, 180, 190, 200],
            "Minority": [1, 2, None, None, None, None, None, None, None, None, None, 40],
            "Hispanic": [None, None, 1, None, 1, None, 1, None, 1, None, 1, 10],
        },
        targets_available=True,
        footnote_detail=(
            "Targets are provisional and still need reconciliation with the NIH-approved plan."
        ),
    ),
    "NICO": ProjectConfig(
        label="NICO Study",
        grant=None,
        study_title="NICO Study",
        record_id="id",
        race_field="race",
        race_minority_codes=(1, 2, 3, 5),
        race_white_codes=(6,),
        race_unknown_codes=(7,),
        race_hispanic_codes=(4,),
        eth_field="fif_childethnicity",
        eth_hispanic_codes=(1,),
        eth_unknown_codes=(3,),
        eth_secondary=(("ethnicity", (0,)),),
        exclusion_flags=("demo_ineligible", "demo_unenrolled"),
        review_flags=("demo_exclude",),
        dual_field="dual_enrolled",
        milestone_start=dt.date(2024, 8, 1),
        milestone_end=dt.date(2028, 12, 1),
        milestone_months=(4, 8, 12),
        footnote_detail=(
            "Child ethnicity is missing for most participants, so the Hispanic count is provisional."
        ),
    ),
}


class RecruitmentReportError(RuntimeError):
    """Raised when report generation cannot complete."""


def build_milestones(config: ProjectConfig) -> list[dt.date]:
    milestones: list[dt.date] = []
    for year in range(config.milestone_start.year, config.milestone_end.year + 1):
        for month in config.milestone_months:
            milestone = dt.date(year, month, 1)
            if config.milestone_start <= milestone <= config.milestone_end:
                milestones.append(milestone)
    return milestones


def pad_series(values: Sequence[int | None], size: int) -> list[int | None]:
    return [values[index] if index < len(values) else None for index in range(size)]


def _paced_call(project: Project, method_name: str, **kwargs: Any) -> Any:
    GlobalRequestPacer.wait(MIN_REQUEST_INTERVAL_SECONDS)
    method = getattr(project, method_name)
    return method(**kwargs)


def _as_optional_int(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _display_cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def ratio_pct(actual: Any, target: Any) -> str:
    actual_value = _as_optional_int(actual)
    target_value = _as_optional_int(target)
    if actual_value is None or target_value in (None, 0):
        return "N/A"
    return f"{round(actual_value / target_value * 100):.0f}%"


def status_text(actual: Any, target: Any, milestone_index: int, current_index: int) -> str:
    actual_value = _as_optional_int(actual)
    target_value = _as_optional_int(target)
    if target_value in (None, 0):
        return "N/A"
    if milestone_index == current_index and actual_value is None:
        return STATUS_PENDING
    if actual_value is None:
        return ""
    if actual_value >= target_value:
        return STATUS_ON_TARGET
    return STATUS_BEHIND


def _status_variant(value: str) -> str:
    if value == STATUS_ON_TARGET:
        return "ok"
    if value == STATUS_BEHIND:
        return "warn"
    if value == STATUS_PENDING:
        return "pending"
    if value == "N/A":
        return "na"
    return "plain"


def _project_title(config: ProjectConfig) -> str:
    if config.grant:
        return f"Recruitment Milestones for {config.grant} - {config.study_title}"
    return f"Recruitment Milestones for {config.study_title}"


def _project_notice(config: ProjectConfig) -> tuple[str, str]:
    if config.targets_available:
        return (
            "Targets are PROVISIONAL / UNVERIFIED against the NIH-approved plan.",
            "notice-provisional",
        )
    return (
        "PENDING TARGET VERIFICATION - NIH milestone dates and targets not supplied; actuals shown are current live counts only.",
        "notice-pending",
    )


def _project_footnote(config: ProjectConfig, report_date: dt.date) -> str:
    parts = [
        "Actual/Target Ratio = Actual / Current Target x 100.",
        "N/A appears when actuals are pending or the current target is 0 or unknown.",
        f"Report cut-off: {report_date.isoformat()}.",
    ]
    if config.footnote_detail:
        parts.append(config.footnote_detail)
    return " ".join(parts)


def _extract_body_content(document_html: str) -> str:
    start_token = "<body>"
    end_token = "</body>"
    start_index = document_html.find(start_token)
    end_index = document_html.rfind(end_token)
    if start_index == -1 or end_index == -1 or end_index <= start_index:
        body = document_html
    else:
        body = document_html[start_index + len(start_token) : end_index]

    table_section = re.search(
        r'(<table class="recruitment-table">.*?<div class="recruitment-footnote">.*?</div>)',
        body,
        flags=re.DOTALL,
    )
    if table_section:
        return table_section.group(1)
    return body


def _year_spans(milestones: Sequence[dt.date]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(milestones):
        year = milestones[index].year
        end_index = index
        while end_index < len(milestones) and milestones[end_index].year == year:
            end_index += 1
        spans.append((year, end_index - index))
        index = end_index
    return spans


def _validate_required_fields(
    project_key: str, field_names: set[str], config: ProjectConfig
) -> None:
    missing_fields = [
        field_name
        for field_name in (config.race_field, config.eth_field)
        if field_name not in field_names
    ]
    if missing_fields:
        missing_csv = ", ".join(sorted(missing_fields))
        raise RecruitmentReportError(
            f"{project_key}: missing configured REDCap fields: {missing_csv}"
        )


def _has_race_codes(participants: pd.DataFrame, race_field: str, codes: Sequence[int]) -> pd.Series:
    columns = [
        f"{race_field}___{code}"
        for code in codes
        if f"{race_field}___{code}" in participants.columns
    ]
    if not columns:
        return pd.Series(False, index=participants.index)
    return participants[columns].fillna(0).astype(float).max(axis=1) > 0


def build_classification(
    project: Project,
    metadata: pd.DataFrame,
    config: ProjectConfig,
    strict_eligibility: bool = False,
) -> tuple[pd.DataFrame, str, int, int, str | None]:
    field_names = set(metadata["field_name"])
    _validate_required_fields(config.label, field_names, config)

    secondary_fields = [field_name for field_name, _ in config.eth_secondary if field_name in field_names]
    status_flags = [
        field_name
        for field_name in (*config.exclusion_flags, *config.review_flags)
        if field_name in field_names
    ]
    dual_field = (
        config.dual_field
        if config.dual_field and config.dual_field in field_names
        else None
    )
    record_fields = list(
        dict.fromkeys(
            [
                config.race_field,
                config.eth_field,
                *secondary_fields,
                *status_flags,
                *([dual_field] if dual_field else []),
            ]
        )
    )

    records = _paced_call(
        project,
        "export_records",
        format_type="df",
        fields=record_fields,
        raw_or_label="raw",
    ).reset_index()
    identifier_column = records.columns[0]
    raw_rows = len(records)
    unique_participants = records[identifier_column].nunique()

    race_columns = [
        column_name for column_name in records.columns if column_name.startswith(config.race_field + "___")
    ]
    grouped = records.groupby(identifier_column)
    if race_columns:
        participants = grouped[race_columns].max()
    else:
        participants = pd.DataFrame(index=pd.Index(sorted(records[identifier_column].unique()), name=identifier_column))

    if config.eth_field in records.columns:
        participants[config.eth_field] = (
            records.dropna(subset=[config.eth_field]).groupby(identifier_column)[config.eth_field].first()
        )
    for field_name, _ in config.eth_secondary:
        if field_name in records.columns:
            participants[field_name] = (
                records.dropna(subset=[field_name]).groupby(identifier_column)[field_name].first()
            )
    for field_name in (*status_flags, *(tuple([dual_field]) if dual_field else tuple())):
        if field_name in records.columns:
            participants[field_name] = grouped[field_name].max()
    participants = participants.reset_index()

    participants["race_any"] = _has_race_codes(
        participants,
        config.race_field,
        (
            *config.race_minority_codes,
            *config.race_white_codes,
            *config.race_unknown_codes,
            *config.race_hispanic_codes,
        ),
    )
    participants["is_minority"] = _has_race_codes(
        participants, config.race_field, config.race_minority_codes
    ).astype(int)
    participants["race_white"] = _has_race_codes(
        participants, config.race_field, config.race_white_codes
    ).astype(int)
    participants["race_unknown"] = _has_race_codes(
        participants, config.race_field, config.race_unknown_codes
    ).astype(int)

    ethnicity = (
        pd.to_numeric(participants[config.eth_field], errors="coerce")
        if config.eth_field in participants
        else pd.Series(np.nan, index=participants.index)
    )
    hispanic = ethnicity.isin(config.eth_hispanic_codes)
    ethnicity_known = ethnicity.notna()
    for field_name, codes in config.eth_secondary:
        if field_name in participants.columns:
            secondary = pd.to_numeric(participants[field_name], errors="coerce")
            hispanic = hispanic | secondary.isin(codes)
            ethnicity_known = ethnicity_known | secondary.notna()
    if config.race_hispanic_codes:
        hispanic_race = _has_race_codes(participants, config.race_field, config.race_hispanic_codes)
        hispanic = hispanic | hispanic_race
        ethnicity_known = ethnicity_known | hispanic_race
    participants["is_hispanic"] = hispanic.astype(int)
    participants["eth_known"] = ethnicity_known.astype(int)

    hard_excluded = pd.Series(False, index=participants.index)
    hard_reason = pd.Series("", index=participants.index)
    for field_name in config.hard_exclude_flags:
        if field_name in participants.columns:
            hit = participants[field_name] == 1
            hard_excluded = hard_excluded | hit
            hard_reason = hard_reason.mask(hit & (hard_reason == ""), field_name + "=Yes")

    eligibility_hit = pd.Series(False, index=participants.index)
    eligibility_reason = pd.Series("", index=participants.index)
    for field_name in config.exclusion_flags:
        if field_name in participants.columns:
            hit = participants[field_name] == 1
            eligibility_hit = eligibility_hit | hit
            eligibility_reason = eligibility_reason.mask(
                hit & (eligibility_reason == ""), field_name + "=Yes"
            )

    review_hit = pd.Series(False, index=participants.index)
    review_reason = pd.Series("", index=participants.index)
    for field_name in config.review_flags:
        if field_name in participants.columns:
            hit = participants[field_name] == 1
            review_hit = review_hit | hit
            review_reason = review_reason.mask(hit & (review_reason == ""), field_name + "=Yes")
    missing_race = participants["race_any"] == 0
    review_hit = review_hit | missing_race
    review_reason = review_reason.mask(missing_race & (review_reason == ""), "missing race")

    excluded = hard_excluded | (eligibility_hit if strict_eligibility else False)
    flagged = (~excluded) & (review_hit | eligibility_hit)

    participants["decision"] = np.where(
        excluded,
        "excluded",
        np.where(flagged, "flagged-review", "included"),
    )
    exclude_reason = np.where(
        hard_excluded,
        "test/training/admin: " + hard_reason,
        "strict-eligibility: " + eligibility_reason,
    )
    flag_reason = np.where(
        eligibility_hit,
        "status flag: " + eligibility_reason,
        "review: " + review_reason,
    )
    participants["reason"] = np.where(
        excluded,
        exclude_reason,
        np.where(flagged, flag_reason, "meets inclusion (has record)"),
    )
    participants["in_cumulative"] = (participants["decision"] != "excluded").astype(int)

    for field_name in (*config.exclusion_flags, *config.review_flags):
        if field_name in participants.columns:
            participants[field_name] = (participants[field_name] == 1).astype(int)
    if dual_field:
        participants["dual_enrolled"] = (participants[dual_field] == 1).astype(int)

    return participants, identifier_column, raw_rows, unique_participants, dual_field


def live_actuals(participants: pd.DataFrame) -> dict[str, int]:
    included = participants[participants["in_cumulative"] == 1]
    return {
        "Total": int(len(included)),
        "Minority": int(included["is_minority"].sum()),
        "Hispanic": int(included["is_hispanic"].sum()),
    }


def actuals_by_milestone(
    snapshot: Mapping[str, Any], config: ProjectConfig, category: str
) -> list[int | None]:
    milestones = snapshot["milestones"]
    current_index = snapshot["current_index"]
    latest_completed_index = snapshot["latest_completed_index"]
    values: list[int | None] = [None] * len(milestones)
    for index, value in enumerate(config.historical_actuals.get(category, [])):
        if index < len(values):
            values[index] = value
    if latest_completed_index >= 0:
        values[latest_completed_index] = snapshot["live"][category]
    elif current_index < len(values):
        values[current_index] = snapshot["live"][category]
    for index in range(current_index + 1, len(values)):
        values[index] = None
    return values


def build_table_rows(snapshot: Mapping[str, Any], config: ProjectConfig) -> list[TableRow]:
    milestones = snapshot["milestones"]
    row_count = len(milestones)
    rows: list[TableRow] = []
    for category_index, category in enumerate(CATEGORIES):
        if category_index:
            rows.append(TableRow(label="", kind="spacer", values=tuple("" for _ in milestones)))
        previous_targets = pad_series(config.previous_targets.get(category, []), row_count)
        current_targets = pad_series(config.current_targets.get(category, []), row_count)
        actuals = actuals_by_milestone(snapshot, config, category)
        ratios = tuple(ratio_pct(actual, target) for actual, target in zip(actuals, current_targets))
        statuses = tuple(
            status_text(actual, target, index, snapshot["current_index"])
            for index, (actual, target) in enumerate(zip(actuals, current_targets))
        )
        rows.extend(
            [
                TableRow(
                    label=f"Previous Target: {CATEGORY_LABELS[category]}",
                    kind="prev",
                    values=tuple(previous_targets),
                ),
                TableRow(
                    label=f"Current Target: {CATEGORY_LABELS[category]}",
                    kind="curr",
                    values=tuple(current_targets),
                ),
                TableRow(
                    label=f"Actual: {CATEGORY_LABELS[category]}",
                    kind="actual",
                    values=tuple(actuals),
                ),
                TableRow(
                    label=f"Actual/Target Ratio: {CATEGORY_LABELS[category]}",
                    kind="ratio",
                    values=ratios,
                ),
                TableRow(
                    label=f"Status: {CATEGORY_LABELS[category]}",
                    kind="status",
                    values=statuses,
                ),
            ]
        )
    return rows


def render_project_table(
    project_key: str,
    snapshot: Mapping[str, Any],
    config: ProjectConfig,
    report_date: dt.date,
) -> str:
    milestones = snapshot["milestones"]
    current_index = snapshot["current_index"]
    total_columns = len(milestones) + 1
    notice_text, notice_class = _project_notice(config)

    parts = ['<table class="recruitment-table">']
    parts.append(
        f'<tr><td colspan="{total_columns}" class="title">{escape(_project_title(config))}</td></tr>'
    )
    parts.append(
        f'<tr><td colspan="{total_columns}" class="{notice_class}">{escape(notice_text)}</td></tr>'
    )
    parts.append("<tr>")
    parts.append('<td rowspan="2" class="milestone-label">Tri Yearly Milestones</td>')
    for year, span in _year_spans(milestones):
        parts.append(f'<td colspan="{span}" class="year">{year}</td>')
    parts.append("</tr>")
    parts.append("<tr>")
    for index, milestone in enumerate(milestones):
        cell_class = "month current" if index == current_index else "month"
        parts.append(
            f'<td class="{cell_class}">{escape(MONTH_LABELS[milestone.month])}</td>'
        )
    parts.append("</tr>")
    parts.append(f'<tr class="spacer"><td colspan="{total_columns}"></td></tr>')

    for row in build_table_rows(snapshot, config):
        if row.kind == "spacer":
            parts.append(f'<tr class="spacer"><td colspan="{total_columns}"></td></tr>')
            continue
        parts.append("<tr>")
        parts.append(f'<td class="row-label">{escape(row.label)}</td>')
        for index, value in enumerate(row.values):
            classes = ["value", row.kind]
            if index == current_index:
                classes.append("current")
            if row.kind == "status":
                classes.append(_status_variant(_display_cell(value)))
            cell_text = escape(_display_cell(value)) or "&nbsp;"
            parts.append(f'<td class="{" ".join(classes)}">{cell_text}</td>')
        parts.append("</tr>")
    parts.append("</table>")
    parts.append(
        f'<div class="recruitment-footnote">{escape(_project_footnote(config, report_date))}</div>'
    )
    return "".join(parts)


def render_dashboard_html(
    rendered_tables: Mapping[str, str], report_date: dt.date, api_url: str
) -> str:
    cards = "".join(
        f'<section class="report-card">{rendered_tables[key]}</section>'
        for key in PROJECT_ORDER
        if key in rendered_tables
    )
    return f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>Recruitment Milestones</title>
<style>
:root {{
  --page-bg: #eef2f6;
  --card-bg: #ffffff;
  --card-border: #d5dbe3;
  --title-bg: #7b7f86;
  --band-blue: #5f8fbd;
  --band-grey: #d9d9d9;
  --band-light: #efefef;
  --band-red: #c0392b;
  --band-orange: #f6a821;
  --grid: #b9c1cb;
  --text: #18314f;
  --muted: #6b7787;
  --ok: #2e7d32;
  --warn: #c0392b;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: Segoe UI, Arial, sans-serif;
  background:
    radial-gradient(circle at top left, rgba(95, 143, 189, 0.16), transparent 28%),
    linear-gradient(180deg, #f7f9fb 0%, var(--page-bg) 100%);
  color: var(--text);
}}
.page-shell {{
  max-width: 1800px;
  margin: 0 auto;
  padding: 24px;
}}
.page-header {{
  margin-bottom: 18px;
}}
.page-header h1 {{
  margin: 0 0 6px;
  font-size: 28px;
}}
.page-header p {{
  margin: 0;
  color: var(--muted);
  line-height: 1.5;
}}
.report-stack {{
  display: grid;
  gap: 24px;
}}
.report-card {{
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 14px;
  box-shadow: 0 12px 32px rgba(38, 57, 77, 0.08);
  padding: 14px;
  overflow-x: auto;
}}
.recruitment-table {{
  width: max-content;
  min-width: 100%;
  border-collapse: collapse;
  border: 1px solid var(--grid);
}}
.recruitment-table td {{
  border: 1px solid var(--grid);
  padding: 7px 10px;
  font-size: 12px;
  text-align: center;
}}
.recruitment-table .title {{
  background: var(--title-bg);
  color: #fff;
  font-weight: 700;
  font-size: 13px;
  padding: 8px 10px;
}}
.recruitment-table .notice-provisional {{
  background: var(--band-grey);
  color: #000;
  font-weight: 700;
}}
.recruitment-table .notice-pending {{
  background: var(--band-red);
  color: #fff;
  font-weight: 700;
}}
.recruitment-table .milestone-label,
.recruitment-table .year,
.recruitment-table .month,
.recruitment-table .row-label {{
  background: var(--band-blue);
  color: #fff;
  font-weight: 700;
}}
.recruitment-table .milestone-label {{
  min-width: 360px;
  vertical-align: middle;
}}
.recruitment-table .row-label {{
  text-align: left;
  white-space: nowrap;
}}
.recruitment-table .month {{
  text-decoration: underline;
  min-width: 86px;
}}
.recruitment-table .current {{
  background: var(--band-orange) !important;
  color: #000 !important;
}}
.recruitment-table .value.prev {{
  background: var(--band-light);
  font-weight: 700;
  color: #000;
}}
.recruitment-table .value.curr,
.recruitment-table .value.actual,
.recruitment-table .value.ratio,
.recruitment-table .value.status {{
  background: #fff;
  font-weight: 700;
  color: #000;
}}
.recruitment-table .value.status.ok {{ color: var(--ok); }}
.recruitment-table .value.status.warn {{ color: var(--warn); }}
.recruitment-table .value.status.pending {{ color: #6b4c00; }}
.recruitment-table .value.status.na {{ color: #8a9099; }}
.recruitment-table .spacer td {{
  height: 10px;
  padding: 0;
  background: #c9c9c9;
  border-color: #fff;
}}
.recruitment-footnote {{
  margin-top: 10px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.5;
}}
@media (max-width: 900px) {{
  .page-shell {{ padding: 14px; }}
  .page-header h1 {{ font-size: 23px; }}
  .report-card {{ padding: 10px; }}
  .recruitment-table .milestone-label {{ min-width: 260px; }}
}}
</style>
</head>
<body>
  <main class=\"page-shell\">
    <header class=\"page-header\">
      <h1>Recruitment Milestones</h1>
      <p>Automatic REDCap API refresh for NANO and NICO. Report cut-off: {escape(report_date.isoformat())}. Source endpoint: {escape(api_url)}.</p>
    </header>
    <div class=\"report-stack\">{cards}</div>
  </main>
</body>
</html>
"""


def build_combined_dashboard_from_documents(
    project_documents: Mapping[str, str], report_date: dt.date, api_url: str
) -> str:
    rendered_tables = {
        project_key: _extract_body_content(document_html)
        for project_key, document_html in project_documents.items()
    }
    return render_dashboard_html(rendered_tables, report_date, api_url)


def _fetch_snapshot(
    project_key: str, token: str, report_date: dt.date, api_url: str
) -> dict[str, Any]:
    config = PROJECT_CONFIG[project_key]
    try:
        project = Project(api_url, token, timeout=(10, 60))
        metadata = _paced_call(project, "export_metadata", format_type="df").reset_index()
        participants, identifier_column, raw_rows, unique_participants, dual_field = build_classification(
            project, metadata, config
        )
    except Exception as error:
        raise RecruitmentReportError(
            f"{project_key}: {sanitize_error(error)}"
        ) from error

    milestones = build_milestones(config)
    current_index = next(
        (index for index, milestone in enumerate(milestones) if milestone >= report_date),
        len(milestones) - 1,
    )
    completed_indices = [
        index for index, milestone in enumerate(milestones) if milestone < report_date
    ]
    latest_completed_index = completed_indices[-1] if completed_indices else -1
    snapshot = {
        "project_key": project_key,
        "metadata": metadata,
        "participants": participants,
        "identifier_column": identifier_column,
        "raw_rows": raw_rows,
        "unique_participants": unique_participants,
        "dual_field": dual_field,
        "milestones": milestones,
        "current_index": current_index,
        "latest_completed_index": latest_completed_index,
        "live": live_actuals(participants),
    }
    return snapshot


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _is_dated_output_file(path: Path) -> bool:
    return bool(
        re.match(
            r"^(nano|nico)_recruitment_milestones_\d{4}-\d{2}-\d{2}\.html$",
            path.name,
        )
        or re.match(
            r"^recruitment_milestones_\d{4}-\d{2}-\d{2}\.(html|xlsx)$",
            path.name,
        )
    )


def _archive_existing_dated_outputs(output_root: Path, archive_root: Path) -> list[Path]:
    archived_files: list[Path] = []
    if not output_root.exists():
        return archived_files

    archive_root.mkdir(parents=True, exist_ok=True)
    for candidate in output_root.iterdir():
        if not candidate.is_file() or not _is_dated_output_file(candidate):
            continue
        archived_target = archive_root / candidate.name
        if archived_target.exists():
            archived_target.unlink()
        candidate.replace(archived_target)
        archived_files.append(archived_target)
    return archived_files


def _build_excel_sheet(
    snapshot: Mapping[str, Any], config: ProjectConfig
) -> pd.DataFrame:
    columns = pd.MultiIndex.from_tuples(
        [(milestone.year, MONTH_LABELS[milestone.month]) for milestone in snapshot["milestones"]],
        names=["Year", "Milestone"],
    )
    rows: list[list[Any]] = []
    index_labels: list[str] = []
    for row in build_table_rows(snapshot, config):
        if row.kind == "spacer":
            continue
        index_labels.append(row.label)
        rows.append([None if value == "" else value for value in row.values])
    return pd.DataFrame(rows, index=index_labels, columns=columns)


def generate_reports(
    tokens: Mapping[str, str] | None = None,
    report_date: dt.date | None = None,
    api_url: str = DEFAULT_API_URL,
    output_dir: str | os.PathLike[str] = DEFAULT_OUTPUT_DIR,
    include_excel: bool = True,
) -> dict[str, Any]:
    resolved_tokens = {
        project_key: (token or "").strip()
        for project_key, token in (
            tokens
            or {
                "NANO": os.environ.get("NANO_API_TOKEN", ""),
                "NICO": os.environ.get("NICO_API_TOKEN", ""),
            }
        ).items()
    }
    selected_projects = [
        project_key for project_key in PROJECT_ORDER if resolved_tokens.get(project_key)
    ]
    if not selected_projects:
        raise RecruitmentReportError(
            "No REDCap API tokens were supplied. Set NANO_API_TOKEN and/or NICO_API_TOKEN."
        )

    resolved_date = report_date or dt.date.today()
    output_root = Path(output_dir)
    archive_root = output_root / ARCHIVE_SUBDIR
    snapshots: dict[str, dict[str, Any]] = {}
    renderings: dict[str, str] = {}
    errors: dict[str, str] = {}
    written_files: list[Path] = []
    archived_files: list[Path] = []

    for project_key in selected_projects:
        try:
            snapshot = _fetch_snapshot(
                project_key, resolved_tokens[project_key], resolved_date, api_url
            )
        except RecruitmentReportError as error:
            errors[project_key] = str(error)
            continue
        snapshots[project_key] = snapshot
        html = render_project_table(project_key, snapshot, PROJECT_CONFIG[project_key], resolved_date)
        renderings[project_key] = html

    if not snapshots:
        detail = "; ".join(f"{key}: {value}" for key, value in errors.items())
        raise RecruitmentReportError(f"No reports were generated. {detail}")

    archived_files = _archive_existing_dated_outputs(output_root, archive_root)

    date_stamp = resolved_date.isoformat()
    for project_key, html in renderings.items():
        standalone_html = render_dashboard_html({project_key: html}, resolved_date, api_url)
        dated_path = archive_root / f"{project_key.lower()}_recruitment_milestones_{date_stamp}.html"
        latest_path = output_root / f"{project_key.lower()}_recruitment_milestones_latest.html"
        _write_text(dated_path, standalone_html)
        _write_text(latest_path, standalone_html)
        written_files.extend([dated_path, latest_path])

    dashboard_html = render_dashboard_html(renderings, resolved_date, api_url)
    dashboard_dated_path = archive_root / f"recruitment_milestones_{date_stamp}.html"
    dashboard_latest_path = output_root / "recruitment_milestones_latest.html"
    _write_text(dashboard_dated_path, dashboard_html)
    _write_text(dashboard_latest_path, dashboard_html)
    written_files.extend([dashboard_dated_path, dashboard_latest_path])

    if include_excel:
        dated_workbook_path = archive_root / f"recruitment_milestones_{date_stamp}.xlsx"
        latest_workbook_path = output_root / "recruitment_milestones_latest.xlsx"
        with pd.ExcelWriter(dated_workbook_path) as workbook:
            for project_key, snapshot in snapshots.items():
                frame = _build_excel_sheet(snapshot, PROJECT_CONFIG[project_key])
                frame.to_excel(workbook, sheet_name=project_key[:31])
        with pd.ExcelWriter(latest_workbook_path) as workbook:
            for project_key, snapshot in snapshots.items():
                frame = _build_excel_sheet(snapshot, PROJECT_CONFIG[project_key])
                frame.to_excel(workbook, sheet_name=project_key[:31])
        written_files.extend([dated_workbook_path, latest_workbook_path])

    return {
        "report_date": resolved_date,
        "written_files": written_files,
        "archived_files": archived_files,
        "snapshots": snapshots,
        "errors": errors,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh NANO/NICO recruitment milestone tables from the REDCap API."
    )
    parser.add_argument(
        "--report-date",
        type=lambda value: dt.date.fromisoformat(value),
        help="Optional ISO date override for the report cut-off (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where HTML and Excel outputs should be written.",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help="REDCap API endpoint.",
    )
    parser.add_argument(
        "--no-excel",
        action="store_true",
        help="Skip the Excel workbook export.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    try:
        result = generate_reports(
            report_date=arguments.report_date,
            api_url=arguments.api_url,
            output_dir=arguments.output_dir,
            include_excel=not arguments.no_excel,
        )
    except RecruitmentReportError as error:
        print(str(error))
        return 1

    print(f"Report date: {result['report_date'].isoformat()}")
    for path in result.get("archived_files", []):
        print(f"archived {path}")
    for path in result["written_files"]:
        print(f"wrote {path}")
    for project_key, detail in result["errors"].items():
        print(f"warning {project_key}: {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
