"""Compare restaurant-website menu prices against Deliveroo (delivery) prices.

Both menus are scraped snapshots. Deliveroo names don't match the restaurant
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

from .synthetic import resolve_bucket
from src.history_archive import resolve_company_snapshots, resolve_snapshot_path
from src.restaurants.config import load_restaurants

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def build_company_snapshots(data_dir: Path | None = None) -> dict[str, tuple[Path, Path]]:
    """Build restaurant-vs-delivery snapshot paths from ``restaurants.csv``."""
    root = data_dir or _DATA_DIR
    snapshots: dict[str, tuple[Path, Path]] = {}
    for restaurant in load_restaurants():
        snapshots[restaurant.brand] = (
            resolve_snapshot_path(restaurant.restaurant_snapshot_path(root).name, root),
            resolve_snapshot_path(restaurant.delivery_snapshot_path(root).name, root),
        )
    return snapshots


# Registry: company -> (restaurant snapshot, delivery snapshot).
COMPANY_SNAPSHOTS: dict[str, tuple[Path, Path]] = build_company_snapshots()


def normalise_name(name: str) -> str:
    """Reduce a menu item name to a canonical key for matching across sources."""
    text = str(name).lower()
    text = text.replace("peri-peri", "peri").replace("peri peri", "peri")
    text = text.replace("&", "and")
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _build_one_comparison(
    company: str,
    restaurant_path: Path,
    delivery_path: Path,
) -> pd.DataFrame:
    """Match restaurant-website vs delivery prices for a single company."""
    restaurant_menu = pd.read_csv(restaurant_path)
    delivery_menu = pd.read_csv(delivery_path)

    restaurant_menu = restaurant_menu.dropna(subset=["price"]).copy()
    restaurant_menu["key"] = restaurant_menu["name"].map(normalise_name)
    if "category" in restaurant_menu.columns:
        restaurant_menu["bucket"] = restaurant_menu["category"].map(resolve_bucket)
    else:
        restaurant_menu["bucket"] = "Mains"
    restaurant_menu = restaurant_menu.sort_values("price").drop_duplicates(
        "key", keep="first"
    )

    delivery_menu = delivery_menu.dropna(subset=["price"]).copy()
    delivery_menu["key"] = delivery_menu["name"].map(normalise_name)
    delivery_menu = delivery_menu.sort_values("price").drop_duplicates(
        "key", keep="first"
    )

    merged = restaurant_menu.merge(
        delivery_menu[["key", "price"]], on="key", suffixes=("_restaurant", "_delivery")
    )
    out = pd.DataFrame(
        {
            "company": company,
            "item": merged["name"],
            "bucket": merged["bucket"],
            "restaurant": merged["price_restaurant"].round(2),
            "delivery": merged["price_delivery"].round(2),
        }
    )
    out["markup_abs"] = (out["delivery"] - out["restaurant"]).round(2)
    out["markup_pct"] = (out["markup_abs"] / out["restaurant"] * 100.0).round(1)
    return out


def build_comparison(
    snapshots: dict[str, tuple[Path, Path]] | None = None,
) -> pd.DataFrame:
    """Return the item-level restaurant-vs-delivery comparison for all companies.

    Columns: ``company``, ``item``, ``bucket``, ``restaurant``, ``delivery``,
    ``markup_abs``, ``markup_pct``.
    """
    registry = snapshots or resolve_company_snapshots()
    frames: list[pd.DataFrame] = []
    for company, (restaurant_path, delivery_path) in registry.items():
        if not restaurant_path.exists() or not delivery_path.exists():
            continue
        frames.append(_build_one_comparison(company, restaurant_path, delivery_path))

    if not frames:
        return pd.DataFrame(
            columns=[
                "company",
                "item",
                "bucket",
                "restaurant",
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


def company_summary(
    comparison: pd.DataFrame,
    *,
    overall_label: str | None = None,
    overall_exclude: str | None = None,
    sort_by: str = "markup_pct",
) -> pd.DataFrame:
    """Average restaurant vs delivery price (and markup) per company.

    When ``overall_label`` is set, an extra top row aggregates *every* item in
    the frame (the market average, not an average of per-restaurant averages).
    ``overall_exclude`` drops one company (e.g. the own brand) from that overall
    row only, keeping the market competitor-only while the brand still gets a row.
    ``sort_by`` picks the ranking column (``"markup_pct"`` or ``"markup_abs"``)
    so the chart order can follow the caller's £/% toggle. The overall row is
    always pinned on top regardless of the sort.
    """
    summary = (
        comparison.groupby("company", as_index=False)
        .agg(
            items=("item", "count"),
            restaurant=("restaurant", "mean"),
            delivery=("delivery", "mean"),
        )
        .round(2)
    )
    summary["markup_abs"] = (summary["delivery"] - summary["restaurant"]).round(2)
    summary["markup_pct"] = (
        (summary["delivery"] / summary["restaurant"] - 1.0) * 100.0
    ).round(1)
    out = summary.sort_values(sort_by, ascending=False).reset_index(drop=True)
    base = (
        comparison
        if not overall_exclude
        else comparison[comparison["company"] != overall_exclude]
    )
    if overall_label and not base.empty:
        restaurant_avg = round(float(base["restaurant"].mean()), 2)
        delivery_avg = round(float(base["delivery"].mean()), 2)
        overall = pd.DataFrame(
            [
                {
                    "company": overall_label,
                    "items": len(base),
                    "restaurant": restaurant_avg,
                    "delivery": delivery_avg,
                    "markup_abs": round(delivery_avg - restaurant_avg, 2),
                    "markup_pct": round(
                        (delivery_avg / restaurant_avg - 1.0) * 100.0, 1
                    ),
                }
            ]
        )
        out = pd.concat([overall, out], ignore_index=True)
    return out


def bucket_summary(
    comparison: pd.DataFrame,
    company: str | None = None,
    *,
    dim: str = "bucket",
    sort_by: str = "markup_pct",
) -> pd.DataFrame:
    """Average restaurant vs delivery price (and markup) per group (``dim``).

    ``sort_by`` picks the ranking column (``"markup_pct"`` or ``"markup_abs"``)
    so the chart order can follow the caller's £/% toggle.
    """
    frame = comparison if company is None else comparison[comparison["company"] == company]
    summary = (
        frame.groupby(dim, as_index=False)
        .agg(
            items=("item", "count"),
            restaurant=("restaurant", "mean"),
            delivery=("delivery", "mean"),
        )
        .round(2)
    )
    summary["markup_abs"] = (summary["delivery"] - summary["restaurant"]).round(2)
    summary["markup_pct"] = (
        (summary["delivery"] / summary["restaurant"] - 1.0) * 100.0
    ).round(1)
    return summary.sort_values(sort_by, ascending=False).reset_index(drop=True)
