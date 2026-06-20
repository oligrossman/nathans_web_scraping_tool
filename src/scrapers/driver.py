"""Headless Chrome WebDriver factory.

Centralising driver creation means every scraper gets the same sensible
defaults (headless, sane window size, a realistic user agent) and there is a
single place to tune browser behaviour later.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _build_options() -> Options:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1440,2000")
    options.add_argument(f"--user-agent={_USER_AGENT}")
    return options


@contextmanager
def chrome_driver() -> Iterator[webdriver.Chrome]:
    """Yield a configured headless Chrome driver and always quit it.

    Selenium 4's built-in Selenium Manager downloads the matching driver
    automatically, so no manual chromedriver setup is required.
    """
    driver = webdriver.Chrome(options=_build_options())
    try:
        yield driver
    finally:
        driver.quit()
