"""Build the synthetic analytics dataset.

Anchors on the current Nando's menu, then synthesises 5 years of quarterly
price history for Nando's, WagaNath and the wider market.

Usage (from the project root)::

    python -m scripts.generate_data            # reuse cached snapshot if present
    python -m scripts.generate_data --rescrape # force a fresh Nando's scrape

Outputs:
    data/nandos_menu_snapshot.csv  - the anchor menu (current prices)
    data/price_history.csv         - the tidy long price history
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.export import items_to_dataframe
from src.scrapers.deliveroo import DeliverooScraper
from src.scrapers.nandos import NandosScraper
from src.synthetic import generate_price_history, save_history

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SNAPSHOT_PATH = DATA_DIR / "nandos_menu_snapshot.csv"
DELIVEROO_PATH = DATA_DIR / "deliveroo_nandos_snapshot.csv"
HISTORY_PATH = DATA_DIR / "price_history.csv"


def load_menu(*, rescrape: bool) -> pd.DataFrame:
    """Return the anchor menu, scraping only when needed."""
    if SNAPSHOT_PATH.exists() and not rescrape:
        print(f"Using cached menu snapshot: {SNAPSHOT_PATH}")
        return pd.read_csv(SNAPSHOT_PATH)

    print("Scraping live Nando's menu for anchor prices...")
    menu = items_to_dataframe(NandosScraper().scrape())
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    menu.to_csv(SNAPSHOT_PATH, index=False)
    print(f"Saved {len(menu)} items to {SNAPSHOT_PATH}")
    return menu


def scrape_deliveroo(*, rescrape: bool) -> None:
    """Snapshot the live Deliveroo Nando's (Camden) delivery prices."""
    if DELIVEROO_PATH.exists() and not rescrape:
        print(f"Using cached Deliveroo snapshot: {DELIVEROO_PATH}")
        return
    print("Scraping live Deliveroo Nando's (Camden) menu...")
    delivery = items_to_dataframe(DeliverooScraper().scrape())
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    delivery.to_csv(DELIVEROO_PATH, index=False)
    print(f"Saved {len(delivery)} Deliveroo items to {DELIVEROO_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rescrape",
        action="store_true",
        help="Force a fresh scrape even if a cached snapshot exists.",
    )
    args = parser.parse_args()

    menu = load_menu(rescrape=args.rescrape)
    history = generate_price_history(menu)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    save_history(history, str(HISTORY_PATH))
    print(
        f"Wrote {len(history)} rows to {HISTORY_PATH} "
        f"({history['source'].nunique()} sources, "
        f"{history['date'].nunique()} quarters)."
    )

    scrape_deliveroo(rescrape=args.rescrape)


if __name__ == "__main__":
    main()
