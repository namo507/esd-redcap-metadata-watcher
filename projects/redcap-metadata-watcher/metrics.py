"""Ground-truth metrics derived from read-only REDCap snapshots.

Every function here is a pure transformation over the aggregate frames on a
:class:`redcap_live.StudySnapshot`. No function opens a network connection, and
none of them can see a participant-level value — the snapshot never carries one.

Completion vocabulary, used consistently across the dashboard:

``Complete`` / ``Incomplete`` / ``Unverified``
    REDCap's own ``<form>_complete`` states (2 / 0 / 1).
``Not started``
    The ``<form>_complete`` cell is empty for that record-event. In a
    longitudinal project this covers both "assigned but untouched" and "this
    form is not mapped to this event".
``Started``
    Complete + Incomplete + Unverified.
``Completion rate``
    Complete ÷ Started. Deliberately excludes ``Not started`` so that a form
    mapped to few events is not scored as if it were failing.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import pandas as pd

STATUS_ORDER = ("Complete", "Incomplete", "Unverified", "Not started")
STARTED_STATUSES = ("Complete", "Incomplete", "Unverified")

_CHOICE_TYPES = {"radio", "dropdown", "checkbox"}
_FREE_TEXT_TYPES = {"text", "notes"}


def _col(frame: pd.DataFrame, name: str, default: Any = "") -> pd.Series:
    if name in frame.columns:
        return frame[name].fillna(default).astype(str)
    return pd.Series([default] * len(frame), index=frame.index, dtype=str)


def _count_choices(value: str) -> int:
    text = (value or "").strip()
    if not text:
        return 0
    return sum(1 for part in text.split("|") if part.strip())


# --------------------------------------------------------------------------- #
# Field-level inventory
# --------------------------------------------------------------------------- #


def field_inventory(snapshot: Any) -> pd.DataFrame:
    """Return one tidy row per field, with the derived flags the filters use."""
    meta = snapshot.metadata
    if meta is None or meta.empty:
        return pd.DataFrame(
            columns=[
                "study", "field_name", "form_name", "field_type", "field_label",
                "validation", "required", "identifier", "has_branching",
                "choice_count", "has_label", "section_header", "field_note",
                "branching_logic", "choices",
            ]
        )

    frame = pd.DataFrame(index=meta.index)
    frame["study"] = snapshot.key
    frame["field_name"] = _col(meta, "field_name")
    frame["form_name"] = _col(meta, "form_name")
    frame["field_type"] = _col(meta, "field_type")
    frame["field_label"] = _col(meta, "field_label")
    frame["section_header"] = _col(meta, "section_header")
    frame["field_note"] = _col(meta, "field_note")
    frame["choices"] = _col(meta, "select_choices_or_calculations")
    frame["branching_logic"] = _col(meta, "branching_logic")
    frame["validation"] = _col(meta, "text_validation_type_or_show_slider_number")
    frame["required"] = _col(meta, "required_field").str.lower().eq("y")
    frame["identifier"] = _col(meta, "identifier").str.lower().eq("y")
    frame["has_branching"] = frame["branching_logic"].str.strip().ne("")
    frame["choice_count"] = frame["choices"].map(_count_choices)
    frame["has_label"] = frame["field_label"].str.strip().ne("")
    frame["has_validation"] = frame["validation"].str.strip().ne("")
    return frame.reset_index(drop=True)


def combined_fields(snapshots: Mapping[str, Any]) -> pd.DataFrame:
    frames = [field_inventory(s) for s in snapshots.values() if s.ok]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return field_inventory(_EmptySnapshot())
    return pd.concat(frames, ignore_index=True)


class _EmptySnapshot:
    key = ""
    metadata = pd.DataFrame()
    ok = False


# --------------------------------------------------------------------------- #
# Completion
# --------------------------------------------------------------------------- #


def completion_totals(snapshot: Any) -> dict[str, int]:
    frame = snapshot.completion
    if frame is None or frame.empty:
        return {status: 0 for status in STATUS_ORDER}
    grouped = frame.groupby("status")["count"].sum()
    return {status: int(grouped.get(status, 0)) for status in STATUS_ORDER}


def completion_rate(totals: Mapping[str, int]) -> float:
    started = sum(int(totals.get(status, 0)) for status in STARTED_STATUSES)
    if not started:
        return 0.0
    return round(100.0 * int(totals.get("Complete", 0)) / started, 1)


def instrument_summary(snapshot: Any) -> pd.DataFrame:
    """Per-instrument field counts, event assignment, and completion."""
    instruments = snapshot.instruments
    if instruments is None or instruments.empty:
        return pd.DataFrame(
            columns=[
                "study", "instrument_name", "instrument_label", "fields",
                "events_assigned", "Complete", "Incomplete", "Unverified",
                "Not started", "started", "completion_rate",
            ]
        )

    base = pd.DataFrame(
        {
            "instrument_name": _col(instruments, "instrument_name"),
            "instrument_label": _col(instruments, "instrument_label"),
        }
    )
    base["study"] = snapshot.key

    fields = field_inventory(snapshot)
    field_counts = (
        fields.groupby("form_name").size().rename("fields")
        if not fields.empty
        else pd.Series(dtype=int, name="fields")
    )
    base = base.merge(
        field_counts, how="left", left_on="instrument_name", right_index=True
    )
    base["fields"] = base["fields"].fillna(0).astype(int)

    mapping = snapshot.event_mapping
    if mapping is not None and not mapping.empty and "form" in mapping.columns:
        assigned = mapping.groupby("form").size().rename("events_assigned")
        base = base.merge(
            assigned, how="left", left_on="instrument_name", right_index=True
        )
    else:
        base["events_assigned"] = pd.NA
    base["events_assigned"] = base["events_assigned"].fillna(0).astype(int)

    completion = snapshot.completion
    if completion is not None and not completion.empty:
        pivot = (
            completion.pivot_table(
                index="instrument_name",
                columns="status",
                values="count",
                aggfunc="sum",
                fill_value=0,
            )
            .reindex(columns=list(STATUS_ORDER), fill_value=0)
            .astype(int)
        )
        base = base.merge(
            pivot, how="left", left_on="instrument_name", right_index=True
        )
    for status in STATUS_ORDER:
        if status not in base.columns:
            base[status] = 0
        base[status] = base[status].fillna(0).astype(int)

    base["started"] = base[list(STARTED_STATUSES)].sum(axis=1)
    base["completion_rate"] = [
        completion_rate(row) for row in base[list(STATUS_ORDER)].to_dict("records")
    ]
    columns = [
        "study", "instrument_name", "instrument_label", "fields",
        "events_assigned", *STATUS_ORDER, "started", "completion_rate",
    ]
    return base[columns].sort_values("instrument_name").reset_index(drop=True)


def event_summary(snapshot: Any) -> pd.DataFrame:
    """Per-event record volume and completion."""
    volume = snapshot.event_volume
    if volume is None or volume.empty:
        return pd.DataFrame(
            columns=["study", "event", "records", "rows", "Complete", "started",
                     "completion_rate"]
        )
    base = volume.copy()
    base.insert(0, "study", snapshot.key)

    completion = snapshot.completion
    if completion is not None and not completion.empty:
        pivot = (
            completion.pivot_table(
                index="event",
                columns="status",
                values="count",
                aggfunc="sum",
                fill_value=0,
            )
            .reindex(columns=list(STATUS_ORDER), fill_value=0)
            .astype(int)
        )
        base = base.merge(pivot, how="left", left_on="event", right_index=True)
    for status in STATUS_ORDER:
        if status not in base.columns:
            base[status] = 0
        base[status] = base[status].fillna(0).astype(int)

    base["started"] = base[list(STARTED_STATUSES)].sum(axis=1)
    base["completion_rate"] = [
        completion_rate(row) for row in base[list(STATUS_ORDER)].to_dict("records")
    ]
    label_map = _event_labels(snapshot)
    base["event_label"] = base["event"].map(label_map).fillna(base["event"])
    return base[
        ["study", "event", "event_label", "records", "rows", *STATUS_ORDER,
         "started", "completion_rate"]
    ].reset_index(drop=True)


def _event_labels(snapshot: Any) -> dict[str, str]:
    events = snapshot.events
    if events is None or events.empty:
        return {}
    if "unique_event_name" not in events.columns:
        return {}
    labels = _col(events, "event_name")
    return dict(zip(_col(events, "unique_event_name"), labels))


# --------------------------------------------------------------------------- #
# Study-level headline numbers
# --------------------------------------------------------------------------- #


def study_overview(snapshots: Mapping[str, Any]) -> pd.DataFrame:
    rows = []
    for snapshot in snapshots.values():
        if not snapshot.ok:
            rows.append(
                {
                    "study": snapshot.key,
                    "title": snapshot.title or snapshot.label,
                    "pid": snapshot.pid,
                    "status": snapshot.status,
                    "records": 0,
                    "instruments": 0,
                    "fields": 0,
                    "events": 0,
                    "completion_rate": 0.0,
                }
            )
            continue
        fields = field_inventory(snapshot)
        totals = completion_totals(snapshot)
        rows.append(
            {
                "study": snapshot.key,
                "title": snapshot.title,
                "pid": snapshot.pid,
                "status": snapshot.status,
                "records": int(snapshot.record_count or 0),
                "rows": int(snapshot.row_count or 0),
                "instruments": int(len(snapshot.instruments)),
                "fields": int(len(fields)),
                "events": int(len(snapshot.events)),
                "longitudinal": snapshot.longitudinal,
                "repeating": snapshot.repeating,
                "identifier_fields": int(fields["identifier"].sum()) if not fields.empty else 0,
                "required_fields": int(fields["required"].sum()) if not fields.empty else 0,
                "branching_fields": int(fields["has_branching"].sum()) if not fields.empty else 0,
                "completed_forms": totals["Complete"],
                "started_forms": sum(totals[s] for s in STARTED_STATUSES),
                "completion_rate": completion_rate(totals),
            }
        )
    return pd.DataFrame(rows)


def field_type_breakdown(snapshots: Mapping[str, Any]) -> pd.DataFrame:
    fields = combined_fields(snapshots)
    if fields.empty:
        return pd.DataFrame(columns=["study", "field_type", "count"])
    return (
        fields.groupby(["study", "field_type"])
        .size()
        .reset_index(name="count")
        .sort_values(["study", "count"], ascending=[True, False])
    )


# --------------------------------------------------------------------------- #
# Data-quality signals
# --------------------------------------------------------------------------- #


def quality_flags(snapshot: Any) -> pd.DataFrame:
    """Rule-based structural findings. Observational only — not a verdict."""
    fields = field_inventory(snapshot)
    if fields.empty:
        return pd.DataFrame(columns=["check", "count", "detail"])

    free_text = fields[fields["field_type"].isin(_FREE_TEXT_TYPES)]
    choice_fields = fields[fields["field_type"].isin(_CHOICE_TYPES)]
    checks = [
        (
            "Fields with no label",
            int((~fields["has_label"]).sum()),
            "A blank field_label renders as an unlabelled question.",
        ),
        (
            "Text fields with no validation",
            int((~free_text["has_validation"]).sum()) if not free_text.empty else 0,
            "Free-text entry without a validation type accepts any value.",
        ),
        (
            "Choice fields with no choices",
            int((choice_fields["choice_count"] == 0).sum()) if not choice_fields.empty else 0,
            "radio/dropdown/checkbox with an empty choice list.",
        ),
        (
            "Identifier-flagged fields",
            int(fields["identifier"].sum()),
            "Marked as directly identifying in REDCap. Governs de-identified exports.",
        ),
        (
            "Fields with branching logic",
            int(fields["has_branching"].sum()),
            "Conditional display; affects which fields a participant ever sees.",
        ),
        (
            "Required fields",
            int(fields["required"].sum()),
            "REDCap prompts, but does not hard-block, on a blank required field.",
        ),
        (
            "Calculated fields",
            int((fields["field_type"] == "calc").sum()),
            "Values derive from other fields and can drift if inputs change.",
        ),
    ]
    orphans = _orphan_instruments(snapshot)
    if orphans:
        checks.append(
            (
                "Instruments not mapped to any event",
                len(orphans),
                "Present in the project but unreachable: " + ", ".join(orphans[:6]),
            )
        )
    return pd.DataFrame(checks, columns=["check", "count", "detail"])


def _orphan_instruments(snapshot: Any) -> list[str]:
    mapping = snapshot.event_mapping
    instruments = snapshot.instruments
    if (
        mapping is None
        or mapping.empty
        or instruments is None
        or instruments.empty
        or "form" not in mapping.columns
    ):
        return []
    mapped = set(_col(mapping, "form"))
    all_forms = set(_col(instruments, "instrument_name"))
    return sorted(all_forms - mapped)


# --------------------------------------------------------------------------- #
# Cross-study comparison
# --------------------------------------------------------------------------- #


def instrument_matrix(snapshots: Mapping[str, Any]) -> pd.DataFrame:
    """Instrument × study presence matrix, most widely shared first."""
    presence: dict[str, dict[str, Any]] = {}
    for key, snapshot in snapshots.items():
        if not snapshot.ok or snapshot.instruments.empty:
            continue
        labels = dict(
            zip(
                _col(snapshot.instruments, "instrument_name"),
                _col(snapshot.instruments, "instrument_label"),
            )
        )
        for name, label in labels.items():
            row = presence.setdefault(
                name, {"instrument_name": name, "instrument_label": label}
            )
            row[key] = True

    if not presence:
        return pd.DataFrame(columns=["instrument_name", "instrument_label", "studies"])

    study_keys = [k for k, s in snapshots.items() if s.ok]
    frame = pd.DataFrame(list(presence.values()))
    for key in study_keys:
        if key not in frame.columns:
            frame[key] = False
        frame[key] = frame[key].fillna(False).astype(bool)
    frame["studies"] = frame[study_keys].sum(axis=1).astype(int)
    return frame[
        ["instrument_name", "instrument_label", "studies", *study_keys]
    ].sort_values(
        ["studies", "instrument_name"], ascending=[False, True]
    ).reset_index(drop=True)


def shared_instruments(snapshots: Mapping[str, Any], minimum: int = 2) -> list[str]:
    matrix = instrument_matrix(snapshots)
    if matrix.empty:
        return []
    return matrix.loc[matrix["studies"] >= minimum, "instrument_name"].tolist()


def compare_instrument(
    snapshots: Mapping[str, Any],
    instrument: str,
    studies: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Field-by-field harmonization view of one instrument across studies.

    One row per field name, one column per study holding that study's field type,
    plus a ``consistency`` verdict:

    ``identical``   present everywhere it is compared, same field type
    ``type differs``present everywhere, but the field type disagrees
    ``partial``     present in some studies and absent in others
    """
    keys = [
        k
        for k in (studies or list(snapshots))
        if k in snapshots and snapshots[k].ok
    ]
    if not keys:
        return pd.DataFrame(columns=["field_name", "consistency"])

    per_study: dict[str, pd.DataFrame] = {}
    for key in keys:
        fields = field_inventory(snapshots[key])
        per_study[key] = fields[fields["form_name"] == instrument]

    names: list[str] = []
    for frame in per_study.values():
        for name in frame["field_name"].tolist():
            if name not in names:
                names.append(name)
    if not names:
        return pd.DataFrame(columns=["field_name", "consistency"])

    rows = []
    for name in names:
        row: dict[str, Any] = {"field_name": name}
        types: list[str] = []
        labels: list[str] = []
        present = 0
        for key in keys:
            frame = per_study[key]
            match = frame[frame["field_name"] == name]
            if match.empty:
                row[key] = "—"
                continue
            present += 1
            field_type = str(match.iloc[0]["field_type"])
            row[key] = field_type
            types.append(field_type)
            labels.append(str(match.iloc[0]["field_label"]).strip().lower())

        # Only instruments the study actually has are counted as comparable.
        comparable = sum(
            1 for key in keys if instrument in set(_col(snapshots[key].instruments, "instrument_name"))
        )
        if present < comparable:
            row["consistency"] = "partial"
        elif len(set(types)) > 1:
            row["consistency"] = "type differs"
        elif len(set(labels)) > 1:
            row["consistency"] = "label differs"
        else:
            row["consistency"] = "identical"
        row["studies_with_field"] = present
        rows.append(row)

    frame = pd.DataFrame(rows)
    order = {"type differs": 0, "label differs": 1, "partial": 2, "identical": 3}
    frame["_sort"] = frame["consistency"].map(order).fillna(9)
    # Within a verdict, surface the most widely shared fields first — a field in
    # 3 of 4 studies is a more actionable gap than one that exists in only 1.
    return (
        frame.sort_values(
            ["_sort", "studies_with_field", "field_name"],
            ascending=[True, False, True],
        )
        .drop(columns="_sort")
        .reset_index(drop=True)
    )


def comparison_headline(comparison: pd.DataFrame) -> dict[str, int]:
    if comparison.empty or "consistency" not in comparison.columns:
        return {"identical": 0, "label differs": 0, "type differs": 0, "partial": 0}
    counts = comparison["consistency"].value_counts().to_dict()
    return {
        key: int(counts.get(key, 0))
        for key in ("identical", "label differs", "type differs", "partial")
    }
