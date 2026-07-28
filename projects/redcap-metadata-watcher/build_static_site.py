"""Pre-render the live dashboard into a static site for GitHub Pages.

GitHub Pages serves static files only — it cannot run Streamlit. This script
does the REDCap reads that the Streamlit app does at runtime, reduces them to
the same aggregate metrics, and writes a JSON payload plus a self-contained
interactive page that reproduces the dashboard client-side.

    python projects/redcap-metadata-watcher/build_static_site.py --output docs

Read-only, like everything else here: it reuses `redcap_live`, so the content
allowlist and the participant-row reduction both apply unchanged.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import shutil
import sys

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
if str(REPO_ROOT / "shared") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "shared"))

import metrics  # noqa: E402
from redcap_live import fetch_studies  # noqa: E402
from study_config import (  # noqa: E402
    api_url,
    configured_studies,
    load_env_file,
)

# Bit flags packed into the last column of each field row.
FLAG_REQUIRED = 1
FLAG_IDENTIFIER = 2
FLAG_BRANCHING = 4
FLAG_LABELLED = 8
FLAG_VALIDATED = 16


class _Dictionary:
    """Dictionary-encodes repeated strings so the payload stays small."""

    def __init__(self) -> None:
        self.values: list[str] = []
        self._index: dict[str, int] = {}

    def index(self, value: str) -> int:
        key = str(value)
        found = self._index.get(key)
        if found is None:
            found = len(self.values)
            self._index[key] = found
            self.values.append(key)
        return found


def build_payload(snapshots: dict) -> dict:
    connected = {k: s for k, s in snapshots.items() if s.ok}
    overview = metrics.study_overview(connected)

    studies = []
    for key, snap in connected.items():
        row = overview[overview["study"] == key].iloc[0]
        totals = metrics.completion_totals(snap)
        instruments = metrics.instrument_summary(snap)
        events = metrics.event_summary(snap)
        quality = metrics.quality_flags(snap)
        fields = metrics.field_inventory(snap)

        type_counts = (
            fields["field_type"].replace("", "(blank)").value_counts().head(12)
            if not fields.empty
            else {}
        )

        studies.append(
            {
                "key": key,
                "title": snap.title,
                "pid": snap.pid,
                "records": int(row["records"]),
                "rows": int(row.get("rows", 0)),
                "instruments": int(row["instruments"]),
                "fields": int(row["fields"]),
                "events": int(row["events"]),
                "longitudinal": bool(snap.longitudinal),
                "repeating": bool(snap.repeating),
                "surveys": bool(snap.surveys_enabled),
                "identifier_fields": int(row["identifier_fields"]),
                "required_fields": int(row["required_fields"]),
                "branching_fields": int(row["branching_fields"]),
                "completion": {k: int(v) for k, v in totals.items()},
                "completion_rate": float(row["completion_rate"]),
                "field_types": [[str(k), int(v)] for k, v in dict(type_counts).items()],
                "instrument_rows": [
                    {
                        "name": r["instrument_name"],
                        "label": r["instrument_label"],
                        "fields": int(r["fields"]),
                        "events": int(r["events_assigned"]),
                        "complete": int(r["Complete"]),
                        "incomplete": int(r["Incomplete"]),
                        "unverified": int(r["Unverified"]),
                        "notStarted": int(r["Not started"]),
                        "started": int(r["started"]),
                        "rate": float(r["completion_rate"]),
                    }
                    for r in instruments.to_dict("records")
                ],
                "event_rows": [
                    {
                        "name": r["event"],
                        "label": r.get("event_label", r["event"]),
                        "records": int(r["records"]),
                        "rows": int(r["rows"]),
                        "started": int(r["started"]),
                        "rate": float(r["completion_rate"]),
                    }
                    for r in events.to_dict("records")
                ],
                "quality": [
                    {
                        "check": r["check"],
                        "count": int(r["count"]),
                        "detail": r["detail"],
                    }
                    for r in quality.to_dict("records")
                ],
            }
        )

    # --- columnar, dictionary-encoded field inventory ---------------------- #
    study_dict, form_dict, type_dict, valid_dict = (
        _Dictionary(),
        _Dictionary(),
        _Dictionary(),
        _Dictionary(),
    )
    field_rows: list[list] = []
    for key, snap in connected.items():
        inventory = metrics.field_inventory(snap)
        for r in inventory.to_dict("records"):
            flags = 0
            if r["required"]:
                flags |= FLAG_REQUIRED
            if r["identifier"]:
                flags |= FLAG_IDENTIFIER
            if r["has_branching"]:
                flags |= FLAG_BRANCHING
            if r["has_label"]:
                flags |= FLAG_LABELLED
            if r["has_validation"]:
                flags |= FLAG_VALIDATED
            field_rows.append(
                [
                    study_dict.index(r["study"]),
                    form_dict.index(r["form_name"]),
                    type_dict.index(r["field_type"]),
                    valid_dict.index(r["validation"]),
                    r["field_name"],
                    r["field_label"],
                    r["field_note"],
                    r["choices"],
                    int(r["choice_count"]),
                    flags,
                ]
            )

    matrix = metrics.instrument_matrix(connected)
    study_keys = list(connected)
    matrix_rows = [
        {
            "name": r["instrument_name"],
            "label": r["instrument_label"],
            "studies": int(r["studies"]),
            "in": [k for k in study_keys if bool(r.get(k))],
        }
        for r in matrix.to_dict("records")
    ]

    failed = [
        {"key": k, "status": s.status, "detail": s.status_detail}
        for k, s in snapshots.items()
        if not s.ok
    ]

    return {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "apiUrl": api_url(),
        "studies": studies,
        "failed": failed,
        "matrix": matrix_rows,
        "fields": {
            "studies": study_dict.values,
            "forms": form_dict.values,
            "types": type_dict.values,
            "validations": valid_dict.values,
            "rows": field_rows,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "docs"),
        help="Directory to write the site into (default: <repo>/docs).",
    )
    parser.add_argument(
        "--payload-only",
        action="store_true",
        help="Regenerate data.json without recopying the page shell.",
    )
    args = parser.parse_args(argv)

    load_env_file()
    studies = configured_studies()
    if not studies:
        print("No study tokens configured; nothing to build.", file=sys.stderr)
        return 1

    print(f"Reading {len(studies)} REDCap projects (read-only)…", flush=True)
    snapshots = fetch_studies(studies, url=api_url())
    for key, snap in snapshots.items():
        state = snap.status if snap.ok else f"{snap.status} — {snap.status_detail}"
        print(f"  {key}: {state}")

    payload = build_payload(snapshots)
    if not payload["studies"]:
        print("No study connected; refusing to publish an empty site.", file=sys.stderr)
        return 1

    serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    # The payload is built from field metadata, which never carries a token — but
    # this site gets published, so prove it rather than assume it.
    for study in studies:
        if study.token and study.token in serialized:
            print(
                f"REFUSING TO WRITE: the {study.token_env} value appears in the "
                "payload. Investigate before publishing.",
                file=sys.stderr,
            )
            return 2

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    data_path = output / "data.json"
    data_path.write_text(serialized, encoding="utf-8")

    if not args.payload_only:
        shutil.copyfile(APP_DIR / "site" / "index.html", output / "index.html")
        # Stop GitHub Pages running the payload through Jekyll.
        (output / ".nojekyll").write_text("", encoding="utf-8")
        assets_out = output / "assets"
        assets_out.mkdir(exist_ok=True)
        for name in ("esd-logo.png", "uofsc-logo.png", "favicon.png"):
            source = REPO_ROOT / "assets" / name
            if source.exists():
                shutil.copyfile(source, assets_out / name)

    size_mb = data_path.stat().st_size / 1_048_576
    print(
        f"\nWrote {output}/ — {len(payload['studies'])} studies, "
        f"{len(payload['fields']['rows']):,} fields, data.json {size_mb:.1f} MB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
