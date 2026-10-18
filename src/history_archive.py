"""Date-stamped scrape archive and real price-history accumulation.

Layout under ``data/scrapes/``::

    prior/price_history.csv      # frozen historical base
    prior/observed_sources.txt   # brands whose prior rows are real, not synthetic
    <YYYY-MM-DD>/<snapshot>.csv  # raw snapshots captured on that date

:func:`update_all_company_prices` composes ``data/all_company_prices.csv`` (the
dashboard's single source of truth) by stacking the frozen ``prior`` history
with one real observation per dated snapshot. Delete a dated folder and re-run
the update to drop those observations. The ``prior`` base is seeded once from
``all_company_prices.csv`` so no history is ever lost.

Only restaurant-menu snapshots become price history (delivery snapshots are still
archived for the record). Wagamama stays synthetic, so its scraped snapshot is
archived but not folded into history.
"""

from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.branding import OWN_BRAND
from src.enrichment.identity import make_dish_id
from src.restaurants.config import load_restaurants
from src.synthetic import HISTORY_COLUMNS, resolve_bucket

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
    """Path of the brand list whose prior rows must survive a synthetic rebuild."""
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
    """Record that ``source`` prior rows are observed history, not a backcast."""
    path = observed_sources_path(prior_path)
    sources = load_observed_sources(prior_path)
    sources.add(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{name}\n" for name in sorted(sources)),
        encoding="utf-8",
    )


def keep_observed_brands(
    synthetic: pd.DataFrame, prior_path: Path | None = None
) -> pd.DataFrame:
    """Replace synthetic rows with the prior file's rows for observed brands.

    ``rebuild_price_history`` regenerates every brand from the current menu.
    Brands already imported from the pricing tracker must keep those rows, so a
    later rebuild cannot put the fabricated backcast back.
    """
    path = prior_path or PRIOR_HISTORY_PATH
    observed = load_observed_sources(path)
    if not observed or not path.exists():
        return synthetic
    existing = pd.read_csv(path, parse_dates=["date"])
    real = existing[existing["source"].isin(observed)]
    if real.empty:
        return synthetic
    generated = synthetic[~synthetic["source"].isin(observed)]
    combined = pd.concat(
        [generated, real.reindex(columns=HISTORY_COLUMNS)],
        ignore_index=True,
    )
    return combined.sort_values(["source", "bucket", "item", "date"]).reset_index(
        drop=True
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
    if "category" in priced.columns:
        priced["bucket"] = priced["category"].map(resolve_bucket)
    else:
        priced["bucket"] = "Mains"
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


def _frames_from_latest_snapshots(when: date | None = None) -> list[pd.DataFrame]:
    """Convert legacy canonical snapshots in ``data/`` into history rows.

    Snapshots stored under ``data/scrapes/<date>/`` are handled by the archive
    pass in :func:`build_source_of_truth`; this only picks up older copies that
    were left directly in ``data/``.
    """
    observed = when or date.today()
    frames: list[pd.DataFrame] = []
    for filename, brand in restaurant_snapshot_filenames().items():
        if brand == OWN_BRAND:
            continue
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
            if brand is None or brand == OWN_BRAND:
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
