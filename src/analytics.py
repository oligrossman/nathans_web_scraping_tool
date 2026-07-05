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
from .enrichment.identity import make_dish_id
from .enrichment.schema import GroupingDimension, MarketDefinition, UNMAPPED_LABEL
from .enrichment.store import DEFAULT_DB_PATH, get_resolved_mappings, init_db
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
    """Real competitor companies only.

    Excludes Wagamama (own brand) and every market series — the generic
    "Market whole" as well as any custom "Market: X" — since a market is a
    benchmark, not a company you can select.
    """
    return sorted(
        s
        for s in history["source"].unique()
        if s != OWN_BRAND and not str(s).startswith("Market")
    )


def with_derived_market(
    history: pd.DataFrame,
    weights: dict[str, float] | None = None,
    *,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> pd.DataFrame:
    """Append a Market series = restaurant-count-weighted avg of competitors.

    For ``GroupingDimension.BUCKET`` the input history is used directly.
    For other dimensions the history is enriched from SQLite mappings first.
    """
    if weights is None:
        weights = load_company_weights()

    base = history[history["source"] != MARKET_LABEL].copy()
    competitors = [c for c in competitor_sources(base) if c in weights]
    if not competitors:
        return base

    dim = dimension_column(dimension)
    if dim != GroupingDimension.BUCKET.value:
        base = enriched_history(base, db_path=db_path)

    comp_means = dimension_means(base, dimension=dimension)
    comp_means = comp_means[comp_means["source"].isin(competitors)]

    market_rows: list[dict] = []
    for (dt, bucket), group in comp_means.groupby(["date", "bucket"]):
        w = group["source"].map(weights).astype(float)
        price = (
            float((group["price"] * w).sum() / w.sum())
            if w.sum() > 0
            else float(group["price"].mean())
        )
        row = {
            "date": dt,
            "bucket": bucket,
            "source": MARKET_LABEL,
            "item": "",
            "price": round(price, 2),
        }
        # Carry the active dimension value so later dimension_means keeps this row.
        row[dim] = bucket
        market_rows.append(row)

    market = pd.DataFrame(market_rows)
    combined = pd.concat([base, market], ignore_index=True)
    return combined.sort_values(["source", "bucket", "item", "date"]).reset_index(
        drop=True
    )


def prepare_chart_history(
    history: pd.DataFrame,
    *,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
    custom_markets: list[MarketDefinition] | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    """History with default Market and any selected custom market series."""
    frame = with_derived_market(
        history,
        weights=weights,
        dimension=dimension,
        db_path=db_path,
    )
    if custom_markets:
        frame = with_custom_markets(
            frame,
            custom_markets,
            dimension=dimension,
            db_path=db_path,
            weights=weights,
        )
    return frame


def focus_options_for_history(
    history: pd.DataFrame,
    *,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
) -> list[str]:
    """Selectable focus values for the comparison tab."""
    means = dimension_means(history, include_total=True, dimension=dimension)
    values = sorted(means["bucket"].unique(), key=lambda v: (v != TOTAL_LABEL, v))
    return values


def add_total_bucket(means: pd.DataFrame) -> pd.DataFrame:
    """Append a ``Total`` row = equal-weighted mean across buckets per date/source."""
    totals = means.groupby(["date", "source"], as_index=False)["price"].mean()
    totals["price"] = totals["price"].round(2)
    totals["bucket"] = TOTAL_LABEL
    return pd.concat([means, totals], ignore_index=True)


def dimension_column(dimension: GroupingDimension | str) -> str:
    """Return the DataFrame column name for a grouping dimension."""
    if isinstance(dimension, GroupingDimension):
        return dimension.value
    return str(dimension)


def enriched_history(
    history: pd.DataFrame,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> pd.DataFrame:
    """Join resolved dish attributes onto price history rows via ``dish_id``."""
    if {"cuisine", "dish_type"}.issubset(history.columns):
        return history.copy()

    init_db(db_path)
    mappings = get_resolved_mappings(db_path)
    frame = history.copy()
    if "dish_id" not in frame.columns:
        frame["dish_id"] = [
            make_dish_id(source, item)
            for source, item in zip(frame["source"], frame["item"])
        ]

    if mappings.empty:
        frame["cuisine"] = UNMAPPED_LABEL
        frame["dish_type"] = UNMAPPED_LABEL
        frame["is_vegetarian"] = False
        frame["is_vegan"] = False
        return frame

    attrs = mappings[
        [
            "dish_id",
            "cuisine",
            "dish_type",
            "is_vegetarian",
            "is_vegan",
        ]
    ].copy()
    merged = frame.merge(attrs, on="dish_id", how="left")
    for col in ("cuisine", "dish_type"):
        merged[col] = merged[col].fillna(UNMAPPED_LABEL)
    merged["is_vegetarian"] = merged["is_vegetarian"].fillna(False).astype(bool)
    merged["is_vegan"] = merged["is_vegan"].fillna(False).astype(bool)
    return merged


def enrich_snapshot(
    frame: pd.DataFrame,
    *,
    source_col: str = "company",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> pd.DataFrame:
    """Attach ``cuisine``/``dish_type`` to a snapshot frame keyed by (company, item).

    Snapshot frames (collection vs delivery, high/low) use ``company`` rather than
    ``source``; this bridges them to the shared ``dish_id`` join in
    :func:`enriched_history` so they can be grouped by any dimension.
    """
    renamed = frame.rename(columns={source_col: "source"})
    enriched = enriched_history(renamed, db_path=db_path)
    return enriched.rename(columns={"source": source_col})


def filter_snapshot_by_market(
    frame: pd.DataFrame,
    definition: MarketDefinition,
    *,
    source_col: str = "company",
    ignore_companies: bool = False,
) -> pd.DataFrame:
    """Subset a snapshot item frame to the rows matching a market definition.

    A market is a competitor benchmark defined by any mix of companies, buckets,
    cuisines and dish types. Empty filter lists mean "no constraint" on that
    axis. Requires ``cuisine``/``dish_type`` columns when the definition uses
    them (call :func:`enrich_snapshot` first).

    ``ignore_companies=True`` applies only the attribute filters (buckets,
    cuisines, dish types) — used to carve the comparable slice of a brand that
    isn't one of the market's competitors (e.g. the Wagamama benchmark).
    """
    if frame.empty:
        return frame
    mask = pd.Series(True, index=frame.index)
    if definition.companies and not ignore_companies:
        mask &= frame[source_col].isin(definition.companies)
    if definition.buckets and "bucket" in frame.columns:
        mask &= frame["bucket"].isin(definition.buckets)
    if definition.cuisines and "cuisine" in frame.columns:
        mask &= frame["cuisine"].isin(definition.cuisines)
    if definition.dish_types and "dish_type" in frame.columns:
        mask &= frame["dish_type"].isin(definition.dish_types)
    return frame[mask].reset_index(drop=True)


def _prepare_for_dimension(
    history: pd.DataFrame,
    dimension: GroupingDimension | str,
) -> pd.DataFrame:
    return history


def dimension_means(
    history: pd.DataFrame,
    source: str | None = None,
    *,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
    include_total: bool = False,
) -> pd.DataFrame:
    """Average price per grouping dimension per quarter, optionally for one source.

    Returns long format with columns ``date``, ``source``, ``bucket``, ``price``
    where ``bucket`` holds the active dimension's value (kept for chart reuse).
    """
    dim = dimension_column(dimension)
    frame = _prepare_for_dimension(history, dimension)
    if source is not None:
        frame = frame[frame["source"] == source]
    means = frame.groupby(["date", "source", dim], as_index=False)["price"].mean()
    means = means.rename(columns={dim: "bucket"})
    means["price"] = means["price"].round(2)
    if include_total:
        means = add_total_bucket(means)
    return means


def bucket_means(
    history: pd.DataFrame,
    source: str | None = None,
    *,
    include_total: bool = False,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
) -> pd.DataFrame:
    """Average price per bucket (or other dimension) per quarter."""
    return dimension_means(
        history,
        source,
        dimension=dimension,
        include_total=include_total,
    )


def _matches_market(row: pd.Series, definition: MarketDefinition) -> bool:
    if definition.companies and row["source"] not in definition.companies:
        return False
    if definition.buckets and row.get("bucket") not in definition.buckets:
        return False
    if definition.cuisines and row.get("cuisine") not in definition.cuisines:
        return False
    if definition.dish_types and row.get("dish_type") not in definition.dish_types:
        return False
    return True


def build_market_series(
    enriched: pd.DataFrame,
    definition: MarketDefinition,
    *,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Build a synthetic market series from filtered enriched history."""
    if weights is None:
        weights = load_company_weights()

    dim = dimension_column(dimension)
    frame = _prepare_for_dimension(enriched, dimension)
    # A market is always the *competitor* field: never include our own brand.
    frame = frame[frame["source"] != OWN_BRAND]
    mask = frame.apply(lambda row: _matches_market(row, definition), axis=1)
    filtered = frame[mask].copy()
    if filtered.empty:
        return pd.DataFrame(columns=["date", "source", "bucket", "item", "price"])

    market_label = f"Market: {definition.name}"
    rows: list[dict] = []
    for (dt, bucket_value), group in filtered.groupby(["date", dim]):
        company_means = (
            group.groupby("source")["price"].mean().reset_index(name="price")
        )
        w = company_means["source"].map(weights).astype(float)
        if w.sum() > 0:
            price = float((company_means["price"] * w).sum() / w.sum())
        else:
            price = float(company_means["price"].mean())
        row = {
            "date": dt,
            "source": market_label,
            "bucket": bucket_value,
            "item": "",
            "price": round(price, 2),
        }
        # Carry the active dimension value so later dimension_means keeps this row.
        row[dim] = bucket_value
        rows.append(row)
    return pd.DataFrame(rows)


def with_custom_markets(
    history: pd.DataFrame,
    definitions: list[MarketDefinition],
    *,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
    db_path: str | Path = DEFAULT_DB_PATH,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Append custom market series to history."""
    if not definitions:
        return history

    base = history[~history["source"].str.startswith("Market")].copy()
    enriched = enriched_history(base, db_path=db_path)
    market_frames = [
        build_market_series(
            enriched,
            definition,
            dimension=dimension,
            weights=weights,
        )
        for definition in definitions
    ]
    market_frames = [frame for frame in market_frames if not frame.empty]
    if not market_frames:
        return history
    return (
        pd.concat([history, *market_frames], ignore_index=True)
        .sort_values(["source", "bucket", "item", "date"])
        .reset_index(drop=True)
    )


def rebase_to_100(frame: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """Add an ``index`` column rebasing each group's price to 100 at its start.

    A price index strips out absolute-level differences so a board can compare
    the *rate* of change across items/buckets/sources on one axis.
    """
    out = frame.sort_values("date").copy()
    first = out.groupby(group_cols)["price"].transform("first")
    out["index"] = (out["price"] / first * 100.0).round(1)
    return out


def add_pct_change(
    frame: pd.DataFrame, group_cols: list[str] | tuple[str, ...] = ("source", "bucket")
) -> pd.DataFrame:
    """Add a ``pct_change`` column: cumulative % change from each group's first quarter.

    This is the compounded price growth since the start of the series (e.g. a
    value of 18.0 means prices are 18% above their first-quarter level), which is
    the natural shape for reasoning about — and projecting — inflation. The value
    at the latest quarter is therefore the total change across the whole window.
    """
    out = frame.sort_values("date").copy()
    first = out.groupby(list(group_cols))["price"].transform("first")
    out["pct_change"] = (((out["price"] / first) - 1.0) * 100.0).round(1)
    return out


def latest_bucket_prices(
    history: pd.DataFrame,
    *,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
) -> pd.DataFrame:
    """Most-recent-quarter average price per source and grouping dimension."""
    means = dimension_means(history, dimension=dimension)
    latest_date = means["date"].max()
    latest = means[means["date"] == latest_date]
    return latest.groupby(["source", "bucket"], as_index=False)["price"].mean().round(2)


def bucket_pct_change(
    history: pd.DataFrame,
    source: str,
    *,
    include_total: bool = False,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
) -> pd.DataFrame:
    """Wide table of quarter-on-quarter % change per bucket for one source.

    Shaped for a heatmap: rows = bucket, columns = quarter.
    """
    means = bucket_means(
        history, source, include_total=include_total, dimension=dimension
    )
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
