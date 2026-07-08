"""Scraper for a menu as listed on Uber Eats (delivery channel).

Uber Eats is a JavaScript-rendered app that lazy-loads menu items as you
scroll. Item cards expose ``data-testid`` attributes beginning with
``store-item-``. Name and price are read from the card text.
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
from .helpers import accept_cookies, scroll_to_load_all

_ITEM_SELECTOR = "[data-testid^='store-item-']"
_PRICE_PATTERN = re.compile(r"£\s*\d+(?:\.\d{2})?")


class UberEatsScraper(BaseScraper):
    site_name = "Uber Eats - Rosa's Thai (Islington)"
    base_url = (
        "https://www.ubereats.com/gb/store/rosas-thai-islington/"
        "eWs49JFtXyymCktZVnXtRA?diningMode=DELIVERY"
    )

    def __init__(
        self,
        url: str | None = None,
        *,
        site_name: str | None = None,
        page_load_timeout: int = 30,
        max_scrolls: int = 20,
    ) -> None:
        if site_name is not None:
            self.site_name = site_name
        self.url = url or self.base_url
        self.page_load_timeout = page_load_timeout
        self.max_scrolls = max_scrolls

    def scrape(self) -> list[MenuItem]:
        with chrome_driver() as driver:
            driver.get(self.url)
            accept_cookies(driver)
            self._wait_for_menu(driver)
            scroll_to_load_all(driver, max_scrolls=self.max_scrolls)
            return self._extract_items(driver)

    def _wait_for_menu(self, driver) -> None:
        try:
            WebDriverWait(driver, self.page_load_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, _ITEM_SELECTOR))
            )
        except TimeoutException as exc:
            raise RuntimeError(
                "Timed out waiting for the Uber Eats menu to load. Uber Eats may "
                "be blocking automated browsers or have changed its layout."
            ) from exc

    def _extract_items(self, driver) -> list[MenuItem]:
        items: list[MenuItem] = []
        seen: set[str] = set()
        category: str | None = None
        for element in driver.find_elements(By.CSS_SELECTOR, "li[data-testid], h3, h2"):
            testid = element.get_attribute("data-testid") or ""
            if testid.startswith("store-item-"):
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});", element
                )
                time.sleep(0.1)
                item = self._card_to_item(element, category)
                if item and item.name not in seen:
                    seen.add(item.name)
                    items.append(item)
            elif element.tag_name in {"h2", "h3"} and element.text.strip():
                category = element.text.strip()
        if not items:
            for card in driver.find_elements(By.CSS_SELECTOR, _ITEM_SELECTOR):
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});", card
                )
                time.sleep(0.1)
                item = self._card_to_item(card, category)
                if item and item.name not in seen:
                    seen.add(item.name)
                    items.append(item)
        return items

    def _card_to_item(self, card: WebElement, category: str | None) -> MenuItem | None:
        lines = [line.strip() for line in card.text.splitlines() if line.strip()]
        if not lines:
            return None
        name = lines[0]
        if name.startswith("#") and "most liked" in name.lower():
            name = lines[1] if len(lines) > 1 else name
        match = _PRICE_PATTERN.search(card.text)
        if not match:
            return None
        return MenuItem(name=name, price=parse_price(match.group()), category=category)
