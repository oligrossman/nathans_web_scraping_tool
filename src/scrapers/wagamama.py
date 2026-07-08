"""Scraper for Wagamama restaurant website menu (takeout.wagamama.com)."""

from __future__ import annotations

import time

import re

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .base import BaseScraper, MenuItem, parse_price
from .driver import chrome_driver
from .helpers import accept_cookies, scroll_to_load_all

_CARD_SELECTOR = "a.product-link.js-product-link"
_SECTION_SELECTOR = "h2"
_PRICE_PATTERN = re.compile(r"£\s*\d+(?:\.\d{2})?")


class WagamamaScraper(BaseScraper):
    site_name = "Wagamama (Brighton, Restaurant)"
    base_url = (
        "https://takeout.wagamama.com/TimeSlotSelection"
        "?storeId=22&orderType=ClickAndCollect"
    )

    def __init__(self, *, page_load_timeout: int = 30, max_scrolls: int = 20) -> None:
        self.page_load_timeout = page_load_timeout
        self.max_scrolls = max_scrolls

    def scrape(self) -> list[MenuItem]:
        with chrome_driver() as driver:
            driver.get(self.base_url)
            accept_cookies(driver)
            self._navigate_to_menu(driver)
            scroll_to_load_all(driver, max_scrolls=self.max_scrolls)
            return self._extract_items(driver)

    def _navigate_to_menu(self, driver) -> None:
        time.sleep(4)
        buttons = driver.find_elements(
            By.XPATH,
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
            "'abcdefghijklmnopqrstuvwxyz'), 'start order')]",
        )
        if buttons:
            driver.execute_script("arguments[0].click();", buttons[0])
            time.sleep(5)
        try:
            WebDriverWait(driver, self.page_load_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, _CARD_SELECTOR))
            )
        except TimeoutException as exc:
            raise RuntimeError(
                "Timed out waiting for the Wagamama menu to load."
            ) from exc

    def _extract_items(self, driver) -> list[MenuItem]:
        items: list[MenuItem] = []
        seen: set[tuple[str, str | None]] = set()
        for card in driver.find_elements(By.CSS_SELECTOR, _CARD_SELECTOR):
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", card)
            category = card.get_attribute("data-tracking-itemcategory")
            item = self._card_to_item(card, category)
            key = (item.name, item.category) if item else None
            if item and key not in seen:
                seen.add(key)
                items.append(item)
        return items

    def _card_to_item(self, card: WebElement, category: str | None) -> MenuItem | None:
        name = card.get_attribute("data-tracking-itemname")
        if not name:
            try:
                name = card.find_element(By.CSS_SELECTOR, ".product-title, h2, h3").text.strip()
            except NoSuchElementException:
                lines = [line.strip() for line in card.text.splitlines() if line.strip()]
                name = lines[0] if lines else None
        price_attr = card.get_attribute("data-tracking-itemprice")
        price = parse_price(price_attr) if price_attr else None
        if price is None:
            try:
                price = parse_price(card.find_element(By.CSS_SELECTOR, ".prices").text)
            except NoSuchElementException:
                match = _PRICE_PATTERN.search(card.text)
                price = parse_price(match.group()) if match else None
        if not name or price is None:
            return None
        description = None
        try:
            details = card.find_element(By.CSS_SELECTOR, ".details").text.splitlines()
            if len(details) > 2:
                description = details[1]
        except NoSuchElementException:
            pass
        return MenuItem(name=name, price=price, category=category, description=description)
