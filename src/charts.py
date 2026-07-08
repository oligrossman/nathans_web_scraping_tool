"""Plotly figure builders for the price-analytics page.

Each function takes already-shaped data (from ``analytics``) and returns a
Plotly ``Figure``. No Streamlit here, so figures can be reused or exported
(PNG/HTML) outside the app.
"""

from __future__ import annotations

import plotly.express as px
import plotly.graph_objects as go

from . import analytics
from .branding import DELIVEROO_TEAL, OWN_BRAND, SOURCE_COLOURS, WAGAMAMA_RED
from .enrichment.schema import GroupingDimension
from .synthetic import MARKET_LABEL, TOTAL_LABEL

_TEMPLATE = "plotly_white"
# Source colours imported from branding — Wagamama red is the hero series.
_SOURCE_COLOURS = SOURCE_COLOURS
# Channel colours for restaurant vs delivery views.
_RESTAURANT_COLOUR = WAGAMAMA_RED
_DELIVERY_COLOUR = DELIVEROO_TEAL
# Pastel low/high tones drawn from the default qualitative palette (the "By
# group" scheme) — teal for low, warm orange for high; avoids clashing with the
# brand-red Wagamama marker and the near-black average.
_LOW_COLOUR = "#00CC96"  # pastel teal
_HIGH_COLOUR = "#FFA15A"  # pastel orange
_AVG_COLOUR = "#111827"  # near-black
_CHANNEL_LABELS = {"restaurant": "Restaurant", "delivery": "Delivery (Deliveroo)"}
_HIGH_LOW_LABELS = {"low": "Lowest", "average": "Average", "high": "Highest"}


def _source_colour_map(sources: list[str]) -> dict[str, str]:
    """Colour map keeping Wagamama brand-red while others use the pastel palette.

    Wagamama is always the hero red in every chart; the remaining sources cycle
    through the default (pastel) qualitative palette so competitors and markets
    read as a soft, distinct set rather than the fixed brand colours.
    """
    palette = [c for c in px.colors.qualitative.Plotly if c.lower() != WAGAMAMA_RED.lower()]
    mapping: dict[str, str] = {}
    i = 0
    for source in sources:
        if source == OWN_BRAND:
            mapping[source] = WAGAMAMA_RED
        else:
            mapping[source] = palette[i % len(palette)]
            i += 1
    return mapping


def _competitor_label(sources: list[str]) -> str:
    """Human-readable label for the competitor source(s) in a comparison.

    Excludes our own brand and any market series ("Market whole", "Market: X").
    """
    others = [s for s in sources if s != OWN_BRAND and not s.startswith("Market")]
    return ", ".join(others) if others else "competitors"


def _style_total_trace(fig: go.Figure, color_col: str = "bucket") -> go.Figure:
    """Make the Total series visually distinct (black dashed, thicker)."""
    for trace in fig.data:
        name = getattr(trace, "name", None)
        if name == TOTAL_LABEL:
            trace.line = dict(color="#111827", dash="dash", width=3)
            trace.marker = dict(size=8, symbol="diamond")
    return fig


def individual_items_line(history, items: list[str]) -> go.Figure:
    """Line chart of selected individual items (Nando's) over time."""
    frame = history[(history["source"] == "Nando's") & (history["item"].isin(items))]
    fig = px.line(
        frame,
        x="date",
        y="price",
        color="item",
        markers=True,
        template=_TEMPLATE,
        labels={"price": "Price (GBP)", "date": "Quarter", "item": "Item"},
        title="Individual item price trends - Nando's",
    )
    fig.update_layout(yaxis_tickprefix="£", legend_title_text="")
    return fig


def total_line(history, source: str = "Nando's", *, indexed: bool = False) -> go.Figure:
    """Line chart of the equal-weighted across-bucket average (Total) over time."""
    means = analytics.bucket_means(history, source, include_total=True)
    totals = means[means["bucket"] == TOTAL_LABEL]
    if indexed:
        totals = analytics.rebase_to_100(totals, ["bucket"])
        y, y_label, prefix, suffix = "index", "Price index (Q1 = 100)", "", " (indexed)"
    else:
        y, y_label, prefix, suffix = "price", "Avg price (GBP)", "£", ""
    fig = px.line(
        totals,
        x="date",
        y=y,
        markers=True,
        template=_TEMPLATE,
        labels={y: y_label, "date": "Quarter"},
        title=f"Total average menu price{suffix} - {source}",
    )
    fig.update_traces(line=dict(color="#111827", width=3), marker=dict(size=8, symbol="diamond"))
    fig.update_layout(yaxis_tickprefix=prefix, showlegend=False)
    return fig


