"""Plotly figure builders for the price-analytics page.

Each function takes already-shaped data (from ``analytics``) and returns a
Plotly ``Figure``. No Streamlit here, so figures can be reused or exported
(PNG/HTML) outside the app.
"""

from __future__ import annotations

import html

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio

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
# Y-axis names live in the margin. Plotly grows that margin until the longest
# tick fits, so one scraped description can leave a sliver for the range.
# One short line stays recognisable; the full name is on the hover.
_AXIS_LABEL_CHARS = 28


def _date_label(value: object) -> str:
    """Calendar label, matching the trend charts (``Apr 2026``, ``8 Jul 2026``)."""
    stamp = pd.Timestamp(value)
    if stamp.day == 1:
        return f"{stamp:%b %Y}"
    return f"{stamp.day} {stamp:%b %Y}"


def comparison_colour_map(
    history: pd.DataFrame, sources: list[str]
) -> dict[str, str]:
    """One colour per source, shared by the trend line and the bars beside it.

    Named brands keep the colours in ``SOURCE_COLOURS``. Every other source
    takes the next ``plotly_white`` colour, in the order the trend first meets
    them (earliest date, then name). Plotly itself spends one palette slot per
    named brand before it colours the rest; matching that here means a company
    stays the same colour when it moves from the line to the bars, even though
    the two frames list sources in a different order.
    """
    if not sources:
        return {}
    palette = list(pio.templates[_TEMPLATE].layout.colorway or [])
    frame = history.loc[history["source"].isin(sources), ["source", "date"]]
    if frame.empty:
        order = list(sources)
    else:
        first_seen = (
            frame.groupby("source", as_index=False)["date"]
            .min()
            .sort_values(["date", "source"], kind="mergesort")
        )
        order = first_seen["source"].tolist()
        order.extend(source for source in sources if source not in set(order))

    mapping: dict[str, str] = {}
    cursor = len(_SOURCE_COLOURS)
    for source in order:
        named = _SOURCE_COLOURS.get(source)
        if named is not None:
            mapping[source] = named
            continue
        if palette:
            mapping[source] = palette[cursor % len(palette)]
            cursor += 1
    return mapping


def _competitor_label(sources: list[str]) -> str:
    """Human-readable label for the competitor source(s) in a comparison.

    Excludes our own brand and any market series ("Market whole", "Market: X").
    """
    others = [s for s in sources if s != OWN_BRAND and not s.startswith("Market")]
    return ", ".join(others) if others else "competitors"


