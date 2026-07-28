"""ESD Lab REDCap Studies Dashboard.

Run from the repository root with:
    streamlit run projects/redcap-metadata-watcher/app.py

A read-only, always-on view of every configured REDCap study. Tokens come from
the environment (repo-root `.env`, or the host's secret store) — there is no
token entry in the UI. Data refreshes on an interval; every REDCap call is an
export, and no participant-level value is ever loaded into the page.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd
import streamlit as st

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parents[1]
ASSET_DIR = REPO_ROOT / "assets"

if str(REPO_ROOT / "shared") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "shared"))

import charts  # noqa: E402
import metrics  # noqa: E402
from redcap_live import fetch_studies  # noqa: E402
from study_config import (  # noqa: E402
    REFRESH_INTERVAL_SECONDS,
    STUDY_REGISTRY,
    api_url,
    configured_studies,
    load_env_file,
    missing_studies,
)

load_env_file()

st.set_page_config(
    page_title="ESD Lab REDCap Studies",
    page_icon=str(ASSET_DIR / "favicon.png"),
    layout="wide",
    initial_sidebar_state="expanded",
)


# --------------------------------------------------------------------------- #
# Presentation helpers
# --------------------------------------------------------------------------- #


@st.cache_data(show_spinner=False)
def _font_css() -> str:
    path = ASSET_DIR / "fonts" / "LibreFranklin-VariableFont_wght.ttf"
    if not path.exists():
        return ""
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return (
        "@font-face{font-family:'Libre Franklin';"
        f"src:url(data:font/ttf;base64,{payload}) format('truetype');"
        "font-weight:100 900;font-display:swap;}"
    )


st.markdown(
    f"""
    <style>
    {_font_css()}
    html, body, [class*="css"] {{ font-family:'Libre Franklin', Arial, sans-serif; }}
    .kpi-grid {{ display:flex; gap:10px; flex-wrap:wrap; margin:2px 0 14px; }}
    .kpi {{ flex:1 1 150px; background:#FFFFFF; border:1px solid #E6EEFC;
            border-left:4px solid var(--accent,#3366FF); border-radius:8px;
            padding:12px 14px; }}
    .kpi .v {{ font-size:1.55rem; font-weight:700; line-height:1.15; color:#000; }}
    .kpi .l {{ font-size:.74rem; color:#5A6472; text-transform:uppercase;
               letter-spacing:.05em; margin-top:2px; }}
    .kpi .s {{ font-size:.74rem; color:#5A6472; margin-top:3px; }}
    .ro-badge {{ display:inline-block; background:#E8F5EF; color:#136B49;
                 border:1px solid #BFE3D2; border-radius:999px;
                 padding:2px 11px; font-size:.74rem; font-weight:600; }}
    .study-chip {{ display:inline-block; border-radius:999px; padding:2px 11px;
                   font-size:.74rem; font-weight:600; color:#FFF; margin-right:6px; }}
    [data-testid="stMetricValue"] {{ font-size:1.5rem; }}
    </style>
    """,
    unsafe_allow_html=True,
)


def kpi_row(items: list[tuple[str, str, str]], accent: str = "#3366FF") -> None:
    """items = [(value, label, sublabel)]"""
    cells = "".join(
        f'<div class="kpi" style="--accent:{accent}"><div class="v">{value}</div>'
        f'<div class="l">{label}</div>'
        + (f'<div class="s">{sub}</div>' if sub else "")
        + "</div>"
        for value, label, sub in items
    )
    st.markdown(f'<div class="kpi-grid">{cells}</div>', unsafe_allow_html=True)


def study_chip(key: str) -> str:
    return (
        f'<span class="study-chip" style="background:{charts.study_color(key)}">'
        f"{key}</span>"
    )


def humanize_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m ago"


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #


@st.cache_data(ttl=REFRESH_INTERVAL_SECONDS, show_spinner=False)
def load_snapshots(_keys: tuple[str, ...], _stamp: str) -> dict:
    """Fetch every configured study. Cached, so reruns do not call REDCap.

    ``_stamp`` participates in the cache key only so the manual refresh button
    can force a miss; ``_keys`` keeps the cache correct if the configured set
    changes.
    """
    studies = [s for s in STUDY_REGISTRY if s.key in _keys]
    return fetch_studies(studies, url=api_url())


def get_snapshots() -> dict:
    available = configured_studies()
    if not available:
        return {}
    keys = tuple(s.key for s in available)
    stamp = st.session_state.get("refresh_stamp", "initial")
    with st.spinner(f"Reading {len(keys)} REDCap projects (read-only)…"):
        return load_snapshots(keys, stamp)


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #

with st.sidebar:
    logo = ASSET_DIR / "esd-logo.png"
    if logo.exists():
        st.image(str(logo), width=180)
    st.markdown("### REDCap Studies")
    st.markdown('<span class="ro-badge">READ-ONLY</span>', unsafe_allow_html=True)
    st.caption(
        "Exports only. This dashboard cannot create, edit, or delete anything in "
        "REDCap, and never loads participant-level values."
    )
    st.divider()

snapshots = get_snapshots()
connected = {k: s for k, s in snapshots.items() if s.ok}

with st.sidebar:
    if not snapshots:
        st.error("No study tokens configured.")
    else:
        st.markdown("**Connection status**")
        for key, snap in snapshots.items():
            icon = {"connected": "🟢", "limited": "🟡"}.get(snap.status, "🔴")
            st.markdown(
                f"{icon} **{key}** · pid {snap.pid or '—'}"
                if snap.ok
                else f"{icon} **{key}** · {snap.status}"
            )
            if not snap.ok:
                st.caption(snap.status_detail[:160])

    for study in missing_studies():
        st.caption(f"○ {study.key} — set {study.token_env} to include it")

    st.divider()
    selected = st.multiselect(
        "Studies in view",
        options=list(connected),
        default=list(connected),
        help="Filters every tab. Colours stay pinned to each study.",
    )
    st.divider()
    if st.button("Refresh from REDCap now", width="stretch"):
        st.session_state["refresh_stamp"] = datetime.now(timezone.utc).isoformat()
        st.cache_data.clear()
        st.rerun()
    st.caption(
        f"Auto-refreshes every {REFRESH_INTERVAL_SECONDS // 60} min. "
        "Reruns in between reuse the cached snapshot."
    )

view = {k: s for k, s in connected.items() if k in selected}


# --------------------------------------------------------------------------- #
# Header + freshness ticker
# --------------------------------------------------------------------------- #

st.title("ESD Lab REDCap Studies")

if not snapshots:
    st.error(
        "No REDCap tokens are configured. Set `NANO_API_TOKEN`, `NICO_API_TOKEN`, "
        "`IPSA_API_TOKEN`, and/or `ACTION_API_TOKEN` in the repository-root `.env` "
        "file (git-ignored) or in the host's secret store, then reload."
    )
    st.stop()

if not connected:
    st.error("No study connected successfully. See the status list in the sidebar.")
    for key, snap in snapshots.items():
        st.warning(f"**{key}** — {snap.status_detail}")
    st.stop()


@st.fragment(run_every=60)
def freshness_bar() -> None:
    newest = max((s.fetched_at for s in connected.values()), default=None)
    if newest is None:
        return
    age = (datetime.now(timezone.utc) - newest).total_seconds()
    if age >= REFRESH_INTERVAL_SECONDS:
        # The cache entry has expired; a full rerun refetches it.
        st.rerun()
    remaining = max(0, REFRESH_INTERVAL_SECONDS - age)
    left, right = st.columns([3, 1])
    with left:
        st.caption(
            f"Live from {api_url()} · {len(connected)} project(s) connected · "
            f"updated {humanize_age(age)} "
            f"({newest.astimezone().strftime('%Y-%m-%d %H:%M %Z')})"
        )
    with right:
        st.caption(f"next refresh in ≈ {int(remaining // 60)}m {int(remaining % 60)}s")


freshness_bar()

if not view:
    st.info("Select at least one study in the sidebar.")
    st.stop()

overview = metrics.study_overview(view)

tab_overview, tab_study, tab_compare, tab_fields, tab_about = st.tabs(
    [
        "Portfolio",
        "Study detail",
        "Instrument comparison",
        "Field explorer",
        "Definitions",
    ]
)


# --------------------------------------------------------------------------- #
# Tab 1 — Portfolio
# --------------------------------------------------------------------------- #

with tab_overview:
    totals = (
        f"{int(overview['records'].sum()):,}",
        f"{int(overview['instruments'].sum()):,}",
        f"{int(overview['fields'].sum()):,}",
        f"{int(overview['events'].sum()):,}",
    )
    portfolio_complete = int(overview["completed_forms"].sum())
    portfolio_started = int(overview["started_forms"].sum())
    portfolio_rate = (
        round(100.0 * portfolio_complete / portfolio_started, 1)
        if portfolio_started
        else 0.0
    )
    kpi_row(
        [
            (totals[0], "Records", f"across {len(view)} studies"),
            (totals[1], "Instruments", "survey + data-entry forms"),
            (totals[2], "Fields", "all field types"),
            (totals[3], "Events", "longitudinal timepoints"),
            (
                f"{portfolio_rate:.0f}%",
                "Forms complete",
                f"{portfolio_complete:,} of {portfolio_started:,} started",
            ),
        ]
    )

    st.markdown("#### Study-by-study")
    display = overview[
        [
            "study", "title", "pid", "records", "instruments", "fields", "events",
            "completion_rate", "identifier_fields", "longitudinal", "repeating",
        ]
    ].copy()
    st.dataframe(
        display,
        hide_index=True,
        width="stretch",
        column_config={
            "study": st.column_config.TextColumn("Study", width="small"),
            "title": st.column_config.TextColumn("REDCap project title", width="large"),
            "pid": st.column_config.TextColumn("PID", width="small"),
            "records": st.column_config.NumberColumn("Records", format="%d"),
            "instruments": st.column_config.NumberColumn("Instruments", format="%d"),
            "fields": st.column_config.NumberColumn("Fields", format="%d"),
            "events": st.column_config.NumberColumn("Events", format="%d"),
            "completion_rate": st.column_config.ProgressColumn(
                "Completion", format="%.1f%%", min_value=0, max_value=100
            ),
            "identifier_fields": st.column_config.NumberColumn("PHI fields", format="%d"),
            "longitudinal": st.column_config.CheckboxColumn("Longitudinal"),
            "repeating": st.column_config.CheckboxColumn("Repeating"),
        },
    )

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Records per study**")
        st.plotly_chart(
            charts.study_metric_figure(overview, "records", label="Records"),
            width="stretch",
        )
        st.markdown("**Fields per study**")
        st.plotly_chart(
            charts.study_metric_figure(overview, "fields", label="Fields"),
            width="stretch",
        )
    with c2:
        st.markdown("**Form completion rate**")
        st.plotly_chart(
            charts.study_completion_figure(overview), width="stretch"
        )
        st.markdown("**Instruments per study**")
        st.plotly_chart(
            charts.study_metric_figure(overview, "instruments", label="Instruments"),
            width="stretch",
        )

    st.markdown("#### Structural profile")
    st.caption(
        "Counts of fields carrying each structural property. These drive the "
        "quality signals on the study tab."
    )
    profile = overview[
        ["study", "required_fields", "branching_fields", "identifier_fields"]
    ].rename(
        columns={
            "required_fields": "Required",
            "branching_fields": "Branching logic",
            "identifier_fields": "Identifier-flagged",
        }
    )
    st.dataframe(profile, hide_index=True, width="stretch")


# --------------------------------------------------------------------------- #
# Tab 2 — Study detail
# --------------------------------------------------------------------------- #

with tab_study:
    key = st.radio(
        "Study", list(view), horizontal=True, label_visibility="collapsed"
    )
    snap = view[key]
    accent = charts.study_color(key)
    fields = metrics.field_inventory(snap)
    instruments = metrics.instrument_summary(snap)
    events = metrics.event_summary(snap)
    totals = metrics.completion_totals(snap)

    st.markdown(
        f"{study_chip(key)} **{snap.title}** · pid {snap.pid} · "
        f"{'longitudinal' if snap.longitudinal else 'classic'}"
        f"{' · repeating instruments' if snap.repeating else ''}"
        f"{' · surveys enabled' if snap.surveys_enabled else ''}",
        unsafe_allow_html=True,
    )
    if snap.status == "limited":
        st.warning(f"Partial data: {snap.status_detail}")

    kpi_row(
        [
            (f"{snap.record_count or 0:,}", "Records", f"{snap.row_count or 0:,} record-events"),
            (f"{len(snap.instruments):,}", "Instruments", ""),
            (f"{len(fields):,}", "Fields", ""),
            (f"{len(snap.events):,}", "Events", ""),
            (f"{metrics.completion_rate(totals):.0f}%", "Completion",
             f"{totals['Complete']:,} of {sum(totals[s] for s in metrics.STARTED_STATUSES):,} started"),
        ],
        accent=accent,
    )

    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("**Completion by instrument** — busiest first")
        st.plotly_chart(
            charts.completion_stack_figure(instruments), width="stretch"
        )
    with c2:
        st.markdown("**Completion rate**")
        st.plotly_chart(
            charts.completion_rate_figure(instruments, color=accent),
            width="stretch",
        )

    c3, c4 = st.columns(2)
    with c3:
        st.markdown("**Field types**")
        st.plotly_chart(
            charts.field_type_figure(fields, color=accent), width="stretch"
        )
    with c4:
        st.markdown("**Records per event**")
        st.plotly_chart(
            charts.event_volume_figure(events, color=accent), width="stretch"
        )

    st.markdown("#### Instruments")
    inst_search = st.text_input(
        "Filter instruments", key=f"inst_{key}", placeholder="name or label contains…"
    )
    inst_view = instruments
    if inst_search:
        needle = inst_search.lower()
        inst_view = instruments[
            instruments["instrument_name"].str.lower().str.contains(needle, na=False)
            | instruments["instrument_label"].str.lower().str.contains(needle, na=False)
        ]
    st.dataframe(
        inst_view.drop(columns=["study"]),
        hide_index=True,
        width="stretch",
        column_config={
            "instrument_name": st.column_config.TextColumn("Instrument", width="medium"),
            "instrument_label": st.column_config.TextColumn("Label", width="large"),
            "fields": st.column_config.NumberColumn("Fields", format="%d"),
            "events_assigned": st.column_config.NumberColumn("Events", format="%d"),
            "started": st.column_config.NumberColumn("Started", format="%d"),
            "completion_rate": st.column_config.ProgressColumn(
                "Completion", format="%.1f%%", min_value=0, max_value=100
            ),
        },
    )

    if not events.empty:
        st.markdown("#### Events")
        st.dataframe(
            events.drop(columns=["study"]),
            hide_index=True,
            width="stretch",
            column_config={
                "event": st.column_config.TextColumn("Unique name", width="medium"),
                "event_label": st.column_config.TextColumn("Event", width="medium"),
                "records": st.column_config.NumberColumn("Records", format="%d"),
                "rows": st.column_config.NumberColumn("Rows", format="%d"),
                "completion_rate": st.column_config.ProgressColumn(
                    "Completion", format="%.1f%%", min_value=0, max_value=100
                ),
            },
        )

    st.markdown("#### Structural signals")
    st.caption(
        "Observations about how the project is built — not a judgement about the "
        "data itself."
    )
    st.dataframe(
        metrics.quality_flags(snap),
        hide_index=True,
        width="stretch",
        column_config={
            "check": st.column_config.TextColumn("Signal", width="medium"),
            "count": st.column_config.NumberColumn("Fields", format="%d"),
            "detail": st.column_config.TextColumn("What it means", width="large"),
        },
    )


# --------------------------------------------------------------------------- #
# Tab 3 — Instrument comparison
# --------------------------------------------------------------------------- #

with tab_compare:
    st.markdown("#### Which instruments do these studies share?")
    matrix = metrics.instrument_matrix(view)

    if len(view) < 2:
        st.info("Select two or more studies in the sidebar to compare them.")
    else:
        c1, c2 = st.columns([2, 3])
        with c1:
            st.markdown("**Shared by how many studies**")
            st.plotly_chart(charts.sharing_figure(matrix), width="stretch")
        with c2:
            st.markdown("**Pairwise shared instruments**")
            st.plotly_chart(charts.overlap_heatmap(view), width="stretch")

        min_studies = st.slider(
            "Show instruments present in at least N studies",
            min_value=1,
            max_value=max(2, len(view)),
            value=2,
        )
        subset = matrix[matrix["studies"] >= min_studies]
        st.caption(f"{len(subset):,} of {len(matrix):,} instruments match.")
        st.dataframe(
            subset,
            hide_index=True,
            width="stretch",
            column_config={
                "instrument_name": st.column_config.TextColumn("Instrument", width="medium"),
                "instrument_label": st.column_config.TextColumn("Label", width="large"),
                "studies": st.column_config.NumberColumn("# studies", format="%d"),
                **{
                    k: st.column_config.CheckboxColumn(k, width="small")
                    for k in view
                },
            },
        )

        st.divider()
        st.markdown("#### Field-level harmonization")
        shared = metrics.shared_instruments(view, minimum=2)
        if not shared:
            st.info("These studies share no instruments.")
        else:
            instrument = st.selectbox(
                "Instrument to compare", shared, key="compare_instrument"
            )
            comparison = metrics.compare_instrument(view, instrument)
            headline = metrics.comparison_headline(comparison)

            kpi_row(
                [
                    (f"{len(comparison):,}", "Fields", "union across studies"),
                    (f"{headline['identical']:,}", "Identical", "same type and label"),
                    (f"{headline['label differs']:,}", "Label differs", "same type"),
                    (f"{headline['type differs']:,}", "Type differs", "needs review"),
                    (f"{headline['partial']:,}", "Partial", "missing in some study"),
                ]
            )
            st.plotly_chart(charts.consistency_figure(headline), width="stretch")

            only_issues = st.toggle(
                "Show only fields that differ", value=True, key="only_issues"
            )
            table = comparison
            if only_issues:
                table = comparison[comparison["consistency"] != "identical"]
            if table.empty:
                st.success(
                    f"`{instrument}` is field-for-field identical across the selected "
                    "studies."
                )
            else:
                st.dataframe(table, hide_index=True, width="stretch")

            st.caption(
                "Cells show each study's field type; `—` means the field is absent "
                "there. Comparison is structural (name, type, label) — it does not "
                "read any response value."
            )


# --------------------------------------------------------------------------- #
# Tab 4 — Field explorer
# --------------------------------------------------------------------------- #

with tab_fields:
    st.markdown("#### Search every field across every selected study")
    all_fields = metrics.combined_fields(view)

    if all_fields.empty:
        st.info("No field metadata available.")
    else:
        search = st.text_input(
            "Search",
            placeholder="Type a field name, question wording, or answer choice…",
            help="Matches field name, label, note, and answer choices.",
        )

        f1, f2, f3 = st.columns(3)
        with f1:
            study_pick = st.multiselect(
                "Study", sorted(all_fields["study"].unique()),
                default=sorted(all_fields["study"].unique()),
            )
        with f2:
            type_pick = st.multiselect(
                "Field type", sorted(all_fields["field_type"].unique())
            )
        with f3:
            scoped = all_fields[all_fields["study"].isin(study_pick)]
            form_pick = st.multiselect(
                "Instrument", sorted(scoped["form_name"].unique())
            )

        t1, t2, t3, t4 = st.columns(4)
        only_required = t1.toggle("Required only")
        only_identifier = t2.toggle("Identifier-flagged only")
        only_branching = t3.toggle("Has branching logic")
        only_unlabelled = t4.toggle("Missing label only")

        result = all_fields[all_fields["study"].isin(study_pick)]
        if type_pick:
            result = result[result["field_type"].isin(type_pick)]
        if form_pick:
            result = result[result["form_name"].isin(form_pick)]
        if only_required:
            result = result[result["required"]]
        if only_identifier:
            result = result[result["identifier"]]
        if only_branching:
            result = result[result["has_branching"]]
        if only_unlabelled:
            result = result[~result["has_label"]]
        if search:
            needle = search.lower()
            haystack = (
                result["field_name"].str.lower()
                + " "
                + result["field_label"].str.lower()
                + " "
                + result["field_note"].str.lower()
                + " "
                + result["choices"].str.lower()
            )
            result = result[haystack.str.contains(needle, na=False, regex=False)]

        st.caption(
            f"**{len(result):,}** of {len(all_fields):,} fields match · "
            f"{result['form_name'].nunique():,} instruments · "
            f"{result['study'].nunique():,} studies"
        )

        st.dataframe(
            result[
                [
                    "study", "form_name", "field_name", "field_type", "field_label",
                    "validation", "choice_count", "required", "identifier",
                    "has_branching",
                ]
            ],
            hide_index=True,
            width="stretch",
            height=460,
            column_config={
                "study": st.column_config.TextColumn("Study", width="small"),
                "form_name": st.column_config.TextColumn("Instrument", width="medium"),
                "field_name": st.column_config.TextColumn("Field", width="medium"),
                "field_type": st.column_config.TextColumn("Type", width="small"),
                "field_label": st.column_config.TextColumn("Label", width="large"),
                "validation": st.column_config.TextColumn("Validation", width="small"),
                "choice_count": st.column_config.NumberColumn("Choices", format="%d"),
                "required": st.column_config.CheckboxColumn("Req."),
                "identifier": st.column_config.CheckboxColumn("PHI"),
                "has_branching": st.column_config.CheckboxColumn("Branch"),
            },
        )

        st.download_button(
            "Download these fields as CSV",
            data=result.to_csv(index=False).encode("utf-8"),
            file_name="redcap_field_search.csv",
            mime="text/csv",
            help="Structural field metadata only — contains no participant data.",
        )


# --------------------------------------------------------------------------- #
# Tab 5 — Definitions
# --------------------------------------------------------------------------- #

with tab_about:
    st.markdown(
        f"""
#### What this dashboard is

A read-only reporting layer over {len(connected)} REDCap project(s) at
`{api_url()}`. It reads project structure and form-completion state, and reports
them as counts.

#### Read-only guarantee

Every call goes through an allowlist that permits only REDCap **export**
content types, and rejects the parameters REDCap uses to write
(`action`, `data`, `returnContent`, `overwriteBehavior`, `forceAutoNumber`).
A call that violates this raises before any network request is made. There is no
code path in this app that can create, edit, or delete a REDCap record, field,
instrument, or event.

#### No participant data

The completion figures come from one export of the `<form>_complete` status
fields. Those rows are reduced to counts inside the acquisition layer and
discarded before anything is rendered — the page never receives a participant
identifier or a response value. The CSV download contains field *metadata* only.

#### How the numbers are defined

| Term | Definition |
| --- | --- |
| **Records** | Distinct record IDs returned by the project's record-ID export. |
| **Record-events** | Rows in a flat export: one per record per event (per repeat instance where applicable). |
| **Complete / Incomplete / Unverified** | REDCap's own `<form>_complete` states (2 / 0 / 1). |
| **Not started** | The `<form>_complete` cell is empty — includes forms not mapped to that event. |
| **Started** | Complete + Incomplete + Unverified. |
| **Completion rate** | Complete ÷ Started. Excludes *Not started*, so a form used in few events is not scored as failing. |
| **Identifier-flagged** | Fields REDCap marks as directly identifying. Counted, never displayed. |

#### Refresh behaviour

Snapshots are cached for {REFRESH_INTERVAL_SECONDS // 60} minutes. Ordinary
interaction — tabs, filters, sorting, searching — reuses the cached snapshot and
makes no API call. A ticker checks every 60 seconds and triggers a refetch once
the cache expires; **Refresh from REDCap now** forces one immediately. Outbound
requests are serialised behind a process-wide pacer.

#### Configuration

Tokens are read from `<repo root>/.env` (git-ignored) or the host's secret
store, never from the UI and never from source. Adding a study is one entry in
`study_config.STUDY_REGISTRY` plus its token environment variable.
"""
    )

    st.markdown("**Configured studies**")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Study": s.key,
                    "Env var": s.token_env,
                    "Configured": s.configured,
                    "Connected": s.key in connected,
                }
                for s in STUDY_REGISTRY
            ]
        ),
        hide_index=True,
        width="stretch",
    )
