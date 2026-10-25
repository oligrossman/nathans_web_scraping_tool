"""Pure transformations over the tidy price-history dataset.

Every function takes and returns a DataFrame (or a small dataclass) and never
touches Streamlit or Plotly. Keeping the analytics logic free of UI/plotting
makes it trivial to test and reuse.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .branding import LEGACY_OWN_BRAND, MARKET_LABEL, OWN_BRAND, TOTAL_LABEL
from .enrichment.identity import make_dish_id
from .enrichment.schema import GroupingDimension, MarketDefinition, UNMAPPED_LABEL
from .enrichment.store import DEFAULT_DB_PATH, get_resolved_mappings, init_db
from .history_archive import ALL_COMPANY_PRICES_PATH

DEFAULT_ALL_COMPANY_PRICES_PATH = ALL_COMPANY_PRICES_PATH
DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parent.parent / "data" / "company_weights.csv"


def load_history(path: str | Path = DEFAULT_ALL_COMPANY_PRICES_PATH) -> pd.DataFrame:
    """Load the tidy all-company prices dataset with ``date`` parsed to datetime."""
    frame = pd.read_csv(path, parse_dates=["date"])
    frame["item"] = frame["item"].fillna("")
    # Keep in sync with branding renames (WagaNath → Wagamama).
    frame["source"] = frame["source"].replace({LEGACY_OWN_BRAND: OWN_BRAND})
    # The file stores a calendar date. Normalise so a midnight timestamp cannot
    # leak into a chart as a time of day.
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    return frame


def repeated_observations(history: pd.DataFrame) -> pd.DataFrame:
    """Keep dishes that have a price on more than one date.

    An average is a price comparison only when the same dishes can be followed.
    A dish seen once — usually the rest of a menu captured on the scrape day —
    would otherwise move that one date without any price having changed.
    Market rows are already averages, so they are left as they are.
    """
    if history.empty or not {"source", "item", "date"}.issubset(history.columns):
        return history
    is_market = history["source"].astype(str).str.startswith("Market")
    n_dates = history.groupby(["source", "item"], sort=False)["date"].transform("nunique")
    return history.loc[is_market | n_dates.ge(2)].reset_index(drop=True)


def load_company_weights(path: str | Path = DEFAULT_WEIGHTS_PATH) -> dict[str, float]:
    """Return ``{company: restaurant_count}`` from the weights CSV.

    A blank or non-positive count is ignored. Those companies are left out of
    the market rather than given a zero that would still change the divisor.
    """
    weights_path = Path(path)
    if not weights_path.exists():
        return {}
    weights_df = pd.read_csv(weights_path)
    if not {"company", "restaurants"}.issubset(weights_df.columns):
        return {}
    weights: dict[str, float] = {}
    for company, count in zip(weights_df["company"], weights_df["restaurants"]):
        name = str(company).strip()
        if not name or name.lower() == "nan":
            continue
        number = pd.to_numeric(count, errors="coerce")
        if pd.isna(number) or float(number) <= 0:
            continue
        weights[name] = float(number)
    return weights


def normalise_company_weights(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a ``company, restaurants`` table that is safe to save.

    A blank count drops that company from the market. A count must be a
    positive number. The own brand is refused, because the market never
    includes it, and a duplicate name is refused so two rows cannot disagree.
    """
    if not {"company", "restaurants"}.issubset(frame.columns):
        raise ValueError("Weights need a company column and a restaurants column.")
    rows: list[dict[str, str | int | float]] = []
    seen: set[str] = set()
    for _, row in frame.iterrows():
        company = str(row["company"]).strip()
        if not company or company.lower() == "nan":
            raise ValueError("Every weight needs a company name.")
        if company in {OWN_BRAND, LEGACY_OWN_BRAND}:
            raise ValueError(
                f"{OWN_BRAND} is left out of the market, so it has no weight."
            )
        if company in seen:
            raise ValueError(f"{company} is listed more than once.")
        seen.add(company)
        raw = row["restaurants"]
        if pd.isna(raw) or str(raw).strip() == "":
            continue
        number = pd.to_numeric(raw, errors="coerce")
        if pd.isna(number) or not float(number) > 0:
            raise ValueError(
                f"{company}: restaurant count must be greater than zero."
            )
        count = float(number)
        rows.append(
            {
                "company": company,
                "restaurants": int(count) if count.is_integer() else round(count, 4),
            }
        )
    if not rows:
        raise ValueError("Enter a restaurant count for at least one competitor.")
    return (
        pd.DataFrame(rows, columns=["company", "restaurants"])
        .sort_values("company")
        .reset_index(drop=True)
    )