def _observation_labels(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Turn timestamps into date labels, kept in calendar order.

    Passing a datetime to Plotly makes the axis a clock. Midnight is then drawn
    as a time, and a short series can collapse to a single timestamp even when
    several months of prices are present. Labels such as ``Mar 2025`` are
    categories, so each workbook date stays visible.
    """
    if frame.empty or "date" not in frame.columns:
        return frame, []
    out = frame.copy()
    out["_stamp"] = pd.to_datetime(out["date"])
    out = out.sort_values("_stamp")
    out["date"] = [
        f"{stamp:%b %Y}" if stamp.day == 1 else f"{stamp.day} {stamp:%b %Y}"
        for stamp in out["_stamp"]
    ]
    order = (
        out.drop_duplicates("_stamp")
        .sort_values("_stamp")["date"]
        .tolist()
    )
    return out.drop(columns="_stamp"), order


def _pin_date_axis(fig: go.Figure, order: list[str]) -> go.Figure:
    """Draw one tick per observation date, in calendar order.

    Labels such as ``Mar 2025`` are still date-shaped. Plotly will parse them
    back into a clock, then a narrow chart keeps a single tick at the latest
    month. Positions are plain numbers so that cannot happen; the tick text is
    the date.
    """
    if not order:
        return fig
    index = {label: i for i, label in enumerate(order)}
    for trace in fig.data:
        raw_x = getattr(trace, "x", None)
        if raw_x is None:
            continue
        labels = [str(value) for value in list(raw_x)]
        trace.customdata = labels
        trace.x = [index.get(label, label) for label in labels]
        template = getattr(trace, "hovertemplate", None)
        if isinstance(template, str) and "%{customdata}" not in template:
            trace.hovertemplate = template.replace("%{x}", "%{customdata}")
    fig.update_xaxes(
        type="linear",
        tickmode="array",
        tickvals=list(range(len(order))),
        ticktext=list(order),
        range=[-0.5, len(order) - 0.5],
        tickangle=-40,
    )
    return fig


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
    frame, date_order = _observation_labels(frame)
    fig = px.line(
        frame,
        x="date",
        y="price",
        color="item",
        markers=True,
        template=_TEMPLATE,
        labels={"price": "Price (GBP)", "date": "Date", "item": "Item"},
        title="Individual item price trends - Nando's",
        category_orders={"date": date_order},
    )
    fig.update_layout(yaxis_tickprefix="£", legend_title_text="")
    return _pin_date_axis(fig, date_order)


def total_line(history, source: str = "Nando's", *, indexed: bool = False) -> go.Figure:
    """Line chart of the equal-weighted across-bucket average (Total) over time."""
    means = analytics.bucket_means(history, source, include_total=True)
    totals = means[means["bucket"] == TOTAL_LABEL]
    if indexed:
        totals = analytics.rebase_to_100(totals, ["bucket"])
        y, y_label, prefix, suffix = "index", "Price index (Q1 = 100)", "", " (indexed)"
    else:
        y, y_label, prefix, suffix = "price", "Avg price (GBP)", "£", ""
    totals, date_order = _observation_labels(totals)
    fig = px.line(
        totals,
        x="date",
        y=y,
        markers=True,
        template=_TEMPLATE,
        labels={y: y_label, "date": "Date"},
        title=f"Total average menu price{suffix} - {source}",
        category_orders={"date": date_order},
    )
    fig.update_traces(line=dict(color="#111827", width=3), marker=dict(size=8, symbol="diamond"))
    fig.update_layout(yaxis_tickprefix=prefix, showlegend=False)
    return _pin_date_axis(fig, date_order)


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
    means, date_order = _observation_labels(means)
    fig = px.line(
        means,
        x="date",
        y=y,
        color="bucket",
        markers=True,
        template=_TEMPLATE,
        labels={y: y_label, "date": "Date", "bucket": dim_label},
        title=f"Average price by {dim_label.lower()}{title} - {source}" if show_title else None,
        category_orders={"bucket": bucket_order, "date": date_order},
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
    return _pin_date_axis(fig, date_order)


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
    means, date_order = _observation_labels(means)
    colour_map = comparison_colour_map(history, sources)
    orders: dict[str, list[str]] = {"date": date_order, "source": list(colour_map)}
    if bucket_order:
        orders["bucket"] = bucket_order
    fig = px.line(
        means,
        x="date",
        y=y,
        color="source",
        facet_col="bucket",
        facet_col_wrap=facet_col_wrap,
        markers=True,
        template=_TEMPLATE,
        color_discrete_map=colour_map,
        labels={y: y_label, "date": "Date", "source": "Source"},
        title=title,
        category_orders=orders,
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
    return _pin_date_axis(fig, date_order)


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
    colour_map = comparison_colour_map(history, sources)
    frame, date_order = _observation_labels(frame)
    fig = px.line(
        frame,
        x="date",
        y=y,
        color="source",
        markers=True,
        template=_TEMPLATE,
        color_discrete_map=colour_map,
        labels={y: y_label, "date": "Date", "source": "Source"},
        title=title,
        category_orders={"date": date_order, "source": list(colour_map)},
    )
    if as_pct_change:
        fig.update_layout(yaxis_ticksuffix="%", legend_title_text="")
    else:
        fig.update_layout(yaxis_tickprefix="£", legend_title_text="")
    return _pin_date_axis(fig, date_order)


def latest_price_bars(
    history,
    *,
    sources: list[str] | None = None,
    as_pct_change: bool = False,
    use_latest: bool = True,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
) -> go.Figure:
    """Grouped bar of price level by grouping dimension across sources.

    ``use_latest`` chooses each series' own most recent average (so a brand whose
    history ends earlier still appears) or the average of those quarterly prices
    over the whole window. With ``as_pct_change`` the bars show each series'
    total % change from its first date to its own last date, and ``use_latest``
    is ignored.
    """
    if sources is None:
        sources = [OWN_BRAND, "Nando's", MARKET_LABEL]
    dim_label = _dimension_label(dimension)
    if as_pct_change:
        latest = analytics.total_bucket_pct_change(history, dimension=dimension)
        y, y_label, title = "pct_change", "Total price change (%)", (
            f"Total price change by {dim_label.lower()} (full period)"
        )
    elif use_latest:
        latest = analytics.latest_bucket_prices(history, dimension=dimension)
        y, y_label, title = "price", "Latest price (GBP)", (
            f"Latest price by {dim_label.lower()}"
        )
    else:
        latest = analytics.average_bucket_prices(history, dimension=dimension)
        y, y_label, title = "price", "Average price (GBP)", (
            f"Average price by {dim_label.lower()}"
        )
    latest = latest[latest["source"].isin(sources)].copy()
    labels = {y: y_label, "bucket": dim_label, "source": "Source"}
    hover_data = None
    if "date" in latest.columns:
        latest["as_of"] = latest["date"].map(_date_label)
        labels["as_of"] = "As of"
        hover_data = {"as_of": True}
    colour_map = comparison_colour_map(history, sources)
    fig = px.bar(
        latest,
        x="bucket",
        y=y,
        color="source",
        barmode="group",
        template=_TEMPLATE,
        color_discrete_map=colour_map,
        hover_data=hover_data,
        labels=labels,
        title=title,
        category_orders={"source": list(colour_map)},
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
    """Heatmap of the % price change between successive dates by grouping dimension.

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
    # A calendar date, not a quarter bucket: two observations can fall in the
    # same quarter (October and December 2022) and must stay separate.
    date_labels = [f"{pd.Timestamp(d).day} {pd.Timestamp(d):%b %Y}" for d in pct.columns]
    y_labels = [f"<b>{b}</b>" if b == TOTAL_LABEL else str(b) for b in pct.index]
    dim_label = _dimension_label(dimension)
    fig = px.imshow(
        pct,
        x=date_labels,
        y=y_labels,
        color_continuous_scale="RdBu_r",
        color_continuous_midpoint=0,
        aspect="auto",
        template=_TEMPLATE,
        labels={"x": "Date", "y": dim_label, "color": "% change"},
        title=f"Price change since the previous date - {source}" if show_title else None,
    )
    # Force a consistent diagonal, italic date axis. Plotly only auto-rotates
    # the labels when they'd collide, so short series would otherwise sit flat.
    fig.update_xaxes(tickangle=-45, tickfont=dict(style="italic"))
    if not show_title:
        fig.update_layout(margin=dict(t=30))
    return fig


