"""Read-only REDCap acquisition for the live dashboard.

Two guarantees are enforced here rather than left to convention:

1. **No writes.** Every outbound call goes through :func:`ReadOnlyClient.export`,
   which allowlists the REDCap ``content`` values it will request and rejects the
   parameters REDCap uses to import or delete (``action``, ``data``,
   ``returnContent``, ``overwriteBehavior``, ``forceAutoNumber``). A violation
   raises before any request is made.
2. **No participant rows leave this module.** The completion export is reduced to
   per-instrument and per-event counts inside :func:`fetch_study`; the raw
   response is discarded before the snapshot is returned. Nothing downstream can
   render a participant-level value because it never receives one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd
import requests

from study_config import (
    MIN_REQUEST_INTERVAL_SECONDS,
    RATE_LIMIT_RETRY_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    StudyDefinition,
    api_url,
)

import sys
from pathlib import Path

_SHARED_DIR = Path(__file__).resolve().parents[2] / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

from redcap_client import GlobalRequestPacer, sanitize_error  # noqa: E402

#: REDCap ``content`` values this dashboard is permitted to request.
READ_ONLY_CONTENTS = frozenset(
    {
        "project",
        "metadata",
        "instrument",
        "event",
        "arm",
        "formEventMapping",
        "exportFieldNames",
        "repeatingFormsEvents",
        "record",
        "version",
    }
)

#: Parameters REDCap uses to mutate a project. Presence of any is a hard error.
WRITE_PARAMETERS = frozenset(
    {
        "action",
        "data",
        "returnContent",
        "overwriteBehavior",
        "forceAutoNumber",
    }
)

COMPLETION_LABELS = {
    "0": "Incomplete",
    "1": "Unverified",
    "2": "Complete",
    "": "Not started",
}


class ReadOnlyViolation(RuntimeError):
    """Raised when a call would not be a pure export."""


@dataclass
class StudySnapshot:
    """Aggregate-only view of one REDCap project."""

    key: str
    label: str
    status: str
    status_detail: str
    fetched_at: datetime
    pid: str = ""
    title: str = ""
    project_info: dict[str, Any] = field(default_factory=dict)
    metadata: pd.DataFrame = field(default_factory=pd.DataFrame)
    instruments: pd.DataFrame = field(default_factory=pd.DataFrame)
    events: pd.DataFrame = field(default_factory=pd.DataFrame)
    event_mapping: pd.DataFrame = field(default_factory=pd.DataFrame)
    completion: pd.DataFrame = field(default_factory=pd.DataFrame)
    event_volume: pd.DataFrame = field(default_factory=pd.DataFrame)
    record_count: int | None = None
    row_count: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {"connected", "limited"}

    @property
    def longitudinal(self) -> bool:
        return str(self.project_info.get("is_longitudinal", "0")) == "1"

    @property
    def repeating(self) -> bool:
        return (
            str(self.project_info.get("has_repeating_instruments_or_events", "0")) == "1"
        )

    @property
    def surveys_enabled(self) -> bool:
        return str(self.project_info.get("surveys_enabled", "0")) == "1"


class ReadOnlyClient:
    """A REDCap API client that can only export."""

    def __init__(self, token: str, url: str | None = None) -> None:
        self._token = token
        self._url = url or api_url()

    def export(self, content: str, **params: Any) -> Any:
        if content not in READ_ONLY_CONTENTS:
            raise ReadOnlyViolation(
                f"content={content!r} is not in the read-only allowlist"
            )
        offending = WRITE_PARAMETERS.intersection(params)
        if offending:
            raise ReadOnlyViolation(
                "refusing a call carrying write parameters: "
                + ", ".join(sorted(offending))
            )

        payload: dict[str, Any] = {
            "token": self._token,
            "content": content,
            "format": "json",
            "returnFormat": "json",
        }
        payload.update({k: v for k, v in params.items() if v is not None})

        response = self._post(payload)
        if _is_rate_limited(response):
            GlobalRequestPacer.wait(RATE_LIMIT_RETRY_SECONDS)
            response = self._post(payload)

        if response.status_code != 200:
            raise RuntimeError(
                f"HTTP {response.status_code}: {sanitize_error(response.text)}"
            )
        return response.json()

    def _post(self, payload: Mapping[str, Any]) -> requests.Response:
        GlobalRequestPacer.wait(MIN_REQUEST_INTERVAL_SECONDS)
        try:
            return requests.post(
                self._url, data=dict(payload), timeout=REQUEST_TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:  # pragma: no cover - network path
            raise RuntimeError(sanitize_error(exc)) from None


def _is_rate_limited(response: requests.Response) -> bool:
    if response.status_code == 429:
        return True
    text = (response.text or "").lower()
    return "rate limit" in text or "too many requests" in text


def _frame(rows: Any, columns: Sequence[str] = ()) -> pd.DataFrame:
    if isinstance(rows, list) and rows and isinstance(rows[0], Mapping):
        return pd.DataFrame(rows)
    return pd.DataFrame(columns=list(columns))


def _summarize_completion(
    rows: Iterable[Mapping[str, Any]],
    *,
    id_field: str,
) -> tuple[pd.DataFrame, pd.DataFrame, int, int]:
    """Reduce raw record rows to counts. Raw values never leave this function."""
    rows = list(rows)
    unique_ids: set[str] = set()
    tallies: dict[tuple[str, str, str], int] = {}
    event_rows: dict[str, int] = {}
    event_ids: dict[str, set[str]] = {}

    for row in rows:
        record_id = str(row.get(id_field, "") or "")
        if record_id:
            unique_ids.add(record_id)
        event = str(row.get("redcap_event_name", "") or "(no event)")
        event_rows[event] = event_rows.get(event, 0) + 1
        if record_id:
            event_ids.setdefault(event, set()).add(record_id)
        for key, value in row.items():
            if not key.endswith("_complete"):
                continue
            instrument = key[: -len("_complete")]
            status = COMPLETION_LABELS.get(str(value or ""), "Not started")
            tally_key = (instrument, event, status)
            tallies[tally_key] = tallies.get(tally_key, 0) + 1

    completion = pd.DataFrame(
        [
            {
                "instrument_name": instrument,
                "event": event,
                "status": status,
                "count": count,
            }
            for (instrument, event, status), count in sorted(tallies.items())
        ],
        columns=["instrument_name", "event", "status", "count"],
    )
    volume = pd.DataFrame(
        [
            {
                "event": event,
                "rows": count,
                "records": len(event_ids.get(event, ())),
            }
            for event, count in sorted(event_rows.items())
        ],
        columns=["event", "rows", "records"],
    )
    return completion, volume, len(unique_ids), len(rows)


def fetch_study(
    study: StudyDefinition,
    *,
    url: str | None = None,
    include_completion: bool = True,
) -> StudySnapshot:
    """Fetch one project. Never raises; failures land in the snapshot status."""
    now = datetime.now(timezone.utc)
    if not study.configured:
        return StudySnapshot(
            key=study.key,
            label=study.label,
            status="unconfigured",
            status_detail=f"{study.token_env} is not set",
            fetched_at=now,
        )

    client = ReadOnlyClient(study.token, url)
    notes: list[str] = []

    try:
        info = client.export("project")
        if isinstance(info, list):
            info = info[0] if info else {}
        if not isinstance(info, Mapping):
            raise RuntimeError("unexpected project payload")
        info = dict(info)
    except Exception as exc:
        return StudySnapshot(
            key=study.key,
            label=study.label,
            status="failed",
            status_detail=sanitize_error(exc),
            fetched_at=now,
        )

    snapshot = StudySnapshot(
        key=study.key,
        label=study.label,
        status="connected",
        status_detail="All requested exports succeeded.",
        fetched_at=now,
        pid=str(info.get("project_id", "")),
        title=str(info.get("project_title", study.label)),
        project_info=info,
    )

    def _try(content: str, columns: Sequence[str] = (), **params: Any) -> pd.DataFrame:
        try:
            return _frame(client.export(content, **params), columns)
        except Exception as exc:
            notes.append(f"{content}: {sanitize_error(exc)}")
            return pd.DataFrame(columns=list(columns))

    snapshot.metadata = _try("metadata", ["field_name", "form_name", "field_type"])
    snapshot.instruments = _try("instrument", ["instrument_name", "instrument_label"])
    snapshot.events = _try("event", ["unique_event_name", "event_name"])
    snapshot.event_mapping = _try("formEventMapping", ["unique_event_name", "form"])

    if include_completion and not snapshot.metadata.empty:
        id_field = str(snapshot.metadata.iloc[0]["field_name"])
        forms = (
            snapshot.instruments["instrument_name"].astype(str).tolist()
            if "instrument_name" in snapshot.instruments.columns
            else []
        )
        fields = [id_field] + [f"{form}_complete" for form in forms]
        try:
            rows = client.export("record", fields=",".join(fields), type="flat")
            if isinstance(rows, list):
                (
                    snapshot.completion,
                    snapshot.event_volume,
                    snapshot.record_count,
                    snapshot.row_count,
                ) = _summarize_completion(rows, id_field=id_field)
                del rows
            else:
                notes.append("record: unexpected payload shape")
        except Exception as exc:
            notes.append(f"record: {sanitize_error(exc)}")

    if notes:
        snapshot.status = "limited"
        snapshot.status_detail = "; ".join(notes)
        snapshot.notes = notes
    return snapshot


def fetch_studies(
    studies: Iterable[StudyDefinition],
    *,
    url: str | None = None,
    progress: Any = None,
) -> dict[str, StudySnapshot]:
    """Fetch several projects sequentially, respecting the global pacer."""
    studies = list(studies)
    snapshots: dict[str, StudySnapshot] = {}
    for index, study in enumerate(studies, start=1):
        if progress is not None:
            progress(index - 1, len(studies), study.label)
        snapshots[study.key] = fetch_study(study, url=url)
    if progress is not None:
        progress(len(studies), len(studies), "")
    return snapshots
