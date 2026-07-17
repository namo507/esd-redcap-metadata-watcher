"""Compact, ESD-branded Plotly figures for metadata inventories."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


DISCOVERY_BLUE = "#3366FF"
SCIENCE_BLUE = "#91BAF4"
COOL_BLUE = "#E6EEFC"
JET_BLACK = "#000000"
TEXT_SOFT = "#5A6472"
CONFIDENT_ORANGE = "#F57F00"
FIRETRUCK_RED = "#D74E2D"
OPTIMAL_YELLOW = "#F4DA26"
BABY_PINK = "#F8B2B1"
SERIES = [DISCOVERY_BLUE, SCIENCE_BLUE, CONFIDENT_ORANGE, OPTIMAL_YELLOW, BABY_PINK]


def style_figure(
    figure: go.Figure,
    *,
    height: int = 300,
    x_title: str | None = None,
    y_title: str | None = None,
) -> go.Figure:
    figure.update_layout(
        height=height,
        margin=dict(l=12, r=12, t=18, b=16),
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        font=dict(family="Libre Franklin, Arial, sans-serif", size=12, color=JET_BLACK),
        hoverlabel=dict(font_family="Libre Franklin, Arial, sans-serif"),
        colorway=SERIES,
        showlegend=False,
        xaxis_title=x_title,
        yaxis_title=y_title,
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


def empty_figure(note: str, *, height: int = 260) -> go.Figure:
    figure = go.Figure()
    figure.add_annotation(
        text=note,
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
        showarrow=False,
        font=dict(family="Libre Franklin, Arial, sans-serif", color=TEXT_SOFT, size=12),
    )
    figure.update_xaxes(visible=False)
    figure.update_yaxes(visible=False)
    return style_figure(figure, height=height)


def _top_counts(
    frame: pd.DataFrame, column: str, *, limit: int = 10, empty_label: str = "(blank)"
) -> pd.DataFrame:
    if frame.empty or column not in frame.columns:
        return pd.DataFrame(columns=[column, "count"])
    values = frame[column].fillna("").astype(str).str.strip().replace("", empty_label)
    return (
        values.value_counts(dropna=False)
        .head(limit)
        .rename_axis(column)
        .reset_index(name="count")
        .sort_values("count", ascending=True)
    )


def field_type_figure(frame: pd.DataFrame) -> go.Figure:
    counts = _top_counts(frame, "field_type", limit=8)
    if counts.empty:
        return empty_figure("No field types are available")
    figure = px.bar(
        counts,
        x="count",
        y="field_type",
        orientation="h",
        color_discrete_sequence=[DISCOVERY_BLUE],
        text="count",
    )
    figure.update_traces(textposition="outside", cliponaxis=False, hovertemplate="%{y}: %{x}<extra></extra>")
    return style_figure(figure, x_title="Fields", y_title=None)


def instrument_count_figure(frame: pd.DataFrame) -> go.Figure:
    counts = _top_counts(frame, "form_name", limit=10)
    if counts.empty:
        return empty_figure("No instrument assignments are available")
    figure = px.bar(
        counts,
        x="count",
        y="form_name",
        orientation="h",
        color_discrete_sequence=[SCIENCE_BLUE],
        text="count",
    )
    figure.update_traces(textposition="outside", cliponaxis=False, hovertemplate="%{y}: %{x}<extra></extra>")
    return style_figure(figure, x_title="Fields", y_title=None)


def prefix_count_figure(frame: pd.DataFrame) -> go.Figure:
    counts = _top_counts(frame, "field_prefix", limit=10)
    if counts.empty:
        return empty_figure("No field-name prefixes are available")
    figure = px.bar(
        counts,
        x="count",
        y="field_prefix",
        orientation="h",
        color_discrete_sequence=[DISCOVERY_BLUE],
        text="count",
    )
    figure.update_traces(textposition="outside", cliponaxis=False, hovertemplate="%{y}: %{x}<extra></extra>")
    return style_figure(figure, x_title="Fields", y_title=None)


def branching_figure(frame: pd.DataFrame) -> go.Figure:
    if frame.empty or "has_branching" not in frame.columns:
        return empty_figure("No branching-logic inventory is available")
    subset = frame[frame["has_branching"].fillna(False).astype(bool)]
    counts = _top_counts(subset, "form_name", limit=10)
    if counts.empty:
        return empty_figure("No fields contain branching logic")
    figure = px.bar(
        counts,
        x="count",
        y="form_name",
        orientation="h",
        color_discrete_sequence=[CONFIDENT_ORANGE],
        text="count",
    )
    figure.update_traces(textposition="outside", cliponaxis=False, hovertemplate="%{y}: %{x}<extra></extra>")
    return style_figure(figure, x_title="Fields with branching logic", y_title=None)


def missing_label_figure(frame: pd.DataFrame) -> go.Figure:
    if frame.empty or "missing_label" not in frame.columns:
        return empty_figure("No label inventory is available")
    subset = frame[frame["missing_label"].fillna(False).astype(bool)]
    counts = _top_counts(subset, "form_name", limit=10)
    if counts.empty:
        return empty_figure("No blank field labels were found")
    figure = px.bar(
        counts,
        x="count",
        y="form_name",
        orientation="h",
        color_discrete_sequence=[FIRETRUCK_RED],
        text="count",
    )
    figure.update_traces(textposition="outside", cliponaxis=False, hovertemplate="%{y}: %{x}<extra></extra>")
    return style_figure(figure, x_title="Blank labels", y_title=None)


def event_coverage_figure(
    mappings: list[dict], events: list[dict], instruments: list[dict]
) -> go.Figure:
    mapping_frame = pd.DataFrame(mappings)
    if mapping_frame.empty:
        return empty_figure("No instrument-event mapping is available")

    form_column = next(
        (name for name in ("form", "instrument_name", "form_name") if name in mapping_frame.columns),
        None,
    )
    event_column = next(
        (name for name in ("unique_event_name", "event_name", "event") if name in mapping_frame.columns),
        None,
    )
    if not form_column or not event_column:
        return empty_figure("The event mapping did not include form and event names")

    coverage = (
        mapping_frame.assign(_present=1)
        .pivot_table(
            index=form_column,
            columns=event_column,
            values="_present",
            aggfunc="max",
            fill_value=0,
        )
        .sort_index()
    )
    if coverage.empty:
        return empty_figure("No instrument-event assignments were found")

    figure = go.Figure(
        go.Heatmap(
            z=coverage.values,
            x=[str(value) for value in coverage.columns],
            y=[str(value) for value in coverage.index],
            colorscale=[[0, "#F4F4F6"], [1, DISCOVERY_BLUE]],
            showscale=False,
            xgap=2,
            ygap=2,
            hovertemplate="Instrument: %{y}<br>Event: %{x}<extra></extra>",
        )
    )
    height = min(620, max(280, 100 + len(coverage.index) * 22))
    styled = style_figure(figure, height=height)
    styled.update_xaxes(tickangle=-35, showgrid=False)
    return styled


def overlap_figure(project_frames: Mapping[str, pd.DataFrame]) -> go.Figure:
    sets = {
        key: set(frame.get("field_key", pd.Series(dtype=str)).dropna().astype(str))
        for key, frame in project_frames.items()
    }
    keys = list(sets)
    if not keys:
        return empty_figure("No connected projects are available")

    rows: list[dict[str, object]] = []
    if len(keys) >= 2:
        common = set.intersection(*(sets[key] for key in keys))
        rows.append({"set": "Common to all connected", "count": len(common), "kind": "common"})
    for key in keys:
        others = set().union(*(sets[other] for other in keys if other != key)) if len(keys) > 1 else set()
        rows.append({"set": f"{key} only", "count": len(sets[key] - others), "kind": "unique"})
    if len(keys) == 3:
        for index, left in enumerate(keys):
            for right in keys[index + 1 :]:
                third = next(key for key in keys if key not in {left, right})
                rows.append(
                    {
                        "set": f"{left} + {right} only",
                        "count": len((sets[left] & sets[right]) - sets[third]),
                        "kind": "pair",
                    }
                )

    data = pd.DataFrame(rows).sort_values("count", ascending=True)
    palette = {"common": DISCOVERY_BLUE, "unique": CONFIDENT_ORANGE, "pair": SCIENCE_BLUE}
    figure = px.bar(
        data,
        x="count",
        y="set",
        orientation="h",
        color="kind",
        color_discrete_map=palette,
        text="count",
    )
    figure.update_traces(textposition="outside", cliponaxis=False, hovertemplate="%{y}: %{x}<extra></extra>")
    return style_figure(figure, height=max(280, 90 + len(data) * 34), x_title="Distinct fields")


def discrepancy_figure(comparison: pd.DataFrame) -> go.Figure:
    counts = _top_counts(comparison, "discrepancy_category", limit=12)
    if counts.empty:
        return empty_figure("No comparison categories are available")
    colors = [
        DISCOVERY_BLUE if value == "ALIGNED" else FIRETRUCK_RED
        for value in counts["discrepancy_category"]
    ]
    figure = go.Figure(
        go.Bar(
            x=counts["count"],
            y=counts["discrepancy_category"],
            orientation="h",
            marker_color=colors,
            text=counts["count"],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{y}: %{x}<extra></extra>",
        )
    )
    return style_figure(figure, height=max(280, 90 + len(counts) * 30), x_title="Fields")
