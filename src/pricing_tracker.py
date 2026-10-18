"""Import observed price history from the pricing-tracker workbook.

The workbook and the scraper do not share one spelling of each dish. History
keeps the scraper's name when a tracker row is the same dish, and drops tracker
rows that are not on the scraped menu. A second name for a dish the app already
knows would split one price series into two.

Matching is deliberate and narrow:

1. Normalise case, whitespace, apostrophes, trademark symbols, and the
   PERi-PERi spelling.
2. If that key hits exactly one scraped name, use it.
3. Otherwise use an explicit alias, and only when that alias is itself a
   scraped name.

Close names are not matched. "15 Chicken Wings" must not become "10 Chicken
Wings", and a bottomless drink must not become the bottled one.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.enrichment.identity import make_dish_id
from src.synthetic import HISTORY_COLUMNS, resolve_bucket

# Tracker spelling -> the scraped name it refers to.
# Normalisation is not enough: a typo, a quantity written as a digit, or a
# country the scraper appends in brackets.
NANDOS_NAME_ALIASES: dict[str, str] = {
    "Buttefly Burger": "Butterfly Burger",
    "pastel de nata": "One Pastel de Nata",
    "4 pastel de natas": "Four Pastel de Natas",
    "creative block 5": "Creative Block 5 (South Africa)",
    "creative block 2": "Creative Block 2 (South Africa)",
    "spier merlot": "Spier Merlot (South Africa)",
    "spier sauvignon blanc": "Spier Sauvignon Blanc (South Africa)",
    "spier rose": "Spier Rose (South Africa)",
}

GIGGLING_SQUID_NAME_ALIASES: dict[str, str] = {
    "Crispy squid bites": "Crispy Squid Bits",
    "Chilli & lime sea bass": "Chilli Lime Sea Bass",
    "Tamarind mushroom & greens": "Tamarind Mushrooms & Greens",
    "Satay 4 ways": "Satay Four Ways",
    "Royal fishing boat curry": "Red Royal Fishing Boat Curry",
    "Chicken kua kling": "Feisty Chicken Kua Kling",
    "Mad Squirrel Sumo American Pale Ale 4.7%": (
        "Mad Squirrel Sumo American Pale Ale 4.7% ABV (440ml)"
    ),
}

PIZZA_EXPRESS_NAME_ALIASES: dict[str, str] = {
    "Doughballs Al Forno": "Dough Balls Al Forno",
    "Lotus Biscoff Cheesecake": "Biscoff® Cheesecake",
    "Grand Chicken Caesar Salad": "Grand Chicken Caesar",
    "Gelato & Sorbet (2 Scoops)": "Gelato & Sorbet",
    "Acqua Panna Still Mineral": "Acqua Panna Still Mineral Water",
    "Baileys": "Baileys (ABV 17%) - 50ml",
    "Ketel One Vodka": "Ketel One Vodka (ABV 40%) - 25ml",
    "Montepulciano D'Abruzzo": "Montepulciano d'Abruzzo D.O.C",
    "Cornish Orchards Raspberry & Elderflower - 500ml": (
        "Cornish Orchards Raspberry & Elderflower Cider (ABV 4%) - 500ml"
    ),
    "Cornish Orchards Gold Cider - 500ml": "Cornish Orchards Gold Cider (ABV 5%) - 500ml",
    "Fuller's London Pride Amber Ale - 500ml": (
        "Fuller's London Pride Amber Ale (ABV 4.7%) - 500ml"
    ),
    "Gluten-Free Peroni Nastro Azzurro - 330ml": (
        "Gluten-Free Peroni Nastro Azzurro (ABV 5%) - 330ml"
    ),
    "Meantime Pizza Pale - 330ml": "Meantime Pizza Pale (ABV 4%) - 330ml",
    "Peroni Nastro Azzurro - 330ml": "Peroni Nastro Azzurro (ABV 5%) - 330ml",
    "Peroni Nastro Azzurro - 330ml 0.0%": "Peroni Nastro Azzuro 0.0 (ABV 0%) - 330ml",
}

PREZZO_NAME_ALIASES: dict[str, str] = {
    "Garlic Roasted Potatoes": "Garlic Roast Potatoes",
    "The Veggie One": "The Veggie One Pizza",
    "Hot Honey Glazed Mozzarella Sticks": "Hot Honey Glazed Giant Mozzarella Sticks",
}

ZIZZI_NAME_ALIASES: dict[str, str] = {
    "Jameson Irish Whiskey 25ml": "Jamesons Irish Whiskey 25ml",
    "Jameson Irish Whiskey 50ml": "Jamesons Irish Whiskey 50ml",
    "Mr Black Coffee Liqueur 25ml": "Mr Black Coffee Liquor 25ml",
    "Mr Black Coffee Liqueur 50ml": "Mr Black Coffee Liquor 50ml",
    "Tequila 25ml": "Tequila 25 ml",
    "Tequila 50ml": "Tequila 50 ml",
    "Sauvignon Blanc St Clair (250ml)": "Sauvignon Blanc, St Clair 250ml",
    "Chicken Mialnese Fonduta": "Chicken Milanese Fonduta",
    "Candy Floss Doloe": "Candy Floss Dolce",
    "Grande Antipasti": "Grande Antipasto",
    "Chianti Flask Magnum": "Chianti Flask Magnum bottle",
}

WAGAMAMA_NAME_ALIASES: dict[str, str] = {
    "Chicken Raisukaree": "chicken raisukaree curry",
    "Prawn Raisukaree": "prawn raisukaree curry",
    "Tofu Firecracker": "tofu firecracker curry (vg)",
    "Tofu Raisukaree": "tofu raisukaree curry (vg)",
    "Hoisin Pulled Duck Buns": "hoisin pulled duck bao buns",
    "miso caramal banana bread": "miso caramel banana bread (v)",
    "gochujang tamarind pork ribs": "new! gochujang tamarind pork ribs",
    "gochujang tamarind corn ribs": "new! gochujang tamarind corn ribs (vg)",
}

BANANA_TREE_NAME_ALIASES: dict[str, str] = {
    "Stir-Fry Broccoli": "Stir Fried Broccoli",
}

FIVE_GUYS_NAME_ALIASES: dict[str, str] = {
    "Grilled Cheese Sandwich": "Grilled Cheese",
}

# Workbook sheet that holds the big price table for each scraped brand.
# Zizzi's scrape is the London Bankside menu, which lines up with "Zizzi London".
# Five Guys and Prezzo are scraped in Cambridge, so the national sheet is used
# rather than the one-date London sheet. Pho's snapshot is not a usable menu.
TRACKER_SHEETS: dict[str, str] = {
    "Banana Tree": "Banana Tree",
    "Five Guys": "Five Guys",
    "Giggling Squid": "Giggling Squid",
    "Nando's": "Nandos",
    "Pizza Express": "Pizza Express",
    "Prezzo": "Prezzo",
    "Rosa's Thai": "Rosas",
    "Wagamama": "wagamama",
    "Zizzi": "Zizzi London",
}

# Column A on the Rosa's sheet says "Rosas", not the brand the app stores.
_TRACKER_ROW_BRAND: dict[str, str] = {
    "Rosa's Thai": "Rosas",
}

DEFAULT_WORKBOOK_PATH = (
    Path(__file__).resolve().parent.parent / "Master - Pricing Tracker.xlsx"
)

_ALIASES_BY_BRAND: dict[str, dict[str, str]] = {
    "Banana Tree": BANANA_TREE_NAME_ALIASES,
    "Five Guys": FIVE_GUYS_NAME_ALIASES,
    "Giggling Squid": GIGGLING_SQUID_NAME_ALIASES,
    "Nando's": NANDOS_NAME_ALIASES,
    "Pizza Express": PIZZA_EXPRESS_NAME_ALIASES,
    "Prezzo": PREZZO_NAME_ALIASES,
    "Wagamama": WAGAMAMA_NAME_ALIASES,
    "Zizzi": ZIZZI_NAME_ALIASES,
}

_PERI_PERI = re.compile(r"peri[\s\-]*peri")


def normalise_dish_name(name: str) -> str:
    """Return a comparison key for a dish name.

    Dish ids must change when the visible name changes. This key only recognises
    the same dish when the difference is capitals, PERi-PERi, an ampersand or
    plus written as "and", an apostrophe, a hyphen, a full stop, brackets, or a
    trailing dietary tag such as (v) or (vg).
    """
    text = str(name).strip().lower().replace("\u00a0", " ")
    for original, replacement in (
        ("®", ""),
        ("™", ""),
        ("’", "'"),
        ("‘", "'"),
        ("`", "'"),
        ("–", "-"),
        ("—", "-"),
        ("‑", "-"),
    ):
        text = text.replace(original, replacement)
    text = _PERI_PERI.sub("peri peri", text)
    text = text.replace("&", " and ")
    text = text.replace("+", " and ")
    text = text.replace("'", "")
    text = text.replace("*", "")
    text = text.replace(".", "")
    text = text.replace("(", " ").replace(")", " ")
    text = re.sub(r"[-/]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Dietary tags the scraper appends, e.g. "chocolate brownie (v)".
    return re.sub(r"\s+(vg|ve|gf|v)$", "", text)


def aliases_for(brand: str) -> dict[str, str]:
    """Return ``{normalised tracker name: scraped name}`` for one brand."""
    raw = _ALIASES_BY_BRAND.get(brand, {})
    aliases = {normalise_dish_name(tracker): scraped for tracker, scraped in raw.items()}
    if len(aliases) != len(raw):
        raise ValueError(f"Alias keys for {brand!r} are not unique after normalisation.")
    return aliases


def match_tracker_name(
    tracker_name: str,
    scrape_names: list[str],
    aliases: dict[str, str],
) -> str | None:
    """Return the scraped name for one tracker dish, or None if it is not the same dish."""
    index: dict[str, list[str]] = {}
    for name in dict.fromkeys(scrape_names):
        index.setdefault(normalise_dish_name(name), []).append(name)

    key = normalise_dish_name(tracker_name)
    alias = aliases.get(key)
    if alias is not None:
        return alias if alias in set(scrape_names) else None
    hits = index.get(key, [])
    if len(hits) == 1:
        return hits[0]
    return None


def _header_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _price(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return float(value)


def row_brand_for(brand: str) -> str:
    """Return the brand text written in column A of that brand's sheet."""
    return _TRACKER_ROW_BRAND.get(brand, brand)


