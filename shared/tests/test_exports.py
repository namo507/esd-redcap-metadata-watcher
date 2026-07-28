from __future__ import annotations

import base64
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import re
from zipfile import ZipFile

import pandas as pd
import plotly.graph_objects as go

import exports as exports_module
from exports import (
    build_csv_exports,
    build_export_bundle,
    build_html_export,
    build_standalone_html,
    build_zip_export,
    dataframe_to_csv_bytes,
    sanitize_filename_stem,
    timestamp_slug,
)


NOW = datetime(2026, 7, 17, 14, 5, 9, tzinfo=timezone.utc)
TOKEN = "0123456789abcdef" * 2


def test_timestamp_and_filename_stem_are_deterministic_and_path_safe() -> None:
    assert timestamp_slug(NOW) == "20260717_140509"
    assert sanitize_filename_stem("../../NANO metadata / watcher.html") == (
        "nano_metadata_watcher_html"
    )
    assert sanitize_filename_stem("***", fallback="metadata watcher") == (
        "metadata_watcher"
    )


def test_csv_is_utf8_escaped_formula_safe_and_credential_redacted() -> None:
    frame = pd.DataFrame(
        {
            "field_name": ["normal", "=HYPERLINK(\"https://bad.test\")"],
            "field_label": ["Café", f"api_token={TOKEN}"],
            "API Token": [TOKEN, TOKEN],
        }
    )

    payload = dataframe_to_csv_bytes(frame)
    text = payload.decode("utf-8-sig")

    assert text.startswith("field_name,field_label,API Token\n")
    assert "Café" in text
    assert "'=HYPERLINK" in text
    assert TOKEN not in text
    assert text.count("[REDACTED]") >= 3


def test_csv_exports_are_timestamped_and_collision_safe() -> None:
    exports = build_csv_exports(
        {
            "Missing/Profile": pd.DataFrame({"count": [1]}),
            "Missing Profile": pd.DataFrame({"count": [2]}),
        },
        prefix="NANO watcher",
        generated_at=NOW,
    )

    assert list(exports) == [
        "nano_watcher_missing_profile_20260717_140509.csv",
        "nano_watcher_missing_profile_20260717_140509_2.csv",
    ]


def test_html_uses_esd_brand_and_escapes_all_non_plotly_content() -> None:
    html = build_standalone_html(
        "NANO <script>alert(1)</script>",
        {"Fields <all>": "5 & 7", "API token": TOKEN},
        {
            "Fields <master>": pd.DataFrame(
                {
                    "field_name": ["<img src=x onerror=alert(1)>"],
                    "field_label": [f"Bearer {TOKEN}"],
                }
            )
        },
        generated_at=NOW,
    )

    assert "#3366FF" in html
    assert "Libre Franklin" in html
    assert "Groundtruth metadata inventory" in html
    assert "NANO &lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Fields &lt;all&gt;" in html
    assert "5 &amp; 7" in html
    assert "Fields &lt;master&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert TOKEN not in html
    assert "<script src=" not in html.casefold()
    assert "recommend" not in html.casefold()
    assert "suggest" not in html.casefold()


def test_html_embeds_packaged_brand_assets_as_data_uris() -> None:
    html = build_standalone_html("NANO metadata", generated_at=NOW)
    encoded_logos = re.findall(
        r'<img class="brand-logo" src="data:image/png;base64,([^"]+)"', html
    )

    assert len(encoded_logos) == 2
    assert [base64.b64decode(value) for value in encoded_logos] == [
        Path(exports_module._ESD_LOGO_PATH).read_bytes(),
        Path(exports_module._UOFSC_LOGO_PATH).read_bytes(),
    ]
    assert '<link rel="icon" type="image/png" href="data:image/png;base64,' in html
    assert 'src="assets/' not in html
    assert 'src="http' not in html.casefold()


