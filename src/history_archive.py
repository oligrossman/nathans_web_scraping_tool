"""Date-stamped scrape archive and observed price-history accumulation.

Layout under ``data/scrapes/``::

    prior/price_history.csv      # frozen base imported from the pricing tracker
    prior/observed_sources.txt   # brands imported from the pricing tracker
    <YYYY-MM-DD>/<snapshot>.csv  # raw snapshots captured on that date

:func:`update_all_company_prices` composes ``data/all_company_prices.csv`` (the
dashboard's single source of truth) by stacking the frozen ``prior`` history
with one observation per dated restaurant snapshot. Delete a dated folder and
re-run the update to drop those observations. The ``prior`` base is seeded once
from ``all_company_prices.csv`` so no history is ever lost.

Only restaurant-menu snapshots become price history. Delivery snapshots stay
in the archive for the restaurant-vs-delivery comparison.
"""

from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.enrichment.identity import make_dish_id
from src.menu_buckets import (
    ALCOHOLIC_DRINKS,
    HISTORY_COLUMNS,
    LEGACY_DRINKS_BUCKET,
    NON_ALCOHOLIC_DRINKS,
    assign_buckets,
    keep_legacy_drink,
    migrate_bucket_list,
    resolve_bucket,
)

_DRINK_BUCKETS = {ALCOHOLIC_DRINKS, NON_ALCOHOLIC_DRINKS}
from src.restaurants.config import load_restaurants

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ARCHIVE_DIR = DATA_DIR / "scrapes"
PRIOR_DIR = ARCHIVE_DIR / "prior"
ALL_COMPANY_PRICES_PATH = DATA_DIR / "all_company_prices.csv"
PRIOR_HISTORY_PATH = PRIOR_DIR / "price_history.csv"
OBSERVED_SOURCES_FILENAME = "observed_sources.txt"

_DATE_FMT = "%Y-%m-%d"


def archive_dir_for(when: date | None = None) -> Path:
    """Return the dated archive directory for ``when`` (default: today)."""
    return ARCHIVE_DIR / (when or date.today()).strftime(_DATE_FMT)


def restaurant_snapshot_filenames() -> dict[str, str]:
    """Map each restaurant-menu snapshot filename to its brand."""
    return {
        restaurant.restaurant_snapshot_path(DATA_DIR).name: restaurant.brand
        for restaurant in load_restaurants()
    }


def snapshot_observed_on(path: Path) -> pd.Timestamp | None:
    """Scrape date of a snapshot stored under ``data/scrapes/YYYY-MM-DD/``.

    A legacy file sitting directly in ``data/`` has no archive date and returns
    None. High/low and delivery use this so a chart can say which menu it is.
    """
    try:
        return pd.Timestamp(datetime.strptime(path.parent.name, _DATE_FMT))
    except ValueError:
        return None


def resolve_snapshot_path(filename: str, data_dir: Path | None = None) -> Path:
    """Return the on-disk path for a snapshot file.

    Prefers the newest dated archive under ``data/scrapes/<YYYY-MM-DD>/``. A
    legacy ``data/<filename>`` copy is only used when no archive exists yet.
    """
    root = data_dir or DATA_DIR
    for archive_date in reversed(list_archive_dates()):
        candidate = archive_dir_for(archive_date) / filename
        if candidate.exists():
            return candidate
    return root / filename


def resolve_company_snapshots(
    data_dir: Path | None = None,
) -> dict[str, tuple[Path, Path]]:
    """Resolve restaurant and delivery snapshot paths for every brand."""
    root = data_dir or DATA_DIR
    snapshots: dict[str, tuple[Path, Path]] = {}
    for restaurant in load_restaurants():
        snapshots[restaurant.brand] = (
            resolve_snapshot_path(restaurant.restaurant_snapshot_path(root).name, root),
            resolve_snapshot_path(restaurant.delivery_snapshot_path(root).name, root),
        )
    return snapshots


def archive_snapshot(
    frame: pd.DataFrame, filename: str, *, when: date | None = None
) -> Path:
    """Write one scraped snapshot into the dated archive folder.

    Re-scraping on the same day overwrites the existing file for that date.
    """
    target_dir = archive_dir_for(when)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    frame.to_csv(path, index=False)
    return path


