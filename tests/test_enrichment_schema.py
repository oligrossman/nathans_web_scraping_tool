from src.enrichment.identity import make_dish_id
from src.enrichment.schema import (
    DISH_CLASSIFICATION_SCHEMA,
    DishMapping,
    DishType,
    GroupingDimension,
)


def test_from_llm_payload_populates_dish_type():
    mapping = DishMapping.from_llm_payload(
        dish_id="abc123",
        source="Wagamama",
        name="Chicken Katsu Curry",
        bucket="Mains",
        payload={
            "cuisine": "Japanese",
            "dish_type": DishType.CURRY.value,
            "is_vegetarian": False,
            "is_vegan": False,
        },
    )
    assert mapping.dish_type == "Curry"
    assert mapping.source == "Wagamama"
    assert mapping.is_vegetarian is False


def test_make_dish_id_is_restaurant_specific_and_stable():
    a = make_dish_id("Nando's", "Mac and Cheese")
    b = make_dish_id("Pizza Express", "Mac and Cheese")
    assert a != b  # same name, different restaurant -> different id
    # Stable under case and whitespace differences.
    assert a == make_dish_id("nando's", "  mac  and   CHEESE ")


def test_classification_schema_only_allows_known_fields():
    props = DISH_CLASSIFICATION_SCHEMA["properties"]
    assert set(props) == {"cuisine", "dish_type", "is_vegetarian", "is_vegan"}
    assert DISH_CLASSIFICATION_SCHEMA["additionalProperties"] is False
    assert props["dish_type"]["enum"] == [member.value for member in DishType]


def test_grouping_dimensions_are_bucket_cuisine_dish_type():
    assert [d.value for d in GroupingDimension] == ["bucket", "cuisine", "dish_type"]
