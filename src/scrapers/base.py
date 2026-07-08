"""Core data contract and interface shared by every site scraper.

`MenuItem` is the single shape that all scrapers produce, and `BaseScraper`
is the interface they all implement. The Streamlit UI depends only on these
two things, never on a specific site, which is what keeps the app extensible.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

_PRICE_PATTERN = re.compile(r"\d+(?:\.\d{1,2})?")
_GBP_PRICE_PATTERN = re.compile(r"£\s*(\d+(?:\.\d{1,2})?)")


@dataclass(slots=True)
class MenuItem:
    """A single menu entry produced by a scraper."""

    name: str
    price: float | None = None
    category: str | None = None
    description: str | None = None


def parse_price(text: str | None) -> float | None:
    """Extract a price as a float from messy text like ``"£12.50"``.

    Prefers an explicit ``£`` amount so volume markers (``660ml``), ABV
    (``4.7%``), and measure sizes (``25ml``) in the same card are not mistaken
    for the price. Falls back to the first bare number when no ``£`` is present.
    """
    if not text:
        return None
    normalised = text.replace(",", "").replace("\u00a0", " ")
    gbp_match = _GBP_PRICE_PATTERN.search(normalised)
    if gbp_match:
        return float(gbp_match.group(1))
    match = _PRICE_PATTERN.search(normalised)
    return float(match.group()) if match else None


class BaseScraper(ABC):
    """Interface every food-chain scraper must implement.

    Subclasses set ``site_name`` and ``base_url`` and implement ``scrape``.
    """

    site_name: str
    base_url: str

    @abstractmethod
    def scrape(self) -> list[MenuItem]:
        """Return every menu item found on the site."""
        raise NotImplementedError
