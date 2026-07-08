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
