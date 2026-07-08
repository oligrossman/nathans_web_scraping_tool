"""Shared Selenium helpers used by multiple chain scrapers."""

from __future__ import annotations

import time

from selenium.webdriver.common.by import By


_COOKIE_XPATHS = (
    "//button[@id='onetrust-accept-btn-handler']",
    "//button[contains(., 'Accept all')]",
    "//button[contains(., 'Accept')]",
    "//button[contains(., 'Agree')]",
)


def accept_cookies(driver) -> None:
    """Dismiss a cookie consent wall when present."""
    time.sleep(3)
    for xpath in _COOKIE_XPATHS:
        buttons = driver.find_elements(By.XPATH, xpath)
        if buttons:
            driver.execute_script("arguments[0].click();", buttons[0])
            time.sleep(2)
            return


def scroll_to_load_all(driver, *, max_scrolls: int = 20, pause: float = 0.8) -> None:
    """Scroll down repeatedly until the page height stops growing."""
    last_height = driver.execute_script("return document.body.scrollHeight")
    for _ in range(max_scrolls):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
