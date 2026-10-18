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

import os
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from collections.abc import Iterator

from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_DRIVER_LOCK = threading.Lock()

# Distro paths on Streamlit Community Cloud (Debian). Selenium Manager's
# downloaded chromedriver exits 127 there: the binary cannot load.
_SYSTEM_BROWSER_CANDIDATES = (
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)
_SYSTEM_DRIVER_CANDIDATES = (
    "/usr/bin/chromedriver",
    "/usr/lib/chromium/chromedriver",
)


def _usable_executable(path: str | None) -> str | None:
    """Return ``path`` when it is an executable file, otherwise ``None``."""
    if not path:
        return None
    if os.path.isfile(path) and os.access(path, os.X_OK):
        return path
    return None


def _first_usable(candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        found = _usable_executable(candidate)
        if found is not None:
            return found
    return None


def resolve_chrome_binaries(
    browser_candidates: tuple[str, ...] = _SYSTEM_BROWSER_CANDIDATES,
    driver_candidates: tuple[str, ...] = _SYSTEM_DRIVER_CANDIDATES,
) -> tuple[str | None, str | None]:
    """Return a matching system browser and driver, or ``(None, None)``.

    A pair is required. Using only one of them leaves Selenium Manager to mix
    a downloaded driver with a different browser. ``CHROME_BIN`` and
    ``CHROMEDRIVER_PATH`` replace the default search for whichever one is set
    to an executable file.
    """
    browser = _usable_executable(os.environ.get("CHROME_BIN")) or _first_usable(
        browser_candidates
    )
    driver = _usable_executable(os.environ.get("CHROMEDRIVER_PATH")) or _first_usable(
        driver_candidates
    )
    if browser is not None and driver is not None:
        return browser, driver
    return None, None


def _driver_failed_to_load(exc: WebDriverException) -> bool:
    """True when the OS refused to execute chromedriver (exit status 127)."""
    return "Status code was: 127" in str(exc)


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

    On a machine with Debian's ``chromium`` and ``chromium-driver`` installed
    (Streamlit Community Cloud, via ``packages.txt``), that pair is used.
    Otherwise Selenium Manager downloads a driver matched to the local Chrome.
    """
    browser, driver_bin = resolve_chrome_binaries()
    with _DRIVER_LOCK:
        last_exc: SessionNotCreatedException | None = None
        for attempt in range(1, max_attempts + 1):
            user_data_dir = tempfile.mkdtemp(prefix="chrome-profile-")
            driver: webdriver.Chrome | None = None
            try:
                options = _build_options(user_data_dir)
                if browser is not None:
                    options.binary_location = browser
                service = Service(executable_path=driver_bin) if driver_bin else None
                if service is None:
                    driver = webdriver.Chrome(options=options)
                else:
                    driver = webdriver.Chrome(service=service, options=options)
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
            except WebDriverException as exc:
                if _driver_failed_to_load(exc):
                    raise RuntimeError(
                        "ChromeDriver exited with status 127, so the operating "
                        "system could not load the driver (a missing shared "
                        "library, or a driver newer than this machine's glibc). "
                        "On Streamlit Community Cloud, packages.txt must install "
                        "chromium and chromium-driver, then reboot the app. "
                        f"Original error: {exc}"
                    ) from exc
                raise
            finally:
                shutil.rmtree(user_data_dir, ignore_errors=True)

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Failed to start Chrome for an unknown reason.")
