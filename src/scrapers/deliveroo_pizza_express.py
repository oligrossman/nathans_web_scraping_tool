"""Deliveroo scraper for Pizza Express Birmingham Bullring."""

from __future__ import annotations

from .deliveroo import DeliverooScraper


class DeliverooPizzaExpressScraper(DeliverooScraper):
    site_name = "Deliveroo - Pizza Express (Birmingham Bullring)"
    base_url = (
        "https://deliveroo.co.uk/menu/Birmingham/bullring/"
        "3225-pizzaexpress-birmingham-bullring"
        "?day=today&geohash=gcqdsckfhvjc&time=ASAP"
    )

    def __init__(self, **kwargs) -> None:
        super().__init__(url=self.base_url, **kwargs)