def _dimension_label(dimension: GroupingDimension | str) -> str:
    if isinstance(dimension, GroupingDimension):
        return dimension.label
    return str(dimension)


def bucket_line(
    history,
    source: str = "Nando's",
    *,
    as_pct_change: bool = False,
    include_total: bool = False,
    show_title: bool = True,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
) -> go.Figure:
    """Average price by grouping dimension over time for one source.

    With ``as_pct_change`` the y-axis shows cumulative % change from each series'
    first quarter (compounded growth) instead of absolute pounds. Set
    ``show_title=False`` to drop the per-chart title (e.g. when a shared caption
    already describes a column of these charts).
    """
    means = analytics.bucket_means(
        history, source, include_total=include_total, dimension=dimension
    )
    dim_label = _dimension_label(dimension)
    if as_pct_change:
        means = analytics.add_pct_change(means, ["source", "bucket"])
        y, y_label, title = "pct_change", "Cumulative price change (%)", " (% change)"
    else:
        y, y_label, title = "price", "Avg price (GBP)", ""
    # Legend order mirrors the heatmap's y-axis: Total first, then the rest
    # alphabetically.
    uniques = list(means["bucket"].unique())
    rest = sorted(b for b in uniques if b != TOTAL_LABEL)
    bucket_order = ([TOTAL_LABEL] if TOTAL_LABEL in uniques else []) + rest
    fig = px.line(
        means,
        x="date",
        y=y,
        color="bucket",
        markers=True,
        template=_TEMPLATE,
        labels={y: y_label, "date": "Quarter", "bucket": dim_label},
        title=f"Average price by {dim_label.lower()}{title} - {source}" if show_title else None,
        category_orders={"bucket": bucket_order},
    )
    if include_total:
        _style_total_trace(fig)
    if as_pct_change:
        fig.update_layout(yaxis_ticksuffix="%", legend_title_text="")
    else:
        fig.update_layout(yaxis_tickprefix="£", legend_title_text="")
    if not show_title:
        # No title means the default ~100px top margin is just dead space; shrink
        # it so the chart sits snugly under its (external) heading.
        fig.update_layout(margin=dict(t=30))
    return fig


