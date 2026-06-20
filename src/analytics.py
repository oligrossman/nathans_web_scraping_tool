"""Pure transformations over the tidy price-history dataset.

Every function takes and returns a DataFrame (or a small dataclass) and never
touches Streamlit or Plotly. Keeping the analytics logic free of UI/plotting
makes it trivial to test and reuse.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .branding import LEGACY_OWN_BRAND, OWN_BRAND
from .synthetic import BUCKETS, MARKET_LABEL, TOTAL_LABEL

DEFAULT_HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "price_history.csv"
DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parent.parent / "data" / "company_weights.csv"


def load_history(path: str | Path = DEFAULT_HISTORY_PATH) -> pd.DataFrame:
    """Load the tidy price history with ``date`` parsed to datetime."""
    frame = pd.read_csv(path, parse_dates=["date"])
    frame["item"] = frame["item"].fillna("")
    # Keep in sync with branding renames (WagaNath → Wagamama).
    frame["source"] = frame["source"].replace({LEGACY_OWN_BRAND: OWN_BRAND})
    return frame


def load_company_weights(path: str | Path = DEFAULT_WEIGHTS_PATH) -> dict[str, float]:
    """Return ``{company: restaurant_count}`` from the weights CSV."""
    weights_df = pd.read_csv(path)
    return dict(zip(weights_df["company"], weights_df["restaurants"].astype(float)))


def competitor_sources(history: pd.DataFrame) -> list[str]:
    """Companies that feed the derived Market benchmark (excludes Wagamama/Market)."""
    return sorted(
        s
        for s in history["source"].unique()
        if s not in (OWN_BRAND, MARKET_LABEL)
    )


def with_derived_market(
    history: pd.DataFrame, weights: dict[str, float] | None = None
) -> pd.DataFrame:
    """Append a Market series = restaurant-count-weighted avg of competitors.

    For each ``(bucket, date)``: ``Market = sum(w_c * price_c) / sum(w_c)`` over
    competitor companies present in both history and weights.
    """
    if weights is None:
        weights = load_company_weights()

    base = history[history["source"] != MARKET_LABEL].copy()
    competitors = [c for c in competitor_sources(base) if c in weights]
    if not competitors:
        return base

    comp_means = bucket_means(base)
    comp_means = comp_means[comp_means["source"].isin(competitors)]

    market_rows: list[dict] = []
    for (dt, bucket), group in comp_means.groupby(["date", "bucket"]):
        w = group["source"].map(weights).astype(float)
        price = (
            float((group["price"] * w).sum() / w.sum())
            if w.sum() > 0
            else float(group["price"].mean())
        )
        market_rows.append(
            {
                "date": dt,
                "bucket": bucket,
                "source": MARKET_LABEL,
                "item": "",
                "price": round(price, 2),
            }
        )

    market = pd.DataFrame(market_rows)

    return pd.concat([base, market], ignore_index=True).sort_values(
        ["source", "bucket", "item", "date"]
    ).reset_index(drop=True)


def add_total_bucket(means: pd.DataFrame) -> pd.DataFrame:
    """Append a ``Total`` row = equal-weighted mean across buckets per date/source."""
    totals = means.groupby(["date", "source"], as_index=False)["price"].mean()
    totals["price"] = totals["price"].round(2)
    totals["bucket"] = TOTAL_LABEL
    return pd.concat([means, totals], ignore_index=True)


def bucket_means(
    history: pd.DataFrame,
    source: str | None = None,
    *,
    include_total: bool = False,
) -> pd.DataFrame:
    """Average price per bucket per quarter, optionally for one source.

    Returns long format with columns ``date``, ``source``, ``bucket``, ``price``.
    When ``include_total`` is True, appends an equal-weighted across-bucket mean.
    """
    frame = history if source is None else history[history["source"] == source]
    means = frame.groupby(["date", "source", "bucket"], as_index=False)["price"].mean()
    means["price"] = means["price"].round(2)
    if include_total:
        means = add_total_bucket(means)
    return means


def rebase_to_100(frame: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """Add an ``index`` column rebasing each group's price to 100 at its start.

    A price index strips out absolute-level differences so a board can compare
    the *rate* of change across items/buckets/sources on one axis.
    """
    out = frame.sort_values("date").copy()
    first = out.groupby(group_cols)["price"].transform("first")
    out["index"] = (out["price"] / first * 100.0).round(1)
    return out


def latest_bucket_prices(history: pd.DataFrame) -> pd.DataFrame:
    """Most-recent-quarter average price per source and bucket."""
    latest_date = history["date"].max()
    latest = history[history["date"] == latest_date]
    return (
        latest.groupby(["source", "bucket"], as_index=False)["price"].mean().round(2)
    )


def bucket_pct_change(
    history: pd.DataFrame,
    source: str,
    *,
    include_total: bool = False,
) -> pd.DataFrame:
    """Wide table of quarter-on-quarter % change per bucket for one source.

    Shaped for a heatmap: rows = bucket, columns = quarter.
    """
    means = bucket_means(history, source, include_total=include_total)
    wide = means.pivot(index="bucket", columns="date", values="price")
    pct = wide.pct_change(axis=1) * 100.0
    return pct.round(1)


def _cagr(first: float, last: float, years: float) -> float:
    if first <= 0 or years <= 0:
        return 0.0
    return ((last / first) ** (1.0 / years) - 1.0) * 100.0


@dataclass(slots=True)
class Kpis:
    """Headline numbers for the KPI cards."""

    overall_cagr_pct: float
    total_change_pct: float
    fastest_bucket: str
    fastest_bucket_change_pct: float
    latest_avg_by_source: dict[str, float]


def compute_kpis(history: pd.DataFrame) -> Kpis:
    """Compute board-level headline metrics across the whole dataset."""
    dates = history["date"]
    span_years = (dates.max() - dates.min()).days / 365.25

    by_date = history.groupby("date")["price"].mean()
    first_avg, last_avg = by_date.iloc[0], by_date.iloc[-1]

    bucket_first_last = (
        history.groupby(["bucket", "date"])["price"]
        .mean()
        .groupby("bucket")
        .agg(["first", "last"])
    )
    bucket_change = (
        (bucket_first_last["last"] / bucket_first_last["first"] - 1.0) * 100.0
    )
    fastest_bucket = bucket_change.idxmax()

    latest_date = dates.max()
    latest_avg = (
        history[history["date"] == latest_date]
        .groupby("source")["price"]
        .mean()
        .round(2)
        .to_dict()
    )

    return Kpis(
        overall_cagr_pct=round(_cagr(first_avg, last_avg, span_years), 1),
        total_change_pct=round((last_avg / first_avg - 1.0) * 100.0, 1),
        fastest_bucket=str(fastest_bucket),
        fastest_bucket_change_pct=round(float(bucket_change.max()), 1),
        latest_avg_by_source=latest_avg,
    )
