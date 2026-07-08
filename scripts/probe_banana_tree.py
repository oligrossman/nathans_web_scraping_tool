"""Probe Banana Tree K10 menu DOM structure."""
from __future__ import annotations

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from src.scrapers.driver import chrome_driver
from src.scrapers.helpers import accept_cookies, scroll_to_load_all

URL = "https://bananatree.co.uk/restaurants/chelmsford/menu"


def main() -> None:
    with chrome_driver() as driver:
        driver.get(URL)
        accept_cookies(driver)
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "[class*='menu-item']"))
        )
        scroll_to_load_all(driver, max_scrolls=20)

        cards = driver.find_elements(By.CSS_SELECTOR, "[class*='menu-item']")
        print(f"Total menu-item elements: {len(cards)}")

        populated = 0
        empty = 0
        sample_attrs: list[str] = []
        for card in cards:
            if "menu-item" not in (card.get_attribute("class") or ""):
                continue
            text = card.text.strip()
            if text and "£" in text:
                populated += 1
            else:
                empty += 1
            if len(sample_attrs) < 3:
                attrs = driver.execute_script(
                    """
                    const el = arguments[0];
                    const out = {class: el.className};
                    for (const a of el.attributes) out[a.name] = a.value;
                    out.childCount = el.children.length;
                    out.innerHTML_len = el.innerHTML.length;
                    return out;
                    """,
                    card,
                )
                sample_attrs.append((text[:60] or "(empty)", attrs))

        print(f"Populated: {populated}, Empty: {empty}")
        for text, attrs in sample_attrs:
            print(f"\n--- sample: {text!r}")
            for k, v in attrs.items():
                print(f"  {k}: {v!r}")

        # Category tabs
        tabs = driver.find_elements(
            By.CSS_SELECTOR,
            "[class*='category'], [class*='tab'], button, a[href*='#']",
        )
        tab_texts = []
        for t in tabs:
            cls = t.get_attribute("class") or ""
            txt = t.text.strip()
            if txt and any(k in cls.lower() for k in ("category", "tab", "nav", "filter")):
                tab_texts.append((txt, cls[:80]))
        print(f"\nPotential category tabs ({len(tab_texts)}):")
        for txt, cls in tab_texts[:20]:
            print(f"  {txt!r} class={cls!r}")

        # Child selectors in populated cards
        if cards:
            for card in cards:
                if "£" in card.text:
                    children = card.find_elements(By.CSS_SELECTOR, "*")
                    print("\nPopulated card child tags:")
                    for c in children[:15]:
                        print(f"  <{c.tag_name}> class={c.get_attribute('class')!r} text={c.text[:40]!r}")
                    break

        # data-* on any element
        data_els = driver.find_elements(By.CSS_SELECTOR, "[data-name], [data-price], [data-item], [data-tracking-itemname]")
        print(f"\nElements with data-name/price/item: {len(data_els)}")
        for el in data_els[:5]:
            print(f"  tag={el.tag_name} attrs={el.get_attribute('outerHTML')[:200]}")


if __name__ == "__main__":
    main()
