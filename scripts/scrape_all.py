"""Scrape every in-scope restaurant and write snapshot CSVs.

Usage (from the project root)::

    python -m scripts.scrape_all            # reuse cached snapshots if present
    python -m scripts.scrape_all --rescrape # force fresh scrapes

Each restaurant is scraped sequentially with per-site error isolation so one
failure does not abort the batch. Snapshots are written only to
``data/scrapes/<YYYY-MM-DD>/`` (re-scraping the same day replaces that file).
"""

from __future__ import annotations

import argparse
import traceback
from datetime import date
from pathlib import Path

import pandas as pd

from src.export import items_to_dataframe
from src.history_archive import persist_scrape_snapshot, resolve_snapshot_path
from src.restaurants.config import Restaurant, load_restaurants
from src.scrapers.registry import build_scrapers, restaurant_scraper_for

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_or_scrape(
    filename: str,
    label: str,
    scraper,
    *,
    rescrape: bool,
    when: date | None = None,
) -> pd.DataFrame | None:
    cached = resolve_snapshot_path(filename, DATA_DIR)
    if cached.exists() and not rescrape:
        print(f"Using cached {label}: {cached}")
        return pd.read_csv(cached)
    print(f"Scraping {label}...")
    frame = items_to_dataframe(scraper.scrape())
    saved = persist_scrape_snapshot(frame, filename, when=when)
    print(f"Saved {len(frame)} items to {saved}")
    return frame


def scrape_restaurant(
    restaurant: Restaurant,
    scrapers: dict,
    *,
    rescrape: bool,
    when: date | None = None,
) -> dict[str, str | int | None]:
    """Scrape restaurant-menu and delivery snapshots for one restaurant."""
    result: dict[str, str | int | None] = {
        "slug": restaurant.slug,
        "restaurant_items": None,
        "delivery_items": None,
        "restaurant_error": None,
        "delivery_error": None,
    }

    restaurant_cls = restaurant_scraper_for(restaurant.slug)
    if restaurant_cls is not None:
        try:
            frame = _load_or_scrape(
                restaurant.restaurant_snapshot_path(DATA_DIR).name,
                restaurant.restaurant_label,
                restaurant_cls(),
                rescrape=rescrape,
                when=when,
            )
            result["restaurant_items"] = len(frame) if frame is not None else 0
        except Exception as exc:
            result["restaurant_error"] = str(exc)
            print(f"ERROR {restaurant.restaurant_label}: {exc}")
            traceback.print_exc()

    delivery_cls = scrapers.get(restaurant.delivery_label)
    if delivery_cls is not None:
        try:
            frame = _load_or_scrape(
                restaurant.delivery_snapshot_path(DATA_DIR).name,
                restaurant.delivery_label,
                delivery_cls(),
                rescrape=rescrape,
                when=when,
            )
            result["delivery_items"] = len(frame) if frame is not None else 0
        except Exception as exc:
            result["delivery_error"] = str(exc)
            print(f"ERROR {restaurant.delivery_label}: {exc}")
            traceback.print_exc()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rescrape",
        action="store_true",
        help="Force fresh scrapes even if cached snapshots exist.",
    )
    args = parser.parse_args()

    restaurants = load_restaurants()
    scrapers = build_scrapers(restaurants)
    when = date.today()
    results: list[dict] = []
    for restaurant in restaurants:
        print(f"\n--- {restaurant.brand} ({restaurant.location}) ---")
        results.append(
            scrape_restaurant(restaurant, scrapers, rescrape=args.rescrape, when=when)
        )

    print("\n=== Scrape summary ===")
    for row in results:
        errors = row["restaurant_error"] or row["delivery_error"] or "none"
        print(
            f"{row['slug']}: restaurant={row['restaurant_items']} "
            f"delivery={row['delivery_items']} errors={errors}"
        )


if __name__ == "__main__":
    main()
