"""Generate REDCap-backed recruitment milestone tables for NANO and NICO."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from redcap import Project

from recruitment_config import (
    CATEGORIES,
    CATEGORY_LABELS,
    MONTH_LABELS,
    PROJECT_CONFIG,
    PROJECT_ORDER,
    ProjectConfig,
)
from recruitment_ground_truth import (
    actuals_from_audit,
    collapse_participants,
    combined_summary,
    inventory_counts,
    minimal_record_fields,
    summary_long,
    validate_metadata,
    validate_project_identity,
)
from recruitment_workbooks import write_csv_package, write_project_workbook
from redcap_client import GlobalRequestPacer, sanitize_error

DEFAULT_API_URL = os.getenv("REDCAP_API_URL", "https://redcap.research.sc.edu/api/")
DEFAULT_OUTPUT_DIR = "recruitment_outputs"
DEFAULT_SECURE_OUTPUT_DIR = "recruitment_audit_secure"
ARCHIVE_SUBDIR = "archive"
MIN_REQUEST_INTERVAL_SECONDS = float(
    os.getenv("REDCAP_MIN_REQUEST_INTERVAL_SECONDS", "1.25")
)

STATUS_ON_TARGET = "\u2705"
STATUS_BEHIND = "\u26a0\ufe0f"
STATUS_PENDING = "\U0001f4cb"

@dataclass(frozen=True)
class TableRow:
    label: str
    kind: str
    values: tuple[Any, ...]

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
            "Reference targets and published actuals are reproduced unchanged; "
            "no protocol-confirmed enrollment status/date mapping is available.",
            "notice-provisional",
        )
    return (
        "N/A - no NICO milestone targets/history or protocol-confirmed "
        "enrollment status/date mapping is present.",
        "notice-pending",
    )


def _project_footnote(config: ProjectConfig, report_date: dt.date) -> str:
    parts = [
        "Actual/Target Ratio = Actual / Current Target x 100.",
        "N/A appears when an actual or target is unavailable, or the target is 0.",
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


def build_classification(
    project: Project,
    metadata: pd.DataFrame,
    config: ProjectConfig,
    strict_eligibility: bool = False,
) -> tuple[pd.DataFrame, str, int, int, str | None]:
    """Compatibility wrapper around the fail-closed participant audit.

    ``strict_eligibility`` is retained for API compatibility only. Source
    ineligible/unenrolled flags are always exclusions under the current
    ground-truth contract, and unresolved affirmative enrollment remains
    missing rather than being counted from record presence.
    """

    del strict_eligibility
    project_key = next(
        (
            key
            for key, configured in PROJECT_CONFIG.items()
            if configured is config or configured == config
        ),
        config.label,
    )
    normalized_metadata, _ = validate_metadata(
        project_key, metadata, config
    )
    record_fields = minimal_record_fields(config, normalized_metadata)
    raw_records = _paced_call(
        project,
        "export_records",
        format_type="df",
        fields=record_fields,
        raw_or_label="raw",
    )
    records = (
        raw_records.reset_index()
        if isinstance(raw_records, pd.DataFrame)
        else raw_records
    )
    milestones = build_milestones(config)
    participants, _ = collapse_participants(
        project_key=project_key,
        records=records,
        metadata=normalized_metadata,
        config=config,
        milestones=milestones,
        report_date=dt.date.today(),
    )
    participants["is_minority"] = participants[
        "racial_minority_flag"
    ].astype("Int64")
    participants["is_hispanic"] = participants[
        "hispanic_ethnicity_flag"
    ].astype("Int64")
    participants["in_cumulative"] = participants[
        "included_in_recruitment_count"
    ].astype("Int64")
    participants["decision"] = participants["inclusion_decision"]
    participants["reason"] = participants["exclusion_reason"]
    return (
        participants,
        config.record_id,
        len(records),
        len(participants),
        config.dual_field,
    )


def live_actuals(participants: pd.DataFrame) -> dict[str, int]:
    inclusion_column = (
        "included_in_recruitment_count"
        if "included_in_recruitment_count" in participants.columns
        else "in_cumulative"
    )
    included = participants.loc[
        participants[inclusion_column].astype("boolean").fillna(False)
    ]
    minority_column = (
        "racial_minority_flag"
        if "racial_minority_flag" in participants.columns
        else "is_minority"
    )
    hispanic_column = (
        "hispanic_ethnicity_flag"
        if "hispanic_ethnicity_flag" in participants.columns
        else "is_hispanic"
    )
    return {
        "Total": int(len(included)),
        "Minority": int(
            included[minority_column].astype("boolean").fillna(False).sum()
        ),
        "Hispanic": int(
            included[hispanic_column].astype("boolean").fillna(False).sum()
        ),
    }


def actuals_by_milestone(
    snapshot: Mapping[str, Any], config: ProjectConfig, category: str
) -> list[int | None]:
    milestones = snapshot["milestones"]
    explicit_actuals = snapshot.get("actuals_by_category", {}).get(category)
    if explicit_actuals is not None:
        return pad_series(explicit_actuals, len(milestones))

    # Compatibility path for callers that build a presentation-only snapshot.
    # Published history is preserved as supplied; a live inventory total is
    # never inserted into a historical milestone.
    values: list[int | None] = [None] * len(milestones)
    for index, value in enumerate(config.historical_actuals.get(category, [])):
        if index < len(values):
            values[index] = value
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
      <p>Read-only REDCap validation for NANO and NICO; published milestone values are preserved and unavailable values remain N/A. Report cut-off: {escape(report_date.isoformat())}. Source endpoint: {escape(api_url)}.</p>
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


def _first_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            if isinstance(item, Mapping):
                return dict(item)
    return {}


def _as_bool(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes"}


def _fetch_snapshot(
    project_key: str, token: str, report_date: dt.date, api_url: str
) -> dict[str, Any]:
    config = PROJECT_CONFIG[project_key]
    try:
        project = Project(api_url, token, timeout=(10, 60))
        project_info = _first_mapping(
            _paced_call(project, "export_project_info", format_type="json")
        )
        validate_project_identity(project_key, project_info, config)

        raw_metadata = _paced_call(project, "export_metadata", format_type="df")
        metadata, schema_checks = validate_metadata(
            project_key, raw_metadata, config
        )
        instruments = _paced_call(
            project, "export_instruments", format_type="json"
        )
        access_evidence: dict[str, bool] = {
            "project_info": True,
            "metadata": True,
            "instruments": True,
        }

        events: Any = []
        event_mappings: Any = []
        repeating: Any = []
        if _as_bool(project_info.get("is_longitudinal")):
            events = _paced_call(project, "export_events", format_type="json")
            event_mappings = _paced_call(
                project,
                "export_instrument_event_mappings",
                format_type="json",
            )
            access_evidence["events"] = True
            access_evidence["event_mappings"] = True
        if _as_bool(project_info.get("has_repeating_instruments_or_events")):
            repeating = _paced_call(
                project,
                "export_repeating_instruments_events",
                format_type="json",
            )
            access_evidence["repeating"] = True

        record_fields = minimal_record_fields(config, metadata)
        raw_records = _paced_call(
            project,
            "export_records",
            format_type="df",
            fields=record_fields,
            raw_or_label="raw",
        )
        records = (
            raw_records.reset_index()
            if isinstance(raw_records, pd.DataFrame)
            else raw_records
        )
        access_evidence["minimal_records"] = True
        milestones = build_milestones(config)
        participants, data_quality = collapse_participants(
            project_key=project_key,
            records=records,
            metadata=metadata,
            config=config,
            milestones=milestones,
            report_date=report_date,
        )
        actuals = actuals_from_audit(participants, config, milestones)
        inventory = inventory_counts(participants)
        project_summary = summary_long(
            project_key=project_key,
            config=config,
            milestones=milestones,
            actuals=actuals,
        )
    except Exception as error:
        raise RecruitmentReportError(
            f"{project_key}: {sanitize_error(error)}"
        ) from error

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
        "project_info": project_info,
        "metadata": metadata,
        "schema_checks": schema_checks,
        "instruments": instruments,
        "events": events,
        "event_mappings": event_mappings,
        "repeating": repeating,
        "record_fields": record_fields,
        "participants": participants,
        "identifier_column": config.record_id,
        "raw_rows": len(records),
        "unique_participants": len(participants),
        "dual_field": config.dual_field,
        "milestones": milestones,
        "current_index": current_index,
        "latest_completed_index": latest_completed_index,
        "actuals_by_category": actuals,
        "inventory": inventory,
        "access_evidence": access_evidence,
        "data_quality": data_quality,
        "summary_long": project_summary,
    }
    return snapshot


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _is_dated_output_file(path: Path) -> bool:
    return bool(
        re.match(
            r"^(nano|nico)_recruitment_milestones_\d{4}-\d{2}-\d{2}\.(html|xlsx)$",
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
    secure_output_dir: str | os.PathLike[str] = DEFAULT_SECURE_OUTPUT_DIR,
    include_secure_audit: bool = True,
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
    secure_written_files: list[Path] = []
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
    if len(selected_projects) > 1 and set(snapshots) != set(selected_projects):
        detail = "; ".join(f"{key}: {value}" for key, value in errors.items())
        raise RecruitmentReportError(
            "A complete multi-project refresh was not available; no outputs were "
            f"published. {detail}"
        )

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

        for project_key, snapshot in snapshots.items():
            config = PROJECT_CONFIG[project_key]
            dated_project_workbook = (
                archive_root
                / f"{project_key.lower()}_recruitment_milestones_{date_stamp}.xlsx"
            )
            latest_project_workbook = (
                output_root
                / f"{project_key.lower()}_recruitment_milestones_latest.xlsx"
            )
            workbook_arguments = {
                "project_key": project_key,
                "config": config,
                "milestones": snapshot["milestones"],
                "actuals": snapshot["actuals_by_category"],
                "report_date": resolved_date,
                "project_info": snapshot["project_info"],
                "inventory": snapshot["inventory"],
                "access_evidence": snapshot["access_evidence"],
            }
            write_project_workbook(path=dated_project_workbook, **workbook_arguments)
            write_project_workbook(path=latest_project_workbook, **workbook_arguments)
            written_files.extend(
                [dated_project_workbook, latest_project_workbook]
            )

    if include_secure_audit and set(PROJECT_ORDER).issubset(snapshots):
        secure_root = Path(secure_output_dir)
        project_summaries = {
            project_key: snapshots[project_key]["summary_long"]
            for project_key in PROJECT_ORDER
        }
        combined = combined_summary(project_summaries)
        all_quality = pd.concat(
            [
                snapshots[project_key]["data_quality"]
                for project_key in PROJECT_ORDER
            ],
            ignore_index=True,
        )
        project_audits = {
            project_key: snapshots[project_key]["participants"]
            for project_key in PROJECT_ORDER
        }
        if include_excel:
            for project_key in PROJECT_ORDER:
                snapshot = snapshots[project_key]
                secure_workbook = (
                    secure_root
                    / f"{project_key.lower()}_recruitment_ground_truth_{date_stamp}.xlsx"
                )
                write_project_workbook(
                    path=secure_workbook,
                    project_key=project_key,
                    config=PROJECT_CONFIG[project_key],
                    milestones=snapshot["milestones"],
                    actuals=snapshot["actuals_by_category"],
                    report_date=resolved_date,
                    project_info=snapshot["project_info"],
                    inventory=snapshot["inventory"],
                    access_evidence=snapshot["access_evidence"],
                    participant_audit=snapshot["participants"],
                    combined_milestone_summary=combined,
                    data_quality_issues=snapshot["data_quality"],
                )
                secure_written_files.append(secure_workbook)
        secure_written_files.extend(
            write_csv_package(
                output_dir=secure_root / f"csv_package_{date_stamp}",
                project_audits=project_audits,
                project_summaries=project_summaries,
                combined=combined,
                data_quality=all_quality,
            )
        )

    return {
        "report_date": resolved_date,
        "written_files": written_files,
        "secure_written_files": secure_written_files,
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
    parser.add_argument(
        "--secure-output-dir",
        default=DEFAULT_SECURE_OUTPUT_DIR,
        help=(
            "Ignored local directory for restricted participant audits and the "
            "six-file CSV package."
        ),
    )
    parser.add_argument(
        "--no-secure-audit",
        action="store_true",
        help="Do not write participant-level workbooks or CSVs.",
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
            secure_output_dir=arguments.secure_output_dir,
            include_secure_audit=not arguments.no_secure_audit,
        )
    except RecruitmentReportError as error:
        print(str(error))
        return 1

    print(f"Report date: {result['report_date'].isoformat()}")
    for path in result.get("archived_files", []):
        print(f"archived {path}")
    for path in result["written_files"]:
        print(f"wrote {path}")
    if result.get("secure_written_files"):
        print(
            "wrote restricted audit package: "
            f"{len(result['secure_written_files'])} files"
        )
    for project_key, detail in result["errors"].items():
        print(f"warning {project_key}: {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
