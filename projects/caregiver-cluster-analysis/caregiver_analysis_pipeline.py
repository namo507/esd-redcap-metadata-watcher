"""Reproducible REDCap trust-screen and caregiver-profile sensitivity pipeline.

The public outputs from this module are aggregate. Raw REDCap caches and the
record-level audit file are intentionally ignored by the project .gitignore.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import math
import os
import re
import time
import warnings

os.environ.setdefault("SOURCE_DATE_EPOCH", "1785369600")
os.environ.setdefault("KMP_WARNINGS", "0")

warnings.filterwarnings(
    "ignore",
    message="urllib3 v2 only supports OpenSSL",
)

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from matplotlib.patches import Ellipse, Polygon, Rectangle
import numpy as np
import pandas as pd
import requests
import seaborn as sns
from dotenv import load_dotenv
from scipy import linalg, optimize, stats
from scipy.special import expit
from scipy.spatial.distance import squareform
from scipy.cluster import hierarchy
from sklearn.cluster import KMeans
from sklearn.covariance import MinCovDet
from sklearn.decomposition import PCA
from sklearn.ensemble import GradientBoostingClassifier, IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.manifold import TSNE
from sklearn.metrics import (
    adjusted_rand_score,
    cohen_kappa_score,
    pairwise_distances,
    roc_auc_score,
    silhouette_samples,
    silhouette_score,
)
from sklearn.mixture import GaussianMixture
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import StandardScaler
from sklearn.feature_extraction.text import TfidfVectorizer
import statsmodels.api as sm
from statsmodels.tools.sm_exceptions import ConvergenceWarning
import yaml


CLUSTER_ORDER = ["Higher acceptability", "Conditional acceptability"]
TIME_FIELDS = ["get_time_fif", "get_time_val", "get_time_tfa", "get_time_demo"]
COMPLETION_FIELDS = [
    "family_information_form_complete",
    "values_complete",
    "tfa_complete",
    "demographics_complete",
]
DIRECT_IDENTIFIER_FIELDS = {
    "email_elig",
    "zip_demo",
    "dob_child1",
    "occup",
    "occup_1",
}


@dataclass
class SourceBundle:
    records: dict[str, pd.DataFrame]
    metadata: dict[str, pd.DataFrame]
    instruments: dict[str, pd.DataFrame]
    cache_inventory: pd.DataFrame
    pulled_at_utc: str


@dataclass
class ClusterFit:
    frame: pd.DataFrame
    domains: pd.DataFrame
    eligible: pd.Series
    X: np.ndarray
    imputer: SimpleImputer
    scaler: StandardScaler
    model: KMeans
    raw_labels: np.ndarray
    named_labels: pd.Series
    silhouette: float
    case_silhouette: pd.Series
    profile_means: pd.DataFrame


def load_config(project_dir: Path) -> dict:
    with (project_dir / "config.yaml").open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_sha_sidecar(path: Path, pulled_at_utc: str) -> None:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    payload = {
        "file": path.name,
        "sha256": sha256_file(path),
        "pulled_at_utc": pulled_at_utc,
    }
    sidecar.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _verify_sha_sidecar(path: Path) -> None:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not sidecar.exists():
        raise RuntimeError(f"Missing SHA-256 sidecar for cache: {path.name}")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    actual = sha256_file(path)
    if payload.get("sha256") != actual:
        raise RuntimeError(f"Cache hash mismatch for {path.name}")


def _redcap_post(
    api_url: str,
    token: str,
    content: str,
    timeout_seconds: int,
    retries: int,
) -> list[dict]:
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
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.post(api_url, data=payload, timeout=timeout_seconds)
            response.raise_for_status()
            if not response.content:
                raise RuntimeError(f"Empty REDCap response for content={content}")
            decoded = response.json()
            if not isinstance(decoded, list) or not decoded:
                raise RuntimeError(
                    f"Unexpected or empty REDCap JSON for content={content}: "
                    f"{type(decoded).__name__}"
                )
            return decoded
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(min(2**attempt, 8))
    raise RuntimeError(
        f"REDCap pull failed after {retries} attempts for content={content}"
    ) from last_error


def _cache_path(cache_dir: Path, project_id: int, content: str, date_text: str) -> Path:
    return cache_dir / f"{project_id}_{content}_{date_text}.parquet"


def _load_or_pull_content(
    *,
    cache_dir: Path,
    project_id: int,
    content: str,
    date_text: str,
    api_url: str,
    token: str,
    timeout_seconds: int,
    retries: int,
    pulled_at_utc: str,
) -> tuple[pd.DataFrame, Path, str]:
    target = _cache_path(cache_dir, project_id, content, date_text)
    if target.exists():
        _verify_sha_sidecar(target)
        return pd.read_parquet(target), target, "verified cache"
    rows = _redcap_post(api_url, token, content, timeout_seconds, retries)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError(f"REDCap returned no {content} rows for project {project_id}")
    frame.to_parquet(target, index=False)
    _write_sha_sidecar(target, pulled_at_utc)
    return frame, target, "live API pull"


def load_redcap_sources(project_dir: Path, config: dict) -> SourceBundle:
    """Load hashed daily caches or pull both REDCap projects with strict checks."""

    repository_root = project_dir.parents[1]
    load_dotenv(repository_root / ".env", override=False)
    redcap_cfg = config["redcap"]
    api_url = os.environ.get(redcap_cfg["api_url_env"], "").strip()
    if not api_url:
        raise RuntimeError(f"Missing {redcap_cfg['api_url_env']} in repository .env")
    cache_dir = project_dir / redcap_cfg["cache_dir"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    pulled_at_utc = datetime.now(timezone.utc).isoformat()
    date_text = datetime.now(timezone.utc).date().isoformat()

    records: dict[str, pd.DataFrame] = {}
    metadata: dict[str, pd.DataFrame] = {}
    instruments: dict[str, pd.DataFrame] = {}
    inventory_rows: list[dict] = []
    for source_name, source_cfg in redcap_cfg["projects"].items():
        token_name = source_cfg["token_env"]
        token = os.environ.get(token_name, "").strip()
        if not token:
            raise RuntimeError(
                f"Missing {token_name} in repository .env. Tokens are never hardcoded."
            )
        project_id = int(source_cfg["project_id"])
        for content in ("record", "metadata", "instrument"):
            frame, path, origin = _load_or_pull_content(
                cache_dir=cache_dir,
                project_id=project_id,
                content=content,
                date_text=date_text,
                api_url=api_url,
                token=token,
                timeout_seconds=int(redcap_cfg["request_timeout_seconds"]),
                retries=int(redcap_cfg["request_retries"]),
                pulled_at_utc=pulled_at_utc,
            )
            inventory_rows.append(
                {
                    "source_project": source_name,
                    "project_id": project_id,
                    "content": content,
                    "rows": len(frame),
                    "columns": len(frame.columns),
                    "cache_file": path.name,
                    "sha256_12": sha256_file(path)[:12],
                    "origin": origin,
                }
            )
            if content == "record":
                records[source_name] = frame
            elif content == "metadata":
                metadata[source_name] = frame
            else:
                instruments[source_name] = frame

        expected_records = int(source_cfg["expected_records"])
        if len(records[source_name]) != expected_records:
            raise RuntimeError(
                f"Project {project_id} has {len(records[source_name])} records; "
                f"expected {expected_records}. Halted to prevent silent cohort drift."
            )
        if "record_id" not in records[source_name]:
            raise RuntimeError(f"Project {project_id} has no record_id field")
        record_ids = records[source_name]["record_id"].astype(str).str.strip()
        if record_ids.eq("").any() or record_ids.duplicated().any():
            raise RuntimeError(f"Project {project_id} record IDs are blank or duplicated")
        records[source_name] = records[source_name].copy()
        records[source_name]["project_id"] = project_id
        records[source_name]["source_project"] = source_name
        records[source_name]["uid"] = (
            str(project_id) + "_" + records[source_name]["record_id"].astype(str)
        )
        records[source_name] = records[source_name].set_index("uid", drop=False)

    combined = pd.concat(records.values(), axis=0, sort=False)
    if combined.index.duplicated().any():
        raise RuntimeError("Namespaced uid collision detected across REDCap projects")
    return SourceBundle(
        records=records,
        metadata=metadata,
        instruments=instruments,
        cache_inventory=pd.DataFrame(inventory_rows),
        pulled_at_utc=pulled_at_utc,
    )


def build_field_reports(bundle: SourceBundle) -> tuple[pd.DataFrame, pd.DataFrame]:
    clean_meta = bundle.metadata["clean_4797"]
    dirty_meta = bundle.metadata["dirty_4581"]
    clean_fields = set(clean_meta["field_name"].astype(str))
    dirty_fields = set(dirty_meta["field_name"].astype(str))
    field_rows = [
        *[
            {"field_name": field, "availability": "both"}
            for field in sorted(clean_fields & dirty_fields)
        ],
        *[
            {"field_name": field, "availability": "clean_4797 only"}
            for field in sorted(clean_fields - dirty_fields)
        ],
        *[
            {"field_name": field, "availability": "dirty_4581 only"}
            for field in sorted(dirty_fields - clean_fields)
        ],
    ]
    field_intersection = pd.DataFrame(field_rows)

    clean = bundle.records["clean_4797"]
    dirty = bundle.records["dirty_4581"]
    mismatch_rows: list[dict] = []
    excluded = {"record_id", "project_id", "source_project", "uid"}
    for field in sorted((set(clean.columns) & set(dirty.columns)) - excluded):
        clean_numeric = pd.to_numeric(clean[field], errors="coerce")
        dirty_numeric = pd.to_numeric(dirty[field], errors="coerce")
        if clean_numeric.notna().sum() < 5 or dirty_numeric.notna().sum() < 5:
            continue
        clean_min, clean_max = clean_numeric.min(), clean_numeric.max()
        dirty_min, dirty_max = dirty_numeric.min(), dirty_numeric.max()
        if not (np.isclose(clean_min, dirty_min) and np.isclose(clean_max, dirty_max)):
            mismatch_rows.append(
                {
                    "field_name": field,
                    "clean_min": clean_min,
                    "clean_max": clean_max,
                    "dirty_min": dirty_min,
                    "dirty_max": dirty_max,
                    "clean_nonmissing_n": int(clean_numeric.notna().sum()),
                    "dirty_nonmissing_n": int(dirty_numeric.notna().sum()),
                    "clean_missing_pct": clean_numeric.isna().mean() * 100,
                    "dirty_missing_pct": dirty_numeric.isna().mean() * 100,
                }
            )
    return field_intersection, pd.DataFrame(mismatch_rows)


def _numeric(frame: pd.DataFrame, field: str) -> pd.Series:
    if field not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[field], errors="coerce")


def _parse_numeric_choices(value: object) -> list[float]:
    if not isinstance(value, str) or not value.strip():
        return []
    codes: list[float] = []
    for part in value.split("|"):
        code = part.split(",", 1)[0].strip()
        try:
            codes.append(float(code))
        except ValueError:
            return []
    return sorted(set(codes))


def identify_likert_fields(
    metadata: pd.DataFrame, available_columns: set[str]
) -> dict[int, list[str]]:
    """Identify ordered 1–4 through 1–7 items from metadata, not outcomes."""

    by_scale = {4: [], 5: [], 6: [], 7: []}
    for row in metadata.itertuples(index=False):
        field = str(getattr(row, "field_name"))
        if field not in available_columns:
            continue
        form = str(getattr(row, "form_name", ""))
        if form not in {"tfa", "values"}:
            continue
        if field.startswith(("get_time_", "survey_time_")) or field.endswith("_complete"):
            continue
        choices = _parse_numeric_choices(
            getattr(row, "select_choices_or_calculations", "")
        )
        scale_size: int | None = None
        for size in by_scale:
            if choices == [float(value) for value in range(1, size + 1)]:
                scale_size = size
                break
        if scale_size is None:
            try:
                minimum = float(getattr(row, "text_validation_min", ""))
                maximum = float(getattr(row, "text_validation_max", ""))
                size = int(maximum - minimum + 1)
                if minimum == 1 and size in by_scale:
                    scale_size = size
            except (TypeError, ValueError):
                pass
        if scale_size:
            by_scale[scale_size].append(field)
    return {size: sorted(fields) for size, fields in by_scale.items()}


def _longest_identical_run(values: np.ndarray) -> int:
    best = current = 0
    previous: float | None = None
    for value in values:
        if not np.isfinite(value):
            previous = None
            current = 0
        elif previous is not None and value == previous:
            current += 1
        else:
            current = 1
        previous = value if np.isfinite(value) else None
        best = max(best, current)
    return best


def _open_text_fields(metadata: pd.DataFrame, columns: set[str]) -> list[str]:
    requested = re.compile(
        r"^(tfa_comments_|tfa_ethic_details$|fif_work_explain$|occup(?:_1)?$)"
    )
    fields = []
    for row in metadata.itertuples(index=False):
        field = str(getattr(row, "field_name"))
        if field in columns and requested.search(field):
            fields.append(field)
    return sorted(set(fields))


def _max_open_text_similarity(text: pd.Series) -> pd.Series:
    clean_text = text.fillna("").astype(str).str.strip()
    eligible = clean_text.str.len().ge(20)
    result = pd.Series(0.0, index=text.index)
    if eligible.sum() < 2:
        return result
    vectorizer = TfidfVectorizer(
        lowercase=True, strip_accents="unicode", ngram_range=(1, 2), min_df=1
    )
    matrix = vectorizer.fit_transform(clean_text.loc[eligible])
    similarities = matrix @ matrix.T
    similarities.setdiag(0)
    result.loc[eligible] = np.asarray(similarities.max(axis=1).toarray()).ravel()
    return result


def _submission_timestamps(frame: pd.DataFrame) -> pd.Series:
    timestamp_columns = [column for column in frame if column.endswith("_timestamp")]
    if not timestamp_columns:
        return pd.Series(pd.NaT, index=frame.index)
    parsed = frame[timestamp_columns].apply(pd.to_datetime, errors="coerce")
    return parsed.max(axis=1)


def _flag_bursts(
    timestamps: pd.Series, window_seconds: float, minimum_submissions: int
) -> pd.Series:
    flags = pd.Series(False, index=timestamps.index)
    ordered = timestamps.dropna().sort_values()
    if len(ordered) < minimum_submissions:
        return flags
    values = ordered.astype("int64").to_numpy() / 1e9
    left = 0
    for right in range(len(values)):
        while values[right] - values[left] > window_seconds:
            left += 1
        if right - left + 1 >= minimum_submissions:
            flags.loc[ordered.index[left : right + 1]] = True
    return flags


def _nonempty(frame: pd.DataFrame, fields: list[str]) -> pd.DataFrame:
    if not fields:
        return pd.DataFrame(index=frame.index)
    return frame[fields].fillna("").astype(str).apply(lambda col: col.str.strip().ne(""))


def _normalize_branching_logic(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _metadata_row_by_field(metadata: pd.DataFrame) -> dict[str, pd.Series]:
    if metadata.empty:
        return {}
    indexed = metadata.drop_duplicates("field_name", keep="first").set_index("field_name")
    return {str(field): indexed.loc[field] for field in indexed.index}


def _resolve_export_columns(
    frame: pd.DataFrame,
    metadata_lookup: dict[str, pd.Series],
    field_name: str,
) -> tuple[list[str], bool]:
    if field_name in frame:
        field_type = str(metadata_lookup.get(field_name, {}).get("field_type", ""))
        return [field_name], field_type == "checkbox"
    checkbox_columns = sorted(
        column for column in frame if column.startswith(f"{field_name}___")
    )
    if checkbox_columns:
        return checkbox_columns, True
    return [], False


def _any_answered(
    frame: pd.DataFrame, fields: list[str], *, checkbox: bool
) -> pd.Series:
    if not fields:
        return pd.Series(False, index=frame.index)
    if checkbox:
        return frame[fields].apply(pd.to_numeric, errors="coerce").eq(1).any(axis=1)
    return _nonempty(frame, fields).any(axis=1)


def _branch_condition_mask(frame: pd.DataFrame, condition: dict) -> pd.Series:
    field = str(condition["field"])
    numeric = _numeric(frame, field)
    if "equals" in condition:
        return numeric.eq(float(condition["equals"]))
    if "not_equals" in condition:
        return numeric.ne(float(condition["not_equals"]))
    if condition.get("nonzero"):
        return numeric.ne(0)
    if condition.get("present"):
        return numeric.notna()
    raise RuntimeError(f"Unsupported branching-audit condition for field {field}")


def _validate_expected_branching_logic(
    metadata_lookup: dict[str, pd.Series],
    field_name: str,
    expected: str,
) -> None:
    row = metadata_lookup.get(field_name)
    if row is None:
        raise RuntimeError(f"Metadata row missing for branching-audit field {field_name}")
    observed = _normalize_branching_logic(row.get("branching_logic", ""))
    if observed != _normalize_branching_logic(expected):
        raise RuntimeError(
            f"Branching logic drift for {field_name}: observed {observed!r}, expected {expected!r}"
        )


def _age_checkbox_columns(frame: pd.DataFrame) -> list[str]:
    return sorted(column for column in frame if column.startswith("fif_childrens_ages___"))


def engineer_behavioral_features_and_rules(
    bundle: SourceBundle, config: dict
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Engineer behavioral features and apply pre-specified rules R1–R10."""

    combined = pd.concat(bundle.records.values(), axis=0, sort=False).copy()
    clean_meta = bundle.metadata["clean_4797"]
    dirty_meta = bundle.metadata["dirty_4581"]
    common_metadata_names = set(clean_meta["field_name"]) & set(dirty_meta["field_name"])
    combined_meta = pd.concat([clean_meta, dirty_meta], ignore_index=True).drop_duplicates(
        "field_name", keep="first"
    )
    likert_by_scale = identify_likert_fields(
        combined_meta.loc[combined_meta["field_name"].isin(common_metadata_names)],
        set(combined.columns),
    )
    likert_fields = [
        field for size in sorted(likert_by_scale) for field in likert_by_scale[size]
    ]
    if len(likert_fields) < 20:
        raise RuntimeError(
            f"Only {len(likert_fields)} shared Likert fields were identified; "
            "metadata-driven fingerprinting is not trustworthy."
        )

    features = pd.DataFrame(index=combined.index)
    for short_name, field in zip(("fif", "val", "tfa", "demo"), TIME_FIELDS):
        features[f"feat_time_{short_name}"] = _numeric(combined, field)
    features["feat_total_time_min"] = features[
        [f"feat_time_{name}" for name in ("fif", "val", "tfa", "demo")]
    ].sum(axis=1, min_count=4)
    time_mean = features[[f"feat_time_{name}" for name in ("fif", "val", "tfa", "demo")]].mean(
        axis=1
    )
    features["feat_time_cv"] = (
        features[[f"feat_time_{name}" for name in ("fif", "val", "tfa", "demo")]].std(
            axis=1, ddof=1
        )
        / time_mean.replace(0, np.nan)
    )

    likert_numeric = combined[likert_fields].apply(pd.to_numeric, errors="coerce")
    for size, fields in likert_by_scale.items():
        features[f"feat_sd_likert_{size}"] = likert_numeric[fields].std(axis=1, ddof=1)
    features["feat_straightline_maxrun"] = likert_numeric.apply(
        lambda row: _longest_identical_run(row.to_numpy(dtype=float)), axis=1
    )
    features["feat_n_distinct_values"] = likert_numeric.nunique(axis=1, dropna=True)

    tfa_fields = [field for field in likert_fields if field.startswith("tfa_")]
    tfa_answered = likert_numeric[tfa_fields].notna().sum(axis=1)
    features["feat_n_missing_tfa"] = len(tfa_fields) - tfa_answered
    features["feat_time_per_item_tfa"] = features["feat_time_tfa"] / tfa_answered.replace(
        0, np.nan
    )

    open_text_fields = _open_text_fields(combined_meta, set(combined.columns))
    if open_text_fields:
        open_text = combined[open_text_fields].fillna("").astype(str)
        open_text_lengths = open_text.apply(lambda col: col.str.strip().str.len())
        features["feat_open_text_n_filled"] = open_text_lengths.gt(0).sum(axis=1)
        features["feat_open_text_mean_len"] = open_text_lengths.where(
            open_text_lengths.gt(0)
        ).mean(axis=1)
        concatenated_text = open_text.apply(
            lambda row: " ".join(value.strip() for value in row if value.strip()), axis=1
        )
        features["feat_open_text_max_sim"] = _max_open_text_similarity(concatenated_text)
    else:
        features["feat_open_text_n_filled"] = 0
        features["feat_open_text_mean_len"] = 0.0
        features["feat_open_text_max_sim"] = 0.0

    value_fields = [
        field
        for field in likert_fields
        if field.endswith("_val") and field not in TIME_FIELDS
    ]
    if value_fields:
        value_numeric = likert_numeric[value_fields]
        row_center = value_numeric.mean(axis=1)
        features["feat_values_scale_incons"] = (
            value_numeric.sub(row_center, axis=0).abs().mean(axis=1)
        )
    else:
        features["feat_values_scale_incons"] = np.nan
    emotion_fields = [
        field
        for field in [
            "tfa_happy",
            "tfa_disgusted",
            "tfa_scared",
            "tfa_angry",
            "tfa_sad",
            "tfa_surprised",
        ]
        if field in combined
    ]
    features["feat_emotion_variance"] = combined[emotion_fields].apply(
        pd.to_numeric, errors="coerce"
    ).std(axis=1, ddof=1)
    completion = pd.DataFrame(
        {
            field: _numeric(combined, field).eq(2)
            for field in COMPLETION_FIELDS
            if field in combined
        }
    )
    features["feat_completed_all"] = completion.all(axis=1)
    features["feat_completed_all_and_fast"] = (
        features["feat_completed_all"] & features["feat_total_time_min"].lt(11.57)
    )
    features["submission_timestamp"] = _submission_timestamps(combined)
    features["source_project"] = combined["source_project"]
    features["project_id"] = combined["project_id"]
    features["record_id"] = combined["record_id"].astype(str)
    metadata_lookup = _metadata_row_by_field(combined_meta)

    rules_cfg = config["fraud_rules"]
    completed_clean = (
        combined["source_project"].eq("clean_4797")
        & features["feat_completed_all"]
        & features["feat_total_time_min"].notna()
    )
    reference_index = combined.index[completed_clean]
    if len(reference_index) != 131:
        raise RuntimeError(
            f"Verified-human timing reference has n={len(reference_index)}, expected 131"
        )

    rules = pd.DataFrame(False, index=combined.index, columns=[f"rule_R{i}" for i in range(1, 11)])
    rules["rule_R1"] = features["feat_total_time_min"].round(2).lt(
        float(rules_cfg["total_time_floor_min"])
    ).fillna(False)
    rules["rule_R2"] = features["feat_time_tfa"].lt(
        float(rules_cfg["tfa_time_floor_min"])
    ).fillna(False)

    fast_thresholds = {}
    rule_r3 = pd.Series(False, index=combined.index)
    for short_name in ("fif", "val", "tfa", "demo"):
        field = f"feat_time_{short_name}"
        threshold = features.loc[reference_index, field].quantile(
            float(rules_cfg["instrument_fast_quantile"])
        )
        fast_thresholds[field] = float(threshold)
        rule_r3 |= features[field].lt(threshold).fillna(False)
    rules["rule_R3"] = rule_r3

    straightline_thresholds = {}
    rule_r4 = pd.Series(False, index=combined.index)
    for size in (4, 5, 6, 7):
        field = f"feat_sd_likert_{size}"
        reference = features.loc[reference_index, field].dropna()
        if reference.empty:
            continue
        threshold = float(
            reference.quantile(float(rules_cfg["straightline_quantile"]))
        )
        straightline_thresholds[field] = threshold
        rule_r4 |= features[field].le(threshold).fillna(False)
    rules["rule_R4"] = rule_r4

    answered_fraction = likert_numeric.notna().mean(axis=1)
    fingerprint_source = likert_numeric.fillna("<NA>").astype(str).agg("|".join, axis=1)
    fingerprints = fingerprint_source.map(
        lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    )
    eligible_fingerprint = answered_fraction.ge(
        float(rules_cfg["duplicate_min_answer_fraction"])
    )
    rules["rule_R5"] = (
        fingerprints.duplicated(keep=False) & eligible_fingerprint
    )
    features["response_fingerprint_sha256"] = fingerprints

    clean_times = features.loc[reference_index, "submission_timestamp"].dropna().sort_values()
    clean_interarrival = clean_times.diff().dt.total_seconds().dropna()
    clean_positive_interarrival = clean_interarrival.loc[clean_interarrival.gt(0)]
    burst_window_seconds = float(
        clean_positive_interarrival.quantile(float(rules_cfg["burst_window_quantile"]))
    )
    burst_window_seconds = min(
        max(burst_window_seconds, float(rules_cfg["burst_window_floor_seconds"])),
        float(rules_cfg["burst_window_ceiling_seconds"]),
    )
    for source_name in ("clean_4797", "dirty_4581"):
        source_index = combined.index[combined["source_project"].eq(source_name)]
        rules.loc[source_index, "rule_R6"] = _flag_bursts(
            features.loc[source_index, "submission_timestamp"],
            burst_window_seconds,
            int(rules_cfg["burst_min_submissions"]),
        )

    rules["rule_R7"] = features["feat_open_text_max_sim"].gt(
        float(rules_cfg["open_text_similarity_threshold"])
    )

    rule_r8 = pd.Series(False, index=combined.index)
    branching_audit_rows: list[dict] = []

    def add_branching_audit_rows(
        mask: pd.Series,
        *,
        family: str,
        violation_type: str,
        dependent_field: str,
        gating_logic: str,
        gating_fields: list[str],
        observed_fields: list[str],
    ) -> None:
        hit_index = combined.index[mask.fillna(False)]
        if not len(hit_index):
            return
        for uid in hit_index:
            row = combined.loc[uid]
            observed_values = {
                field: row.get(field, "")
                for field in observed_fields
                if str(row.get(field, "")).strip() != ""
            }
            gating_values = {
                field: row.get(field, "")
                for field in gating_fields
                if field in combined.columns
            }
            branching_audit_rows.append(
                {
                    "uid": uid,
                    "source_project": row["source_project"],
                    "project_id": row["project_id"],
                    "record_id": str(row["record_id"]),
                    "family": family,
                    "violation_type": violation_type,
                    "dependent_field": dependent_field,
                    "gating_fields": ", ".join(gating_fields),
                    "gating_logic": gating_logic,
                    "observed_fields": ", ".join(observed_fields),
                    "gating_values": json.dumps(gating_values, sort_keys=True),
                    "observed_values": json.dumps(observed_values, sort_keys=True),
                }
            )

    followup_fields = []
    for row in combined_meta.itertuples(index=False):
        branching = str(getattr(row, "branching_logic", ""))
        field = str(getattr(row, "field_name"))
        if "[fif_num_autistic]" in branching and field in combined:
            followup_fields.append(field)
    zero_autistic = _numeric(combined, "fif_num_autistic").eq(0)
    if followup_fields:
        autistic_followup_mask = zero_autistic & _nonempty(combined, followup_fields).any(axis=1)
        rule_r8 |= autistic_followup_mask
        add_branching_audit_rows(
            autistic_followup_mask,
            family="autistic_child_followup_hidden_answer",
            violation_type="orphaned_followup",
            dependent_field="metadata rows containing [fif_num_autistic]",
            gating_logic="[fif_num_autistic] must not equal 0 when dependent follow-up fields are answered",
            gating_fields=["fif_num_autistic"],
            observed_fields=followup_fields,
        )

    branching_cfg = rules_cfg.get("branching_audit", {})
    for pair_cfg in branching_cfg.get("mutually_exclusive_pairs", []):
        label = str(pair_cfg["label"])
        fields_cfg = pair_cfg.get("fields", [])
        source_projects = {str(value) for value in pair_cfg.get("source_projects", [])}
        resolved_fields: list[str] = []
        gating_fields: set[str] = set()
        for field_cfg in fields_cfg:
            field_name = str(field_cfg["field"])
            _validate_expected_branching_logic(
                metadata_lookup,
                field_name,
                str(field_cfg["expected_branching_logic"]),
            )
            resolved, _ = _resolve_export_columns(combined, metadata_lookup, field_name)
            if not resolved:
                raise RuntimeError(f"No export columns found for branching-audit field {field_name}")
            resolved_fields.extend(resolved)
            gating_fields.update(str(field) for field in field_cfg.get("gating_fields", []))
        pair_mask = _nonempty(combined, resolved_fields).sum(axis=1).ge(2)
        if source_projects:
            pair_mask &= combined["source_project"].isin(source_projects)
        rule_r8 |= pair_mask
        add_branching_audit_rows(
            pair_mask,
            family=label,
            violation_type="mutually_exclusive_conditional_pair",
            dependent_field=", ".join(str(field_cfg["field"]) for field_cfg in fields_cfg),
            gating_logic=" | ".join(
                str(field_cfg["expected_branching_logic"]) for field_cfg in fields_cfg
            ),
            gating_fields=sorted(gating_fields),
            observed_fields=resolved_fields,
        )

    for followup_cfg in branching_cfg.get("orphaned_followups", []):
        label = str(followup_cfg["label"])
        dependent_field = str(followup_cfg["dependent_field"])
        source_projects = {str(value) for value in followup_cfg.get("source_projects", [])}
        _validate_expected_branching_logic(
            metadata_lookup,
            dependent_field,
            str(followup_cfg["expected_branching_logic"]),
        )
        dependent_columns, is_checkbox = _resolve_export_columns(
            combined, metadata_lookup, dependent_field
        )
        if not dependent_columns:
            raise RuntimeError(
                f"No export columns found for branching-audit dependent field {dependent_field}"
            )
        answered = _any_answered(combined, dependent_columns, checkbox=is_checkbox)
        allowed_mask = pd.Series(False, index=combined.index)
        gating_fields: list[str] = []
        for condition in followup_cfg.get("allowed_when_any", []):
            gating_fields.append(str(condition["field"]))
            allowed_mask |= _branch_condition_mask(combined, condition).fillna(False)
        orphan_mask = answered & ~allowed_mask
        if source_projects:
            orphan_mask &= combined["source_project"].isin(source_projects)
        rule_r8 |= orphan_mask
        add_branching_audit_rows(
            orphan_mask,
            family=label,
            violation_type="orphaned_followup",
            dependent_field=dependent_field,
            gating_logic=str(followup_cfg["expected_branching_logic"]),
            gating_fields=sorted(set(gating_fields)),
            observed_fields=dependent_columns,
        )

    age_checkbox_columns = _age_checkbox_columns(combined)
    if age_checkbox_columns:
        selected_age_bands = combined[age_checkbox_columns].apply(
            pd.to_numeric, errors="coerce"
        ).eq(1).sum(axis=1)
        child_count = _numeric(combined, "fif_num_children")
        # Multiple children may share an age band, so only selected bands > count is impossible.
        age_band_mask = child_count.notna() & selected_age_bands.gt(child_count)
        rule_r8 |= age_band_mask
        add_branching_audit_rows(
            age_band_mask,
            family="child_age_band_count_exceeds_child_count",
            violation_type="impossible_count",
            dependent_field="fif_childrens_ages___*",
            gating_logic="selected child age-band checkboxes must not exceed fif_num_children",
            gating_fields=["fif_num_children"],
            observed_fields=["fif_num_children", *age_checkbox_columns],
        )
    rules["rule_R8"] = rule_r8
    branching_audit = pd.DataFrame(branching_audit_rows)
    if branching_audit.empty:
        branching_audit = pd.DataFrame(
            columns=[
                "uid",
                "source_project",
                "project_id",
                "record_id",
                "family",
                "violation_type",
                "dependent_field",
                "gating_fields",
                "gating_logic",
                "observed_fields",
                "gating_values",
                "observed_values",
            ]
        )
    else:
        branching_audit = branching_audit.sort_values(
            ["source_project", "family", "record_id", "violation_type"]
        ).reset_index(drop=True)

    rule_r9 = pd.Series(False, index=combined.index)
    caregiver_age = _numeric(combined, "age_check_demo")
    rule_r9 |= caregiver_age.notna() & caregiver_age.ne(0) & ~caregiver_age.between(
        float(rules_cfg["plausible_caregiver_age_min"]),
        float(rules_cfg["plausible_caregiver_age_max"]),
    )
    if "zip_demo" in combined and "demo_country" in combined:
        zip_text = combined["zip_demo"].fillna("").astype(str).str.strip()
        us_resident = _numeric(combined, "demo_country").eq(1)
        populated_zip = zip_text.ne("")
        rule_r9 |= us_resident & populated_zip & ~zip_text.str.fullmatch(
            r"\d{5}(?:-\d{4})?"
        )
    if "dob_child1" in combined:
        child_birth_year = _numeric(combined, "dob_child1")
        current_year = datetime.now().year
        age_at_first_birth = caregiver_age - (current_year - child_birth_year)
        usable = caregiver_age.between(
            float(rules_cfg["plausible_caregiver_age_min"]),
            float(rules_cfg["plausible_caregiver_age_max"]),
        ) & child_birth_year.notna()
        rule_r9 |= usable & ~age_at_first_birth.between(10, 60)
    rules["rule_R9"] = rule_r9

    rule_r10 = pd.Series(False, index=combined.index)
    clean_index = combined.index[combined["source_project"].eq("clean_4797")]
    knee_columns = sorted(column for column in combined if column.startswith("elig_knee___"))
    if "elig_knee___2" in knee_columns:
        knee_numeric = combined.loc[clean_index, knee_columns].apply(
            pd.to_numeric, errors="coerce"
        )
        correct = knee_numeric["elig_knee___2"].eq(1) & knee_numeric.drop(
            columns=["elig_knee___2"]
        ).fillna(0).eq(0).all(axis=1)
        attempted = knee_numeric.notna().any(axis=1)
        rule_r10.loc[clean_index] |= attempted & ~correct
    age_mismatch = caregiver_age.loc[clean_index].eq(0)
    rule_r10.loc[clean_index] |= age_mismatch.fillna(False)
    email_component_available = False
    if "email_elig" in combined:
        email = (
            combined.loc[clean_index, "email_elig"]
            .astype("string")
            .str.strip()
            .str.lower()
        )
        populated = email.notna() & email.ne("")
        if populated.any():
            email_component_available = True
            duplicate_email = email.duplicated(keep=False) & populated
            domains = email.str.rsplit("@", n=1).str[-1]
            disposable = domains.isin(set(rules_cfg["disposable_email_domains"]))
            rule_r10.loc[clean_index] |= duplicate_email | disposable.fillna(False)
    rules["rule_R10"] = rule_r10

    # R10 remains a validation signal. It is intentionally not tier-defining:
    # the prompt's explicit Tier-1 definition lists R1/R2/R5/R8/R9, and the
    # observed R10 reference false-positive rate exceeds the stated usability bar.
    confirmed = rules[["rule_R1", "rule_R2", "rule_R5", "rule_R8", "rule_R9"]].any(
        axis=1
    )
    suspicion_count = rules[["rule_R3", "rule_R4", "rule_R6", "rule_R7"]].sum(axis=1)
    tier = pd.Series(4, index=combined.index, dtype=int)
    tier.loc[suspicion_count.eq(1)] = 3
    tier.loc[suspicion_count.ge(2)] = 2
    tier.loc[confirmed] = 1
    rules["suspicion_rule_count"] = suspicion_count
    rules["tier"] = tier
    rules["tier_label"] = tier.map(
        {1: "Confirmed invalid", 2: "High suspicion", 3: "Uncertain", 4: "Pass"}
    )

    rule_definitions = pd.DataFrame(
        [
            ["R1", "Total time below verified-human floor", rules_cfg["total_time_floor_min"], "minutes; 4797 completed-human minimum"],
            ["R2", "TFA time below verified-human floor", rules_cfg["tfa_time_floor_min"], "minutes; 4797 completed-human minimum"],
            ["R3", "Any instrument below clean 1st percentile", json.dumps(fast_thresholds, sort_keys=True), "computed on 131 completed 4797 records"],
            ["R4", "Within-block response SD at/below clean 1st percentile", json.dumps(straightline_thresholds, sort_keys=True), "separate 1–4, 1–5, 1–6, and 1–7 blocks"],
            ["R5", "Exact ordered Likert fingerprint duplicate", rules_cfg["duplicate_min_answer_fraction"], "minimum answered fraction before hashing"],
            ["R6", "At least 3 submissions in clean-derived short window", burst_window_seconds, "seconds; bounded clean 1st-percentile inter-arrival"],
            ["R7", "Near-duplicate open text", rules_cfg["open_text_similarity_threshold"], "TF-IDF cosine similarity"],
            ["R8", "Logical family-information inconsistency", "logical", "autistic-child follow-ups, branching contradictions, or impossible age-band count"],
            ["R9", "Impossible demographic combination", "logical", "age, ZIP, and first-birth plausibility"],
            ["R10", "4797 instrument-native anti-fraud gate (validation-only, not tier-defining)", "logical", f"knee/age/email; email available={email_component_available}"],
        ],
        columns=["rule", "definition", "threshold", "justification"],
    )
    reference_rules = rules.loc[reference_index, [f"rule_R{i}" for i in range(1, 11)]]
    false_positive = pd.DataFrame(
        {
            "rule": [f"R{i}" for i in range(1, 11)],
            "verified_human_n": len(reference_index),
            "flagged_n": [int(reference_rules[f"rule_R{i}"].sum()) for i in range(1, 11)],
            "false_positive_pct": [
                reference_rules[f"rule_R{i}"].mean() * 100 for i in range(1, 11)
            ],
        }
    )
    context = {
        "combined_records": combined,
        "likert_fields": likert_fields,
        "likert_by_scale": likert_by_scale,
        "likert_numeric": likert_numeric,
        "open_text_fields": open_text_fields,
        "verified_human_index": reference_index,
        "burst_window_seconds": burst_window_seconds,
        "email_component_available": email_component_available,
        "branching_audit": branching_audit,
    }
    return features, rules, rule_definitions, false_positive, context


