"""ESD Lab REDCap Metadata Watcher.

Run with:
    streamlit run app.py

Tokens remain in Streamlit session state only. The application makes read-only,
explicitly paced API calls on Connect and Refresh; ordinary UI reruns reuse the
in-session snapshots and never call REDCap.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path
import time
from typing import Any, Mapping
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import streamlit as st

from charts import (
    branching_figure,
    discrepancy_figure,
    event_coverage_figure,
    field_type_figure,
    instrument_count_figure,
    missing_label_figure,
    overlap_figure,
    prefix_count_figure,
)
from exports import (
    build_csv_exports,
    build_html_export,
    build_zip_export,
    dataframe_to_csv_bytes,
    timestamp_slug,
)
from redcap_client import ProjectSnapshot, fetch_projects, pycap_version
from watcher_core import (
    build_missing_profile,
    build_overlap_summary,
    build_project_summary,
    compare_projects,
)


# ---- CONFIG (edit here) -------------------------------------------------
REDCAP_API_URL = "https://redcap.research.sc.edu/api/"
PROJECT_REGISTRY = {
    "NANO": {
        "pid": 4218,
        "label": "NANO Study Surveys",
        "reference": True,
    },
    "IPSA": {
        "pid": 1289,
        "label": "IPSA Study Surveys and Data Entry",
        "reference": False,
    },
    "ACTION": {
        "pid": 1556,
        "label": "ACTION Study",
        "reference": False,
    },
}
REFERENCE_PROJECT = "NANO"
DOE_DOC_PATTERNS = [
    r"\bdoe\b",
    r"\bdoc\b",
    r"date_of_eval",
    r"date_of_eval\w*",
    r"eval_date",
    r"assess.*date",
    r"visit_date",
]
OUTPUT_DIR = "output"  # Naming compatibility; public exports are download-only.
SIMILARITY_THRESHOLD = None  # Inference is intentionally disabled.
REDCAP_MIN_REQUEST_INTERVAL_SECONDS = 1.25
REFRESH_COOLDOWN_SECONDS = 60
RATE_LIMIT_RETRY_SECONDS = 15
# ------------------------------------------------------------------------


APP_DIR = Path(__file__).resolve().parent
ASSET_DIR = APP_DIR / "assets"

_LIBRE_FRANKLIN_PATH = ASSET_DIR / "fonts" / "LibreFranklin-VariableFont_wght.ttf"
if _LIBRE_FRANKLIN_PATH.exists():
    _font_payload = base64.b64encode(_LIBRE_FRANKLIN_PATH.read_bytes()).decode("ascii")
    FONT_FACE_CSS = (
        "@font-face { font-family: 'Libre Franklin'; "
        "src: url('data:font/ttf;base64,"
        + _font_payload
        + "') format('truetype'); font-style: normal; font-weight: 100 900; "
        "font-display: swap; }"
    )
else:
    FONT_FACE_CSS = (
        "@import url('https://fonts.googleapis.com/css2?family=Libre+Franklin:"
        "wght@400;500;600;700;800;900&display=swap');"
    )

st.set_page_config(
    page_title="ESD Lab REDCap Metadata Watcher",
    page_icon=str(ASSET_DIR / "favicon.png"),
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "About": "Read-only REDCap metadata inventory for ESD Lab studies.",
    },
)


THEME_CSS = """
<style>
__FONT_FACE_CSS__
:root {
  --esd-blue: #3366FF;
  --esd-science: #91BAF4;
  --esd-cool-blue: #E6EEFC;
  --esd-cool-white: #F4F4F6;
  --esd-black: #000000;
  --esd-soft: #5A6472;
  --esd-orange: #F57F00;
  --esd-red: #D74E2D;
  --esd-yellow: #F4DA26;
  --esd-pink: #F8B2B1;
  --esd-green: #12864B;
  --esd-green-bg: #E7F7EE;
  --esd-line: #E6EEFC;
  --esd-line-soft: #EDF1F7;
  --esd-radius: 20px;
  --esd-radius-sm: 12px;
  --esd-shadow: 0 1px 2px rgba(16,29,66,.05), 0 1px 1px rgba(16,29,66,.04);
  --esd-shadow-panel: 0 6px 20px rgba(30,58,138,.08), 0 1px 3px rgba(30,58,138,.06);
}
html, body, [class*="css"] { font-family: 'Libre Franklin', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
.stApp { background: var(--esd-cool-white); color: var(--esd-black); }
[data-testid="stAppViewContainer"] > .main { background: var(--esd-cool-white); }
.block-container { max-width: 1680px; padding: 4.25rem 1.5rem 3rem; }
h1, h2, h3, h4, h5, h6 { font-family: 'Libre Franklin', sans-serif; color: var(--esd-black); letter-spacing: -.02em; }
p, label, li, div { font-family: 'Libre Franklin', sans-serif; }
[data-testid="stSidebar"] { background: #FFFFFF; border-right: 1px solid var(--esd-line); }
[data-testid="stSidebar"] .block-container { padding-top: 1.25rem; }
.esd-header {
  display: flex; align-items: center; justify-content: space-between; gap: 24px;
  min-height: 74px; padding: 14px 18px; margin: -2px 0 14px;
  background: rgba(255,255,255,.96); border: 1px solid var(--esd-line);
  border-radius: var(--esd-radius); box-shadow: var(--esd-shadow);
}
.esd-brand { display: flex; align-items: center; gap: 14px; min-width: 0; }
.esd-brand img { display: block; width: auto; height: 32px; object-fit: contain; }
.esd-brand-divider { width: 1px; height: 30px; background: var(--esd-line); flex: 0 0 auto; }
.esd-title-wrap { min-width: 0; }
.esd-eyebrow { color: var(--esd-blue); font-size: 11px; line-height: 1.2; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
.esd-title { margin-top: 3px; color: var(--esd-black); font-size: 24px; line-height: 1.15; font-weight: 800; letter-spacing: -.025em; }
.esd-header-meta { display: flex; align-items: center; justify-content: flex-end; gap: 10px; flex-wrap: wrap; color: var(--esd-soft); font-size: 12px; text-align: right; }
.esd-header-pill { padding: 7px 11px; border-radius: 999px; background: #F1F5FF; color: var(--esd-blue); font-size: 11px; font-weight: 800; letter-spacing: .04em; text-transform: uppercase; white-space: nowrap; }
.status-grid { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 12px; margin: 0 0 18px; }
.status-card { min-width: 0; padding: 15px 16px; background: #fff; border: 1px solid var(--esd-line); border-radius: var(--esd-radius-sm); box-shadow: var(--esd-shadow); }
.status-line { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.status-study { min-width: 0; font-size: 14px; font-weight: 800; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status-badge { flex: 0 0 auto; display: inline-flex; align-items: center; min-height: 26px; padding: 4px 9px; border-radius: 999px; font-size: 10px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; }
.status-badge.connected { color: var(--esd-green); background: var(--esd-green-bg); }
.status-badge.limited { color: #6B5A00; background: #FFF8C9; }
.status-badge.failed { color: var(--esd-red); background: #FFF0ED; }
.status-badge.pending { color: var(--esd-soft); background: var(--esd-cool-white); }
.status-detail { margin-top: 7px; color: var(--esd-soft); font-size: 11px; line-height: 1.4; overflow-wrap: anywhere; }
.gate-copy { max-width: 760px; margin: 5px 0 18px; }
.gate-copy h1 { margin: 0 0 8px; font-size: clamp(26px, 3.2vw, 42px); font-weight: 800; }
.gate-copy p { margin: 0; color: var(--esd-soft); font-size: 15px; line-height: 1.55; }
.kpi-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; margin: 8px 0 18px; }
.kpi-card { min-width: 0; min-height: 138px; padding: 16px; background: #fff; border: 1px solid var(--esd-line); border-radius: var(--esd-radius); box-shadow: var(--esd-shadow); }
.kpi-icon { width: 38px; height: 38px; margin-bottom: 13px; display: flex; align-items: center; justify-content: center; border-radius: 11px; background: var(--esd-cool-blue); }
.kpi-icon img { width: 23px; height: 23px; object-fit: contain; }
.kpi-label { color: var(--esd-soft); font-size: 10px; font-weight: 800; line-height: 1.25; letter-spacing: .08em; text-transform: uppercase; }
.kpi-value { margin-top: 6px; color: var(--esd-black); font-size: clamp(22px, 2.2vw, 30px); line-height: 1.05; font-weight: 900; letter-spacing: -.03em; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.kpi-note { margin-top: 5px; color: var(--esd-soft); font-size: 10px; line-height: 1.3; }
.section-label { margin: 8px 0 4px; color: var(--esd-blue); font-size: 11px; line-height: 1.2; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
.section-title { margin: 0 0 2px; font-size: 18px; line-height: 1.25; font-weight: 800; }
.section-subtitle { margin: 0 0 10px; color: var(--esd-soft); font-size: 12px; }
.inventory-strip { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; padding: 11px 14px; margin: 2px 0 14px; border: 1px solid var(--esd-line); border-radius: var(--esd-radius-sm); background: #fff; color: var(--esd-soft); font-size: 12px; }
.inventory-strip b { color: var(--esd-black); }
[data-testid="stVerticalBlockBorderWrapper"] { border-color: var(--esd-line) !important; border-radius: var(--esd-radius) !important; box-shadow: var(--esd-shadow-panel); background: #fff; }
[data-testid="stMetric"] { background: #fff; border: 1px solid var(--esd-line); border-radius: var(--esd-radius); padding: 14px; box-shadow: var(--esd-shadow); }
[data-testid="stDataFrame"] { border: 1px solid var(--esd-line); border-radius: var(--esd-radius-sm); overflow: hidden; background: #fff; }
[data-baseweb="tab-list"] { gap: 4px; background: #fff; border: 1px solid var(--esd-line); border-radius: 999px; padding: 4px; width: fit-content; max-width: 100%; overflow-x: auto; }
[data-baseweb="tab"] { height: 38px; padding: 0 16px; border-radius: 999px; color: var(--esd-soft); font-size: 12px; font-weight: 700; }
[aria-selected="true"][data-baseweb="tab"] { background: #F1F5FF; color: var(--esd-blue); }
[data-baseweb="tab-highlight"], [data-baseweb="tab-border"] { display: none; }
.stButton > button, .stDownloadButton > button { min-height: 42px; border-radius: 999px; border-color: var(--esd-science); font-weight: 700; }
.stButton > button[kind="primary"] { background: var(--esd-blue); border-color: var(--esd-blue); }
.stButton > button:focus-visible, .stDownloadButton > button:focus-visible { outline: 3px solid rgba(51,102,255,.35); outline-offset: 2px; }
[data-testid="stTextInput"] input, [data-testid="stSelectbox"] > div > div, [data-testid="stMultiSelect"] > div > div { border-radius: var(--esd-radius-sm); }
.footer-note { margin-top: 26px; padding-top: 16px; border-top: 1px solid var(--esd-line); color: var(--esd-soft); font-size: 11px; line-height: 1.5; }
@media (max-width: 1180px) { .kpi-grid { grid-template-columns: repeat(3, minmax(0,1fr)); } }
@media (max-width: 760px) {
  .block-container { padding: 4.25rem .85rem 2rem; }
  .esd-header { align-items: flex-start; flex-direction: column; padding: 14px; }
  .esd-brand { flex-wrap: wrap; }
  .esd-brand img { height: 27px; }
  .esd-title { font-size: 20px; }
  .esd-header-meta { justify-content: flex-start; text-align: left; }
  .status-grid { grid-template-columns: 1fr; }
  .kpi-grid { grid-template-columns: repeat(2, minmax(0,1fr)); }
  .kpi-card { min-height: 124px; padding: 14px; }
  [data-baseweb="tab-list"] {
    display: flex; flex-wrap: wrap; width: 100%; overflow-x: visible;
    border-radius: var(--esd-radius-sm);
  }
  [data-baseweb="tab"] { flex: 1 1 auto; justify-content: center; padding: 0 12px; }
}
@media (max-width: 430px) { .kpi-grid { grid-template-columns: 1fr; } }
</style>
""".replace("__FONT_FACE_CSS__", FONT_FACE_CSS)

st.markdown(THEME_CSS, unsafe_allow_html=True)


@lru_cache(maxsize=16)
def _image_data_uri(filename: str) -> str:
    path = ASSET_DIR / filename
    if not path.exists():
        return ""
    mime = "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _fmt_timestamp(value: datetime | None) -> str:
    if not value:
        return "Not refreshed"
    local = value.astimezone()
    return local.strftime("%b %d, %Y · %I:%M:%S %p %Z")


def _header(snapshots: Mapping[str, ProjectSnapshot]) -> None:
    connected = sum(snapshot.connected for snapshot in snapshots.values())
    latest = max((snapshot.fetched_at for snapshot in snapshots.values()), default=None)
    logo = _image_data_uri("esd-logo.png")
    uofsc = _image_data_uri("uofsc-logo.png")
    logo_html = f'<img src="{logo}" alt="Early Social Development Lab">' if logo else ""
    uofsc_html = f'<img src="{uofsc}" alt="University of South Carolina">' if uofsc else ""
    st.markdown(
        f"""
        <header class="esd-header">
          <div class="esd-brand">
            {logo_html}
            <span class="esd-brand-divider" aria-hidden="true"></span>
            {uofsc_html}
            <div class="esd-title-wrap">
              <div class="esd-eyebrow">Metadata governance</div>
              <div class="esd-title">REDCap Metadata Watcher</div>
            </div>
          </div>
          <div class="esd-header-meta">
            <span>{escape(_fmt_timestamp(latest))}</span>
            <span class="esd-header-pill">{connected} of {len(PROJECT_REGISTRY)} connected</span>
          </div>
        </header>
        """,
        unsafe_allow_html=True,
    )


def _status_panel(
    snapshots: Mapping[str, ProjectSnapshot], attempted: bool
) -> None:
    cards: list[str] = []
    status_labels = {
        "connected": "Connected",
        "limited": "Limited coverage",
        "failed": "Connection failed",
    }
    for key, config in PROJECT_REGISTRY.items():
        snapshot = snapshots.get(key)
        if snapshot:
            state = snapshot.status
            label = status_labels.get(state, state.title())
            detail = snapshot.status_detail
        else:
            state = "failed" if attempted else "pending"
            label = "Not connected" if attempted else "Awaiting token"
            detail = "No token was supplied" if attempted else f"PID {config['pid']}"
        cards.append(
            f'<article class="status-card">'
            f'<div class="status-line"><div class="status-study">{escape(key)}</div>'
            f'<span class="status-badge {escape(state)}">{escape(label)}</span></div>'
            f'<div class="status-detail">{escape(detail)}</div></article>'
        )
    st.markdown(
        '<section class="status-grid" aria-label="REDCap connection status">'
        + "".join(cards)
        + "</section>",
        unsafe_allow_html=True,
    )


def _initialize_state() -> None:
    defaults: dict[str, Any] = {
        "snapshots": {},
        "connection_attempted": False,
        "last_fetch_epoch": 0.0,
        "export_generated_at": None,
        "fetch_generation": 0,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _clear_session() -> None:
    for key in list(st.session_state):
        if key.startswith("token_input_") or key in {
            "snapshots",
            "connection_attempted",
            "last_fetch_epoch",
            "export_generated_at",
            "fetch_generation",
        }:
            del st.session_state[key]
    _initialize_state()
    st.rerun()


def _tokens_from_inputs() -> dict[str, str]:
    return {
        key: str(st.session_state.get(f"token_input_{key}", "")).strip()
        for key in PROJECT_REGISTRY
    }


def _fetch(include_record_count: bool) -> None:
    tokens = _tokens_from_inputs()
    selected = {key: token for key, token in tokens.items() if token}
    if not selected:
        st.warning("Enter at least one project token before connecting.")
        return

    status = st.status("Connecting to the selected REDCap projects…", expanded=True)

    def progress(project: str, index: int, total: int) -> None:
        status.write(f"Reading {project} metadata ({index} of {total})")

    try:
        snapshots = fetch_projects(
            tokens=selected,
            registry=PROJECT_REGISTRY,
            api_url=REDCAP_API_URL,
            doe_doc_patterns=DOE_DOC_PATTERNS,
            include_record_count=include_record_count,
            minimum_interval_seconds=REDCAP_MIN_REQUEST_INTERVAL_SECONDS,
            rate_limit_retry_seconds=RATE_LIMIT_RETRY_SECONDS,
            progress=progress,
        )
    except Exception as exc:
        status.update(label="The metadata refresh did not complete", state="error")
        st.error(f"Refresh stopped: {escape(str(exc)[:360])}")
        return

    st.session_state.snapshots = snapshots
    st.session_state.connection_attempted = True
    st.session_state.last_fetch_epoch = time.time()
    st.session_state.export_generated_at = datetime.now(timezone.utc)
    st.session_state.fetch_generation += 1
    connected = sum(snapshot.connected for snapshot in snapshots.values())
    status.update(
        label=f"Refresh complete · {connected} project{'s' if connected != 1 else ''} connected",
        state="complete",
        expanded=False,
    )
    time.sleep(0.25)
    st.rerun()


def _landing() -> None:
    st.markdown(
        """
        <section class="gate-copy">
          <div class="esd-eyebrow">Live read-only inventory</div>
          <h1>Connect the study metadata you need to inspect.</h1>
          <p>Enter one to three REDCap API tokens. Tokens stay in this browser session, are never written to disk, and are used only for read-only metadata exports.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )
    columns = st.columns(3)
    for column, (key, config) in zip(columns, PROJECT_REGISTRY.items()):
        with column:
            with st.container(border=True):
                st.markdown(f"**{key}**")
                st.caption(f"{config['label']} · PID {config['pid']}")
                st.text_input(
                    f"{key} API token",
                    type="password",
                    key=f"token_input_{key}",
                    autocomplete="off",
                    placeholder="Paste token",
                )

    include_count = st.checkbox(
        "Run the optional lightweight record-count check",
        value=False,
        help="Requests only the record identifier field. Metadata analysis does not depend on this check.",
    )
    action, note = st.columns([1, 4], vertical_alignment="center")
    with action:
        if st.button("Connect", type="primary", width="stretch"):
            _fetch(include_count)
    with note:
        st.caption(
            "API reads are serialized and paced. Normal filtering, tab changes, and downloads do not call REDCap."
        )

    with st.expander("Configuration summary"):
        config_frame = pd.DataFrame(
            [
                {
                    "Study": key,
                    "Project": config["label"],
                    "PID": config["pid"],
                    "Default reference": bool(config["reference"]),
                }
                for key, config in PROJECT_REGISTRY.items()
            ]
        )
        st.code(REDCAP_API_URL, language=None)
        st.dataframe(config_frame, hide_index=True, width="stretch")


def _sidebar_filters(
    connected: Mapping[str, ProjectSnapshot], generation: int
) -> dict[str, Any]:
    st.sidebar.image(str(ASSET_DIR / "esd-logo.png"), width=190)
    st.sidebar.markdown("### Inventory filters")
    project_options = list(connected)
    selected_projects = st.sidebar.multiselect(
        "Projects",
        project_options,
        default=project_options,
        key=f"filter_projects_{generation}",
    )
    active_frames = [connected[key].metadata for key in selected_projects]
    instrument_options = sorted(
        {
            str(value)
            for frame in active_frames
            if "form_name" in frame.columns
            for value in frame["form_name"].dropna().astype(str)
            if str(value).strip()
        }
    )
    field_type_options = sorted(
        {
            str(value)
            for frame in active_frames
            if "field_type" in frame.columns
            for value in frame["field_type"].dropna().astype(str)
            if str(value).strip()
        }
    )
    instruments = st.sidebar.multiselect(
        "Instruments", instrument_options, key=f"filter_instruments_{generation}"
    )
    field_types = st.sidebar.multiselect(
        "Field types", field_type_options, key=f"filter_types_{generation}"
    )
    mismatch_categories = st.sidebar.multiselect(
        "Mismatch categories",
        [
            "ALIGNED",
            "MISSING_VS_REFERENCE",
            "PARTIAL_PRESENCE",
            "TYPE_MISMATCH",
            "LABEL_MISMATCH",
            "INSTRUMENT_MISMATCH",
            "VALIDATION_GAP",
        ],
        key=f"filter_mismatch_{generation}",
    )
    doe_doc = st.sidebar.selectbox(
        "DOE/DOC flag",
        ["All fields", "DOE/DOC only", "Exclude DOE/DOC"],
        key=f"filter_doe_{generation}",
    )
    missing_label = st.sidebar.selectbox(
        "Missing-label status",
        ["All fields", "Blank labels only", "Labels present only"],
        key=f"filter_missing_{generation}",
    )
    st.sidebar.divider()
    st.sidebar.caption(
        "Filters update the visible inventory only. API data is fetched only on Connect or Refresh."
    )
    return {
        "projects": selected_projects,
        "instruments": instruments,
        "field_types": field_types,
        "mismatch_categories": mismatch_categories,
        "doe_doc": doe_doc,
        "missing_label": missing_label,
    }


def _apply_field_filters(frame: pd.DataFrame, filters: Mapping[str, Any]) -> pd.DataFrame:
    result = frame.copy()
    if filters["instruments"] and "form_name" in result.columns:
        result = result[result["form_name"].isin(filters["instruments"])]
    if filters["field_types"] and "field_type" in result.columns:
        result = result[result["field_type"].isin(filters["field_types"])]
    if filters["doe_doc"] != "All fields" and "is_doe_doc" in result.columns:
        desired = filters["doe_doc"] == "DOE/DOC only"
        result = result[result["is_doe_doc"].fillna(False).astype(bool) == desired]
    if filters["missing_label"] != "All fields" and "missing_label" in result.columns:
        desired = filters["missing_label"] == "Blank labels only"
        result = result[result["missing_label"].fillna(False).astype(bool) == desired]
    return result.copy()


def _summary_for(snapshot: ProjectSnapshot) -> dict[str, Any]:
    return build_project_summary(
        snapshot.metadata,
        project_info=snapshot.project_info,
        instruments=snapshot.instruments,
        events=snapshot.events,
        repeating=snapshot.repeating,
    )


def _summary_kpis(snapshot: ProjectSnapshot) -> dict[str, tuple[Any, str]]:
    summary = _summary_for(snapshot)
    events = summary["event_count"] if summary["is_longitudinal"] else "Not longitudinal"
    kpis: dict[str, tuple[Any, str]] = {
        "Instruments": (summary["instrument_count"], "Defined forms"),
        "Design fields": (summary["design_field_count"], "Metadata rows"),
        "Export fields": (summary["export_field_count"], "Checkbox expansion included"),
        "Events": (events, "Project structure"),
        "Required": (summary["required_field_count"], "Required-field flag"),
        "Branching": (summary["branching_field_count"], "Branching logic present"),
        "Validated": (summary["validated_field_count"], "Validation type present"),
        "Matrix fields": (
            summary["matrix_field_count"],
            f"{summary['matrix_group_count']} matrix groups",
        ),
        "Identifiers": (summary["identifier_field_count"], "Identifier flag"),
        "DOE / DOC": (summary["doe_doc_field_count"], "Configured pattern match"),
        "Blank labels": (summary["missing_label_count"], "Empty after HTML stripping"),
        "Repeating": (
            summary["repeating_definition_count"],
            "Instrument or event definitions",
        ),
    }
    if snapshot.record_count is not None:
        kpis["Record count"] = (snapshot.record_count, "Optional identifier-only check")
    return kpis


def _render_kpis(kpis: Mapping[str, tuple[Any, str]]) -> None:
    icons = [
        "icon-checklist-blue.png",
        "icon-bar-chart-blue.png",
        "icon-growth-chart-blue.png",
        "icon-brain-connections-blue.png",
        "icon-waveform-blue.png",
    ]
    cards: list[str] = []
    for index, (label, (value, note)) in enumerate(kpis.items()):
        icon = _image_data_uri(icons[index % len(icons)])
        icon_html = f'<img src="{icon}" alt="">' if icon else ""
        cards.append(
            f'<article class="kpi-card">'
            f'<div class="kpi-icon" aria-hidden="true">{icon_html}</div>'
            f'<div class="kpi-label">{escape(str(label))}</div>'
            f'<div class="kpi-value">{escape(str(value))}</div>'
            f'<div class="kpi-note">{escape(str(note))}</div></article>'
        )
    st.markdown(
        '<section class="kpi-grid" aria-label="Summary metrics">'
        + "".join(cards)
        + "</section>",
        unsafe_allow_html=True,
    )


def _chart_panel(title: str, subtitle: str, figure: Any, *, chart_key: str) -> None:
    with st.container(border=True):
        st.markdown(f'<div class="section-label">{escape(title)}</div>', unsafe_allow_html=True)
        st.caption(subtitle)
        st.plotly_chart(
            figure,
            use_container_width=True,
            key=chart_key,
            config={"displaylogo": False, "responsive": True},
        )


def _detail_table(frame: pd.DataFrame, *, key: str, generation: int) -> None:
    search = st.text_input(
        "Search field name or label",
        key=f"search_{key}_{generation}",
        placeholder="Search visible metadata",
    ).strip()
    visible = frame.copy()
    if search:
        query = search.casefold()
        names = visible.get("field_name", pd.Series("", index=visible.index)).astype(str)
        labels = visible.get("field_label", pd.Series("", index=visible.index)).astype(str)
        visible = visible[
            names.str.casefold().str.contains(query, regex=False)
            | labels.str.casefold().str.contains(query, regex=False)
        ]

    page_size = st.selectbox(
        "Rows per page",
        [100, 200],
        key=f"page_size_{key}_{generation}",
        label_visibility="collapsed",
    )
    pages = max(1, (len(visible) + page_size - 1) // page_size)
    page_key = f"page_{key}_{generation}"
    if int(st.session_state.get(page_key, 1)) > pages:
        st.session_state[page_key] = 1
    page = st.number_input(
        "Page",
        min_value=1,
        max_value=pages,
        value=1,
        step=1,
        key=page_key,
    )
    start = (int(page) - 1) * page_size
    page_frame = visible.iloc[start : start + page_size].copy()
    st.caption(f"Showing {start + 1 if len(visible) else 0}–{min(start + page_size, len(visible))} of {len(visible)} visible rows")
    columns = [
        "field_name",
        "export_field_name",
        "form_name",
        "field_type",
        "field_label",
        "is_required",
        "has_branching",
        "is_validated",
        "is_identifier",
        "is_matrix",
        "is_doe_doc",
        "missing_label",
        "action_tags",
    ]
    columns = [column for column in columns if column in page_frame.columns]
    st.dataframe(
        page_frame[columns],
        hide_index=True,
        width="stretch",
        height=min(620, 78 + max(len(page_frame), 1) * 35),
        column_config={
            "field_name": st.column_config.TextColumn("Field name", width="medium"),
            "export_field_name": st.column_config.TextColumn("Export field", width="medium"),
            "form_name": st.column_config.TextColumn("Instrument", width="medium"),
            "field_type": st.column_config.TextColumn("Type", width="small"),
            "field_label": st.column_config.TextColumn("Field label", width="large"),
            "is_required": st.column_config.CheckboxColumn("Required"),
            "has_branching": st.column_config.CheckboxColumn("Branching"),
            "is_validated": st.column_config.CheckboxColumn("Validated"),
            "is_identifier": st.column_config.CheckboxColumn("Identifier"),
            "is_matrix": st.column_config.CheckboxColumn("Matrix"),
            "is_doe_doc": st.column_config.CheckboxColumn("DOE/DOC"),
            "missing_label": st.column_config.CheckboxColumn("Blank label"),
            "action_tags": st.column_config.TextColumn("Action tags", width="medium"),
        },
    )


def _project_figures(snapshot: ProjectSnapshot, frame: pd.DataFrame) -> dict[str, Any]:
    figures = {
        "Field type distribution": field_type_figure(frame),
        "Top instruments by field count": instrument_count_figure(frame),
        "Top field-name prefixes": prefix_count_figure(frame),
        "Branching fields by instrument": branching_figure(frame),
        "Blank labels by instrument": missing_label_figure(frame),
    }
    if snapshot.is_longitudinal:
        figures["Instrument coverage by event"] = event_coverage_figure(
            snapshot.event_mappings, snapshot.events, snapshot.instruments
        )
    return figures


def _summary_frame(snapshot: ProjectSnapshot) -> pd.DataFrame:
    summary = _summary_for(snapshot)
    return pd.DataFrame(
        [{"Metric": key.replace("_", " ").title(), "Value": value} for key, value in summary.items()]
    )


def _project_exports(
    snapshot: ProjectSnapshot,
    frame: pd.DataFrame,
    generated_at: datetime,
) -> tuple[Any, Any]:
    kpis = {label: value for label, (value, _note) in _summary_kpis(snapshot).items()}
    figures = _project_figures(snapshot, frame)
    tables = {
        "Project summary": _summary_frame(snapshot),
        "Field inventory": frame,
        "API coverage": snapshot.coverage_frame(),
    }
    html = build_html_export(
        f"{snapshot.key} metadata watcher",
        kpis=kpis,
        tables=tables,
        figures=figures,
        subtitle=f"{snapshot.label} · PID {snapshot.pid} · read-only metadata inventory",
        generated_at=generated_at,
        filename_stem=f"{snapshot.key}_metadata_watcher",
    )
    bundle = build_zip_export(
        f"{snapshot.key} metadata watcher",
        kpis=kpis,
        tables=tables,
        figures=figures,
        subtitle=f"{snapshot.label} · PID {snapshot.pid} · read-only metadata inventory",
        generated_at=generated_at,
        filename_stem=f"{snapshot.key}_metadata_watcher",
    )
    return html, bundle


def _project_tab(
    snapshot: ProjectSnapshot,
    filtered: pd.DataFrame,
    generation: int,
    generated_at: datetime,
) -> None:
    st.markdown(f'<div class="section-label">{escape(snapshot.key)} · PID {escape(str(snapshot.pid))}</div>', unsafe_allow_html=True)
    st.markdown(f"## {snapshot.label}")
    st.markdown(
        f"""
        <div class="inventory-strip">
          <span><b>{len(filtered):,}</b> of <b>{len(snapshot.metadata):,}</b> export-expanded rows visible</span>
          <span>Fetched {_fmt_timestamp(snapshot.fetched_at)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _render_kpis(_summary_kpis(snapshot))
    st.caption("Summary cards use the complete project inventory. Figures and the field table use the active sidebar filters.")

    figures = _project_figures(snapshot, filtered)
    figure_items = list(figures.items())
    for index in range(0, len(figure_items), 2):
        columns = st.columns(2)
        for column, (title, figure) in zip(columns, figure_items[index : index + 2]):
            with column:
                _chart_panel(
                    title,
                    "Visible metadata rows",
                    figure,
                    chart_key=f"project_chart_{snapshot.key}_{index}_{title}_{generation}",
                )

    if snapshot.has_repeating:
        st.markdown('<div class="section-label">Repeating definitions</div>', unsafe_allow_html=True)
        if snapshot.repeating:
            st.dataframe(pd.DataFrame(snapshot.repeating), hide_index=True, width="stretch")
        else:
            st.info("The project reports repeating structures, but no repeating definition rows were returned.")

    st.markdown('<div class="section-label">Field inventory</div>', unsafe_allow_html=True)
    st.markdown("### Sortable field-level table")
    _detail_table(filtered, key=snapshot.key, generation=generation)

    with st.expander("Permissions and API coverage"):
        st.dataframe(snapshot.coverage_frame(), hide_index=True, width="stretch")

    html_artifact, bundle_artifact = _project_exports(snapshot, filtered, generated_at)
    download_columns = st.columns(3)
    with download_columns[0]:
        st.download_button(
            "Download field CSV",
            data=dataframe_to_csv_bytes(filtered),
            file_name=f"{snapshot.key}_field_inventory_{timestamp_slug(generated_at)}.csv",
            mime="text/csv",
            width="stretch",
        )
    with download_columns[1]:
        st.download_button(
            "Download HTML dashboard",
            data=html_artifact.data,
            file_name=html_artifact.filename,
            mime=html_artifact.media_type,
            width="stretch",
        )
    with download_columns[2]:
        st.download_button(
            "Download project bundle",
            data=bundle_artifact.data,
            file_name=bundle_artifact.filename,
            mime=bundle_artifact.media_type,
            width="stretch",
        )


def _comparison_frames_with_flags(
    frames: Mapping[str, pd.DataFrame], reference: str
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    master = compare_projects(frames, reference_project=reference)
    overlap = build_overlap_summary(frames, reference_project=reference)
    missing = build_missing_profile(frames)
    doe_keys = {
        str(key)
        for frame in frames.values()
        if {"field_key", "is_doe_doc"}.issubset(frame.columns)
        for key in frame.loc[frame["is_doe_doc"].fillna(False).astype(bool), "field_key"]
    }
    blank_keys = {
        str(key)
        for frame in frames.values()
        if {"field_key", "missing_label"}.issubset(frame.columns)
        for key in frame.loc[frame["missing_label"].fillna(False).astype(bool), "field_key"]
    }
    if not master.empty:
        master["any_doe_doc"] = master["field_key"].astype(str).isin(doe_keys)
        master["any_missing_label"] = master["field_key"].astype(str).isin(blank_keys)
    return master, overlap, missing


def _comparison_tab(
    frames: Mapping[str, pd.DataFrame],
    filters: Mapping[str, Any],
    generation: int,
    generated_at: datetime,
) -> dict[str, Any] | None:
    if len(frames) < 2:
        st.info("Provide two or more successfully connected project tokens to unlock cross-project comparison.")
        return None

    st.markdown('<div class="section-label">Cross-project inventory</div>', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Comparison</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-subtitle">Exact field-key coverage and deterministic discrepancy categories.</div>',
        unsafe_allow_html=True,
    )

    options = list(frames)
    default_index = options.index(REFERENCE_PROJECT) if REFERENCE_PROJECT in options else 0
    reference = st.selectbox(
        "Reference project",
        options,
        index=default_index,
        key=f"reference_{generation}",
    )
    master, overlap, missing = _comparison_frames_with_flags(frames, reference)

    filtered_master = master.copy()
    if filters["mismatch_categories"]:
        filtered_master = filtered_master[
            filtered_master["discrepancy_category"].isin(filters["mismatch_categories"])
        ]
    if filters["doe_doc"] != "All fields" and "any_doe_doc" in filtered_master:
        desired = filters["doe_doc"] == "DOE/DOC only"
        filtered_master = filtered_master[filtered_master["any_doe_doc"] == desired]
    if filters["missing_label"] != "All fields" and "any_missing_label" in filtered_master:
        desired = filters["missing_label"] == "Blank labels only"
        filtered_master = filtered_master[filtered_master["any_missing_label"] == desired]

    union_count = len(set().union(*(set(frame["field_key"].astype(str)) for frame in frames.values())))
    common_rows = overlap[overlap["metric"] == "COMMON_ALL"] if not overlap.empty else pd.DataFrame()
    common_count = int(common_rows["count"].iloc[0]) if not common_rows.empty else 0
    missing_ref_count = int(
        overlap.loc[overlap["metric"] == "MISSING_VS_REFERENCE", "count"].sum()
    ) if not overlap.empty else 0
    aligned_count = int((master.get("discrepancy_category", pd.Series(dtype=str)) == "ALIGNED").sum())
    _render_kpis(
        {
            "Projects": (len(frames), "Connected comparison set"),
            "Distinct union": (union_count, "Field keys across projects"),
            "Common to all": (common_count, "Present in every connected project"),
            "Aligned": (aligned_count, "Exact key, label, type, and instrument"),
            "Missing vs reference": (missing_ref_count, f"Reference: {reference}"),
            "Flagged rows": (len(missing), "Deterministic missing-profile rules"),
        }
    )

    chart_columns = st.columns(2)
    with chart_columns[0]:
        _chart_panel(
            "Overlap structure",
            "Counts for the active project and field filters",
            overlap_figure(frames),
            chart_key=f"comparison_overlap_{generation}",
        )
    with chart_columns[1]:
        _chart_panel(
            "Discrepancy categories",
            "Exact comparison outcomes",
            discrepancy_figure(master),
            chart_key=f"comparison_discrepancy_{generation}",
        )

    st.markdown('<div class="section-label">Primary comparison worklist</div>', unsafe_allow_html=True)
    st.markdown("### Field-level comparison")
    st.caption(f"Showing {len(filtered_master):,} of {len(master):,} comparison rows. Matching uses trimmed, case-insensitive field names.")
    comparison_columns = [
        "field_name",
        "example_label",
        "example_instrument",
        "example_field_type",
        *[f"in_{key}" for key in frames],
        "missing_in",
        "label_mismatch",
        "type_mismatch",
        "instrument_mismatch",
        "validation_gap",
        "identifier_flag_mismatch",
        "mismatch_total",
        "discrepancy_category",
        "any_doe_doc",
        "any_missing_label",
    ]
    comparison_columns = [column for column in comparison_columns if column in filtered_master]
    st.dataframe(
        filtered_master[comparison_columns],
        hide_index=True,
        width="stretch",
        height=620,
        column_config={
            **{f"in_{key}": st.column_config.CheckboxColumn(key) for key in frames},
            "validation_gap": st.column_config.CheckboxColumn("Validation gap"),
            "identifier_flag_mismatch": st.column_config.CheckboxColumn("Identifier mismatch"),
            "any_doe_doc": st.column_config.CheckboxColumn("DOE/DOC"),
            "any_missing_label": st.column_config.CheckboxColumn("Blank label"),
        },
    )

    st.markdown('<div class="section-label">Structural issue inventory</div>', unsafe_allow_html=True)
    st.markdown("### Missing profile")
    if missing.empty:
        st.info("No missing-profile rows were produced for the active comparison set.")
    else:
        visible_missing = missing.copy()
        if filters["mismatch_categories"]:
            category_to_issue = {
                "VALIDATION_GAP": "VALIDATION_GAP",
            }
            issue_filters = {
                category_to_issue[value]
                for value in filters["mismatch_categories"]
                if value in category_to_issue
            }
            if issue_filters:
                visible_missing = visible_missing[visible_missing["issue_type"].isin(issue_filters)]
        st.dataframe(visible_missing, hide_index=True, width="stretch", height=420)

    st.markdown('<div class="section-label">Numeric source of truth</div>', unsafe_allow_html=True)
    st.markdown("### Overlap summary")
    st.dataframe(
        overlap.drop(columns=["field_keys"], errors="ignore"),
        hide_index=True,
        width="stretch",
    )

    figures = {
        "Overlap structure": overlap_figure(frames),
        "Discrepancy categories": discrepancy_figure(master),
    }
    tables = {
        "Overlap summary": overlap,
        "Missing profile": missing,
        "Field-level comparison": master,
    }
    comparison_html = build_html_export(
        "Cross-project metadata comparison",
        kpis={
            "Connected projects": len(frames),
            "Distinct field union": union_count,
            "Common to all": common_count,
            "Aligned": aligned_count,
            "Missing vs reference": missing_ref_count,
            "Reference project": reference,
        },
        tables=tables,
        figures=figures,
        subtitle=f"Connected projects: {', '.join(frames)} · Reference: {reference}",
        generated_at=generated_at,
        filename_stem="cross_project_comparison",
    )
    download_columns = st.columns(4)
    artifacts = [
        ("Overlap CSV", overlap, f"overlap_summary_{timestamp_slug(generated_at)}.csv"),
        ("Missing profile CSV", missing, f"missing_profile_{timestamp_slug(generated_at)}.csv"),
        ("Comparison CSV", master, f"field_level_comparison_{timestamp_slug(generated_at)}.csv"),
    ]
    for column, (label, frame, filename) in zip(download_columns[:3], artifacts):
        with column:
            st.download_button(
                label,
                data=dataframe_to_csv_bytes(frame),
                file_name=filename,
                mime="text/csv",
                width="stretch",
            )
    with download_columns[3]:
        st.download_button(
            "Comparison HTML",
            data=comparison_html.data,
            file_name=comparison_html.filename,
            mime=comparison_html.media_type,
            width="stretch",
        )
    return {
        "reference": reference,
        "master": master,
        "overlap": overlap,
        "missing": missing,
        "figures": figures,
        "html": comparison_html,
    }


def _everything_zip(
    connected: Mapping[str, ProjectSnapshot],
    frames: Mapping[str, pd.DataFrame],
    generated_at: datetime,
) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for key, snapshot in connected.items():
            frame = frames.get(key, snapshot.metadata)
            html, _bundle = _project_exports(snapshot, frame, generated_at)
            archive.writestr(html.filename, html.data)
            csvs = build_csv_exports(
                {
                    "summary": _summary_frame(snapshot),
                    "field_inventory": frame,
                    "api_coverage": snapshot.coverage_frame(),
                },
                prefix=key,
                generated_at=generated_at,
            )
            for filename, data in csvs.items():
                archive.writestr(filename, data)

        if len(frames) >= 2:
            reference = REFERENCE_PROJECT if REFERENCE_PROJECT in frames else next(iter(frames))
            master, overlap, missing = _comparison_frames_with_flags(frames, reference)
            comparison_html = build_html_export(
                "Cross-project metadata comparison",
                tables={
                    "Overlap summary": overlap,
                    "Missing profile": missing,
                    "Field-level comparison": master,
                },
                figures={
                    "Overlap structure": overlap_figure(frames),
                    "Discrepancy categories": discrepancy_figure(master),
                },
                subtitle=f"Connected projects: {', '.join(frames)} · Reference: {reference}",
                generated_at=generated_at,
                filename_stem="cross_project_comparison",
            )
            archive.writestr(comparison_html.filename, comparison_html.data)
            for filename, data in build_csv_exports(
                {
                    "overlap_summary": overlap,
                    "missing_profile": missing,
                    "field_level_comparison": master,
                },
                prefix="cross_project",
                generated_at=generated_at,
            ).items():
                archive.writestr(filename, data)
    return buffer.getvalue()


def _overview_tab(
    snapshots: Mapping[str, ProjectSnapshot],
    connected: Mapping[str, ProjectSnapshot],
    filtered_frames: Mapping[str, pd.DataFrame],
    generated_at: datetime,
) -> None:
    mode = "Cross-project" if len(connected) >= 2 else "Single project"
    failed = [key for key, snapshot in snapshots.items() if not snapshot.connected]
    st.markdown('<div class="section-label">Current session</div>', unsafe_allow_html=True)
    st.markdown(f"## {mode} mode")
    _render_kpis(
        {
            "Connected": (len(connected), "Metadata projects available"),
            "Unavailable": (len(failed), ", ".join(failed) if failed else "None"),
            "Visible fields": (
                sum(len(frame) for frame in filtered_frames.values()),
                "Export-expanded rows after filters",
            ),
            "API mode": ("Read only", "PyCap export methods only"),
            "Request pacing": (
                f"{REDCAP_MIN_REQUEST_INTERVAL_SECONDS:.2f}s",
                "Minimum interval between calls",
            ),
            "Refresh cooldown": (f"{REFRESH_COOLDOWN_SECONDS}s", "Per session"),
        }
    )
    st.markdown("### Connection and permissions coverage")
    for key, snapshot in snapshots.items():
        with st.expander(f"{key} · {snapshot.status.replace('_', ' ').title()}"):
            st.dataframe(snapshot.coverage_frame(), hide_index=True, width="stretch")

    st.markdown('<div class="section-label">Session export</div>', unsafe_allow_html=True)
    st.download_button(
        "Export everything",
        data=_everything_zip(connected, filtered_frames, generated_at),
        file_name=f"ESD_REDCap_metadata_watcher_{timestamp_slug(generated_at)}.zip",
        mime="application/zip",
        type="primary",
    )
    st.caption("The bundle contains the available timestamped CSV and standalone HTML dashboards. It contains no API tokens.")


def _connected_app(snapshots: Mapping[str, ProjectSnapshot]) -> None:
    connected = {key: snapshot for key, snapshot in snapshots.items() if snapshot.connected}
    if not connected:
        st.error("No project metadata connected successfully. Clear the session and check the token values.")
        if st.button("Clear tokens / reset session", type="primary"):
            _clear_session()
        return

    controls = st.columns([1.6, 2.1, 7.3], vertical_alignment="center")
    elapsed = time.time() - float(st.session_state.last_fetch_epoch or 0)
    remaining = max(0, int(REFRESH_COOLDOWN_SECONDS - elapsed + 0.999))
    with controls[0]:
        if st.button(
            "Refresh from API",
            disabled=remaining > 0,
            type="primary",
            width="stretch",
        ):
            _fetch(include_record_count=False)
    with controls[1]:
        if st.button("Clear tokens / reset", width="stretch"):
            _clear_session()
    with controls[2]:
        if remaining:
            st.caption(f"Refresh available in {remaining} seconds. Filters and exports remain available.")
        else:
            st.caption("Refresh is available. One click replaces the in-session metadata snapshots.")

    generation = int(st.session_state.fetch_generation)
    filters = _sidebar_filters(connected, generation)
    selected = {
        key: connected[key]
        for key in filters["projects"]
        if key in connected
    }
    filtered_frames = {
        key: _apply_field_filters(snapshot.metadata, filters)
        for key, snapshot in selected.items()
    }
    generated_at = st.session_state.export_generated_at or datetime.now(timezone.utc)

    tab_names = ["Overview", *selected.keys()]
    if len(selected) >= 2:
        tab_names.append("Cross-Project Comparison")
    tabs = st.tabs(tab_names)
    with tabs[0]:
        _overview_tab(snapshots, connected, filtered_frames, generated_at)
    tab_index = 1
    for key, snapshot in selected.items():
        with tabs[tab_index]:
            _project_tab(
                snapshot,
                filtered_frames[key],
                generation,
                generated_at,
            )
        tab_index += 1
    if len(selected) >= 2:
        with tabs[tab_index]:
            _comparison_tab(filtered_frames, filters, generation, generated_at)


def main() -> None:
    _initialize_state()
    snapshots: Mapping[str, ProjectSnapshot] = st.session_state.snapshots
    _header(snapshots)
    _status_panel(snapshots, bool(st.session_state.connection_attempted))
    if not st.session_state.connection_attempted:
        _landing()
    else:
        _connected_app(snapshots)
    st.markdown(
        f"""
        <footer class="footer-note">
          REDCap endpoint: {escape(REDCAP_API_URL)} · PyCap {escape(pycap_version())} · Read-only metadata watcher · Tokens are session-only and excluded from exports.
        </footer>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
