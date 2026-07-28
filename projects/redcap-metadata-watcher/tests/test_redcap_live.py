"""The read-only contract and the aggregate-only reduction."""

from __future__ import annotations

import pytest

import redcap_live
from redcap_live import (
    READ_ONLY_CONTENTS,
    WRITE_PARAMETERS,
    ReadOnlyClient,
    ReadOnlyViolation,
    _summarize_completion,
)


class _Recorder:
    """Stands in for requests.post so no test ever reaches the network."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, url, data=None, timeout=None):  # noqa: ANN001
        self.calls.append(dict(data or {}))

        class _Response:
            status_code = 200
            text = "[]"

            @staticmethod
            def json():
                return []

        return _Response()


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(redcap_live.requests, "post", rec)
    monkeypatch.setattr(redcap_live.GlobalRequestPacer, "wait", lambda *a, **k: None)
    return rec


@pytest.mark.parametrize("parameter", sorted(WRITE_PARAMETERS))
def test_write_parameters_are_refused_before_any_request(
    parameter: str, recorder: _Recorder
) -> None:
    client = ReadOnlyClient("token")
    with pytest.raises(ReadOnlyViolation):
        client.export("record", **{parameter: "whatever"})
    assert recorder.calls == [], "a blocked call must not reach the transport"


@pytest.mark.parametrize("content", ["user", "userRole", "file", "arm_", "importRecord"])
def test_content_outside_the_allowlist_is_refused(
    content: str, recorder: _Recorder
) -> None:
    client = ReadOnlyClient("token")
    with pytest.raises(ReadOnlyViolation):
        client.export(content)
    assert recorder.calls == []


def test_allowlist_holds_only_export_contents() -> None:
    # A regression guard: nothing that mutates a project may be added here.
    forbidden = {"importRecord", "deleteRecord", "user", "userRole", "file"}
    assert not (READ_ONLY_CONTENTS & forbidden)


def test_permitted_export_sends_json_format_and_token(recorder: _Recorder) -> None:
    client = ReadOnlyClient("secret-token", url="https://example.invalid/api/")
    client.export("metadata", fields="a,b")
    assert len(recorder.calls) == 1
    payload = recorder.calls[0]
    assert payload["content"] == "metadata"
    assert payload["format"] == "json"
    assert payload["token"] == "secret-token"
    assert payload["fields"] == "a,b"
    assert not WRITE_PARAMETERS & set(payload)


def test_none_valued_parameters_are_dropped(recorder: _Recorder) -> None:
    ReadOnlyClient("t").export("record", fields=None, type="flat")
    assert "fields" not in recorder.calls[0]
    assert recorder.calls[0]["type"] == "flat"


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

ROWS = [
    {"id": "1", "redcap_event_name": "v1", "a_complete": "2", "b_complete": "0"},
    {"id": "1", "redcap_event_name": "v2", "a_complete": "2", "b_complete": ""},
    {"id": "2", "redcap_event_name": "v1", "a_complete": "1", "b_complete": "2"},
    {"id": "2", "redcap_event_name": "v2", "a_complete": "", "b_complete": ""},
]


def test_summarize_counts_records_rows_and_statuses() -> None:
    completion, volume, records, rows = _summarize_completion(ROWS, id_field="id")
    assert records == 2
    assert rows == 4

    def count(instrument: str, status: str) -> int:
        match = completion[
            (completion["instrument_name"] == instrument)
            & (completion["status"] == status)
        ]
        return int(match["count"].sum())

    assert count("a", "Complete") == 2
    assert count("a", "Unverified") == 1
    assert count("a", "Not started") == 1
    assert count("b", "Incomplete") == 1
    assert count("b", "Complete") == 1
    assert count("b", "Not started") == 2

    assert set(volume["event"]) == {"v1", "v2"}
    assert int(volume.loc[volume["event"] == "v1", "records"].iloc[0]) == 2


def test_summarize_returns_no_participant_values() -> None:
    """The reduction must not carry record IDs or response values forward."""
    rows = [
        {"id": "SUBJ-007", "redcap_event_name": "v1", "a_complete": "2"},
        {"id": "SUBJ-008", "redcap_event_name": "v1", "a_complete": "0"},
    ]
    completion, volume, _, _ = _summarize_completion(rows, id_field="id")

    assert set(completion.columns) == {"instrument_name", "event", "status", "count"}
    assert set(volume.columns) == {"event", "rows", "records"}
    for frame in (completion, volume):
        cells = frame.astype(str).to_numpy().ravel().tolist()
        assert not any("SUBJ-" in cell for cell in cells)


def test_summarize_handles_a_project_with_no_events() -> None:
    rows = [{"id": "1", "a_complete": "2"}]
    completion, volume, records, row_count = _summarize_completion(rows, id_field="id")
    assert records == 1 and row_count == 1
    assert volume["event"].tolist() == ["(no event)"]
    assert int(completion.loc[completion["status"] == "Complete", "count"].sum()) == 1