def read_big_table(
    path: Path | str,
    sheet: str,
    brand: str,
    *,
    row_brand: str | None = None,
) -> pd.DataFrame:
    """Read the dish-by-date price grid from one workbook sheet.

    Ignores the summary blocks above the grid and the change-percentage columns
    to the right of it. Returns columns ``tracker_name``, ``date``, ``price``.
    """
    from openpyxl import load_workbook

    expected = (row_brand if row_brand is not None else brand).casefold()
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = workbook[sheet]
        header: list[object] | None = None
        records: list[dict[str, object]] = []
        for row in worksheet.iter_rows(values_only=True):
            if not row:
                continue
            brand_cell = "" if row[0] is None else str(row[0]).strip()
            dish_cell = ""
            if len(row) > 3 and row[3] is not None:
                dish_cell = str(row[3]).strip()
            if brand_cell == "Brand" and dish_cell == "Dish name":
                header = list(row)
                continue
            # The tracker is not consistent about capitals ("Pho" and "pho").
            if header is None or brand_cell.casefold() != expected or not dish_cell:
                continue
            for column, value in enumerate(row):
                observed = _header_date(header[column]) if column < len(header) else None
                price = _price(value)
                if observed is None or price is None:
                    continue
                records.append(
                    {"tracker_name": dish_cell, "date": observed, "price": price}
                )
    finally:
        workbook.close()

    if header is None:
        raise ValueError(f"Sheet {sheet!r} has no big table (no Brand / Dish name header).")
    return pd.DataFrame(records, columns=["tracker_name", "date", "price"])


