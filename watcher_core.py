"""Pure, deterministic transformations for the REDCap metadata watcher.

This module intentionally has no network, Streamlit, PyCap, filesystem, or secret
handling.  Callers pass the results of read-only REDCap exports in and receive
pandas objects back.  Comparisons use exact normalized field names and exact
normalized metadata values; no fuzzy label matching or concept inference occurs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import unescape
from itertools import combinations
import re
from typing import Any

import pandas as pd


DEFAULT_DOE_DOC_PATTERNS: tuple[str, ...] = (
    r"\bdoe\b",
    r"\bdoc\b",
    r"date_of_eval",
    r"date_of_eval\w*",
    r"eval_date",
    r"assess.*date",
    r"visit_date",
)

TEXT_COLUMNS: tuple[str, ...] = (
    "field_name",
    "form_name",
    "section_header",
    "field_type",
    "field_label",
    "select_choices_or_calculations",
    "field_note",
    "text_validation_type_or_show_slider_number",
    "text_validation_min",
    "text_validation_max",
    "identifier",
    "branching_logic",
    "required_field",
    "custom_alignment",
    "question_number",
    "matrix_group_name",
    "matrix_ranking",
    "field_annotation",
)

DERIVED_COLUMNS: tuple[str, ...] = (
    "design_row_id",
    "export_field_name",
    "export_choice_value",
    "field_key",
    "field_prefix",
    "is_required",
    "has_branching",
    "is_validated",
    "is_identifier",
    "is_matrix",
    "missing_label",
    "is_doe_doc",
    "action_tags",
    "has_action_tags",
    "tag_hidden",
    "tag_readonly",
    "tag_calctext",
    "field_type_detail",
)

OVERLAP_COLUMNS: tuple[str, ...] = (
    "metric",
    "scope",
    "project",
    "other_project",
    "reference_project",
    "count",
    "field_keys",
)

MISSING_PROFILE_COLUMNS: tuple[str, ...] = (
    "issue_type",
    "project",
    "field_name",
    "field_key",
    "instrument",
    "compared_with",
    "observed",
    "details",
)

_TAG_PATTERN = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_-]*)")
_HTML_TAG_PATTERN = re.compile(r"<[^>]*>", flags=re.DOTALL)
_WHITESPACE_PATTERN = re.compile(r"\s+")
_TRUE_VALUES = frozenset({"1", "true", "t", "yes", "y"})


def _safe_text(value: Any) -> str:
    """Return a string for any scalar-like input without propagating row errors."""

    if value is None:
        return ""
    try:
        missing = pd.isna(value)
        if isinstance(missing, bool) and missing:
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - exceptionally defensive
            return ""
    try:
        return str(value)
    except Exception:  # pragma: no cover - custom objects can raise from __str__
        return ""


def _plain_text(value: Any) -> str:
    """Normalize display text for tests/comparison while removing HTML markup."""

    try:
        text = unescape(_safe_text(value))
        text = _HTML_TAG_PATTERN.sub(" ", text).replace("\xa0", " ")
        return _WHITESPACE_PATTERN.sub(" ", text).strip()
    except Exception:  # pragma: no cover - malformed rows must not stop a run
        return ""


def _comparison_text(value: Any) -> str:
    """Exact normalized comparison value (not a similarity heuristic)."""

    return _plain_text(value).casefold()


def _field_key(value: Any) -> str:
    return _safe_text(value).strip().casefold()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _safe_text(value).strip().casefold() in _TRUE_VALUES


def _nonblank(value: Any) -> bool:
    return bool(_safe_text(value).strip())


def _extract_action_tags(value: Any) -> str:
    """Extract all REDCap action tags, preserving first-seen order."""

    seen: set[str] = set()
    tags: list[str] = []
    try:
        matches = _TAG_PATTERN.findall(_safe_text(value))
    except Exception:  # pragma: no cover - exceptionally defensive
        matches = []
    for match in matches:
        tag = f"@{match.upper()}"
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return "; ".join(tags)


def _coerce_frame(value: Any) -> pd.DataFrame:
    if value is None:
        return pd.DataFrame()
    if isinstance(value, pd.DataFrame):
        return value.copy(deep=True)
    try:
        return pd.DataFrame(value).copy(deep=True)
    except Exception:
        return pd.DataFrame()


def _materialize_index_column(
    frame: pd.DataFrame,
    target: str,
    index_aliases: Sequence[str] = (),
) -> pd.DataFrame:
    """Materialize a PyCap index while guarding index-and-column collisions."""

    result = frame.copy(deep=True)
    if target in result.columns:
        return result.reset_index(drop=True)

    index_name = _safe_text(result.index.name).strip()
    valid_index_names = {target, *index_aliases}
    if index_name in valid_index_names:
        result = result.reset_index()
        if index_name != target:
            result = result.rename(columns={index_name: target})
        return result

    # PyCap normally names the index.  This fallback also handles callers that
    # stripped the index name while retaining a non-default string index.
    if not isinstance(result.index, pd.RangeIndex):
        result = result.reset_index().rename(columns={"index": target})
    return result


def _compile_patterns(patterns: Sequence[str] | None) -> tuple[re.Pattern[str], ...]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns if patterns is not None else DEFAULT_DOE_DOC_PATTERNS:
        try:
            compiled.append(re.compile(_safe_text(pattern), flags=re.IGNORECASE))
        except re.error:
            # A bad configurable pattern should not make the metadata unusable.
            continue
    return tuple(compiled)


def _matches_any(value: Any, patterns: Sequence[re.Pattern[str]]) -> bool:
    try:
        return any(pattern.search(_safe_text(value)) is not None for pattern in patterns)
    except Exception:  # pragma: no cover - malformed rows must not stop a run
        return False


def normalize_metadata(
    metadata: Any,
    export_field_names: Any = None,
    doe_doc_patterns: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Normalize one project's REDCap metadata and merge export field names.

    ``metadata`` may have ``field_name`` as either an index or a column.
    ``export_field_names`` may have ``original_field_name`` as either an index or
    a column.  Checkbox mappings are intentionally one-to-many, so the result is
    export-expanded.  ``design_row_id`` lets summaries count original metadata
    rows without silently treating checkbox choices as separate design fields.
    """

    frame = _materialize_index_column(_coerce_frame(metadata), "field_name")
    if "field_name" not in frame.columns:
        frame["field_name"] = pd.Series(dtype="object")

    for column in TEXT_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
        frame[column] = frame[column].map(_safe_text)

    # A stable id must be assigned before the one-to-many export-name merge.
    frame = frame.reset_index(drop=True)
    frame["design_row_id"] = range(len(frame))
    frame["_merge_key"] = frame["field_name"].map(_field_key)

    existing_export = None
    if "export_field_name" in frame.columns:
        existing_export = frame["export_field_name"].map(_safe_text)
        frame = frame.drop(columns=["export_field_name"])
    frame["_existing_export_field_name"] = (
        existing_export if existing_export is not None else ""
    )

    exports = _coerce_frame(export_field_names)
    exports = _materialize_index_column(
        exports,
        "original_field_name",
        index_aliases=("field_name",),
    )
    if "original_field_name" not in exports.columns and "field_name" in exports.columns:
        exports = exports.rename(columns={"field_name": "original_field_name"})

    export_subset = pd.DataFrame(
        columns=["_merge_key", "export_field_name", "export_choice_value", "_export_order"]
    )
    if "original_field_name" in exports.columns:
        if "export_field_name" not in exports.columns:
            exports["export_field_name"] = exports["original_field_name"]
        if "choice_value" not in exports.columns:
            exports["choice_value"] = ""
        exports["_merge_key"] = exports["original_field_name"].map(_field_key)
        exports["export_field_name"] = exports["export_field_name"].map(_safe_text)
        exports["choice_value"] = exports["choice_value"].map(_safe_text)
        exports["_export_order"] = range(len(exports))
        export_subset = exports[
            ["_merge_key", "export_field_name", "choice_value", "_export_order"]
        ].rename(columns={"choice_value": "export_choice_value"})
        export_subset = export_subset.loc[export_subset["_merge_key"] != ""]

    if not export_subset.empty:
        frame = frame.merge(export_subset, how="left", on="_merge_key", sort=False)
        frame = frame.sort_values(
            ["design_row_id", "_export_order"], kind="stable", na_position="last"
        ).reset_index(drop=True)
    else:
        frame["export_field_name"] = ""
        frame["export_choice_value"] = ""
        frame["_export_order"] = pd.NA

    frame["export_field_name"] = frame["export_field_name"].map(_safe_text)
    frame["export_choice_value"] = frame["export_choice_value"].map(_safe_text)
    fallback_export = frame["_existing_export_field_name"].where(
        frame["_existing_export_field_name"].map(_nonblank), frame["field_name"]
    )
    frame["export_field_name"] = frame["export_field_name"].where(
        frame["export_field_name"].map(_nonblank), fallback_export
    )

    frame["field_key"] = frame["field_name"].map(_field_key)
    frame["field_prefix"] = frame["field_name"].map(
        lambda value: _field_key(value).partition("_")[0]
    )
    frame["is_required"] = frame["required_field"].map(
        lambda value: _safe_text(value).strip().casefold() == "y"
    )
    frame["has_branching"] = frame["branching_logic"].map(_nonblank)
    frame["is_validated"] = frame[
        "text_validation_type_or_show_slider_number"
    ].map(_nonblank)
    frame["is_identifier"] = frame["identifier"].map(
        lambda value: _safe_text(value).strip().casefold() == "y"
    )
    frame["is_matrix"] = frame["matrix_group_name"].map(_nonblank)
    frame["missing_label"] = frame["field_label"].map(
        lambda value: not bool(_plain_text(value))
    )

    compiled_patterns = _compile_patterns(doe_doc_patterns)
    frame["is_doe_doc"] = [
        _matches_any(field_name, compiled_patterns)
        or _matches_any(_plain_text(field_label), compiled_patterns)
        for field_name, field_label in zip(
            frame["field_name"], frame["field_label"]
        )
    ]

    frame["action_tags"] = frame["field_annotation"].map(_extract_action_tags)
    frame["has_action_tags"] = frame["action_tags"].map(_nonblank)
    action_tag_sets = frame["action_tags"].map(
        lambda value: frozenset(part.strip() for part in value.split(";") if part.strip())
    )
    frame["tag_hidden"] = action_tag_sets.map(lambda tags: "@HIDDEN" in tags)
    frame["tag_readonly"] = action_tag_sets.map(lambda tags: "@READONLY" in tags)
    frame["tag_calctext"] = action_tag_sets.map(lambda tags: "@CALCTEXT" in tags)
    frame["field_type_detail"] = [
        (
            f"text:{_safe_text(validation).strip().casefold()}"
            if _safe_text(field_type).strip().casefold() == "text"
            and _nonblank(validation)
            else _safe_text(field_type).strip().casefold()
        )
        for field_type, validation in zip(
            frame["field_type"],
            frame["text_validation_type_or_show_slider_number"],
        )
    ]

    return frame.drop(
        columns=["_merge_key", "_existing_export_field_name", "_export_order"],
        errors="ignore",
    )


