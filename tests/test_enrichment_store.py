from pathlib import Path

from src.enrichment.identity import make_dish_id
from src.enrichment.schema import DishMapping, DishType
from src.enrichment.store import (
    get_resolved_mappings,
    init_db,
    set_override,
    upsert_llm_mapping,
)


def test_resolved_mapping_prefers_override(tmp_path: Path):
    db_path = tmp_path / "mappings.db"
    init_db(db_path)
    dish_id = make_dish_id("Wagamama", "Pad Thai")
    upsert_llm_mapping(
        DishMapping(
            dish_id=dish_id,
            source="Wagamama",
            name="Pad Thai",
            bucket="Mains",
            cuisine="Thai",
            dish_type=DishType.NOODLES.value,
            is_vegetarian=False,
            is_vegan=False,
        ),
        model="test-model",
        db_path=db_path,
    )
    set_override(
        dish_id,
        cuisine="PanAsian",
        db_path=db_path,
    )

    resolved = get_resolved_mappings(db_path)
    row = resolved.loc[resolved["dish_id"] == dish_id].iloc[0]
    assert row["source"] == "Wagamama"
    assert row["cuisine"] == "PanAsian"
    assert row["dish_type"] == DishType.NOODLES.value
    assert bool(row["is_override"]) is True
