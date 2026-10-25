"""Read-only analytics tools for the MCP server.

Plain functions with no MCP imports so tests stay ordinary pytest. Each tool
answers a director-level pricing question over the committed ``data/`` files.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from src import analytics, delivery_compare, high_low, scrape_log
from src.branding import MARKET_LABEL, OWN_BRAND
from src.enrichment.schema import GroupingDimension
from src.enrichment.store import (
    DEFAULT_DB_PATH,
    get_resolved_mappings,
    list_markets as load_saved_markets,
)
from src.scrapers.registry import SCRAPERS

from mcp_server.serialize import clamp_limit, dataframe_to_records

# Shared note for tool descriptions and payloads. Every history price is an
# observed tracker date or a dated restaurant scrape.
OBSERVED_HISTORY_NOTE = (
    "Price history is observed only: pricing-tracker dates plus dated "
    "restaurant-menu scrapes. No price is estimated. "
    "Use data_freshness for the latest history date and scrape ages."
)
SNAPSHOT_CAVEAT = (
    "Restaurant-vs-delivery and high/low figures come from each restaurant's "
    "latest scraped menu snapshot, not an average over the price history. "
    "Deliveroo delivery and service fees at checkout are not included."
)

_MAPPINGS_REQUIRED_DIMS = frozenset(
    {GroupingDimension.CUISINE.value, GroupingDimension.DISH_TYPE.value}
)


def _history() -> pd.DataFrame:
    return analytics.load_history()


def _valid_companies(history: pd.DataFrame | None = None) -> list[str]:
    frame = history if history is not None else _history()
    return [OWN_BRAND, *analytics.competitor_sources(frame)]


def _unknown_company(name: str, history: pd.DataFrame | None = None) -> dict[str, Any]:
    return {
        "error": f"Unknown company {name!r}.",
        "valid_companies": _valid_companies(history),
    }


def _parse_dimension(dimension: str) -> GroupingDimension | dict[str, Any]:
    try:
        return GroupingDimension(dimension)
    except ValueError:
        return {
            "error": f"Unknown dimension {dimension!r}.",
            "valid_dimensions": [d.value for d in GroupingDimension],
        }


def _mappings_db_path() -> Path:
    return Path(DEFAULT_DB_PATH)


def _mappings_missing_message(dimension: str | None = None) -> dict[str, Any]:
    detail = (
        f"Grouping by {dimension!r} needs dish mappings in "
        f"{_mappings_db_path().name}."
        if dimension
        else f"Custom markets and cuisine/dish-type grouping need "
        f"{_mappings_db_path().name}."
    )
    return {
        "error": (
            f"{detail} The mappings database is missing on this host. "
            "Price history tools that use course (bucket) still work from "
            "committed CSVs under data/."
        ),
        "mappings_db": str(_mappings_db_path()),
        "mappings_present": False,
    }


def _require_mappings_for_dimension(
    dimension: GroupingDimension,
) -> dict[str, Any] | None:
    if dimension.value not in _MAPPINGS_REQUIRED_DIMS:
        return None
    if not _mappings_db_path().exists():
        return _mappings_missing_message(dimension.value)
    return None


def glossary() -> str:
    """Short vocabulary for models: buckets, Market, observed history, scrapes."""
    dims = ", ".join(d.value for d in GroupingDimension)
    return "\n".join(
        [
            "# Menu intelligence glossary",
            "",
            f"- **Own brand**: {OWN_BRAND}.",
            "- **Buckets (course)**: Starters, Mains, Sides, "
            "Drinks (non-alcoholic), Drinks (alcoholic), Desserts.",
            f"- **Grouping dimensions**: {dims}.",
            f"- **{MARKET_LABEL}**: each competitor's average price, weighted by "
            "its restaurant count in data/company_weights.csv "
            "(sum of price × count, divided by the sum of counts). "
            "Wagamama is excluded. A company with no count is left out. "
            "Edit the counts on Comparison Configuration.",
            "- **Total**: equal mean of the course averages. Each course counts "
            "once. Restaurant counts are not used.",
            "- **Custom markets**: named competitor benchmarks saved in "
            "data/menu_mappings.db (e.g. Grill dishes).",
            f"- **Price history**: {OBSERVED_HISTORY_NOTE}",
            f"- **Scraped snapshots**: {SNAPSHOT_CAVEAT}",
        ]
    )


def list_companies() -> dict[str, Any]:
    """Own brand and competitor companies present in the price history."""
    history = _history()
    return {
        "own_brand": OWN_BRAND,
        "competitors": analytics.competitor_sources(history),
        "note": OBSERVED_HISTORY_NOTE,
    }


def data_freshness() -> dict[str, Any]:
    """Latest price-history date plus scrape status for registered sites."""
    history = _history()
    latest = history["date"].max()
    status = scrape_log.status_table(list(SCRAPERS))
    return {
        "latest_history_date": pd.Timestamp(latest).strftime("%Y-%m-%d"),
        "scrape_status": dataframe_to_records(status, limit=200),
        "note": (
            f"{OBSERVED_HISTORY_NOTE} Scrape ages are from data/scrape_log.json."
        ),
    }


def price_kpis(source: str | None = None) -> dict[str, Any]:
    """Board-level KPIs for one source or the whole (non-market) history."""
    history = _history()
    history = history[~history["source"].astype(str).str.startswith("Market")]
    if source is not None:
        if source not in _valid_companies(history):
            return _unknown_company(source, history)
        history = history[history["source"] == source]
        if history.empty:
            return {"error": f"No price rows for {source!r}."}
    kpis = analytics.compute_kpis(history)
    payload = asdict(kpis)
    # numpy scalars → plain Python for JSON
    for key, value in list(payload.items()):
        if hasattr(value, "item"):
            payload[key] = value.item()
        elif isinstance(value, dict):
            payload[key] = {
                k: (v.item() if hasattr(v, "item") else v) for k, v in value.items()
            }
    payload["source"] = source or "all"
    payload["note"] = OBSERVED_HISTORY_NOTE
    return payload


def price_trends(
    source: str,
    dimension: str = GroupingDimension.BUCKET.value,
    *,
    include_total: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    """Average price and cumulative % change by source and grouping dimension."""
    history = _history()
    if source not in _valid_companies(history):
        return _unknown_company(source, history)

    parsed = _parse_dimension(dimension)
    if isinstance(parsed, dict):
        return parsed
    missing = _require_mappings_for_dimension(parsed)
    if missing:
        return missing

    chart = analytics.prepare_chart_history(
        history,
        dimension=parsed,
        db_path=_mappings_db_path(),
    )
    means = analytics.bucket_means(
        chart,
        source,
        include_total=include_total,
        dimension=parsed,
    )
    with_pct = analytics.add_pct_change(means)
    records = dataframe_to_records(with_pct, limit=limit)
    return {
        "source": source,
        "dimension": parsed.value,
        "rows": records,
        "row_count": len(records),
        "truncated": len(with_pct) > clamp_limit(limit),
        "note": OBSERVED_HISTORY_NOTE,
    }


def compare_to_market(
    dimension: str = GroupingDimension.BUCKET.value,
    market_name: str | None = None,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Latest average prices for Wagamama vs a market benchmark by dimension."""
    parsed = _parse_dimension(dimension)
    if isinstance(parsed, dict):
        return parsed
    missing = _require_mappings_for_dimension(parsed)
    if missing:
        return missing

    history = _history()
    label = market_name or MARKET_LABEL
    custom_markets = []

    if label != MARKET_LABEL:
        if not _mappings_db_path().exists():
            return _mappings_missing_message()
        saved = load_saved_markets(_mappings_db_path())
        match = next((m for m in saved if m.name == label), None)
        if match is None:
            return {
                "error": f"Unknown market {label!r}.",
                "valid_markets": [MARKET_LABEL, *[m.name for m in saved]],
            }
        custom_markets = [match]
        series_label = f"Market: {match.name}"
    else:
        series_label = MARKET_LABEL

    chart = analytics.prepare_chart_history(
        history,
        dimension=parsed,
        custom_markets=custom_markets or None,
        db_path=_mappings_db_path(),
    )
    means = analytics.bucket_means(
        chart,
        include_total=True,
        dimension=parsed,
    )
    # One brand's history can end earlier than another's. Read each source
    # on its own latest date rather than the newest date in the file.
    focus = means[means["source"].isin([OWN_BRAND, series_label])]
    if focus.empty:
        return {
            "error": f"No rows for {OWN_BRAND!r} or {series_label!r}.",
            "note": OBSERVED_HISTORY_NOTE,
        }
    idx = focus.groupby(["source", "bucket"], sort=False)["date"].idxmax()
    latest = focus.loc[idx].sort_values(["source", "bucket"])
    as_of = {
        source: pd.Timestamp(group["date"].max()).strftime("%Y-%m-%d")
        for source, group in latest.groupby("source")
    }
    records = dataframe_to_records(latest, limit=limit)
    return {
        "market": label,
        "market_series": series_label,
        "dimension": parsed.value,
        "as_of": as_of,
        "rows": records,
        "row_count": len(records),
        "note": OBSERVED_HISTORY_NOTE,
    }


