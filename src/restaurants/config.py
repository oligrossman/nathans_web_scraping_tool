"""Restaurant registry loaded from ``data/restaurants.csv``."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_RESTAURANTS_CSV = _DATA_DIR / "restaurants.csv"


@dataclass(frozen=True, slots=True)
class Restaurant:
    """One in-scope restaurant with website-menu and delivery endpoints."""

    brand: str
    location: str
    slug: str
    restaurant_url: str
    delivery_url: str
    delivery_platform: str
    postcode: str
    site_count: int
    restaurant_snapshot: str | None = None
    delivery_snapshot: str | None = None

    @property
    def restaurant_label(self) -> str:
        return f"{self.brand} ({self.location}, Restaurant)"

    @property
    def delivery_label(self) -> str:
        platform_names = {"deliveroo": "Deliveroo", "ubereats": "Uber Eats"}
        platform = platform_names.get(
            self.delivery_platform, self.delivery_platform.title()
        )
        return f"{platform} - {self.brand} ({self.location})"

    def restaurant_snapshot_path(self, data_dir: Path | None = None) -> Path:
        root = data_dir or _DATA_DIR
        filename = self.restaurant_snapshot or f"{self.slug}_menu_snapshot.csv"
        return root / filename

    def delivery_snapshot_path(self, data_dir: Path | None = None) -> Path:
        root = data_dir or _DATA_DIR
        if self.delivery_snapshot:
            return root / self.delivery_snapshot
        return root / f"{self.delivery_platform}_{self.slug}_snapshot.csv"


def load_restaurants(path: Path | None = None) -> list[Restaurant]:
    """Load every in-scope restaurant from the CSV registry."""
    csv_path = path or _RESTAURANTS_CSV
    restaurants: list[Restaurant] = []
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            restaurants.append(
                Restaurant(
                    brand=row["brand"],
                    location=row["location"],
                    slug=row["slug"],
                    restaurant_url=row["restaurant_url"],
                    delivery_url=row["delivery_url"],
                    delivery_platform=row["delivery_platform"],
                    postcode=row["postcode"],
                    site_count=int(row["site_count"]),
                    restaurant_snapshot=row.get("restaurant_snapshot") or None,
                    delivery_snapshot=row.get("delivery_snapshot") or None,
                )
            )
    return restaurants


def restaurant_by_slug(slug: str, path: Path | None = None) -> Restaurant:
    """Return the restaurant matching ``slug``."""
    for restaurant in load_restaurants(path):
        if restaurant.slug == slug:
            return restaurant
    raise KeyError(f"No restaurant registered for slug {slug!r}.")
