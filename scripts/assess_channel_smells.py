"""Ask the classifier to judge delivery prices that sit below the restaurant price.

Usage (from the project root)::

    export OPENAI_API_KEY=sk-...
    python -m scripts.assess_channel_smells            # new cheaper pairs only
    python -m scripts.assess_channel_smells --remap    # judge them all again
    python -m scripts.assess_channel_smells --limit 5  # cost control

A cheaper delivery price is sent with the other cards of that dish. The model
decides whether the gap is a fair comparison (a can against a bottle, or a real
lower menu price) or a mismatched specification (the delivery price equals a
gluten-free card while the name is the parent dish). Verdicts are stored in
``data/menu_mappings.db``. The Streamlit app reads them and does not call the API.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
from pathlib import Path
from typing import Callable

from src.delivery_compare import SmellCase, collect_smell_cases
from src.enrichment import llm
from src.enrichment.channel_check import SmellAssessment, assess_smells_async
from src.enrichment.store import (
    DEFAULT_DB_PATH,
    init_db,
    load_channel_check_ids,
    upsert_channel_check,
)

DEFAULT_CONCURRENCY = 8


def count_pending_smells(
    *,
    remap: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, int]:
    """How many cheaper pairs would be sent to the classifier."""
    cases = collect_smell_cases()
    total = len(cases)
    if total == 0:
        return {"total": 0, "pending": 0}
    stored = set() if remap else load_channel_check_ids(db_path)
    pending = sum(1 for case in cases if case.check_id not in stored)
    return {"total": total, "pending": pending}


def assess_smells(
    *,
    remap: bool = False,
    limit: int | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    db_path: Path = DEFAULT_DB_PATH,
    on_progress: Callable[[int, int, SmellAssessment], None] | None = None,
) -> dict[str, object]:
    """Judge cheaper delivery pairs and store the verdicts.

    Returns a summary dict with ``status`` in ``{"empty", "up_to_date", "done"}``.
    """
    init_db(db_path)
    cases = collect_smell_cases()
    if not cases:
        return {"status": "empty", "total": 0, "succeeded": 0, "failed": 0}

    stored = set() if remap else load_channel_check_ids(db_path)
    pending = [case for case in cases if case.check_id not in stored]
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        return {
            "status": "up_to_date",
            "total": len(cases),
            "succeeded": 0,
            "failed": 0,
        }

    model = llm._model_name()
    total = len(pending)
    counter = itertools.count(1)
    failures: list[SmellAssessment] = []

    def _handle(result: SmellAssessment) -> None:
        n = next(counter)
        if result.ok:
            _store(result.case, result.verdict or "", result.reason or "", model, db_path)
        else:
            failures.append(result)
        if on_progress is not None:
            on_progress(n, total, result)

    asyncio.run(
        assess_smells_async(
            pending,
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


def _store(
    case: SmellCase,
    verdict: str,
    reason: str,
    model: str,
    db_path: Path,
) -> None:
    upsert_channel_check(
        check_id=case.check_id,
        company=case.company,
        item=case.item,
        restaurant=case.restaurant,
        delivery=case.delivery,
        verdict=verdict,
        reason=reason,
        model=model,
        db_path=db_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--remap",
        action="store_true",
        help="Judge every cheaper pair again, including those already stored.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of pairs to assess this run.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Maximum simultaneous API calls (default: {DEFAULT_CONCURRENCY}).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite path (default: {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()

    def _report(n: int, total: int, result: SmellAssessment) -> None:
        if result.ok:
            print(
                f"  [{n}/{total}] {result.case.company} / {result.case.item} -> "
                f"{result.verdict}: {result.reason}"
            )
        else:
            print(
                f"  [{n}/{total}] {result.case.company} / {result.case.item} -> "
                f"FAILED: {result.error}"
            )

    summary = assess_smells(
        remap=args.remap,
        limit=args.limit,
        concurrency=args.concurrency,
        db_path=args.db,
        on_progress=_report,
    )
    if summary["status"] == "empty":
        print("No delivery prices are below the matched restaurant price.")
        return
    if summary["status"] == "up_to_date":
        print(f"All {summary['total']} cheaper pairs are already assessed in {args.db}.")
        return
    print(
        f"Done. {summary['succeeded']}/{summary['total']} assessed. "
        f"Verdicts stored in {args.db}"
    )
    if summary["failed"]:
        print(
            f"{summary['failed']} pair(s) failed; re-run to retry "
            "(already-assessed pairs are skipped)."
        )


if __name__ == "__main__":
    main()
