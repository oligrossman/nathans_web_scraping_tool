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
from .synthetic import TOTAL_LABEL

_TEMPLATE = "plotly_white"
# Source colours imported from branding — Wagamama red is the hero series.
_SOURCE_COLOURS = SOURCE_COLOURS
# Channel colours for collection vs delivery views.
_COLLECTION_COLOUR = WAGAMAMA_RED
_DELIVERY_COLOUR = DELIVEROO_TEAL
_LOW_COLOUR = "#2563EB"  # blue
_HIGH_COLOUR = "#DC2626"  # red
_AVG_COLOUR = "#111827"  # near-black
_CHANNEL_LABELS = {"collection": "Collection", "delivery": "Delivery (Deliveroo)"}
_HIGH_LOW_LABELS = {"low": "Lowest", "average": "Average", "high": "Highest"}
_CHANNEL_COLOURS = {
    _CHANNEL_LABELS["collection"]: _COLLECTION_COLOUR,
    _CHANNEL_LABELS["delivery"]: _DELIVERY_COLOUR,
}


def _competitor_label(sources: list[str]) -> str:
    """Human-readable label for the competitor source(s) in a comparison."""
    others = [s for s in sources if s not in (OWN_BRAND, "Market")]
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


def bucket_line(
    history,
    source: str = "Nando's",
    *,
    indexed: bool = False,
    include_total: bool = False,
) -> go.Figure:
    """Average price by bucket over time for one source.

    When ``indexed`` is True the series are rebased to 100 at the first quarter,
    which compares the *rate* of change rather than absolute price levels.
    When ``include_total`` is True, an equal-weighted across-bucket mean is added.
    """
    means = analytics.bucket_means(history, source, include_total=include_total)
    if indexed:
        means = analytics.rebase_to_100(means, ["bucket"])
        y, y_label, prefix, title = "index", "Price index (Q1 = 100)", "", " (indexed)"
    else:
        y, y_label, prefix, title = "price", "Avg price (GBP)", "£", ""
    fig = px.line(
        means,
        x="date",
        y=y,
        color="bucket",
        markers=True,
        template=_TEMPLATE,
        labels={y: y_label, "date": "Quarter", "bucket": "Bucket"},
        title=f"Average price by bucket{title} - {source}",
        category_orders={"bucket": list(means["bucket"].unique())},
    )
    if include_total:
        _style_total_trace(fig)
    fig.update_layout(yaxis_tickprefix=prefix, legend_title_text="")
    return fig


def comparison_facets(
    history,
    *,
    sources: list[str] | None = None,
    include_total: bool = False,
) -> go.Figure:
    """Small-multiple line charts: Wagamama vs selected competitor(s) vs Market."""
    means = analytics.bucket_means(history, include_total=include_total)
    if sources is None:
        sources = [OWN_BRAND, "Nando's", "Market"]
    means = means[means["source"].isin(sources)]
    bucket_order = (
        [TOTAL_LABEL, "Starters", "Mains", "Sides", "Drinks", "Desserts"]
        if include_total
        else None
    )
    title = f"Price trends by bucket - Wagamama vs {_competitor_label(sources)} vs Market"
    fig = px.line(
        means,
        x="date",
        y="price",
        color="source",
        facet_col="bucket",
        facet_col_wrap=3,
        markers=True,
        template=_TEMPLATE,
        color_discrete_map=_SOURCE_COLOURS,
        labels={"price": "Avg price (GBP)", "date": "Quarter", "source": "Source"},
        title=title,
        category_orders={"bucket": bucket_order} if bucket_order else None,
    )
    fig.update_yaxes(matches=None, tickprefix="£")
    fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
    fig.update_layout(legend_title_text="")
    return fig


def single_bucket_comparison(
    history,
    bucket: str,
    *,
    sources: list[str] | None = None,
    indexed: bool = False,
) -> go.Figure:
    """Focused comparison of one bucket (or Total) across selected sources."""
    means = analytics.bucket_means(history, include_total=True)
    if sources is None:
        sources = [OWN_BRAND, "Nando's", "Market"]
    frame = means[(means["bucket"] == bucket) & (means["source"].isin(sources))]
    if indexed:
        frame = analytics.rebase_to_100(frame, ["source"])
        y, y_label, prefix, suffix = "index", "Price index (Q1 = 100)", "", " (indexed)"
    else:
        y, y_label, prefix, suffix = "price", "Avg price (GBP)", "£", ""
    title = f"{bucket}: Wagamama vs {_competitor_label(sources)} vs Market{suffix}"
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
    fig.update_layout(yaxis_tickprefix=prefix, legend_title_text="")
    return fig


