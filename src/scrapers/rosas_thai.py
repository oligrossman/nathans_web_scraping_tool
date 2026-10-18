"""Scraper for the Rosa's Thai restaurant menu.

The Islington site uses the London menu at ``https://rosasthai.com/menus``.
That page loads a K10 / TenKites menu, the same platform as Banana Tree, so
the extraction is reused rather than copied.
"""

from __future__ import annotations

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .banana_tree import BananaTreeScraper, _CARD_SELECTOR


class RosasThaiScraper(BananaTreeScraper):
    site_name = "Rosa's Thai (London - Islington, Restaurant)"
    base_url = "https://rosasthai.com/menus"

    def _wait_for_menu(self, driver) -> None:
        try:
            WebDriverWait(driver, self.page_load_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, _CARD_SELECTOR))
            )
        except TimeoutException as exc:
            raise RuntimeError(
                "Timed out waiting for the Rosa's Thai menu to load."
            ) from exc
