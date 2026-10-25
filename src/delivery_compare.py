"""Compare each restaurant's latest menu with its latest delivery menu.

Both sides are the newest scraped snapshot for that file, not an average over
the price history. Deliveroo names don't match the restaurant menu
character-for-character (e.g. "PERi-PERi Nuts" vs "Peri Peri Nuts"), so we
normalise names to a canonical key and inner-join on it. The result is a
like-for-like, item-level view of the delivery price premium.

A delivery price below the restaurant price is a smell, not a verdict. This
module records the other cards of that dish and leaves the judgment to the
classifier (``src.enrichment.channel_check``). A stored ``mishap`` verdict is
the only thing that removes a pair from the markup averages. A justified gap,
such as a can on delivery against a bottle in the restaurant, stays in.

Note: this captures the *in-menu* markup Deliveroo applies. Deliveroo's delivery
and service fees are only revealed in the basket at checkout and are not part of
this snapshot.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .menu_buckets import assign_buckets
from src.history_archive import (
    resolve_company_snapshots,
    resolve_snapshot_path,
    snapshot_observed_on,
)
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


# Classifier verdicts. Only ``mishap`` drops a pair from the averages.
LIKE_FOR_LIKE = "like_for_like"
UNASSESSED = "unassessed"
MISHAP = "mishap"
JUSTIFIED = "justified"
UNCERTAIN = "uncertain"

_COMPARISON_COLUMNS = [
    "company",
    "item",
    "bucket",
    "restaurant",
    "delivery",
    "as_of",
    "markup_abs",
    "markup_pct",
    "check_id",
    "assessment",
    "assessment_reason",
    "comparable",
]


def normalise_name(name: str) -> str:
    """Reduce a menu item name to a canonical key for matching across sources."""
    text = str(name).lower()
    text = text.replace("peri-peri", "peri").replace("peri peri", "peri")
    text = text.replace("&", "and")
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def make_check_id(company: str, item: str, restaurant: float, delivery: float) -> str:
    """Stable id for one cheaper pair, including both prices.

    A later scrape with a different price is a new question for the classifier.
    The same prices reuse the stored verdict.
    """
    restaurant = round(float(restaurant), 2)
    delivery = round(float(delivery), 2)
    key = (
        f"{normalise_name(company)}|{normalise_name(item)}|"
        f"{restaurant:.2f}|{delivery:.2f}"
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class SmellCase:
    """One joined pair whose delivery price is below the restaurant price.

    ``restaurant_siblings`` and ``delivery_siblings`` are other cards whose
    name is this dish plus a qualifier (``Gluten-Free Pollo ad Astra`` for
    ``Pollo ad Astra``). ``price_ties`` names the cards priced at the delivery
    figure. Those are facts for the classifier, not a verdict.
    """

    check_id: str
    company: str
    item: str
    bucket: str
    restaurant: float
    delivery: float
    as_of: str
    restaurant_description: str
    delivery_description: str
    restaurant_siblings: tuple[tuple[str, float], ...]
    delivery_siblings: tuple[tuple[str, float], ...]
    price_ties: tuple[str, ...]


def _clean_text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _same_price(left: float, right: float) -> bool:
    return round(float(left), 2) == round(float(right), 2)


def _priced_cards(menu: pd.DataFrame) -> pd.DataFrame:
    frame = menu.dropna(subset=["name", "price"]).copy()
    frame["name"] = frame["name"].astype(str)
    frame["key"] = frame["name"].map(normalise_name)
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce").round(2)
    frame = frame.dropna(subset=["price"])
    frame = frame.loc[frame["key"] != ""].copy()
    if "description" not in frame.columns:
        frame["description"] = ""
    frame["description"] = frame["description"].map(_clean_text)
    return frame


def _sibling_cards(
    cards: pd.DataFrame,
    base_key: str,
    delivery_price: float,
) -> tuple[tuple[str, float], ...]:
    """Other cards whose name ends with this dish, one row per card.

    When any copy of a card is priced at the delivery figure, that is the
    price we show, so an exact tie is not hidden by a cheaper duplicate.
    """
    if not base_key or cards.empty:
        return ()
    suffix = f" {base_key}"
    extra = cards.loc[cards["key"].str.endswith(suffix, na=False)]
    records: list[tuple[str, float]] = []
    for _, group in extra.groupby("key", sort=True):
        prices = [float(price) for price in group["price"]]
        tied = [price for price in prices if _same_price(price, delivery_price)]
        shown = tied[0] if tied else min(prices)
        records.append((str(group.iloc[0]["name"]), round(shown, 2)))
    return tuple(records)


def _price_ties(
    siblings: tuple[tuple[str, float], ...],
    delivery_price: float,
    *,
    menu_label: str,
) -> tuple[str, ...]:
    names = sorted(
        name for name, price in siblings if _same_price(price, delivery_price)
    )
    return tuple(f"{menu_label}: {name}" for name in names)


def _as_of_text(observed: object) -> str:
    if observed is None or pd.isna(observed):
        return ""
    return pd.Timestamp(observed).strftime("%Y-%m-%d")


def evidence_text(case: SmellCase) -> str:
    """Facts the classifier reads. No verdict is stated here."""
    lines = [
        f"Company: {case.company}",
        f"Matched name: {case.item}",
        f"Menu bucket: {case.bucket}",
        f"Restaurant price: £{case.restaurant:.2f}",
        f"Delivery price: £{case.delivery:.2f}",
        f"Gap: £{case.delivery - case.restaurant:.2f}",
    ]
    if case.restaurant_description:
        lines.append(f"Restaurant description: {case.restaurant_description}")
    if case.delivery_description:
        lines.append(f"Delivery description: {case.delivery_description}")
    lines.append(_card_block("Other restaurant cards of this dish", case.restaurant_siblings))
    lines.append(_card_block("Other delivery cards of this dish", case.delivery_siblings))
    if case.price_ties:
        lines.append(
            "Cards whose price equals the delivery price exactly: "
            + "; ".join(case.price_ties)
        )
    else:
        lines.append(
            "No other card on either menu is priced exactly at the delivery price."
        )
    return "\n".join(lines)


def _card_block(title: str, cards: tuple[tuple[str, float], ...]) -> str:
    if not cards:
        return f"{title}: none"
    lines = [f"{title}:"]
    lines.extend(f"- {name} £{price:.2f}" for name, price in cards)
    return "\n".join(lines)


def compare_menus(
    company: str,
    restaurant_menu: pd.DataFrame,
    delivery_menu: pd.DataFrame,
    *,
    observed: object = None,
) -> tuple[pd.DataFrame, list[SmellCase]]:
    """Join one company's menus and list the cheaper delivery pairs.

    The join still keeps the cheapest card when a name is duplicated. Sibling
    cards (a qualifier in front of the same dish) stay available as evidence.
    Nothing here decides that a cheaper price is wrong.
    """
    restaurant_cards = _priced_cards(restaurant_menu)
    delivery_cards = _priced_cards(delivery_menu)
    if restaurant_cards.empty or delivery_cards.empty:
        return pd.DataFrame(columns=_COMPARISON_COLUMNS), []

    restaurant_cards = restaurant_cards.copy()
    restaurant_cards["bucket"] = assign_buckets(restaurant_cards)
    restaurant_join = restaurant_cards.sort_values("price").drop_duplicates(
        "key", keep="first"
    )
    delivery_join = delivery_cards.sort_values("price").drop_duplicates(
        "key", keep="first"
    )
    merged = restaurant_join.merge(
        delivery_join[["key", "price", "description"]],
        on="key",
        suffixes=("_restaurant", "_delivery"),
    )
    as_of = _as_of_text(observed)
    rows: list[dict[str, object]] = []
    smells: list[SmellCase] = []
    for record in merged.itertuples(index=False):
        restaurant_price = round(float(record.price_restaurant), 2)
        delivery_price = round(float(record.price_delivery), 2)
        cheaper = delivery_price < restaurant_price
        check_id = (
            make_check_id(company, record.name, restaurant_price, delivery_price)
            if cheaper
            else ""
        )
        rows.append(
            {
                "company": company,
                "item": record.name,
                "bucket": record.bucket,
                "restaurant": restaurant_price,
                "delivery": delivery_price,
                "as_of": observed,
                "check_id": check_id,
            }
        )
        if not cheaper:
            continue
        restaurant_siblings = _sibling_cards(
            restaurant_cards, record.key, delivery_price
        )
        delivery_siblings = _sibling_cards(delivery_cards, record.key, delivery_price)
        smells.append(
            SmellCase(
                check_id=check_id,
                company=company,
                item=str(record.name),
                bucket=str(record.bucket),
                restaurant=restaurant_price,
                delivery=delivery_price,
                as_of=as_of,
                restaurant_description=_clean_text(record.description_restaurant),
                delivery_description=_clean_text(record.description_delivery),
                restaurant_siblings=restaurant_siblings,
                delivery_siblings=delivery_siblings,
                price_ties=_price_ties(
                    restaurant_siblings, delivery_price, menu_label="restaurant menu"
                )
                + _price_ties(
                    delivery_siblings, delivery_price, menu_label="delivery menu"
                ),
            )
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=_COMPARISON_COLUMNS), []
    out["markup_abs"] = (out["delivery"] - out["restaurant"]).round(2)
    out["markup_pct"] = (out["markup_abs"] / out["restaurant"] * 100.0).round(1)
    out["assessment"] = LIKE_FOR_LIKE
    out.loc[out["delivery"] < out["restaurant"], "assessment"] = UNASSESSED
    out["assessment_reason"] = ""
    out["comparable"] = True
    return out, smells


def _build_one_comparison(
    company: str,
    restaurant_path: Path,
    delivery_path: Path,
) -> pd.DataFrame:
    """Match restaurant-website vs delivery prices for a single company."""
    frame, _smells = compare_menus(
        company,
        pd.read_csv(restaurant_path),
        pd.read_csv(delivery_path),
        observed=snapshot_observed_on(restaurant_path),
    )
    return frame


def collect_smell_cases(
    snapshots: dict[str, tuple[Path, Path]] | None = None,
) -> list[SmellCase]:
    """Every cheaper delivery pair on the latest snapshots, with its evidence."""
    registry = snapshots or resolve_company_snapshots()
    cases: list[SmellCase] = []
    for company, (restaurant_path, delivery_path) in registry.items():
        if not restaurant_path.exists() or not delivery_path.exists():
            continue
        _frame, smells = compare_menus(
            company,
            pd.read_csv(restaurant_path),
            pd.read_csv(delivery_path),
            observed=snapshot_observed_on(restaurant_path),
        )
        cases.extend(smells)
    return cases


def apply_assessments(frame: pd.DataFrame, checks: pd.DataFrame) -> pd.DataFrame:
    """Copy stored classifier verdicts onto cheaper pairs.

    Pairs the classifier has not seen stay ``unassessed`` and comparable, so a
    cheaper price is not dropped before it has been judged. ``mishap`` is the
    verdict that sets ``comparable`` to false.
    """
    out = frame.copy()
    if "assessment" not in out.columns:
        cheaper = out["delivery"] < out["restaurant"]
        out["assessment"] = LIKE_FOR_LIKE
        out.loc[cheaper, "assessment"] = UNASSESSED
        out["assessment_reason"] = ""
        out["comparable"] = True
    if checks is None or checks.empty or "check_id" not in out.columns:
        return out
    slim = (
        checks[["check_id", "verdict", "reason"]]
        .dropna(subset=["check_id"])
        .drop_duplicates("check_id")
    )
    slim = slim.loc[slim["check_id"].astype(str) != ""]
    merged = out.merge(slim, on="check_id", how="left")
    known = merged["verdict"].isin([MISHAP, JUSTIFIED, UNCERTAIN])
    merged.loc[known, "assessment"] = merged.loc[known, "verdict"]
    merged.loc[known, "assessment_reason"] = (
        merged.loc[known, "reason"].fillna("").astype(str)
    )
    merged.loc[known & (merged["verdict"] == MISHAP), "comparable"] = False
    return merged.drop(columns=["verdict", "reason"])


def for_average(comparison: pd.DataFrame) -> pd.DataFrame:
    """Rows that belong in a markup average.

    Missing the column means this frame predates smell checks, so every row
    stays. A ``mishap`` verdict is the only exclusion.
    """
    if comparison.empty or "comparable" not in comparison.columns:
        return comparison
    return comparison.loc[comparison["comparable"].astype(bool)].reset_index(drop=True)


def _item_preview(names: pd.Series) -> str:
    labels = [str(name) for name in names.tolist()]
    shown = ", ".join(labels[:3])
    if len(labels) > 3:
        shown += f", and {len(labels) - 3} more"
    return f" ({shown})" if shown else ""


def assessment_note(frame: pd.DataFrame) -> str:
    """One or more sentences describing how cheaper pairs were treated."""
    if frame is None or frame.empty or "assessment" not in frame.columns:
        return ""
    counts = frame["assessment"].value_counts()

    def _count(label: str) -> int:
        return int(counts.get(label, 0))

    def _pair(n: int) -> str:
        return f"{n} cheaper delivery {'pair' if n == 1 else 'pairs'}"

    def _was(n: int) -> str:
        return "was" if n == 1 else "were"

    parts: list[str] = []
    mishaps = _count(MISHAP)
    if mishaps:
        preview = _item_preview(frame.loc[frame["assessment"] == MISHAP, "item"])
        parts.append(
            f"{_pair(mishaps)} {_was(mishaps)} judged a different specification "
            f"and left out of the charts{preview}."
        )
    justified = _count(JUSTIFIED)
    if justified:
        parts.append(
            f"{_pair(justified)} {_was(justified)} kept: the classifier judged "
            "the lower delivery price a fair comparison."
        )
    uncertain = _count(UNCERTAIN)
    if uncertain:
        parts.append(
            f"{_pair(uncertain)} {_was(uncertain)} kept with an uncertain verdict."
        )
    waiting = _count(UNASSESSED)
    if waiting:
        parts.append(
            f"{_pair(waiting)} {_was(waiting)} not assessed yet. "
            "Use Assess delivery checks on the Data page."
        )
    return " ".join(parts)


def build_comparison(
    snapshots: dict[str, tuple[Path, Path]] | None = None,
    *,
    assessments: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Return the item-level restaurant-vs-delivery comparison for all companies.

    Columns include the prices, the markup, and the classifier fields
    ``assessment``, ``assessment_reason`` and ``comparable``. Pass
    ``assessments`` to avoid reading the mappings database. ``None`` loads
    stored verdicts when that database already exists.
    """
    from src.enrichment.store import DEFAULT_DB_PATH, load_channel_checks

    registry = snapshots or resolve_company_snapshots()
    frames: list[pd.DataFrame] = []
    for company, (restaurant_path, delivery_path) in registry.items():
        if not restaurant_path.exists() or not delivery_path.exists():
            continue
        frame = _build_one_comparison(company, restaurant_path, delivery_path)
        if not frame.empty:
            frames.append(frame)

    if not frames:
        frame = pd.DataFrame(columns=_COMPARISON_COLUMNS)
    else:
        frame = (
            pd.concat(frames, ignore_index=True)
            .sort_values(["company", "markup_pct"], ascending=[True, False])
            .reset_index(drop=True)
        )
    checks = assessments if assessments is not None else load_channel_checks(DEFAULT_DB_PATH)
    judged = apply_assessments(frame, checks)
    return judged.drop(columns=["check_id"], errors="ignore")


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
    always pinned on top regardless of the sort. Pairs the classifier judged a
    mismatched specification are excluded.
    """
    comparison = for_average(comparison)
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
    so the chart order can follow the caller's £/% toggle. Pairs the classifier
    judged a mismatched specification are excluded.
    """
    comparison = for_average(comparison)
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