def comparison_facets(
    history,
    *,
    sources: list[str] | None = None,
    include_total: bool = False,
    as_pct_change: bool = False,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
) -> go.Figure:
    """Small-multiple line charts: Wagamama vs competitor(s) vs market (excl. Wagamama).

    With ``as_pct_change`` each panel shows cumulative % change from the first
    quarter (compounded growth) rather than absolute pounds.
    """
    means = analytics.bucket_means(history, include_total=include_total, dimension=dimension)
    if sources is None:
        sources = [OWN_BRAND, "Nando's", MARKET_LABEL]
    means = means[means["source"].isin(sources)]
    dim_label = _dimension_label(dimension)
    bucket_order = (
        [TOTAL_LABEL, *sorted(b for b in means["bucket"].unique() if b != TOTAL_LABEL)]
        if include_total
        else None
    )
    if as_pct_change:
        means = analytics.add_pct_change(means, ["source", "bucket"])
        y, y_label, suffix = "pct_change", "Cumulative price change (%)", " (% change)"
    else:
        y, y_label, suffix = "price", "Avg price (GBP)", ""
    title = (
        f"Price trends by {dim_label.lower()}{suffix} - Wagamama vs "
        f"{_competitor_label(sources)} vs market (market excl. Wagamama)"
    )
    facet_col_wrap = 3
    fig = px.line(
        means,
        x="date",
        y=y,
        color="source",
        facet_col="bucket",
        facet_col_wrap=facet_col_wrap,
        markers=True,
        template=_TEMPLATE,
        color_discrete_map=_SOURCE_COLOURS,
        labels={y: y_label, "date": "Quarter", "source": "Source"},
        title=title,
        category_orders={"bucket": bucket_order} if bucket_order else None,
    )
    if as_pct_change:
        fig.update_yaxes(matches=None, ticksuffix="%")
    else:
        fig.update_yaxes(matches=None, tickprefix="£")
    fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
    # Grow the figure with the number of facet rows so a 3x3 grid isn't squished.
    n_facets = means["bucket"].nunique()
    n_rows = -(-n_facets // facet_col_wrap)  # ceil division
    fig.update_layout(
        legend_title_text="",
        height=max(420, 300 * n_rows),
        margin=dict(t=90),
    )
    return fig


def single_bucket_comparison(
    history,
    bucket: str,
    *,
    sources: list[str] | None = None,
    as_pct_change: bool = False,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
) -> go.Figure:
    """Focused comparison of one grouping value across selected sources.

    With ``as_pct_change`` the y-axis shows cumulative % change from each
    source's first quarter (compounded growth) instead of absolute pounds.
    """
    means = analytics.bucket_means(history, include_total=True, dimension=dimension)
    if sources is None:
        sources = [OWN_BRAND, "Nando's", MARKET_LABEL]
    frame = means[(means["bucket"] == bucket) & (means["source"].isin(sources))]
    dim_label = _dimension_label(dimension)
    if as_pct_change:
        frame = analytics.add_pct_change(frame, ["source", "bucket"])
        y, y_label, suffix = "pct_change", "Cumulative price change (%)", " (% change)"
    else:
        y, y_label, suffix = "price", "Avg price (GBP)", ""
    title = (
        f"{bucket}: Wagamama vs {_competitor_label(sources)} "
        f"vs market (excl. Wagamama){suffix}"
    )
    fig = px.line(
        frame,
        x="date",
        y=y,
        color="source",
        markers=True,
        template=_TEMPLATE,
        color_discrete_map=_SOURCE_COLOURS,
        labels={y: y_label, "date": "Quarter", "source": "Source"},
        title=title,
    )
    if as_pct_change:
        fig.update_layout(yaxis_ticksuffix="%", legend_title_text="")
    else:
        fig.update_layout(yaxis_tickprefix="£", legend_title_text="")
    return fig


def latest_price_bars(
    history,
    *,
    sources: list[str] | None = None,
    as_pct_change: bool = False,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
) -> go.Figure:
    """Grouped bar of the latest average price by grouping dimension across sources.

    With ``as_pct_change`` the bars show each series' total % change over the
    whole window (compounded growth from first to latest quarter) instead of the
    latest absolute price.
    """
    if sources is None:
        sources = [OWN_BRAND, "Nando's", MARKET_LABEL]
    dim_label = _dimension_label(dimension)
    if as_pct_change:
        means = analytics.dimension_means(history, dimension=dimension)
        means = analytics.add_pct_change(means, ["source", "bucket"])
        latest = means[means["date"] == means["date"].max()]
        y, y_label, title = "pct_change", "Total price change (%)", (
            f"Total price change by {dim_label.lower()} (full period)"
        )
    else:
        latest = analytics.latest_bucket_prices(history, dimension=dimension)
        y, y_label, title = "price", "Avg price (GBP)", (
            f"Latest average price by {dim_label.lower()}"
        )
    latest = latest[latest["source"].isin(sources)]
    # Pastel palette for competitors/markets, but Wagamama stays brand-red.
    fig = px.bar(
        latest,
        x="bucket",
        y=y,
        color="source",
        barmode="group",
        template=_TEMPLATE,
        color_discrete_map=_source_colour_map(sources),
        labels={y: y_label, "bucket": dim_label, "source": "Source"},
        title=title,
    )
    if as_pct_change:
        fig.update_layout(yaxis_ticksuffix="%", legend_title_text="")
    else:
        fig.update_layout(yaxis_tickprefix="£", legend_title_text="")
    return fig


def pct_change_heatmap(
    history,
    source: str = "Nando's",
    *,
    include_total: bool = False,
    show_title: bool = True,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
) -> go.Figure:
    """Heatmap of quarter-on-quarter % price change by grouping dimension.

    Set ``show_title=False`` to drop the per-chart title when a shared caption
    already describes the column.
    """
    pct = analytics.bucket_pct_change(
        history, source, include_total=include_total, dimension=dimension
    )
    pct = pct.iloc[:, 1:]  # drop the first all-NaN quarter
    # Pin Total to the top row (imshow draws row 0 at the top), then the rest
    # alphabetically. Bold the Total label below.
    rest = sorted(b for b in pct.index if b != TOTAL_LABEL)
    ordered = ([TOTAL_LABEL] if TOTAL_LABEL in pct.index else []) + rest
    pct = pct.loc[ordered]
    quarter_labels = [f"{d.year} Q{(d.month - 1) // 3 + 1}" for d in pct.columns]
    y_labels = [f"<b>{b}</b>" if b == TOTAL_LABEL else str(b) for b in pct.index]
    dim_label = _dimension_label(dimension)
    fig = px.imshow(
        pct,
        x=quarter_labels,
        y=y_labels,
        color_continuous_scale="RdBu_r",
        color_continuous_midpoint=0,
        aspect="auto",
        template=_TEMPLATE,
        labels={"x": "Quarter", "y": dim_label, "color": "% change"},
        title=f"Quarter-on-quarter % price change - {source}" if show_title else None,
    )
    # Force a consistent diagonal, italic quarter axis. Plotly only auto-rotates
    # the labels when they'd collide, so short series would otherwise sit flat.
    fig.update_xaxes(tickangle=-45, tickfont=dict(style="italic"))
    if not show_title:
        fig.update_layout(margin=dict(t=30))
    return fig


# --- Restaurant vs delivery (real scraped snapshot) ------------------------


def delivery_scatter(
    comparison, *, dim: str = "bucket", dim_label: str = "Bucket"
) -> go.Figure:
    """Per-item scatter; points above the parity line carry a delivery premium.

    One colour per ``dim`` value (a single legend entry each), every point a
    plain circle. A separate ``Wagamama`` legend entry — hidden until clicked —
    overlays a brand-red ring on the own-brand dots, so the chart stays clean by
    default but you can reveal exactly which points are Wagamama on demand.
    """
    palette = px.colors.qualitative.Plotly
    dim_values = sorted(str(v) for v in comparison[dim].dropna().unique())
    colour_map = {v: palette[i % len(palette)] for i, v in enumerate(dim_values)}
    has_company = "company" in comparison.columns

    fig = go.Figure()
    for value in dim_values:
        sub = comparison[comparison[dim].astype(str) == value]
        if sub.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=sub["restaurant"],
                y=sub["delivery"],
                mode="markers",
                name=value,
                marker=dict(
                    color=colour_map[value],
                    size=9,
                    line=dict(width=1, color="white"),
                ),
                text=sub["item"],
                hovertemplate=(
                    "%{text}<br>Restaurant £%{x:.2f}<br>Delivery £%{y:.2f}"
                    f"<extra>{value}</extra>"
                ),
            )
        )

    # Toggleable highlight: click the "Wagamama" legend entry to ring its dots.
    if has_company:
        own = comparison[comparison["company"] == OWN_BRAND]
        if not own.empty:
            fig.add_trace(
                go.Scatter(
                    x=own["restaurant"],
                    y=own["delivery"],
                    mode="markers",
                    name=OWN_BRAND,
                    visible="legendonly",
                    marker=dict(
                        color="rgba(0,0,0,0)",
                        size=16,
                        line=dict(width=3, color=WAGAMAMA_RED),
                    ),
                    text=own["item"],
                    hovertemplate=(
                        "%{text}<br>Restaurant £%{x:.2f}<br>Delivery £%{y:.2f}"
                        f"<extra>{OWN_BRAND}</extra>"
                    ),
                )
            )

    hi = float(max(comparison["restaurant"].max(), comparison["delivery"].max())) * 1.05
    fig.add_trace(
        go.Scatter(
            x=[0, hi],
            y=[0, hi],
            mode="lines",
            name="Same price",
            line=dict(color="#94A3B8", dash="dash"),
            hoverinfo="skip",
        )
    )
    fig.update_layout(
        template=_TEMPLATE,
        title="Per-item price: delivery vs restaurant (above dashed line = premium)",
        xaxis_title="Restaurant price (GBP)",
        yaxis_title="Delivery price (GBP)",
        legend_title_text=dim_label,
        xaxis_tickprefix="£",
        yaxis_tickprefix="£",
    )
    return fig