def test_html_documents_safe_font_fallback_when_libre_franklin_is_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(exports_module, "_FONT_SEARCH_DIRS", ())
    exports_module._embedded_libre_franklin_css.cache_clear()
    try:
        html = build_standalone_html("NANO metadata", generated_at=NOW)
    finally:
        exports_module._embedded_libre_franklin_css.cache_clear()

    assert (
        "<!-- Libre Franklin local font unavailable; safe system font fallback "
        "stack active. -->"
    ) in html
    assert "@font-face" not in html
    assert 'font-family: "Libre Franklin", -apple-system' in html
    assert "fonts.googleapis" not in html


def test_html_embeds_local_libre_franklin_when_available(
    tmp_path, monkeypatch
) -> None:
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    font_file = font_dir / "LibreFranklin-Medium.woff2"
    font_file.write_bytes(b"local-libre-franklin-font")
    monkeypatch.setattr(exports_module, "_FONT_SEARCH_DIRS", (font_dir,))
    exports_module._embedded_libre_franklin_css.cache_clear()
    try:
        html = build_standalone_html("NANO metadata", generated_at=NOW)
    finally:
        exports_module._embedded_libre_franklin_css.cache_clear()

    encoded = base64.b64encode(b"local-libre-franklin-font").decode("ascii")
    assert (
        "<!-- Libre Franklin embedded from local font data; no external font "
        "request. -->"
    ) in html
    assert "@font-face" in html
    assert f'data:font/woff2;base64,{encoded}' in html
    assert "font-weight: 500" in html
    assert str(font_file) not in html
    assert "fonts.googleapis" not in html


def test_plotly_runtime_is_inline_once_and_reused_by_later_figures() -> None:
    unsafe_label = "</script><script>alert(1)</script>"
    figures = {
        "Field types": go.Figure(go.Bar(x=[2, 1], y=[unsafe_label, "yesno"])),
        "Missing labels": go.Figure(go.Bar(x=[1], y=["intake"])),
    }

    html = build_standalone_html(
        "NANO metadata",
        figures=figures,
        generated_at=NOW,
    )

    assert "plotly.js v" in html.lower()
    assert html.lower().count("plotly.js v") == 1
    assert all(marker in html for marker in ("esd-figure-0", "esd-figure-1"))
    assert html.count('id="esd-figure-') == 2
    assert "<script src=" not in html.casefold()
    assert "Field types" in html
    assert "Missing labels" in html
    assert unsafe_label not in html
    assert r"\u003c\u002fscript\u003e" in html


def test_html_artifact_has_timestamped_safe_filename() -> None:
    artifact = build_html_export(
        "../Cross Project Comparison",
        generated_at=NOW,
    )

    assert artifact.filename == "cross_project_comparison_20260717_140509.html"
    assert artifact.media_type == "text/html; charset=utf-8"
    assert artifact.data.startswith(b"<!doctype html>")


def test_zip_bundle_contains_html_and_csvs_without_writing_files(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    tables = {
        "Overlap Summary": pd.DataFrame({"metric": ["common_all"], "count": [4]}),
        "Missing Profile": pd.DataFrame({"issue_type": ["missing_label"]}),
    }

    payload = build_export_bundle(
        "Cross Project Comparison",
        {"Common fields": 4},
        tables,
        generated_at=NOW,
        filename_stem="cross_project_comparison",
    )

    assert list(tmp_path.iterdir()) == []
    with ZipFile(BytesIO(payload)) as archive:
        names = archive.namelist()
        assert names == [
            "cross_project_comparison_20260717_140509.html",
            "cross_project_comparison_overlap_summary_20260717_140509.csv",
            "cross_project_comparison_missing_profile_20260717_140509.csv",
        ]
        assert archive.read(names[0]).startswith(b"<!doctype html>")
        assert archive.read(names[1]).startswith(b"\xef\xbb\xbf")


def test_zip_artifact_wraps_in_memory_bundle() -> None:
    artifact = build_zip_export(
        "NANO metadata watcher",
        tables={"Fields": pd.DataFrame({"field_name": ["record_id"]})},
        generated_at=NOW,
    )

    assert artifact.filename == "nano_metadata_watcher_20260717_140509.zip"
    assert artifact.media_type == "application/zip"
    with ZipFile(BytesIO(artifact.data)) as archive:
        assert all(".." not in name and not name.startswith("/") for name in archive.namelist())
