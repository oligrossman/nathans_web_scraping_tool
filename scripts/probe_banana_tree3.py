"""Simulate current Banana Tree scraper logic."""
from __future__ import annotations

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from src.scrapers.banana_tree import BananaTreeScraper, _CARD_SELECTOR
from src.scrapers.driver import chrome_driver
from src.scrapers.helpers import accept_cookies, scroll_to_load_all

URL = "https://bananatree.co.uk/restaurants/chelmsford/menu"


def main() -> None:
    scraper = BananaTreeScraper()
    with chrome_driver() as driver:
        driver.get(URL)
        accept_cookies(driver)
        scraper._wait_for_menu(driver)
        scroll_to_load_all(driver, max_scrolls=20)

        cards = driver.find_elements(By.CSS_SELECTOR, _CARD_SELECTOR)
        print(f"Total [{_CARD_SELECTOR}]: {len(cards)}")

        passed_filter = 0
        extracted = 0
        failed_reasons: dict[str, int] = {}
        for card in cards:
            if "menu-item" not in (card.get_attribute("class") or ""):
                failed_reasons["no menu-item in class"] = failed_reasons.get("no menu-item in class", 0) + 1
                continue
            passed_filter += 1
            lines = [line.strip() for line in card.text.splitlines() if line.strip()]
            item = scraper._card_to_item(card, category=None)
            if item:
                extracted += 1
            else:
                reason = "few lines" if len(lines) < 2 else "no price/name"
                failed_reasons[reason] = failed_reasons.get(reason, 0) + 1
                if failed_reasons[reason] <= 3:
                    print(f"FAIL ({reason}): lines={lines[:5]!r} class={card.get_attribute('class')[:60]}")

        print(f"Passed filter: {passed_filter}")
        print(f"Extracted: {extracted}")
        print(f"Failed reasons: {failed_reasons}")


if __name__ == "__main__":
    main()
