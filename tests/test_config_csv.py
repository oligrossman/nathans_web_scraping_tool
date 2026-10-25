"""CSV export and import for dish mappings and custom markets."""

from pathlib import Path

import pandas as pd

from src.enrichment.config_csv import (
    apply_dish_plan,
    apply_market_plan,
    export_dishes,
    export_markets,
    plan_dishes,
    plan_markets,
)
from src.enrichment.identity import make_dish_id
from src.enrichment.schema import DishMapping, DishType, MarketDefinition
from src.enrichment.store import (
    get_resolved_mappings,
    init_db,
    list_markets,
    save_market,
    upsert_llm_mapping,
)


def _mapping(source: str, name: str, *, cuisine: str = "Thai") -> DishMapping:
    return DishMapping(
        dish_id=make_dish_id(source, name),
        source=source,
        name=name,
        bucket="Mains",
        cuisine=cuisine,
        dish_type=DishType.NOODLES.value,
        is_vegetarian=False,
        is_vegan=False,
    )


def _seed(db_path: Path) -> DishMapping:
    init_db(db_path)
    mapping = _mapping("Wagamama", "Pad Thai")
    upsert_llm_mapping(mapping, model="test-model", db_path=db_path)
    return mapping


def test_round_trip_changes_nothing(tmp_path: Path) -> None:
    db_path = tmp_path / "mappings.db"
    _seed(db_path)
    exported = export_dishes(get_resolved_mappings(db_path))

    plan = plan_dishes(exported, str(db_path))

    assert plan.structural_error is None
    assert plan.issues == []
    assert plan.added == 0
    assert plan.updated == 0
    assert plan.removed_count == 0


def test_invalid_key_fields_are_warned_and_left_unchanged(tmp_path: Path) -> None:
    db_path = tmp_path / "mappings.db"
    mapping = _seed(db_path)
    exported = export_dishes(get_resolved_mappings(db_path))
    broken = exported.replace(",Thai,", ",Korean,")
    broken = broken.replace(",Noodles,", ",Snack,")

    plan = plan_dishes(broken, str(db_path))

    columns = {issue.column for issue in plan.issues}
    assert columns == {"cuisine", "dish_type"}
    assert plan.updated == 0
    assert plan.removed_count == 0
    apply_dish_plan(plan, str(db_path))
    saved = get_resolved_mappings(db_path).iloc[0]
    assert saved["dish_id"] == mapping.dish_id
    assert saved["cuisine"] == "Thai"
    assert saved["dish_type"] == "Noodles"


def test_import_adds_updates_and_removes(tmp_path: Path) -> None:
    db_path = tmp_path / "mappings.db"
    kept = _seed(db_path)
    extra = _mapping("Nando's", "Chips", cuisine="British")
    upsert_llm_mapping(extra, model="test-model", db_path=db_path)

    added = _mapping("Pizza Express", "Margherita", cuisine="Italian")
    added.dish_type = DishType.PIZZA.value
    added.bucket = "Mains"
    frame = get_resolved_mappings(db_path)
    frame = frame[frame["dish_id"] != extra.dish_id]
    frame.loc[frame["dish_id"] == kept.dish_id, "cuisine"] = "PanAsian"
    frame.loc[frame["dish_id"] == kept.dish_id, "is_vegetarian"] = True
    new_row = {
        "dish_id": "",
        "source": added.source,
        "name": added.name,
        "bucket": added.bucket,
        "cuisine": added.cuisine,
        "dish_type": added.dish_type,
        "is_vegetarian": False,
        "is_vegan": False,
        "is_override": False,
    }
    frame = pd.concat([frame, pd.DataFrame([new_row])], ignore_index=True)

    plan = plan_dishes(export_dishes(frame), str(db_path))
    assert plan.issues == []
    assert plan.added == 1
    assert plan.updated == 1
    assert [name for _dish_id, _source, name in plan.removed] == ["Chips"]

    apply_dish_plan(plan, str(db_path))
    saved = get_resolved_mappings(db_path)
    assert set(saved["name"]) == {"Pad Thai", "Margherita"}
    pad_thai = saved.loc[saved["name"] == "Pad Thai"].iloc[0]
    assert pad_thai["cuisine"] == "PanAsian"
    assert bool(pad_thai["is_vegetarian"]) is True
    assert bool(pad_thai["is_override"]) is True
    margherita = saved.loc[saved["name"] == "Margherita"].iloc[0]
    assert margherita["dish_id"] == added.dish_id
    assert bool(margherita["is_override"]) is False


def test_mismatched_dish_id_is_not_imported(tmp_path: Path) -> None:
    db_path = tmp_path / "mappings.db"
    mapping = _seed(db_path)
    text = export_dishes(get_resolved_mappings(db_path)).replace(
        mapping.dish_id, "not-the-id"
    )

    plan = plan_dishes(text, str(db_path))

    assert any(issue.column == "dish_id" for issue in plan.issues)
    assert plan.removed_count == 0
    assert plan.updated == 0


