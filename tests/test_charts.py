"""Percentile price curves and the Wagamama delivery best-fit line."""

from __future__ import annotations

import pandas as pd
import pytest

import plotly.express as px

from src.analytics import dimension_means
from src.branding import SOURCE_COLOURS, WAGAMAMA_RED
from src.charts import (
    bucket_line,
    delivery_scatter,
    high_low_dumbbell,
    latest_price_bars,
    linear_fit,
    pct_change_heatmap,
    percentile_prices,
    price_curve,
    single_bucket_comparison,
)


def test_trend_axis_lists_each_workbook_date() -> None:
    history = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2026-04-01", "2025-11-01", "2025-03-01", "2025-05-01"]
            ),
            "source": ["Banana Tree"] * 4,
            "bucket": ["Mains"] * 4,
            "price": [8.8, 8.3, 8.0, 8.5],
        }
    )

    fig = bucket_line(history, "Banana Tree", show_title=False)

    assert list(fig.layout.xaxis.ticktext) == [
        "Mar 2025",
        "May 2025",
        "Nov 2025",
        "Apr 2026",
    ]
    assert list(fig.data[0].x) == [0, 1, 2, 3]
    assert fig.layout.xaxis.type == "linear"
    assert "00:00" not in " ".join(fig.layout.xaxis.ticktext)


def test_heatmap_labels_are_calendar_dates() -> None:
    history = pd.DataFrame(
        {
            "date": pd.to_datetime(["2022-10-01", "2022-12-01", "2026-07-08"]),
            "source": ["Zizzi", "Zizzi", "Zizzi"],
            "bucket": ["Mains", "Mains", "Mains"],
            "price": [10.0, 11.0, 12.0],
        }
    )

    fig = pct_change_heatmap(history, "Zizzi")

    assert list(fig.data[0].x) == ["1 Dec 2022", "8 Jul 2026"]


def test_percentile_prices_uses_twenty_shared_steps() -> None:
    prices = pd.Series([10, 20, 30, 40])

    curve = percentile_prices(prices)

    assert curve["percentile"].tolist() == list(range(5, 101, 5))
    assert curve.loc[curve["percentile"] == 100, "price"].item() == 40
    assert curve.loc[curve["percentile"] == 50, "price"].item() == prices.quantile(0.5)
    assert curve["price"].is_monotonic_increasing


def test_price_curve_aligns_menus_of_different_lengths() -> None:
    frame = pd.DataFrame(
        {
            "item": ["a", "b", "c", "d", "e"],
            "price": [4.0, 8.0, 1.0, 3.0, 12.0],
            "series": ["Competitors", "Competitors", "Competitors", "Wagamama", "Wagamama"],
        }
    )

    fig = price_curve(frame, series_col="series", title="Percentiles")

    assert [list(trace.x) for trace in fig.data] == [list(range(5, 101, 5))] * 2
    wag = next(trace for trace in fig.data if trace.name == "Wagamama")
    assert wag.line.color == WAGAMAMA_RED
    assert fig.layout.xaxis.title.text == "Percentile"


def test_long_axis_name_stays_narrow_and_hover_keeps_it() -> None:
    """A scraped description must not become the width of the range chart."""
    long_name = "182 kcal - slightly smaller portion, ideal as a side or starter " + ("noodle " * 20)
    frame = pd.DataFrame(
        {
            "item": ["Phở house", long_name],
            "low": [1.0, 1.0],
            "average": [12.5, 4.0],
            "high": [29.95, 29.95],
            "is_benchmark": [True, False],
        }
    )

    fig = high_low_dumbbell(
        frame, "item", title="Within bucket range", highlight_col="is_benchmark"
    )

    ticks = list(fig.layout.yaxis.ticktext)
    assert len(ticks) == 2
    for tick in ticks:
        visible = tick.replace("<b>", "").replace("</b>", "")
        visible = visible.replace("<span style='color:#D11317'>", "").replace("</span>", "")
        assert "<br>" not in visible
        assert len(visible) <= 28
    assert any("…" in tick for tick in ticks)
    assert any("D11317" in tick and "Phở house" in tick for tick in ticks)

    lowest = next(trace for trace in fig.data if trace.name == "Lowest")
    assert long_name in [row[0] for row in lowest.customdata]
    assert "%{customdata[0]}" in lowest.hovertemplate


def test_linear_fit_recovers_a_known_line() -> None:
    intercept, slope = linear_fit([1, 2, 3], [2, 4, 6])
    assert intercept == pytest.approx(0.0)
    assert slope == pytest.approx(2.0)
    assert linear_fit([5], [9]) is None


