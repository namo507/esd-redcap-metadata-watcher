"""Study-specific, source-backed configuration for recruitment reporting.

Only fields whose REDCap metadata and record-export access were observed are
listed here.  Neither project currently exposes a protocol-confirmed primary
enrollment/consent status field or enrollment/consent date field in the
repository evidence.  Those mappings therefore remain ``None`` and the
pipeline must not substitute a visit, evaluation, birth, or optional-consent
date.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


MONTH_LABELS = {4: "Apr 1", 8: "Aug 1", 12: "Dec 1"}
PROJECT_ORDER = ("NANO", "NICO")
CATEGORIES = ("Total", "Minority", "Hispanic")
CATEGORY_LABELS = {
    "Total": "Total Recruitment",
    "Minority": "Racial Minority Recruitment",
    "Hispanic": "Hispanic Ethnicity Recruitment",
}


@dataclass(frozen=True, slots=True)
class FieldExpectation:
    """Expected REDCap schema for one recruitment-relevant field."""

    field_name: str
    form_name: str
    field_type: str
    allowed_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiagnosticDateField:
    """Accessible date field that is explicitly not an enrollment anchor."""

    field_name: str
    description: str


@dataclass(frozen=True)
class ProjectConfig:
    """Complete configuration required to acquire and classify one project."""

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

    expected_project_id: int | None = None
    expected_project_title: str | None = None
    field_expectations: tuple[FieldExpectation, ...] = ()
    eth_non_hispanic_codes: tuple[int, ...] = ()
    eth_secondary_non_hispanic: tuple[tuple[str, tuple[int, ...]], ...] = ()
    eth_secondary_unknown: tuple[tuple[str, tuple[int, ...]], ...] = ()
    enrollment_status_field: str | None = None
    enrolled_status_codes: tuple[str, ...] = ()
    record_presence_confirms_enrollment: bool = False
    diagnostic_date_fields: tuple[DiagnosticDateField, ...] = ()
    target_provenance: str = ""
    actual_provenance: str = ""


PROJECT_CONFIG: dict[str, ProjectConfig] = {
    "NANO": ProjectConfig(
        label="NANO Study",
        grant="MH132925",
        study_title="The Role of Autonomic Regulation of Attention in the Emergence of ASD",
        expected_project_id=4218,
        expected_project_title="NANO Study Surveys",
        record_id="demo_id",
        race_field="fif_childrace",
        race_minority_codes=(1, 2, 3, 4),
        race_white_codes=(5,),
        race_unknown_codes=(6,),
        race_hispanic_codes=(),
        eth_field="fif_childethnicity",
        eth_hispanic_codes=(1,),
        eth_non_hispanic_codes=(2,),
        eth_unknown_codes=(3,),
        exclusion_flags=("demo_ineligible", "demo_unenrolled"),
        review_flags=("demo_exclude",),
        date_anchor=None,
        enrollment_status_field=None,
        record_presence_confirms_enrollment=False,
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
        field_expectations=(
            FieldExpectation(
                "demo_id", "demographics_complete_this_first", "text"
            ),
            FieldExpectation(
                "fif_childrace",
                "family_information_form",
                "checkbox",
                ("1", "2", "3", "4", "5", "6"),
            ),
            FieldExpectation(
                "fif_childethnicity",
                "family_information_form",
                "radio",
                ("1", "2", "3"),
            ),
            FieldExpectation(
                "demo_ineligible",
                "demographics_complete_this_first",
                "yesno",
                ("0", "1"),
            ),
            FieldExpectation(
                "demo_unenrolled",
                "demographics_complete_this_first",
                "yesno",
                ("0", "1"),
            ),
            FieldExpectation(
                "demo_exclude",
                "demographics_complete_this_first",
                "checkbox",
                ("1",),
            ),
        ),
        diagnostic_date_fields=(
            DiagnosticDateField("visit_date", "Visit date"),
            DiagnosticDateField("bsrc_doe", "Data-sharing consent date"),
            DiagnosticDateField(
                "papf_parent_date", "Optional media/advertising form date"
            ),
            DiagnosticDateField(
                "fif_doe", "Family information form evaluation date"
            ),
        ),
        target_provenance=(
            "Values transcribed unchanged from the user-supplied reference table "
            "and the existing repository configuration; protocol verification is "
            "not present in the repository."
        ),
        actual_provenance=(
            "Published cumulative values transcribed unchanged from the "
            "user-supplied reference table; no enrollment-date recomputation."
        ),
        footnote_detail=(
            "No protocol-confirmed enrollment status/date field is configured. "
            "Published actuals are preserved and are not overwritten by the live "
            "record inventory."
        ),
    ),
    "NICO": ProjectConfig(
        label="NICO Study",
        grant=None,
        study_title="NICO Study",
        expected_project_id=3836,
        expected_project_title="NICO Study",
        record_id="id",
        race_field="race",
        race_minority_codes=(1, 2, 3, 5),
        race_white_codes=(6,),
        race_unknown_codes=(7,),
        race_hispanic_codes=(4,),
        eth_field="fif_childethnicity",
        eth_hispanic_codes=(1,),
        eth_non_hispanic_codes=(2,),
        eth_unknown_codes=(3,),
        eth_secondary=(("ethnicity", (0,)),),
        eth_secondary_non_hispanic=(("ethnicity", (1,)),),
        eth_secondary_unknown=(("ethnicity", (2,)),),
        exclusion_flags=("demo_ineligible", "demo_unenrolled"),
        review_flags=("demo_exclude",),
        dual_field="dual_enrolled",
        date_anchor=None,
        enrollment_status_field=None,
        record_presence_confirms_enrollment=False,
        milestone_start=dt.date(2024, 8, 1),
        milestone_end=dt.date(2028, 12, 1),
        milestone_months=(4, 8, 12),
        field_expectations=(
            FieldExpectation("id", "demographics_complete_this_first", "text"),
            FieldExpectation(
                "race",
                "infant_demographics",
                "checkbox",
                ("1", "2", "3", "4", "5", "6", "7"),
            ),
            FieldExpectation(
                "fif_childethnicity",
                "family_information_and_demographics",
                "radio",
                ("1", "2", "3"),
            ),
            FieldExpectation(
                "ethnicity", "prapare", "radio", ("0", "1", "2")
            ),
            FieldExpectation(
                "demo_ineligible",
                "demographics_complete_this_first",
                "yesno",
                ("0", "1"),
            ),
            FieldExpectation(
                "demo_unenrolled",
                "demographics_complete_this_first",
                "yesno",
                ("0", "1"),
            ),
            FieldExpectation(
                "demo_exclude",
                "demographics_complete_this_first",
                "checkbox",
                ("1",),
            ),
            FieldExpectation(
                "dual_enrolled",
                "demographics_complete_this_first",
                "dropdown",
                ("0", "1"),
            ),
        ),
        diagnostic_date_fields=(
            DiagnosticDateField("visit_date", "Visit date"),
            DiagnosticDateField("dob", "Infant date of birth"),
        ),
        target_provenance=(
            "No NICO NIH milestone targets are present in the supplied reference "
            "table or repository configuration."
        ),
        actual_provenance=(
            "No protocol-confirmed enrollment date or published NICO milestone "
            "history is present; milestone actuals remain unavailable."
        ),
        footnote_detail=(
            "No protocol-confirmed enrollment status/date field or milestone "
            "target/history is configured. Missing values remain N/A."
        ),
    ),
}

