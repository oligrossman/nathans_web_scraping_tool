"""Compare collection prices against Deliveroo (delivery) prices.

Both menus are scraped snapshots. Deliveroo names don't match the collection
menu character-for-character (e.g. "PERi-PERi Nuts" vs "Peri Peri Nuts"), so we
normalise names to a canonical key and inner-join on it. The result is a
like-for-like, item-level view of the delivery price premium.

Note: this captures the *in-menu* markup Deliveroo applies. Deliveroo's delivery
and service fees are only revealed in the basket at checkout and are not part of
this snapshot.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .synthetic import CATEGORY_TO_BUCKET

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Registry: company -> (collection snapshot, deliveroo snapshot).
# Add new companies here as scrapers and snapshots become available.
COMPANY_SNAPSHOTS: dict[str, tuple[Path, Path]] = {
    "Nando's": (
        _DATA_DIR / "nandos_menu_snapshot.csv",
        _DATA_DIR / "deliveroo_nandos_snapshot.csv",
    ),
}


def normalise_name(name: str) -> str:
    """Reduce a menu item name to a canonical key for matching across sources."""
    text = str(name).lower()
    text = text.replace("peri-peri", "peri").replace("peri peri", "peri")
    text = text.replace("&", "and")
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _build_one_comparison(
    company: str,
    collection_path: Path,
    deliveroo_path: Path,
) -> pd.DataFrame:
    """Match collection vs delivery prices for a single company."""
    collection = pd.read_csv(collection_path)
    deliveroo = pd.read_csv(deliveroo_path)

    collection = collection.dropna(subset=["price"]).copy()
    collection["key"] = collection["name"].map(normalise_name)
    collection["bucket"] = collection["category"].map(CATEGORY_TO_BUCKET)
    collection = collection.sort_values("price").drop_duplicates("key", keep="first")

    deliveroo = deliveroo.dropna(subset=["price"]).copy()
    deliveroo["key"] = deliveroo["name"].map(normalise_name)
    deliveroo = deliveroo.sort_values("price").drop_duplicates("key", keep="first")

    merged = collection.merge(
        deliveroo[["key", "price"]], on="key", suffixes=("_collection", "_delivery")
    )
    out = pd.DataFrame(
        {
            "company": company,
            "item": merged["name"],
            "bucket": merged["bucket"],
            "collection": merged["price_collection"].round(2),
            "delivery": merged["price_delivery"].round(2),
        }
    )
    out["markup_abs"] = (out["delivery"] - out["collection"]).round(2)
    out["markup_pct"] = (out["markup_abs"] / out["collection"] * 100.0).round(1)
    return out


def build_comparison(
    snapshots: dict[str, tuple[Path, Path]] | None = None,
) -> pd.DataFrame:
    """Return the item-level collection-vs-delivery comparison for all companies.

    Columns: ``company``, ``item``, ``bucket``, ``collection``, ``delivery``,
    ``markup_abs``, ``markup_pct``.
    """
    registry = snapshots or COMPANY_SNAPSHOTS
    frames: list[pd.DataFrame] = []
    for company, (collection_path, deliveroo_path) in registry.items():
        if not collection_path.exists() or not deliveroo_path.exists():
            continue
        frames.append(_build_one_comparison(company, collection_path, deliveroo_path))

    if not frames:
        return pd.DataFrame(
            columns=[
                "company",
                "item",
                "bucket",
                "collection",
                "delivery",
                "markup_abs",
                "markup_pct",
            ]
        )

    return (
        pd.concat(frames, ignore_index=True)
        .sort_values(["company", "markup_pct"], ascending=[True, False])
        .reset_index(drop=True)
    )


def company_summary(comparison: pd.DataFrame) -> pd.DataFrame:
    """Average collection vs delivery price (and markup) per company."""
    summary = (
        comparison.groupby("company", as_index=False)
        .agg(
            items=("item", "count"),
            collection=("collection", "mean"),
            delivery=("delivery", "mean"),
        )
        .round(2)
    )
    summary["markup_pct"] = (
        (summary["delivery"] / summary["collection"] - 1.0) * 100.0
    ).round(1)
    return summary.sort_values("markup_pct", ascending=False).reset_index(drop=True)


def bucket_summary(comparison: pd.DataFrame, company: str | None = None) -> pd.DataFrame:
    """Average collection vs delivery price (and markup) per bucket."""
    frame = comparison if company is None else comparison[comparison["company"] == company]
    summary = (
        frame.groupby("bucket", as_index=False)
        .agg(
            items=("item", "count"),
            collection=("collection", "mean"),
            delivery=("delivery", "mean"),
        )
        .round(2)
    )
    summary["markup_pct"] = (
        (summary["delivery"] / summary["collection"] - 1.0) * 100.0
    ).round(1)
    return summary.sort_values("markup_pct", ascending=False).reset_index(drop=True)
