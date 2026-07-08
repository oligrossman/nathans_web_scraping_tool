"""Scraper for the Banana Tree restaurant menu (K10 / TenKites platform).

Why this scraper is structured the way it is
--------------------------------------------
The K10 platform renders the menu in two awkward ways:

1. **Lazy card faces.** The visible ``.k10-recipe_menu-item`` cards only paint
   their text once scrolled into view, so reading ``card.text`` misses roughly
   half of them. Fortunately every card also embeds a *hidden modal*
   (``.k10-recipe-modal``) whose name/price/description are present in the DOM
   from the start. Reading the modal is therefore far more reliable than the
   visible face.

2. **"Build your own" (BYO) dishes.** A single dish (e.g. *Thai Massaman
   Curry*) is one card that contains several priced protein options
   (*Beef*, *Chicken*, *Tofu* …). Each option is its own recipe modal nested
   inside the parent. This is the "multiple items on one card" case: naively
   we only captured one of them.

The fix reads directly from the DOM structure (modals + BYO headers) in a
single JavaScript pass, then names and de-duplicates the results in Python.
The menu is rendered twice (masonry layout), so de-duplication on
``(name, price)`` is essential.
"""

from __future__ import annotations

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .base import BaseScraper, MenuItem, parse_price
from .driver import chrome_driver
from .helpers import accept_cookies, scroll_to_load_all

_CARD_SELECTOR = ".k10-recipe_menu-item"

# Single DOM pass. Returns standalone items plus BYO parent dishes with their
# priced variant options. Everything is read from embedded modals / BYO headers
# so it does not depend on lazy-loaded visible card faces.
_EXTRACT_JS = r"""
const textOf = (el, sel) => {
  const n = el.querySelector(sel);
  return n ? n.textContent.trim() : null;
};

const standalone = [];
for (const card of document.querySelectorAll('.k10-recipe_menu-item')) {
  standalone.push({
    name: textOf(card, '.k10-recipe-modal__recipe-name'),
    price: textOf(card, '.k10-recipe-modal__price'),
    desc: textOf(card, '.k10-recipe-modal__description, .k10-recipe-modal__summary'),
  });
}

const byo = [];
for (const parent of document.querySelectorAll('.k10-recipe_byo.k10-byo')) {
  const dish = textOf(parent, '.k10-byo__name');
  const desc = textOf(parent, '.k10-byo__desc');
  const variants = [];
  for (const item of parent.querySelectorAll('.k10-byo-item_recipe')) {
    variants.push({
      name: textOf(item, '.k10-recipe-modal__recipe-name'),
      price: textOf(item, '.k10-recipe-modal__price'),
    });
  }
  byo.push({ dish, desc, variants });
}

return { standalone, byo };
"""


def _combine_name(dish: str | None, variant: str | None) -> str | None:
    """Build a self-descriptive name for a BYO variant.

    A bare protein variant (``"Beef"``) is prefixed with its parent dish
    (``"Thai Massaman Curry - Beef"``). When the variant already contains the
    dish name (or vice versa) the more descriptive of the two is kept to avoid
    redundant labels like ``"Noodles - Chicken Singapore Noodles"``.
    """
    if not variant:
        return dish
    if not dish:
        return variant
    if dish.lower() in variant.lower() or variant.lower() in dish.lower():
        return dish if len(dish) >= len(variant) else variant
    return f"{dish} - {variant}"


class BananaTreeScraper(BaseScraper):
    site_name = "Banana Tree (Chelmsford, Restaurant)"
    base_url = "https://bananatree.co.uk/restaurants/chelmsford/menu"

    def __init__(self, *, page_load_timeout: int = 30, max_scrolls: int = 25) -> None:
        self.page_load_timeout = page_load_timeout
        self.max_scrolls = max_scrolls

    def scrape(self) -> list[MenuItem]:
        with chrome_driver() as driver:
            driver.get(self.base_url)
            accept_cookies(driver)
            self._wait_for_menu(driver)
            scroll_to_load_all(driver, max_scrolls=self.max_scrolls)
            data = driver.execute_script(_EXTRACT_JS)
        return self._build_items(data)

    def _wait_for_menu(self, driver) -> None:
        try:
            WebDriverWait(driver, self.page_load_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, _CARD_SELECTOR))
            )
        except TimeoutException as exc:
            raise RuntimeError(
                "Timed out waiting for the Banana Tree menu to load."
            ) from exc

    @staticmethod
    def _build_items(data: dict) -> list[MenuItem]:
        items: list[MenuItem] = []
        seen: set[tuple[str, float]] = set()

        def add(name: str | None, price_text: str | None,
                description: str | None, category: str | None) -> None:
            price = parse_price(price_text)
            if not name or price is None:
                return
            key = (name, price)
            if key in seen:
                return
            seen.add(key)
            items.append(
                MenuItem(
                    name=name,
                    price=price,
                    category=category,
                    description=description,
                )
            )

        for entry in data.get("standalone", []):
            add(entry.get("name"), entry.get("price"), entry.get("desc"), None)

        for dish in data.get("byo", []):
            dish_name = dish.get("dish")
            for variant in dish.get("variants", []):
                add(
                    _combine_name(dish_name, variant.get("name")),
                    variant.get("price"),
                    dish.get("desc"),
                    dish_name,
                )

        return items
