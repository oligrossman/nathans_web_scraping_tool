"""Probe Banana Tree card child selectors."""
from __future__ import annotations

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from src.scrapers.driver import chrome_driver
from src.scrapers.helpers import accept_cookies, scroll_to_load_all

URL = "https://bananatree.co.uk/restaurants/chelmsford/menu"
_CARD = ".k10-recipe_menu-item"


def card_info(driver, card, idx: int) -> None:
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", card)
    time.sleep(0.3)
    info = driver.execute_script(
        """
        const el = arguments[0];
        const all = el.querySelectorAll('*');
        const classes = new Set();
        for (const n of all) {
          if (n.className && typeof n.className === 'string') {
            for (const c of n.className.split(/\\s+/)) {
              if (c.includes('recipe') || c.includes('name') || c.includes('price') || c.includes('title'))
                classes.add(c);
            }
          }
        }
        const pick = (...sels) => {
          for (const s of sels) {
            const n = el.querySelector(s);
            if (n && n.textContent.trim()) return [s, n.textContent.trim()];
          }
          return null;
        };
        return {
          text: el.textContent.trim().slice(0, 120),
          textLen: el.textContent.trim().length,
          recipeClasses: [...classes].sort().slice(0, 30),
          name: pick('.k10-recipe__name', '.k10-w-recipe__name', 'h3', 'h4'),
          price: pick('.k10-recipe__price', '.k10-w-recipe__price', '[class*="price"]'),
          summary: pick('.k10-recipe__summary', '.k10-w-recipe__summary'),
          outer: pick('.k10-recipe__outer', '.k10-w-recipe__outer'),
        };
        """,
        card,
    )
    print(f"\nCard {idx}: textLen={info['textLen']}")
    print(f"  text: {info['text']!r}")
    print(f"  name: {info['name']}")
    print(f"  price: {info['price']}")
    print(f"  summary: {info['summary']}")
    print(f"  outer: {info['outer']}")
    if idx == 0:
        print(f"  classes: {info['recipeClasses']}")


def main() -> None:
    with chrome_driver() as driver:
        driver.get(URL)
        accept_cookies(driver)
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, _CARD)))
        scroll_to_load_all(driver, max_scrolls=20)
        cards = driver.find_elements(By.CSS_SELECTOR, _CARD)

        # First 3 and some empty ones
        for i, card in enumerate(cards[:5]):
            card_info(driver, card, i)

        empty_idxs = [i for i, c in enumerate(cards) if not c.text.strip()]
        print(f"\nEmpty cards: {len(empty_idxs)} / {len(cards)}")
        for i in empty_idxs[:3]:
            card_info(driver, cards[i], i)


if __name__ == "__main__":
    main()