def save_company_weights(
    frame: pd.DataFrame,
    path: str | Path = DEFAULT_WEIGHTS_PATH,
) -> pd.DataFrame:
    """Write restaurant-count weights. Returns the table that was written."""
    cleaned = normalise_company_weights(frame)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    cleaned.to_csv(target, index=False)
    return cleaned


def weighted_company_mean(company_means: pd.DataFrame, weights: dict[str, float]) -> float:
    """Restaurant-count-weighted average of one price per company.

    ``company_means`` has ``source`` and ``price``. A company with no positive
    weight is left out. When nobody in the group has a weight, each company
    counts once. That fallback keeps a custom market drawable before its
    companies have been given counts.
    """
    if company_means.empty:
        return float("nan")
    scored = company_means.copy()
    scored["weight"] = scored["source"].map(weights)
    scored["weight"] = pd.to_numeric(scored["weight"], errors="coerce")
    scored = scored[scored["weight"].gt(0)]
    if scored.empty:
        return float(company_means["price"].mean())
    return float((scored["price"] * scored["weight"]).sum() / scored["weight"].sum())


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
        price = weighted_company_mean(group, weights)
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
    """History with default Market and any selected custom market series.

    Dishes observed on only one date are dropped first, so the market and every
    trend are built from dishes that can actually be followed.
    """
    frame = with_derived_market(
        repeated_observations(history),
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

    Snapshot frames (restaurant vs delivery, high/low) use ``company`` rather than
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
    """Build a market series from filtered competitor history.

    Each point is the restaurant-count-weighted average of the competitor
    prices on that date.
    """
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
        price = weighted_company_mean(company_means, weights)
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


def _at_series_end(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep each source and grouping value on its own latest date.

    A single cutoff — the newest date anywhere in the file — drops every brand
    whose history ends sooner, so each series is read at its own latest date.
    """
    if frame.empty:
        return frame.copy()
    latest = frame.groupby(["source", "bucket"])["date"].transform("max")
    return frame.loc[frame["date"].eq(latest)].reset_index(drop=True)


def latest_bucket_prices(
    history: pd.DataFrame,
    *,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
) -> pd.DataFrame:
    """Average price on each series' own latest date, per grouping value."""
    return _at_series_end(dimension_means(history, dimension=dimension))


def average_bucket_prices(
    history: pd.DataFrame,
    *,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
) -> pd.DataFrame:
    """Mean of the quarterly average prices, per source and grouping value.

    Each quarter already averages the dishes in the group. Averaging those
    quarters equally matches the level of the trend line, so a quarter with
    more dishes does not count for more.
    """
    means = dimension_means(history, dimension=dimension)
    if means.empty:
        return means
    averaged = means.groupby(["source", "bucket"], as_index=False)["price"].mean()
    averaged["price"] = averaged["price"].round(2)
    return averaged


def total_bucket_pct_change(
    history: pd.DataFrame,
    *,
    dimension: GroupingDimension | str = GroupingDimension.BUCKET,
) -> pd.DataFrame:
    """Cumulative % change from each series' first date to its own last date."""
    means = dimension_means(history, dimension=dimension)
    if means.empty:
        return means
    return _at_series_end(add_pct_change(means, ["source", "bucket"]))


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
    history = repeated_observations(history)
    if history.empty:
        return Kpis(0.0, 0.0, "", 0.0, {})
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