def persist_scrape_snapshot(
    frame: pd.DataFrame, filename: str, *, when: date | None = None
) -> Path:
    """Persist a scrape to the dated archive (the only supported write path)."""
    return archive_snapshot(frame, filename, when=when)


def ensure_prior_seed() -> None:
    """Seed the frozen base from ``all_company_prices.csv``, once."""
    if PRIOR_HISTORY_PATH.exists():
        return
    if ALL_COMPANY_PRICES_PATH.exists():
        PRIOR_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ALL_COMPANY_PRICES_PATH, PRIOR_HISTORY_PATH)


def observed_sources_path(prior_path: Path | None = None) -> Path:
    """Path of the brand list imported from the pricing tracker."""
    base = prior_path or PRIOR_HISTORY_PATH
    return base.parent / OBSERVED_SOURCES_FILENAME


def load_observed_sources(prior_path: Path | None = None) -> set[str]:
    """Return brands imported from the pricing tracker, if any."""
    path = observed_sources_path(prior_path)
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def mark_observed_source(source: str, prior_path: Path | None = None) -> None:
    """Record that ``source`` prior rows were imported from the pricing tracker."""
    path = observed_sources_path(prior_path)
    sources = load_observed_sources(prior_path)
    sources.add(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{name}\n" for name in sorted(sources)),
        encoding="utf-8",
    )


