"""ESD-branded Plotly figures for the live REDCap dashboard.

Palette provenance
------------------
The four study hues and the four completion-status hues were both run through
the dataviz validator (lightness band, chroma floor, CVD separation,
normal-vision floor, contrast) against this app's white chart surface, in light
and dark mode:

    study    #3366FF #D74E2D #00A17A #8B5CF6   all checks PASS
    status   #1F8A5F #7C3AED #D74E2D #9CA3AF   separation PASS

``#3366FF`` and ``#D74E2D`` are the existing ESD brand blue and red. The status
neutral ``#9CA3AF`` sits below the chroma and contrast floors on purpose — it
encodes *absence* ("Not started") and is only ever drawn alongside a visible
legend and labels, which is the relief the validator requires.

Study colour is keyed to the study, never to its position in a filtered list, so
hiding a study never repaints the others.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd
import plotly.graph_objects as go

# --- Brand ------------------------------------------------------------------
DISCOVERY_BLUE = "#3366FF"
FIRETRUCK_RED = "#D74E2D"
DEEP_TEAL = "#00A17A"
VIOLET = "#8B5CF6"

JET_BLACK = "#000000"
TEXT_SOFT = "#5A6472"
COOL_BLUE = "#E6EEFC"
SURFACE = "#FFFFFF"

#: Fixed, non-cycling assignment. A fifth study would fold into "Other" rather
#: than generate a new hue.
STUDY_COLORS: dict[str, str] = {
    "NANO": DISCOVERY_BLUE,
    "NICO": FIRETRUCK_RED,
    "IPSA": DEEP_TEAL,
    "ACTION": VIOLET,
}
FALLBACK_COLOR = "#5A6472"

STATUS_COLORS: dict[str, str] = {
    "Complete": "#1F8A5F",
    "Unverified": "#7C3AED",
    "Incomplete": FIRETRUCK_RED,
    "Not started": "#9CA3AF",
}
STATUS_ORDER = ("Complete", "Unverified", "Incomplete", "Not started")

FONT = "Libre Franklin, Arial, sans-serif"


def study_color(key: str) -> str:
    return STUDY_COLORS.get(str(key).upper(), FALLBACK_COLOR)


def style_figure(
    figure: go.Figure,
    *,
    height: int = 300,
    x_title: str | None = None,
    y_title: str | None = None,
    show_legend: bool = False,
) -> go.Figure:
    figure.update_layout(
        height=height,
        margin=dict(l=12, r=16, t=18, b=16),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT, size=12, color=JET_BLACK),
        hoverlabel=dict(font_family=FONT, bgcolor=SURFACE, bordercolor=COOL_BLUE),
        showlegend=show_legend,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            x=0,
            font=dict(size=11, color=TEXT_SOFT),
            title_text="",
        ),
        xaxis_title=x_title,
        yaxis_title=y_title,
        bargap=0.28,
    )
    figure.update_xaxes(
        automargin=True,
        showgrid=True,
        gridcolor=COOL_BLUE,
        zeroline=False,
        linecolor=COOL_BLUE,
        tickfont=dict(color=TEXT_SOFT, size=11),
        title_font=dict(color=TEXT_SOFT, size=11),
    )
    figure.update_yaxes(
        automargin=True,
        showgrid=False,
        zeroline=False,
        linecolor=COOL_BLUE,
        tickfont=dict(color=TEXT_SOFT, size=11),
        title_font=dict(color=TEXT_SOFT, size=11),
    )
    return figure


def empty_figure(note: str, *, height: int = 240) -> go.Figure:
    figure = go.Figure()
    figure.add_annotation(
        text=note,
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
        showarrow=False,
        font=dict(family=FONT, color=TEXT_SOFT, size=12),
    )
    figure.update_xaxes(visible=False)
    figure.update_yaxes(visible=False)
    return style_figure(figure, height=height)


def _truncate(values: Sequence[str], limit: int = 38) -> list[str]:
    out = []
    for value in values:
        text = str(value)
        out.append(text if len(text) <= limit else text[: limit - 1] + "…")
    return out


# --------------------------------------------------------------------------- #
# Study-level
# --------------------------------------------------------------------------- #


def study_metric_figure(
    overview: pd.DataFrame,
    metric: str,
    *,
    label: str,
    height: int = 240,
    suffix: str = "",
) -> go.Figure:
    """One horizontal bar per study, coloured by study, directly labelled."""
    if overview.empty or metric not in overview.columns:
        return empty_figure("No data available.", height=height)

    frame = overview.sort_values(metric, ascending=True)
    colors = [study_color(key) for key in frame["study"]]
    text = [f"{value:,.0f}{suffix}" for value in frame[metric]]

    figure = go.Figure(
        go.Bar(
            x=frame[metric],
            y=frame["study"],
            orientation="h",
            marker=dict(color=colors, cornerradius=4),
            text=text,
            textposition="outside",
            textfont=dict(size=11, color=TEXT_SOFT, family=FONT),
            cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>" + label + ": %{x:,.0f}" + suffix + "<extra></extra>",
        )
    )
    top = float(frame[metric].max() or 0)
    figure.update_xaxes(range=[0, top * 1.18 if top else 1])
    return style_figure(figure, height=height, x_title=label)


def completion_stack_figure(
    summary: pd.DataFrame,
    *,
    limit: int = 14,
    height: int = 460,
    sort_by: str = "started",
) -> go.Figure:
    """Stacked instrument completion, one segment per REDCap status."""
    if summary.empty:
        return empty_figure("No completion data available.", height=height)

    frame = summary[summary["started"] > 0].copy()
    if frame.empty:
        return empty_figure("No started instruments in this selection.", height=height)
    frame = frame.sort_values(sort_by, ascending=False).head(limit)
    frame = frame.sort_values(sort_by, ascending=True)
    labels = _truncate(frame["instrument_name"].tolist())

    figure = go.Figure()
    for status in STATUS_ORDER:
        if status == "Not started" or status not in frame.columns:
            continue
        figure.add_trace(
            go.Bar(
                x=frame[status],
                y=labels,
                name=status,
                orientation="h",
                marker=dict(
                    color=STATUS_COLORS[status],
                    cornerradius=4,
                    line=dict(color=SURFACE, width=2),
                ),
                hovertemplate="<b>%{y}</b><br>" + status + ": %{x:,.0f}<extra></extra>",
            )
        )
    figure.update_layout(barmode="stack")
    return style_figure(
        figure, height=height, x_title="Record-events started", show_legend=True
    )


def completion_rate_figure(
    summary: pd.DataFrame,
    *,
    label_column: str = "instrument_name",
    limit: int = 14,
    height: int = 420,
    color: str = DISCOVERY_BLUE,
) -> go.Figure:
    """Completion rate for the busiest rows — single series, so no legend."""
    if summary.empty or "completion_rate" not in summary.columns:
        return empty_figure("No completion data available.", height=height)

    frame = summary[summary["started"] > 0].copy()
    if frame.empty:
        return empty_figure("Nothing started yet in this selection.", height=height)
    frame = frame.sort_values("started", ascending=False).head(limit)
    frame = frame.sort_values("completion_rate", ascending=True)

    figure = go.Figure(
        go.Bar(
            x=frame["completion_rate"],
            y=_truncate(frame[label_column].astype(str).tolist()),
            orientation="h",
            marker=dict(color=color, cornerradius=4),
            text=[f"{value:.0f}%" for value in frame["completion_rate"]],
            textposition="outside",
            textfont=dict(size=11, color=TEXT_SOFT, family=FONT),
            cliponaxis=False,
            customdata=frame[["started", "Complete"]].to_numpy(),
            hovertemplate=(
                "<b>%{y}</b><br>Completion: %{x:.1f}%"
                "<br>Complete: %{customdata[1]:,.0f} of %{customdata[0]:,.0f} started"
                "<extra></extra>"
            ),
        )
    )
    figure.update_xaxes(range=[0, 112], ticksuffix="%")
    return style_figure(figure, height=height, x_title="Completion rate")


def field_type_figure(
    fields: pd.DataFrame, *, limit: int = 10, height: int = 320, color: str = DISCOVERY_BLUE
) -> go.Figure:
    if fields.empty or "field_type" not in fields.columns:
        return empty_figure("No field metadata available.", height=height)
    counts = (
        fields["field_type"].replace("", "(blank)").value_counts().head(limit)
        .sort_values(ascending=True)
    )
    figure = go.Figure(
        go.Bar(
            x=counts.to_numpy(),
            y=counts.index.tolist(),
            orientation="h",
            marker=dict(color=color, cornerradius=4),
            text=[f"{value:,}" for value in counts.to_numpy()],
            textposition="outside",
            textfont=dict(size=11, color=TEXT_SOFT, family=FONT),
            cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>%{x:,.0f} fields<extra></extra>",
        )
    )
    top = float(counts.max() or 0)
    figure.update_xaxes(range=[0, top * 1.18 if top else 1])
    return style_figure(figure, height=height, x_title="Fields")


def event_volume_figure(
    events: pd.DataFrame, *, height: int = 360, color: str = DISCOVERY_BLUE
) -> go.Figure:
    if events.empty:
        return empty_figure("This project has no event structure.", height=height)
    frame = events.sort_values("records", ascending=True).tail(16)
    labels = _truncate(frame.get("event_label", frame["event"]).astype(str).tolist())
    figure = go.Figure(
        go.Bar(
            x=frame["records"],
            y=labels,
            orientation="h",
            marker=dict(color=color, cornerradius=4),
            text=[f"{value:,}" for value in frame["records"]],
            textposition="outside",
            textfont=dict(size=11, color=TEXT_SOFT, family=FONT),
            cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>%{x:,.0f} records<extra></extra>",
        )
    )
    top = float(frame["records"].max() or 0)
    figure.update_xaxes(range=[0, top * 1.2 if top else 1])
    return style_figure(figure, height=height, x_title="Records with data")


# --------------------------------------------------------------------------- #
# Cross-study
# --------------------------------------------------------------------------- #


def sharing_figure(matrix: pd.DataFrame, *, height: int = 260) -> go.Figure:
    """How many instruments are shared by exactly N studies."""
    if matrix.empty or "studies" not in matrix.columns:
        return empty_figure("No instruments to compare.", height=height)
    counts = matrix["studies"].value_counts().sort_index()
    labels = [f"{int(n)} stud{'y' if n == 1 else 'ies'}" for n in counts.index]
    figure = go.Figure(
        go.Bar(
            x=counts.to_numpy(),
            y=labels,
            orientation="h",
            marker=dict(color=DISCOVERY_BLUE, cornerradius=4),
            text=[f"{value:,}" for value in counts.to_numpy()],
            textposition="outside",
            textfont=dict(size=11, color=TEXT_SOFT, family=FONT),
            cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>%{x:,.0f} instruments<extra></extra>",
        )
    )
    top = float(counts.max() or 0)
    figure.update_xaxes(range=[0, top * 1.2 if top else 1])
    return style_figure(figure, height=height, x_title="Instruments")


def overlap_heatmap(
    snapshots: Mapping[str, object], *, height: int = 320
) -> go.Figure:
    """Pairwise shared-instrument counts. Sequential single hue, light→dark."""
    keys = [k for k, s in snapshots.items() if getattr(s, "ok", False)]
    if len(keys) < 2:
        return empty_figure("Two or more connected studies are needed.", height=height)

    sets: dict[str, set[str]] = {}
    for key in keys:
        instruments = snapshots[key].instruments
        column = (
            instruments["instrument_name"]
            if "instrument_name" in instruments.columns
            else pd.Series(dtype=str)
        )
        sets[key] = set(column.astype(str))

    values = [[len(sets[row] & sets[col]) for col in keys] for row in keys]
    text = [[str(v) for v in row] for row in values]

    figure = go.Figure(
        go.Heatmap(
            z=values,
            x=keys,
            y=keys,
            text=text,
            texttemplate="%{text}",
            textfont=dict(size=12, family=FONT),
            colorscale=[[0.0, "#F2F6FF"], [0.5, "#8FB0FF"], [1.0, DISCOVERY_BLUE]],
            showscale=False,
            xgap=2,
            ygap=2,
            hovertemplate="<b>%{y} ∩ %{x}</b><br>%{z} shared instruments<extra></extra>",
        )
    )
    figure = style_figure(figure, height=height)
    figure.update_xaxes(showgrid=False)
    figure.update_yaxes(showgrid=False, autorange="reversed")
    return figure


def study_completion_figure(overview: pd.DataFrame, *, height: int = 240) -> go.Figure:
    return study_metric_figure(
        overview, "completion_rate", label="Completion rate", height=height, suffix="%"
    )


def consistency_figure(headline: Mapping[str, int], *, height: int = 200) -> go.Figure:
    """Field-harmonization verdict mix for one shared instrument."""
    order = ["identical", "label differs", "type differs", "partial"]
    colors = {
        "identical": "#1F8A5F",
        "label differs": "#7C3AED",
        "type differs": FIRETRUCK_RED,
        "partial": "#9CA3AF",
    }
    values = [int(headline.get(key, 0)) for key in order]
    if not any(values):
        return empty_figure("No fields to compare.", height=height)

    figure = go.Figure()
    for key, value in zip(order, values):
        if not value:
            continue
        figure.add_trace(
            go.Bar(
                x=[value],
                y=["fields"],
                name=key,
                orientation="h",
                marker=dict(
                    color=colors[key],
                    cornerradius=4,
                    line=dict(color=SURFACE, width=2),
                ),
                hovertemplate="<b>" + key + "</b>: %{x:,.0f} fields<extra></extra>",
            )
        )
    figure.update_layout(barmode="stack")
    figure = style_figure(figure, height=height, x_title="Fields", show_legend=True)
    figure.update_yaxes(visible=False)
    return figure
