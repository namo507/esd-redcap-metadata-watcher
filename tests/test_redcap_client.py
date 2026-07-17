import unittest
from unittest.mock import Mock, call, patch

import pandas as pd

import redcap_client as client


DUMMY_TOKEN = "A1B2C3D4E5F60718" + "293A4B5C6D7E8F90"
API_URL = "https://redcap.invalid/api/"
CONFIG = {"pid": 101, "label": "Test study"}


def classic_project_mock() -> Mock:
    """Return a complete classic-project double with no network behavior."""

    project = Mock(name="redcap_project")
    project.export_project_info.return_value = {
        "project_id": 101,
        "project_title": "Test study",
        "is_longitudinal": 0,
        "has_repeating_instruments_or_events": 0,
    }
    project.export_metadata.return_value = pd.DataFrame(
        {
            "form_name": ["intake"],
            "field_type": ["text"],
            "field_label": ["Record ID"],
            "identifier": ["y"],
        },
        index=pd.Index(["record_id"], name="field_name"),
    )
    project.export_field_names.return_value = pd.DataFrame(
        {
            "choice_value": [""],
            "export_field_name": ["record_id"],
        },
        index=pd.Index(["record_id"], name="original_field_name"),
    )
    project.export_instruments.return_value = [
        {"instrument_name": "intake", "instrument_label": "Intake"}
    ]
    return project


class RequestPacingTests(unittest.TestCase):
    def setUp(self):
        self.previous_next_allowed_at = client.GlobalRequestPacer._next_allowed_at
        client.GlobalRequestPacer._next_allowed_at = 0.0

    def tearDown(self):
        client.GlobalRequestPacer._next_allowed_at = self.previous_next_allowed_at

    def test_global_pacer_waits_for_remaining_interval(self):
        # First call reserves t=102.0. The second arrives at t=100.5 and must
        # wait for the exact 1.5-second remainder before reserving its slot.
        with (
            patch.object(
                client.time,
                "monotonic",
                side_effect=[100.0, 100.0, 100.5, 102.25],
            ),
            patch.object(client.time, "sleep") as sleep,
        ):
            client.GlobalRequestPacer.wait(2.0)
            client.GlobalRequestPacer.wait(2.0)

        sleep.assert_called_once_with(1.5)
        self.assertEqual(client.GlobalRequestPacer._next_allowed_at, 104.25)

    def test_call_read_only_paces_every_attempt_and_retries_one_429(self):
        operation = Mock(
            side_effect=[RuntimeError("HTTP 429: too many requests"), {"ok": True}]
        )
        with (
            patch.object(client.GlobalRequestPacer, "wait") as wait,
            patch.object(client.time, "sleep") as sleep,
        ):
            result = client._call_read_only(
                "metadata",
                operation,
                minimum_interval_seconds=1.25,
                rate_limit_retry_seconds=7.0,
            )

        self.assertEqual(result.state, "success")
        self.assertEqual(result.value, {"ok": True})
        self.assertEqual(operation.call_count, 2)
        self.assertEqual(wait.call_args_list, [call(1.25), call(1.25)])
        sleep.assert_called_once_with(7.0)

    def test_call_read_only_stops_after_one_rate_limit_retry(self):
        operation = Mock(
            side_effect=[
                RuntimeError("429 rate limit"),
                RuntimeError(f"authorization={DUMMY_TOKEN}"),
            ]
        )
        with (
            patch.object(client.GlobalRequestPacer, "wait") as wait,
            patch.object(client.time, "sleep") as sleep,
        ):
            result = client._call_read_only(
                "metadata",
                operation,
                minimum_interval_seconds=2.0,
                rate_limit_retry_seconds=0.5,
            )

        self.assertEqual(result.state, "failed")
        self.assertEqual(operation.call_count, 2)
        self.assertEqual(wait.call_count, 2)
        # Backoff can never be shorter than the configured request interval.
        sleep.assert_called_once_with(2.0)
        self.assertNotIn(DUMMY_TOKEN, result.detail)
        self.assertIn("[redacted]", result.detail)

    def test_non_rate_failure_is_not_retried_or_slept(self):
        operation = Mock(side_effect=RuntimeError("Malformed response"))
        with (
            patch.object(client.GlobalRequestPacer, "wait") as wait,
            patch.object(client.time, "sleep") as sleep,
        ):
            result = client._call_read_only(
                "metadata",
                operation,
                minimum_interval_seconds=1.0,
                rate_limit_retry_seconds=10.0,
            )

        self.assertEqual(result.state, "failed")
        operation.assert_called_once_with()
        wait.assert_called_once_with(1.0)
        sleep.assert_not_called()


