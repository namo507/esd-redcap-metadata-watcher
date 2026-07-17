"""Read-only, rate-limited REDCap acquisition for the metadata watcher."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping

import pandas as pd
from redcap import Project

from watcher_core import normalize_metadata


_SECRET_RE = re.compile(r"\b[a-fA-F0-9]{24,128}\b")
_TOKEN_PARAM_RE = re.compile(
    r"(?i)(token|api[_ -]?key|authorization)(\s*[:=]\s*)([^\s,;]+)"
)


def pycap_version() -> str:
    """Return the installed PyCap version without importing private symbols."""
    try:
        return version("PyCap")
    except PackageNotFoundError:
        return "not installed"


def sanitize_error(error: BaseException | str, max_length: int = 420) -> str:
    """Return a bounded error message with credential-like strings removed."""
    message = " ".join(str(error).replace("\n", " ").split())
    message = _TOKEN_PARAM_RE.sub(r"\1\2[redacted]", message)
    message = _SECRET_RE.sub("[redacted]", message)
    if not message:
        message = error.__class__.__name__ if isinstance(error, BaseException) else "Unknown error"
    return message[:max_length] + ("…" if len(message) > max_length else "")


def _looks_like_auth_failure(message: str) -> bool:
    value = message.lower()
    markers = (
        "invalid token",
        "api token",
        "not authorized",
        "unauthorized",
        "permission denied",
        "403",
        "401",
    )
    return any(marker in value for marker in markers)


def _looks_like_rate_limit(message: str) -> bool:
    value = message.lower()
    return any(
        marker in value
        for marker in ("429", "rate limit", "too many requests", "throttl")
    )


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def _first_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, list) and value and isinstance(value[0], Mapping):
        return dict(value[0])
    return {}


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, pd.DataFrame):
        return value.reset_index().to_dict(orient="records")
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, list):
        return [dict(row) for row in value if isinstance(row, Mapping)]
    return []


class GlobalRequestPacer:
    """Serialize outbound REDCap calls and enforce a process-wide interval."""

    _lock = threading.Lock()
    _next_allowed_at = 0.0

    @classmethod
    def wait(cls, minimum_interval_seconds: float) -> None:
        interval = max(float(minimum_interval_seconds), 0.0)
        with cls._lock:
            now = time.monotonic()
            wait_for = max(0.0, cls._next_allowed_at - now)
            if wait_for:
                time.sleep(wait_for)
            cls._next_allowed_at = time.monotonic() + interval


@dataclass
class CallResult:
    state: str
    detail: str
    value: Any = None


@dataclass
class ProjectSnapshot:
    key: str
    label: str
    pid: int | str
    status: str
    status_detail: str
    fetched_at: datetime
    project_info: dict[str, Any] = field(default_factory=dict)
    metadata: pd.DataFrame = field(default_factory=pd.DataFrame)
    instruments: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    event_mappings: list[dict[str, Any]] = field(default_factory=list)
    repeating: list[dict[str, Any]] = field(default_factory=list)
    record_count: int | None = None
    calls: dict[str, CallResult] = field(default_factory=dict)
    client_version: str = field(default_factory=pycap_version)

    @property
    def connected(self) -> bool:
        return self.status in {"connected", "limited"}

    @property
    def is_longitudinal(self) -> bool:
        return _as_bool(self.project_info.get("is_longitudinal"))

    @property
    def has_repeating(self) -> bool:
        return _as_bool(
            self.project_info.get("has_repeating_instruments_or_events")
        )

    def coverage_frame(self) -> pd.DataFrame:
        labels = {
            "project_info": "Project information",
            "metadata": "Field metadata",
            "field_names": "Export field names",
            "instruments": "Instruments",
            "events": "Events",
            "event_mappings": "Instrument-event mapping",
            "repeating": "Repeating instruments/events",
            "record_count": "Optional record-count check",
        }
        rows = []
        for name, result in self.calls.items():
            rows.append(
                {
                    "API call": labels.get(name, name.replace("_", " ").title()),
                    "State": result.state.replace("_", " ").title(),
                    "Coverage note": result.detail,
                }
            )
        return pd.DataFrame(rows)


def _call_read_only(
    name: str,
    operation: Callable[[], Any],
    *,
    minimum_interval_seconds: float,
    rate_limit_retry_seconds: float,
) -> CallResult:
    """Execute one read-only call with pacing and a single 429 recovery."""
    for attempt in range(2):
        GlobalRequestPacer.wait(minimum_interval_seconds)
        try:
            value = operation()
            return CallResult("success", "Available", value)
        except Exception as exc:  # PyCap can raise Requests and decoding errors.
            detail = sanitize_error(exc)
            if attempt == 0 and _looks_like_rate_limit(detail):
                time.sleep(max(rate_limit_retry_seconds, minimum_interval_seconds))
                continue
            return CallResult("failed", detail)
    return CallResult("failed", f"{name} did not complete")


def _mark_skipped(
    calls: dict[str, CallResult], names: Iterable[str], detail: str
) -> None:
    for name in names:
        calls.setdefault(name, CallResult("skipped", detail))


def fetch_project_snapshot(
    *,
    key: str,
    config: Mapping[str, Any],
    token: str,
    api_url: str,
    doe_doc_patterns: Iterable[str],
    include_record_count: bool = False,
    minimum_interval_seconds: float = 1.25,
    rate_limit_retry_seconds: float = 15.0,
) -> ProjectSnapshot:
    """Fetch one project using only PyCap export methods."""
    label = str(config.get("label", key))
    pid = config.get("pid", "")
    fetched_at = datetime.now(timezone.utc)
    calls: dict[str, CallResult] = {}

    try:
        project = Project(api_url, token, timeout=(10, 60))
    except Exception as exc:
        detail = sanitize_error(exc)
        return ProjectSnapshot(
            key=key,
            label=label,
            pid=pid,
            status="failed",
            status_detail=detail,
            fetched_at=fetched_at,
            calls={"project_info": CallResult("failed", detail)},
        )

    calls["project_info"] = _call_read_only(
        "project_info",
        lambda: project.export_project_info(format_type="json"),
        minimum_interval_seconds=minimum_interval_seconds,
        rate_limit_retry_seconds=rate_limit_retry_seconds,
    )
    info = _first_mapping(calls["project_info"].value)

    if calls["project_info"].state == "failed" and _looks_like_auth_failure(
        calls["project_info"].detail
    ):
        _mark_skipped(
            calls,
            (
                "metadata",
                "field_names",
                "instruments",
                "events",
                "event_mappings",
                "repeating",
                "record_count",
            ),
            "Not called after token authentication failed",
        )
        return ProjectSnapshot(
            key=key,
            label=label,
            pid=pid,
            status="failed",
            status_detail=calls["project_info"].detail,
            fetched_at=fetched_at,
            project_info=info,
            calls=calls,
        )

    calls["metadata"] = _call_read_only(
        "metadata",
        lambda: project.export_metadata(format_type="df"),
        minimum_interval_seconds=minimum_interval_seconds,
        rate_limit_retry_seconds=rate_limit_retry_seconds,
    )
    if calls["metadata"].state == "failed" and _looks_like_auth_failure(
        calls["metadata"].detail
    ):
        _mark_skipped(
            calls,
            (
                "field_names",
                "instruments",
                "events",
                "event_mappings",
                "repeating",
                "record_count",
            ),
            "Not called after token authentication failed",
        )
        return ProjectSnapshot(
            key=key,
            label=label,
            pid=pid,
            status="failed",
            status_detail=calls["metadata"].detail,
            fetched_at=fetched_at,
            project_info=info,
            calls=calls,
        )

    calls["field_names"] = _call_read_only(
        "field_names",
        lambda: project.export_field_names(format_type="df"),
        minimum_interval_seconds=minimum_interval_seconds,
        rate_limit_retry_seconds=rate_limit_retry_seconds,
    )
    calls["instruments"] = _call_read_only(
        "instruments",
        lambda: project.export_instruments(format_type="json"),
        minimum_interval_seconds=minimum_interval_seconds,
        rate_limit_retry_seconds=rate_limit_retry_seconds,
    )

    is_longitudinal = _as_bool(info.get("is_longitudinal"))
    if is_longitudinal:
        calls["events"] = _call_read_only(
            "events",
            lambda: project.export_events(format_type="json"),
            minimum_interval_seconds=minimum_interval_seconds,
            rate_limit_retry_seconds=rate_limit_retry_seconds,
        )
        calls["event_mappings"] = _call_read_only(
            "event_mappings",
            lambda: project.export_instrument_event_mappings(format_type="json"),
            minimum_interval_seconds=minimum_interval_seconds,
            rate_limit_retry_seconds=rate_limit_retry_seconds,
        )
    else:
        _mark_skipped(
            calls,
            ("events", "event_mappings"),
            "Not longitudinal",
        )

    has_repeating = _as_bool(info.get("has_repeating_instruments_or_events"))
    if has_repeating:
        calls["repeating"] = _call_read_only(
            "repeating",
            lambda: project.export_repeating_instruments_events(format_type="json"),
            minimum_interval_seconds=minimum_interval_seconds,
            rate_limit_retry_seconds=rate_limit_retry_seconds,
        )
    else:
        calls["repeating"] = CallResult("skipped", "None reported")

    raw_metadata = calls["metadata"].value
    metadata_ok = calls["metadata"].state == "success"
    metadata_frame = raw_metadata if isinstance(raw_metadata, pd.DataFrame) else pd.DataFrame()
    field_names = calls["field_names"].value
    field_names_frame = field_names if isinstance(field_names, pd.DataFrame) else pd.DataFrame()

    try:
        normalized = normalize_metadata(
            metadata_frame,
            field_names_frame,
            doe_doc_patterns=list(doe_doc_patterns),
        )
    except Exception as exc:
        normalized = pd.DataFrame()
        calls["metadata"] = CallResult(
            "failed", f"Metadata normalization failed: {sanitize_error(exc)}"
        )
        metadata_ok = False

    record_count: int | None = None
    if include_record_count and metadata_ok and not normalized.empty:
        record_field = str(normalized.iloc[0].get("field_name", "")).strip()
        if record_field:
            calls["record_count"] = _call_read_only(
                "record_count",
                lambda: project.export_records(
                    format_type="df",
                    fields=[record_field],
                    raw_or_label="raw",
                ),
                minimum_interval_seconds=minimum_interval_seconds,
                rate_limit_retry_seconds=rate_limit_retry_seconds,
            )
            record_frame = calls["record_count"].value
            if calls["record_count"].state == "success" and isinstance(
                record_frame, pd.DataFrame
            ):
                if record_field in record_frame.columns:
                    record_count = int(record_frame[record_field].nunique(dropna=True))
                else:
                    record_count = int(len(record_frame.index.unique()))
        else:
            calls["record_count"] = CallResult(
                "skipped", "No record identifier field was available"
            )
    else:
        calls["record_count"] = CallResult(
            "skipped",
            "Disabled" if not include_record_count else "No metadata available",
        )

    failed_calls = [name for name, result in calls.items() if result.state == "failed"]
    if not metadata_ok:
        status = "failed"
        status_detail = calls["metadata"].detail
    elif failed_calls:
        status = "limited"
        status_detail = "Metadata connected; one or more optional reads were unavailable"
    else:
        status = "connected"
        status_detail = "Metadata connected"

    resolved_label = str(info.get("project_title") or label)
    resolved_pid = info.get("project_id") or pid
    return ProjectSnapshot(
        key=key,
        label=resolved_label,
        pid=resolved_pid,
        status=status,
        status_detail=status_detail,
        fetched_at=fetched_at,
        project_info=info,
        metadata=normalized,
        instruments=_records(calls["instruments"].value),
        events=_records(calls["events"].value),
        event_mappings=_records(calls["event_mappings"].value),
        repeating=_records(calls["repeating"].value),
        record_count=record_count,
        calls=calls,
    )


def fetch_projects(
    *,
    tokens: Mapping[str, str],
    registry: Mapping[str, Mapping[str, Any]],
    api_url: str,
    doe_doc_patterns: Iterable[str],
    include_record_count: bool = False,
    minimum_interval_seconds: float = 1.25,
    rate_limit_retry_seconds: float = 15.0,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, ProjectSnapshot]:
    """Fetch every supplied project sequentially so bursts never occur."""
    selected = [(key, token.strip()) for key, token in tokens.items() if token.strip()]
    snapshots: dict[str, ProjectSnapshot] = {}
    total = len(selected)
    for index, (key, token) in enumerate(selected, start=1):
        if progress:
            progress(key, index, total)
        snapshots[key] = fetch_project_snapshot(
            key=key,
            config=registry[key],
            token=token,
            api_url=api_url,
            doe_doc_patterns=doe_doc_patterns,
            include_record_count=include_record_count,
            minimum_interval_seconds=minimum_interval_seconds,
            rate_limit_retry_seconds=rate_limit_retry_seconds,
        )
    return snapshots
