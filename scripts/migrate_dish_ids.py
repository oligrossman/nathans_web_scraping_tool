"""One-off migration: re-key dish mappings from name to (restaurant, dish) id.

Older databases keyed ``dish_mappings``/``dish_overrides`` by ``dish_key``
(the normalised item name), which collides when two restaurants share a name.
This backfills the new ``dish_id`` schema (unique per restaurant) by matching
existing classifications to every (restaurant, item) pair in the price history,
so no OpenAI calls are re-spent.

Usage (from the project root)::

    python -m scripts.migrate_dish_ids
    python -m scripts.migrate_dish_ids --db path/to.db --history path/to.csv

Safe to run more than once: if the DB is already on the new schema it exits
without changes.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

from src.analytics import DEFAULT_HISTORY_PATH
from src.delivery_compare import normalise_name
from src.enrichment.identity import make_dish_id
from src.enrichment.store import DEFAULT_DB_PATH, _utc_now, init_db


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY_PATH)
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"No database at {args.db}; nothing to migrate.")
        return

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    if not _table_exists(conn, "dish_mappings"):
        print("No dish_mappings table; nothing to migrate.")
        conn.close()
        return

    cols = _columns(conn, "dish_mappings")
    if "dish_key" not in cols:
        print("Database already on the dish_id schema; nothing to migrate.")
        conn.close()
        return

    # Read the legacy, name-keyed rows before we drop the tables.
    old_mappings = {
        row["dish_key"]: dict(row)
        for row in conn.execute("SELECT * FROM dish_mappings")
    }
    old_overrides: dict[str, dict] = {}
    if _table_exists(conn, "dish_overrides"):
        old_overrides = {
            row["dish_key"]: dict(row)
            for row in conn.execute("SELECT * FROM dish_overrides")
        }
    print(
        f"Found {len(old_mappings)} legacy mapping(s) and "
        f"{len(old_overrides)} override(s)."
    )

    if not Path(args.history).exists():
        print(f"No price history at {args.history}; regenerate it first.")
        conn.close()
        return

    history = pd.read_csv(args.history)
    pairs = history[["source", "item", "bucket"]].drop_duplicates()

    now = _utc_now()
    new_mappings: list[tuple] = []
    new_overrides: list[tuple] = []
    matched = 0
    for _, pair in pairs.iterrows():
        source, item, bucket = pair["source"], pair["item"], pair["bucket"]
        old_key = normalise_name(item)
        dish_id = make_dish_id(source, item)

        old = old_mappings.get(old_key)
        if old is not None:
            matched += 1
            new_mappings.append(
                (
                    dish_id,
                    source,
                    item,
                    bucket,
                    old["cuisine"],
                    old["dish_type"],
                    old["is_vegetarian"],
                    old["is_vegan"],
                    old.get("model", "migrated"),
                    old.get("created_at", now),
                )
            )

        old_ov = old_overrides.get(old_key)
        if old_ov is not None:
            new_overrides.append(
                (
                    dish_id,
                    source,
                    old_ov.get("name") or item,
                    old_ov.get("bucket"),
                    old_ov.get("cuisine"),
                    old_ov.get("dish_type"),
                    old_ov.get("is_vegetarian"),
                    old_ov.get("is_vegan"),
                    old_ov.get("updated_at", now),
                )
            )

    # Drop legacy tables, recreate on the new schema, then insert backfilled rows.
    conn.execute("DROP TABLE IF EXISTS dish_mappings")
    conn.execute("DROP TABLE IF EXISTS dish_overrides")
    conn.commit()
    conn.close()

    init_db(args.db)

    conn = sqlite3.connect(args.db)
    conn.executemany(
        """
        INSERT OR REPLACE INTO dish_mappings (
            dish_id, source, name, bucket, cuisine, dish_type,
            is_vegetarian, is_vegan, model, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        new_mappings,
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO dish_overrides (
            dish_id, source, name, bucket, cuisine, dish_type,
            is_vegetarian, is_vegan, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        new_overrides,
    )
    conn.commit()
    conn.close()

    print(
        f"Migrated {len(new_mappings)} mapping row(s) "
        f"({matched} matched from {len(old_mappings)} legacy) and "
        f"{len(new_overrides)} override(s) into {args.db}."
    )
    unmatched = len(pairs) - matched
    if unmatched:
        print(
            f"{unmatched} (restaurant, item) pair(s) have no mapping yet; "
            "run `python -m scripts.enrich_menus` to classify them."
        )


if __name__ == "__main__":
    main()