def _price_curve_trace(frame, price_col, cols, *, name, color, showlegend):
    """One sorted-price line for :func:`price_curve`."""
    plot = frame.sort_values(price_col).reset_index(drop=True)
    detail = len(cols) > 1
    head = f"<b>{name}</b><br>" if name else ""
    if detail:
        hovertemplate = head + "%{customdata[0]} (%{customdata[1]})<br>£%{y:.2f}<extra></extra>"
    else:
        hovertemplate = head + "%{customdata[0]}<br>£%{y:.2f}<extra></extra>"
    return go.Scatter(
        x=list(range(1, len(plot) + 1)),
        y=plot[price_col],
        mode="lines+markers",
        name=name or "",
        line=dict(color=color, width=2),
        marker=dict(color=color, size=9),
        customdata=plot[cols].to_numpy(),
        hovertemplate=hovertemplate,
        showlegend=showlegend,
    )


def price_curve(
    frame,
    *,
    title: str,
    price_col: str = "price",
    label_col: str = "item",
    detail_col: str | None = None,
    series_col: str | None = None,
) -> go.Figure:
    """Items sorted low→high as a price curve.

    The high/low analogue of ``delivery_scatter``: it shows the shape of the
    price distribution for whatever subset is currently drilled into, so a steep
    tail flags a few premium items. With ``series_col`` each series is sorted and
    drawn independently (e.g. the market vs the Wagamama benchmark), so their
    curves can be compared directly; the own-brand series is drawn in brand red.
    """
    cols = [label_col] + ([detail_col] if detail_col else [])
    fig = go.Figure()

    if series_col and series_col in frame.columns:
        for name, group in frame.groupby(series_col):
            if group.empty:
                continue
            color = WAGAMAMA_RED if name == OWN_BRAND else _LOW_COLOUR
            fig.add_trace(
                _price_curve_trace(
                    group, price_col, cols, name=str(name), color=color,
                    showlegend=True,
                )
            )
        fig.update_layout(legend_title_text="")
    else:
        fig.add_trace(
            _price_curve_trace(
                frame, price_col, cols, name="", color=_LOW_COLOUR,
                showlegend=False,
            )
        )
        if not frame.empty:
            avg = float(frame[price_col].mean())
            fig.add_hline(
                y=avg,
                line=dict(color=_AVG_COLOUR, dash="dash"),
                annotation_text=f"Avg £{avg:.2f}",
                annotation_position="top left",
            )

    fig.update_layout(
        template=_TEMPLATE,
        title=title,
        xaxis_title="Item (sorted by price)",
        yaxis_title="Price (GBP)",
        yaxis_tickprefix="£",
        height=420,
    )
    return fig


