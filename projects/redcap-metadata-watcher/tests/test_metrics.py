"""Metric definitions over synthetic snapshots."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

import metrics
from redcap_live import StudySnapshot


def make_snapshot(
    key: str,
    *,
    fields: list[dict],
    instruments: list[str],
    completion: list[dict] | None = None,
    events: list[str] | None = None,
    mapping: list[dict] | None = None,
    records: int = 10,
) -> StudySnapshot:
    return StudySnapshot(
        key=key,
        label=key,
        status="connected",
        status_detail="",
        fetched_at=datetime.now(timezone.utc),
        pid="1",
        title=f"{key} Study",
        project_info={"is_longitudinal": "1"},
        metadata=pd.DataFrame(fields),
        instruments=pd.DataFrame(
            [{"instrument_name": n, "instrument_label": n.title()} for n in instruments]
        ),
        events=pd.DataFrame(
            [{"unique_event_name": e, "event_name": e.upper()} for e in (events or [])]
        ),
        event_mapping=pd.DataFrame(mapping or []),
        completion=pd.DataFrame(
            completion or [],
            columns=["instrument_name", "event", "status", "count"],
        ),
        event_volume=pd.DataFrame(columns=["event", "rows", "records"]),
        record_count=records,
        row_count=records * 2,
    )


FIELDS_A = [
    {
        "field_name": "demo_id", "form_name": "demo", "field_type": "text",
        "field_label": "ID", "required_field": "y", "identifier": "y",
        "branching_logic": "", "select_choices_or_calculations": "",
        "text_validation_type_or_show_slider_number": "",
    },
    {
        "field_name": "age", "form_name": "demo", "field_type": "text",
        "field_label": "Age", "required_field": "", "identifier": "",
        "branching_logic": "[demo_id] <> ''", "select_choices_or_calculations": "",
        "text_validation_type_or_show_slider_number": "integer",
    },
    {
        "field_name": "sex", "form_name": "demo", "field_type": "radio",
        "field_label": "Sex", "required_field": "", "identifier": "",
        "branching_logic": "", "select_choices_or_calculations": "1, M | 2, F",
        "text_validation_type_or_show_slider_number": "",
    },
    {
        "field_name": "q1", "form_name": "survey", "field_type": "radio",
        "field_label": "", "required_field": "", "identifier": "",
        "branching_logic": "", "select_choices_or_calculations": "",
        "text_validation_type_or_show_slider_number": "",
    },
]


def test_field_inventory_derives_flags() -> None:
    snap = make_snapshot("A", fields=FIELDS_A, instruments=["demo", "survey"])
    inv = metrics.field_inventory(snap)

    assert len(inv) == 4
    assert set(inv["study"]) == {"A"}
    by_name = inv.set_index("field_name")
    assert bool(by_name.loc["demo_id", "required"])
    assert bool(by_name.loc["demo_id", "identifier"])
    assert bool(by_name.loc["age", "has_branching"])
    assert bool(by_name.loc["age", "has_validation"])
    assert int(by_name.loc["sex", "choice_count"]) == 2
    assert not bool(by_name.loc["q1", "has_label"])


def test_completion_rate_excludes_not_started() -> None:
    totals = {"Complete": 6, "Incomplete": 3, "Unverified": 1, "Not started": 990}
    assert metrics.completion_rate(totals) == 60.0


def test_completion_rate_of_nothing_started_is_zero() -> None:
    assert metrics.completion_rate({"Complete": 0, "Not started": 5}) == 0.0


def test_instrument_summary_joins_fields_events_and_completion() -> None:
    snap = make_snapshot(
        "A",
        fields=FIELDS_A,
        instruments=["demo", "survey"],
        completion=[
            {"instrument_name": "demo", "event": "v1", "status": "Complete", "count": 8},
            {"instrument_name": "demo", "event": "v1", "status": "Incomplete", "count": 2},
            {"instrument_name": "survey", "event": "v1", "status": "Not started", "count": 5},
        ],
        mapping=[{"unique_event_name": "v1", "form": "demo"}],
    )
    summary = metrics.instrument_summary(snap).set_index("instrument_name")

    assert int(summary.loc["demo", "fields"]) == 3
    assert int(summary.loc["survey", "fields"]) == 1
    assert int(summary.loc["demo", "events_assigned"]) == 1
    assert int(summary.loc["survey", "events_assigned"]) == 0
    assert int(summary.loc["demo", "started"]) == 10
    assert summary.loc["demo", "completion_rate"] == 80.0
    # Nothing started -> rate is 0, not NaN.
    assert summary.loc["survey", "completion_rate"] == 0.0


def test_quality_flags_report_expected_counts() -> None:
    snap = make_snapshot("A", fields=FIELDS_A, instruments=["demo", "survey"])
    flags = metrics.quality_flags(snap).set_index("check")["count"].to_dict()

    assert flags["Fields with no label"] == 1
    assert flags["Identifier-flagged fields"] == 1
    assert flags["Fields with branching logic"] == 1
    assert flags["Required fields"] == 1
    assert flags["Choice fields with no choices"] == 1  # q1 is a radio with no choices
    assert flags["Text fields with no validation"] == 1  # demo_id


def test_instrument_matrix_ranks_widely_shared_first() -> None:
    a = make_snapshot("A", fields=FIELDS_A, instruments=["demo", "survey", "only_a"])
    b = make_snapshot("B", fields=FIELDS_A, instruments=["demo", "survey"])
    matrix = metrics.instrument_matrix({"A": a, "B": b})

    assert list(matrix["instrument_name"])[:2] == ["demo", "survey"]
    assert int(matrix.loc[matrix["instrument_name"] == "demo", "studies"].iloc[0]) == 2
    assert int(matrix.loc[matrix["instrument_name"] == "only_a", "studies"].iloc[0]) == 1
    assert metrics.shared_instruments({"A": a, "B": b}) == ["demo", "survey"]


def test_compare_instrument_classifies_each_field() -> None:
    fields_b = [
        # identical to A
        dict(FIELDS_A[0]),
        # same name, different type -> type differs
        {**FIELDS_A[1], "field_type": "notes"},
        # same name and type, different label -> label differs
        {**FIELDS_A[2], "field_label": "Gender"},
        # FIELDS_A[3] (q1) omitted -> partial
        {
            "field_name": "only_b", "form_name": "demo", "field_type": "text",
            "field_label": "Only B", "required_field": "", "identifier": "",
            "branching_logic": "", "select_choices_or_calculations": "",
            "text_validation_type_or_show_slider_number": "",
        },
    ]
    a = make_snapshot("A", fields=FIELDS_A, instruments=["demo"])
    b = make_snapshot("B", fields=fields_b, instruments=["demo"])

    comparison = metrics.compare_instrument({"A": a, "B": b}, "demo")
    verdicts = comparison.set_index("field_name")["consistency"].to_dict()

    assert verdicts["demo_id"] == "identical"
    assert verdicts["age"] == "type differs"
    assert verdicts["sex"] == "label differs"
    assert verdicts["only_b"] == "partial"

    headline = metrics.comparison_headline(comparison)
    assert headline == {
        "identical": 1,
        "label differs": 1,
        "type differs": 1,
        "partial": 1,
    }
    # Most severe verdict sorts first.
    assert comparison.iloc[0]["consistency"] == "type differs"


def test_compare_instrument_missing_everywhere_is_empty() -> None:
    a = make_snapshot("A", fields=FIELDS_A, instruments=["demo"])
    assert metrics.compare_instrument({"A": a}, "nonexistent").empty


def test_study_overview_includes_failed_studies_as_zero_rows() -> None:
    good = make_snapshot("A", fields=FIELDS_A, instruments=["demo"])
    bad = StudySnapshot(
        key="B",
        label="B",
        status="failed",
        status_detail="bad token",
        fetched_at=datetime.now(timezone.utc),
    )
    overview = metrics.study_overview({"A": good, "B": bad}).set_index("study")

    assert int(overview.loc["A", "fields"]) == 4
    assert int(overview.loc["B", "fields"]) == 0
    assert overview.loc["B", "status"] == "failed"


def test_combined_fields_spans_studies() -> None:
    a = make_snapshot("A", fields=FIELDS_A, instruments=["demo"])
    b = make_snapshot("B", fields=FIELDS_A[:2], instruments=["demo"])
    combined = metrics.combined_fields({"A": a, "B": b})

    assert len(combined) == 6
    assert set(combined["study"]) == {"A", "B"}


@pytest.mark.parametrize("empty_attr", ["metadata", "instruments"])
def test_metrics_tolerate_empty_frames(empty_attr: str) -> None:
    snap = make_snapshot("A", fields=FIELDS_A, instruments=["demo"])
    setattr(snap, empty_attr, pd.DataFrame())

    assert metrics.instrument_summary(snap) is not None
    assert metrics.field_inventory(snap) is not None
    assert metrics.quality_flags(snap) is not None