# --- Restaurant vs delivery (real scraped snapshot) ------------------------


def linear_fit(x, y) -> tuple[float, float] | None:
    """Ordinary least-squares line ``y = intercept + slope * x``.

    Returns ``None`` when there are fewer than two distinct x values, because a
    line is not identified. ``numpy.polyfit`` returns the slope first.
    """
    xs = np.asarray(x, dtype=float)
    ys = np.asarray(y, dtype=float)
    ok = np.isfinite(xs) & np.isfinite(ys)
    xs, ys = xs[ok], ys[ok]
    if xs.size < 2 or np.unique(xs).size < 2:
        return None
    slope, intercept = np.polyfit(xs, ys, 1)
    return float(intercept), float(slope)


def delivery_scatter(
    comparison,
    *,
    dim: str = "bucket",
    dim_label: str = "Bucket",
    benchmark=None,
) -> go.Figure:
    """Per-item scatter; points above the parity line carry a delivery premium.

    One colour per ``dim`` value (a single legend entry each), every point a
    plain circle. A separate ``Wagamama`` legend entry — hidden until clicked —
    overlays a brand-red ring on the own-brand dots, so the chart stays clean by
    default but you can reveal exactly which points are Wagamama on demand.

    ``benchmark`` is Wagamama's own restaurant/delivery pairs. When it is passed
    (a competitor is on the chart, not Wagamama alone), its line of best fit is
    drawn in brand red across the prices actually plotted.
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
    _add_benchmark_fit(fig, benchmark, x_max=hi)
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


def _fit_axis_label(text: str, *, width: int = _AXIS_LABEL_CHARS) -> str:
    """Cut a category name so the plot, not the label, keeps the width.

    ``width`` is a character budget for the tick. A longer name is cut on a
    word boundary and marked with an ellipsis. The caller keeps the original
    string for the hover.
    """
    clean = " ".join(str(text).split())
    if len(clean) <= width:
        return html.escape(clean, quote=False)
    # Leave room for the ellipsis, and don't end mid-word when a space is near.
    clipped = clean[: width - 1].rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    clipped = clipped.rstrip(" -–—,;:/&")
    if len(clipped) < width // 2:
        clipped = clean[: width - 1].rstrip(" -–—,;:/&")
    # Tick text can sit inside a colour span. Escape the dish name only.
    return html.escape(clipped, quote=False) + "…"


def _price_hover(series_name: str) -> str:
    """Hover that leads with the full row name, then the series value."""
    return f"<b>%{{customdata[0]}}</b><br>{series_name}: £%{{x:.2f}}<extra></extra>"


def _ordinal(n: int) -> str:
    """5 -> '5th'. Teens stay 'th' so 11, 12 and 13 are not '11st'."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def percentile_prices(prices, *, buckets: int = 20) -> pd.DataFrame:
    """Price at every ``100 / buckets`` percentile, low to high.

    Twenty buckets means the 5th, 10th, …, 100th percentile. Menus with
    different item counts then share an x-axis, so the lines are comparable.
    The 100th percentile is the dearest item; the 50th is the median.
    """
    if buckets < 1 or 100 % buckets != 0:
        raise ValueError("buckets must be a divisor of 100.")
    step = 100 // buckets
    levels = list(range(step, 101, step))
    clean = pd.to_numeric(pd.Series(prices), errors="coerce").dropna()
    if clean.empty:
        return pd.DataFrame(columns=["percentile", "price"])
    values = clean.quantile([level / 100 for level in levels], interpolation="linear")
    return pd.DataFrame(
        {"percentile": levels, "price": [float(v) for v in values.to_numpy()]}
    )


