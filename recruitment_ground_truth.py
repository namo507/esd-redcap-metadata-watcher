"""Auditable recruitment classification and milestone derivation.

This module is deliberately independent of Streamlit and performs no REDCap
writes.  Callers provide raw REDCap metadata/record frames and receive:

* one participant-level audit row per record identifier,
* participant-specific data-quality issues, and
* cumulative milestone values only when their source is explicit.

Missing protocol mappings are represented as missing values.  They are never
replaced with a convenient visit, evaluation, birth, or optional-consent date.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import datetime as dt
import logging
import re
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from recruitment_config import (
    CATEGORIES,
    CATEGORY_LABELS,
    FieldExpectation,
    ProjectConfig,
)


LOGGER = logging.getLogger(__name__)
MISSING_TEXT = ""
TRUE_CODES = {"1", "true", "yes", "y"}

AUDIT_REQUIRED_COLUMNS = (
    "participant_id",
    "project",
    "raw_enrollment_status",
    "raw_enrollment_date",
    "raw_race",
    "raw_ethnicity",
    "included_in_recruitment_count",
    "racial_minority_flag",
    "hispanic_ethnicity_flag",
    "exclusion_reason",
    "milestone_period",
)

DQ_COLUMNS = (
    "project",
    "participant_id",
    "issue_type",
    "field_name",
    "raw_value",
    "detail",
)


class RecruitmentGroundTruthError(RuntimeError):
    """Raised when an output cannot be supported by the observed source data."""


@dataclass(frozen=True, slots=True)
class SchemaCheck:
    """Observed-versus-configured metadata evidence for one field."""

    field_name: str
    expected_form: str
    observed_form: str
    expected_type: str
    observed_type: str
    expected_codes: str
    observed_codes: str
    result: str
    detail: str


def _string_code(value: Any) -> str | None:
    """Normalize a REDCap raw code without converting real code ``0`` to null."""

    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


def _is_checked(value: Any) -> bool:
    code = _string_code(value)
    return bool(code and code.casefold() in TRUE_CODES)


def _distinct_codes(values: Iterable[Any]) -> list[str]:
    return sorted(
        {code for value in values if (code := _string_code(value)) is not None},
        key=lambda code: (not code.lstrip("-").isdigit(), code),
    )


def parse_choices(value: Any) -> dict[str, str]:
    """Parse REDCap ``code, label | code, label`` choice text."""

    output: dict[str, str] = {}
    for choice in str(value or "").split("|"):
        if "," not in choice:
            continue
        code, label = choice.split(",", 1)
        normalized_code = code.strip()
        if normalized_code:
            output[normalized_code] = re.sub(r"\s+", " ", label).strip()
    return output


def normalize_metadata(metadata: pd.DataFrame) -> pd.DataFrame:
    """Return metadata with an explicit ``field_name`` column."""

    if not isinstance(metadata, pd.DataFrame) or metadata.empty:
        raise RecruitmentGroundTruthError("REDCap metadata export was empty.")
    normalized = metadata.copy()
    if "field_name" not in normalized.columns:
        normalized = normalized.reset_index()
    if "field_name" not in normalized.columns:
        raise RecruitmentGroundTruthError(
            "REDCap metadata did not contain a field_name column."
        )
    normalized["field_name"] = normalized["field_name"].astype(str).str.strip()
    if normalized["field_name"].eq("").any():
        raise RecruitmentGroundTruthError(
            "REDCap metadata contained a blank field name."
        )
    return normalized


def validate_project_identity(
    project_key: str, project_info: Mapping[str, Any], config: ProjectConfig
) -> None:
    """Fail closed if a token resolves to a different REDCap project."""

    observed_id = _string_code(project_info.get("project_id"))
    expected_id = (
        str(config.expected_project_id)
        if config.expected_project_id is not None
        else None
    )
    if expected_id and observed_id != expected_id:
        raise RecruitmentGroundTruthError(
            f"{project_key}: token resolved to REDCap project {observed_id or 'unknown'}, "
            f"expected {expected_id}."
        )
    observed_title = str(project_info.get("project_title") or "").strip()
    if config.expected_project_title and observed_title != config.expected_project_title:
        raise RecruitmentGroundTruthError(
            f"{project_key}: token resolved to project title {observed_title!r}, "
            f"expected {config.expected_project_title!r}."
        )


def _expectation_check(
    metadata: pd.DataFrame, expectation: FieldExpectation
) -> SchemaCheck:
    rows = metadata.loc[metadata["field_name"].eq(expectation.field_name)]
    if rows.empty:
        return SchemaCheck(
            expectation.field_name,
            expectation.form_name,
            "",
            expectation.field_type,
            "",
            "|".join(expectation.allowed_codes),
            "",
            "FAIL",
            "Field is not defined in metadata.",
        )
    row = rows.iloc[0]
    observed_form = str(row.get("form_name", "") or "").strip()
    observed_type = str(row.get("field_type", "") or "").strip()
    choices = parse_choices(row.get("select_choices_or_calculations", ""))
    observed_codes = set(choices)
    if observed_type == "yesno" and not observed_codes:
        observed_codes = {"0", "1"}

    problems: list[str] = []
    if expectation.form_name and observed_form != expectation.form_name:
        problems.append(
            f"form is {observed_form!r}, expected {expectation.form_name!r}"
        )
    if expectation.field_type and observed_type != expectation.field_type:
        problems.append(
            f"type is {observed_type!r}, expected {expectation.field_type!r}"
        )
    missing_codes = set(expectation.allowed_codes) - observed_codes
    if missing_codes:
        problems.append(f"configured codes absent: {sorted(missing_codes)}")
    return SchemaCheck(
        expectation.field_name,
        expectation.form_name,
        observed_form,
        expectation.field_type,
        observed_type,
        "|".join(expectation.allowed_codes),
        "|".join(sorted(observed_codes)),
        "FAIL" if problems else "PASS",
        "; ".join(problems) if problems else "Observed schema matches configuration.",
    )


def validate_metadata(
    project_key: str, metadata: pd.DataFrame, config: ProjectConfig
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validate required field forms, types, and codes before record export."""

    normalized = normalize_metadata(metadata)
    checks = [
        _expectation_check(normalized, expectation)
        for expectation in config.field_expectations
    ]
    check_frame = pd.DataFrame([asdict(check) for check in checks])
    failures = check_frame.loc[check_frame["result"].eq("FAIL")]
    if not failures.empty:
        names = ", ".join(failures["field_name"].astype(str))
        raise RecruitmentGroundTruthError(
            f"{project_key}: configured REDCap schema did not validate for: {names}."
        )
    return normalized, check_frame


