"""Maps dropdown labels to scrapers, built from ``data/restaurants.csv``.

This is the only file you touch to expose a new chain in the UI. The Streamlit
app reads ``SCRAPERS`` directly, so registering a class here makes it selectable.
"""

from __future__ import annotations

from pathlib import Path

from src.history_archive import resolve_snapshot_path
from src.restaurants.config import Restaurant, load_restaurants

from .banana_tree import BananaTreeScraper
from .base import BaseScraper
from .deliveroo import DeliverooScraper
from .deliveroo_pizza_express import DeliverooPizzaExpressScraper
from .five_guys import FiveGuysScraper
from .giggling_squid import GigglingSquidScraper
from .nandos import NandosScraper
from .pho import PhoScraper
from .pizza_express import PizzaExpressScraper
from .prezzo import PrezzoScraper
from .rosas_thai import RosasThaiScraper
from .ubereats import UberEatsScraper
from .wagamama import WagamamaScraper
from .zizzi import ZizziScraper

_RESTAURANT_BY_SLUG: dict[str, type[BaseScraper]] = {
    "banana_tree": BananaTreeScraper,
    "five_guys": FiveGuysScraper,
    "giggling_squid": GigglingSquidScraper,
    "nandos": NandosScraper,
    "pho": PhoScraper,
    "pizza_express": PizzaExpressScraper,
    "prezzo": PrezzoScraper,
    "rosas_thai": RosasThaiScraper,
    "zizzi": ZizziScraper,
    "wagamama": WagamamaScraper,
}


def _deliveroo_factory(restaurant: Restaurant) -> type[BaseScraper]:
    """Return a Deliveroo scraper class configured for one restaurant."""
    if restaurant.slug == "pizza_express":
        return DeliverooPizzaExpressScraper
    if restaurant.slug == "nandos":
        return DeliverooScraper

    class ConfiguredDeliverooScraper(DeliverooScraper):
        site_name = restaurant.delivery_label
        base_url = restaurant.delivery_url

        def __init__(self, **kwargs) -> None:
            super().__init__(
                url=restaurant.delivery_url,
                site_name=restaurant.delivery_label,
                **kwargs,
            )

    ConfiguredDeliverooScraper.__name__ = (
        f"Deliveroo{restaurant.slug.title().replace('_', '')}Scraper"
    )
    return ConfiguredDeliverooScraper


def _ubereats_factory(restaurant: Restaurant) -> type[BaseScraper]:
    class ConfiguredUberEatsScraper(UberEatsScraper):
        site_name = restaurant.delivery_label
        base_url = restaurant.delivery_url

        def __init__(self, **kwargs) -> None:
            super().__init__(
                url=restaurant.delivery_url,
                site_name=restaurant.delivery_label,
                **kwargs,
            )

    ConfiguredUberEatsScraper.__name__ = (
        f"UberEats{restaurant.slug.title().replace('_', '')}Scraper"
    )
    return ConfiguredUberEatsScraper


def build_scrapers(
    restaurants: list[Restaurant] | None = None,
) -> dict[str, type[BaseScraper]]:
    """Build the site-name -> scraper-class registry from restaurant config."""
    registry: dict[str, type[BaseScraper]] = {}
    for restaurant in restaurants or load_restaurants():
        restaurant_cls = _RESTAURANT_BY_SLUG.get(restaurant.slug)
        if restaurant_cls is not None:
            registry[restaurant_cls.site_name] = restaurant_cls
        if restaurant.delivery_platform == "deliveroo":
            delivery_cls = _deliveroo_factory(restaurant)
        elif restaurant.delivery_platform == "ubereats":
            delivery_cls = _ubereats_factory(restaurant)
        else:
            continue
        registry[delivery_cls.site_name] = delivery_cls
    return registry


def restaurant_scraper_for(slug: str) -> type[BaseScraper] | None:
    """Return the restaurant-menu scraper class for a restaurant slug."""
    return _RESTAURANT_BY_SLUG.get(slug)


def snapshot_paths_by_site(
    restaurants: list[Restaurant] | None = None,
    data_dir: Path | None = None,
) -> dict[str, Path]:
    """Map each scraper ``site_name`` to its latest on-disk snapshot path.

    Built with the exact class-selection logic ``build_scrapers`` uses, so every
    key here matches a key in ``SCRAPERS``. Paths resolve to the newest dated
    archive under ``data/scrapes/<YYYY-MM-DD>/``.
    """
    paths: dict[str, Path] = {}
    for restaurant in restaurants or load_restaurants():
        restaurant_cls = _RESTAURANT_BY_SLUG.get(restaurant.slug)
        if restaurant_cls is not None:
            paths[restaurant_cls.site_name] = resolve_snapshot_path(
                restaurant.restaurant_snapshot_path(data_dir).name, data_dir
            )
        if restaurant.delivery_platform == "deliveroo":
            delivery_cls = _deliveroo_factory(restaurant)
        elif restaurant.delivery_platform == "ubereats":
            delivery_cls = _ubereats_factory(restaurant)
        else:
            continue
        paths[delivery_cls.site_name] = resolve_snapshot_path(
            restaurant.delivery_snapshot_path(data_dir).name, data_dir
        )
    return paths


SCRAPERS: dict[str, type[BaseScraper]] = build_scrapers()


def get_scraper(site_name: str) -> BaseScraper:
    """Instantiate the scraper registered under ``site_name``."""
    try:
        scraper_cls = SCRAPERS[site_name]
    except KeyError as exc:
        raise ValueError(f"No scraper registered for {site_name!r}.") from exc
    return scraper_cls()
