"""Build the synthetic analytics dataset and menu snapshots.

Usage (from the project root)::

    python -m scripts.generate_data            # reuse cached snapshots if present
    python -m scripts.generate_data --rescrape # force fresh scrapes

Outputs:
    data/nandos_menu_snapshot.csv
    data/pizza_express_menu_snapshot.csv
    data/deliveroo_nandos_snapshot.csv
    data/deliveroo_pizza_express_snapshot.csv
    data/price_history.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.export import items_to_dataframe
from src.scrapers.deliveroo import DeliverooScraper
from src.scrapers.deliveroo_pizza_express import DeliverooPizzaExpressScraper
from src.scrapers.nandos import NandosScraper
from src.scrapers.pizza_express import PizzaExpressScraper
from src.synthetic import generate_price_history, save_history

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
NANDOS_SNAPSHOT = DATA_DIR / "nandos_menu_snapshot.csv"
PE_SNAPSHOT = DATA_DIR / "pizza_express_menu_snapshot.csv"
NANDOS_DELIVEROO = DATA_DIR / "deliveroo_nandos_snapshot.csv"
PE_DELIVEROO = DATA_DIR / "deliveroo_pizza_express_snapshot.csv"
HISTORY_PATH = DATA_DIR / "price_history.csv"


def _load_or_scrape(
    path: Path,
    label: str,
    scraper,
    *,
    rescrape: bool,
) -> pd.DataFrame:
    if path.exists() and not rescrape:
        print(f"Using cached {label}: {path}")
        return pd.read_csv(path)
    print(f"Scraping {label}...")
    frame = items_to_dataframe(scraper.scrape())
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    print(f"Saved {len(frame)} items to {path}")
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rescrape",
        action="store_true",
        help="Force fresh scrapes even if cached snapshots exist.",
    )
    args = parser.parse_args()

    nandos = _load_or_scrape(
        NANDOS_SNAPSHOT, "Nando's menu", NandosScraper(), rescrape=args.rescrape
    )
    pizza_express = _load_or_scrape(
        PE_SNAPSHOT,
        "Pizza Express menu",
        PizzaExpressScraper(),
        rescrape=args.rescrape,
    )

    history = generate_price_history(nandos, pizza_express_df=pizza_express)
    save_history(history, str(HISTORY_PATH))
    print(
        f"Wrote {len(history)} rows to {HISTORY_PATH} "
        f"({history['source'].nunique()} sources, "
        f"{history['date'].nunique()} quarters)."
    )

    _load_or_scrape(
        NANDOS_DELIVEROO,
        "Deliveroo Nando's",
        DeliverooScraper(),
        rescrape=args.rescrape,
    )
    _load_or_scrape(
        PE_DELIVEROO,
        "Deliveroo Pizza Express",
        DeliverooPizzaExpressScraper(),
        rescrape=args.rescrape,
    )


if __name__ == "__main__":
    main()
