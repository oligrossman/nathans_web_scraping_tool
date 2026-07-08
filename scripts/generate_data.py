"""Build the synthetic analytics dataset and menu snapshots.

Usage (from the project root)::

    python -m scripts.generate_data              # reuse cached snapshots if present
    python -m scripts.generate_data --rescrape   # force fresh scrapes
    python -m scripts.generate_data --skip-scrape # rebuild price history only

Outputs:
    data/*_menu_snapshot.csv
    data/deliveroo_*_snapshot.csv / data/ubereats_*_snapshot.csv
    data/all_company_prices.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.pipeline import ALL_COMPANY_PRICES_PATH, rebuild_price_history
from src.restaurants.config import load_restaurants
from scripts.scrape_all import scrape_restaurant
from src.scrapers.registry import build_scrapers

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


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
        help="Rebuild price history from existing snapshots only (no scraping).",
    )
    args = parser.parse_args()

    if not args.skip_scrape:
        restaurants = load_restaurants()
        scrapers = build_scrapers(restaurants)
        for restaurant in restaurants:
            result = scrape_restaurant(restaurant, scrapers, rescrape=args.rescrape)
            if result["restaurant_error"]:
                print(f"Warning: {restaurant.brand} restaurant scrape failed.")

    summary = rebuild_price_history()
    print(
        f"Wrote {summary['rows']} rows to {ALL_COMPANY_PRICES_PATH} "
        f"({len(summary['sources'])} sources, {summary['dates']} dates, "
        f"{len(summary['archives'])} dated archive(s)): "
        f"{', '.join(summary['sources'])}."
    )


if __name__ == "__main__":
    main()
