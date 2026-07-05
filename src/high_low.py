"""High vs low menu price spread from collection snapshots.

For each aggregation level we report the lowest item price, highest item price,
and the average menu price. Item-level drilldown shows where each item sits
within its bucket's min–max range (low = bucket min, high = bucket max,
average = item price).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .branding import OWN_BRAND
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


def _spread_pct(frame: pd.DataFrame) -> pd.Series:
    """Spread as a percentage of the low price (0 where low is missing/zero)."""
    return (
        (frame["spread"] / frame["low"].replace(0, pd.NA) * 100.0)
        .fillna(0.0)
        .round(1)
    )


def company_summary(
    menu: pd.DataFrame,
    *,
    overall_label: str | None = None,
    overall_exclude: str | None = None,
    sort_by: str = "spread",
) -> pd.DataFrame:
    """Min, max and average menu price per company.

    When ``overall_label`` is set, an extra top row aggregates *every* item in
    the frame (the market average across items, not of per-restaurant averages).
    ``overall_exclude`` drops one company (e.g. the own brand) from that overall
    row only, so the market stays competitor-only while the brand still gets its
    own per-company row. ``sort_by`` picks the ranking column (``"spread"`` or
    ``"spread_pct"``) so the chart order follows the caller's £/% toggle; the
    overall row is always pinned on top.
    """
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
    out["spread_pct"] = _spread_pct(out)
    out = out.sort_values(sort_by, ascending=False).reset_index(drop=True)
    base = menu if not overall_exclude else menu[menu["company"] != overall_exclude]
    if overall_label and not base.empty:
        overall = pd.DataFrame(
            [
                {
                    "company": overall_label,
                    "items": len(base),
                    "low": round(float(base["price"].min()), 2),
                    "high": round(float(base["price"].max()), 2),
                    "average": round(float(base["price"].mean()), 2),
                }
            ]
        )
        overall["spread"] = (overall["high"] - overall["low"]).round(2)
        overall["spread_pct"] = _spread_pct(overall)
        out = pd.concat([overall, out], ignore_index=True)
    return out


def bucket_summary(
    menu: pd.DataFrame, company: str, *, dim: str = "bucket", sort_by: str = "spread"
) -> pd.DataFrame:
    """Min, max and average price per group (``dim``) within a company.

    ``sort_by`` picks the ranking column (``"spread"`` or ``"spread_pct"``) so
    the chart order follows the caller's £/% toggle.
    """
    frame = menu[menu["company"] == company]
    rows = []
    for group_value, group in frame.groupby(dim):
        rows.append(
            {
                dim: group_value,
                "items": len(group),
                "low": group["price"].min(),
                "high": group["price"].max(),
                "average": group["price"].mean(),
            }
        )
    out = pd.DataFrame(rows).round(2)
    out["spread"] = (out["high"] - out["low"]).round(2)
    out["spread_pct"] = _spread_pct(out)
    return out.sort_values(sort_by, ascending=False).reset_index(drop=True)


def item_detail(
    menu: pd.DataFrame,
    company: str | None,
    group_value: str,
    *,
    dim: str = "bucket",
) -> pd.DataFrame:
    """Each item plotted against its group's (``dim``) min–max range.

    ``company=None`` pools every company, so the min–max range spans the whole
    market for that group rather than a single restaurant.
    """
    frame = menu if company is None else menu[menu["company"] == company]
    group_menu = frame[frame[dim] == group_value]
    group_low = group_menu["price"].min()
    group_high = group_menu["price"].max()
    out = group_menu[["item", "price"]].copy()
    if company is None:
        # Pooled across companies: qualify names so identical dishes stay distinct.
        out["item"] = group_menu["item"] + " (" + group_menu["company"] + ")"
    out["low"] = group_low
    out["high"] = group_high
    out["average"] = out["price"]
    out["group_avg"] = round(group_menu["price"].mean(), 2)
    out["spread"] = (out["high"] - out["low"]).round(2)
    # Flag own-brand rows so charts can highlight Wagamama against the field.
    out["is_benchmark"] = (group_menu["company"] == OWN_BRAND).to_numpy()
    return out.sort_values("price", ascending=False).reset_index(drop=True)
