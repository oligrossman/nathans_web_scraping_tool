"""Shared analytics-build steps used by both the CLI scripts and the app.

The scrape step writes dated snapshots under ``data/scrapes/<YYYY-MM-DD>/``. This
module turns those snapshots into the downstream artifacts the dashboard reads:

- The synthetic *prior base* (``data/scrapes/prior/price_history.csv``) via
  :func:`rebuild_price_history`, which then recomposes the live
  ``data/all_company_prices.csv`` from that base plus any dated real archives.
- ``data/menu_mappings.db`` via the enrichment entry point (see
  ``scripts.enrich_menus.enrich_all``)

Day-to-day, scraping archives dated snapshots and the **Update price history**
button (``src.history_archive.update_all_company_prices``) folds them in. Regenerating
the synthetic base is the rarer operation handled here.

Keeping the logic here means the Streamlit buttons and the command line run the
exact same code path, so results never diverge between the two.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.branding import OWN_BRAND
from src.history_archive import (
    ALL_COMPANY_PRICES_PATH,
    PRIOR_HISTORY_PATH,
    resolve_snapshot_path,
    update_all_company_prices,
)
from src.restaurants.config import load_restaurants
from src.synthetic import generate_price_history, save_history

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_menu_snapshots(data_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    """Return ``{brand: menu_df}`` for every competitor snapshot on disk.

    The own brand (Wagamama) is skipped because its price history is synthesised
    from ``WAGAMAMA_MENU`` rather than a scraped snapshot.
    """
    root = data_dir or DATA_DIR
    menus: dict[str, pd.DataFrame] = {}
    for restaurant in load_restaurants():
        if restaurant.brand == OWN_BRAND:
            continue
        snapshot = resolve_snapshot_path(
            restaurant.restaurant_snapshot_path(root).name, root
        )
        if not snapshot.exists():
            continue
        menus[restaurant.brand] = pd.read_csv(snapshot)
    return menus


def rebuild_price_history(
    data_dir: Path | None = None,
    prior_path: Path | None = None,
) -> dict[str, object]:
    """Regenerate the synthetic *prior base* then recompose the live history.

    Writes the deterministic synthetic backcast to the frozen prior base
    (``data/scrapes/prior/price_history.csv``) from the current restaurant
    snapshots, then folds in any dated real archives via
    :func:`src.history_archive.update_all_company_prices`.
    """
    root = data_dir or DATA_DIR
    base_target = prior_path or PRIOR_HISTORY_PATH

    menus = load_menu_snapshots(root)
    history = generate_price_history(menus)
    base_target.parent.mkdir(parents=True, exist_ok=True)
    save_history(history, str(base_target))

    return update_all_company_prices()
