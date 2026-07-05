"""Offline batch enrichment of menu items via OpenAI.

Usage (from the project root)::

    export OPENAI_API_KEY=sk-...
    python -m scripts.enrich_menus                  # classify new dishes only
    python -m scripts.enrich_menus --remap          # reclassify all dishes
    python -m scripts.enrich_menus --limit 10       # cost control during testing
    python -m scripts.enrich_menus --concurrency 16 # tune parallel API calls

Reads collection snapshots + the Wagamama synthetic menu, writes to
``data/menu_mappings.db``. Classification runs concurrently via asyncio since
each call is network bound. The Streamlit app never calls the API.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
from pathlib import Path

import pandas as pd

from src.branding import OWN_BRAND
from src.delivery_compare import COMPANY_SNAPSHOTS
from src.enrichment import llm
from src.enrichment.identity import make_dish_id
from src.enrichment.llm import DishRequest, DishResult
from src.enrichment.store import DEFAULT_DB_PATH, init_db, list_mapped_keys, upsert_llm_mapping
from src.synthetic import WAGAMAMA_MENU

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_CONCURRENCY = 8


def _collect_dishes() -> pd.DataFrame:
    """Unique (restaurant, dish) rows from snapshots and the Wagamama menu.

    Each row carries a ``dish_id`` unique per restaurant, so the same item name
    at two chains is classified (and stored) independently.
    """
    frames: list[pd.DataFrame] = []

    for company, (collection_path, _) in COMPANY_SNAPSHOTS.items():
        if not collection_path.exists():
            continue
        menu = pd.read_csv(collection_path)
        menu = menu.dropna(subset=["name"]).copy()
        menu["source"] = company
        frames.append(menu)

    wagamama = pd.DataFrame(
        [
            {"name": item, "bucket": bucket, "price": price, "source": OWN_BRAND}
            for item, bucket, price in WAGAMAMA_MENU
        ]
    )
    wagamama["category"] = wagamama["bucket"]
    frames.append(wagamama)

    if not frames:
        return pd.DataFrame(columns=["dish_id", "source", "name", "bucket", "description"])

    combined = pd.concat(frames, ignore_index=True)
    combined["bucket"] = combined.get("category", combined.get("bucket"))
    combined = combined.dropna(subset=["name", "bucket"])
    combined["dish_id"] = [
        make_dish_id(source, name)
        for source, name in zip(combined["source"], combined["name"])
    ]

    agg = (
        combined.groupby("dish_id", as_index=False)
        .agg(
            source=("source", "first"),
            name=("name", "first"),
            bucket=("bucket", "first"),
            description=("description", "first"),
        )
        .sort_values(["source", "name"])
    )
    return agg


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--remap",
        action="store_true",
        help="Reclassify all dishes, including those already mapped.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of dishes to classify this run.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=(
            "Maximum simultaneous API calls "
            f"(default: {DEFAULT_CONCURRENCY})."
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite path (default: {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()

    init_db(args.db)
    dishes = _collect_dishes()
    if dishes.empty:
        print("No dishes found. Run `python -m scripts.generate_data` first.")
        return

    mapped = set() if args.remap else list_mapped_keys(args.db)
    pending = dishes[~dishes["dish_id"].isin(mapped)]
    if args.limit is not None:
        pending = pending.head(args.limit)

    if pending.empty:
        print(f"All {len(dishes)} dishes already mapped in {args.db}.")
        return

    model = llm._model_name()
    total = len(pending)
    print(
        f"Classifying {total} dish(es) with model {model!r} "
        f"at concurrency {args.concurrency}..."
    )

    requests = [
        DishRequest(
            dish_id=row["dish_id"],
            source=row["source"],
            name=row["name"],
            bucket=row["bucket"],
            description=None if pd.isna(row["description"]) else str(row["description"]),
        )
        for _, row in pending.iterrows()
    ]

    counter = itertools.count(1)
    failures: list[DishResult] = []

    def _handle(result: DishResult) -> None:
        n = next(counter)
        if result.ok:
            upsert_llm_mapping(result.mapping, model=model, db_path=args.db)
            print(
                f"  [{n}/{total}] {result.request.name} -> "
                f"{result.mapping.cuisine}, {result.mapping.dish_type}"
            )
        else:
            failures.append(result)
            print(f"  [{n}/{total}] {result.request.name} -> FAILED: {result.error}")

    asyncio.run(
        llm.classify_dishes_async(
            requests,
            concurrency=args.concurrency,
            model=model,
            on_result=_handle,
        )
    )

    succeeded = total - len(failures)
    print(f"Done. {succeeded}/{total} classified. Mappings stored in {args.db}")
    if failures:
        print(f"{len(failures)} dish(es) failed; re-run to retry (already-mapped are skipped).")


if __name__ == "__main__":
    main()
