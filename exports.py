"""In-memory, ESD-branded exports for the metadata watcher.

The functions in this module are deliberately independent of Streamlit and
never write to the filesystem.  They read packaged brand images and, when one
is available locally, Libre Franklin font data so standalone HTML remains
self-contained.  If Libre Franklin is unavailable, exports retain the declared
safe system-font stack and include an HTML comment documenting that fallback.
Callers provide already-derived values, pandas dataframes, and named Plotly
figures; the module returns UTF-8 bytes (or an ``ExportArtifact``) that can be
passed directly to a download control.

Exports are descriptive only.  This module does not generate recommendations,
interpretations, or inferred findings, and it never accepts or stores API
credentials.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from html import escape
from io import BytesIO
import json
from pathlib import Path
import re
import unicodedata
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import plotly.io as pio


_SENSITIVE_LABEL_RE = re.compile(
    r"(?:api[\s_-]*token|access[\s_-]*token|authorization|bearer|password|secret)",
    flags=re.IGNORECASE,
)
_TOKEN_ASSIGNMENT_RE = re.compile(
    r"((?:api[\s_-]*token|access[\s_-]*token|password|secret)\s*[:=]\s*)"
    r"([^\s,;]+)",
    flags=re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)
_REDCAP_TOKEN_RE = re.compile(r"\b[0-9A-Fa-f]{32,64}\b")
_UNSAFE_FILENAME_RE = re.compile(r"[^a-z0-9]+")
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")
_REDACTED = "[REDACTED]"
_MODULE_DIR = Path(__file__).resolve().parent
_ASSET_DIR = _MODULE_DIR / "assets"
_ESD_LOGO_PATH = _ASSET_DIR / "esd-logo.png"
_UOFSC_LOGO_PATH = _ASSET_DIR / "uofsc-logo.png"
_FAVICON_PATH = _ASSET_DIR / "favicon.png"
_FONT_SEARCH_DIRS: tuple[Path, ...] = (
    _ASSET_DIR / "fonts",
    Path.home() / "Library" / "Fonts",
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts"),
)
_FONT_MEDIA_TYPES = {
    ".woff2": ("font/woff2", "woff2"),
    ".woff": ("font/woff", "woff"),
    ".ttf": ("font/ttf", "truetype"),
    ".otf": ("font/otf", "opentype"),
}


@dataclass(frozen=True, slots=True)
class ExportArtifact:
    """A complete download payload suitable for ``st.download_button``."""

    filename: str
    media_type: str
    data: bytes


@lru_cache(maxsize=16)
def _file_data_uri(path: Path, media_type: str) -> str | None:
    """Read a local asset as a data URI, or return ``None`` when unavailable."""

    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:{media_type};base64,{encoded}"


def _font_weight(path: Path) -> str:
    name = re.sub(r"[^a-z0-9]+", "", path.stem.casefold())
    if "variable" in name or "wght" in name:
        return "100 900"
    if "black" in name:
        return "900"
    if "extrabold" in name or "ultrabold" in name:
        return "800"
    if "semibold" in name or "demibold" in name:
        return "600"
    if "bold" in name:
        return "700"
    if "medium" in name:
        return "500"
    if "light" in name:
        return "300"
    if "thin" in name:
        return "100"
    return "400"


def _font_style(path: Path) -> str:
    return "italic" if "italic" in path.stem.casefold() else "normal"


def _find_libre_franklin_files() -> list[Path]:
    candidates: set[Path] = set()
    for directory in _FONT_SEARCH_DIRS:
        if not directory.is_dir():
            continue
        try:
            paths = directory.rglob("*")
            for path in paths:
                normalized_name = re.sub(r"[^a-z]+", "", path.stem.casefold())
                if (
                    path.is_file()
                    and path.suffix.casefold() in _FONT_MEDIA_TYPES
                    and "librefranklin" in normalized_name
                ):
                    candidates.add(path)
        except OSError:
            continue

    ordered = sorted(candidates, key=lambda path: str(path).casefold())
    variable = [
        path
        for path in ordered
        if "variable" in path.stem.casefold() or "wght" in path.stem.casefold()
    ]
    if variable:
        # One upright and one italic variable face cover the full weight range.
        selected: list[Path] = []
        for style in ("normal", "italic"):
            match = next((path for path in variable if _font_style(path) == style), None)
            if match is not None:
                selected.append(match)
        return selected

    selected_by_face: dict[tuple[str, str], Path] = {}
    for path in ordered:
        selected_by_face.setdefault((_font_weight(path), _font_style(path)), path)
    return list(selected_by_face.values())


@lru_cache(maxsize=1)
def _embedded_libre_franklin_css() -> tuple[str, str]:
    """Return local ``@font-face`` CSS and a non-path-disclosing status note."""

    faces: list[str] = []
    for path in _find_libre_franklin_files():
        media_type, format_name = _FONT_MEDIA_TYPES[path.suffix.casefold()]
        data_uri = _file_data_uri(path, media_type)
        if data_uri is None:
            continue
        faces.append(
            "@font-face {"
            'font-family: "Libre Franklin";'
            f'src: url("{data_uri}") format("{format_name}");'
            f"font-weight: {_font_weight(path)};"
            f"font-style: {_font_style(path)};"
            "font-display: swap;"
            "}"
        )

    if faces:
        return (
            "\n".join(faces),
            "Libre Franklin embedded from local font data; no external font request.",
        )
    return (
        "",
        "Libre Franklin local font unavailable; safe system font fallback stack active.",
    )


def _brand_lockup_html() -> str:
    esd_logo = _file_data_uri(_ESD_LOGO_PATH, "image/png")
    uofsc_logo = _file_data_uri(_UOFSC_LOGO_PATH, "image/png")
    if esd_logo and uofsc_logo:
        return (
            '<div class="brand-lockup" aria-label="ESD Lab and University of South Carolina">'
            f'<img class="brand-logo" src="{esd_logo}" alt="ESD Lab">'
            '<span class="brand-divider" aria-hidden="true"></span>'
            f'<img class="brand-logo" src="{uofsc_logo}" '
            'alt="University of South Carolina">'
            "</div>"
        )
    return (
        '<div class="brand-lockup"><span class="brand-primary">ESD Lab</span>'
        '<span class="brand-divider" aria-hidden="true"></span>'
        '<span class="brand-secondary">University of South Carolina</span></div>'
    )


def _resolved_datetime(value: datetime | None) -> datetime:
    return value if value is not None else datetime.now(timezone.utc)


def timestamp_slug(value: datetime | None = None) -> str:
    """Return the required ``YYYYMMDD_HHMMSS`` timestamp."""

    return _resolved_datetime(value).strftime("%Y%m%d_%H%M%S")


def sanitize_filename_stem(value: Any, *, fallback: str = "export") -> str:
    """Return a lowercase, path-free filename stem.

    Only ASCII letters, digits, and underscores are retained.  This prevents
    path traversal when the result is used as a ZIP member name.
    """

    text = unicodedata.normalize("NFKD", _display_text(value))
    text = text.encode("ascii", errors="ignore").decode("ascii").casefold()
    text = _UNSAFE_FILENAME_RE.sub("_", text).strip("_")
    fallback_text = _UNSAFE_FILENAME_RE.sub(
        "_", unicodedata.normalize("NFKD", fallback).casefold()
    ).strip("_")
    return (text or fallback_text or "export")[:80].rstrip("_")


def _display_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        missing = pd.isna(value)
        if not hasattr(missing, "__len__") and bool(missing):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    try:
        return str(value)
    except Exception:  # pragma: no cover - custom objects may reject str()
        return ""


def _redact_text(value: Any) -> str:
    text = _display_text(value)
    text = _TOKEN_ASSIGNMENT_RE.sub(r"\1[REDACTED]", text)
    text = _BEARER_RE.sub(_REDACTED, text)
    return _REDCAP_TOKEN_RE.sub(_REDACTED, text)


def _is_sensitive_label(value: Any) -> bool:
    return _SENSITIVE_LABEL_RE.search(_display_text(value)) is not None


def _redact_scalar(value: Any, *, force: bool = False) -> Any:
    if force:
        return "" if _display_text(value) == "" else _REDACTED
    if isinstance(value, (str, bytes)):
        return _redact_text(value)
    return value


def _safe_frame(value: Any) -> pd.DataFrame:
    if value is None:
        frame = pd.DataFrame()
    elif isinstance(value, pd.DataFrame):
        frame = value.copy(deep=True)
    else:
        try:
            frame = pd.DataFrame(value).copy(deep=True)
        except Exception as exc:
            raise TypeError("table values must be convertible to a pandas DataFrame") from exc

    for column in frame.columns:
        force_redaction = _is_sensitive_label(column)
        frame[column] = frame[column].map(
            lambda cell, force=force_redaction: _redact_scalar(cell, force=force)
        )
    frame.columns = [_redact_text(column) for column in frame.columns]
    return frame


def _protect_csv_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    candidate = value.lstrip(" \t\r\n")
    if candidate.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def dataframe_to_csv_bytes(value: Any) -> bytes:
    """Return a UTF-8-BOM CSV with secret and spreadsheet-formula protection."""

    frame = _safe_frame(value)
    protected_columns = [
        _protect_csv_cell(_redact_text(column)) for column in frame.columns
    ]
    frame.columns = protected_columns
    for column in frame.columns:
        frame[column] = frame[column].map(_protect_csv_cell)
    csv_text = frame.to_csv(
        index=False,
        lineterminator="\n",
        quoting=csv.QUOTE_MINIMAL,
    )
    return b"\xef\xbb\xbf" + csv_text.encode("utf-8")


def _deduplicated_filename(filename: str, existing: set[str]) -> str:
    if filename not in existing:
        existing.add(filename)
        return filename
    stem, dot, suffix = filename.rpartition(".")
    index = 2
    while True:
        candidate = f"{stem}_{index}{dot}{suffix}" if dot else f"{filename}_{index}"
        if candidate not in existing:
            existing.add(candidate)
            return candidate
        index += 1


def build_csv_exports(
    tables: Mapping[str, Any] | None,
    *,
    prefix: str | None = None,
    generated_at: datetime | None = None,
) -> dict[str, bytes]:
    """Build timestamped CSV payloads for a mapping of table name to data."""

    when = _resolved_datetime(generated_at)
    timestamp = timestamp_slug(when)
    safe_prefix = sanitize_filename_stem(prefix) if prefix else ""
    exports: dict[str, bytes] = {}
    used_names: set[str] = set()
    for name, frame in (tables or {}).items():
        table_stem = sanitize_filename_stem(name, fallback="table")
        stem = f"{safe_prefix}_{table_stem}" if safe_prefix else table_stem
        filename = _deduplicated_filename(f"{stem}_{timestamp}.csv", used_names)
        exports[filename] = dataframe_to_csv_bytes(frame)
    return exports


def html_export_filename(
    title: Any,
    *,
    generated_at: datetime | None = None,
    filename_stem: str | None = None,
) -> str:
    stem = sanitize_filename_stem(filename_stem or title, fallback="metadata_watcher")
    return f"{stem}_{timestamp_slug(generated_at)}.html"


def zip_export_filename(
    title: Any,
    *,
    generated_at: datetime | None = None,
    filename_stem: str | None = None,
) -> str:
    stem = sanitize_filename_stem(filename_stem or title, fallback="metadata_watcher")
    return f"{stem}_{timestamp_slug(generated_at)}.zip"


def _redact_nested(value: Any, *, sensitive: bool = False) -> Any:
    """Redact credential-shaped values in a JSON-compatible Plotly payload."""

    if sensitive:
        return None if value is None else _REDACTED
    if isinstance(value, dict):
        return {
            key: _redact_nested(item, sensitive=_is_sensitive_label(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_nested(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _plotly_fragment(figure: Any, *, include_plotlyjs: bool, index: int) -> str:
    try:
        figure_json = pio.to_json(figure, validate=True, pretty=False)
        safe_figure = _redact_nested(json.loads(figure_json))
        return pio.to_html(
            safe_figure,
            full_html=False,
            include_plotlyjs=include_plotlyjs,
            config={"displaylogo": False, "responsive": True},
            default_width="100%",
            div_id=f"esd-figure-{index}",
            validate=True,
        )
    except Exception as exc:
        raise TypeError(f"figure {index + 1} is not a valid Plotly figure") from exc


_ESD_EXPORT_CSS = """
:root {
  color-scheme: light;
  --discovery-blue: #3366FF;
  --science-blue: #91BAF4;
  --cool-blue: #E6EEFC;
  --cool-white: #F4F4F6;
  --jet-black: #000000;
  --confident-orange: #F57F00;
  --firetruck-red: #D74E2D;
  --optimal-yellow: #F4DA26;
  --baby-pink: #F8B2B1;
  --soft-text: #5A6472;
  --line-soft: #EDF1F7;
  --blue-tint: #F1F5FF;
  --success: #12864B;
  --success-bg: #E7F7EE;
  --radius-card: 20px;
  --radius-small: 12px;
  --shadow-card: 0 1px 2px rgba(16,29,66,.05), 0 1px 1px rgba(16,29,66,.04);
  --shadow-panel: 0 6px 20px rgba(30,58,138,.08), 0 1px 3px rgba(30,58,138,.06);
  font-family: "Libre Franklin", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 15px;
  line-height: 1.45;
  color: var(--jet-black);
  background: var(--cool-white);
}
* { box-sizing: border-box; }
body { margin: 0; min-width: 320px; background: var(--cool-white); }
.brand-band {
  position: sticky; top: 0; z-index: 10; min-height: 74px;
  display: flex; align-items: center; justify-content: space-between; gap: 20px;
  padding: 14px clamp(18px, 3vw, 40px); background: rgba(255,255,255,.94);
  border-bottom: 1px solid var(--cool-blue); box-shadow: var(--shadow-card);
}
.brand-lockup { display: flex; align-items: center; gap: 14px; font-weight: 800; }
.brand-logo { display: block; width: auto; height: 32px; object-fit: contain; }
.brand-primary { color: var(--discovery-blue); font-size: 18px; letter-spacing: -.02em; }
.brand-divider { width: 1px; height: 28px; background: var(--cool-blue); }
.brand-secondary { font-size: 13px; }
.export-meta { color: var(--soft-text); font-size: 12px; font-variant-numeric: tabular-nums; }
.page { width: min(100%, 1680px); margin: 0 auto; padding: 24px; }
.page-head { margin: 4px 0 18px; }
.eyebrow { color: var(--discovery-blue); font-size: 11px; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
h1 { margin: 7px 0 0; font-size: 24px; line-height: 1.15; font-weight: 800; letter-spacing: -.022em; }
.subtitle { margin: 8px 0 0; color: var(--soft-text); font-size: 13px; }
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; margin-bottom: 16px; }
.kpi-card { min-height: 116px; padding: 18px; background: #fff; border: 1px solid var(--cool-blue); border-radius: var(--radius-card); box-shadow: var(--shadow-card); }
.kpi-label { color: var(--soft-text); font-size: 11px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.kpi-value { margin-top: 12px; color: var(--jet-black); font-size: 30px; line-height: 1.05; font-weight: 900; letter-spacing: -.03em; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.section-heading { margin: 22px 0 12px; font-size: 15px; font-weight: 800; }
.figure-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr)); gap: 14px; }
.panel { min-width: 0; overflow: hidden; background: #fff; border: 1px solid var(--cool-blue); border-radius: var(--radius-card); box-shadow: var(--shadow-panel); }
.panel-head { padding: 16px 20px; border-bottom: 1px solid var(--cool-blue); }
.panel-head h2 { margin: 0; font-size: 15px; line-height: 1.25; font-weight: 800; }
.figure-body { min-height: 300px; padding: 10px 14px 14px; }
.table-panel { margin-bottom: 14px; }
.table-wrap { width: 100%; overflow-x: auto; padding: 0 20px 18px; }
table.dataframe { width: 100%; border-collapse: collapse; font-size: 13px; }
table.dataframe th { padding: 10px 12px; text-align: left; color: var(--soft-text); background: var(--cool-blue); font-size: 11px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; white-space: nowrap; }
table.dataframe th:first-child { border-radius: 10px 0 0 10px; }
table.dataframe th:last-child { border-radius: 0 10px 10px 0; }
table.dataframe td { padding: 12px; border-bottom: 1px solid var(--line-soft); vertical-align: top; overflow-wrap: anywhere; }
table.dataframe tbody tr:hover { background: var(--blue-tint); }
.empty { padding: 18px 20px; color: var(--soft-text); font-size: 13px; }
.footer { margin-top: 20px; padding: 18px 0 4px; border-top: 1px solid var(--cool-blue); color: var(--soft-text); font-size: 12px; font-variant-numeric: tabular-nums; }
@media (max-width: 760px) {
  .brand-band { align-items: flex-start; flex-direction: column; gap: 8px; }
  .brand-lockup { gap: 10px; }
  .brand-logo { height: 27px; }
  .brand-divider { height: 24px; }
  .page { padding: 16px 14px; }
  .kpi-grid, .figure-grid { grid-template-columns: 1fr; }
  .kpi-card { min-height: auto; }
  .figure-body { min-height: 240px; padding: 8px; }
  .table-wrap { padding: 0 14px 14px; }
}
@media print {
  .brand-band { position: static; }
  .page { width: 100%; padding: 12px; }
  .panel, .kpi-card { box-shadow: none; break-inside: avoid; }
}
""".strip()


def build_standalone_html(
    title: Any,
    kpis: Mapping[str, Any] | None = None,
    tables: Mapping[str, Any] | None = None,
    figures: Mapping[str, Any] | None = None,
    *,
    subtitle: Any | None = None,
    generated_at: datetime | None = None,
) -> str:
    """Render one escaped, self-contained ESD dashboard document.

    Plotly's JavaScript runtime is included inline with the first figure only;
    subsequent figure fragments reuse that runtime.  No CDN is referenced.
    """

    when = _resolved_datetime(generated_at)
    timestamp_label = when.isoformat(timespec="seconds")
    safe_title = escape(_redact_text(title), quote=True)
    subtitle_text = _redact_text(subtitle)
    safe_subtitle = escape(subtitle_text, quote=True) if subtitle_text else ""
    font_face_css, font_status = _embedded_libre_franklin_css()
    embedded_css = f"{font_face_css}\n{_ESD_EXPORT_CSS}" if font_face_css else _ESD_EXPORT_CSS
    favicon = _file_data_uri(_FAVICON_PATH, "image/png")

    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="referrer" content="no-referrer">',
        f"<title>{safe_title}</title>",
        f"<!-- {font_status} -->",
    ]
    if favicon:
        parts.append(f'<link rel="icon" type="image/png" href="{favicon}">')
    parts.extend(
        [
            f"<style>{embedded_css}</style>",
            "</head>",
            "<body>",
            '<header class="brand-band">',
            _brand_lockup_html(),
            f'<div class="export-meta">Exported {escape(timestamp_label, quote=True)}</div>',
            "</header>",
            '<main class="page">',
            '<section class="page-head">',
            '<div class="eyebrow">Groundtruth metadata inventory</div>',
            f"<h1>{safe_title}</h1>",
        ]
    )
    if safe_subtitle:
        parts.append(f'<p class="subtitle">{safe_subtitle}</p>')
    parts.append("</section>")

    if kpis:
        parts.append('<section class="kpi-grid" aria-label="Summary metrics">')
        for label, value in kpis.items():
            safe_label = escape(_redact_text(label), quote=True)
            display_value = _REDACTED if _is_sensitive_label(label) else _redact_text(value)
            safe_value = escape(display_value, quote=True)
            parts.extend(
                [
                    '<article class="kpi-card">',
                    f'<div class="kpi-label">{safe_label}</div>',
                    f'<div class="kpi-value">{safe_value}</div>',
                    "</article>",
                ]
            )
        parts.append("</section>")

    if figures:
        parts.append('<h2 class="section-heading">Visualizations</h2>')
        parts.append('<section class="figure-grid">')
        for index, (name, figure) in enumerate(figures.items()):
            safe_name = escape(_redact_text(name), quote=True)
            fragment = _plotly_fragment(
                figure,
                include_plotlyjs=index == 0,
                index=index,
            )
            parts.extend(
                [
                    '<article class="panel">',
                    f'<div class="panel-head"><h2>{safe_name}</h2></div>',
                    f'<div class="figure-body">{fragment}</div>',
                    "</article>",
                ]
            )
        parts.append("</section>")

    if tables:
        parts.append('<h2 class="section-heading">Tables</h2>')
        for index, (name, value) in enumerate(tables.items()):
            safe_name = escape(_redact_text(name), quote=True)
            frame = _safe_frame(value)
            table_html = frame.to_html(
                index=False,
                border=0,
                classes="data-table",
                escape=True,
                na_rep="",
                justify="left",
                table_id=f"esd-table-{index}",
            )
            parts.extend(
                [
                    '<article class="panel table-panel">',
                    f'<div class="panel-head"><h2>{safe_name}</h2></div>',
                    f'<div class="table-wrap">{table_html}</div>',
                    "</article>",
                ]
            )

    parts.extend(
        [
            f'<footer class="footer">Generated {escape(timestamp_label, quote=True)}</footer>',
            "</main>",
            "</body>",
            "</html>",
        ]
    )
    return "\n".join(parts)


def build_html_export(
    title: Any,
    kpis: Mapping[str, Any] | None = None,
    tables: Mapping[str, Any] | None = None,
    figures: Mapping[str, Any] | None = None,
    *,
    subtitle: Any | None = None,
    generated_at: datetime | None = None,
    filename_stem: str | None = None,
) -> ExportArtifact:
    """Return a timestamped standalone HTML download artifact."""

    when = _resolved_datetime(generated_at)
    document = build_standalone_html(
        title,
        kpis,
        tables,
        figures,
        subtitle=subtitle,
        generated_at=when,
    )
    return ExportArtifact(
        filename=html_export_filename(
            title, generated_at=when, filename_stem=filename_stem
        ),
        media_type="text/html; charset=utf-8",
        data=document.encode("utf-8"),
    )


def build_export_bundle(
    title: Any,
    kpis: Mapping[str, Any] | None = None,
    tables: Mapping[str, Any] | None = None,
    figures: Mapping[str, Any] | None = None,
    *,
    subtitle: Any | None = None,
    generated_at: datetime | None = None,
    filename_stem: str | None = None,
) -> bytes:
    """Return an in-memory ZIP containing the standalone HTML and all CSVs."""

    when = _resolved_datetime(generated_at)
    html_artifact = build_html_export(
        title,
        kpis,
        tables,
        figures,
        subtitle=subtitle,
        generated_at=when,
        filename_stem=filename_stem,
    )
    csv_exports = build_csv_exports(
        tables,
        prefix=filename_stem or sanitize_filename_stem(title),
        generated_at=when,
    )

    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(html_artifact.filename, html_artifact.data)
        for filename, data in csv_exports.items():
            archive.writestr(filename, data)
    return buffer.getvalue()


def build_zip_export(
    title: Any,
    kpis: Mapping[str, Any] | None = None,
    tables: Mapping[str, Any] | None = None,
    figures: Mapping[str, Any] | None = None,
    *,
    subtitle: Any | None = None,
    generated_at: datetime | None = None,
    filename_stem: str | None = None,
) -> ExportArtifact:
    """Return a timestamped ZIP download artifact backed entirely by memory."""

    when = _resolved_datetime(generated_at)
    return ExportArtifact(
        filename=zip_export_filename(
            title, generated_at=when, filename_stem=filename_stem
        ),
        media_type="application/zip",
        data=build_export_bundle(
            title,
            kpis,
            tables,
            figures,
            subtitle=subtitle,
            generated_at=when,
            filename_stem=filename_stem,
        ),
    )


__all__ = [
    "ExportArtifact",
    "build_csv_exports",
    "build_export_bundle",
    "build_html_export",
    "build_standalone_html",
    "build_zip_export",
    "dataframe_to_csv_bytes",
    "html_export_filename",
    "sanitize_filename_stem",
    "timestamp_slug",
    "zip_export_filename",
]
