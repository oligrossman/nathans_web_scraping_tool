"""SQLite persistence for LLM dish mappings, user overrides, and markets."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.menu_buckets import BUCKETS

from .schema import (
    UNMAPPED_LABEL,
    ChannelVerdict,
    Cuisine,
    DishMapping,
    DishType,
    MarketDefinition,
)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "menu_mappings.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Table name -> (DDL, expected column names). The guard rebuilds a table whose
# columns drift from this contract, but only when it is empty (safe to drop).
_TABLE_SCHEMAS: dict[str, tuple[str, tuple[str, ...]]] = {
    "dish_mappings": (
        """
        CREATE TABLE dish_mappings (
            dish_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            name TEXT NOT NULL,
            bucket TEXT NOT NULL,
            cuisine TEXT NOT NULL,
            dish_type TEXT NOT NULL,
            is_vegetarian INTEGER NOT NULL,
            is_vegan INTEGER NOT NULL,
            model TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        (
            "dish_id",
            "source",
            "name",
            "bucket",
            "cuisine",
            "dish_type",
            "is_vegetarian",
            "is_vegan",
            "model",
            "created_at",
        ),
    ),
    "dish_overrides": (
        """
        CREATE TABLE dish_overrides (
            dish_id TEXT PRIMARY KEY,
            source TEXT,
            name TEXT,
            bucket TEXT,
            cuisine TEXT,
            dish_type TEXT,
            is_vegetarian INTEGER,
            is_vegan INTEGER,
            updated_at TEXT NOT NULL
        )
        """,
        (
            "dish_id",
            "source",
            "name",
            "bucket",
            "cuisine",
            "dish_type",
            "is_vegetarian",
            "is_vegan",
            "updated_at",
        ),
    ),
    "markets": (
        """
        CREATE TABLE markets (
            name TEXT PRIMARY KEY,
            definition_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        ("name", "definition_json", "created_at"),
    ),
    "channel_checks": (
        """
        CREATE TABLE channel_checks (
            check_id TEXT PRIMARY KEY,
            company TEXT NOT NULL,
            item TEXT NOT NULL,
            restaurant REAL NOT NULL,
            delivery REAL NOT NULL,
            verdict TEXT NOT NULL,
            reason TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        (
            "check_id",
            "company",
            "item",
            "restaurant",
            "delivery",
            "verdict",
            "reason",
            "model",
            "created_at",
        ),
    ),
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """Create tables, rebuilding any whose schema has drifted (when empty)."""
    with _connect(db_path) as conn:
        for table, (ddl, expected_cols) in _TABLE_SCHEMAS.items():
            if _table_exists(conn, table):
                if _table_columns(conn, table) == expected_cols:
                    continue
                row_count = conn.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                if row_count:
                    raise sqlite3.OperationalError(
                        f"Table {table!r} has an outdated schema and holds "
                        f"{row_count} row(s). Migrate or delete {db_path} and "
                        "re-run `python -m scripts.enrich_menus`."
                    )
                conn.execute(f"DROP TABLE {table}")
            conn.execute(ddl)


def upsert_llm_mapping(
    mapping: DishMapping,
    *,
    model: str,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    """Insert or replace an LLM-generated mapping."""
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO dish_mappings (
                dish_id, source, name, bucket, cuisine, dish_type,
                is_vegetarian, is_vegan, model, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dish_id) DO UPDATE SET
                source = excluded.source,
                name = excluded.name,
                bucket = excluded.bucket,
                cuisine = excluded.cuisine,
                dish_type = excluded.dish_type,
                is_vegetarian = excluded.is_vegetarian,
                is_vegan = excluded.is_vegan,
                model = excluded.model,
                created_at = excluded.created_at
            """,
            (
                mapping.dish_id,
                mapping.source,
                mapping.name,
                mapping.bucket,
                mapping.cuisine,
                mapping.dish_type,
                int(mapping.is_vegetarian),
                int(mapping.is_vegan),
                model,
                _utc_now(),
            ),
        )


def set_override(
    dish_id: str,
    *,
    source: str | None = None,
    name: str | None = None,
    bucket: str | None = None,
    cuisine: str | None = None,
    dish_type: str | None = None,
    is_vegetarian: bool | None = None,
    is_vegan: bool | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    """Upsert partial overrides for one dish."""
    init_db(db_path)
    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT * FROM dish_overrides WHERE dish_id = ?",
            (dish_id,),
        ).fetchone()
        row = dict(existing) if existing else {"dish_id": dish_id}
        if source is not None:
            row["source"] = source
        if name is not None:
            row["name"] = name
        if bucket is not None:
            row["bucket"] = bucket
        if cuisine is not None:
            row["cuisine"] = cuisine
        if dish_type is not None:
            row["dish_type"] = dish_type
        if is_vegetarian is not None:
            row["is_vegetarian"] = int(is_vegetarian)
        if is_vegan is not None:
            row["is_vegan"] = int(is_vegan)
        row["updated_at"] = _utc_now()

        conn.execute(
            """
            INSERT INTO dish_overrides (
                dish_id, source, name, bucket, cuisine, dish_type,
                is_vegetarian, is_vegan, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dish_id) DO UPDATE SET
                source = COALESCE(excluded.source, dish_overrides.source),
                name = COALESCE(excluded.name, dish_overrides.name),
                bucket = COALESCE(excluded.bucket, dish_overrides.bucket),
                cuisine = COALESCE(excluded.cuisine, dish_overrides.cuisine),
                dish_type = COALESCE(excluded.dish_type, dish_overrides.dish_type),
                is_vegetarian = COALESCE(
                    excluded.is_vegetarian, dish_overrides.is_vegetarian
                ),
                is_vegan = COALESCE(excluded.is_vegan, dish_overrides.is_vegan),
                updated_at = excluded.updated_at
            """,
            (
                dish_id,
                row.get("source"),
                row.get("name"),
                row.get("bucket"),
                row.get("cuisine"),
                row.get("dish_type"),
                row.get("is_vegetarian"),
                row.get("is_vegan"),
                row["updated_at"],
            ),
        )


def delete_dish(dish_id: str, db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """Remove one dish's mapping and any override."""
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM dish_overrides WHERE dish_id = ?", (dish_id,))
        conn.execute("DELETE FROM dish_mappings WHERE dish_id = ?", (dish_id,))


def clear_override(dish_id: str, db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """Remove all user overrides for a dish."""
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM dish_overrides WHERE dish_id = ?", (dish_id,))


def list_mapped_keys(db_path: Path | str = DEFAULT_DB_PATH) -> set[str]:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT dish_id FROM dish_mappings").fetchall()
    return {row["dish_id"] for row in rows}


def get_resolved_mappings(db_path: Path | str = DEFAULT_DB_PATH) -> pd.DataFrame:
    """Return one row per dish with overrides applied over LLM values."""
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                m.dish_id,
                COALESCE(o.source, m.source) AS source,
                COALESCE(o.name, m.name) AS name,
                COALESCE(o.bucket, m.bucket) AS bucket,
                COALESCE(o.cuisine, m.cuisine) AS cuisine,
                COALESCE(o.dish_type, m.dish_type) AS dish_type,
                COALESCE(o.is_vegetarian, m.is_vegetarian) AS is_vegetarian,
                COALESCE(o.is_vegan, m.is_vegan) AS is_vegan,
                CASE WHEN o.dish_id IS NOT NULL THEN 1 ELSE 0 END AS is_override
            FROM dish_mappings m
            LEFT JOIN dish_overrides o ON o.dish_id = m.dish_id
            ORDER BY source, name
            """
        ).fetchall()

    columns = [
        "dish_id",
        "source",
        "name",
        "bucket",
        "cuisine",
        "dish_type",
        "is_vegetarian",
        "is_vegan",
        "is_override",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)

    records: list[dict[str, Any]] = []
    for row in rows:
        records.append(
            {
                "dish_id": row["dish_id"],
                "source": row["source"],
                "name": row["name"],
                "bucket": row["bucket"],
                "cuisine": row["cuisine"],
                "dish_type": row["dish_type"],
                "is_vegetarian": bool(row["is_vegetarian"]),
                "is_vegan": bool(row["is_vegan"]),
                "is_override": bool(row["is_override"]),
            }
        )
    return pd.DataFrame(records)


def save_market(
    definition: MarketDefinition,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO markets (name, definition_json, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                definition_json = excluded.definition_json,
                created_at = excluded.created_at
            """,
            (definition.name, json.dumps(definition.to_dict()), _utc_now()),
        )


def list_markets(db_path: Path | str = DEFAULT_DB_PATH) -> list[MarketDefinition]:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT definition_json FROM markets ORDER BY name"
        ).fetchall()
    return [MarketDefinition.from_dict(json.loads(row["definition_json"])) for row in rows]


def delete_market(name: str, db_path: Path | str = DEFAULT_DB_PATH) -> None:
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM markets WHERE name = ?", (name,))


_CHANNEL_CHECK_COLUMNS = ["check_id", "verdict", "reason"]


def load_channel_checks(db_path: Path | str = DEFAULT_DB_PATH) -> pd.DataFrame:
    """Stored smell-check verdicts, or an empty frame when none have been run.

    Reading does not create the database or the table. Chart code can call this
    on every page load without writing.
    """
    empty = pd.DataFrame(columns=_CHANNEL_CHECK_COLUMNS)
    path = Path(db_path)
    if not path.exists():
        return empty
    with _connect(path) as conn:
        if not _table_exists(conn, "channel_checks"):
            return empty
        rows = conn.execute(
            "SELECT check_id, verdict, reason FROM channel_checks"
        ).fetchall()
    if not rows:
        return empty
    return pd.DataFrame([dict(row) for row in rows])


def load_channel_check_ids(db_path: Path | str = DEFAULT_DB_PATH) -> set[str]:
    checks = load_channel_checks(db_path)
    if checks.empty:
        return set()
    return set(checks["check_id"].astype(str))


def upsert_channel_check(
    *,
    check_id: str,
    company: str,
    item: str,
    restaurant: float,
    delivery: float,
    verdict: str,
    reason: str,
    model: str,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    """Insert or replace one classifier verdict for a cheaper delivery pair."""
    allowed = {member.value for member in ChannelVerdict}
    if verdict not in allowed:
        raise ValueError(
            f"Unknown channel verdict {verdict!r}. "
            f"Expected one of {sorted(allowed)}."
        )
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channel_checks (
                check_id, company, item, restaurant, delivery,
                verdict, reason, model, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(check_id) DO UPDATE SET
                company = excluded.company,
                item = excluded.item,
                restaurant = excluded.restaurant,
                delivery = excluded.delivery,
                verdict = excluded.verdict,
                reason = excluded.reason,
                model = excluded.model,
                created_at = excluded.created_at
            """,
            (
                check_id,
                company,
                item,
                restaurant,
                delivery,
                verdict,
                reason,
                model,
                _utc_now(),
            ),
        )


def enum_options() -> dict[str, list[str]]:
    """Dropdown options for the mapping editor."""
    return {
        "cuisine": [member.value for member in Cuisine],
        "dish_type": [member.value for member in DishType],
        "bucket": list(BUCKETS),
    }


def unmapped_label() -> str:
    return UNMAPPED_LABEL
