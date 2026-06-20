"""Scraper for Pizza Express collection menu (mobile-pay site).

The menu is split into category tabs (Drinks, Starters & Sides, Pizza, etc.).
We accept cookies, click each tab, scroll to load items, and read every
``.menu-card`` for name and price.
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

_CARD_SELECTOR = ".menu-card"
_COOKIE_XPATHS = (
    "//button[contains(., 'Accept')]",
    "//button[contains(., 'Agree')]",
    "//button[contains(., 'Allow')]",
)
_PRICE_SELECTOR = "h4.price-header"
_DESC_SELECTOR = "[class*='description']"

# Top-level tabs on the restaurant menu page.
_CATEGORY_TABS = (
    "Drinks",
    "Starters & Sides",
    "Pizza",
    "Mains",
    "Desserts",
    "Kids",
    "Gluten-Free",
)


class PizzaExpressScraper(BaseScraper):
    site_name = "Pizza Express (Birmingham Bullring, Collection)"
    base_url = (
        "https://mobile-pay.pizzaexpress.com/birmingham-bullring-shopping-centre/"
        "restaurant-menu/main-menu"
    )

    def __init__(self, *, page_load_timeout: int = 30, max_scrolls: int = 15) -> None:
        self.page_load_timeout = page_load_timeout
        self.max_scrolls = max_scrolls

    def scrape(self) -> list[MenuItem]:
        with chrome_driver() as driver:
            driver.get(self.base_url)
            self._accept_cookies(driver)
            self._wait_for_menu(driver)
            items: list[MenuItem] = []
            seen: set[tuple[str, str]] = set()
            for category in _CATEGORY_TABS:
                if not self._click_category(driver, category):
                    continue
                self._scroll_to_load_all(driver)
                for card in driver.find_elements(By.CSS_SELECTOR, _CARD_SELECTOR):
                    item = self._card_to_item(card, category)
                    key = (item.name, item.category) if item else None
                    if item and key not in seen:
                        seen.add(key)
                        items.append(item)
            return items

    def _accept_cookies(self, driver) -> None:
        time.sleep(4)
        for xpath in _COOKIE_XPATHS:
            for button in driver.find_elements(By.XPATH, xpath):
                try:
                    driver.execute_script("arguments[0].click();", button)
                    time.sleep(2)
                    return
                except Exception:
                    continue

    def _wait_for_menu(self, driver) -> None:
        try:
            WebDriverWait(driver, self.page_load_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, _CARD_SELECTOR))
            )
        except TimeoutException as exc:
            raise RuntimeError(
                "Timed out waiting for the Pizza Express menu to load."
            ) from exc

    def _click_category(self, driver, category: str) -> bool:
        matches = driver.find_elements(
            By.XPATH, f"//*[normalize-space(text())='{category}']"
        )
        for element in matches:
            if not element.is_displayed():
                continue
            try:
                driver.execute_script("arguments[0].click();", element)
                time.sleep(2)
                return True
            except Exception:
                continue
        return False

    def _scroll_to_load_all(self, driver) -> None:
        last_height = driver.execute_script("return document.body.scrollHeight")
        for _ in range(self.max_scrolls):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.6)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

    def _card_to_item(self, card: WebElement, category: str) -> MenuItem | None:
        try:
            scope = card.find_element(By.CSS_SELECTOR, ".menu-card-inner")
        except NoSuchElementException:
            scope = card
        try:
            name = scope.find_element(By.CSS_SELECTOR, "[class*='title']").text.strip()
            name = name.splitlines()[0]
        except NoSuchElementException:
            return None
        try:
            price_text = scope.find_element(By.CSS_SELECTOR, _PRICE_SELECTOR).text
        except NoSuchElementException:
            return None
        price = parse_price(price_text)
        if not name or price is None:
            return None
        return MenuItem(
            name=name,
            price=price,
            category=category,
            description=self._optional_text(scope, _DESC_SELECTOR),
        )

    @staticmethod
    def _optional_text(scope: WebElement, selector: str) -> str | None:
        try:
            return scope.find_element(By.CSS_SELECTOR, selector).text.strip() or None
        except NoSuchElementException:
            return None
