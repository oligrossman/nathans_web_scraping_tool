"""Facts the Contents page shows, read from the files the app already uses.

The page stays a thin renderer. Location, platform, tracker sheet, and which
dates are scrapes all come from the registry and the price file, so a change
to those files shows up here without a second copy of the same sentence.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from .analytics import DEFAULT_WEIGHTS_PATH, load_company_weights
from .branding import OWN_BRAND
from .history_archive import ALL_COMPANY_PRICES_PATH, list_archive_dates
from .pricing_tracker import DEFAULT_WORKBOOK_PATH, TRACKER_NOTES, TRACKER_SHEETS
from .restaurants.config import Restaurant, load_restaurants

WORKBOOK_NAME = DEFAULT_WORKBOOK_PATH.name


def restaurant_menu_kind(restaurant: Restaurant) -> str:
    """Say whether the restaurant channel is a web page or a PDF."""
    url = restaurant.restaurant_url.casefold()
    if ".pdf" in url:
        return "PDF menu"
    return "Restaurant site"


def menu_sources(
    restaurants: list[Restaurant] | None = None,
    weights_path: Path = DEFAULT_WEIGHTS_PATH,
) -> pd.DataFrame:
    """One row per brand: where the menus are scraped, and which tracker sheet.

    ``Market weight`` is the restaurant count used for Market whole.
    Wagamama is excluded from that average even if a weight row appears.
    """
    weights = _market_weights(weights_path)
    rows = []
    for restaurant in restaurants if restaurants is not None else load_restaurants():
        if restaurant.brand == OWN_BRAND:
            weight = "Excluded"
        elif restaurant.brand in weights:
            weight = _count_label(weights[restaurant.brand])
        else:
            weight = "Not set"
        rows.append(
            {
                "Brand": restaurant.brand,
                "Location": restaurant.location,
                "Restaurant menu": restaurant_menu_kind(restaurant),
                "Delivery": restaurant.delivery_platform,
                "Postcode": restaurant.postcode,
                "UK sites": restaurant.site_count,
                "Market weight": weight,
                "Tracker sheet": TRACKER_SHEETS.get(restaurant.brand, "—"),
            }
        )
    return pd.DataFrame(rows)


def tracker_notes_in_registry_order(
    restaurants: list[Restaurant] | None = None,
) -> list[tuple[str, str]]:
    """Notes that explain a workbook alignment the registry cannot."""
    brands = [restaurant.brand for restaurant in (restaurants or load_restaurants())]
    return [(brand, TRACKER_NOTES[brand]) for brand in brands if brand in TRACKER_NOTES]


def price_date_coverage(
    prices: pd.DataFrame,
    archive_dates: list[date],
    brands: list[str],
) -> pd.DataFrame:
    """Split each brand's price dates into tracker dates and scrape dates.

    A date is a scrape when a folder exists under ``data/scrapes/<date>/``.
    Every other date in the price file is a pricing-tracker observation.
    """
    archive = set(archive_dates)
    by_brand: dict[str, list[date]] = {brand: [] for brand in brands}
    if not prices.empty and {"date", "source"}.issubset(prices.columns):
        observed = prices.copy()
        observed["date"] = pd.to_datetime(observed["date"]).dt.date
        for source, group in observed.groupby("source", sort=False):
            by_brand[str(source)] = sorted(set(group["date"]))

    rows = []
    for brand in brands:
        observed = by_brand.get(brand, [])
        tracker = [day for day in observed if day not in archive]
        scrapes = [day for day in observed if day in archive]
        rows.append(
            {
                "Brand": brand,
                "Tracker dates": _span(tracker),
                "Scrape dates": _join_dates(scrapes),
            }
        )
    return pd.DataFrame(rows)


def load_price_date_coverage(
    prices_path: Path = ALL_COMPANY_PRICES_PATH,
    archive_dates: list[date] | None = None,
    brands: list[str] | None = None,
) -> pd.DataFrame:
    """Coverage for the live price file. Empty when that file has not been built."""
    if brands is None:
        brands = [restaurant.brand for restaurant in load_restaurants()]
    if not prices_path.exists():
        return price_date_coverage(pd.DataFrame(), archive_dates or [], brands)
    prices = pd.read_csv(prices_path, usecols=["date", "source"])
    dated = archive_dates if archive_dates is not None else list_archive_dates()
    return price_date_coverage(prices, dated, brands)


def market_weight_rows(
    restaurants: list[Restaurant] | None = None,
    weights_path: Path = DEFAULT_WEIGHTS_PATH,
) -> pd.DataFrame:
    """One row per competitor for the weight editor.

    ``uk_sites`` is the estate size from the restaurant list. ``restaurants``
    is the count the market average uses. A competitor with no weight row is
    included with a blank count, so it can be filled in. A weight row for a
    company that is no longer in the list is kept, so saving does not drop it
    by accident. Wagamama is absent: the market never includes it.
    """
    weights = _market_weights(weights_path)
    rows: list[dict] = []
    seen: set[str] = set()
    for restaurant in restaurants if restaurants is not None else load_restaurants():
        if restaurant.brand == OWN_BRAND or restaurant.brand in seen:
            continue
        seen.add(restaurant.brand)
        rows.append(
            {
                "company": restaurant.brand,
                "uk_sites": restaurant.site_count,
                "restaurants": weights.get(restaurant.brand),
            }
        )
    for company, count in weights.items():
        if company in seen or company == OWN_BRAND:
            continue
        rows.append({"company": company, "uk_sites": None, "restaurants": count})
    frame = pd.DataFrame(rows, columns=["company", "uk_sites", "restaurants"])
    frame["uk_sites"] = pd.to_numeric(frame["uk_sites"], errors="coerce").astype("Int64")
    frame["restaurants"] = pd.to_numeric(frame["restaurants"], errors="coerce").astype(
        "Float64"
    )
    return frame


def _count_label(count: float) -> str:
    """Show a whole restaurant count without a trailing ``.0``."""
    number = float(count)
    if number.is_integer():
        return str(int(number))
    return str(number)


def _market_weights(path: Path) -> dict[str, float]:
    """Restaurant counts from the weights file. Blank and non-positive rows are skipped."""
    return load_company_weights(path)


def _span(days: list[date]) -> str:
    if not days:
        return "—"
    ordered = sorted(days)
    if len(ordered) == 1:
        return ordered[0].isoformat()
    return f"{ordered[0].isoformat()} to {ordered[-1].isoformat()} ({len(ordered)} dates)"


def _join_dates(days: list[date]) -> str:
    if not days:
        return "—"
    return ", ".join(day.isoformat() for day in sorted(days))
