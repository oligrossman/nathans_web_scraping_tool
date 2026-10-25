"""Scrape menus and recompose observed price history.

Usage (from the project root)::

    python -m scripts.generate_data              # reuse cached snapshots if present
    python -m scripts.generate_data --rescrape   # force fresh scrapes
    python -m scripts.generate_data --skip-scrape # recompose history only

Outputs:
    data/scrapes/<YYYY-MM-DD>/*_snapshot.csv
    data/all_company_prices.csv

Price history is the pricing-tracker workbook plus dated restaurant-menu
scrapes. This script does not estimate a price.
"""

from __future__ import annotations

import argparse

from src.history_archive import ALL_COMPANY_PRICES_PATH, update_all_company_prices
from src.restaurants.config import load_restaurants
from scripts.scrape_all import scrape_restaurant
from src.scrapers.registry import build_scrapers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rescrape",
        action="store_true",
        help="Force fresh scrapes even if cached snapshots exist.",
    )
    parser.add_argument(
        "--skip-scrape",
        action="store_true",
        help="Recompose price history from the tracker and existing snapshots.",
    )
    args = parser.parse_args()

    if not args.skip_scrape:
        restaurants = load_restaurants()
        scrapers = build_scrapers(restaurants)
        for restaurant in restaurants:
            result = scrape_restaurant(restaurant, scrapers, rescrape=args.rescrape)
            if result["restaurant_error"]:
                print(f"Warning: {restaurant.brand} restaurant scrape failed.")

    summary = update_all_company_prices()
    print(
        f"Wrote {summary['rows']} rows to {ALL_COMPANY_PRICES_PATH} "
        f"({len(summary['sources'])} sources, {summary['dates']} dates, "
        f"{len(summary['archives'])} dated archive(s)): "
        f"{', '.join(summary['sources'])}."
    )


if __name__ == "__main__":
    main()
