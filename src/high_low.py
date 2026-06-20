"""High vs low menu price spread from collection snapshots.

For each aggregation level we report the lowest item price, highest item price,
and the average menu price. Item-level drilldown shows where each item sits
within its bucket's min–max range (low = bucket min, high = bucket max,
average = item price).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .delivery_compare import COMPANY_SNAPSHOTS
from .synthetic import CATEGORY_TO_BUCKET

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_company_menu(company: str, collection_path: Path) -> pd.DataFrame:
    menu = pd.read_csv(collection_path).dropna(subset=["price"]).copy()
    menu["company"] = company
    menu["bucket"] = menu["category"].map(CATEGORY_TO_BUCKET)
    menu = menu.dropna(subset=["bucket"])
    return menu.rename(columns={"name": "item"})


def build_menu_prices(
    snapshots: dict[str, tuple[Path, Path]] | None = None,
) -> pd.DataFrame:
    """Item-level menu prices from collection snapshots.

    Columns: ``company``, ``item``, ``bucket``, ``price``.
    """
    registry = snapshots or COMPANY_SNAPSHOTS
    frames: list[pd.DataFrame] = []
    for company, (collection_path, _) in registry.items():
        if not collection_path.exists():
            continue
        frames.append(_load_company_menu(company, collection_path))
    if not frames:
        return pd.DataFrame(columns=["company", "item", "bucket", "price"])
    return pd.concat(frames, ignore_index=True)


def company_summary(menu: pd.DataFrame) -> pd.DataFrame:
    """Min, max and average menu price per company."""
    rows = []
    for company, group in menu.groupby("company"):
        rows.append(
            {
                "company": company,
                "items": len(group),
                "low": group["price"].min(),
                "high": group["price"].max(),
                "average": group["price"].mean(),
            }
        )
    out = pd.DataFrame(rows).round(2)
    out["spread"] = (out["high"] - out["low"]).round(2)
    return out.sort_values("spread", ascending=False).reset_index(drop=True)


def bucket_summary(menu: pd.DataFrame, company: str) -> pd.DataFrame:
    """Min, max and average price per bucket within a company."""
    frame = menu[menu["company"] == company]
    rows = []
    for bucket, group in frame.groupby("bucket"):
        rows.append(
            {
                "bucket": bucket,
                "items": len(group),
                "low": group["price"].min(),
                "high": group["price"].max(),
                "average": group["price"].mean(),
            }
        )
    out = pd.DataFrame(rows).round(2)
    out["spread"] = (out["high"] - out["low"]).round(2)
    return out.sort_values("spread", ascending=False).reset_index(drop=True)


def item_detail(menu: pd.DataFrame, company: str, bucket: str) -> pd.DataFrame:
    """Each item plotted against its bucket's min–max range."""
    bucket_menu = menu[(menu["company"] == company) & (menu["bucket"] == bucket)]
    bucket_low = bucket_menu["price"].min()
    bucket_high = bucket_menu["price"].max()
    out = bucket_menu[["item", "price"]].copy()
    out["low"] = bucket_low
    out["high"] = bucket_high
    out["average"] = out["price"]
    out["spread"] = (out["high"] - out["low"]).round(2)
    return out.sort_values("price", ascending=False).reset_index(drop=True)
