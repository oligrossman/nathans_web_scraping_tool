"""Facts the Contents page shows, read from the files the app already uses.

The page stays a thin renderer. Location, platform, tracker sheet, and which
dates are scrapes all come from the registry and the price file, so a change
to those files shows up here without a second copy of the same sentence.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from .analytics import DEFAULT_WEIGHTS_PATH
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
            weight = str(weights[restaurant.brand])
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


def _market_weights(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    frame = pd.read_csv(path)
    if not {"company", "restaurants"}.issubset(frame.columns):
        return {}
    return {
        str(company): int(count)
        for company, count in zip(frame["company"], frame["restaurants"])
    }


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
