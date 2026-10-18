"""Replace one brand's prior history with the pricing-tracker workbook.

Usage (from the project root)::

    python -m scripts.import_pricing_tracker \\
        --workbook "/path/to/Master - Pricing Tracker.xlsx" \\
        --sheet Nandos \\
        --brand "Nando's"

Only the big dish-by-date table is read. Dish names stay as the scraper spells
them. Tracker rows that are not on the scraped menu are left out. Other brands
in the prior file are not touched.

``--dry-run`` prints the match report and writes nothing.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.history_archive import replace_brand_in_prior, resolve_snapshot_path, update_all_company_prices
from src.pricing_tracker import (
    TRACKER_SHEETS,
    aliases_for,
    build_observed_history,
    normalise_dish_name,
    read_big_table,
)
from src.restaurants.config import load_restaurants

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _snapshot_for(brand: str) -> Path:
    for restaurant in load_restaurants():
        if restaurant.brand == brand:
            filename = restaurant.restaurant_snapshot_path(DATA_DIR).name
            return resolve_snapshot_path(filename, DATA_DIR)
    raise SystemExit(f"No restaurant configured for brand {brand!r}.")


def _print_report(brand: str, report) -> None:
    aliases = aliases_for(brand)
    alias_pairs = [
        (tracker, canonical)
        for tracker, canonical in report.matched
        if normalise_dish_name(tracker) in aliases
    ]
    spelling_pairs = [
        (tracker, canonical)
        for tracker, canonical in report.matched
        if tracker != canonical and normalise_dish_name(tracker) not in aliases
    ]
    history = report.history
    print(f"{brand}: {history['item'].nunique() if len(history) else 0} dishes, {len(history)} prices")
    if len(history):
        print(
            f"Dates {history['date'].min().date().isoformat()} "
            f"to {history['date'].max().date().isoformat()}"
        )
    if alias_pairs:
        print("Explicit aliases (same dish, name differs beyond spelling):")
        for tracker, canonical in alias_pairs:
            print(f"  {tracker}  ->  {canonical}")
    case_only = [
        (tracker, canonical)
        for tracker, canonical in spelling_pairs
        if tracker.casefold() == canonical.casefold()
    ]
    other_spelling = [pair for pair in spelling_pairs if pair not in case_only]
    if other_spelling:
        print("Spelling aligned to the scraper:")
        for tracker, canonical in other_spelling:
            print(f"  {tracker}  ->  {canonical}")
    if case_only:
        print(f"Capitals only: {len(case_only)} dishes (stored under the scraper spelling).")
    if report.conflicts:
        print("Same dish, two prices on one date (later row kept):")
        for item, observed, kept, dropped in report.conflicts:
            print(f"  {item} on {observed}: kept £{kept:.2f}, dropped £{dropped:.2f}")
    print(f"Left out ({len(report.excluded)} tracker dishes not on the scraped menu):")
    for name in report.excluded:
        print(f"  {name}")
    print(f"Scraped dishes with no tracker history ({len(report.scrape_without_history)}):")
    for name in report.scrape_without_history:
        print(f"  {name}")


def _import_one(workbook: Path, brand: str, sheet: str, *, dry_run: bool) -> None:
    snapshot = _snapshot_for(brand)
    if not snapshot.exists():
        raise SystemExit(f"No scraped menu for {brand} at {snapshot}.")
    tracker = read_big_table(workbook, sheet, brand)
    report = build_observed_history(tracker, pd.read_csv(snapshot), brand)
    print(f"\n# {brand}  (sheet {sheet})")
    _print_report(brand, report)
    if dry_run:
        return
    counts = replace_brand_in_prior(brand, report.history)
    print(
        f"Replaced {counts['removed']} prior {brand} rows "
        f"with {counts['added']} observed prices "
        f"({counts['kept']} other-brand rows kept)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--sheet", default=None)
    parser.add_argument("--brand", default=None)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Import every scraped brand in TRACKER_SHEETS except Nando's.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the match report without writing the prior file.",
    )
    args = parser.parse_args()

    if args.all:
        jobs = [(brand, sheet) for brand, sheet in TRACKER_SHEETS.items() if brand != "Nando's"]
    else:
        brand = args.brand or "Nando's"
        sheet = args.sheet or TRACKER_SHEETS.get(brand, "Nandos")
        jobs = [(brand, sheet)]

    for brand, sheet in jobs:
        _import_one(args.workbook, brand, sheet, dry_run=args.dry_run)
    if args.dry_run:
        print("\nDry run: prior history was not changed.")
        return
    summary = update_all_company_prices()
    print(
        f"\nRebuilt {summary['path']} with {summary['rows']} rows "
        f"across {summary['dates']} dates."
    )


if __name__ == "__main__":
    main()