def delivery_markup(
    company: str | None = None,
    bucket: str | None = None,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Restaurant vs Deliveroo markup: company summary → bucket → items."""
    comparison = delivery_compare.build_comparison()
    smell_note = delivery_compare.assessment_note(comparison)
    note = f"{SNAPSHOT_CAVEAT} {smell_note}".strip() if smell_note else SNAPSHOT_CAVEAT
    if comparison.empty:
        return {
            "error": "No matched restaurant-vs-delivery items on this host.",
            "note": note,
        }

    companies = sorted(comparison["company"].unique())
    if company is not None and company not in companies:
        return {
            "error": f"Unknown company {company!r} in delivery comparison.",
            "valid_companies": companies,
            "note": note,
        }

    if company is None:
        summary = delivery_compare.company_summary(
            comparison,
            overall_label=MARKET_LABEL,
            overall_exclude=OWN_BRAND,
        )
        level = "company"
        frame = summary
    elif bucket is None:
        frame = delivery_compare.bucket_summary(comparison, company)
        level = "bucket"
    else:
        frame = comparison[
            (comparison["company"] == company) & (comparison["bucket"] == bucket)
        ].sort_values("markup_pct", ascending=False)
        level = "item"

    records = dataframe_to_records(frame, limit=limit)
    return {
        "level": level,
        "company": company,
        "bucket": bucket,
        "rows": records,
        "row_count": len(records),
        "truncated": len(frame) > clamp_limit(limit),
        "note": note,
    }


def price_range(
    company: str | None = None,
    bucket: str | None = None,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """High/low/average menu prices: company summary → bucket → items."""
    menu = high_low.build_menu_prices()
    if menu.empty:
        return {
            "error": "No restaurant menu snapshots on this host.",
            "note": SNAPSHOT_CAVEAT,
        }

    companies = sorted(menu["company"].unique())
    if company is not None and company not in companies:
        return {
            "error": f"Unknown company {company!r} in high/low menus.",
            "valid_companies": companies,
            "note": SNAPSHOT_CAVEAT,
        }

    if company is None:
        frame = high_low.company_summary(
            menu,
            overall_label=MARKET_LABEL,
            overall_exclude=OWN_BRAND,
        )
        level = "company"
    elif bucket is None:
        frame = high_low.bucket_summary(menu, company)
        level = "bucket"
    else:
        frame = high_low.item_detail(menu, company, bucket)
        level = "item"

    records = dataframe_to_records(frame, limit=limit)
    return {
        "level": level,
        "company": company,
        "bucket": bucket,
        "rows": records,
        "row_count": len(records),
        "truncated": len(frame) > clamp_limit(limit),
        "note": SNAPSHOT_CAVEAT,
    }


def _without_markets(history: pd.DataFrame) -> pd.DataFrame:
    """Drop derived market series. A market is an average, not a company."""
    return history[~history["source"].astype(str).str.startswith("Market")].copy()


def _rows_at_each_source_end(history: pd.DataFrame) -> pd.DataFrame:
    """Keep each company's own latest date.

    One shared cutoff drops every brand whose history ends sooner. Wagamama's
    series stops before the newest competitor scrape, so a search has to read
    each company at its own end.
    """
    if history.empty:
        return history
    latest = history.groupby("source")["date"].transform("max")
    return history.loc[history["date"].eq(latest)].copy()


def _scoped_history(source: str | None) -> pd.DataFrame | dict[str, Any]:
    """Price history for one company, or every company. Markets are excluded."""
    history = _without_markets(_history())
    if source is None:
        return history
    if source not in _valid_companies(history):
        return _unknown_company(source, history)
    return history[history["source"] == source].copy()


def find_dishes(
    query: str,
    source: str | None = None,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Case-insensitive name search on each company's own latest recorded date."""
    needle = (query or "").strip()
    if not needle:
        return {"error": "query must be a non-empty string."}

    history = _scoped_history(source)
    if isinstance(history, dict):
        return history
    if history.empty:
        return {
            "query": needle,
            "source": source,
            "as_of": None,
            "rows": [],
            "row_count": 0,
            "truncated": False,
            "note": OBSERVED_HISTORY_NOTE,
        }

    latest = _rows_at_each_source_end(history)
    matches = latest[
        latest["item"].astype(str).str.contains(needle, case=False, na=False)
    ].sort_values(["source", "item"])
    cols = [c for c in ("date", "source", "item", "bucket", "price") if c in matches.columns]
    records = dataframe_to_records(matches[cols], limit=limit)
    as_of = pd.Timestamp(history["date"].max()).strftime("%Y-%m-%d")
    return {
        "query": needle,
        "source": source,
        "as_of": as_of,
        "rows": records,
        "row_count": len(records),
        "truncated": len(matches) > clamp_limit(limit),
        "note": (
            "Each row is that company's own latest recorded date, not one shared date. "
            f"{OBSERVED_HISTORY_NOTE}"
        ),
    }


def dish_history(
    query: str,
    source: str | None = None,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Every recorded price for dishes whose name contains ``query``."""
    needle = (query or "").strip()
    if not needle:
        return {"error": "query must be a non-empty string."}

    history = _scoped_history(source)
    if isinstance(history, dict):
        return history

    matches = history[
        history["item"].astype(str).str.contains(needle, case=False, na=False)
    ].copy()
    if not matches.empty:
        matches = analytics.add_pct_change(matches, ["source", "item"])
        matches = matches.sort_values(["source", "item", "date"])
    cols = [
        c
        for c in ("date", "source", "item", "bucket", "price", "pct_change")
        if c in matches.columns
    ]
    records = dataframe_to_records(matches[cols], limit=limit)
    return {
        "query": needle,
        "source": source,
        "items": int(matches["item"].nunique()) if not matches.empty else 0,
        "rows": records,
        "row_count": len(records),
        "truncated": len(matches) > clamp_limit(limit),
        "note": (
            "pct_change is versus that dish's own first recorded price. "
            f"{OBSERVED_HISTORY_NOTE}"
        ),
    }


def latest_prices(
    group: str,
    dimension: str = GroupingDimension.BUCKET.value,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Item count, average and median on each company's own latest date.

    ``group`` is the value inside ``dimension`` (for example ``Mains`` when
    dimension is ``bucket``). Dishes seen on only one date are excluded, the
    same rule the trend charts use, so a one-off scrape cannot move the average.
    """
    parsed = _parse_dimension(dimension)
    if isinstance(parsed, dict):
        return parsed
    missing = _require_mappings_for_dimension(parsed)
    if missing:
        return missing

    needle = (group or "").strip()
    if not needle:
        return {"error": "group must be a non-empty string."}

    history = analytics.repeated_observations(_without_markets(_history()))
    dim = analytics.dimension_column(parsed)
    if dim != GroupingDimension.BUCKET.value:
        history = analytics.enriched_history(history, db_path=_mappings_db_path())

    labels = sorted({str(value) for value in history[dim].dropna().unique()})
    canonical = next((label for label in labels if label.lower() == needle.lower()), None)
    if canonical is None:
        return {
            "error": f"Unknown {parsed.value} {needle!r}.",
            "valid_groups": labels,
        }

    subset = history[history[dim].astype(str) == canonical]
    latest = _rows_at_each_source_end(subset)
    summary = (
        latest.groupby("source", as_index=False)
        .agg(
            date=("date", "max"),
            dishes=("item", "nunique"),
            average=("price", "mean"),
            median=("price", "median"),
        )
        .sort_values(["average", "source"], ascending=[False, True])
    )
    records = dataframe_to_records(summary, limit=limit)
    return {
        "dimension": parsed.value,
        "group": canonical,
        "rows": records,
        "row_count": len(records),
        "truncated": len(summary) > clamp_limit(limit),
        "note": (
            "Each company is read on its own latest date. Average and median "
            "are equal-weighted across dishes seen on more than one date. "
            f"{OBSERVED_HISTORY_NOTE}"
        ),
    }


def list_markets() -> dict[str, Any]:
    """Named competitor benchmarks saved in the mappings database."""
    if not _mappings_db_path().exists():
        return _mappings_missing_message()
    saved = load_saved_markets(_mappings_db_path())
    return {
        "markets": [market.to_dict() for market in saved],
        "count": len(saved),
        "note": (
            f"Pass a market name to compare_to_market. {MARKET_LABEL!r} is always "
            "available and is not stored in this list."
        ),
    }


def company_weights() -> dict[str, Any]:
    """Restaurant counts behind the Market whole average."""
    weights = analytics.load_company_weights()
    total = float(sum(weights.values()))
    rows = [
        {
            "company": company,
            "restaurants": int(count) if float(count).is_integer() else float(count),
            "weight_pct": round(100.0 * float(count) / total, 1) if total else 0.0,
        }
        for company, count in weights.items()
    ]
    rows.sort(key=lambda row: (-row["restaurants"], row["company"]))
    return {
        "rows": rows,
        "restaurant_count": int(total) if total.is_integer() else total,
        "note": (
            f"{MARKET_LABEL} weights each competitor's average by these restaurant "
            f"counts, then divides by the sum of the counts. {OWN_BRAND} is "
            "excluded. A company with no row is left out. Edit the counts on "
            "Comparison Configuration, or in data/company_weights.csv."
        ),
    }


def dish_attributes(
    query: str,
    source: str | None = None,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Cuisine, dish type and dietary flags for dishes matching ``query``."""
    needle = (query or "").strip()
    if not needle:
        return {"error": "query must be a non-empty string."}
    if not _mappings_db_path().exists():
        return _mappings_missing_message()

    mappings = get_resolved_mappings(_mappings_db_path())
    if source is not None:
        known = sorted(mappings["source"].dropna().unique()) if not mappings.empty else []
        if source not in known:
            return {
                "error": f"Unknown company {source!r} in dish mappings.",
                "valid_companies": known,
            }
        mappings = mappings[mappings["source"] == source]

    matches = mappings[
        mappings["name"].astype(str).str.contains(needle, case=False, na=False)
    ].sort_values(["source", "name"])
    cols = [
        "source",
        "name",
        "bucket",
        "cuisine",
        "dish_type",
        "is_vegetarian",
        "is_vegan",
        "is_override",
    ]
    records = dataframe_to_records(matches[cols], limit=limit)
    return {
        "query": needle,
        "source": source,
        "rows": records,
        "row_count": len(records),
        "truncated": len(matches) > clamp_limit(limit),
        "note": "Attributes are the resolved mapping: a manual override wins over the model.",
    }