class RedactionAndSecurityTests(unittest.TestCase):
    def test_sanitize_error_redacts_named_and_bare_credentials(self):
        second_secret = "0123456789ABCDEF" * 2
        raw = RuntimeError(
            f"API token: {DUMMY_TOKEN}\n"
            f"authorization={second_secret}, raw={DUMMY_TOKEN}"
        )

        sanitized = client.sanitize_error(raw)

        self.assertNotIn(DUMMY_TOKEN, sanitized)
        self.assertNotIn(second_secret, sanitized)
        self.assertGreaterEqual(sanitized.count("[redacted]"), 2)
        self.assertNotIn("\n", sanitized)

    def test_sanitize_error_bounds_output_and_handles_empty_exception(self):
        bounded = client.sanitize_error("x" * 500, max_length=20)
        self.assertEqual(bounded, "x" * 20 + "…")
        self.assertEqual(client.sanitize_error(RuntimeError("")), "RuntimeError")

    def test_project_constructor_failure_does_not_expose_token(self):
        with patch.object(
            client,
            "Project",
            side_effect=RuntimeError(f"token={DUMMY_TOKEN}"),
        ) as project_class:
            snapshot = client.fetch_project_snapshot(
                key="TEST",
                config=CONFIG,
                token=DUMMY_TOKEN,
                api_url=API_URL,
                doe_doc_patterns=[],
            )

        project_class.assert_called_once_with(API_URL, DUMMY_TOKEN, timeout=(10, 60))
        self.assertEqual(snapshot.status, "failed")
        self.assertNotIn(DUMMY_TOKEN, snapshot.status_detail)
        self.assertNotIn(DUMMY_TOKEN, snapshot.calls["project_info"].detail)

    def test_auth_failure_skips_all_downstream_api_calls(self):
        project = Mock(name="redcap_project")
        project.export_project_info.side_effect = RuntimeError(
            f"401 invalid token: {DUMMY_TOKEN}"
        )

        with (
            patch.object(client, "Project", return_value=project),
            patch.object(client.GlobalRequestPacer, "wait") as wait,
        ):
            snapshot = client.fetch_project_snapshot(
                key="TEST",
                config=CONFIG,
                token=DUMMY_TOKEN,
                api_url=API_URL,
                doe_doc_patterns=[],
            )

        self.assertEqual(snapshot.status, "failed")
        self.assertNotIn(DUMMY_TOKEN, snapshot.status_detail)
        project.export_project_info.assert_called_once_with(format_type="json")
        project.export_metadata.assert_not_called()
        project.export_field_names.assert_not_called()
        project.export_instruments.assert_not_called()
        project.export_records.assert_not_called()
        wait.assert_called_once_with(1.25)
        downstream = {
            "metadata",
            "field_names",
            "instruments",
            "events",
            "event_mappings",
            "repeating",
            "record_count",
        }
        self.assertEqual(
            {name for name in downstream if snapshot.calls[name].state == "skipped"},
            downstream,
        )

    def test_classic_snapshot_uses_only_expected_read_exports(self):
        project = classic_project_mock()
        with (
            patch.object(client, "Project", return_value=project),
            patch.object(client.GlobalRequestPacer, "wait") as wait,
        ):
            snapshot = client.fetch_project_snapshot(
                key="TEST",
                config=CONFIG,
                token=DUMMY_TOKEN,
                api_url=API_URL,
                doe_doc_patterns=[],
            )

        self.assertEqual(snapshot.status, "connected")
        self.assertEqual(snapshot.metadata["field_key"].tolist(), ["record_id"])
        self.assertEqual(wait.call_count, 4)
        project.export_project_info.assert_called_once_with(format_type="json")
        project.export_metadata.assert_called_once_with(format_type="df")
        project.export_field_names.assert_called_once_with(format_type="df")
        project.export_instruments.assert_called_once_with(format_type="json")
        project.export_events.assert_not_called()
        project.export_instrument_event_mappings.assert_not_called()
        project.export_repeating_instruments_events.assert_not_called()
        project.export_records.assert_not_called()
        project.import_metadata.assert_not_called()
        project.import_records.assert_not_called()
        project.delete_records.assert_not_called()

    def test_record_permission_failure_is_limited_and_metadata_survives(self):
        project = classic_project_mock()
        project.export_records.side_effect = RuntimeError(
            f"403 permission denied token={DUMMY_TOKEN}"
        )
        with (
            patch.object(client, "Project", return_value=project),
            patch.object(client.GlobalRequestPacer, "wait"),
        ):
            snapshot = client.fetch_project_snapshot(
                key="TEST",
                config=CONFIG,
                token=DUMMY_TOKEN,
                api_url=API_URL,
                doe_doc_patterns=[],
                include_record_count=True,
            )

        self.assertEqual(snapshot.status, "limited")
        self.assertFalse(snapshot.metadata.empty)
        self.assertIsNone(snapshot.record_count)
        self.assertEqual(snapshot.calls["record_count"].state, "failed")
        self.assertNotIn(DUMMY_TOKEN, snapshot.calls["record_count"].detail)
        project.export_records.assert_called_once_with(
            format_type="df",
            fields=["record_id"],
            raw_or_label="raw",
        )


if __name__ == "__main__":
    unittest.main()