def _bucket_by_item(menu: pd.DataFrame) -> dict[str, str]:
    """Map each scraped dish to the board bucket its menu category resolves to."""
    if "name" not in menu.columns:
        raise ValueError("Menu snapshot needs a name column.")
    buckets: dict[str, str] = {}
    for name, group in menu.groupby("name", sort=False):
        categories = group["category"] if "category" in group.columns else [None]
        # A dish listed under two menu headings still has one price series.
        # The first heading in the snapshot decides the bucket.
        buckets[str(name)] = resolve_bucket(next(iter(categories)))
    return buckets


@dataclass(frozen=True)
class ImportReport:
    """Result of aligning one brand's tracker grid to the scraped menu."""

    history: pd.DataFrame
    matched: tuple[tuple[str, str], ...]
    excluded: tuple[str, ...]
    scrape_without_history: tuple[str, ...]
    conflicts: tuple[tuple[str, str, float, float], ...] = ()


def build_observed_history(
    tracker_prices: pd.DataFrame,
    menu: pd.DataFrame,
    brand: str,
    aliases: dict[str, str] | None = None,
) -> ImportReport:
    """Turn tracker prices into tidy history rows that use scraped dish names.

    Two tracker rows for the same scraped dish are merged. The same price on
    the same date is kept once. If the prices disagree, the later row in the
    sheet is kept and the clash is listed on ``conflicts`` so it can be checked.
    """
    alias_map = aliases_for(brand) if aliases is None else aliases
    scrape_names = [str(name) for name in menu["name"].dropna().unique()]
    buckets = _bucket_by_item(menu)

    prices: dict[str, dict[date, float]] = {}
    seen_pairs: list[tuple[str, str]] = []
    excluded: list[str] = []
    conflicts: list[tuple[str, str, float, float]] = []
    for tracker_name in tracker_prices["tracker_name"].drop_duplicates():
        canonical = match_tracker_name(str(tracker_name), scrape_names, alias_map)
        if canonical is None:
            excluded.append(str(tracker_name))
            continue
        seen_pairs.append((str(tracker_name), canonical))

    for record in tracker_prices.itertuples(index=False):
        canonical = match_tracker_name(str(record.tracker_name), scrape_names, alias_map)
        if canonical is None:
            continue
        observed = record.date
        if isinstance(observed, datetime):
            observed = observed.date()
        price = round(float(record.price), 2)
        series = prices.setdefault(canonical, {})
        previous = series.get(observed)
        if previous is not None and previous != price:
            conflicts.append((canonical, observed.isoformat(), price, previous))
        series[observed] = price

    rows: list[dict[str, object]] = []
    for item, series in prices.items():
        for observed, price in series.items():
            rows.append(
                {
                    "date": pd.Timestamp(observed),
                    "source": brand,
                    "item": item,
                    "dish_id": make_dish_id(brand, item),
                    "bucket": buckets[item],
                    "price": price,
                }
            )
    history = pd.DataFrame(rows, columns=HISTORY_COLUMNS)
    if not history.empty:
        history = history.sort_values(
            ["source", "bucket", "item", "date"]
        ).reset_index(drop=True)

    matched_items = set(prices)
    missing = tuple(name for name in scrape_names if name not in matched_items)
    unique_pairs = tuple(dict.fromkeys(seen_pairs))
    return ImportReport(
        history=history,
        matched=unique_pairs,
        excluded=tuple(dict.fromkeys(excluded)),
        scrape_without_history=missing,
        conflicts=tuple(conflicts),
    )


