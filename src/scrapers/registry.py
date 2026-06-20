"""Maps the dropdown label a user sees to the scraper that handles it.

This is the only file you touch to expose a new chain in the UI. The Streamlit
app reads ``SCRAPERS`` directly, so registering a class here makes it selectable.
"""

from __future__ import annotations

from .base import BaseScraper
from .deliveroo import DeliverooScraper
from .deliveroo_pizza_express import DeliverooPizzaExpressScraper
from .nandos import NandosScraper
from .pizza_express import PizzaExpressScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    NandosScraper.site_name: NandosScraper,
    DeliverooScraper.site_name: DeliverooScraper,
    PizzaExpressScraper.site_name: PizzaExpressScraper,
    DeliverooPizzaExpressScraper.site_name: DeliverooPizzaExpressScraper,
}


def get_scraper(site_name: str) -> BaseScraper:
    """Instantiate the scraper registered under ``site_name``."""
    try:
        scraper_cls = SCRAPERS[site_name]
    except KeyError as exc:
        raise ValueError(f"No scraper registered for {site_name!r}.") from exc
    return scraper_cls()