def test_missing_column_does_not_delete(tmp_path: Path) -> None:
    db_path = tmp_path / "mappings.db"
    _seed(db_path)

    plan = plan_dishes("dish_id,source,name\nabc,Wagamama,Pad Thai\n", str(db_path))

    assert plan.structural_error is not None
    apply_dish_plan(plan, str(db_path))
    assert len(get_resolved_mappings(db_path)) == 1


def test_empty_file_does_not_delete(tmp_path: Path) -> None:
    db_path = tmp_path / "mappings.db"
    _seed(db_path)

    plan = plan_dishes(
        "dish_id,source,name,bucket,cuisine,dish_type,is_vegetarian,is_vegan\n",
        str(db_path),
    )

    assert plan.structural_error is not None
    apply_dish_plan(plan, str(db_path))
    assert len(get_resolved_mappings(db_path)) == 1


def test_saved_dish_id_survives_a_rewritten_item_name(tmp_path: Path) -> None:
    db_path = tmp_path / "mappings.db"
    mapping = _seed(db_path)
    text = (
        "dish_id,source,name,bucket,cuisine,dish_type,is_vegetarian,is_vegan\n"
        f"{mapping.dish_id},Wagamama,Pad Thai rewritten,Mains,PanAsian,Noodles,false,false\n"
    )

    plan = plan_dishes(text, str(db_path))

    assert plan.issues == []
    assert plan.updated == 1
    assert plan.removed_count == 0
    apply_dish_plan(plan, str(db_path))
    saved = get_resolved_mappings(db_path).iloc[0]
    assert saved["name"] == "Pad Thai"
    assert saved["cuisine"] == "PanAsian"


def test_excel_scientific_notation_restores_the_stored_id(tmp_path: Path) -> None:
    db_path = tmp_path / "mappings.db"
    init_db(db_path)
    mapping = _mapping("Rosa's Thai", "Steamed Chicken", cuisine="Thai")
    assert mapping.dish_id == "6411988093e7"
    upsert_llm_mapping(mapping, model="test-model", db_path=db_path)
    text = (
        "dish_id,source,name,bucket,cuisine,dish_type,is_vegetarian,is_vegan\n"
        "6.411988093E+16,Rosa's Thai,Steamed Chicken,Mains,Thai,Noodles,false,false\n"
    )

    plan = plan_dishes(text, str(db_path))

    assert plan.issues == []
    assert plan.removed_count == 0
    assert plan.added == 0


def test_export_writes_dish_id_as_spreadsheet_text(tmp_path: Path) -> None:
    db_path = tmp_path / "mappings.db"
    mapping = _seed(db_path)

    exported = export_dishes(get_resolved_mappings(db_path))

    assert f'"{mapping.dish_id}"' in exported
    plan = plan_dishes(exported, str(db_path))
    assert plan.issues == []
    assert plan.updated == 0


def test_duplicate_dish_id_is_left_unchanged(tmp_path: Path) -> None:
    db_path = tmp_path / "mappings.db"
    mapping = _seed(db_path)
    line = (
        f"{mapping.dish_id},Wagamama,Pad Thai,Mains,Japanese,Curry,false,false\n"
    )

    plan = plan_dishes(
        "dish_id,source,name,bucket,cuisine,dish_type,is_vegetarian,is_vegan\n"
        + line
        + line,
        str(db_path),
    )

    assert plan.updated == 0
    assert plan.removed_count == 0
    assert sum(issue.column == "dish_id" for issue in plan.issues) == 2


def test_market_round_trip_and_invalid_company(tmp_path: Path) -> None:
    db_path = tmp_path / "mappings.db"
    init_db(db_path)
    save_market(
        MarketDefinition(name="Grill dishes", dish_types=["Grill"], buckets=["Mains"]),
        db_path,
    )
    exported = export_markets(list_markets(db_path))
    same = plan_markets(exported, str(db_path), companies={"Nando's"})
    assert same.issues == []
    assert same.added == 0
    assert same.updated == 0
    assert same.removed_count == 0

    edited = exported.replace(",Grill,", ",Snack,")
    plan = plan_markets(edited, str(db_path), companies={"Nando's"})
    assert any(issue.column == "dish_types" for issue in plan.issues)
    assert plan.removed_count == 0

    added = (
        "name,cuisines,dish_types,companies,buckets\n"
        "Asian noodles,Thai,Noodles,Nando's,Mains\n"
    )
    plan = plan_markets(added, str(db_path), companies={"Nando's"})
    assert plan.issues == []
    assert plan.added == 1
    assert plan.removed == ["Grill dishes"]
    apply_market_plan(plan, str(db_path))
    saved = list_markets(db_path)
    assert [market.name for market in saved] == ["Asian noodles"]
    assert saved[0].companies == ["Nando's"]

    unknown = added.replace("Nando's", "Missing Chain")
    refused = plan_markets(unknown, str(db_path), companies={"Nando's"})
    assert any(issue.column == "companies" for issue in refused.issues)
    assert refused.removed_count == 0