def _menu_for_brand(brand: str) -> pd.DataFrame | None:
    """Latest restaurant snapshot for ``brand``, or None when it has not been scraped."""
    from src.history_archive import resolve_snapshot_path
    from src.restaurants.config import load_restaurants

    for restaurant in load_restaurants():
        if restaurant.brand != brand:
            continue
        path = resolve_snapshot_path(restaurant.restaurant_snapshot_path().name)
        if not path.exists():
            return None
        return pd.read_csv(path)
    return None


def import_observed_brand(
    workbook: Path | str,
    brand: str,
    menu: pd.DataFrame,
    *,
    prior_path: Path | None = None,
) -> dict[str, object]:
    """Match one tracker sheet to a scraped menu and replace that brand's prior rows.

    An empty match is left untouched. A bad sheet must not wipe history that is
    already there.
    """
    from src.history_archive import replace_brand_in_prior

    sheet = TRACKER_SHEETS[brand]
    table = read_big_table(
        workbook,
        sheet,
        brand,
        row_brand=row_brand_for(brand),
    )
    report = build_observed_history(table, menu, brand)
    dishes = int(report.history["item"].nunique()) if len(report.history) else 0
    summary: dict[str, object] = {
        "brand": brand,
        "sheet": sheet,
        "dishes": dishes,
        "prices": len(report.history),
        "excluded": len(report.excluded),
    }
    if report.history.empty:
        summary["written"] = False
        return summary
    counts = replace_brand_in_prior(brand, report.history, prior_path)
    summary["written"] = True
    summary.update(counts)
    return summary


def sync_tracker_into_prior(workbook: Path | None = None) -> dict[str, object]:
    """Write every scraped brand's tracker grid into the frozen prior base.

    Brands with no restaurant snapshot are skipped. The workbook is optional:
    a machine without it still stacks the prior file that is already on disk.
    """
    path = workbook or DEFAULT_WORKBOOK_PATH
    if not path.exists():
        return {"workbook": None, "imported": []}
    imported: list[dict[str, object]] = []
    for brand in TRACKER_SHEETS:
        menu = _menu_for_brand(brand)
        if menu is None:
            continue
        imported.append(import_observed_brand(path, brand, menu))
    return {"workbook": str(path), "imported": imported}
