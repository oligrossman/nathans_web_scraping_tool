"""CSV round-trip for comparison configuration.

The configuration page edits two tables: dish attributes and custom markets.
This module turns each table into a CSV and reads an edited file back.

A row is written only when every key cell is valid. A key cell is the dish
identity (``dish_id``, restaurant, item) or a value that must come from a fixed
list (bucket, cuisine, dish type, vegetarian, vegan, and the same lists on a
market). Invalid rows are reported and left unchanged. Rows absent from a
readable file are removals.

Parsing never touches the database. ``apply_dish_plan`` and ``apply_market_plan``
are the only functions that write.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pandas as pd

from src.enrichment.identity import make_dish_id
from src.enrichment.schema import Cuisine, DishMapping, DishType, MarketDefinition
from src.enrichment.store import (
    delete_dish,
    delete_market,
    get_resolved_mappings,
    list_markets,
    save_market,
    set_override,
    upsert_llm_mapping,
)
from src.menu_buckets import BUCKETS

DISH_COLUMNS = (
    "dish_id",
    "source",
    "name",
    "bucket",
    "cuisine",
    "dish_type",
    "is_vegetarian",
    "is_vegan",
    "is_override",
)
DISH_REQUIRED = DISH_COLUMNS[:-1]
DISH_EDITABLE = (
    "bucket",
    "cuisine",
    "dish_type",
    "is_vegetarian",
    "is_vegan",
)

MARKET_COLUMNS = ("name", "cuisines", "dish_types", "companies", "buckets")

_TRUE = {"1", "true", "yes", "y"}
_FALSE = {"0", "false", "no", "n"}
_CSV_MODEL = "csv-import"
# Excel stores ="6411988093e7" as text. Without that, it reads the id as a number.
_EXCEL_TEXT = re.compile(r'^="(.*)"$')


@dataclass(frozen=True)
class FieldIssue:
    """One invalid cell, using the spreadsheet row number (header is row 1)."""

    row: int
    column: str
    value: str
    message: str

    def as_dict(self) -> dict[str, object]:
        return {
            "row": self.row,
            "column": self.column,
            "value": self.value,
            "message": self.message,
        }


@dataclass
class DishPlan:
    """Changes a dish CSV would make. Nothing is written until it is applied."""

    inserts: list[DishMapping] = field(default_factory=list)
    updates: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    removed: list[tuple[str, str, str]] = field(default_factory=list)
    issues: list[FieldIssue] = field(default_factory=list)
    structural_error: str | None = None

    @property
    def added(self) -> int:
        return len(self.inserts)

    @property
    def updated(self) -> int:
        return len(self.updates)

    @property
    def removed_count(self) -> int:
        return len(self.removed)


@dataclass
class MarketPlan:
    """Changes a market CSV would make."""

    saves: list[MarketDefinition] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    issues: list[FieldIssue] = field(default_factory=list)
    structural_error: str | None = None

    @property
    def added(self) -> int:
        return sum(1 for market in self.saves if market.name in self._new_names)

    # Filled by plan_markets so added and updated stay distinct.
    _new_names: set[str] = field(default_factory=set)

    @property
    def updated(self) -> int:
        return len(self.saves) - self.added

    @property
    def removed_count(self) -> int:
        return len(self.removed)


def export_dishes(frame: pd.DataFrame) -> str:
    """Serialise resolved dish mappings. Booleans are ``true`` / ``false``."""
    out = frame.reindex(columns=list(DISH_COLUMNS)).copy()
    for column in ("is_vegetarian", "is_vegan", "is_override"):
        out[column] = [_export_bool(value) for value in out[column]]
    # A hex id such as 6411988093e7 is a number to Excel (6411988093 × 10^7).
    # The formula forces the cell to stay text, so a save puts the same id back.
    out["dish_id"] = [
        "" if _is_blank(value) else f'="{str(value).strip()}"' for value in out["dish_id"]
    ]
    return out.to_csv(index=False)


def export_markets(markets: list[MarketDefinition]) -> str:
    """Serialise markets. List fields are comma-separated inside one cell."""
    rows = [
        {
            "name": market.name,
            "cuisines": ", ".join(market.cuisines),
            "dish_types": ", ".join(market.dish_types),
            "companies": ", ".join(market.companies),
            "buckets": ", ".join(market.buckets),
        }
        for market in markets
    ]
    return pd.DataFrame(rows, columns=list(MARKET_COLUMNS)).to_csv(index=False)


def plan_dishes(payload: str | bytes, db_path: str) -> DishPlan:
    """Validate a dish CSV and describe the writes, without performing them."""
    frame, error = _read_frame(payload)
    if error is not None:
        return DishPlan(structural_error=error)
    missing = [column for column in DISH_REQUIRED if column not in frame.columns]
    if missing:
        return DishPlan(
            structural_error="Missing columns: " + ", ".join(missing) + "."
        )

    issues = _unknown_column_issues(frame, set(DISH_COLUMNS))
    accepted: list[dict[str, object]] = []
    protected: set[str] = set()
    for index, record in frame.iterrows():
        row_number = int(index) + 2
        parsed, row_issues, protect = _parse_dish_row(record, row_number)
        if row_issues:
            issues.extend(row_issues)
            protected.update(protect)
            continue
        accepted.append(parsed)

    accepted, duplicate_issues = _drop_duplicate_ids(accepted)
    issues.extend(duplicate_issues)
    protected.update(issue.value for issue in duplicate_issues)

    current = get_resolved_mappings(db_path)
    by_id = {str(row["dish_id"]): row for row in current.to_dict(orient="records")}
    # An id we already stored is the dish, even if a spreadsheet rewrote the
    # item text (a trademark sign, a curly apostrophe). A new id still has to
    # match the restaurant and item, otherwise the row is a different dish.
    checked: list[dict[str, object]] = []
    for record in accepted:
        dish_id = _restore_excel_dish_id(str(record["dish_id"]), by_id)
        record["dish_id"] = dish_id
        hashed = make_dish_id(str(record["source"]), str(record["name"]))
        if dish_id != hashed and dish_id not in by_id:
            issues.append(
                FieldIssue(
                    int(record["row"]),
                    "dish_id",
                    dish_id,
                    "dish_id does not match the restaurant and item. "
                    "Leave dish_id blank to calculate it.",
                )
            )
            protected.add(dish_id)
            protected.add(hashed)
            continue
        checked.append(record)

    plan = DishPlan(issues=issues)
    accepted_ids: set[str] = set()
    for record in checked:
        dish_id = str(record["dish_id"])
        accepted_ids.add(dish_id)
        existing = by_id.get(dish_id)
        if existing is None:
            plan.inserts.append(
                DishMapping(
                    dish_id=dish_id,
                    source=str(record["source"]),
                    name=str(record["name"]),
                    bucket=str(record["bucket"]),
                    cuisine=str(record["cuisine"]),
                    dish_type=str(record["dish_type"]),
                    is_vegetarian=bool(record["is_vegetarian"]),
                    is_vegan=bool(record["is_vegan"]),
                )
            )
            continue
        changes = {
            column: record[column]
            for column in DISH_EDITABLE
            if record[column] != existing[column]
        }
        if changes:
            plan.updates.append((dish_id, changes))

    for dish_id, row in by_id.items():
        if dish_id in accepted_ids or dish_id in protected:
            continue
        plan.removed.append((dish_id, str(row["source"]), str(row["name"])))
    return plan


def apply_dish_plan(plan: DishPlan, db_path: str) -> None:
    """Write a dish plan. A file that could not be read writes nothing."""
    if plan.structural_error is not None:
        return
    for mapping in plan.inserts:
        upsert_llm_mapping(mapping, model=_CSV_MODEL, db_path=db_path)
    for dish_id, changes in plan.updates:
        set_override(dish_id, db_path=db_path, **changes)
    for dish_id, _source, _name in plan.removed:
        delete_dish(dish_id, db_path)


def plan_markets(
    payload: str | bytes,
    db_path: str,
    *,
    companies: set[str],
) -> MarketPlan:
    """Validate a market CSV and describe the writes, without performing them."""
    frame, error = _read_frame(payload)
    if error is not None:
        return MarketPlan(structural_error=error)
    missing = [column for column in MARKET_COLUMNS if column not in frame.columns]
    if missing:
        return MarketPlan(
            structural_error="Missing columns: " + ", ".join(missing) + "."
        )

    issues = _unknown_column_issues(frame, set(MARKET_COLUMNS))
    accepted: dict[str, MarketDefinition] = {}
    protected: set[str] = set()
    seen: dict[str, int] = {}
    for index, record in frame.iterrows():
        row_number = int(index) + 2
        market, row_issues = _parse_market_row(record, row_number, companies)
        name = str(record.get("name", "")).strip()
        if row_issues:
            issues.extend(row_issues)
            if name:
                protected.add(name)
            continue
        assert market is not None
        if market.name in seen:
            if market.name in accepted:
                issues.append(
                    FieldIssue(
                        seen[market.name],
                        "name",
                        market.name,
                        "Market name is duplicated in the file.",
                    )
                )
            issues.append(
                FieldIssue(
                    row_number,
                    "name",
                    market.name,
                    "Market name is duplicated in the file.",
                )
            )
            protected.add(market.name)
            accepted.pop(market.name, None)
            continue
        seen[market.name] = row_number
        accepted[market.name] = market

    current = {market.name: market for market in list_markets(db_path)}
    plan = MarketPlan(issues=issues)
    for name, market in accepted.items():
        existing = current.get(name)
        if existing is None:
            plan._new_names.add(name)
            plan.saves.append(market)
        elif existing.to_dict() != market.to_dict():
            plan.saves.append(market)
    for name in current:
        if name not in accepted and name not in protected:
            plan.removed.append(name)
    return plan


def apply_market_plan(plan: MarketPlan, db_path: str) -> None:
    """Write a market plan. A file that could not be read writes nothing."""
    if plan.structural_error is not None:
        return
    for market in plan.saves:
        save_market(market, db_path)
    for name in plan.removed:
        delete_market(name, db_path)


def _read_frame(payload: str | bytes) -> tuple[pd.DataFrame | None, str | None]:
    try:
        if isinstance(payload, bytes):
            text = payload.decode("utf-8-sig")
        else:
            text = payload
        frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    except UnicodeDecodeError:
        return None, "The file is not UTF-8 text."
    except pd.errors.EmptyDataError:
        return None, "The file has no rows."
    except pd.errors.ParserError:
        return None, "The file could not be read as a CSV."
    if frame.empty:
        return None, "The file has no dish or market rows."
    frame.columns = [_normalise_header(column) for column in frame.columns]
    if frame.columns.duplicated().any():
        return None, "The file repeats a column heading."
    return frame, None


def _normalise_header(name: object) -> str:
    return str(name).strip().casefold().replace(" ", "_")


def _unknown_column_issues(frame: pd.DataFrame, known: set[str]) -> list[FieldIssue]:
    return [
        FieldIssue(
            1,
            column,
            "",
            f"Column {column!r} is not part of this file and was ignored.",
        )
        for column in frame.columns
        if column not in known
    ]


def _parse_dish_row(
    record: pd.Series, row_number: int
) -> tuple[dict[str, object] | None, list[FieldIssue], set[str]]:
    issues: list[FieldIssue] = []
    source = str(record.get("source", "")).strip()
    name = str(record.get("name", "")).strip()
    raw_id = _unwrap_dish_id(str(record.get("dish_id", "")))
    if not source:
        issues.append(FieldIssue(row_number, "source", source, "Restaurant is blank."))
    if not name:
        issues.append(FieldIssue(row_number, "name", name, "Item is blank."))

    bucket = _canonical(record.get("bucket", ""), _lookup(BUCKETS))
    if bucket is None:
        issues.append(
            FieldIssue(
                row_number,
                "bucket",
                str(record.get("bucket", "")).strip(),
                "Bucket must be one of: " + ", ".join(BUCKETS) + ".",
            )
        )
    cuisine = _canonical(record.get("cuisine", ""), _lookup(_enum_values(Cuisine)))
    if cuisine is None:
        issues.append(
            FieldIssue(
                row_number,
                "cuisine",
                str(record.get("cuisine", "")).strip(),
                "Cuisine is not a known cuisine.",
            )
        )
    dish_type = _canonical(
        record.get("dish_type", ""), _lookup(_enum_values(DishType))
    )
    if dish_type is None:
        issues.append(
            FieldIssue(
                row_number,
                "dish_type",
                str(record.get("dish_type", "")).strip(),
                "Dish type is not a known dish type.",
            )
        )
    vegetarian = _parse_bool(record.get("is_vegetarian", ""))
    if vegetarian is None:
        issues.append(
            FieldIssue(
                row_number,
                "is_vegetarian",
                str(record.get("is_vegetarian", "")).strip(),
                "Vegetarian must be true or false.",
            )
        )
    vegan = _parse_bool(record.get("is_vegan", ""))
    if vegan is None:
        issues.append(
            FieldIssue(
                row_number,
                "is_vegan",
                str(record.get("is_vegan", "")).strip(),
                "Vegan must be true or false.",
            )
        )

    dish_id = raw_id
    if not dish_id and source and name:
        dish_id = make_dish_id(source, name)

    protect: set[str] = set()
    if raw_id:
        protect.add(raw_id)
    if source and name:
        protect.add(make_dish_id(source, name))
    if issues or bucket is None or cuisine is None or dish_type is None:
        return None, issues, protect
    if vegetarian is None or vegan is None or not dish_id:
        return None, issues, protect
    return (
        {
            "row": row_number,
            "dish_id": dish_id,
            "source": source,
            "name": name,
            "bucket": bucket,
            "cuisine": cuisine,
            "dish_type": dish_type,
            "is_vegetarian": vegetarian,
            "is_vegan": vegan,
        },
        [],
        set(),
    )


def _parse_market_row(
    record: pd.Series, row_number: int, companies: set[str]
) -> tuple[MarketDefinition | None, list[FieldIssue]]:
    issues: list[FieldIssue] = []
    name = str(record.get("name", "")).strip()
    if not name:
        issues.append(FieldIssue(row_number, "name", "", "Market name is blank."))

    cuisines = _tokens(record.get("cuisines", ""), _lookup(_enum_values(Cuisine)))
    dish_types = _tokens(record.get("dish_types", ""), _lookup(_enum_values(DishType)))
    buckets = _tokens(record.get("buckets", ""), _lookup(BUCKETS))
    company_values = _company_tokens(record.get("companies", ""), companies)
    for column, parsed, raw in (
        ("cuisines", cuisines, record.get("cuisines", "")),
        ("dish_types", dish_types, record.get("dish_types", "")),
        ("buckets", buckets, record.get("buckets", "")),
        ("companies", company_values, record.get("companies", "")),
    ):
        if parsed is None:
            issues.append(
                FieldIssue(
                    row_number,
                    column,
                    str(raw).strip(),
                    f"{column} contains a value that is not allowed.",
                )
            )
    if issues or cuisines is None or dish_types is None or buckets is None:
        return None, issues
    if company_values is None or not name:
        return None, issues
    return (
        MarketDefinition(
            name=name,
            cuisines=cuisines,
            dish_types=dish_types,
            companies=company_values,
            buckets=buckets,
        ),
        [],
    )


def _drop_duplicate_ids(
    rows: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[FieldIssue]]:
    counts: dict[str, int] = {}
    for row in rows:
        dish_id = str(row["dish_id"])
        counts[dish_id] = counts.get(dish_id, 0) + 1
    duplicated = {dish_id for dish_id, count in counts.items() if count > 1}
    if not duplicated:
        return rows, []
    issues = [
        FieldIssue(
            int(row["row"]),
            "dish_id",
            str(row["dish_id"]),
            "dish_id is duplicated in the file.",
        )
        for row in rows
        if str(row["dish_id"]) in duplicated
    ]
    kept = [row for row in rows if str(row["dish_id"]) not in duplicated]
    return kept, issues


def _tokens(value: object, allowed: dict[str, str]) -> list[str] | None:
    parts = _split_list(value)
    canonical: list[str] = []
    for part in parts:
        matched = allowed.get(part.casefold())
        if matched is None:
            return None
        canonical.append(matched)
    return canonical


def _company_tokens(value: object, companies: set[str]) -> list[str] | None:
    parts = _split_list(value)
    if any(part not in companies for part in parts):
        return None
    return parts


def _split_list(value: object) -> list[str]:
    text = "" if _is_blank(value) else str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _canonical(value: object, allowed: dict[str, str]) -> str | None:
    if _is_blank(value):
        return None
    return allowed.get(str(value).strip().casefold())


def _lookup(values: tuple[str, ...] | list[str]) -> dict[str, str]:
    return {value.casefold(): value for value in values}


def _enum_values(enum_cls: type) -> tuple[str, ...]:
    return tuple(member.value for member in enum_cls)


def _unwrap_dish_id(value: str) -> str:
    """Undo the text markers a spreadsheet adds around a dish id."""
    text = value.strip().lstrip("\ufeff\u200b")
    if text.startswith(("'", "\t")):
        text = text[1:].strip()
    wrapped = _EXCEL_TEXT.fullmatch(text)
    if wrapped:
        text = wrapped.group(1).strip().lstrip("\ufeff\u200b")
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


_EXCEL_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_MANGLEABLE_ID = re.compile(r"[0-9]+[eE][0-9]+|[0-9]{8,}")


def _restore_excel_dish_id(uploaded: str, known: dict[str, object]) -> str:
    """Turn Excel's scientific notation back into the stored dish id.

    ``6411988093e7`` is the id for Rosa's Thai / Steamed Chicken. Excel reads
    the ``e7`` as × 10^7 and writes ``6.411988093E+16``. The digits still
    identify that one dish.
    """
    if uploaded in known:
        return uploaded
    text = uploaded.strip()
    if not _EXCEL_NUMBER.fullmatch(text):
        return uploaded
    try:
        number = Decimal(text)
    except InvalidOperation:
        return uploaded
    matches = [
        dish_id
        for dish_id in known
        if _MANGLEABLE_ID.fullmatch(dish_id) and _same_number(dish_id, number)
    ]
    if len(matches) == 1:
        return matches[0]
    return uploaded


def _same_number(dish_id: str, number: Decimal) -> bool:
    try:
        return Decimal(dish_id) == number
    except InvalidOperation:
        return False


def _export_bool(value: object) -> str:
    if isinstance(value, bool):
        parsed: bool | None = value
    else:
        parsed = None if _is_blank(value) else _parse_bool(value)
    if parsed is None:
        return ""
    return "true" if parsed else "false"


def _parse_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if _is_blank(value):
        return None
    text = str(value).strip().casefold()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


def _is_blank(value: object) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except TypeError:
        return False
    return str(value).strip() == ""
