"""Scraper for a Nando's menu as listed on Deliveroo (delivery channel).

Deliveroo is a JavaScript-rendered app behind a cookie-consent wall and lazy-
loads its menu as you scroll, so we drive headless Chrome: accept cookies,
scroll to the bottom, then read each item card.

Item name lives in the card's first text line; the price is the first currency
token in the card (Deliveroo shows the live price first and any struck-through
original second, so the first match is the price actually charged).

The default target is Nando's Camden delivering to a Camden geohash, matching
the collection restaurant we scrape elsewhere so the two are comparable.
"""

from __future__ import annotations

import re
import time

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .base import BaseScraper, MenuItem, parse_price
from .driver import chrome_driver

_ITEM_SELECTOR = "[data-testid='menu-item-image']"
_PRICE_PATTERN = re.compile(r"£\s*\d+(?:\.\d{2})?")
_COOKIE_XPATHS = (
    "//button[@id='onetrust-accept-btn-handler']",
    "//button[contains(., 'Accept all')]",
    "//button[contains(., 'Accept')]",
)


class DeliverooScraper(BaseScraper):
    site_name = "Deliveroo - Nando's (Camden)"
    base_url = (
        "https://deliveroo.co.uk/menu/London/camden/nandos-camden"
        "?geohash=gcpvhy2jdbkf&time=ASAP"
    )

    def __init__(
        self,
        url: str | None = None,
        *,
        page_load_timeout: int = 30,
        max_scrolls: int = 18,
    ) -> None:
        self.url = url or self.base_url
        self.page_load_timeout = page_load_timeout
        self.max_scrolls = max_scrolls

    def scrape(self) -> list[MenuItem]:
        with chrome_driver() as driver:
            driver.get(self.url)
            self._accept_cookies(driver)
            self._wait_for_menu(driver)
            self._scroll_to_load_all(driver)
            return self._extract_items(driver)

    def _accept_cookies(self, driver) -> None:
        """Dismiss the consent wall; lazy-loading is blocked until it's gone."""
        time.sleep(4)
        for xpath in _COOKIE_XPATHS:
            buttons = driver.find_elements(By.XPATH, xpath)
            if buttons:
                driver.execute_script("arguments[0].click();", buttons[0])
                time.sleep(2)
                return

    def _wait_for_menu(self, driver) -> None:
        try:
            WebDriverWait(driver, self.page_load_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, _ITEM_SELECTOR))
            )
        except TimeoutException as exc:
            raise RuntimeError(
                "Timed out waiting for the Deliveroo menu to load. Deliveroo may "
                "be blocking automated browsers or have changed its layout."
            ) from exc

    def _scroll_to_load_all(self, driver) -> None:
        last_height = driver.execute_script("return document.body.scrollHeight")
        for _ in range(self.max_scrolls):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.8)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

    def _extract_items(self, driver) -> list[MenuItem]:
        items: list[MenuItem] = []
        seen: set[str] = set()
        for image in driver.find_elements(By.CSS_SELECTOR, _ITEM_SELECTOR):
            card = image.find_element(By.XPATH, "..")
            item = self._card_to_item(card)
            if item and item.name not in seen:
                seen.add(item.name)
                items.append(item)
        return items

    def _card_to_item(self, card: WebElement) -> MenuItem | None:
        lines = [line.strip() for line in card.text.splitlines() if line.strip()]
        if not lines:
            return None
        name = lines[0]
        match = _PRICE_PATTERN.search(card.text)
        if not match:
            return None
        return MenuItem(name=name, price=parse_price(match.group()))
