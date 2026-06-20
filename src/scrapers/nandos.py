"""Scraper for the Nando's UK online ordering menu.

The page is a JavaScript-rendered React app, so we drive a real (headless)
browser, wait for the menu to render, scroll to trigger lazy-loading of every
category, then read the product cards out of the DOM.

Nando's builds the page with CSS-module class names whose hash suffix changes
between deployments (e.g. ``MenuItemOverview_title__Em0oB``). We therefore match
on the stable *prefix* with ``[class*='...']`` and prefer ``data-testid`` hooks,
which is far more durable than matching a full class name.
"""

from __future__ import annotations

import time

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .base import BaseScraper, MenuItem, parse_price
from .driver import chrome_driver

_CARD_SELECTOR = "[data-testid='menu-item-card']"
_SECTION_SELECTOR = "section[class*='MenuSection_section']"
_SECTION_TITLE_SELECTOR = "[class*='menuSectionTitle']"

# Within a card, name/price/description, each with a resilient fallback.
_NAME_SELECTORS = ("[class*='MenuItemOverview_title']", "h3")
_PRICE_SELECTORS = ("[class*='Price_price']",)
_DESC_SELECTORS = ("[class*='MenuItemOverview_description']",)


class NandosScraper(BaseScraper):
    site_name = "Nando's (Camden, Collection)"
    base_url = "https://www.nandos.co.uk/order/menu/camden?fulfilment=COLLECTION"

    def __init__(self, *, page_load_timeout: int = 30, max_scrolls: int = 25) -> None:
        self.page_load_timeout = page_load_timeout
        self.max_scrolls = max_scrolls

    def scrape(self) -> list[MenuItem]:
        with chrome_driver() as driver:
            driver.get(self.base_url)
            self._wait_for_menu(driver)
            self._scroll_to_load_all(driver)
            return self._extract_items(driver)

    def _wait_for_menu(self, driver) -> None:
        """Block until at least one menu card is present in the DOM."""
        try:
            WebDriverWait(driver, self.page_load_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, _CARD_SELECTOR))
            )
        except TimeoutException as exc:
            raise RuntimeError(
                "Timed out waiting for the Nando's menu to load. The site may be "
                "slow, blocking automated browsers, or have changed."
            ) from exc

    def _scroll_to_load_all(self, driver) -> None:
        """Scroll down repeatedly until the page height stops growing."""
        last_height = driver.execute_script("return document.body.scrollHeight")
        for _ in range(self.max_scrolls):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.0)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

    def _extract_items(self, driver) -> list[MenuItem]:
        """Walk each category section and read the cards it contains."""
        items: list[MenuItem] = []
        seen: set[tuple[str, str | None]] = set()
        for section in driver.find_elements(By.CSS_SELECTOR, _SECTION_SELECTOR):
            category = self._first_text(section, (_SECTION_TITLE_SELECTOR,))
            for card in section.find_elements(By.CSS_SELECTOR, _CARD_SELECTOR):
                item = self._card_to_item(card, category)
                key = (item.name, item.category) if item else None
                if item and key not in seen:
                    seen.add(key)
                    items.append(item)
        return items

    def _card_to_item(self, card: WebElement, category: str | None) -> MenuItem | None:
        name = self._first_text(card, _NAME_SELECTORS)
        if not name:
            return None
        return MenuItem(
            name=name,
            price=parse_price(self._first_text(card, _PRICE_SELECTORS)),
            category=category,
            description=self._first_text(card, _DESC_SELECTORS),
        )

    @staticmethod
    def _first_text(scope: WebElement, selectors: tuple[str, ...]) -> str | None:
        """Return the first non-empty text found for any of the selectors."""
        for selector in selectors:
            try:
                element = scope.find_element(By.CSS_SELECTOR, selector)
            except NoSuchElementException:
                continue
            text = element.text.strip()
            if text:
                return text
        return None