def minimal_record_fields(
    config: ProjectConfig, metadata: pd.DataFrame
) -> list[str]:
    """Return the deterministic, minimum record-export field list."""

    names = set(metadata["field_name"].astype(str))
    requested = [
        config.record_id,
        config.enrollment_status_field,
        config.date_anchor,
        config.race_field,
        config.eth_field,
        *(field_name for field_name, _ in config.eth_secondary),
        *config.exclusion_flags,
        *config.review_flags,
        *config.hard_exclude_flags,
        config.dual_field,
    ]
    return [
        field_name
        for field_name in dict.fromkeys(requested)
        if field_name and field_name in names
    ]


def _choice_maps(metadata: pd.DataFrame) -> dict[str, dict[str, str]]:
    maps: dict[str, dict[str, str]] = {}
    for _, row in metadata.iterrows():
        maps[str(row["field_name"])] = parse_choices(
            row.get("select_choices_or_calculations", "")
        )
    return maps


def _metadata_validation_map(metadata: pd.DataFrame) -> dict[str, str]:
    if "text_validation_type_or_show_slider_number" not in metadata.columns:
        return {}
    return {
        str(row["field_name"]): str(
            row.get("text_validation_type_or_show_slider_number", "") or ""
        )
        for _, row in metadata.iterrows()
    }


def _checkbox_codes_in_frame(records: pd.DataFrame, field_name: str) -> set[str]:
    prefix = field_name + "___"
    return {
        column[len(prefix) :]
        for column in records.columns
        if str(column).startswith(prefix)
    }


def _selected_checkbox_codes(
    group: pd.DataFrame, field_name: str, candidate_codes: Sequence[int | str]
) -> list[str]:
    selected: list[str] = []
    for candidate in candidate_codes:
        code = str(candidate)
        column = f"{field_name}___{code}"
        if column in group.columns and group[column].map(_is_checked).any():
            selected.append(code)
    return selected


