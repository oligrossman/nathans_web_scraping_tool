import pandas as pd

from src.analytics import dimension_means, enriched_history
from src.enrichment.identity import make_dish_id
from src.enrichment.schema import DishMapping, DishType, GroupingDimension
from src.enrichment.store import init_db, upsert_llm_mapping


def _map(db_path, source, name, cuisine, dish_type):
    upsert_llm_mapping(
        DishMapping(
            dish_id=make_dish_id(source, name),
            source=source,
            name=name,
            bucket="Mains",
            cuisine=cuisine,
            dish_type=dish_type,
            is_vegetarian=False,
            is_vegan=False,
        ),
        model="test-model",
        db_path=db_path,
    )


def test_dimension_means_by_dish_type(tmp_path):
    db_path = tmp_path / "mappings.db"
    init_db(db_path)
    _map(db_path, "Wagamama", "Item A", "Japanese", DishType.NOODLES.value)
    _map(db_path, "Wagamama", "Item B", "Italian", DishType.PIZZA.value)

    history = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2024-03-31"),
                "source": "Wagamama",
                "item": "Item A",
                "bucket": "Mains",
                "price": 10.0,
            },
            {
                "date": pd.Timestamp("2024-03-31"),
                "source": "Wagamama",
                "item": "Item B",
                "bucket": "Mains",
                "price": 12.0,
            },
        ]
    )
    enriched = enriched_history(history, db_path=db_path)
    means = dimension_means(
        enriched,
        source="Wagamama",
        dimension=GroupingDimension.DISH_TYPE,
    )
    values = set(means["bucket"])
    assert values == {"Noodles", "Pizza"}
    noodles = means.loc[means["bucket"] == "Noodles", "price"].iloc[0]
    assert noodles == 10.0


def test_same_name_different_restaurants_map_independently(tmp_path):
    db_path = tmp_path / "mappings.db"
    init_db(db_path)
    # Same item name, two restaurants -> two distinct dish_ids and mappings.
    _map(db_path, "Nando's", "Mac and Cheese", "American", DishType.SIDE.value)
    _map(db_path, "Pizza Express", "Mac and Cheese", "Italian", DishType.PASTA.value)

    history = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2024-03-31"),
                "source": "Nando's",
                "item": "Mac and Cheese",
                "bucket": "Sides",
                "price": 4.0,
            },
            {
                "date": pd.Timestamp("2024-03-31"),
                "source": "Pizza Express",
                "item": "Mac and Cheese",
                "bucket": "Mains",
                "price": 11.0,
            },
        ]
    )
    enriched = enriched_history(history, db_path=db_path)
    by_source = enriched.set_index("source")["dish_type"].to_dict()
    assert by_source["Nando's"] == DishType.SIDE.value
    assert by_source["Pizza Express"] == DishType.PASTA.value