def test_delivery_scatter_draws_wagamama_fit_only_when_asked() -> None:
    comparison = pd.DataFrame(
        {
            "item": ["A", "B"],
            "restaurant": [10.0, 20.0],
            "delivery": [12.0, 24.0],
            "bucket": ["Mains", "Mains"],
            "company": ["Nando's", "Nando's"],
        }
    )
    benchmark = pd.DataFrame(
        {"restaurant": [10.0, 20.0, 30.0], "delivery": [11.0, 22.0, 33.0]}
    )

    with_fit = delivery_scatter(comparison, benchmark=benchmark)
    fit = next(trace for trace in with_fit.data if trace.name == "Wagamama best fit")
    assert fit.line.dash == "dot"
    assert fit.line.color == "rgba(209, 19, 23, 0.45)"
    same_price = next(trace for trace in with_fit.data if trace.name == "Same price")
    assert list(fit.x) == list(same_price.x)
    assert list(fit.y) == [0.0, 27.72]

    plain = delivery_scatter(comparison)
    assert "Wagamama best fit" not in [trace.name for trace in plain.data]


def _trace_colour(trace) -> str:
    colour = getattr(getattr(trace, "line", None), "color", None)
    if not colour:
        colour = trace.marker.color
    if isinstance(colour, (list, tuple)):
        colour = colour[0]
    return str(colour).lower()


def _series_y(fig, name: str) -> list[float]:
    trace = next(trace for trace in fig.data if trace.name == name)
    return [float(value) for value in trace.y]


def _mixed_end_dates() -> tuple[pd.DataFrame, list[str]]:
    """Wagamama's history ends in April; competitors are scraped in July."""
    history = pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    "2026-01-01",
                    "2026-04-01",
                    "2026-01-01",
                    "2026-07-08",
                    "2025-03-01",
                    "2026-07-08",
                    "2026-01-01",
                    "2026-07-08",
                    "2026-01-01",
                    "2026-07-08",
                ]
            ),
            "source": [
                "Wagamama",
                "Wagamama",
                "Nando's",
                "Nando's",
                "Banana Tree",
                "Banana Tree",
                "Pizza Express",
                "Pizza Express",
                "Market whole",
                "Market whole",
            ],
            "bucket": ["Mains"] * 10,
            "price": [10.0, 14.0, 8.0, 12.0, 7.0, 11.0, 9.0, 13.0, 8.5, 12.5],
        }
    )
    sources = ["Wagamama", "Banana Tree", "Nando's", "Pizza Express", "Market whole"]
    return history, sources


def test_bars_keep_wagamama_on_its_own_latest_date() -> None:
    history, sources = _mixed_end_dates()

    latest = latest_price_bars(history, sources=sources, use_latest=True)
    average = latest_price_bars(history, sources=sources, use_latest=False)

    assert _series_y(latest, "Wagamama") == [14.0]
    assert _series_y(latest, "Nando's") == [12.0]
    assert latest.layout.title.text == "Latest price by course (bucket)"
    wag = next(trace for trace in latest.data if trace.name == "Wagamama")
    assert "Apr 2026" in str(wag.customdata)

    assert _series_y(average, "Wagamama") == [12.0]
    assert _series_y(average, "Nando's") == [10.0]
    assert average.layout.title.text == "Average price by course (bucket)"

    changed = latest_price_bars(history, sources=sources, as_pct_change=True)
    assert _series_y(changed, "Wagamama") == [40.0]


def test_bar_colours_match_the_focus_trend() -> None:
    history, sources = _mixed_end_dates()
    means = dimension_means(history)
    oracle_frame = means[means["bucket"] == "Mains"].sort_values(["date", "source"])
    oracle = px.line(
        oracle_frame,
        x="date",
        y="price",
        color="source",
        color_discrete_map=SOURCE_COLOURS,
        template="plotly_white",
    )
    expected = {trace.name: _trace_colour(trace) for trace in oracle.data}

    line = single_bucket_comparison(history, "Mains", sources=sources)
    bars = latest_price_bars(history, sources=sources)
    assert {trace.name for trace in bars.data} == set(expected)

    for trace in line.data:
        assert _trace_colour(trace) == expected[trace.name]
    for trace in bars.data:
        assert _trace_colour(trace) == expected[trace.name]
    assert expected["Wagamama"] == WAGAMAMA_RED.lower()
    assert expected["Nando's"] == SOURCE_COLOURS["Nando's"].lower()
    assert expected["Market whole"] == SOURCE_COLOURS["Market whole"].lower()