def replace_brand_in_prior(
    source: str,
    rows: pd.DataFrame,
    prior_path: Path | None = None,
) -> dict[str, int]:
    """Replace one brand's rows in the frozen prior base.

    Every other brand is left as it is. ``rows`` uses :data:`HISTORY_COLUMNS`.
    An empty frame is refused so a bad match cannot wipe a brand by accident.
    """
    path = prior_path or PRIOR_HISTORY_PATH
    if rows.empty:
        raise ValueError(f"Refusing to replace {source!r} with an empty history.")
    missing = [column for column in HISTORY_COLUMNS if column not in rows.columns]
    if missing:
        raise ValueError(f"History rows are missing columns: {missing}")
    incoming = rows.loc[:, list(HISTORY_COLUMNS)].copy()
    if not incoming["source"].eq(source).all():
        raise ValueError(f"Every history row must have source {source!r}.")

    if path.exists():
        existing = pd.read_csv(path, parse_dates=["date"])
    else:
        existing = pd.DataFrame(columns=HISTORY_COLUMNS)
    removed = int(existing["source"].eq(source).sum()) if len(existing) else 0
    kept = existing.loc[~existing["source"].eq(source), list(HISTORY_COLUMNS)]
    incoming["date"] = pd.to_datetime(incoming["date"])
    combined = pd.concat([kept, incoming], ignore_index=True)
    combined = combined.sort_values(
        ["source", "bucket", "item", "date"]
    ).reset_index(drop=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(path, index=False)
    mark_observed_source(source, path)
    return {
        "removed": removed,
        "added": len(incoming),
        "kept": len(kept),
    }


def list_archive_dates() -> list[date]:
    """Return the dated archive folders present, oldest first."""
    if not ARCHIVE_DIR.exists():
        return []
    dates: list[date] = []
    for child in ARCHIVE_DIR.iterdir():
        if not child.is_dir() or child.name == "prior":
            continue
        try:
            dates.append(datetime.strptime(child.name, _DATE_FMT).date())
        except ValueError:
            continue
    return sorted(dates)


def delete_archive(when: date) -> bool:
    """Delete a dated archive folder. Returns True if it existed."""
    target_dir = archive_dir_for(when)
    if target_dir.exists():
        shutil.rmtree(target_dir)
        return True
    return False


def _snapshot_to_history(
    frame: pd.DataFrame, source: str, when: date
) -> pd.DataFrame:
    """Convert one raw snapshot into tidy price-history rows for a date."""
    if "price" not in frame.columns or "name" not in frame.columns:
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    priced = frame.dropna(subset=["price", "name"]).copy()
    if priced.empty:
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    priced["bucket"] = assign_buckets(priced)
    return pd.DataFrame(
        {
            "date": pd.Timestamp(when),
            "source": source,
            "item": priced["name"].to_numpy(),
            "dish_id": [make_dish_id(source, name) for name in priced["name"]],
            "bucket": priced["bucket"].to_numpy(),
            "price": priced["price"].astype(float).round(2).to_numpy(),
        }
    )


def buckets_from_latest_menus() -> dict[tuple[str, str], str]:
    """``(brand, dish name) -> bucket`` from each brand's newest restaurant menu.

    Historical prices do not store the menu heading. The current heading is how
    a dish is classified on every date, which is the same rule the pricing
    tracker uses when it writes the prior file.
    """
    lookup: dict[tuple[str, str], str] = {}
    for brand, (restaurant_path, _) in resolve_company_snapshots().items():
        if not restaurant_path.exists():
            continue
        menu = pd.read_csv(restaurant_path)
        if "name" not in menu.columns:
            continue
        buckets = assign_buckets(menu)
        for name, bucket in zip(menu["name"], buckets):
            if pd.isna(name):
                continue
            # A dip can also be listed under Pizza. The first heading is the
            # course for the price series, matching the tracker import.
            lookup.setdefault((brand, str(name)), str(bucket))
    return lookup


def rebucket_history(
    frame: pd.DataFrame,
    lookup: dict[tuple[str, str], str] | None = None,
) -> pd.DataFrame:
    """Apply the current drink split to a tidy history frame.

    Only the drink split is applied. A dish labelled ``Drinks`` is split into
    alcoholic or non-alcoholic (a kids meal or a brownie can leave). A dish
    labelled as food moves only when it is actually a drink, such as a spirit
    that had been left in Mains. Other courses are left as they are.
    """
    if frame.empty or "bucket" not in frame.columns:
        return frame
    buckets = lookup if lookup is not None else buckets_from_latest_menus()
    sources = frame["source"].astype(str)
    items = frame["item"].astype(str)
    current = frame["bucket"].astype(str)
    revised: list[str] = []
    for source, item, bucket in zip(sources, items, current):
        found = buckets.get((source, item))
        if bucket == LEGACY_DRINKS_BUCKET:
            if found is None:
                found = resolve_bucket(LEGACY_DRINKS_BUCKET, item)
            revised.append(keep_legacy_drink(item, found))
            continue
        if found in _DRINK_BUCKETS:
            revised.append(found)
            continue
        revised.append(bucket)
    out = frame.copy()
    out["bucket"] = revised
    return out


def _write_history(path: Path, frame: pd.DataFrame) -> None:
    saved = frame.copy()
    saved["date"] = pd.to_datetime(saved["date"]).dt.strftime(_DATE_FMT)
    path.parent.mkdir(parents=True, exist_ok=True)
    saved.to_csv(path, index=False)


def refresh_recorded_buckets() -> dict[str, int]:
    """Rewrite stored history and saved mappings onto the current buckets.

    The price history and the mapping database both store a bucket on each
    dish. Changing the drink split has to update those copies, or the charts
    keep the old single ``Drinks`` series until the next manual import.
    """
    from src.enrichment.schema import MarketDefinition
    from src.enrichment.store import (
        DEFAULT_DB_PATH,
        list_markets,
        save_market,
    )

    lookup = buckets_from_latest_menus()
    changed = {"history_rows": 0, "prior_rows": 0, "mappings": 0, "markets": 0}

    for path, key in (
        (ALL_COMPANY_PRICES_PATH, "history_rows"),
        (PRIOR_HISTORY_PATH, "prior_rows"),
    ):
        if not path.exists():
            continue
        frame = pd.read_csv(path)
        revised = rebucket_history(frame, lookup)
        changed[key] = int((revised["bucket"] != frame["bucket"].astype(str)).sum())
        if changed[key]:
            _write_history(path, revised)

    if DEFAULT_DB_PATH.exists():
        import sqlite3

        with sqlite3.connect(DEFAULT_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            for table in ("dish_mappings", "dish_overrides"):
                rows = conn.execute(
                    f"SELECT dish_id, source, name, bucket FROM {table}"
                ).fetchall()
                for row in rows:
                    current = row["bucket"]
                    if current is None:
                        continue
                    found = lookup.get((row["source"], row["name"]))
                    if current == LEGACY_DRINKS_BUCKET:
                        if found is None:
                            found = resolve_bucket(LEGACY_DRINKS_BUCKET, row["name"])
                        found = keep_legacy_drink(row["name"], found)
                    elif found not in _DRINK_BUCKETS:
                        continue
                    if found and found != current:
                        conn.execute(
                            f"UPDATE {table} SET bucket = ? WHERE dish_id = ?",
                            (found, row["dish_id"]),
                        )
                        changed["mappings"] += 1

    for market in list_markets():
        migrated = migrate_bucket_list(list(market.buckets))
        if migrated == list(market.buckets):
            continue
        save_market(
            MarketDefinition(
                name=market.name,
                cuisines=list(market.cuisines),
                dish_types=list(market.dish_types),
                companies=list(market.companies),
                buckets=migrated,
            )
        )
        changed["markets"] += 1
    return changed


def _frames_from_latest_snapshots(when: date | None = None) -> list[pd.DataFrame]:
    """Convert legacy canonical snapshots in ``data/`` into history rows.

    Snapshots stored under ``data/scrapes/<date>/`` are handled by the archive
    pass in :func:`build_source_of_truth`; this only picks up older copies that
    were left directly in ``data/``.
    """
    observed = when or date.today()
    frames: list[pd.DataFrame] = []
    for filename, brand in restaurant_snapshot_filenames().items():
        path = resolve_snapshot_path(filename)
        if ARCHIVE_DIR in path.parents or not path.exists():
            continue
        rows = _snapshot_to_history(pd.read_csv(path), brand, observed)
        if not rows.empty:
            frames.append(rows)
    return frames


def build_source_of_truth() -> tuple[pd.DataFrame, dict[str, object]]:
    """Stack prior history, dated archives, and the latest canonical snapshots."""
    ensure_prior_seed()
    filename_to_brand = restaurant_snapshot_filenames()

    frames: list[pd.DataFrame] = []
    if PRIOR_HISTORY_PATH.exists():
        frames.append(pd.read_csv(PRIOR_HISTORY_PATH, parse_dates=["date"]))

    dates_used: list[str] = []
    brands_from_archives: set[str] = set()
    for archive_date in list_archive_dates():
        captured = False
        for path in sorted(archive_dir_for(archive_date).glob("*.csv")):
            brand = filename_to_brand.get(path.name)
            if brand is None:
                continue
            rows = _snapshot_to_history(pd.read_csv(path), brand, archive_date)
            if not rows.empty:
                frames.append(rows)
                brands_from_archives.add(brand)
                captured = True
        if captured:
            dates_used.append(archive_date.strftime(_DATE_FMT))

    brands_from_latest: set[str] = set()
    for snapshot_frame in _frames_from_latest_snapshots():
        brands_from_latest.update(snapshot_frame["source"].unique())
        frames.append(snapshot_frame)

    if not frames:
        combined = pd.DataFrame(columns=HISTORY_COLUMNS)
    else:
        combined = pd.concat(frames, ignore_index=True)
        combined["date"] = pd.to_datetime(combined["date"])
        combined = (
            combined.drop_duplicates(subset=["date", "source", "item"], keep="last")
            .sort_values(["source", "bucket", "item", "date"])
            .reset_index(drop=True)
        )
        # Prior rows were classified when they were imported. Re-apply the
        # current headings so a drink split reaches those historical dates too.
        combined = rebucket_history(combined)

    summary: dict[str, object] = {
        "rows": len(combined),
        "sources": sorted(combined["source"].unique().tolist()) if len(combined) else [],
        "dates": int(combined["date"].nunique()) if len(combined) else 0,
        "archives": dates_used,
        "brands_from_archives": sorted(brands_from_archives),
        "brands_from_latest": sorted(brands_from_latest),
    }
    return combined, summary


def update_all_company_prices() -> dict[str, object]:
    """Recompose ``all_company_prices.csv`` from the tracker, prior base, and archives.

    The pricing-tracker workbook is matched onto each scraped menu and written
    into the prior base first. Dated restaurant snapshots are then stacked on
    those dishes, so one click compiles history and the latest scrape together.
    """
    from src.pricing_tracker import sync_tracker_into_prior

    tracker = sync_tracker_into_prior()
    combined, summary = build_source_of_truth()
    ALL_COMPANY_PRICES_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(ALL_COMPANY_PRICES_PATH, index=False)
    summary["path"] = str(ALL_COMPANY_PRICES_PATH)
    summary["tracker"] = tracker
    return summary
