"""Headless Chrome WebDriver factory.

Centralising driver creation means every scraper gets the same sensible
defaults (headless, sane window size, a realistic user agent) and there is a
single place to tune browser behaviour later.

Chrome is not reliable when several headless sessions start at once (common in
the Streamlit scrape page's thread pool), so we serialise access with a lock,
give each session its own temporary profile directory, and retry transient
startup failures.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from collections.abc import Iterator

from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException
from selenium.webdriver.chrome.options import Options

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_DRIVER_LOCK = threading.Lock()


def _build_options(user_data_dir: str) -> Options:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument(f"--user-data-dir={user_data_dir}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--window-size=1440,2000")
    options.add_argument(f"--user-agent={_USER_AGENT}")
    return options


@contextmanager
def chrome_driver(*, max_attempts: int = 3) -> Iterator[webdriver.Chrome]:
    """Yield a configured headless Chrome driver and always quit it.

    Selenium 4's built-in Selenium Manager downloads the matching driver
    automatically, so no manual chromedriver setup is required.
    """
    with _DRIVER_LOCK:
        last_exc: SessionNotCreatedException | None = None
        for attempt in range(1, max_attempts + 1):
            user_data_dir = tempfile.mkdtemp(prefix="chrome-profile-")
            driver: webdriver.Chrome | None = None
            try:
                driver = webdriver.Chrome(options=_build_options(user_data_dir))
                try:
                    yield driver
                finally:
                    driver.quit()
                return
            except SessionNotCreatedException as exc:
                last_exc = exc
                if driver is not None:
                    try:
                        driver.quit()
                    except Exception:
                        pass
                if attempt < max_attempts:
                    time.sleep(0.75 * attempt)
            finally:
                shutil.rmtree(user_data_dir, ignore_errors=True)

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Failed to start Chrome for an unknown reason.")
