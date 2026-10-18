"""The scrape dropdown is the registry, not the restaurant CSV on its own."""

from src.restaurants.config import restaurant_by_slug
from src.scrapers.registry import SCRAPERS


def test_rosas_thai_restaurant_and_delivery_are_selectable() -> None:
    restaurant = restaurant_by_slug("rosas_thai")

    assert restaurant.restaurant_label in SCRAPERS
    assert restaurant.delivery_label in SCRAPERS
    assert SCRAPERS[restaurant.restaurant_label].base_url == restaurant.restaurant_url
    assert restaurant.delivery_platform == "ubereats"
    assert restaurant.site_count == 40
    assert restaurant.postcode == "N1 0QX"
