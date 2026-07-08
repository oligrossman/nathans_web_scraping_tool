"""Scraper for Zizzi restaurant website menu."""

from __future__ import annotations

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .base import BaseScraper, MenuItem, parse_price
from .driver import chrome_driver
from .helpers import accept_cookies, scroll_to_load_all

_CARD_SELECTOR = "article"
_SECTION_SELECTOR = "h2"


class ZizziScraper(BaseScraper):
    site_name = "Zizzi (London - Bankside, Restaurant)"
    base_url = (
        "https://www.zizzi.co.uk/italian-restaurants/greater-london/"
        "london/bankside/menus"
    )

    def __init__(self, *, page_load_timeout: int = 30, max_scrolls: int = 25) -> None:
        self.page_load_timeout = page_load_timeout
        self.max_scrolls = max_scrolls

    def scrape(self) -> list[MenuItem]:
        with chrome_driver() as driver:
            driver.get(self.base_url)
            accept_cookies(driver)
            self._wait_for_menu(driver)
            scroll_to_load_all(driver, max_scrolls=self.max_scrolls)
            return self._extract_items(driver)

    def _wait_for_menu(self, driver) -> None:
        try:
            WebDriverWait(driver, self.page_load_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, _CARD_SELECTOR))
            )
        except TimeoutException as exc:
            raise RuntimeError("Timed out waiting for the Zizzi menu to load.") from exc

    def _extract_items(self, driver) -> list[MenuItem]:
        items: list[MenuItem] = []
        seen: set[tuple[str, str | None]] = set()
        category: str | None = None
        for element in driver.find_elements(By.CSS_SELECTOR, f"{_SECTION_SELECTOR}, {_CARD_SELECTOR}"):
            if element.tag_name == "h2" and element.text.strip():
                category = element.text.strip()
                continue
            if element.tag_name != "article" or "£" not in element.text:
                continue
            item = self._card_to_item(element, category)
            key = (item.name, item.category) if item else None
            if item and key not in seen:
                seen.add(key)
                items.append(item)
        return items

    def _card_to_item(self, card: WebElement, category: str | None) -> MenuItem | None:
        lines = [line.strip() for line in card.text.splitlines() if line.strip()]
        if len(lines) < 2:
            return None
        name = lines[0]
        price = self._price_from_card(card)
        if not name or price is None:
            return None
        description = lines[1] if len(lines) > 2 and not lines[1].startswith("£") else None
        return MenuItem(name=name, price=price, category=category, description=description)

    @staticmethod
    def _price_from_card(card: WebElement) -> float | None:
        """Read the menu price line from a card (the line containing ``£``)."""
        for line in card.text.splitlines():
            if "£" in line:
                price = parse_price(line)
                if price is not None:
                    return price
        return None
