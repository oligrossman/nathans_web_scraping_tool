"""Scrape every in-scope restaurant and write snapshot CSVs.

Usage (from the project root)::

    python -m scripts.scrape_all            # reuse cached snapshots if present
    python -m scripts.scrape_all --rescrape # force fresh scrapes

Each restaurant is scraped sequentially with per-site error isolation so one
failure does not abort the batch.
"""

from __future__ import annotations

import argparse
import traceback
from pathlib import Path

import pandas as pd

from src.export import items_to_dataframe
from src.restaurants.config import Restaurant, load_restaurants
from src.scrapers.registry import build_scrapers, restaurant_scraper_for

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_or_scrape(
    path: Path,
    label: str,
    scraper,
    *,
    rescrape: bool,
) -> pd.DataFrame | None:
    if path.exists() and not rescrape:
        print(f"Using cached {label}: {path}")
        return pd.read_csv(path)
    print(f"Scraping {label}...")
    frame = items_to_dataframe(scraper.scrape())
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    print(f"Saved {len(frame)} items to {path}")
    return frame


def scrape_restaurant(
    restaurant: Restaurant,
    scrapers: dict,
    *,
    rescrape: bool,
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
                restaurant.restaurant_snapshot_path(DATA_DIR),
                restaurant.restaurant_label,
                restaurant_cls(),
                rescrape=rescrape,
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
                restaurant.delivery_snapshot_path(DATA_DIR),
                restaurant.delivery_label,
                delivery_cls(),
                rescrape=rescrape,
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
    results: list[dict] = []
    for restaurant in restaurants:
        print(f"\n--- {restaurant.brand} ({restaurant.location}) ---")
        results.append(
            scrape_restaurant(restaurant, scrapers, rescrape=args.rescrape)
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
