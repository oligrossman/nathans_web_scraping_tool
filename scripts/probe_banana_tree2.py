"""Detailed Banana Tree extraction probe."""
from __future__ import annotations

import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from src.scrapers.driver import chrome_driver
from src.scrapers.helpers import accept_cookies, scroll_to_load_all

URL = "https://bananatree.co.uk/restaurants/chelmsford/menu"
_CARD = ".k10-recipe_menu-item"
_PRICE_RE = re.compile(r"\d+\.\d{2}")


def extract_text(card) -> str:
    return card.text.strip()


def extract_child(card, driver) -> dict:
    return driver.execute_script(
        """
        const el = arguments[0];
        const pick = (sel) => {
          const n = el.querySelector(sel);
          return n ? n.textContent.trim() : null;
        };
        return {
          name: pick('.k10-recipe__name, .recipe-name, h3, h4, [class*="name"]'),
          price: pick('.k10-recipe__price, .recipe-price, [class*="price"]'),
          desc: pick('.k10-recipe__description, [class*="description"]'),
        };
        """,
        card,
    )


def count_extractable(cards, driver, *, scroll: bool) -> int:
    ok = 0
    for card in cards:
        if scroll:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", card)
            time.sleep(0.2)
        text = extract_text(card)
        child = extract_child(card, driver)
        has_price = bool(_PRICE_RE.search(text)) or bool(child.get("price"))
        has_name = bool(text.splitlines()[0].strip() if text else child.get("name"))
        if has_name and has_price:
            ok += 1
    return ok


def main() -> None:
    with chrome_driver() as driver:
        driver.get(URL)
        accept_cookies(driver)
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, _CARD))
        )
        scroll_to_load_all(driver, max_scrolls=20)
        cards = driver.find_elements(By.CSS_SELECTOR, _CARD)
        print(f"Cards: {len(cards)}")
        print(f"Extractable without per-card scroll: {count_extractable(cards, driver, scroll=False)}")
        print(f"Extractable with per-card scroll: {count_extractable(cards, driver, scroll=True)}")

        # Category tabs
        tabs = driver.find_elements(By.CSS_SELECTOR, ".k10-tabs__tab, [class*='tabs'] a, [class*='tabs'] button")
        tab_labels = []
        for t in tabs:
            txt = t.text.strip()
            if txt and len(txt) < 40 and txt not in tab_labels:
                tab_labels.append(txt)
        print(f"Tab candidates: {tab_labels[:15]}")

        # Try clicking each short tab
        for label in ["All", "For The Table", "To Share", "Small Plates", "Ramen and Laksa"]:
            els = driver.find_elements(
                By.XPATH,
                f"//*[contains(@class,'tab') and normalize-space()='{label}']"
                f"|//a[normalize-space()='{label}']|//button[normalize-space()='{label}']",
            )
            if not els:
                print(f"No tab for {label!r}")
                continue
            driver.execute_script("arguments[0].click();", els[0])
            time.sleep(2)
            scroll_to_load_all(driver, max_scrolls=10)
            tab_cards = driver.find_elements(By.CSS_SELECTOR, _CARD)
            count = count_extractable(tab_cards, driver, scroll=True)
            print(f"Tab {label!r}: {len(tab_cards)} cards, {count} extractable")

        # Sample child structure from first card
        if cards:
            html = driver.execute_script("return arguments[0].innerHTML;", cards[0])[:1500]
            print("\nFirst card innerHTML snippet:")
            print(html)


if __name__ == "__main__":
    main()
