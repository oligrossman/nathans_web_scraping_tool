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
from src.branding import OWN_BRAND
from src.enrichment.schema import GroupingDimension
from src.enrichment.store import DEFAULT_DB_PATH, list_markets
from src.scrapers.registry import SCRAPERS
from src.synthetic import MARKET_LABEL

from mcp_server.serialize import clamp_limit, dataframe_to_records

# Shared caveat for tool descriptions / payload notes. Multi-year history is
# synthetic; restaurant-vs-delivery and high/low come from the latest scrapes.
SYNTHETIC_HISTORY_CAVEAT = (
    "Multi-year quarterly price history is synthetic (demonstration backcast). "
    "Use data_freshness for the latest history date and scrape ages."
)
SNAPSHOT_CAVEAT = (
    "Restaurant-vs-delivery and high/low figures come from the latest scraped "
    "menu snapshots, not the synthetic multi-year history. Deliveroo delivery "
    "and service fees at checkout are not included."
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
    """Short vocabulary for models: buckets, Market, synthetic vs scraped."""
    dims = ", ".join(d.value for d in GroupingDimension)
    return "\n".join(
        [
            "# Menu intelligence glossary",
            "",
            f"- **Own brand**: {OWN_BRAND}.",
            "- **Buckets (course)**: Starters, Mains, Sides, Drinks, Desserts "
            "(plus Total = equal-weighted mean across buckets).",
            f"- **Grouping dimensions**: {dims}.",
            f"- **{MARKET_LABEL}**: restaurant-count-weighted average of "
            "competitor companies (never includes Wagamama). Weights live in "
            "data/company_weights.csv.",
            "- **Custom markets**: named competitor benchmarks saved in "
            "data/menu_mappings.db (e.g. Grill dishes).",
            f"- **Synthetic history**: {SYNTHETIC_HISTORY_CAVEAT}",
            f"- **Scraped snapshots**: {SNAPSHOT_CAVEAT}",
        ]
    )


def list_companies() -> dict[str, Any]:
    """Own brand and competitor companies present in the price history."""
    history = _history()
    return {
        "own_brand": OWN_BRAND,
        "competitors": analytics.competitor_sources(history),
        "note": SYNTHETIC_HISTORY_CAVEAT,
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
            f"{SYNTHETIC_HISTORY_CAVEAT} Scrape ages are from data/scrape_log.json."
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
    payload["note"] = SYNTHETIC_HISTORY_CAVEAT
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
        "note": SYNTHETIC_HISTORY_CAVEAT,
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
        saved = list_markets(_mappings_db_path())
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
    # Own-brand synthetic history can end earlier than scraped competitors —
    # take each source's own latest quarter rather than the global max date.
    focus = means[means["source"].isin([OWN_BRAND, series_label])]
    if focus.empty:
        return {
            "error": f"No rows for {OWN_BRAND!r} or {series_label!r}.",
            "note": SYNTHETIC_HISTORY_CAVEAT,
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
        "note": SYNTHETIC_HISTORY_CAVEAT,
    }


def delivery_markup(
    company: str | None = None,
    bucket: str | None = None,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Restaurant vs Deliveroo markup: company summary → bucket → items."""
    comparison = delivery_compare.build_comparison()
    if comparison.empty:
        return {
            "error": "No matched restaurant-vs-delivery items on this host.",
            "note": SNAPSHOT_CAVEAT,
        }

    companies = sorted(comparison["company"].unique())
    if company is not None and company not in companies:
        return {
            "error": f"Unknown company {company!r} in delivery comparison.",
            "valid_companies": companies,
            "note": SNAPSHOT_CAVEAT,
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
        "note": SNAPSHOT_CAVEAT,
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


def find_dishes(
    query: str,
    source: str | None = None,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Case-insensitive name search on the latest quarter of price history."""
    history = _history()
    history = history[~history["source"].astype(str).str.startswith("Market")]
    if source is not None:
        if source not in _valid_companies(history):
            return _unknown_company(source, history)
        history = history[history["source"] == source]

    latest_date = history["date"].max()
    latest = history[history["date"] == latest_date]
    needle = (query or "").strip()
    if not needle:
        return {"error": "query must be a non-empty string."}

    matches = latest[
        latest["item"].astype(str).str.contains(needle, case=False, na=False)
    ].sort_values(["source", "item"])
    cols = [c for c in ("date", "source", "item", "bucket", "price") if c in matches.columns]
    records = dataframe_to_records(matches[cols], limit=limit)
    return {
        "query": needle,
        "source": source,
        "as_of": pd.Timestamp(latest_date).strftime("%Y-%m-%d"),
        "rows": records,
        "row_count": len(records),
        "truncated": len(matches) > clamp_limit(limit),
        "note": SYNTHETIC_HISTORY_CAVEAT,
    }