def _format_codes(
    source: str, codes: Sequence[str], choice_maps: Mapping[str, Mapping[str, str]]
) -> str:
    choices = choice_maps.get(source, {})
    formatted = [
        f"{source}={code}: {choices[code]}" if code in choices else f"{source}={code}"
        for code in codes
    ]
    return " | ".join(formatted)


def _raw_distinct(group: pd.DataFrame, field_name: str) -> list[str]:
    if field_name not in group.columns:
        return []
    return _distinct_codes(group[field_name].tolist())


def _flag_is_true(
    group: pd.DataFrame, field_name: str, metadata: pd.DataFrame
) -> tuple[bool, list[str]]:
    row = metadata.loc[metadata["field_name"].eq(field_name)]
    field_type = (
        str(row.iloc[0].get("field_type", "") or "") if not row.empty else ""
    )
    if field_type == "checkbox":
        raw_codes = _selected_checkbox_codes(group, field_name, ("1",))
        return "1" in raw_codes, raw_codes
    raw_codes = _raw_distinct(group, field_name)
    return "1" in raw_codes, raw_codes


def _parse_redcap_date(value: Any, validation: str) -> dt.date | None:
    text = _string_code(value)
    if text is None:
        return None
    formats = ["%Y-%m-%d"]
    if validation == "date_mdy":
        formats.extend(("%m/%d/%Y", "%m-%d-%Y"))
    elif validation == "date_dmy":
        formats.extend(("%d/%m/%Y", "%d-%m-%Y"))
    else:
        formats.extend(("%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y", "%d-%m-%Y"))
    for pattern in formats:
        try:
            return dt.datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _date_values(
    group: pd.DataFrame, field_name: str, validation: str
) -> tuple[list[str], list[dt.date], list[str]]:
    raw_values = _raw_distinct(group, field_name)
    parsed: list[dt.date] = []
    invalid: list[str] = []
    for raw in raw_values:
        parsed_date = _parse_redcap_date(raw, validation)
        if parsed_date is None:
            invalid.append(raw)
        else:
            parsed.append(parsed_date)
    return raw_values, sorted(set(parsed)), invalid


def _first_milestone_on_or_after(
    enrollment_date: dt.date, milestones: Sequence[dt.date]
) -> str:
    for milestone in milestones:
        if enrollment_date <= milestone:
            return milestone.isoformat()
    return "AFTER_REPORTING_WINDOW"


def _ethnicity_state(
    group: pd.DataFrame,
    config: ProjectConfig,
    race_codes: Sequence[str],
) -> tuple[bool | pd._libs.missing.NAType, str, bool, bool]:
    positive_sources: list[str] = []
    negative_sources: list[str] = []
    unknown_sources: list[str] = []
    observed_sources: list[str] = []

    primary = _raw_distinct(group, config.eth_field)
    if primary:
        observed_sources.extend(f"{config.eth_field}={code}" for code in primary)
    for code in primary:
        if int(code) in config.eth_hispanic_codes if code.lstrip("-").isdigit() else False:
            positive_sources.append(f"{config.eth_field}={code}")
        elif int(code) in config.eth_non_hispanic_codes if code.lstrip("-").isdigit() else False:
            negative_sources.append(f"{config.eth_field}={code}")
        elif int(code) in config.eth_unknown_codes if code.lstrip("-").isdigit() else False:
            unknown_sources.append(f"{config.eth_field}={code}")

    secondary_positive = dict(config.eth_secondary)
    secondary_negative = dict(config.eth_secondary_non_hispanic)
    secondary_unknown = dict(config.eth_secondary_unknown)
    for field_name in dict.fromkeys(
        [*secondary_positive, *secondary_negative, *secondary_unknown]
    ):
        codes = _raw_distinct(group, field_name)
        if codes:
            observed_sources.extend(f"{field_name}={code}" for code in codes)
        for code in codes:
            if not code.lstrip("-").isdigit():
                continue
            numeric = int(code)
            if numeric in secondary_positive.get(field_name, ()):
                positive_sources.append(f"{field_name}={code}")
            elif numeric in secondary_negative.get(field_name, ()):
                negative_sources.append(f"{field_name}={code}")
            elif numeric in secondary_unknown.get(field_name, ()):
                unknown_sources.append(f"{field_name}={code}")

    if set(race_codes).intersection({str(code) for code in config.race_hispanic_codes}):
        positive_sources.append(f"{config.race_field}=Hispanic")

    conflict = bool(positive_sources and negative_sources)
    if positive_sources:
        result: bool | pd._libs.missing.NAType = True
    elif negative_sources:
        result = False
    else:
        result = pd.NA
    source_text = " | ".join(observed_sources)
    return result, source_text, conflict, bool(unknown_sources)