def _ensure_normalized(frame: Any) -> pd.DataFrame:
    candidate = _coerce_frame(frame)
    required = {"field_key", "export_field_name", *DERIVED_COLUMNS[4:]}
    if required.issubset(candidate.columns):
        return candidate.copy(deep=True)
    return normalize_metadata(candidate)


def _design_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Remove export expansion while retaining true cross-form collisions."""

    if frame.empty:
        return frame.copy(deep=True)
    if "design_row_id" in frame.columns:
        return frame.drop_duplicates(subset=["design_row_id"], keep="first").copy()
    keys = [column for column in ("field_key", "form_name") if column in frame.columns]
    return frame.drop_duplicates(subset=keys, keep="first").copy() if keys else frame.copy()


def _info_mapping(project_info: Any) -> dict[str, Any]:
    if isinstance(project_info, Mapping):
        return dict(project_info)
    if isinstance(project_info, Sequence) and not isinstance(project_info, (str, bytes)):
        for item in project_info:
            if isinstance(item, Mapping):
                return dict(item)
    return {}


def _collection_count(value: Any, identity_columns: Sequence[str]) -> int:
    frame = _coerce_frame(value)
    if frame.empty:
        return 0
    available = [column for column in identity_columns if column in frame.columns]
    if available:
        identities = frame[available].copy()
        for column in available:
            identities[column] = identities[column].map(_comparison_text)
        identities = identities.loc[(identities != "").any(axis=1)]
        return int(len(identities.drop_duplicates()))
    return int(len(frame))


def build_project_summary(
    metadata: Any,
    project_info: Any = None,
    instruments: Any = None,
    events: Any = None,
    repeating: Any = None,
) -> dict[str, Any]:
    """Return ground-truth project metrics from normalized metadata and exports."""

    normalized = _ensure_normalized(metadata)
    design = _design_rows(normalized)
    info = _info_mapping(project_info)

    is_longitudinal = _as_bool(info.get("is_longitudinal", False))
    event_count = _collection_count(events, ("unique_event_name", "event_name"))
    if not is_longitudinal and event_count:
        # The event export is direct evidence when project-info is unavailable.
        is_longitudinal = True

    repeating_count = _collection_count(
        repeating,
        ("form_name", "instrument_name", "event_name", "unique_event_name"),
    )
    has_repeating = _as_bool(
        info.get("has_repeating_instruments_or_events", repeating_count > 0)
    )

    explicit_instrument_count = _collection_count(
        instruments, ("instrument_name", "form_name")
    )
    metadata_instrument_count = int(
        design.get("form_name", pd.Series(dtype="object"))
        .map(_safe_text)
        .loc[lambda s: s.str.strip() != ""]
        .nunique()
    )
    instrument_count = explicit_instrument_count or metadata_instrument_count

    export_names = normalized.get("export_field_name", pd.Series(dtype="object")).map(
        _field_key
    )
    matrix_groups = design.get("matrix_group_name", pd.Series(dtype="object")).map(
        _comparison_text
    )
    matrix_groups = matrix_groups.loc[matrix_groups != ""]

    collision_count = 0
    if not design.empty and {"field_key", "form_name"}.issubset(design.columns):
        collisions = (
            design.loc[design["field_key"] != ""]
            .groupby("field_key", sort=False)["form_name"]
            .nunique()
        )
        collision_count = int((collisions > 1).sum())

    def flag_count(column: str) -> int:
        if design.empty or column not in design.columns:
            return 0
        return int(design[column].map(bool).sum())

    return {
        "project_title": _safe_text(info.get("project_title", "")),
        "project_id": _safe_text(info.get("project_id", info.get("project_pid", ""))),
        "instrument_count": int(instrument_count),
        "design_field_count": int(len(design)),
        "distinct_field_count": int(
            design.get("field_key", pd.Series(dtype="object"))
            .loc[lambda s: s != ""]
            .nunique()
        ),
        "export_field_count": int(export_names.loc[export_names != ""].nunique()),
        "event_count": int(event_count) if is_longitudinal else None,
        "is_longitudinal": bool(is_longitudinal),
        "required_field_count": flag_count("is_required"),
        "branching_field_count": flag_count("has_branching"),
        "validated_field_count": flag_count("is_validated"),
        "matrix_field_count": flag_count("is_matrix"),
        "matrix_group_count": int(matrix_groups.nunique()),
        "identifier_field_count": flag_count("is_identifier"),
        "doe_doc_field_count": flag_count("is_doe_doc"),
        "missing_label_count": flag_count("missing_label"),
        "action_tagged_field_count": flag_count("has_action_tags"),
        "has_repeating": bool(has_repeating),
        "repeating_definition_count": int(repeating_count),
        "field_key_collision_count": collision_count,
    }


def _project_frames(project_metadata: Mapping[str, Any]) -> dict[str, pd.DataFrame]:
    if not isinstance(project_metadata, Mapping):
        raise TypeError("project_metadata must be a mapping of project name to metadata")
    return {
        _safe_text(project): _design_rows(_ensure_normalized(frame))
        for project, frame in project_metadata.items()
        if _safe_text(project)
    }


def _project_key_sets(frames: Mapping[str, pd.DataFrame]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for project, frame in frames.items():
        if "field_key" not in frame.columns:
            result[project] = set()
            continue
        result[project] = {
            key for key in frame["field_key"].map(_field_key).tolist() if key
        }
    return result


def _resolve_reference(projects: Sequence[str], reference_project: str | None) -> str:
    if not projects:
        return ""
    requested = _safe_text(reference_project)
    return requested if requested in projects else projects[0]


def build_overlap_summary(
    project_metadata: Mapping[str, Any],
    reference_project: str | None = None,
) -> pd.DataFrame:
    """Build the numeric overlap source-of-truth table for connected projects."""

    frames = _project_frames(project_metadata)
    projects = list(frames)
    if not projects:
        return pd.DataFrame(columns=OVERLAP_COLUMNS)
    key_sets = _project_key_sets(frames)
    reference = _resolve_reference(projects, reference_project)
    rows: list[dict[str, Any]] = []

    def add(
        metric: str,
        scope: str,
        keys: set[str],
        project: str = "",
        other_project: str = "",
    ) -> None:
        rows.append(
            {
                "metric": metric,
                "scope": scope,
                "project": project,
                "other_project": other_project,
                "reference_project": reference,
                "count": len(keys),
                "field_keys": " | ".join(sorted(keys)),
            }
        )

    for project in projects:
        add("TOTAL_DISTINCT", project, key_sets[project], project=project)

    common_all = set.intersection(*(key_sets[project] for project in projects))
    add("COMMON_ALL", " & ".join(projects), common_all)

    for left, right in combinations(projects, 2):
        add(
            "PAIR_COMMON",
            f"{left} & {right}",
            key_sets[left] & key_sets[right],
            project=left,
            other_project=right,
        )

    for project in projects:
        other_keys = set().union(
            *(key_sets[other] for other in projects if other != project)
        )
        add(
            "UNIQUE_TO_PROJECT",
            project,
            key_sets[project] - other_keys,
            project=project,
        )

    for project in projects:
        if project == reference:
            continue
        add(
            "MISSING_VS_REFERENCE",
            f"{reference} -> {project}",
            key_sets[reference] - key_sets[project],
            project=project,
            other_project=reference,
        )

    return pd.DataFrame(rows, columns=OVERLAP_COLUMNS)


def _first_nonblank(frame: pd.DataFrame, column: str) -> str:
    if column not in frame.columns:
        return ""
    for value in frame[column]:
        text = _safe_text(value).strip()
        if text:
            return text
    return ""


def _aggregate_fields(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    aggregates: dict[str, dict[str, Any]] = {}
    if frame.empty or "field_key" not in frame.columns:
        return aggregates
    valid = frame.loc[frame["field_key"].map(_field_key) != ""].copy()
    valid["field_key"] = valid["field_key"].map(_field_key)
    for key, group in valid.groupby("field_key", sort=False, dropna=False):
        labels = {
            normalized
            for normalized in group.get("field_label", pd.Series(dtype="object")).map(
                _comparison_text
            )
            if normalized
        }
        field_types = {
            normalized
            for normalized in group.get("field_type", pd.Series(dtype="object")).map(
                _comparison_text
            )
            if normalized
        }
        instruments = {
            normalized
            for normalized in group.get("form_name", pd.Series(dtype="object")).map(
                _comparison_text
            )
            if normalized
        }
        validation_values = group.get(
            "is_validated", pd.Series(False, index=group.index)
        ).map(bool)
        identifier_values = group.get(
            "is_identifier", pd.Series(False, index=group.index)
        ).map(bool)
        doe_values = group.get("is_doe_doc", pd.Series(False, index=group.index)).map(
            bool
        )
        aggregates[key] = {
            "field_name": _first_nonblank(group, "field_name") or key,
            "label": _first_nonblank(group, "field_label"),
            "instrument": _first_nonblank(group, "form_name"),
            "field_type": _first_nonblank(group, "field_type"),
            "labels": labels,
            "field_types": field_types,
            "instruments": instruments,
            "validated": bool(validation_values.any()),
            "identifier": bool(identifier_values.any()),
            "is_doe_doc": bool(doe_values.any()),
        }
    return aggregates


def compare_projects(
    project_metadata: Mapping[str, Any],
    reference_project: str | None = None,
) -> pd.DataFrame:
    """Build one exact-key comparison row per distinct field across projects.

    Mismatch columns count *excess* exact normalized variants.  For example, one
    shared label has ``label_mismatch == 0`` and two different labels have
    ``label_mismatch == 1``.  Companion ``*_variant_count`` columns expose the
    raw distinct counts.  This definition keeps ``mismatch_total`` and ALIGNED
    internally consistent.
    """

    frames = _project_frames(project_metadata)
    projects = list(frames)
    reference = _resolve_reference(projects, reference_project)
    aggregates = {project: _aggregate_fields(frame) for project, frame in frames.items()}
    all_keys = sorted(set().union(*(set(values) for values in aggregates.values())))

    presence_columns = [f"in_{project}" for project in projects]
    base_columns = [
        "field_name",
        "field_key",
        "example_label",
        "example_instrument",
        "example_field_type",
        *presence_columns,
        "missing_in",
        "label_mismatch",
        "type_mismatch",
        "instrument_mismatch",
        "mismatch_total",
        "label_variant_count",
        "type_variant_count",
        "instrument_variant_count",
        "validation_gap",
        "identifier_flag_mismatch",
        "discrepancy_category",
        "reference_project",
    ]
    if not all_keys:
        return pd.DataFrame(columns=base_columns)

    representative_order = [reference, *[p for p in projects if p != reference]]
    rows: list[dict[str, Any]] = []
    for key in all_keys:
        present = [project for project in projects if key in aggregates[project]]
        missing = [project for project in projects if project not in present]

        representative: dict[str, Any] = {}
        for project in representative_order:
            if key in aggregates.get(project, {}):
                representative = aggregates[project][key]
                break

        labels = set().union(
            *(aggregates[project][key]["labels"] for project in present)
        )
        field_types = set().union(
            *(aggregates[project][key]["field_types"] for project in present)
        )
        instruments = set().union(
            *(aggregates[project][key]["instruments"] for project in present)
        )
        label_mismatch = max(len(labels) - 1, 0)
        type_mismatch = max(len(field_types) - 1, 0)
        instrument_mismatch = max(len(instruments) - 1, 0)
        validation_values = {
            aggregates[project][key]["validated"] for project in present
        }
        identifier_values = {
            aggregates[project][key]["identifier"] for project in present
        }
        validation_gap = len(present) >= 2 and len(validation_values) > 1
        identifier_mismatch = len(present) >= 2 and len(identifier_values) > 1

        present_everywhere = len(present) == len(projects)
        if not present_everywhere and reference in present:
            category = "MISSING_VS_REFERENCE"
        elif not present_everywhere:
            category = "PARTIAL_PRESENCE"
        elif type_mismatch:
            category = "TYPE_MISMATCH"
        elif label_mismatch:
            category = "LABEL_MISMATCH"
        elif instrument_mismatch:
            category = "INSTRUMENT_MISMATCH"
        elif validation_gap:
            category = "VALIDATION_GAP"
        else:
            category = "ALIGNED"

        row: dict[str, Any] = {
            "field_name": representative.get("field_name", key),
            "field_key": key,
            "example_label": representative.get("label", ""),
            "example_instrument": representative.get("instrument", ""),
            "example_field_type": representative.get("field_type", ""),
            "missing_in": ", ".join(missing),
            "label_mismatch": label_mismatch,
            "type_mismatch": type_mismatch,
            "instrument_mismatch": instrument_mismatch,
            "mismatch_total": label_mismatch
            + type_mismatch
            + instrument_mismatch,
            "label_variant_count": len(labels),
            "type_variant_count": len(field_types),
            "instrument_variant_count": len(instruments),
            "validation_gap": bool(validation_gap),
            "identifier_flag_mismatch": bool(identifier_mismatch),
            "discrepancy_category": category,
            "reference_project": reference,
        }
        row.update({f"in_{project}": project in present for project in projects})
        rows.append(row)

    return pd.DataFrame(rows, columns=base_columns)


def _display_for_key(
    key: str,
    aggregates: Mapping[str, Mapping[str, dict[str, Any]]],
    project_order: Sequence[str],
) -> tuple[str, str]:
    for project in project_order:
        item = aggregates.get(project, {}).get(key)
        if item:
            return item["field_name"], item["instrument"]
    return key, ""


def build_missing_profile(project_metadata: Mapping[str, Any]) -> pd.DataFrame:
    """Return field-attributed structural issues without inference or advice."""

    frames = _project_frames(project_metadata)
    projects = list(frames)
    if not projects:
        return pd.DataFrame(columns=MISSING_PROFILE_COLUMNS)
    aggregates = {project: _aggregate_fields(frame) for project, frame in frames.items()}
    all_keys = sorted(set().union(*(set(values) for values in aggregates.values())))
    rows: list[dict[str, Any]] = []

    def add(
        issue_type: str,
        project: str,
        key: str,
        field_name: str,
        instrument: str,
        compared_with: Sequence[str] = (),
        observed: str = "",
        details: str = "",
    ) -> None:
        rows.append(
            {
                "issue_type": issue_type,
                "project": project,
                "field_name": field_name,
                "field_key": key,
                "instrument": instrument,
                "compared_with": ", ".join(compared_with),
                "observed": observed,
                "details": details,
            }
        )

    # Blank-label issues are field/form attributed and deduplicated after export
    # expansion by _design_rows.
    for project in projects:
        frame = frames[project]
        if "missing_label" not in frame.columns:
            continue
        blank_rows = frame.loc[frame["missing_label"].map(bool)]
        blank_rows = blank_rows.drop_duplicates(
            subset=[c for c in ("field_key", "form_name") if c in blank_rows.columns]
        )
        for _, row in blank_rows.iterrows():
            key = _field_key(row.get("field_key", row.get("field_name", "")))
            add(
                "MISSING_LABEL",
                project,
                key,
                _safe_text(row.get("field_name", key)),
                _safe_text(row.get("form_name", "")),
                observed="blank",
                details="Field label is blank",
            )

    for key in all_keys:
        present = [project for project in projects if key in aggregates[project]]
        field_name, instrument = _display_for_key(key, aggregates, projects)

        validated = {
            project: aggregates[project][key]["validated"] for project in present
        }
        if len(present) >= 2 and len(set(validated.values())) > 1:
            validated_projects = [project for project, value in validated.items() if value]
            for project, value in validated.items():
                if not value:
                    add(
                        "VALIDATION_GAP",
                        project,
                        key,
                        aggregates[project][key]["field_name"],
                        aggregates[project][key]["instrument"],
                        compared_with=validated_projects,
                        observed="not validated",
                        details=f"Validated in {', '.join(validated_projects)}",
                    )

        doe_projects = [
            project
            for project in present
            if aggregates[project][key]["is_doe_doc"]
        ]
        if doe_projects:
            for project in projects:
                if project not in present:
                    add(
                        "DOE_DOC_MISSING",
                        project,
                        key,
                        field_name,
                        instrument,
                        compared_with=doe_projects,
                        observed="absent",
                        details=f"Present as DOE/DOC in {', '.join(doe_projects)}",
                    )

        identifiers = {
            project: aggregates[project][key]["identifier"] for project in present
        }
        if len(present) >= 2 and len(set(identifiers.values())) > 1:
            for project, value in identifiers.items():
                opposite_projects = [
                    other
                    for other, other_value in identifiers.items()
                    if other_value != value
                ]
                add(
                    "IDENTIFIER_FLAG_MISMATCH",
                    project,
                    key,
                    aggregates[project][key]["field_name"],
                    aggregates[project][key]["instrument"],
                    compared_with=opposite_projects,
                    observed="identifier" if value else "not identifier",
                    details="Identifier flag differs across connected projects",
                )

    issue_order = {
        "MISSING_LABEL": 0,
        "VALIDATION_GAP": 1,
        "DOE_DOC_MISSING": 2,
        "IDENTIFIER_FLAG_MISMATCH": 3,
    }
    rows.sort(
        key=lambda row: (
            issue_order.get(row["issue_type"], 99),
            row["field_key"],
            projects.index(row["project"]),
            row["instrument"],
        )
    )
    return pd.DataFrame(rows, columns=MISSING_PROFILE_COLUMNS)


__all__ = [
    "DEFAULT_DOE_DOC_PATTERNS",
    "DERIVED_COLUMNS",
    "MISSING_PROFILE_COLUMNS",
    "OVERLAP_COLUMNS",
    "TEXT_COLUMNS",
    "build_missing_profile",
    "build_overlap_summary",
    "build_project_summary",
    "compare_projects",
    "normalize_metadata",
]