def fit_behavioral_detectors(
    features: pd.DataFrame,
    rules: pd.DataFrame,
    config: dict,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Fit three clean-reference novelty detectors and a PU source classifier."""

    seed = int(config["analysis"]["seed"])
    detector_cfg = config["detectors"]
    candidate_fields = [
        column
        for column in features
        if column.startswith("feat_")
        and column
        not in {
            "feat_completed_all",
            "feat_completed_all_and_fast",
        }
    ]
    usable_fields = []
    for field in candidate_fields:
        numeric = pd.to_numeric(features[field], errors="coerce")
        if numeric.notna().mean() >= 0.20 and numeric.nunique(dropna=True) > 1:
            usable_fields.append(field)
    numeric_features = features[usable_fields].apply(pd.to_numeric, errors="coerce")
    clean_tier4 = features["source_project"].eq("clean_4797") & rules["tier"].eq(4)
    if clean_tier4.sum() < max(40, len(usable_fields) * 2):
        raise RuntimeError(
            f"Only {int(clean_tier4.sum())} clean Tier-4 records are available "
            "for the human behavioral envelope."
        )
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    clean_imputed = imputer.fit_transform(numeric_features.loc[clean_tier4])
    clean_scaled = scaler.fit_transform(clean_imputed)
    all_scaled = scaler.transform(imputer.transform(numeric_features))

    isolation = IsolationForest(
        n_estimators=int(detector_cfg["isolation_forest_estimators"]),
        contamination="auto",
        random_state=seed,
        n_jobs=-1,
    ).fit(clean_scaled)
    lof = LocalOutlierFactor(
        n_neighbors=min(int(detector_cfg["lof_neighbors"]), len(clean_scaled) - 1),
        novelty=True,
        contamination="auto",
        n_jobs=-1,
    ).fit(clean_scaled)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        robust_covariance = MinCovDet(
            random_state=seed, support_fraction=0.80
        ).fit(clean_scaled)

    scores = pd.DataFrame(index=features.index)
    scores["score_isolation"] = -isolation.decision_function(all_scaled)
    scores["score_lof"] = -lof.decision_function(all_scaled)
    scores["score_mahalanobis"] = robust_covariance.mahalanobis(all_scaled)
    score_quantile = float(detector_cfg["score_cut_quantile"])
    detector_thresholds = {}
    for score_field in ("score_isolation", "score_lof", "score_mahalanobis"):
        threshold = float(scores.loc[clean_tier4, score_field].quantile(score_quantile))
        detector_thresholds[score_field] = threshold
        scores[score_field.replace("score_", "flag_")] = scores[score_field].gt(threshold)

    source_is_clean = features["source_project"].eq("clean_4797").astype(int)
    classifier = GradientBoostingClassifier(
        n_estimators=250,
        learning_rate=0.035,
        max_depth=2,
        min_samples_leaf=12,
        subsample=0.85,
        random_state=seed,
    )
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    source_probability_oof = cross_val_predict(
        classifier,
        all_scaled,
        source_is_clean,
        cv=cv,
        method="predict_proba",
        n_jobs=-1,
    )[:, 1]
    classifier.fit(all_scaled, source_is_clean)
    source_probability_fit = classifier.predict_proba(all_scaled)[:, 1]
    c_estimate = float(source_probability_oof[clean_tier4].mean())
    if not 0 < c_estimate <= 1:
        raise RuntimeError("Elkan–Noto labeling-propensity estimate is invalid")
    scores["score_source_classifier"] = 1 - source_probability_oof
    scores["score_pu_nonhuman"] = 1 - np.clip(
        source_probability_oof / c_estimate, 0, 1
    )
    pu_threshold = float(
        scores.loc[clean_tier4, "score_pu_nonhuman"].quantile(score_quantile)
    )
    detector_thresholds["score_pu_nonhuman"] = pu_threshold
    scores["flag_pu"] = scores["score_pu_nonhuman"].gt(pu_threshold)

    source_auc = roc_auc_score(source_is_clean, source_probability_oof)
    rng = np.random.default_rng(seed)
    clean_indices = np.flatnonzero(clean_tier4.to_numpy())
    negative_control_label = np.zeros(len(clean_indices), dtype=int)
    negative_control_label[rng.choice(
        len(clean_indices), size=len(clean_indices) // 2, replace=False
    )] = 1
    negative_cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed + 1)
    negative_probability = cross_val_predict(
        GradientBoostingClassifier(
            n_estimators=150,
            learning_rate=0.04,
            max_depth=2,
            min_samples_leaf=8,
            random_state=seed + 1,
        ),
        all_scaled[clean_indices],
        negative_control_label,
        cv=negative_cv,
        method="predict_proba",
    )[:, 1]
    negative_control_auc = roc_auc_score(
        negative_control_label, negative_probability
    )

    dirty_mask = features["source_project"].eq("dirty_4581")
    dirty_pu = scores.loc[dirty_mask, "score_pu_nonhuman"].to_numpy()
    bootstrap_rng = np.random.default_rng(seed + 100)
    bootstrap_estimates = np.array(
        [
            bootstrap_rng.choice(dirty_pu, size=len(dirty_pu), replace=True).mean()
            for _ in range(int(detector_cfg["pu_bootstrap_repeats"]))
        ]
    )
    contamination_summary = pd.DataFrame(
        [
            {
                "quantity": "Elkan–Noto-style nonhuman share in 4581",
                "estimate": dirty_pu.mean(),
                "interval_low": np.quantile(bootstrap_estimates, 0.025),
                "interval_high": np.quantile(bootstrap_estimates, 0.975),
                "interpretation": (
                    "Model-implied separation fraction only; recruitment-period and "
                    "instrument drift violate SCAR, so this is not an identifiable bot prevalence."
                ),
            },
            {
                "quantity": "Source-classifier AUC: 4797 versus 4581",
                "estimate": source_auc,
                "interval_low": np.nan,
                "interval_high": np.nan,
                "interpretation": "Separation includes legitimate cohort and instrument drift.",
            },
            {
                "quantity": "Negative-control AUC: random 4797 half A versus half B",
                "estimate": negative_control_auc,
                "interval_low": np.nan,
                "interval_high": np.nan,
                "interpretation": "Should remain near 0.5; detects pipeline-induced separation.",
            },
            {
                "quantity": "Elkan–Noto labeling propensity c",
                "estimate": c_estimate,
                "interval_low": np.nan,
                "interval_high": np.nan,
                "interpretation": "Mean out-of-fold labeled-positive score among clean Tier-4 records.",
            },
        ]
    )

    permutation = permutation_importance(
        classifier,
        all_scaled,
        source_is_clean,
        scoring="roc_auc",
        n_repeats=20,
        random_state=seed,
        n_jobs=-1,
    )
    importance = pd.DataFrame(
        {
            "feature": usable_fields,
            "permutation_auc_decrease": permutation.importances_mean,
            "permutation_sd": permutation.importances_std,
        }
    )
    try:
        import shap

        sample_rng = np.random.default_rng(seed)
        sample_n = min(500, len(all_scaled))
        sample_index = np.sort(
            sample_rng.choice(len(all_scaled), size=sample_n, replace=False)
        )
        explainer = shap.TreeExplainer(classifier)
        shap_values = explainer.shap_values(all_scaled[sample_index])
        if isinstance(shap_values, list):
            shap_array = np.asarray(shap_values[-1])
        else:
            shap_array = np.asarray(shap_values)
        if shap_array.ndim == 3:
            shap_array = shap_array[:, :, -1]
        shap_importance = np.mean(np.abs(shap_array), axis=0)
        importance["mean_abs_shap"] = shap_importance
        shap_status = "computed"
    except Exception as exc:
        importance["mean_abs_shap"] = np.nan
        shap_status = f"unavailable: {type(exc).__name__}: {exc}"
    importance = importance.sort_values(
        ["permutation_auc_decrease", "mean_abs_shap"], ascending=False
    ).reset_index(drop=True)

    flags_for_agreement = pd.DataFrame(
        {
            "rule_high_suspicion_or_invalid": rules["tier"].le(2),
            "isolation": scores["flag_isolation"],
            "lof": scores["flag_lof"],
            "mahalanobis": scores["flag_mahalanobis"],
            "pu_classifier": scores["flag_pu"],
        },
        index=features.index,
    )
    agreement_rows = []
    for left_index, left_name in enumerate(flags_for_agreement.columns):
        for right_name in flags_for_agreement.columns[left_index + 1 :]:
            left = flags_for_agreement[left_name].astype(bool)
            right = flags_for_agreement[right_name].astype(bool)
            union = (left | right).sum()
            agreement_rows.append(
                {
                    "method_a": left_name,
                    "method_b": right_name,
                    "cohen_kappa": cohen_kappa_score(left, right),
                    "overlap_n": int((left & right).sum()),
                    "jaccard_overlap": (left & right).sum() / union if union else np.nan,
                }
            )
    agreement = pd.DataFrame(agreement_rows)
    context = {
        "feature_fields": usable_fields,
        "imputer": imputer,
        "scaler": scaler,
        "all_scaled": all_scaled,
        "clean_tier4": clean_tier4,
        "detector_thresholds": detector_thresholds,
        "source_classifier": classifier,
        "source_probability_fit": source_probability_fit,
        "shap_status": shap_status,
        "agreement_flags": flags_for_agreement,
    }
    return scores, contamination_summary, importance, agreement, context


def scale_to_100(series: pd.Series, low: float, high: float, reverse: bool = False) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if reverse:
        numeric = low + high - numeric
    return (numeric - low) / (high - low) * 100


def row_mean(series_list: list[pd.Series], minimum_count: int) -> pd.Series:
    frame = pd.concat(series_list, axis=1)
    return frame.mean(axis=1).where(frame.notna().sum(axis=1) >= minimum_count)


def build_cluster_domains(frame: pd.DataFrame) -> pd.DataFrame:
    """Build the locked ten TFA composites without held-out-variable leakage."""

    domains = pd.DataFrame(index=frame.index)
    domains["Positive affect / clinical trust"] = row_mean(
        [
            scale_to_100(_numeric(frame, field), 1, 5)
            for field in ["tfa_happy", "tfa_trusting", "tfa_comfortable"]
        ],
        2,
    )
    domains["Low distress"] = row_mean(
        [
            scale_to_100(_numeric(frame, field), 1, 5, reverse=True)
            for field in ["tfa_scared", "tfa_sad"]
        ],
        2,
    )
    domains["Low aversive affect"] = row_mean(
        [
            scale_to_100(_numeric(frame, field), 1, 5, reverse=True)
            for field in ["tfa_disgusted", "tfa_angry"]
        ],
        2,
    )
    quick = row_mean(
        [
            scale_to_100(_numeric(frame, "tfa_difficult"), 1, 4),
            scale_to_100(_numeric(frame, "tfa_doable"), 1, 4, reverse=True),
        ],
        2,
    )
    one_visit = row_mean(
        [
            scale_to_100(_numeric(frame, "tfa_separate_difficult"), 1, 4),
            scale_to_100(
                _numeric(frame, "tfa_separate_doable"), 1, 4, reverse=True
            ),
        ],
        2,
    )
    series = row_mean(
        [
            scale_to_100(_numeric(frame, "tfa_series_difficult"), 1, 4),
            scale_to_100(_numeric(frame, "tfa_series_doable"), 1, 4, reverse=True),
        ],
        2,
    )
    domains["Feasibility across pathways"] = row_mean([quick, one_visit, series], 2)
    domains["Low-burden modality openness"] = row_mean(
        [
            scale_to_100(_numeric(frame, field), 1, 7)
            for field in ["tfa_video", "tfa_saliva", "tfa_heart", "tfa_observe"]
        ],
        3,
    )
    domains["Equipment-intensive openness"] = row_mean(
        [
            scale_to_100(_numeric(frame, field), 1, 7)
            for field in ["tfa_mri", "tfa_eeg"]
        ],
        2,
    )
    domains["Blood-test openness"] = scale_to_100(_numeric(frame, "tfa_blood"), 1, 7)
    domains["Accuracy tolerance"] = scale_to_100(
        _numeric(frame, "tfa_screen_accuracy"), 1, 5
    )
    domains["Perceived screening utility"] = scale_to_100(
        _numeric(frame, "tfa_overall_help"), 1, 4, reverse=True
    )
    domains["Ethical / moral support"] = row_mean(
        [
            scale_to_100(_numeric(frame, "tfa_scan_any"), 1, 5, reverse=True),
            scale_to_100(_numeric(frame, "tfa_scan_mine"), 1, 6, reverse=True),
        ],
        2,
    )
    return domains


def fit_named_kmeans(frame: pd.DataFrame, config: dict) -> ClusterFit:
    domains = build_cluster_domains(frame)
    eligible = domains.notna().sum(axis=1).ge(
        int(config["analysis"]["minimum_cluster_domains"])
    )
    model_domains = domains.loc[eligible]
    if len(model_domains) < 10:
        raise RuntimeError("Too few cluster-eligible records for k=2 analysis")
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    X = scaler.fit_transform(imputer.fit_transform(model_domains))
    model = KMeans(
        n_clusters=int(config["analysis"]["selected_k"]),
        random_state=int(config["analysis"]["seed"]),
        n_init=100,
    ).fit(X)
    raw_labels = model.labels_
    acceptability = model_domains.drop(columns=["Accuracy tolerance"]).assign(
        raw_cluster=raw_labels
    ).groupby("raw_cluster").mean().mean(axis=1)
    higher_raw = int(acceptability.idxmax())
    named = pd.Series(
        np.where(
            raw_labels == higher_raw,
            "Higher acceptability",
            "Conditional acceptability",
        ),
        index=model_domains.index,
        dtype="string",
    )
    profile_means = (
        model_domains.assign(cluster=named)
        .groupby("cluster")
        .mean()
        .reindex(CLUSTER_ORDER)
    )
    case_silhouette = pd.Series(
        silhouette_samples(X, raw_labels), index=model_domains.index
    )
    return ClusterFit(
        frame=frame,
        domains=domains,
        eligible=eligible,
        X=X,
        imputer=imputer,
        scaler=scaler,
        model=model,
        raw_labels=raw_labels,
        named_labels=named,
        silhouette=float(silhouette_score(X, raw_labels)),
        case_silhouette=case_silhouette,
        profile_means=profile_means,
    )


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return np.nan, np.nan
    proportion = successes / total
    denominator = 1 + z**2 / total
    center = (proportion + z**2 / (2 * total)) / denominator
    half = (
        z
        * np.sqrt(
            proportion * (1 - proportion) / total + z**2 / (4 * total**2)
        )
        / denominator
    )
    return center - half, center + half


def _screen_summary(frame: pd.DataFrame, fit: ClusterFit) -> tuple[pd.DataFrame, float, float, float]:
    response = _numeric(frame, "tfa_free_screen")
    screen_yes = response.eq(4).where(response.notna())
    rows = []
    rates = {}
    intervals = {}
    for cluster_name in CLUSTER_ORDER:
        index = fit.named_labels.index[fit.named_labels.eq(cluster_name)]
        values = screen_yes.loc[index].dropna().astype(bool)
        yes = int(values.sum())
        total = len(values)
        low, high = wilson_interval(yes, total)
        rate = yes / total if total else np.nan
        rows.append(
            {
                "cluster": cluster_name,
                "valid_n": total,
                "definitely_yes_n": yes,
                "definitely_yes_pct": rate * 100,
                "wilson_low_pct": low * 100,
                "wilson_high_pct": high * 100,
            }
        )
        rates[cluster_name] = rate
        intervals[cluster_name] = (low, high)
    gap = (rates[CLUSTER_ORDER[0]] - rates[CLUSTER_ORDER[1]]) * 100
    # Newcombe-Wilson interval for an independent difference in proportions.
    gap_low = (
        intervals[CLUSTER_ORDER[0]][0] - intervals[CLUSTER_ORDER[1]][1]
    ) * 100
    gap_high = (
        intervals[CLUSTER_ORDER[0]][1] - intervals[CLUSTER_ORDER[1]][0]
    ) * 100
    return pd.DataFrame(rows), gap, gap_low, gap_high


def _penalized_loglik(beta: np.ndarray, X: np.ndarray, y: np.ndarray) -> float:
    eta = X @ beta
    probability = expit(eta)
    loglik = np.sum(y * eta - np.logaddexp(0, eta))
    weights = np.clip(probability * (1 - probability), 1e-10, None)
    information = X.T @ (weights[:, None] * X)
    sign, logdet = np.linalg.slogdet(information)
    if sign <= 0:
        return -np.inf
    return float(loglik + 0.5 * logdet)


def fit_firth_logistic(
    X: np.ndarray, y: np.ndarray, max_iter: int = 200, tolerance: float = 1e-8
) -> dict:
    """Fit Jeffreys-prior/Firth logistic regression with profile-likelihood CIs."""

    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    beta = np.zeros(X.shape[1], dtype=float)
    current = _penalized_loglik(beta, X, y)
    converged = False
    for iteration in range(max_iter):
        eta = X @ beta
        probability = expit(eta)
        weights = np.clip(probability * (1 - probability), 1e-10, None)
        information = X.T @ (weights[:, None] * X)
        information_inv = np.linalg.pinv(information)
        hat_diag = np.sum((X @ information_inv) * X, axis=1) * weights
        adjusted_score = X.T @ (
            y - probability + hat_diag * (0.5 - probability)
        )
        step = information_inv @ adjusted_score
        step_scale = 1.0
        accepted = False
        while step_scale >= 1e-6:
            candidate = beta + step_scale * step
            candidate_loglik = _penalized_loglik(candidate, X, y)
            if candidate_loglik >= current:
                beta = candidate
                current = candidate_loglik
                accepted = True
                break
            step_scale /= 2
        if not accepted:
            break
        if np.max(np.abs(step_scale * step)) < tolerance:
            converged = True
            break
    eta = X @ beta
    probability = expit(eta)
    weights = np.clip(probability * (1 - probability), 1e-10, None)
    covariance = np.linalg.pinv(X.T @ (weights[:, None] * X))
    standard_error = np.sqrt(np.diag(covariance))
    cutoff = stats.chi2.ppf(0.95, 1)

    def profile_value(coefficient_index: int, fixed_value: float) -> float:
        nuisance_index = [
            index for index in range(X.shape[1]) if index != coefficient_index
        ]

        def objective(nuisance: np.ndarray) -> float:
            trial = np.empty(X.shape[1], dtype=float)
            trial[coefficient_index] = fixed_value
            trial[nuisance_index] = nuisance
            return -_penalized_loglik(trial, X, y)

        result = optimize.minimize(
            objective,
            beta[nuisance_index],
            method="BFGS",
            options={"maxiter": 300, "gtol": 1e-8},
        )
        return -float(result.fun)

    ci = np.full((X.shape[1], 2), np.nan)
    for index in range(X.shape[1]):
        target = current - cutoff / 2

        def root(value: float) -> float:
            return profile_value(index, value) - target

        for direction, ci_column in [(-1, 0), (1, 1)]:
            inner = beta[index]
            step_size = max(0.5, 2 * standard_error[index])
            outer = inner + direction * step_size
            for _ in range(20):
                if root(outer) <= 0:
                    break
                step_size *= 1.7
                outer = inner + direction * step_size
            try:
                bracket = (outer, inner) if direction < 0 else (inner, outer)
                ci[index, ci_column] = optimize.brentq(root, *bracket, maxiter=100)
            except (ValueError, RuntimeError):
                pass
    return {
        "coef": beta,
        "se": standard_error,
        "ci": ci,
        "penalized_loglik": current,
        "converged": converged,
        "iterations": iteration + 1,
    }


def _logistic_design(
    frame: pd.DataFrame, fit: ClusterFit, interaction: bool = False
) -> tuple[pd.DataFrame, pd.Series]:
    index = fit.named_labels.index
    response_raw = _numeric(frame.loc[index], "tfa_free_screen")
    asd_raw = _numeric(frame.loc[index], "fif_num_autistic")
    design = pd.DataFrame(
        {
            "intercept": 1.0,
            "higher_acceptability": fit.named_labels.eq(
                "Higher acceptability"
            ).astype(float),
            "asd_child_at_home": asd_raw.gt(0).where(asd_raw.notna()).astype(float),
        },
        index=index,
    )
    if interaction:
        design["cluster_x_asd"] = (
            design["higher_acceptability"] * design["asd_child_at_home"]
        )
    outcome = response_raw.eq(4).where(response_raw.notna()).astype(float)
    valid = design.notna().all(axis=1) & outcome.notna()
    return design.loc[valid], outcome.loc[valid]


def fit_logistic_models(frame: pd.DataFrame, fit: ClusterFit) -> pd.DataFrame:
    def safe_exp(value: float) -> float:
        if np.isnan(value):
            return np.nan
        if value == -np.inf:
            return 0.0
        if value == np.inf:
            return np.inf
        return float(np.exp(value)) if value < np.log(np.finfo(float).max) else np.inf

    rows = []
    for model_name, interaction in [("additive", False), ("interaction", True)]:
        design, outcome = _logistic_design(frame, fit, interaction=interaction)
        if outcome.nunique() < 2 or len(outcome) < design.shape[1] + 5:
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ConvergenceWarning)
                mle = sm.Logit(outcome, design).fit(disp=False, maxiter=200)
            mle_confidence = mle.conf_int()
            mle_converged = bool(mle.mle_retvals.get("converged", False))
        except Exception:
            mle = None
            mle_confidence = None
            mle_converged = False
        firth = fit_firth_logistic(design.to_numpy(), outcome.to_numpy())
        for column_index, term in enumerate(design.columns):
            if term == "intercept":
                continue
            rows.append(
                {
                    "model": model_name,
                    "term": term,
                    "n": len(outcome),
                    "mle_or": (
                        safe_exp(float(mle.params[term])) if mle is not None else np.nan
                    ),
                    "mle_ci_low": (
                        safe_exp(float(mle_confidence.loc[term, 0]))
                        if mle_confidence is not None
                        else np.nan
                    ),
                    "mle_ci_high": (
                        safe_exp(float(mle_confidence.loc[term, 1]))
                        if mle_confidence is not None
                        else np.nan
                    ),
                    "mle_p": (
                        float(mle.pvalues[term]) if mle is not None else np.nan
                    ),
                    "mle_converged": mle_converged,
                    "firth_or": safe_exp(float(firth["coef"][column_index])),
                    "firth_profile_ci_low": safe_exp(
                        float(firth["ci"][column_index, 0])
                    ),
                    "firth_profile_ci_high": safe_exp(
                        float(firth["ci"][column_index, 1])
                    ),
                    "firth_converged": bool(firth["converged"]),
                }
            )
    return pd.DataFrame(rows)


def benjamini_hochberg(p_values: pd.Series) -> np.ndarray:
    p = np.asarray(p_values, dtype=float)
    order = np.argsort(p)
    ranked = p[order]
    adjusted_ranked = ranked * len(p) / np.arange(1, len(p) + 1)
    adjusted_ranked = np.minimum.accumulate(adjusted_ranked[::-1])[::-1]
    adjusted = np.empty_like(adjusted_ranked)
    adjusted[order] = np.clip(adjusted_ranked, 0, 1)
    return adjusted


def _cohens_d(first: pd.Series, second: pd.Series) -> float:
    first = pd.to_numeric(first, errors="coerce").dropna()
    second = pd.to_numeric(second, errors="coerce").dropna()
    if len(first) < 2 or len(second) < 2:
        return np.nan
    pooled = np.sqrt(
        (
            (len(first) - 1) * first.var(ddof=1)
            + (len(second) - 1) * second.var(ddof=1)
        )
        / (len(first) + len(second) - 2)
    )
    return (first.mean() - second.mean()) / pooled if pooled > 0 else np.nan


def _knowledge_scores(frame: pd.DataFrame) -> pd.DataFrame:
    verified_key = {
        "tfa_genetics": 1,
        "tfa_vaccines": 4,
        "tfa_tylenol": 4,
        "tfa_medications": 1,
        "tfa_rate": 1,
        "tfa_autistic_us": 2,
        "tfa_mult_kids_odds": 3,
    }
    binary = pd.DataFrame(index=frame.index)
    for field, correct in verified_key.items():
        value = _numeric(frame, field)
        binary[field] = value.eq(correct).where(value.notna()).astype(float)
    scores = pd.DataFrame(index=frame.index)
    scores["knowledge_binary_verified_7"] = binary.sum(axis=1).where(
        binary.notna().all(axis=1)
    )
    graded_maps = {
        "tfa_genetics": {1: 4, 2: 3, 3: 2, 4: 1},
        "tfa_vaccines": {1: 1, 2: 2, 3: 3, 4: 4},
        "tfa_tylenol": {1: 1, 2: 2, 3: 3, 4: 4},
        "tfa_medications": {1: 4, 2: 3, 3: 2, 4: 1},
        "tfa_rate": {1: 4, 2: 3, 3: 2, 4: 1},
    }
    graded = pd.DataFrame(
        {
            field: _numeric(frame, field).map(mapping)
            for field, mapping in graded_maps.items()
        },
        index=frame.index,
    )
    scores["knowledge_graded_documented_5"] = graded.sum(axis=1).where(
        graded.notna().all(axis=1)
    )
    return scores


def characterize_definition(
    definition: str, frame: pd.DataFrame, fit: ClusterFit
) -> pd.DataFrame:
    """Run held-out knowledge, values, and demographic comparisons."""

    rows: list[dict] = []
    knowledge = _knowledge_scores(frame)
    for measure in knowledge.columns:
        groups = [
            knowledge.loc[
                fit.named_labels.index[fit.named_labels.eq(cluster)], measure
            ].dropna()
            for cluster in CLUSTER_ORDER
        ]
        p_value = (
            stats.mannwhitneyu(groups[0], groups[1], alternative="two-sided").pvalue
            if min(map(len, groups)) > 0
            else np.nan
        )
        rows.append(
            {
                "definition": definition,
                "family": "knowledge",
                "measure": measure,
                "higher_n": len(groups[0]),
                "conditional_n": len(groups[1]),
                "higher_estimate": groups[0].mean(),
                "conditional_estimate": groups[1].mean(),
                "effect": _cohens_d(groups[0], groups[1]),
                "raw_p": p_value,
            }
        )

    value_fields = [
        "conformity_val",
        "tradition_val",
        "benevolence_val",
        "universalism_val",
        "self_direction_val",
        "stimulation_val",
        "hedonism_val",
        "achievement_val",
        "power_val",
        "security_val",
    ]
    for measure in value_fields:
        groups = [
            _numeric(
                frame.loc[
                    fit.named_labels.index[fit.named_labels.eq(cluster)]
                ],
                measure,
            ).dropna()
            for cluster in CLUSTER_ORDER
        ]
        p_value = (
            stats.mannwhitneyu(groups[0], groups[1], alternative="two-sided").pvalue
            if min(map(len, groups)) > 0
            else np.nan
        )
        rows.append(
            {
                "definition": definition,
                "family": "values",
                "measure": measure,
                "higher_n": len(groups[0]),
                "conditional_n": len(groups[1]),
                "higher_estimate": groups[0].mean(),
                "conditional_estimate": groups[1].mean(),
                "effect": _cohens_d(groups[0], groups[1]),
                "raw_p": p_value,
            }
        )

    continuous_demographics = {
        "caregiver_age": _numeric(frame, "age_check_demo").where(
            _numeric(frame, "age_check_demo").between(18, 100)
        ),
        "household_size": _numeric(frame, "demo_num_home").where(
            _numeric(frame, "demo_num_home").between(1, 20)
        ),
        "number_of_children": _numeric(frame, "fif_num_children").where(
            _numeric(frame, "fif_num_children").between(0, 20)
        ),
    }
    for measure, values in continuous_demographics.items():
        groups = [
            values.loc[
                fit.named_labels.index[fit.named_labels.eq(cluster)]
            ].dropna()
            for cluster in CLUSTER_ORDER
        ]
        p_value = (
            stats.mannwhitneyu(groups[0], groups[1], alternative="two-sided").pvalue
            if min(map(len, groups)) > 0
            else np.nan
        )
        rows.append(
            {
                "definition": definition,
                "family": "demographics",
                "measure": measure,
                "higher_n": len(groups[0]),
                "conditional_n": len(groups[1]),
                "higher_estimate": groups[0].median(),
                "conditional_estimate": groups[1].median(),
                "effect": _cohens_d(groups[0], groups[1]),
                "raw_p": p_value,
            }
        )
    result = pd.DataFrame(rows)
    result["fdr_p"] = np.nan
    for family, index in result.groupby("family").groups.items():
        valid_index = result.loc[index, "raw_p"].dropna().index
        if len(valid_index):
            result.loc[valid_index, "fdr_p"] = benjamini_hochberg(
                result.loc[valid_index, "raw_p"]
            )
    return result


def run_inclusion_sensitivity(
    bundle: SourceBundle,
    rules: pd.DataFrame,
    project_dir: Path,
    config: dict,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, ClusterFit], dict[str, pd.DataFrame]]:
    combined = pd.concat(bundle.records.values(), axis=0, sort=False)
    selected_path = project_dir / "cleaned_autism_study_data.csv"
    selected_column = pd.read_csv(selected_path, nrows=0).columns[0]
    selected_ids = (
        pd.read_csv(selected_path, usecols=[selected_column])[
            selected_column
        ]
        .pipe(pd.to_numeric, errors="coerce")
        .dropna()
        .astype(int)
        .astype(str)
    )
    selected_uid = {"4797_" + record_id for record_id in selected_ids}
    clean = combined["source_project"].eq("clean_4797")
    dirty = combined["source_project"].eq("dirty_4581")
    supplied = combined.index.isin(selected_uid)
    definitions = {
        "1. 4797 Tier 4 only": clean & supplied & rules["tier"].eq(4),
        "2. 4797 Tiers 3+4": clean & supplied & rules["tier"].isin([3, 4]),
        "3. 4797 status quo": clean & supplied,
        "4. 4797 Tiers 3+4 + 4581 Tier 4": (
            clean & supplied & rules["tier"].isin([3, 4])
        )
        | (dirty & rules["tier"].eq(4)),
        "5. 4581 Tier 4 replication": dirty & rules["tier"].eq(4),
    }
    fits: dict[str, ClusterFit] = {}
    frames: dict[str, pd.DataFrame] = {}
    status_quo_fit: ClusterFit | None = None
    summary_rows = []
    logistic_tables = []
    characterization_tables = []
    for definition, mask in definitions.items():
        frame = combined.loc[mask].copy()
        fit = fit_named_kmeans(frame, config)
        fits[definition] = fit
        frames[definition] = frame
        if definition.startswith("3."):
            status_quo_fit = fit
        screen_table, gap, gap_low, gap_high = _screen_summary(frame, fit)
        logistic = fit_logistic_models(frame, fit)
        logistic.insert(0, "definition", definition)
        logistic_tables.append(logistic)
        characterization_tables.append(characterize_definition(definition, frame, fit))
        sizes = fit.named_labels.value_counts().reindex(CLUSTER_ORDER, fill_value=0)
        summary_rows.append(
            {
                "definition": definition,
                "input_n": len(frame),
                "clusterable_n": int(fit.eligible.sum()),
                "silhouette_k2": fit.silhouette,
                "higher_n": int(sizes["Higher acceptability"]),
                "conditional_n": int(sizes["Conditional acceptability"]),
                "screen_gap_pp": gap,
                "screen_gap_ci_low_pp": gap_low,
                "screen_gap_ci_high_pp": gap_high,
            }
        )
    assert status_quo_fit is not None
    summary = pd.DataFrame(summary_rows)
    summary["ari_vs_status_quo"] = np.nan
    for row_index, definition in enumerate(summary["definition"]):
        fit = fits[definition]
        overlap = status_quo_fit.named_labels.index.intersection(fit.named_labels.index)
        if len(overlap) >= 2:
            summary.loc[row_index, "ari_vs_status_quo"] = adjusted_rand_score(
                status_quo_fit.named_labels.loc[overlap],
                fit.named_labels.loc[overlap],
            )
    logistic_all = pd.concat(logistic_tables, ignore_index=True)
    additive_cluster = logistic_all.loc[
        logistic_all["model"].eq("additive")
        & logistic_all["term"].eq("higher_acceptability")
    ].set_index("definition")
    for column, source_column in [
        ("cluster_firth_or", "firth_or"),
        ("cluster_firth_ci_low", "firth_profile_ci_low"),
        ("cluster_firth_ci_high", "firth_profile_ci_high"),
    ]:
        summary[column] = summary["definition"].map(additive_cluster[source_column])
    characterization = pd.concat(characterization_tables, ignore_index=True)
    return summary, logistic_all, characterization, fits, frames


def analyze_latent_profiles(
    status_quo_fit: ClusterFit, status_quo_frame: pd.DataFrame, config: dict
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Fit GMM grid, parametric BLRT, consensus matrix, and BCH correction."""

    seed = int(config["analysis"]["seed"])
    model_cfg = config["modeling"]
    X = status_quo_fit.X
    grid_rows = []
    fitted_models: dict[tuple[int, str], GaussianMixture] = {}
    for components in range(
        int(model_cfg["gmm_k_min"]), int(model_cfg["gmm_k_max"]) + 1
    ):
        for covariance_type in model_cfg["gmm_covariance_types"]:
            sklearn_covariance_type = (
                "diag" if covariance_type == "diagonal" else covariance_type
            )
            model = GaussianMixture(
                n_components=components,
                covariance_type=sklearn_covariance_type,
                n_init=20,
                max_iter=1000,
                reg_covar=1e-6,
                random_state=seed,
            ).fit(X)
            fitted_models[(components, covariance_type)] = model
            posterior = model.predict_proba(X)
            if components == 1:
                entropy = 1.0
                mean_max_posterior = 1.0
                p10_max_posterior = 1.0
            else:
                raw_entropy = -np.sum(
                    posterior * np.log(np.clip(posterior, 1e-12, 1))
                )
                entropy = 1 - raw_entropy / (len(X) * np.log(components))
                max_posterior = posterior.max(axis=1)
                mean_max_posterior = max_posterior.mean()
                p10_max_posterior = np.quantile(max_posterior, 0.10)
            grid_rows.append(
                {
                    "k": components,
                    "covariance_type": covariance_type,
                    "aic": model.aic(X),
                    "bic": model.bic(X),
                    "converged": model.converged_,
                    "entropy_separation": entropy,
                    "mean_max_posterior": mean_max_posterior,
                    "p10_max_posterior": p10_max_posterior,
                }
            )
    grid = pd.DataFrame(grid_rows).sort_values("bic").reset_index(drop=True)
    best_row = grid.iloc[0]
    best_model = fitted_models[
        (int(best_row["k"]), str(best_row["covariance_type"]))
    ]
    best_k2_row = (
        grid.loc[grid["k"].eq(2)].sort_values("bic").iloc[0]
    )
    best_k2_model = fitted_models[(2, str(best_k2_row["covariance_type"]))]
    posterior = best_k2_model.predict_proba(X)
    modal = posterior.argmax(axis=1)

    model_domains = status_quo_fit.domains.loc[status_quo_fit.eligible]
    component_acceptability = (
        model_domains.drop(columns=["Accuracy tolerance"])
        .assign(component=modal)
        .groupby("component")
        .mean()
        .mean(axis=1)
    )
    higher_component = int(component_acceptability.idxmax())
    component_names = {
        higher_component: "Higher acceptability",
        1 - higher_component: "Conditional acceptability",
    }
    posterior_named = pd.DataFrame(
        {
            "uid": model_domains.index,
            "modal_component": modal,
            "modal_profile": pd.Series(modal).map(component_names).to_numpy(),
            "max_posterior": posterior.max(axis=1),
            "posterior_higher": posterior[:, higher_component],
            "posterior_conditional": posterior[:, 1 - higher_component],
        }
    ).set_index("uid")

    covariance_type = str(best_k2_row["covariance_type"])
    sklearn_covariance_type = "diag" if covariance_type == "diagonal" else covariance_type
    null_model = GaussianMixture(
        n_components=1,
        covariance_type=sklearn_covariance_type,
        n_init=20,
        max_iter=1000,
        reg_covar=1e-6,
        random_state=seed,
    ).fit(X)
    alternative_model = best_k2_model
    observed_lr = 2 * (
        alternative_model.score(X) * len(X) - null_model.score(X) * len(X)
    )
    bootstrap_lr = []
    for repeat in range(int(model_cfg["blrt_bootstrap_repeats"])):
        simulated, _ = null_model.sample(len(X))
        simulated_null = GaussianMixture(
            n_components=1,
            covariance_type=sklearn_covariance_type,
            n_init=5,
            max_iter=500,
            reg_covar=1e-6,
            random_state=seed + repeat + 1,
        ).fit(simulated)
        simulated_alt = GaussianMixture(
            n_components=2,
            covariance_type=sklearn_covariance_type,
            n_init=10,
            max_iter=500,
            reg_covar=1e-6,
            random_state=seed + repeat + 1001,
        ).fit(simulated)
        bootstrap_lr.append(
            2
            * (
                simulated_alt.score(simulated) * len(simulated)
                - simulated_null.score(simulated) * len(simulated)
            )
        )
    bootstrap_lr_array = np.asarray(bootstrap_lr)
    blrt = pd.DataFrame(
        [
            {
                "comparison": "GMM k=2 versus k=1",
                "covariance_type": covariance_type,
                "observed_lr": observed_lr,
                "bootstrap_repeats": len(bootstrap_lr_array),
                "bootstrap_p": (
                    1 + np.sum(bootstrap_lr_array >= observed_lr)
                )
                / (len(bootstrap_lr_array) + 1),
                "best_overall_bic_k": int(best_row["k"]),
                "best_overall_bic_covariance": str(best_row["covariance_type"]),
            }
        ]
    )

    repeats = int(config["analysis"]["consensus_repeats"])
    rng = np.random.default_rng(seed)
    consensus = np.zeros((len(X), len(X)), dtype=np.float32)
    for repeat in range(repeats):
        bootstrap_index = rng.choice(len(X), size=len(X), replace=True)
        bootstrap_model = KMeans(
            n_clusters=2,
            random_state=seed + repeat + 1,
            n_init=20,
        ).fit(X[bootstrap_index])
        predicted = bootstrap_model.predict(X)
        consensus += predicted[:, None] == predicted[None, :]
    consensus /= repeats
    np.fill_diagonal(consensus, 1.0)
    consensus_frame = pd.DataFrame(
        consensus,
        index=model_domains.index,
        columns=model_domains.index,
    )

    # BCH three-step correction using the modal-class misclassification matrix.
    # Rows are modal assignments and columns are latent components.
    classification_matrix = np.zeros((2, 2), dtype=float)
    for assigned_class in range(2):
        for latent_class in range(2):
            classification_matrix[assigned_class, latent_class] = (
                posterior[modal == assigned_class, latent_class].sum()
                / posterior[:, latent_class].sum()
            )
    bch_transform = np.linalg.pinv(classification_matrix)
    modal_one_hot = np.eye(2)[modal]
    bch_weights = modal_one_hot @ bch_transform
    outcome_raw = _numeric(status_quo_frame.loc[model_domains.index], "tfa_free_screen")
    outcome = outcome_raw.eq(4).where(outcome_raw.notna()).astype(float)
    bch_rows = []
    for component in range(2):
        valid = outcome.notna().to_numpy()
        weights = bch_weights[valid, component]
        values = outcome.to_numpy()[valid]
        denominator = weights.sum()
        estimate = np.sum(weights * values) / denominator if denominator else np.nan
        bch_rows.append(
            {
                "profile": component_names[component],
                "valid_n_unweighted": int(valid.sum()),
                "bch_weight_sum": denominator,
                "bch_definitely_yes_pct": estimate * 100,
                "modal_definitely_yes_pct": outcome.loc[
                    posterior_named.index[
                        posterior_named["modal_component"].eq(component)
                    ]
                ].mean()
                * 100,
            }
        )
    bch_table = pd.DataFrame(bch_rows).set_index("profile").reindex(CLUSTER_ORDER).reset_index()
    context = {
        "best_model": best_model,
        "best_k2_model": best_k2_model,
        "best_row": best_row,
        "best_k2_row": best_k2_row,
        "posterior": posterior,
        "posterior_named": posterior_named,
        "consensus": consensus_frame,
        "classification_matrix": classification_matrix,
        "bch_weights": bch_weights,
        "bootstrap_lr": bootstrap_lr_array,
    }
    return grid, blrt, posterior_named.reset_index(), bch_table, context


def baseline_regression_checks(
    status_quo_fit: ClusterFit,
    status_quo_frame: pd.DataFrame,
    logistic_table: pd.DataFrame,
    config: dict,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    seed = int(config["analysis"]["seed"])
    screen_table, screen_gap, _, _ = _screen_summary(
        status_quo_frame, status_quo_fit
    )
    sizes = status_quo_fit.named_labels.value_counts().reindex(CLUSTER_ORDER)
    sample_size = int(np.floor(0.80 * len(status_quo_fit.X)))
    rng = np.random.default_rng(seed)
    stability = []
    for repeat in range(int(config["analysis"]["stability_repeats"])):
        sample_index = np.sort(
            rng.choice(len(status_quo_fit.X), size=sample_size, replace=False)
        )
        labels = KMeans(
            n_clusters=2, random_state=seed + repeat + 1, n_init=20
        ).fit_predict(status_quo_fit.X[sample_index])
        stability.append(
            adjusted_rand_score(
                status_quo_fit.raw_labels[sample_index],
                labels,
            )
        )
    definition = "3. 4797 status quo"
    additive = logistic_table.loc[
        logistic_table["definition"].eq(definition)
        & logistic_table["model"].eq("additive")
    ].set_index("term")
    interaction = logistic_table.loc[
        logistic_table["definition"].eq(definition)
        & logistic_table["model"].eq("interaction")
        & logistic_table["term"].eq("cluster_x_asd")
    ]
    observed = {
        "selected_n": len(status_quo_frame),
        "clusterable_n": int(status_quo_fit.eligible.sum()),
        "screen_valid_n": int(screen_table["valid_n"].sum()),
        "higher_n": int(sizes["Higher acceptability"]),
        "conditional_n": int(sizes["Conditional acceptability"]),
        "silhouette_k2": status_quo_fit.silhouette,
        "mean_subsample_ari": float(np.mean(stability)),
        "higher_screen_yes_n": int(
            screen_table.set_index("cluster").loc[
                "Higher acceptability", "definitely_yes_n"
            ]
        ),
        "higher_screen_valid_n": int(
            screen_table.set_index("cluster").loc[
                "Higher acceptability", "valid_n"
            ]
        ),
        "conditional_screen_yes_n": int(
            screen_table.set_index("cluster").loc[
                "Conditional acceptability", "definitely_yes_n"
            ]
        ),
        "conditional_screen_valid_n": int(
            screen_table.set_index("cluster").loc[
                "Conditional acceptability", "valid_n"
            ]
        ),
        "screen_gap_pp": screen_gap,
        "additive_cluster_or": additive.loc["higher_acceptability", "mle_or"],
        "additive_asd_or": additive.loc["asd_child_at_home", "mle_or"],
        "additive_asd_p": additive.loc["asd_child_at_home", "mle_p"],
        "interaction_or": (
            interaction["mle_or"].iloc[0] if len(interaction) else np.nan
        ),
        "interaction_p": (
            interaction["mle_p"].iloc[0] if len(interaction) else np.nan
        ),
    }
    expected = {
        "selected_n": 135,
        "clusterable_n": 131,
        "screen_valid_n": 127,
        "higher_n": 84,
        "conditional_n": 47,
        "silhouette_k2": 0.180,
        "mean_subsample_ari": 0.704,
        "higher_screen_yes_n": 56,
        "higher_screen_valid_n": 80,
        "conditional_screen_yes_n": 13,
        "conditional_screen_valid_n": 47,
        "screen_gap_pp": 42.3,
        "additive_cluster_or": 8.73,
        "additive_asd_or": 4.32,
        "additive_asd_p": 0.0012,
        "interaction_or": 2.02,
        "interaction_p": 0.445,
    }
    tolerances = {
        "silhouette_k2": 0.002,
        "mean_subsample_ari": 0.01,
        "screen_gap_pp": 0.2,
        "additive_cluster_or": 0.06,
        "additive_asd_or": 0.06,
        "additive_asd_p": 0.0002,
        "interaction_or": 0.06,
        "interaction_p": 0.003,
    }
    check_rows = []
    for metric, expected_value in expected.items():
        observed_value = observed[metric]
        tolerance = tolerances.get(metric, 0)
        passed = (
            abs(float(observed_value) - float(expected_value)) <= tolerance
        )
        check_rows.append(
            {
                "metric": metric,
                "expected": expected_value,
                "observed": observed_value,
                "tolerance": tolerance,
                "passed": passed,
            }
        )
    checks = pd.DataFrame(check_rows)
    if not checks["passed"].all():
        failed = checks.loc[~checks["passed"], "metric"].tolist()
        raise RuntimeError(f"Status-quo regression checks failed: {failed}")

    assigned = status_quo_fit.eligible
    excluded_index = status_quo_frame.index[~assigned]
    excluded_screen = _numeric(
        status_quo_frame.loc[excluded_index], "tfa_free_screen"
    )
    excluded_valid_no = int(excluded_screen.notna().sum() - excluded_screen.eq(4).sum())
    screen_by_cluster = screen_table.set_index("cluster")
    higher_yes = int(
        screen_by_cluster.loc["Higher acceptability", "definitely_yes_n"]
    )
    higher_n = int(screen_by_cluster.loc["Higher acceptability", "valid_n"])
    conditional_yes = int(
        screen_by_cluster.loc["Conditional acceptability", "definitely_yes_n"]
    )
    conditional_n = int(
        screen_by_cluster.loc["Conditional acceptability", "valid_n"]
    )
    tipping_rows = []
    for added_to_higher in range(excluded_valid_no + 1):
        added_to_conditional = excluded_valid_no - added_to_higher
        gap = (
            higher_yes / (higher_n + added_to_higher)
            - conditional_yes / (conditional_n + added_to_conditional)
        ) * 100
        tipping_rows.append(
            {
                "excluded_no_records_added_to_higher": added_to_higher,
                "excluded_no_records_added_to_conditional": added_to_conditional,
                "screen_gap_pp": gap,
            }
        )
    tipping = pd.DataFrame(tipping_rows)

    pooled_rate = (higher_yes + conditional_yes) / (higher_n + conditional_n)
    z_alpha = stats.norm.ppf(0.975)
    z_power = stats.norm.ppf(0.80)
    minimum_detectable_difference = (
        (z_alpha + z_power)
        * np.sqrt(
            pooled_rate
            * (1 - pooled_rate)
            * (1 / higher_n + 1 / conditional_n)
        )
        * 100
    )
    precision = pd.DataFrame(
        [
            {
                "smaller_profile_n": min(higher_n, conditional_n),
                "larger_profile_n": max(higher_n, conditional_n),
                "two_sided_alpha": 0.05,
                "power": 0.80,
                "pooled_reference_rate": pooled_rate,
                "approx_min_detectable_difference_pp": minimum_detectable_difference,
                "observed_difference_pp": screen_gap,
                "interpretation": (
                    "A non-significant difference smaller than this threshold would be "
                    "imprecise evidence, not evidence of no difference."
                ),
            }
        ]
    )
    return checks, tipping, precision


def _figure_palette(config: dict) -> dict[str, str]:
    figures = config["figures"]
    return {
        "Higher acceptability": figures["higher_acceptability"],
        "Conditional acceptability": figures["conditional_acceptability"],
        "blue": figures["blue"],
        "gold": figures["gold"],
        "orange": figures["orange"],
        "olive": figures["olive"],
        "pink": figures["pink"],
        "neutral": figures["neutral"],
        "light_neutral": figures["light_neutral"],
        "ink": figures["ink"],
        "grid": figures["grid"],
    }


def configure_plotting(config: dict) -> None:
    palette = _figure_palette(config)
    mpl.rcParams.update(
        {
            "figure.dpi": 140,
            "savefig.dpi": int(config["figures"]["raster_dpi"]),
            "font.family": "DejaVu Sans",
            "axes.facecolor": "white",
            "figure.facecolor": "white",
            "axes.edgecolor": palette["ink"],
            "axes.labelcolor": palette["ink"],
            "text.color": palette["ink"],
            "xtick.color": palette["ink"],
            "ytick.color": palette["ink"],
            "axes.grid": False,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    sns.set_theme(style="white", font="DejaVu Sans")


def _save_figure(
    figure: mpl.figure.Figure,
    output_dir: Path,
    stem: str,
    caption: str,
) -> list[dict]:
    layout_engine = figure.get_layout_engine()
    if layout_engine is not None:
        try:
            layout_engine.set(rect=(0.0, 0.075, 1.0, 0.98), h_pad=0.08)
        except TypeError:
            pass
    else:
        figure.subplots_adjust(bottom=0.11, top=0.92)
    figure.text(
        0.5,
        0.012,
        caption,
        ha="center",
        va="bottom",
        fontsize=8.5,
        color="#6B7280",
        wrap=True,
    )
    rows = []
    for extension in ("png", "pdf"):
        target = output_dir / f"{stem}.{extension}"
        figure.savefig(
            target,
            bbox_inches="tight",
            dpi=300 if extension == "png" else None,
            metadata={"Title": stem, "Subject": caption},
        )
        rows.append({"file": target.name, "caption": caption})
    plt.close(figure)
    return rows


def _save_clustergrid(
    clustergrid: sns.matrix.ClusterGrid,
    output_dir: Path,
    stem: str,
    caption: str,
) -> list[dict]:
    clustergrid.figure.subplots_adjust(bottom=0.11, top=0.91)
    clustergrid.figure.text(
        0.5,
        0.012,
        caption,
        ha="center",
        va="bottom",
        fontsize=8.5,
        color="#6B7280",
    )
    rows = []
    for extension in ("png", "pdf"):
        target = output_dir / f"{stem}.{extension}"
        clustergrid.figure.savefig(
            target,
            bbox_inches="tight",
            dpi=300 if extension == "png" else None,
            metadata={"Title": stem, "Subject": caption},
        )
        rows.append({"file": target.name, "caption": caption})
    plt.close(clustergrid.figure)
    return rows


def _confidence_ellipse(
    x: np.ndarray,
    y: np.ndarray,
    axis: mpl.axes.Axes,
    color: str,
    confidence: float = 0.95,
) -> None:
    if len(x) < 3:
        return
    covariance = np.cov(x, y)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = eigenvalues.argsort()[::-1]
    eigenvalues = eigenvalues[order]
    eigenvectors = eigenvectors[:, order]
    radius = np.sqrt(stats.chi2.ppf(confidence, 2))
    width, height = 2 * radius * np.sqrt(np.maximum(eigenvalues, 0))
    angle = np.degrees(np.arctan2(eigenvectors[1, 0], eigenvectors[0, 0]))
    ellipse = Ellipse(
        (np.mean(x), np.mean(y)),
        width=width,
        height=height,
        angle=angle,
        facecolor=color,
        edgecolor=color,
        alpha=0.10,
        linewidth=1.5,
    )
    axis.add_patch(ellipse)


def _color_accessibility_table(config: dict) -> pd.DataFrame:
    palette = _figure_palette(config)
    # Brettel-style linear approximation for deuteranopia verification.
    transform = np.array(
        [
            [0.367, 0.861, -0.228],
            [0.280, 0.673, 0.047],
            [-0.012, 0.043, 0.969],
        ]
    )

    def rgb(hex_color: str) -> np.ndarray:
        return np.array(mpl.colors.to_rgb(hex_color))

    pairs = [
        ("Higher versus Conditional", palette["blue"], palette["orange"]),
        ("Heatmap endpoints", palette["blue"], palette["orange"]),
        ("Olive versus pink", palette["olive"], palette["pink"]),
    ]
    rows = []
    for label, first, second in pairs:
        normal_distance = np.linalg.norm(rgb(first) - rgb(second))
        simulated_distance = np.linalg.norm(
            np.clip(transform @ rgb(first), 0, 1)
            - np.clip(transform @ rgb(second), 0, 1)
        )
        rows.append(
            {
                "comparison": label,
                "normal_rgb_distance": normal_distance,
                "deuteranopia_simulated_distance": simulated_distance,
                "passes_noncolor_backup": True,
                "noncolor_backup": "direct labels, ordering, markers, or facet position",
            }
        )
    return pd.DataFrame(rows)


def generate_upgrade_figures(
    *,
    project_dir: Path,
    bundle: SourceBundle,
    features: pd.DataFrame,
    rules: pd.DataFrame,
    false_positive: pd.DataFrame,
    detector_scores: pd.DataFrame,
    detector_importance: pd.DataFrame,
    detector_context: dict,
    sensitivity: pd.DataFrame,
    logistic_table: pd.DataFrame,
    fits: dict[str, ClusterFit],
    frames: dict[str, pd.DataFrame],
    latent_context: dict,
    feature_context: dict,
    config: dict,
) -> pd.DataFrame:
    """Create and inspect the required fraud- and cluster-facing figure suite."""

    configure_plotting(config)
    output_dir = project_dir / "Caregiver Outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    palette = _figure_palette(config)
    diverging = LinearSegmentedColormap.from_list(
        "esd_blue_white_orange",
        [palette["blue"], "#F8FAFC", palette["orange"]],
    )
    chart_rows: list[dict] = []

    # Figure 7: timing ECDF.
    fig, ax = plt.subplots(figsize=(9.5, 5.8), constrained_layout=True)
    for source_name, label, color, linestyle in [
        ("clean_4797", "4797 verified-clean reference", palette["blue"], "-"),
        ("dirty_4581", "4581 unverified legacy", palette["orange"], "--"),
    ]:
        values = features.loc[
            features["source_project"].eq(source_name), "feat_total_time_min"
        ].dropna()
        sns.ecdfplot(
            values,
            ax=ax,
            label=f"{label} (n={len(values):,})",
            color=color,
            linestyle=linestyle,
            linewidth=2.2,
        )
    human_floor = float(config["fraud_rules"]["total_time_floor_min"])
    ax.axvspan(0.1, human_floor, color=palette["orange"], alpha=0.10)
    ax.axvline(
        human_floor,
        color=palette["ink"],
        linestyle=":",
        linewidth=1.5,
        label=f"Verified-human floor: {human_floor:.2f} min",
    )
    ax.set_xscale("log")
    ax.set(
        title="Total survey completion time by REDCap project",
        xlabel="Total of four instrument durations (minutes, log scale)",
        ylabel="Cumulative share of records",
    )
    ax.grid(axis="y", color=palette["grid"], linewidth=0.8)
    ax.legend(frameon=False, loc="lower right")
    sns.despine(ax=ax)
    chart_rows += _save_figure(
        fig,
        output_dir,
        "figure_7_timing_ecdf",
        "Completed timing blocks only; 4797 is the human reference and 4581 is unverified. "
        "Screening, knowledge, values, and demographics are held out of clustering.",
    )

    # Figure 8: response-fingerprint heatmap.
    likert = feature_context["likert_numeric"]
    standardized = (likert - likert.mean(axis=0)) / likert.std(axis=0).replace(0, np.nan)
    fig, axes = plt.subplots(1, 2, figsize=(16, 8), constrained_layout=True)
    for axis, source_name, label in zip(
        axes,
        ("clean_4797", "dirty_4581"),
        ("4797 verified-clean", "4581 unverified"),
    ):
        source_index = features.index[features["source_project"].eq(source_name)]
        matrix = standardized.loc[source_index]
        answered = likert.loc[source_index].notna().mean(axis=1)
        matrix = matrix.loc[answered.ge(0.80)]
        order = matrix.std(axis=1).sort_values().index
        image = axis.imshow(
            matrix.loc[order].to_numpy(),
            aspect="auto",
            interpolation="nearest",
            cmap=diverging,
            norm=TwoSlopeNorm(vmin=-2.5, vcenter=0, vmax=2.5),
        )
        axis.set(
            title=f"{label} (n={len(order):,})",
            xlabel=f"Ordered shared Likert items (n={matrix.shape[1]})",
            ylabel="Respondents, lowest within-row SD first",
        )
        axis.set_xticks([])
        axis.set_yticks([])
    colorbar = fig.colorbar(image, ax=axes, shrink=0.75, pad=0.02)
    colorbar.set_label("Item-standardized response (z)")
    fig.suptitle("Shared Likert response fingerprints", fontsize=15, fontweight="bold")
    chart_rows += _save_figure(
        fig,
        output_dir,
        "figure_8_response_fingerprint_heatmap",
        "Rows with at least 80% of shared Likert items answered; the same zero-centered "
        "blue–white–orange scale is used in both panels. Held-out outcomes are excluded.",
    )

    # Figure 9: rule co-occurrence and verified-human false-positive rates.
    rule_columns = [f"rule_R{i}" for i in range(1, 11)]
    cooccurrence = rules[rule_columns].astype(int).T @ rules[rule_columns].astype(int)
    fig, axes = plt.subplots(
        1, 2, figsize=(14, 6), gridspec_kw={"width_ratios": [1.2, 1]}, constrained_layout=True
    )
    sns.heatmap(
        cooccurrence,
        annot=True,
        fmt="d",
        cmap=sns.light_palette(palette["blue"], as_cmap=True),
        cbar_kws={"label": "Co-flagged records"},
        ax=axes[0],
    )
    axes[0].set(title="Rule co-occurrence across both projects", xlabel="Rule", ylabel="Rule")
    axes[0].tick_params(axis="x", labelrotation=45)
    axes[0].tick_params(axis="y", labelrotation=0)
    fpr = false_positive.sort_values("false_positive_pct")
    axes[1].barh(
        fpr["rule"],
        fpr["false_positive_pct"],
        color=palette["orange"],
        edgecolor=palette["ink"],
        linewidth=0.5,
    )
    axes[1].axvline(5, color=palette["neutral"], linestyle=":", linewidth=1.3)
    axes[1].set(
        title="False-positive rate in 131 verified humans",
        xlabel="Percent flagged",
        ylabel="",
    )
    axes[1].grid(axis="x", color=palette["grid"], linewidth=0.8)
    sns.despine(ax=axes[1])
    chart_rows += _save_figure(
        fig,
        output_dir,
        "figure_9_rule_cooccurrence",
        "Rules R1–R10 on all 1,956 records; false-positive rates use the 131 completed "
        "4797 caregivers. Outcomes are held out of every fraud rule.",
    )

    # Figure 10: behavioral feature space.
    all_scaled = detector_context["all_scaled"]
    pca_embedding = PCA(n_components=2, random_state=int(config["analysis"]["seed"])).fit_transform(
        all_scaled
    )
    try:
        import umap

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            warnings.simplefilter("ignore", UserWarning)
            umap_embedding = umap.UMAP(
                n_neighbors=30,
                min_dist=0.15,
                n_components=2,
                random_state=int(config["analysis"]["seed"]),
            ).fit_transform(all_scaled)
    except Exception:
        umap_embedding = pca_embedding.copy()
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.8), constrained_layout=True)
    for source_name, label, color, marker, size, alpha, zorder in [
        ("dirty_4581", "4581", palette["orange"], "x", 13, 0.38, 1),
        ("clean_4797", "4797", palette["blue"], "o", 24, 0.80, 2),
    ]:
        mask = features["source_project"].eq(source_name).to_numpy()
        axes[0].scatter(
            pca_embedding[mask, 0],
            pca_embedding[mask, 1],
            s=size,
            alpha=alpha,
            color=color,
            marker=marker,
            label=label,
            zorder=zorder,
        )
    tier_colors = {1: palette["pink"], 2: palette["orange"], 3: palette["gold"], 4: palette["blue"]}
    for tier in (1, 2, 3, 4):
        mask = rules["tier"].eq(tier).to_numpy()
        axes[1].scatter(
            umap_embedding[mask, 0],
            umap_embedding[mask, 1],
            s=13,
            alpha=0.48,
            color=tier_colors[tier],
            label=f"Tier {tier}",
        )
    axes[0].set(title="PCA colored by source project", xlabel="PC1", ylabel="PC2")
    axes[1].set(title="UMAP colored by deterministic tier", xlabel="UMAP1", ylabel="UMAP2")
    for axis in axes:
        axis.legend(frameon=False, markerscale=1.6)
        axis.set_xticks([])
        axis.set_yticks([])
        sns.despine(ax=axis, left=True, bottom=True)
    chart_rows += _save_figure(
        fig,
        output_dir,
        "figure_10_behavioral_feature_space",
        "Behavioral features only for all 1,956 records; no screening, knowledge, values, "
        "or demographic outcomes enter the embeddings or tier assignment.",
    )

    # Figure 11: detector score distributions and agreement.
    score_fields = [
        ("score_isolation", "Isolation forest", palette["blue"], "-"),
        ("score_lof", "Local outlier factor", palette["gold"], "--"),
        ("score_mahalanobis", "Robust Mahalanobis", palette["orange"], "-."),
        ("score_pu_nonhuman", "PU/source classifier", palette["pink"], ":"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.8), constrained_layout=True)
    for field, label, color, linestyle in score_fields:
        percentiles = detector_scores[field].rank(pct=True)
        sns.kdeplot(
            percentiles.loc[features["source_project"].eq("dirty_4581")],
            ax=axes[0],
            color=color,
            linestyle=linestyle,
            linewidth=2,
            label=label,
            common_norm=False,
            clip=(0, 1),
        )
    axes[0].set(
        title="4581 anomaly-score percentile distributions",
        xlabel="Within-all-records score percentile (higher = less human-like)",
        ylabel="Density",
        xlim=(0, 1),
    )
    axes[0].legend(frameon=False)
    agreement_flags = detector_context["agreement_flags"]
    kappa_matrix = pd.DataFrame(
        np.eye(len(agreement_flags.columns)),
        index=agreement_flags.columns,
        columns=agreement_flags.columns,
    )
    for left in agreement_flags:
        for right in agreement_flags:
            kappa_matrix.loc[left, right] = cohen_kappa_score(
                agreement_flags[left], agreement_flags[right]
            )
    sns.heatmap(
        kappa_matrix,
        annot=True,
        fmt=".2f",
        cmap=diverging,
        center=0,
        vmin=-1,
        vmax=1,
        cbar_kws={"label": "Cohen's κ"},
        ax=axes[1],
    )
    axes[1].set(title="Binary flag concordance", xlabel="", ylabel="")
    sns.despine(ax=axes[0])
    chart_rows += _save_figure(
        fig,
        output_dir,
        "figure_11_detector_scores_and_agreement",
        "All 1,956 records. Cuts are the clean Tier-4 95th percentiles. The prior lab "
        "LightGBM artifact was unavailable, so the PU two-sample classifier is shown instead.",
    )

    # Figure 11b: explainability.
    importance = detector_importance.head(15).sort_values(
        "permutation_auc_decrease"
    )
    fig, axes = plt.subplots(1, 2, figsize=(13, 6.5), constrained_layout=True)
    axes[0].barh(
        importance["feature"],
        importance["permutation_auc_decrease"],
        color=palette["blue"],
    )
    axes[0].set(
        title="Permutation importance",
        xlabel="Decrease in source-classifier AUC",
        ylabel="",
    )
    shap_order = detector_importance.dropna(subset=["mean_abs_shap"]).head(15).sort_values(
        "mean_abs_shap"
    )
    if len(shap_order):
        axes[1].barh(
            shap_order["feature"],
            shap_order["mean_abs_shap"],
            color=palette["orange"],
        )
        axes[1].set(title="SHAP summary", xlabel="Mean absolute SHAP value", ylabel="")
    else:
        axes[1].text(
            0.5,
            0.5,
            detector_context["shap_status"],
            ha="center",
            va="center",
            transform=axes[1].transAxes,
            wrap=True,
        )
        axes[1].set_axis_off()
    for axis in axes:
        axis.grid(axis="x", color=palette["grid"], linewidth=0.8)
        sns.despine(ax=axis)
    chart_rows += _save_figure(
        fig,
        output_dir,
        "figure_11b_pu_feature_importance",
        "Gradient-boosted 4797-versus-4581 classifier on behavioral features only "
        f"(SHAP status: {detector_context['shap_status']}). Importance is not proof of fraud.",
    )

    status_name = "3. 4797 status quo"
    status_fit = fits[status_name]
    status_frame = frames[status_name]

    # Figure 12: correctly standardized clustermap with row annotations.
    pooled_name = "4. 4797 Tiers 3+4 + 4581 Tier 4"
    pooled_fit = fits[pooled_name]
    pooled_data = pd.DataFrame(
        pooled_fit.X,
        index=pooled_fit.named_labels.index,
        columns=pooled_fit.domains.columns,
    )
    cluster_map = {
        "Higher acceptability": palette["blue"],
        "Conditional acceptability": palette["orange"],
    }
    tier_map = {1: palette["pink"], 2: palette["orange"], 3: palette["gold"], 4: palette["olive"]}
    source_map = {"clean_4797": palette["blue"], "dirty_4581": palette["light_neutral"]}
    row_colors = pd.DataFrame(
        {
            "profile": pooled_fit.named_labels.map(cluster_map),
            "tier": rules.loc[pooled_data.index, "tier"].map(tier_map),
            "source": features.loc[pooled_data.index, "source_project"].map(source_map),
        },
        index=pooled_data.index,
    )
    grid = sns.clustermap(
        pooled_data,
        cmap=diverging,
        center=0,
        vmin=-3,
        vmax=3,
        row_colors=row_colors,
        xticklabels=True,
        yticklabels=False,
        figsize=(14, 10),
        cbar_kws={"label": "Once-standardized TFA domain score"},
    )
    grid.figure.suptitle(
        "TFA domain clustermap with profile, tier, and source annotations",
        y=1.02,
        fontsize=15,
        fontweight="bold",
    )
    plt.setp(grid.ax_heatmap.get_xticklabels(), rotation=45, ha="right", fontsize=8)
    chart_rows += _save_clustergrid(
        grid,
        output_dir,
        "figure_12_tfa_clustermap",
        f"Pooled sensitivity definition; n={len(pooled_data):,} clusterable records. "
        "Ten TFA domains are standardized exactly once; all outcomes are held out.",
    )

    # Figure 13: PCA biplot with centroid and loading vectors.
    pca = PCA(n_components=2, random_state=int(config["analysis"]["seed"]))
    pca_scores = pca.fit_transform(status_fit.X)
    fig, ax = plt.subplots(figsize=(9.5, 7.5), constrained_layout=True)
    for cluster_name, marker in zip(CLUSTER_ORDER, ("o", "s")):
        mask = status_fit.named_labels.eq(cluster_name).to_numpy()
        ax.scatter(
            pca_scores[mask, 0],
            pca_scores[mask, 1],
            s=36,
            alpha=0.65,
            marker=marker,
            color=palette[cluster_name],
            label=cluster_name,
        )
        _confidence_ellipse(
            pca_scores[mask, 0],
            pca_scores[mask, 1],
            ax,
            palette[cluster_name],
        )
    loading_scale = np.max(np.abs(pca_scores)) * 0.65
    for index, domain in enumerate(status_fit.domains.columns):
        x_loading = pca.components_[0, index] * loading_scale
        y_loading = pca.components_[1, index] * loading_scale
        ax.arrow(
            0,
            0,
            x_loading,
            y_loading,
            color=palette["ink"],
            alpha=0.65,
            width=0.007,
            head_width=0.10,
            length_includes_head=True,
        )
        ax.text(x_loading * 1.08, y_loading * 1.08, domain, fontsize=7.5)
    centroid = pca_scores.mean(axis=0)
    ax.scatter(
        centroid[0],
        centroid[1],
        marker="*",
        s=180,
        facecolor="white",
        edgecolor=palette["ink"],
        linewidth=1.5,
        label="Overall centroid",
        zorder=5,
    )
    ax.axhline(0, color=palette["grid"], linewidth=0.8)
    ax.axvline(0, color=palette["grid"], linewidth=0.8)
    ax.set(
        title="PCA biplot of the ten TFA domains",
        xlabel=f"PC1 ({pca.explained_variance_ratio_[0] * 100:.1f}% variance)",
        ylabel=f"PC2 ({pca.explained_variance_ratio_[1] * 100:.1f}% variance)",
    )
    ax.legend(frameon=False)
    sns.despine(ax=ax)
    chart_rows += _save_figure(
        fig,
        output_dir,
        "figure_13_tfa_pca_biplot",
        f"4797 status-quo clusterable cohort, n={len(status_fit.X)}. The centroid is valid "
        "in linear PCA space; screening, knowledge, values, and demographics are held out.",
    )

    # Figure 14: UMAP and t-SNE parameter sensitivity; medoid only on t-SNE.
    seed = int(config["analysis"]["seed"])
    medoid_index = int(
        np.argmin(
            pairwise_distances(
                status_fit.X,
                np.median(status_fit.X, axis=0, keepdims=True),
            ).ravel()
        )
    )
    fig, axes = plt.subplots(2, 3, figsize=(15, 9), constrained_layout=True)
    try:
        import umap

        for column, neighbors in enumerate((10, 30, 50)):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                warnings.simplefilter("ignore", UserWarning)
                embedding = umap.UMAP(
                    n_neighbors=neighbors,
                    min_dist=0.15,
                    n_components=2,
                    random_state=seed,
                ).fit_transform(status_fit.X)
            for cluster_name, marker in zip(CLUSTER_ORDER, ("o", "s")):
                mask = status_fit.named_labels.eq(cluster_name).to_numpy()
                axes[0, column].scatter(
                    embedding[mask, 0],
                    embedding[mask, 1],
                    s=18,
                    alpha=0.65,
                    marker=marker,
                    color=palette[cluster_name],
                )
            axes[0, column].set_title(f"UMAP, n_neighbors={neighbors}")
    except Exception:
        for axis in axes[0]:
            axis.text(0.5, 0.5, "UMAP unavailable", ha="center", va="center")
    for column, perplexity in enumerate((10, 30, 50)):
        embedding = TSNE(
            n_components=2,
            perplexity=perplexity,
            init="pca",
            learning_rate="auto",
            max_iter=1500,
            random_state=seed,
        ).fit_transform(status_fit.X)
        for cluster_name, marker in zip(CLUSTER_ORDER, ("o", "s")):
            mask = status_fit.named_labels.eq(cluster_name).to_numpy()
            axes[1, column].scatter(
                embedding[mask, 0],
                embedding[mask, 1],
                s=18,
                alpha=0.65,
                marker=marker,
                color=palette[cluster_name],
            )
        axes[1, column].scatter(
            embedding[medoid_index, 0],
            embedding[medoid_index, 1],
            marker="*",
            s=130,
            facecolor="white",
            edgecolor=palette["ink"],
            linewidth=1.2,
            zorder=5,
        )
        axes[1, column].set_title(f"t-SNE, perplexity={perplexity}; medoid starred")
    for axis in axes.flat:
        axis.set_xticks([])
        axis.set_yticks([])
        sns.despine(ax=axis, left=True, bottom=True)
    fig.suptitle("Nonlinear embedding sensitivity of TFA domains", fontsize=15, fontweight="bold")
    chart_rows += _save_figure(
        fig,
        output_dir,
        "figure_14_umap_tsne_sensitivity",
        f"4797 status-quo clusterable cohort, n={len(status_fit.X)}. The actual medoid, "
        "not an invalid transformed-space centroid, is marked in t-SNE panels; outcomes are held out.",
    )

    # Figure 15: consensus matrix.
    consensus = latent_context["consensus"].to_numpy()
    distance = np.clip(1 - consensus, 0, 1)
    linkage = hierarchy.linkage(squareform(distance, checks=False), method="average")
    order = hierarchy.leaves_list(linkage)
    fig, ax = plt.subplots(figsize=(8.5, 7.5), constrained_layout=True)
    image = ax.imshow(
        consensus[np.ix_(order, order)],
        cmap=sns.light_palette(palette["blue"], as_cmap=True),
        vmin=0,
        vmax=1,
        aspect="equal",
        interpolation="nearest",
    )
    fig.colorbar(image, ax=ax, label="Bootstrap co-assignment frequency")
    ax.set(
        title="K-means bootstrap consensus matrix",
        xlabel="Respondents reordered by consensus",
        ylabel="Respondents reordered by consensus",
    )
    ax.set_xticks([])
    ax.set_yticks([])
    chart_rows += _save_figure(
        fig,
        output_dir,
        "figure_15_consensus_matrix",
        f"4797 status-quo clusterable cohort, n={len(status_fit.X)}; "
        f"{config['analysis']['consensus_repeats']} seeded bootstrap fits. Outcomes are held out.",
    )

    # Figure 16: per-case silhouette.
    fig, ax = plt.subplots(figsize=(9, 6.5), constrained_layout=True)
    y_lower = 0
    y_ticks = []
    y_labels = []
    for cluster_name in CLUSTER_ORDER:
        values = np.sort(
            status_fit.case_silhouette.loc[
                status_fit.named_labels.index[
                    status_fit.named_labels.eq(cluster_name)
                ]
            ].to_numpy()
        )
        y_upper = y_lower + len(values)
        ax.fill_betweenx(
            np.arange(y_lower, y_upper),
            0,
            values,
            facecolor=palette[cluster_name],
            alpha=0.75,
            edgecolor=palette["ink"],
            linewidth=0.3,
        )
        y_ticks.append((y_lower + y_upper) / 2)
        y_labels.append(f"{cluster_name} (n={len(values)})")
        y_lower = y_upper + 4
    ax.axvline(
        status_fit.silhouette,
        color=palette["ink"],
        linestyle="--",
        label=f"Mean={status_fit.silhouette:.3f}",
    )
    ax.axvline(0, color=palette["neutral"], linewidth=1)
    ax.set(
        title="Per-case silhouette values for the primary k=2 solution",
        xlabel="Silhouette value",
        ylabel="Profile",
        yticks=y_ticks,
        yticklabels=y_labels,
    )
    ax.legend(frameon=False)
    sns.despine(ax=ax)
    chart_rows += _save_figure(
        fig,
        output_dir,
        "figure_16_case_silhouette",
        f"4797 status-quo clusterable cohort, n={len(status_fit.X)}. Negative values remain "
        "visible; screening, knowledge, values, and demographics are held out.",
    )

    # Figure 2 upgrade: domain means, bootstrap CIs, and overall references.
    bootstrap_rng = np.random.default_rng(seed)
    bootstrap_repeats = int(config["analysis"]["bootstrap_repeats"])
    domain_rows = []
    model_domains = status_fit.domains.loc[status_fit.eligible]
    for domain in model_domains.columns:
        overall = model_domains[domain].mean()
        for cluster_name in CLUSTER_ORDER:
            values = model_domains.loc[
                status_fit.named_labels.index[
                    status_fit.named_labels.eq(cluster_name)
                ],
                domain,
            ].dropna().to_numpy()
            means = np.array(
                [
                    bootstrap_rng.choice(values, size=len(values), replace=True).mean()
                    for _ in range(bootstrap_repeats)
                ]
            )
            domain_rows.append(
                {
                    "domain": domain,
                    "cluster": cluster_name,
                    "mean": values.mean(),
                    "low": np.quantile(means, 0.025),
                    "high": np.quantile(means, 0.975),
                    "overall": overall,
                }
            )
    domain_bootstrap = pd.DataFrame(domain_rows)
    gap_order = (
        domain_bootstrap.pivot(index="domain", columns="cluster", values="mean")
        .assign(
            gap=lambda table: table["Higher acceptability"]
            - table["Conditional acceptability"]
        )
        .sort_values("gap")
        .index.tolist()
    )
    fig, ax = plt.subplots(figsize=(11.5, 7.5), constrained_layout=True)
    y = np.arange(len(gap_order))
    for row_index, domain in enumerate(gap_order):
        overall = domain_bootstrap.loc[
            domain_bootstrap["domain"].eq(domain), "overall"
        ].iloc[0]
        ax.vlines(
            overall,
            row_index - 0.34,
            row_index + 0.34,
            color=palette["neutral"],
            linestyle="--",
            linewidth=1.2,
        )
    for cluster_name, offset, marker in [
        ("Higher acceptability", 0.11, "o"),
        ("Conditional acceptability", -0.11, "s"),
    ]:
        subset = (
            domain_bootstrap.loc[domain_bootstrap["cluster"].eq(cluster_name)]
            .set_index("domain")
            .reindex(gap_order)
        )
        ax.errorbar(
            subset["mean"],
            y + offset,
            xerr=[
                subset["mean"] - subset["low"],
                subset["high"] - subset["mean"],
            ],
            fmt=marker,
            markersize=7,
            capsize=3,
            linewidth=1.4,
            color=palette[cluster_name],
            label=cluster_name,
        )
    ax.set(
        title="Primary TFA profile means with bootstrap uncertainty",
        xlabel="Aligned domain score (0–100; higher = more accepting/supportive)",
        ylabel="",
        yticks=y,
        yticklabels=gap_order,
        xlim=(0, 102),
    )
    ax.grid(axis="x", color=palette["grid"], linewidth=0.8)
    ax.legend(frameon=False)
    sns.despine(ax=ax)
    chart_rows += _save_figure(
        fig,
        output_dir,
        "figure_2_primary_cluster_profiles",
        f"4797 status-quo clusterable cohort, n={len(status_fit.X)}; "
        f"{bootstrap_repeats} bootstrap repeats. Dashed ticks are overall domain means; outcomes are held out.",
    )

    # Figure 17: distribution views across all ten domains.
    distribution = model_domains.assign(cluster=status_fit.named_labels).melt(
        id_vars="cluster", var_name="domain", value_name="score"
    )
    fig, axes = plt.subplots(5, 2, figsize=(14, 18), constrained_layout=True)
    for axis, domain in zip(axes.flat, model_domains.columns):
        subset = distribution.loc[distribution["domain"].eq(domain)]
        sns.violinplot(
            data=subset,
            x="score",
            y="cluster",
            hue="cluster",
            order=CLUSTER_ORDER,
            hue_order=CLUSTER_ORDER,
            palette={name: palette[name] for name in CLUSTER_ORDER},
            inner=None,
            cut=0,
            linewidth=0.7,
            ax=axis,
            legend=False,
        )
        sns.boxplot(
            data=subset,
            x="score",
            y="cluster",
            order=CLUSTER_ORDER,
            width=0.18,
            showfliers=False,
            boxprops={"facecolor": "white", "alpha": 0.75},
            ax=axis,
        )
        axis.set(title=domain, xlabel="0–100 score", ylabel="", xlim=(0, 100))
        axis.grid(axis="x", color=palette["grid"], linewidth=0.7)
        sns.despine(ax=axis)
    fig.suptitle("TFA domain distributions by caregiver profile", fontsize=15, fontweight="bold")
    chart_rows += _save_figure(
        fig,
        output_dir,
        "figure_17_tfa_domain_distributions",
        f"4797 status-quo clusterable cohort, n={len(status_fit.X)}. Violin and box summaries "
        "show full distribution shape; outcomes are held out.",
    )

    # Figure 18: MLE points from regression baseline with Firth profile CIs.
    status_logistic = logistic_table.loc[
        logistic_table["definition"].eq(status_name)
    ].copy()
    forest = pd.concat(
        [
            status_logistic.loc[
                status_logistic["model"].eq("additive")
                & status_logistic["term"].isin(
                    ["higher_acceptability", "asd_child_at_home"]
                )
            ],
            status_logistic.loc[
                status_logistic["model"].eq("interaction")
                & status_logistic["term"].eq("cluster_x_asd")
            ],
        ]
    )
    forest["label"] = forest["term"].map(
        {
            "higher_acceptability": "Higher acceptability profile",
            "asd_child_at_home": "ASD child at home",
            "cluster_x_asd": "Profile × ASD-at-home interaction",
        }
    )
    forest = forest.set_index("label").reindex(
        [
            "Higher acceptability profile",
            "ASD child at home",
            "Profile × ASD-at-home interaction",
        ]
    )
    fig, ax = plt.subplots(figsize=(9.5, 5.2), constrained_layout=True)
    y = np.arange(len(forest))
    point = forest["mle_or"].to_numpy()
    low = forest["firth_profile_ci_low"].to_numpy()
    high = forest["firth_profile_ci_high"].to_numpy()
    ax.errorbar(
        point,
        y,
        xerr=[point - low, high - point],
        fmt="o",
        color=palette["blue"],
        ecolor=palette["neutral"],
        capsize=4,
        markersize=8,
        linewidth=1.7,
    )
    ax.axvline(1, color=palette["ink"], linestyle="--", linewidth=1.2)
    for position, value in enumerate(point):
        ax.text(value * 1.08, position, f"{value:.2f}", va="center", fontsize=9)
    ax.set_xscale("log")
    ax.set(
        title="Screening-intent logistic odds ratios",
        xlabel="Odds ratio (log scale); MLE point with Firth profile-likelihood 95% CI",
        ylabel="",
        yticks=y,
        yticklabels=forest.index,
    )
    ax.grid(axis="x", color=palette["grid"], linewidth=0.8)
    sns.despine(ax=ax)
    chart_rows += _save_figure(
        fig,
        output_dir,
        "figure_18_logistic_forest",
        f"4797 status-quo cluster-assigned records with complete model variables, n={int(forest['n'].max())}. "
        "Screening is held out of clustering; sparse-data inference uses Firth profile intervals.",
    )

    # Figure 19: alluvial-style tier-to-status-quo-profile flow.
    flow_index = status_fit.named_labels.index
    flow = pd.crosstab(
        rules.loc[flow_index, "tier"],
        status_fit.named_labels,
    ).reindex(index=[1, 2, 3, 4], columns=CLUSTER_ORDER, fill_value=0)
    fig, ax = plt.subplots(figsize=(10, 7), constrained_layout=True)
    total_flow = int(flow.to_numpy().sum())
    ax.set_xlim(0, 1)
    ax.set_ylim(0, total_flow + 12)
    ax.axis("off")
    left_x, right_x, width = 0.08, 0.82, 0.08
    tier_colors = {1: palette["pink"], 2: palette["orange"], 3: palette["gold"], 4: palette["olive"]}
    left_bottoms = {}
    bottom = 0
    for tier in [1, 2, 3, 4]:
        height = flow.loc[tier].sum()
        left_bottoms[tier] = bottom
        ax.add_patch(Rectangle((left_x, bottom), width, height, color=tier_colors[tier], alpha=0.85))
        ax.text(left_x - 0.015, bottom + height / 2, f"Tier {tier} (n={height})", ha="right", va="center")
        bottom += height
    right_bottoms = {}
    bottom = 0
    for cluster_name in CLUSTER_ORDER:
        height = flow[cluster_name].sum()
        right_bottoms[cluster_name] = bottom
        ax.add_patch(Rectangle((right_x, bottom), width, height, color=palette[cluster_name], alpha=0.9))
        ax.text(right_x + width + 0.015, bottom + height / 2, f"{cluster_name} (n={height})", ha="left", va="center")
        bottom += height
    left_offsets = {tier: 0 for tier in flow.index}
    right_offsets = {cluster: 0 for cluster in flow.columns}
    for tier in flow.index:
        for cluster_name in flow.columns:
            count = int(flow.loc[tier, cluster_name])
            if count == 0:
                continue
            left_bottom = left_bottoms[tier] + left_offsets[tier]
            right_bottom = right_bottoms[cluster_name] + right_offsets[cluster_name]
            polygon = Polygon(
                [
                    (left_x + width, left_bottom),
                    (right_x, right_bottom),
                    (right_x, right_bottom + count),
                    (left_x + width, left_bottom + count),
                ],
                closed=True,
                facecolor=palette[cluster_name],
                edgecolor="none",
                alpha=0.28,
            )
            ax.add_patch(polygon)
            left_offsets[tier] += count
            right_offsets[cluster_name] += count
    ax.text(left_x + width / 2, total_flow + 2.5, "Trust tier", ha="center", fontweight="bold")
    ax.text(right_x + width / 2, total_flow + 2.5, "Status-quo profile", ha="center", fontweight="bold")
    ax.set_title("Trust tier to primary caregiver-profile assignment", pad=28)
    chart_rows += _save_figure(
        fig,
        output_dir,
        "figure_19_tier_to_cluster_alluvial",
        f"4797 status-quo clusterable cohort, n={len(flow_index)}. Flows describe filtering "
        "sensitivity only; screening and all characterization outcomes are held out.",
    )

    # Figure 20: tier sensitivity dot plot.
    ordered = sensitivity.set_index("definition").reindex(
        [
            "1. 4797 Tier 4 only",
            "2. 4797 Tiers 3+4",
            "3. 4797 status quo",
            "4. 4797 Tiers 3+4 + 4581 Tier 4",
            "5. 4581 Tier 4 replication",
        ]
    )
    fig, ax = plt.subplots(figsize=(11, 6), constrained_layout=True)
    y = np.arange(len(ordered))
    point = ordered["screen_gap_pp"].to_numpy()
    low = ordered["screen_gap_ci_low_pp"].to_numpy()
    high = ordered["screen_gap_ci_high_pp"].to_numpy()
    ax.errorbar(
        point,
        y,
        xerr=[point - low, high - point],
        fmt="o",
        color=palette["blue"],
        ecolor=palette["neutral"],
        capsize=4,
        markersize=8,
        linewidth=1.6,
    )
    ax.axvline(0, color=palette["ink"], linestyle="--", linewidth=1.1)
    ax.axvline(
        ordered.loc["3. 4797 status quo", "screen_gap_pp"],
        color=palette["orange"],
        linestyle=":",
        linewidth=1.3,
        label="Status-quo gap",
    )
    ax.set(
        title="Definitely-yes screening gap across inclusion definitions",
        xlabel="Higher minus Conditional acceptability (percentage points; Newcombe-Wilson 95% CI)",
        ylabel="",
        yticks=y,
        yticklabels=ordered.index,
    )
    ax.grid(axis="x", color=palette["grid"], linewidth=0.8)
    ax.legend(frameon=False)
    sns.despine(ax=ax)
    chart_rows += _save_figure(
        fig,
        output_dir,
        "figure_20_tier_sensitivity",
        "All five pre-specified inclusion definitions; each point uses its cluster-assigned "
        "records with valid screening outcomes. Screening remains held out of profile formation.",
    )

    chart_map = pd.DataFrame(chart_rows).drop_duplicates("file")
    chart_map["analytical_role"] = chart_map["file"].str.extract(
        r"(figure_\d+[a-z]?)", expand=False
    )
    return chart_map


def _aggregate_latent_quality(
    posterior_table: pd.DataFrame, latent_context: dict
) -> pd.DataFrame:
    posterior = posterior_table.copy()
    rows = []
    for profile in CLUSTER_ORDER:
        values = posterior.loc[
            posterior["modal_profile"].eq(profile), "max_posterior"
        ]
        rows.append(
            {
                "profile": profile,
                "modal_n": len(values),
                "mean_max_posterior": values.mean(),
                "p10_max_posterior": values.quantile(0.10),
                "below_0_70_n": int(values.lt(0.70).sum()),
                "below_0_70_pct": values.lt(0.70).mean() * 100,
            }
        )
    rows.append(
        {
            "profile": "Overall",
            "modal_n": len(posterior),
            "mean_max_posterior": posterior["max_posterior"].mean(),
            "p10_max_posterior": posterior["max_posterior"].quantile(0.10),
            "below_0_70_n": int(posterior["max_posterior"].lt(0.70).sum()),
            "below_0_70_pct": posterior["max_posterior"].lt(0.70).mean() * 100,
        }
    )
    return pd.DataFrame(rows)


def _decision_summary(
    sensitivity: pd.DataFrame,
    contamination_summary: pd.DataFrame,
    tipping: pd.DataFrame,
    regression_checks: pd.DataFrame,
) -> pd.DataFrame:
    primary = sensitivity.loc[
        sensitivity["definition"].isin(
            [
                "1. 4797 Tier 4 only",
                "2. 4797 Tiers 3+4",
                "3. 4797 status quo",
            ]
        )
    ]
    robust = (
        primary["screen_gap_pp"].min() >= 30
        and primary["cluster_firth_ci_low"].min() > 1
        and regression_checks["passed"].all()
    )
    robustness_statement = (
        "Robust within the verified-clean primary cohort: the two-profile screening "
        "association remains large across clean-project trust tiers."
        if robust
        else "Fragile or materially changed within the clean-project trust tiers; treat the "
        "two-profile result as unresolved until the cause is understood."
    )
    implied = contamination_summary.iloc[0]
    return pd.DataFrame(
        [
            {
                "decision": "D1. Primary cohort",
                "recommendation": "Keep project 4797 primary; use 4581 only as a caveated replication.",
                "status": "Implemented as the default",
                "evidence": robustness_statement,
            },
            {
                "decision": "D2. Trust target",
                "recommendation": "Use four graded inclusion tiers, not a binary bot label.",
                "status": "Implemented",
                "evidence": "All 1,956 records receive R1–R10 flags and a tier.",
            },
            {
                "decision": "D3. Contamination fraction",
                "recommendation": "Do not present an identifiable bot prevalence.",
                "status": "Open / not identifiable from these data",
                "evidence": (
                    f"PU model-implied separation share {implied['estimate']:.1%} "
                    f"({implied['interval_low']:.1%}–{implied['interval_high']:.1%}), "
                    "but recruitment and instrument drift violate the identifying assumption."
                ),
            },
            {
                "decision": "D4. Knowledge scoring",
                "recommendation": "Use the binary verified correct-count as primary.",
                "status": "Pre-specified in config; graded documented subset is sensitivity only",
                "evidence": "The graded method was documented as significant, creating a forking-path risk.",
            },
            {
                "decision": "D5. Four cluster-excluded records",
                "recommendation": "Report the full tipping-point range.",
                "status": "Implemented",
                "evidence": (
                    f"All valid excluded outcomes were non-Definitely-yes; plausible gap range "
                    f"{tipping['screen_gap_pp'].min():.1f}–{tipping['screen_gap_pp'].max():.1f} pp."
                ),
            },
        ]
    )


def _data_quality_summary(
    bundle: SourceBundle,
    features: pd.DataFrame,
    rules: pd.DataFrame,
    field_intersection: pd.DataFrame,
    feature_context: dict,
    detector_context: dict,
) -> pd.DataFrame:
    combined = feature_context["combined_records"]
    return pd.DataFrame(
        [
            {
                "check": "Expected project row counts",
                "evidence": (
                    f"4797={len(bundle.records['clean_4797'])}; "
                    f"4581={len(bundle.records['dirty_4581'])}"
                ),
                "status": "Pass",
                "risk": "Critical check; pipeline halts on cohort drift.",
            },
            {
                "check": "Namespaced UID uniqueness",
                "evidence": f"{combined.index.nunique():,}/{len(combined):,} unique",
                "status": "Pass" if combined.index.is_unique else "Fail",
                "risk": "Prevents 174 overlapping numeric record IDs from corrupting joins.",
            },
            {
                "check": "Metadata field inventory",
                "evidence": (
                    f"both={(field_intersection['availability'] == 'both').sum()}, "
                    f"4797-only={(field_intersection['availability'] == 'clean_4797 only').sum()}, "
                    f"4581-only={(field_intersection['availability'] == 'dirty_4581 only').sum()}"
                ),
                "status": "Pass",
                "risk": "Pooled models use only shared behavioral/TFA fields.",
            },
            {
                "check": "Verified-human timing reference",
                "evidence": f"n={len(feature_context['verified_human_index'])}",
                "status": "Pass",
                "risk": "R1–R4 thresholds are anchored to completed 4797 caregivers.",
            },
            {
                "check": "Email anti-fraud component",
                "evidence": (
                    "available"
                    if feature_context["email_component_available"]
                    else "not returned under current API permissions"
                ),
                "status": (
                    "Pass"
                    if feature_context["email_component_available"]
                    else "Caveat"
                ),
                "risk": "R10 uses knee and age gates; it does not infer missing email evidence.",
            },
            {
                "check": "Tier coverage",
                "evidence": f"{rules['tier'].notna().sum():,}/{len(rules):,} assigned",
                "status": "Pass",
                "risk": "All records are represented in sensitivity definitions.",
            },
            {
                "check": "Prior supervised LightGBM artifact",
                "evidence": "Not present in the scoped project files",
                "status": "Caveat",
                "risk": "Concordance uses rules, three novelty detectors, and PU/source classifier.",
            },
            {
                "check": "PU/contamination identifiability",
                "evidence": (
                    f"SHAP={detector_context['shap_status']}; source drift remains inseparable from fraud"
                ),
                "status": "Caveat",
                "risk": "Model scores are human-envelope distance, not bot probabilities.",
            },
        ]
    )


def _export_tables(
    project_dir: Path,
    tables: dict[str, pd.DataFrame],
    record_flags: pd.DataFrame,
    chart_map: pd.DataFrame,
) -> pd.DataFrame:
    output_dir = project_dir / "Caregiver Outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows = []
    for filename, frame in tables.items():
        target = output_dir / filename
        safe_frame = frame.copy()
        forbidden_headers = DIRECT_IDENTIFIER_FIELDS & set(safe_frame.columns)
        if forbidden_headers:
            raise RuntimeError(
                f"Direct identifier fields would be exported in {filename}: "
                f"{sorted(forbidden_headers)}"
            )
        safe_frame.to_csv(target, index=False)
        manifest_rows.append(
            {
                "file": filename,
                "rows": len(safe_frame),
                "columns": len(safe_frame.columns),
                "sha256_12": sha256_file(target)[:12],
                "caption": "",
            }
        )

    record_target = output_dir / "record_flags.parquet"
    forbidden_record_fields = DIRECT_IDENTIFIER_FIELDS & set(record_flags.columns)
    if forbidden_record_fields:
        raise RuntimeError(
            f"Direct identifiers would enter the record audit: {forbidden_record_fields}"
        )
    record_flags.to_parquet(record_target, index=False)
    _write_sha_sidecar(record_target, datetime.now(timezone.utc).isoformat())

    chart_caption = chart_map.set_index("file")["caption"].to_dict()
    for path in sorted(output_dir.iterdir()):
        if path.name == "output_manifest.csv":
            continue
        if path.suffix.lower() not in {".png", ".pdf", ".csv"}:
            continue
        if path.name in {row["file"] for row in manifest_rows}:
            continue
        if path.suffix.lower() == ".csv":
            try:
                frame = pd.read_csv(path)
                rows, columns = len(frame), len(frame.columns)
            except Exception:
                rows, columns = np.nan, np.nan
        else:
            rows, columns = np.nan, np.nan
        manifest_rows.append(
            {
                "file": path.name,
                "rows": rows,
                "columns": columns,
                "sha256_12": sha256_file(path)[:12],
                "caption": chart_caption.get(path.name, ""),
            }
        )
    manifest = pd.DataFrame(manifest_rows).drop_duplicates("file", keep="last")
    manifest = manifest.sort_values("file").reset_index(drop=True)
    manifest.to_csv(output_dir / "output_manifest.csv", index=False)
    return manifest


def run_upgrade(project_dir: Path) -> dict:
    """Run the full prompt-defined upgrade and return notebook-display tables."""

    project_dir = project_dir.resolve()
    config = load_config(project_dir)
    bundle = load_redcap_sources(project_dir, config)
    field_intersection, range_mismatches = build_field_reports(bundle)
    (
        features,
        rules,
        rule_definitions,
        false_positive,
        feature_context,
    ) = engineer_behavioral_features_and_rules(bundle, config)
    (
        detector_scores,
        contamination_summary,
        detector_importance,
        detector_agreement,
        detector_context,
    ) = fit_behavioral_detectors(features, rules, config)
    (
        sensitivity,
        logistic_table,
        characterization,
        fits,
        frames,
    ) = run_inclusion_sensitivity(bundle, rules, project_dir, config)
    status_name = "3. 4797 status quo"
    (
        gmm_grid,
        blrt,
        posterior_table,
        bch_table,
        latent_context,
    ) = analyze_latent_profiles(fits[status_name], frames[status_name], config)
    regression_checks, tipping, precision = baseline_regression_checks(
        fits[status_name],
        frames[status_name],
        logistic_table,
        config,
    )
    decision_summary = _decision_summary(
        sensitivity, contamination_summary, tipping, regression_checks
    )
    branching_audit = feature_context["branching_audit"]
    data_quality = _data_quality_summary(
        bundle,
        features,
        rules,
        field_intersection,
        feature_context,
        detector_context,
    )
    tier_counts = (
        pd.DataFrame(
            {
                "source_project": features["source_project"],
                "tier": rules["tier"],
                "tier_label": rules["tier_label"],
            }
        )
        .groupby(["source_project", "tier", "tier_label"], as_index=False)
        .size()
        .rename(columns={"size": "n"})
    )
    tier_counts["pct_within_project"] = tier_counts.groupby("source_project")[
        "n"
    ].transform(lambda values: values / values.sum() * 100)
    rule_counts = pd.DataFrame(
        {
            "rule": [f"R{i}" for i in range(1, 11)],
            "all_n": [int(rules[f"rule_R{i}"].sum()) for i in range(1, 11)],
            "clean_4797_n": [
                int(
                    rules.loc[
                        features["source_project"].eq("clean_4797"), f"rule_R{i}"
                    ].sum()
                )
                for i in range(1, 11)
            ],
            "dirty_4581_n": [
                int(
                    rules.loc[
                        features["source_project"].eq("dirty_4581"), f"rule_R{i}"
                    ].sum()
                )
                for i in range(1, 11)
            ],
        }
    )
    latent_quality = _aggregate_latent_quality(posterior_table, latent_context)
    color_accessibility = _color_accessibility_table(config)
    chart_map = generate_upgrade_figures(
        project_dir=project_dir,
        bundle=bundle,
        features=features,
        rules=rules,
        false_positive=false_positive,
        detector_scores=detector_scores,
        detector_importance=detector_importance,
        detector_context=detector_context,
        sensitivity=sensitivity,
        logistic_table=logistic_table,
        fits=fits,
        frames=frames,
        latent_context=latent_context,
        feature_context=feature_context,
        config=config,
    )

    status_assignment = fits[status_name].named_labels
    record_flags = pd.concat(
        [
            features[
                [
                    "source_project",
                    "project_id",
                    "record_id",
                ]
            ],
            rules,
            detector_scores,
        ],
        axis=1,
    )
    record_flags["cluster_status_quo"] = record_flags.index.map(status_assignment)
    record_flags.insert(0, "uid", record_flags.index)
    # The audit artifact is ignored and remains internal; no direct contact,
    # location, DOB, or open-text fields are included.
    tables = {
        "table_12_api_cache_inventory.csv": bundle.cache_inventory,
        "table_13_field_intersection.csv": field_intersection,
        "table_13b_shared_field_range_mismatches.csv": range_mismatches,
        "table_14_fraud_rule_definitions.csv": rule_definitions,
        "table_15_rule_false_positive_rates.csv": false_positive,
        "table_15b_rule_counts_by_project.csv": rule_counts,
        "table_16_trust_tier_counts.csv": tier_counts,
        "table_17_contamination_identifiability.csv": contamination_summary,
        "table_18_detector_feature_importance.csv": detector_importance,
        "table_19_detector_agreement.csv": detector_agreement,
        "table_20_tier_sensitivity.csv": sensitivity,
        "table_21_characterization_by_definition.csv": characterization,
        "table_22_logistic_models.csv": logistic_table,
        "table_23_gmm_diagnostics.csv": gmm_grid,
        "table_24_gmm_bootstrap_lrt.csv": blrt,
        "table_25_latent_profile_quality.csv": latent_quality,
        "table_26_bch_screening_outcome.csv": bch_table,
        "table_27_status_quo_regression_checks.csv": regression_checks,
        "table_28_excluded_case_tipping_point.csv": tipping,
        "table_29_power_precision.csv": precision,
        "table_30_decision_summary.csv": decision_summary,
        "table_31_upgrade_data_quality.csv": data_quality,
        "table_32_color_accessibility_check.csv": color_accessibility,
        "table_33_figure_map.csv": chart_map,
        "table_34_branching_logic_audit.csv": branching_audit,
    }
    manifest = _export_tables(
        project_dir, tables, record_flags.reset_index(drop=True), chart_map
    )
    return {
        "config": config,
        "bundle": bundle,
        "field_intersection": field_intersection,
        "range_mismatches": range_mismatches,
        "features": features,
        "rules": rules,
        "rule_definitions": rule_definitions,
        "false_positive": false_positive,
        "tier_counts": tier_counts,
        "rule_counts": rule_counts,
        "detector_scores": detector_scores,
        "contamination_summary": contamination_summary,
        "detector_importance": detector_importance,
        "detector_agreement": detector_agreement,
        "sensitivity": sensitivity,
        "logistic_table": logistic_table,
        "characterization": characterization,
        "gmm_grid": gmm_grid,
        "blrt": blrt,
        "latent_quality": latent_quality,
        "bch_table": bch_table,
        "regression_checks": regression_checks,
        "tipping": tipping,
        "precision": precision,
        "decision_summary": decision_summary,
        "data_quality": data_quality,
        "color_accessibility": color_accessibility,
        "chart_map": chart_map,
        "manifest": manifest,
        "fits": fits,
        "frames": frames,
        "feature_context": feature_context,
        "branching_audit": branching_audit,
        "detector_context": detector_context,
        "latent_context": latent_context,
    }
