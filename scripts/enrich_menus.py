"""Offline batch enrichment of menu items via OpenAI.

Usage (from the project root)::

    export OPENAI_API_KEY=sk-...
    python -m scripts.enrich_menus                  # classify new dishes only
    python -m scripts.enrich_menus --remap          # reclassify all dishes
    python -m scripts.enrich_menus --limit 10       # cost control during testing
    python -m scripts.enrich_menus --concurrency 16 # tune parallel API calls

Reads unique dishes from ``data/all_company_prices.csv``, compares them against
``data/menu_mappings.db``, and classifies any that are not yet mapped. Writes to
``data/menu_mappings.db``. Classification runs concurrently via asyncio since
each call is network bound. The Streamlit app never calls the API.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
from pathlib import Path
from typing import Callable

import pandas as pd

from src.enrichment import llm
from src.enrichment.llm import DishRequest, DishResult
from src.enrichment.store import DEFAULT_DB_PATH, init_db, list_mapped_keys, upsert_llm_mapping
from src.history_archive import ALL_COMPANY_PRICES_PATH

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_CONCURRENCY = 8

_DISH_COLUMNS = ["dish_id", "source", "name", "bucket", "description"]


def _collect_dishes(prices_path: Path = ALL_COMPANY_PRICES_PATH) -> pd.DataFrame:
    """Unique dishes from the latest row of each ``dish_id`` in all company prices.

    Enrichment keys off the composed price dataset (not raw snapshots) so it
    stays aligned with what the analytics dashboard actually displays.
    """
    path = Path(prices_path)
    if not path.exists():
        return pd.DataFrame(columns=_DISH_COLUMNS)

    prices = pd.read_csv(path, parse_dates=["date"])
    required = {"dish_id", "source", "item", "bucket"}
    if not required.issubset(prices.columns):
        return pd.DataFrame(columns=_DISH_COLUMNS)

    priced = prices.dropna(subset=["dish_id", "source", "item", "bucket"]).copy()
    if priced.empty:
        return pd.DataFrame(columns=_DISH_COLUMNS)

    latest = (
        priced.sort_values("date")
        .groupby("dish_id", as_index=False)
        .agg(
            source=("source", "last"),
            name=("item", "last"),
            bucket=("bucket", "last"),
        )
        .sort_values(["source", "name"])
    )
    latest["description"] = pd.NA
    return latest[_DISH_COLUMNS]


def count_pending_dishes(
    *,
    remap: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
    prices_path: Path = ALL_COMPANY_PRICES_PATH,
) -> dict[str, int]:
    """Report how many dishes would be classified without calling the API.

    Compares unique ``dish_id`` rows in ``all_company_prices.csv`` against keys
    already stored in ``menu_mappings.db``. Returns
    ``{"total": all unique dishes, "pending": those not yet mapped}``.
    """
    dishes = _collect_dishes(prices_path)
    total = len(dishes)
    if total == 0:
        return {"total": 0, "pending": 0}
    mapped = set() if remap else list_mapped_keys(db_path)
    pending = int((~dishes["dish_id"].isin(mapped)).sum())
    return {"total": total, "pending": pending}


def enrich_all(
    *,
    remap: bool = False,
    limit: int | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    db_path: Path = DEFAULT_DB_PATH,
    prices_path: Path = ALL_COMPANY_PRICES_PATH,
    on_progress: Callable[[int, int, DishResult], None] | None = None,
) -> dict[str, object]:
    """Classify menu dishes via OpenAI and upsert mappings into the SQLite DB.

    Shared by the CLI and the Streamlit app. ``on_progress(done, total, result)``
    is invoked after each dish so callers can drive a progress bar. Returns a
    summary dict with ``status`` in ``{"empty", "up_to_date", "done"}``.
    """
    init_db(db_path)
    dishes = _collect_dishes(prices_path)
    if dishes.empty:
        return {"status": "empty", "total": 0, "succeeded": 0, "failed": 0}

    mapped = set() if remap else list_mapped_keys(db_path)
    pending = dishes[~dishes["dish_id"].isin(mapped)]
    if limit is not None:
        pending = pending.head(limit)

    if pending.empty:
        return {"status": "up_to_date", "total": len(dishes), "succeeded": 0, "failed": 0}

    model = llm._model_name()
    total = len(pending)

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
            upsert_llm_mapping(result.mapping, model=model, db_path=db_path)
        else:
            failures.append(result)
        if on_progress is not None:
            on_progress(n, total, result)

    asyncio.run(
        llm.classify_dishes_async(
            requests,
            concurrency=concurrency,
            model=model,
            on_result=_handle,
        )
    )

    return {
        "status": "done",
        "model": model,
        "total": total,
        "succeeded": total - len(failures),
        "failed": len(failures),
        "db_path": str(db_path),
    }


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

    def _report(n: int, total: int, result: DishResult) -> None:
        if result.ok:
            print(
                f"  [{n}/{total}] {result.request.name} -> "
                f"{result.mapping.cuisine}, {result.mapping.dish_type}"
            )
        else:
            print(f"  [{n}/{total}] {result.request.name} -> FAILED: {result.error}")

    summary = enrich_all(
        remap=args.remap,
        limit=args.limit,
        concurrency=args.concurrency,
        db_path=args.db,
        on_progress=_report,
    )

    if summary["status"] == "empty":
        print(
            "No dishes found in all_company_prices.csv. "
            "Run **Update price history** on the Data page first."
        )
        return
    if summary["status"] == "up_to_date":
        print(f"All {summary['total']} dishes already mapped in {args.db}.")
        return

    print(
        f"Done. {summary['succeeded']}/{summary['total']} classified. "
        f"Mappings stored in {args.db}"
    )
    if summary["failed"]:
        print(
            f"{summary['failed']} dish(es) failed; re-run to retry "
            "(already-mapped are skipped)."
        )


if __name__ == "__main__":
    main()