def latest_price_bars(
    history,
    *,
    sources: list[str] | None = None,
) -> go.Figure:
    """Grouped bar of the latest average price by bucket across sources."""
    latest = analytics.latest_bucket_prices(history)
    if sources is None:
        sources = [OWN_BRAND, "Nando's", "Market"]
    latest = latest[latest["source"].isin(sources)]
    fig = px.bar(
        latest,
        x="bucket",
        y="price",
        color="source",
        barmode="group",
        template=_TEMPLATE,
        color_discrete_map=_SOURCE_COLOURS,
        labels={"price": "Avg price (GBP)", "bucket": "Bucket", "source": "Source"},
        title="Latest average price by bucket",
    )
    fig.update_layout(yaxis_tickprefix="£", legend_title_text="")
    return fig


def pct_change_heatmap(
    history,
    source: str = "Nando's",
    *,
    include_total: bool = False,
) -> go.Figure:
    """Heatmap of quarter-on-quarter % price change by bucket."""
    pct = analytics.bucket_pct_change(history, source, include_total=include_total)
    pct = pct.iloc[:, 1:]  # drop the first all-NaN quarter
    quarter_labels = [f"{d.year} Q{(d.month - 1) // 3 + 1}" for d in pct.columns]
    fig = px.imshow(
        pct,
        x=quarter_labels,
        y=pct.index,
        color_continuous_scale="RdBu_r",
        color_continuous_midpoint=0,
        aspect="auto",
        template=_TEMPLATE,
        labels={"x": "Quarter", "y": "Bucket", "color": "% change"},
        title=f"Quarter-on-quarter % price change - {source}",
    )
    return fig


# --- Collection vs delivery (real scraped snapshot) ------------------------


def delivery_bucket_bars(summary) -> go.Figure:
    """Grouped bars of average collection vs delivery price per bucket."""
    long = summary.melt(
        id_vars="bucket",
        value_vars=["collection", "delivery"],
        var_name="channel",
        value_name="price",
    )
    long["channel"] = long["channel"].map(_CHANNEL_LABELS)
    fig = px.bar(
        long,
        x="bucket",
        y="price",
        color="channel",
        barmode="group",
        template=_TEMPLATE,
        color_discrete_map=_CHANNEL_COLOURS,
        labels={"price": "Avg price (GBP)", "bucket": "Bucket", "channel": ""},
        title="Average price by bucket: collection vs delivery",
    )
    fig.update_layout(yaxis_tickprefix="£", legend_title_text="")
    return fig


def delivery_scatter(comparison) -> go.Figure:
    """Per-item scatter; points above the parity line carry a delivery premium."""
    fig = px.scatter(
        comparison,
        x="collection",
        y="delivery",
        color="bucket",
        hover_name="item",
        template=_TEMPLATE,
        labels={
            "collection": "Collection price (GBP)",
            "delivery": "Delivery price (GBP)",
            "bucket": "Bucket",
        },
        title="Per-item price: delivery vs collection (above dashed line = premium)",
    )
    hi = float(max(comparison["collection"].max(), comparison["delivery"].max())) * 1.05
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
    fig.update_layout(xaxis_tickprefix="£", yaxis_tickprefix="£")
    return fig


def drilldown_dumbbell(
    frame,
    label_col: str,
    *,
    title: str,
    left_col: str = "collection",
    right_col: str = "delivery",
    mid_col: str | None = None,
    left_label: str | None = None,
    right_label: str | None = None,
    mid_label: str | None = None,
) -> go.Figure:
    """Generic dumbbell chart with optional middle marker (e.g. average price)."""
    plot = frame.iloc[::-1].copy()
    labels = plot[label_col].astype(str).tolist()
    fig = go.Figure()

    for _, row in plot.iterrows():
        label = row[label_col]
        fig.add_trace(
            go.Scatter(
                x=[row[left_col], row[right_col]],
                y=[label, label],
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
            y=plot[label_col],
            mode="markers",
            name=left_name,
            marker=dict(
                color=_COLLECTION_COLOUR if left_col == "collection" else _LOW_COLOUR,
                size=12,
            ),
            customdata=[[lbl] for lbl in labels],
            hovertemplate=f"{left_name}: %{{x:.2f}}<extra></extra>",
        )
    )
    if mid_col and mid_col in plot.columns:
        mid_name = mid_label or _HIGH_LOW_LABELS.get(mid_col, mid_col.title())
        fig.add_trace(
            go.Scatter(
                x=plot[mid_col],
                y=plot[label_col],
                mode="markers",
                name=mid_name,
                marker=dict(color=_AVG_COLOUR, size=10, symbol="diamond"),
                customdata=[[lbl] for lbl in labels],
                hovertemplate=f"{mid_name}: %{{x:.2f}}<extra></extra>",
            )
        )
    fig.add_trace(
        go.Scatter(
            x=plot[right_col],
            y=plot[label_col],
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


def high_low_dumbbell(frame, label_col: str, *, title: str) -> go.Figure:
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
    )


def delivery_dumbbell(comparison, top_n: int = 15) -> go.Figure:
    """Dumbbell chart of the items with the largest delivery markup."""
    frame = comparison.head(top_n)
    return drilldown_dumbbell(
        frame,
        "item",
        title=f"Biggest delivery markups (top {top_n} matched items)",
    )