def _add_issue(
    issues: list[dict[str, Any]],
    project_key: str,
    participant_id: str,
    issue_type: str,
    field_name: str,
    raw_value: Any,
    detail: str,
) -> None:
    issues.append(
        {
            "project": project_key,
            "participant_id": participant_id,
            "issue_type": issue_type,
            "field_name": field_name,
            "raw_value": MISSING_TEXT if raw_value is None else str(raw_value),
            "detail": detail,
        }
    )


def collapse_participants(
    *,
    project_key: str,
    records: pd.DataFrame,
    metadata: pd.DataFrame,
    config: ProjectConfig,
    milestones: Sequence[dt.date],
    report_date: dt.date,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Collapse longitudinal/repeating exports to one auditable participant row."""

    if not isinstance(records, pd.DataFrame):
        raise RecruitmentGroundTruthError(
            f"{project_key}: record export did not return a dataframe."
        )
    frame = records.copy()
    if config.record_id not in frame.columns:
        frame = frame.reset_index()
    if config.record_id not in frame.columns:
        raise RecruitmentGroundTruthError(
            f"{project_key}: record export did not expose {config.record_id!r}."
        )

    frame["_participant_id"] = frame[config.record_id].map(_string_code)
    issues: list[dict[str, Any]] = []
    missing_id_rows = frame["_participant_id"].isna()
    if missing_id_rows.any():
        _add_issue(
            issues,
            project_key,
            "",
            "missing_participant_id",
            config.record_id,
            int(missing_id_rows.sum()),
            "Record rows with blank identifiers were excluded from participant collapse.",
        )
    frame = frame.loc[~missing_id_rows].copy()
    if frame.empty:
        empty_audit = pd.DataFrame(columns=AUDIT_REQUIRED_COLUMNS)
        return empty_audit, pd.DataFrame(issues, columns=DQ_COLUMNS)

    choices = _choice_maps(metadata)
    validations = _metadata_validation_map(metadata)
    allowed_race_codes = {
        str(code)
        for code in (
            *config.race_minority_codes,
            *config.race_white_codes,
            *config.race_unknown_codes,
            *config.race_hispanic_codes,
        )
    }
    observed_race_columns = _checkbox_codes_in_frame(frame, config.race_field)
    unexpected_race_columns = observed_race_columns - allowed_race_codes
    if unexpected_race_columns:
        _add_issue(
            issues,
            project_key,
            "",
            "unexpected_race_code",
            config.race_field,
            "|".join(sorted(unexpected_race_columns)),
            "Checkbox columns contain code(s) outside the validated configuration.",
        )

    audit_rows: list[dict[str, Any]] = []
    context_columns = [
        name
        for name in (
            "redcap_event_name",
            "redcap_repeat_instrument",
            "redcap_data_access_group",
        )
        if name in frame.columns
    ]

    for participant_id, group in frame.groupby("_participant_id", sort=True):
        race_codes = _selected_checkbox_codes(
            group, config.race_field, sorted(allowed_race_codes)
        )
        raw_race = _format_codes(config.race_field, race_codes, choices)
        specific_minority = bool(
            set(race_codes)
            & {str(code) for code in config.race_minority_codes}
        )
        if race_codes:
            minority_flag: bool | pd._libs.missing.NAType = specific_minority
        else:
            minority_flag = pd.NA

        ethnicity_flag, ethnicity_sources, eth_conflict, eth_unknown = _ethnicity_state(
            group, config, race_codes
        )
        raw_ethnicity_parts: list[str] = []
        primary_codes = _raw_distinct(group, config.eth_field)
        if primary_codes:
            raw_ethnicity_parts.append(
                _format_codes(config.eth_field, primary_codes, choices)
            )
        for field_name, _ in config.eth_secondary:
            codes = _raw_distinct(group, field_name)
            if codes:
                raw_ethnicity_parts.append(_format_codes(field_name, codes, choices))
        raw_ethnicity = " | ".join(raw_ethnicity_parts)

        raw_status_parts: list[str] = []
        exclusion_hits: list[str] = []
        review_hits: list[str] = []
        for field_name in (
            *config.exclusion_flags,
            *config.review_flags,
            *config.hard_exclude_flags,
        ):
            hit, raw_codes = _flag_is_true(group, field_name, metadata)
            raw_status_parts.append(
                f"{field_name}={'|'.join(raw_codes) if raw_codes else 'missing'}"
            )
            if hit and field_name in (
                *config.exclusion_flags,
                *config.hard_exclude_flags,
            ):
                exclusion_hits.append(f"{field_name}=1")
            elif hit:
                review_hits.append(f"{field_name}=1")
            invalid_codes = set(raw_codes) - {"0", "1"}
            if invalid_codes:
                _add_issue(
                    issues,
                    project_key,
                    participant_id,
                    "invalid_status_code",
                    field_name,
                    "|".join(sorted(invalid_codes)),
                    "Status flag contains a value outside 0/1.",
                )

        if config.enrollment_status_field:
            enrollment_status_values = _raw_distinct(
                group, config.enrollment_status_field
            )
            raw_enrollment_status = "|".join(enrollment_status_values)
            status_conflict = len(enrollment_status_values) > 1
            enrolled = (
                len(enrollment_status_values) == 1
                and enrollment_status_values[0] in config.enrolled_status_codes
            )
        elif config.record_presence_confirms_enrollment:
            enrollment_status_values = ["record_presence"]
            raw_enrollment_status = "record_presence"
            status_conflict = False
            enrolled = True
        else:
            enrollment_status_values = []
            raw_enrollment_status = "UNAVAILABLE"
            status_conflict = False
            enrolled = None

        enrollment_date: dt.date | None = None
        raw_enrollment_date = MISSING_TEXT
        date_conflict = False
        date_invalid = False
        if config.date_anchor:
            raw_dates, parsed_dates, invalid_dates = _date_values(
                group,
                config.date_anchor,
                validations.get(config.date_anchor, ""),
            )
            raw_enrollment_date = "|".join(raw_dates)
            date_conflict = len(parsed_dates) > 1
            date_invalid = bool(invalid_dates)
            if len(parsed_dates) == 1 and not invalid_dates:
                enrollment_date = parsed_dates[0]

        if exclusion_hits:
            included: bool | pd._libs.missing.NAType = False
            exclusion_reason = "; ".join(exclusion_hits)
            inclusion_decision = "excluded"
        elif status_conflict:
            included = pd.NA
            exclusion_reason = "UNRESOLVED_ENROLLMENT_STATUS_CONFLICT"
            inclusion_decision = "unresolved"
        elif enrolled is None:
            included = pd.NA
            exclusion_reason = "UNRESOLVED_NO_CONFIRMED_ENROLLMENT_STATUS"
            inclusion_decision = "unresolved"
        elif not enrolled:
            included = False
            exclusion_reason = "NOT_CONFIRMED_ENROLLED"
            inclusion_decision = "excluded"
        elif enrollment_date and enrollment_date > report_date:
            included = False
            exclusion_reason = "ENROLLMENT_AFTER_REPORT_CUTOFF"
            inclusion_decision = "excluded"
        else:
            included = True
            exclusion_reason = ""
            inclusion_decision = "included"

        if review_hits:
            inclusion_decision = (
                "review" if inclusion_decision == "included" else inclusion_decision
            )

        milestone_period = (
            _first_milestone_on_or_after(enrollment_date, milestones)
            if included is True and enrollment_date is not None
            else MISSING_TEXT
        )

        row: dict[str, Any] = {
            "participant_id": participant_id,
            "project": project_key,
            "raw_enrollment_status": raw_enrollment_status,
            "raw_enrollment_date": raw_enrollment_date,
            "raw_race": raw_race,
            "raw_ethnicity": raw_ethnicity,
            "included_in_recruitment_count": included,
            "racial_minority_flag": minority_flag,
            "hispanic_ethnicity_flag": ethnicity_flag,
            "exclusion_reason": exclusion_reason,
            "milestone_period": milestone_period,
            "inclusion_decision": inclusion_decision,
            "raw_status_flags": "; ".join(raw_status_parts),
            "review_flags": "; ".join(review_hits),
            "source_row_count": int(len(group)),
            "race_selection_count": len(race_codes),
            "ethnicity_source_values": ethnicity_sources,
        }
        if config.dual_field:
            dual_values = _raw_distinct(group, config.dual_field)
            row["raw_dual_enrollment"] = "|".join(dual_values)
        for column in context_columns:
            row[column] = "|".join(_raw_distinct(group, column))
        for diagnostic in config.diagnostic_date_fields:
            raw_dates, parsed_dates, invalid_dates = _date_values(
                group,
                diagnostic.field_name,
                validations.get(diagnostic.field_name, ""),
            )
            row[f"candidate_{diagnostic.field_name}_raw"] = "|".join(raw_dates)
            row[f"candidate_{diagnostic.field_name}_first"] = (
                parsed_dates[0].isoformat() if parsed_dates else MISSING_TEXT
            )
            row[f"candidate_{diagnostic.field_name}_count"] = len(parsed_dates)
            row[f"candidate_{diagnostic.field_name}_invalid"] = "|".join(
                invalid_dates
            )

        if not race_codes:
            _add_issue(
                issues,
                project_key,
                participant_id,
                "missing_race",
                config.race_field,
                "",
                "No configured race checkbox is selected.",
            )
        elif set(race_codes) & {str(code) for code in config.race_unknown_codes}:
            _add_issue(
                issues,
                project_key,
                participant_id,
                "unknown_other_race",
                config.race_field,
                raw_race,
                "Unknown/Other race is selected.",
            )
        if len(race_codes) > 1:
            _add_issue(
                issues,
                project_key,
                participant_id,
                "multiple_race_selections",
                config.race_field,
                raw_race,
                "Multiple race checkboxes are selected; the configured any-specific-non-White rule is applied.",
            )
        if not raw_ethnicity:
            _add_issue(
                issues,
                project_key,
                participant_id,
                "missing_ethnicity",
                config.eth_field,
                "",
                "No configured ethnicity source contains a value.",
            )
        elif eth_unknown and pd.isna(ethnicity_flag):
            _add_issue(
                issues,
                project_key,
                participant_id,
                "unknown_declined_ethnicity",
                config.eth_field,
                raw_ethnicity,
                "Only unknown/declined ethnicity values are present.",
            )
        if eth_conflict:
            _add_issue(
                issues,
                project_key,
                participant_id,
                "conflicting_ethnicity_sources",
                config.eth_field,
                raw_ethnicity,
                "Configured sources contain both Hispanic and non-Hispanic values.",
            )
        if exclusion_hits:
            _add_issue(
                issues,
                project_key,
                participant_id,
                "excluded_status",
                "|".join(config.exclusion_flags),
                "; ".join(exclusion_hits),
                "Participant is excluded by an observed ineligible/unenrolled/hard-exclusion flag.",
            )
        if review_hits:
            _add_issue(
                issues,
                project_key,
                participant_id,
                "review_status",
                "|".join(config.review_flags),
                "; ".join(review_hits),
                "Project field explicitly marks the participant for review.",
            )
        if status_conflict:
            _add_issue(
                issues,
                project_key,
                participant_id,
                "conflicting_enrollment_status",
                config.enrollment_status_field or "",
                raw_enrollment_status,
                "More than one distinct enrollment-status code is present.",
            )
        if config.date_anchor:
            if not raw_enrollment_date:
                _add_issue(
                    issues,
                    project_key,
                    participant_id,
                    "missing_enrollment_date",
                    config.date_anchor,
                    "",
                    "Confirmed enrollment-date field is blank.",
                )
            if date_invalid:
                _add_issue(
                    issues,
                    project_key,
                    participant_id,
                    "invalid_enrollment_date",
                    config.date_anchor,
                    raw_enrollment_date,
                    "Enrollment-date value does not match the configured REDCap date validation.",
                )
            if date_conflict:
                _add_issue(
                    issues,
                    project_key,
                    participant_id,
                    "conflicting_enrollment_dates",
                    config.date_anchor,
                    raw_enrollment_date,
                    "More than one distinct valid enrollment date is present.",
                )
        if included is True and not milestone_period:
            _add_issue(
                issues,
                project_key,
                participant_id,
                "unassignable_milestone",
                config.date_anchor or "",
                raw_enrollment_date,
                "Included participant has no usable confirmed enrollment date.",
            )
        audit_rows.append(row)

    if not config.enrollment_status_field and not config.record_presence_confirms_enrollment:
        _add_issue(
            issues,
            project_key,
            "",
            "unresolved_enrollment_status_mapping",
            "",
            "",
            "No protocol-confirmed enrollment-status field/rule is configured; inclusion remains unresolved unless an exclusion flag is present.",
        )
    if not config.date_anchor:
        _add_issue(
            issues,
            project_key,
            "",
            "unresolved_enrollment_date_mapping",
            "",
            "",
            "No protocol-confirmed enrollment/consent date field is configured; diagnostic dates are not used for milestones.",
        )

    audit = pd.DataFrame(audit_rows)
    ordered = [
        *AUDIT_REQUIRED_COLUMNS,
        *[column for column in audit.columns if column not in AUDIT_REQUIRED_COLUMNS],
    ]
    audit = audit.loc[:, ordered].sort_values(
        ["project", "participant_id"], kind="stable"
    )
    for column in (
        "included_in_recruitment_count",
        "racial_minority_flag",
        "hispanic_ethnicity_flag",
    ):
        audit[column] = audit[column].astype("boolean")
    issue_frame = pd.DataFrame(issues, columns=DQ_COLUMNS)
    if not issue_frame.empty:
        issue_frame = issue_frame.sort_values(
            ["project", "issue_type", "participant_id", "field_name"],
            kind="stable",
        ).reset_index(drop=True)

    LOGGER.info(
        "%s recruitment audit: %d raw rows, %d participants, %d data-quality issues",
        project_key,
        len(frame),
        len(audit),
        len(issue_frame),
    )
    return audit.reset_index(drop=True), issue_frame


def actuals_from_audit(
    audit: pd.DataFrame,
    config: ProjectConfig,
    milestones: Sequence[dt.date],
) -> dict[str, list[int | None]]:
    """Build cumulative actuals without substituting unverifiable dates."""

    if config.date_anchor and config.enrollment_status_field:
        included = audit.loc[
            audit["included_in_recruitment_count"].eq(True)
            & audit["raw_enrollment_date"].ne("")
        ].copy()
        included["_enrollment_date"] = pd.to_datetime(
            included["raw_enrollment_date"], errors="coerce"
        ).dt.date
        output: dict[str, list[int | None]] = {}
        masks = {
            "Total": pd.Series(True, index=included.index),
            "Minority": included["racial_minority_flag"].eq(True),
            "Hispanic": included["hispanic_ethnicity_flag"].eq(True),
        }
        for category, category_mask in masks.items():
            output[category] = [
                int(
                    (
                        category_mask
                        & included["_enrollment_date"].notna()
                        & included["_enrollment_date"].le(milestone)
                    ).sum()
                )
                for milestone in milestones
            ]
        return output

    output = {category: [None] * len(milestones) for category in CATEGORIES}
    for category in CATEGORIES:
        for index, value in enumerate(config.historical_actuals.get(category, [])):
            if index < len(milestones):
                output[category][index] = int(value)
    return output


def inventory_counts(audit: pd.DataFrame) -> dict[str, int | None]:
    """Return aggregate source inventory without claiming unresolved inclusion."""

    definite_included = audit["included_in_recruitment_count"].eq(True)
    unresolved = audit["included_in_recruitment_count"].isna()
    return {
        "participant_records": int(len(audit)),
        "definitely_included": int(definite_included.sum()),
        "definitely_excluded": int(
            audit["included_in_recruitment_count"].eq(False).sum()
        ),
        "unresolved_inclusion": int(unresolved.sum()),
        "racial_minority_observed": int(
            audit["racial_minority_flag"].eq(True).sum()
        ),
        "hispanic_observed": int(
            audit["hispanic_ethnicity_flag"].eq(True).sum()
        ),
    }


def summary_long(
    *,
    project_key: str,
    config: ProjectConfig,
    milestones: Sequence[dt.date],
    actuals: Mapping[str, Sequence[int | None]],
) -> pd.DataFrame:
    """Return one normalized summary row per project/category/milestone."""

    rows: list[dict[str, Any]] = []
    for category in CATEGORIES:
        previous = config.previous_targets.get(category, [])
        current = config.current_targets.get(category, [])
        category_actuals = actuals.get(category, [])
        for index, milestone in enumerate(milestones):
            previous_target = previous[index] if index < len(previous) else None
            current_target = current[index] if index < len(current) else None
            actual = (
                category_actuals[index] if index < len(category_actuals) else None
            )
            ratio = (
                actual / current_target
                if actual is not None and current_target not in (None, 0)
                else None
            )
            if current_target in (None, 0):
                status = "N/A"
            elif actual is None:
                status = "Not reported"
            elif actual >= current_target:
                status = "On target"
            else:
                status = "Behind"
            rows.append(
                {
                    "Project": project_key,
                    "Category": CATEGORY_LABELS[category],
                    "Milestone": milestone.isoformat(),
                    "Previous Target": previous_target,
                    "Current Target": current_target,
                    "Actual": actual,
                    "Actual/Target Ratio": ratio,
                    "Status": status,
                    "Target Provenance": config.target_provenance,
                    "Actual Provenance": config.actual_provenance,
                }
            )
    return pd.DataFrame(rows)


def combined_summary(
    project_summaries: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Combine project summaries and add complete-case aggregate rows.

    A cross-project value is emitted only when both NANO and NICO supply that
    metric for the same category/milestone.  This prevents a NANO-only value
    from being mislabeled as a combined value when NICO is unavailable.
    """

    if not project_summaries:
        return pd.DataFrame()
    ordered = [
        project_summaries[key]
        for key in ("NANO", "NICO")
        if key in project_summaries
    ]
    projects = pd.concat(ordered, ignore_index=True)
    if not {"NANO", "NICO"}.issubset(project_summaries):
        return projects

    def complete_sum(series: pd.Series) -> int | float | None:
        numeric = pd.to_numeric(series, errors="coerce")
        if len(numeric) != 2 or numeric.isna().any():
            return None
        total = numeric.sum()
        return int(total) if float(total).is_integer() else float(total)

    combined_rows: list[dict[str, Any]] = []
    for (category, milestone), group in projects.groupby(
        ["Category", "Milestone"], sort=False
    ):
        previous_target = complete_sum(group["Previous Target"])
        current_target = complete_sum(group["Current Target"])
        actual = complete_sum(group["Actual"])
        ratio = (
            actual / current_target
            if actual is not None and current_target not in (None, 0)
            else None
        )
        if current_target in (None, 0):
            status = "N/A"
        elif actual is None:
            status = "Not reported"
        elif actual >= current_target:
            status = "On target"
        else:
            status = "Behind"
        combined_rows.append(
            {
                "Project": "COMBINED",
                "Category": category,
                "Milestone": milestone,
                "Previous Target": previous_target,
                "Current Target": current_target,
                "Actual": actual,
                "Actual/Target Ratio": ratio,
                "Status": status,
                "Target Provenance": (
                    "Sum of NANO and NICO only when both project values are present; "
                    "otherwise N/A."
                ),
                "Actual Provenance": (
                    "Sum of NANO and NICO only when both project values are present; "
                    "otherwise N/A."
                ),
            }
        )
    return pd.concat(
        [projects, pd.DataFrame(combined_rows)],
        ignore_index=True,
    )


__all__ = [
    "AUDIT_REQUIRED_COLUMNS",
    "DQ_COLUMNS",
    "RecruitmentGroundTruthError",
    "SchemaCheck",
    "actuals_from_audit",
    "collapse_participants",
    "combined_summary",
    "inventory_counts",
    "minimal_record_fields",
    "normalize_metadata",
    "parse_choices",
    "summary_long",
    "validate_metadata",
    "validate_project_identity",
]