def drilldown_dumbbell(
    frame,
    label_col: str,
    *,
    title: str,
    left_col: str = "restaurant",
    right_col: str = "delivery",
    mid_col: str | None = None,
    left_label: str | None = None,
    right_label: str | None = None,
    mid_label: str | None = None,
    annotate_col: str | None = None,
    highlight_col: str | None = None,
    highlight_label: str = OWN_BRAND,
) -> go.Figure:
    """Generic dumbbell chart with optional middle marker (e.g. average price).

    Rows are drawn in the frame's given order (top row = first row), so callers
    sort before plotting. Each row is pinned to an explicit numeric y-position
    (rather than a categorical axis), so ordering is preserved exactly and
    duplicate labels never collapse into a single slot. ``annotate_col`` names a
    column of ready-made text labels (e.g. ``"+£1.20"``) drawn to the right of
    each dumbbell. ``highlight_col`` (a bool column) splits the middle marker
    into a second, brand-red series so a benchmark like Wagamama reads
    separately in the legend, and bolds that row's axis label.
    """
    # Reverse so the first frame row ends up at the top; index becomes the
    # y-position (0 = bottom). Numeric y avoids categorical re-ordering and
    # duplicate-label collapsing entirely.
    plot = frame.iloc[::-1].reset_index(drop=True)
    ypos = plot.index.tolist()
    labels = plot[label_col].astype(str).tolist()
    fig = go.Figure()

    for y, row in plot.iterrows():
        fig.add_trace(
            go.Scatter(
                x=[row[left_col], row[right_col]],
                y=[y, y],
                mode="lines",
                line=dict(color="#CBD5E1", width=3),
                showlegend=False,
                hoverinfo="skip",
            )
        )

    left_name = left_label or _CHANNEL_LABELS.get(left_col, left_col.title())
    right_name = right_label or _CHANNEL_LABELS.get(right_col, right_col.title())

    fig.add_trace(
        go.Scatter(
            x=plot[left_col],
            y=ypos,
            mode="markers",
            name=left_name,
            marker=dict(
                color=_RESTAURANT_COLOUR if left_col == "restaurant" else _LOW_COLOUR,
                size=12,
            ),
            customdata=[[lbl] for lbl in labels],
            hovertemplate=f"{left_name}: %{{x:.2f}}<extra></extra>",
        )
    )
    if mid_col and mid_col in plot.columns:
        mid_name = mid_label or _HIGH_LOW_LABELS.get(mid_col, mid_col.title())
        if highlight_col and highlight_col in plot.columns:
            flag = plot[highlight_col].astype(bool)
            groups = [
                (~flag, mid_name, _AVG_COLOUR, "diamond"),
                (flag, highlight_label, WAGAMAMA_RED, "star"),
            ]
        else:
            groups = [(plot[label_col].notna(), mid_name, _AVG_COLOUR, "diamond")]
        for sel, name, colour, symbol in groups:
            part = plot[sel]
            if part.empty:
                continue
            fig.add_trace(
                go.Scatter(
                    x=part[mid_col],
                    y=part.index.tolist(),
                    mode="markers",
                    name=name,
                    marker=dict(color=colour, size=12, symbol=symbol),
                    customdata=[[lbl] for lbl in part[label_col].astype(str)],
                    hovertemplate=f"{name}: %{{x:.2f}}<extra></extra>",
                )
            )
    fig.add_trace(
        go.Scatter(
            x=plot[right_col],
            y=ypos,
            mode="markers",
            name=right_name,
            marker=dict(
                color=_DELIVERY_COLOUR if right_col == "delivery" else _HIGH_COLOUR,
                size=12,
            ),
            customdata=[[lbl] for lbl in labels],
            hovertemplate=f"{right_name}: %{{x:.2f}}<extra></extra>",
        )
    )

    if annotate_col and annotate_col in plot.columns:
        x_cols = [c for c in (left_col, right_col, mid_col) if c and c in plot.columns]
        row_max = plot[x_cols].max(axis=1)
        lo = float(plot[x_cols].min(axis=1).min())
        hi = float(row_max.max())
        span = (hi - lo) or hi or 1.0
        # Nudge labels clear of the marker so they don't overlap the dots.
        offset = span * 0.04
        fig.add_trace(
            go.Scatter(
                x=row_max + offset,
                y=ypos,
                mode="text",
                text=["<i>" + str(t) + "</i>" for t in plot[annotate_col]],
                textposition="middle right",
                textfont=dict(size=12, color="#374151"),
                showlegend=False,
                hoverinfo="skip",
                cliponaxis=False,
            )
        )
        fig.update_xaxes(range=[max(0.0, lo - 0.05 * span), hi + 0.34 * span])

    # Make the benchmark (e.g. Wagamama) row's tick label pop: bold + brand red.
    if highlight_col and highlight_col in plot.columns:
        flags = plot[highlight_col].astype(bool).tolist()
        ticktext = [
            f"<b><span style='color:{WAGAMAMA_RED}'>{lbl}</span></b>" if flag else lbl
            for lbl, flag in zip(labels, flags)
        ]
    else:
        ticktext = labels
    fig.update_yaxes(
        tickmode="array",
        tickvals=ypos,
        ticktext=ticktext,
        range=[-0.5, len(plot) - 0.5],
    )
    height = max(400, 40 * len(plot) + 120)
    fig.update_layout(
        template=_TEMPLATE,
        title=title,
        xaxis_tickprefix="£",
        legend_title_text="",
        height=height,
        dragmode=False,
        hovermode="closest",
    )
    return fig


def high_low_dumbbell(
    frame,
    label_col: str,
    *,
    title: str,
    annotate_col: str | None = None,
    highlight_col: str | None = None,
) -> go.Figure:
    """Dumbbell chart for low / average / high price spread."""
    return drilldown_dumbbell(
        frame,
        label_col,
        title=title,
        left_col="low",
        right_col="high",
        mid_col="average",
        left_label=_HIGH_LOW_LABELS["low"],
        right_label=_HIGH_LOW_LABELS["high"],
        mid_label=_HIGH_LOW_LABELS["average"],
        annotate_col=annotate_col,
        highlight_col=highlight_col,
    )


def delivery_dumbbell(comparison, top_n: int = 15) -> go.Figure:
    """Dumbbell chart of the items with the largest delivery markup."""
    frame = comparison.head(top_n)
    return drilldown_dumbbell(
        frame,
        "item",
        title=f"Biggest delivery markups (top {top_n} matched items)",
    )