def _add_benchmark_fit(fig: go.Figure, benchmark, *, x_max: float) -> None:
    """Draw Wagamama's best-fit line out to the same x as the y = x line."""
    if benchmark is None or getattr(benchmark, "empty", True):
        return
    fit = linear_fit(benchmark["restaurant"], benchmark["delivery"])
    if fit is None or not np.isfinite(x_max) or x_max <= 0:
        return
    intercept, slope = fit
    fig.add_trace(
        go.Scatter(
            x=[0, x_max],
            y=[
                round(intercept, 2),
                round(intercept + slope * x_max, 2),
            ],
            mode="lines",
            name=f"{OWN_BRAND} best fit",
            line=dict(color="rgba(209, 19, 23, 0.45)", width=2, dash="dot"),
            hovertemplate=(
                f"{OWN_BRAND} best fit<br>Restaurant £%{{x:.2f}}"
                "<br>Delivery £%{y:.2f}<extra></extra>"
            ),
        )
    )


def _percentile_curve_trace(frame, price_col, *, name, color):
    """One percentile line for :func:`price_curve`."""
    curve = percentile_prices(frame[price_col])
    if curve.empty:
        return None
    ordinals = [_ordinal(int(level)) for level in curve["percentile"]]
    return go.Scatter(
        x=curve["percentile"],
        y=curve["price"],
        mode="lines+markers",
        name=name or "",
        line=dict(color=color, width=2),
        marker=dict(color=color, size=8),
        customdata=ordinals,
        hovertemplate=(
            (f"<b>{name}</b><br>" if name else "")
            + "%{customdata} percentile<br>£%{y:.2f}<extra></extra>"
        ),
        showlegend=bool(name),
    )


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
    """Price distribution, low to high.

    With ``series_col`` each series is drawn at the same percentiles (5th, 10th,
    …, 100th — twenty points). Item rank would stretch a long menu further along
    the x-axis than a short one, so the lines would not be comparable. The
    own-brand series is brand red.

    Without ``series_col`` each item is one point, sorted cheap to dear, with a
    dashed average.
    """
    cols = [label_col] + ([detail_col] if detail_col else [])
    fig = go.Figure()
    xaxis_title = "Item (sorted by price)"

    if series_col and series_col in frame.columns:
        xaxis_title = "Percentile"
        for name, group in frame.groupby(series_col):
            if group.empty:
                continue
            color = WAGAMAMA_RED if name == OWN_BRAND else _LOW_COLOUR
            trace = _percentile_curve_trace(
                group, price_col, name=str(name), color=color
            )
            if trace is not None:
                fig.add_trace(trace)
        fig.update_layout(legend_title_text="")
        fig.update_xaxes(range=[0, 105], dtick=10)
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
        xaxis_title=xaxis_title,
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
    subtitle: str | None = None,
) -> go.Figure:
    """Generic dumbbell chart with optional middle marker (e.g. average price).

    Rows are drawn in the frame's given order (top row = first row), so callers
    sort before plotting. Each row is pinned to an explicit numeric y-position
    (rather than a categorical axis), so ordering is preserved exactly and
    duplicate labels never collapse into a single slot. Axis names are capped at
    one short line so a long dish name cannot steal the plot width; the full
    name stays on the hover. ``annotate_col`` names a column of ready-made text
    labels (e.g. ``"+£1.20 vs avg"``) drawn in the margin past the right-hand
    dot, where they cannot collide with the markers. ``subtitle`` is a second
    line under the title, used when that margin label needs a sentence to say
    what it measures. ``highlight_col`` (a bool column) splits the middle marker
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
            hovertemplate=_price_hover(left_name),
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
                    hovertemplate=_price_hover(name),
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
            hovertemplate=_price_hover(right_name),
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
        # "+£1.20" fits in the old pad. "vs avg" needs more of the axis or the
        # words clip against the edge of a half-width chart.
        longest = max(len(str(text)) for text in plot[annotate_col])
        right_pad = 0.34 if longest <= 8 else 0.58
        fig.update_xaxes(range=[max(0.0, lo - 0.05 * span), hi + right_pad * span])

    # Short ticks keep automargin from handing the width to one long name.
    # The benchmark row (e.g. Wagamama) stays bold and brand red.
    fitted = [_fit_axis_label(lbl) for lbl in labels]
    if highlight_col and highlight_col in plot.columns:
        flags = plot[highlight_col].astype(bool).tolist()
        ticktext = [
            f"<b><span style='color:{WAGAMAMA_RED}'>{lbl}</span></b>" if flag else lbl
            for lbl, flag in zip(fitted, flags)
        ]
    else:
        ticktext = fitted
    fig.update_yaxes(
        tickmode="array",
        tickvals=ypos,
        ticktext=ticktext,
        range=[-0.5, len(plot) - 0.5],
    )
    height = max(400, 40 * len(plot) + 120)
    title_layout: str | dict = title
    if subtitle:
        title_layout = {
            "text": title,
            "subtitle": {"text": subtitle, "font": {"size": 13, "color": "#4B5563"}},
        }
        height += 28
    fig.update_layout(
        template=_TEMPLATE,
        title=title_layout,
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
    mid_label: str | None = None,
    subtitle: str | None = None,
) -> go.Figure:
    """Dumbbell chart for low / average / high price spread.

    ``mid_label`` renames the middle marker. Company and course rows use
    "Average" (the mean price). Item rows pass "Item price", because that
    marker is the dish itself and the margin label is its gap versus the
    group average.
    """
    return drilldown_dumbbell(
        frame,
        label_col,
        title=title,
        left_col="low",
        right_col="high",
        mid_col="average",
        left_label=_HIGH_LOW_LABELS["low"],
        right_label=_HIGH_LOW_LABELS["high"],
        mid_label=mid_label or _HIGH_LOW_LABELS["average"],
        annotate_col=annotate_col,
        highlight_col=highlight_col,
        subtitle=subtitle,
    )


def delivery_dumbbell(comparison, top_n: int = 15) -> go.Figure:
    """Dumbbell chart of the items with the largest delivery markup."""
    frame = comparison.head(top_n)
    return drilldown_dumbbell(
        frame,
        "item",
        title=f"Biggest delivery markups (top {top_n} matched items)",
    )

