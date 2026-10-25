"""Tests for dated snapshot archive resolution and persistence."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src import history_archive


@pytest.fixture
def archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    scrapes = data_dir / "scrapes"
    scrapes.mkdir(parents=True)
    monkeypatch.setattr(history_archive, "DATA_DIR", data_dir)
    monkeypatch.setattr(history_archive, "ARCHIVE_DIR", scrapes)
    return data_dir


def test_rebucket_splits_drinks_and_leaves_other_courses() -> None:
    """A spirit leaves Mains. A dessert does not, because this change is the drink split."""
    from src.menu_buckets import ALCOHOLIC_DRINKS, NON_ALCOHOLIC_DRINKS

    frame = pd.DataFrame(
        {
            "source": ["Zizzi", "Prezzo", "Five Guys", "Nando's"],
            "item": ["House Prosecco 125ml", "Tiramisu", "Kids Burger With Soda", "Coke"],
            "bucket": ["Mains", "Mains", "Drinks", "Drinks"],
        }
    )
    lookup = {
        ("Zizzi", "House Prosecco 125ml"): ALCOHOLIC_DRINKS,
        ("Prezzo", "Tiramisu"): "Desserts",
        ("Five Guys", "Kids Burger With Soda"): "Mains",
        ("Nando's", "Coke"): NON_ALCOHOLIC_DRINKS,
    }

    revised = history_archive.rebucket_history(frame, lookup)

    assert revised["bucket"].tolist() == [
        ALCOHOLIC_DRINKS,
        "Mains",
        "Mains",
        NON_ALCOHOLIC_DRINKS,
    ]


def test_resolve_snapshot_path_prefers_newest_archive(archive_root: Path) -> None:
    older = archive_root / "scrapes" / "2026-07-01"
    newer = archive_root / "scrapes" / "2026-07-08"
    older.mkdir()
    newer.mkdir()
    (older / "banana_tree_menu_snapshot.csv").write_text("old\n", encoding="utf-8")
    (newer / "banana_tree_menu_snapshot.csv").write_text("new\n", encoding="utf-8")
    (archive_root / "banana_tree_menu_snapshot.csv").write_text(
        "legacy\n", encoding="utf-8"
    )

    resolved = history_archive.resolve_snapshot_path("banana_tree_menu_snapshot.csv")

    assert resolved == newer / "banana_tree_menu_snapshot.csv"
    assert resolved.read_text(encoding="utf-8") == "new\n"


def test_update_compiles_tracker_before_stacking_scrapes(
    archive_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = archive_root / "scrapes" / "prior" / "price_history.csv"
    live = archive_root / "all_company_prices.csv"
    monkeypatch.setattr(history_archive, "PRIOR_HISTORY_PATH", prior)
    monkeypatch.setattr(history_archive, "ALL_COMPANY_PRICES_PATH", live)
    monkeypatch.setattr(
        history_archive,
        "restaurant_snapshot_filenames",
        lambda: {"rosas_thai_menu_snapshot.csv": "Rosa's Thai"},
    )

    def fake_sync() -> dict[str, object]:
        prior.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            {
                "date": ["2025-03-01"],
                "source": ["Rosa's Thai"],
                "item": ["Sticky Rice"],
                "dish_id": ["rice"],
                "bucket": ["Mains"],
                "price": [4.1],
            }
        ).to_csv(prior, index=False)
        return {"workbook": "tracker.xlsx", "imported": [{"brand": "Rosa's Thai", "written": True}]}

    monkeypatch.setattr("src.pricing_tracker.sync_tracker_into_prior", fake_sync)
    archive = archive_root / "scrapes" / "2026-10-18"
    archive.mkdir()
    pd.DataFrame(
        {"name": ["Sticky Rice"], "category": ["Rice"], "price": [4.4]}
    ).to_csv(archive / "rosas_thai_menu_snapshot.csv", index=False)

    summary = history_archive.update_all_company_prices()

    saved = pd.read_csv(live, parse_dates=["date"])
    rice = saved[saved["item"] == "Sticky Rice"].sort_values("date")
    assert rice["date"].dt.strftime("%Y-%m-%d").tolist() == ["2025-03-01", "2026-10-18"]
    assert summary["tracker"]["imported"][0]["brand"] == "Rosa's Thai"


def test_own_brand_restaurant_scrape_enters_history(
    archive_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = archive_root / "scrapes" / "prior" / "price_history.csv"
    live = archive_root / "all_company_prices.csv"
    monkeypatch.setattr(history_archive, "PRIOR_HISTORY_PATH", prior)
    monkeypatch.setattr(history_archive, "ALL_COMPANY_PRICES_PATH", live)
    monkeypatch.setattr(
        history_archive,
        "restaurant_snapshot_filenames",
        lambda: {"wagamama_menu_snapshot.csv": "Wagamama"},
    )
    archive = archive_root / "scrapes" / "2026-07-08"
    archive.mkdir()
    pd.DataFrame(
        {"name": ["chicken katsu curry"], "category": ["Curry"], "price": [14.9]}
    ).to_csv(archive / "wagamama_menu_snapshot.csv", index=False)

    combined, _summary = history_archive.build_source_of_truth()

    assert combined["source"].tolist() == ["Wagamama"]
    assert combined["price"].tolist() == [14.9]
    assert combined["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-07-08"]


def test_persist_scrape_snapshot_overwrites_same_day_file(archive_root: Path) -> None:
    when = date(2026, 7, 8)
    frame = pd.DataFrame({"name": ["Item A"], "price": [9.95]})

    first = history_archive.persist_scrape_snapshot(
        frame, "banana_tree_menu_snapshot.csv", when=when
    )
    second = history_archive.persist_scrape_snapshot(
        pd.DataFrame({"name": ["Item B"], "price": [12.5]}),
        "banana_tree_menu_snapshot.csv",
        when=when,
    )

    assert first == second
    assert first == archive_root / "scrapes" / "2026-07-08" / "banana_tree_menu_snapshot.csv"
    saved = pd.read_csv(first)
    assert saved["name"].tolist() == ["Item B"]
    assert not (archive_root / "banana_tree_menu_snapshot.csv").exists()
