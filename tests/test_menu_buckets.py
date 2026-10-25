"""Drink split and the rule that a shifted category column is not a heading."""

from __future__ import annotations

import pandas as pd

from src.menu_buckets import (
    ALCOHOLIC_DRINKS,
    NON_ALCOHOLIC_DRINKS,
    assign_buckets,
    keep_legacy_drink,
    migrate_bucket_list,
    resolve_bucket,
)


def test_generic_drinks_heading_splits_on_the_dish_name() -> None:
    assert resolve_bucket("Drinks", "Bottle of Coca-Cola") == NON_ALCOHOLIC_DRINKS
    assert resolve_bucket("Drinks", "Camden Pale Ale") == ALCOHOLIC_DRINKS
    assert resolve_bucket("Drinks", "Beavertown Lazer Crush Alcohol Free IPA") == (
        NON_ALCOHOLIC_DRINKS
    )
    assert resolve_bucket("Drinks", "Spier Rose (South Africa)") == ALCOHOLIC_DRINKS
    assert resolve_bucket("Drinks", "Raspberry & Rose Lemonade") == NON_ALCOHOLIC_DRINKS


def test_zero_alcohol_wins_over_a_beer_heading() -> None:
    assert resolve_bucket("BEER & CIDER", "Peroni 0.0%") == NON_ALCOHOLIC_DRINKS
    assert resolve_bucket("BEER & CIDER", "Peroni 330ml") == ALCOHOLIC_DRINKS
    assert (
        resolve_bucket("beer, cider + alcohol-free", "asahi zero (vg)")
        == NON_ALCOHOLIC_DRINKS
    )


def test_missed_drink_headings_are_no_longer_mains() -> None:
    assert resolve_bucket("FIZZ", "House Prosecco 125ml") == ALCOHOLIC_DRINKS
    assert resolve_bucket("SPIRITS", "Sipsmith London Dry Gin 25ml") == ALCOHOLIC_DRINKS
    assert resolve_bucket("LIQUEURS", "Baileys 50ML") == ALCOHOLIC_DRINKS
    assert resolve_bucket("MOCKTAILS & SOFTS", "Ginger Ale") == NON_ALCOHOLIC_DRINKS
    assert resolve_bucket("MOCKTAILS & SOFTS", "Mango & Lime Cooler") == NON_ALCOHOLIC_DRINKS


def test_a_meal_or_dessert_leaves_a_mixed_drinks_heading() -> None:
    assert resolve_bucket("Drinks", "Kids Burger With Soda") == "Mains"
    assert resolve_bucket("kids drinks + desserts", "mini brownie bite (v)") == "Desserts"
    assert (
        resolve_bucket(
            "kids drinks + desserts", "cawston press apple + pear juice drink (vg)"
        )
        == NON_ALCOHOLIC_DRINKS
    )


def test_a_spritz_leaves_an_aperitivo_list_and_the_snacks_stay() -> None:
    assert resolve_bucket("APERITIVO", "Aperol Spritz") == ALCOHOLIC_DRINKS
    assert resolve_bucket("APERITIVO", "Pasta Crisps") == "Starters"


def test_a_food_section_is_not_reclassified_because_the_name_mentions_a_drink() -> None:
    blob = "Phojito - our signature cocktail; white rum, mint, lime & soda"
    assert resolve_bucket("Classics", blob) == "Mains"
    assert resolve_bucket("extras", "tea-stained egg (v)") == "Mains"


def test_shifted_category_column_uses_the_dish_name() -> None:
    """Five Guys writes the previous dish into the category cell."""
    menu = pd.DataFrame(
        {
            "name": ["Milkshake", "Large Cajun Fries", "Kids Burger With Juice"],
            "category": ["LARGE CAJUN FRIES", "KIDS BURGER WITH JUICE", "MILKSHAKE"],
        }
    )

    buckets = assign_buckets(menu).tolist()

    assert buckets == [NON_ALCOHOLIC_DRINKS, "Sides", "Mains"]


def test_a_heading_used_by_many_dishes_stays_a_heading() -> None:
    menu = pd.DataFrame(
        {
            "name": ["Margherita", "Pepperoni"],
            "category": ["Pizza", "Pizza"],
        }
    )

    assert assign_buckets(menu).tolist() == ["Mains", "Mains"]


def test_a_beer_that_mentions_pizza_or_pasta_stays_a_drink() -> None:
    assert (
        resolve_bucket("Drinks", "Meantime Pizza Pale (ABV 4%) - 330ml")
        == ALCOHOLIC_DRINKS
    )
    assert (
        resolve_bucket("BEER & CIDER", "Beyond Belief Brewing Pasta Beer IPA")
        == ALCOHOLIC_DRINKS
    )


def test_food_words_are_not_caught_by_a_shorter_drink_or_dessert_word() -> None:
    assert resolve_bucket(None, "Classic Platter for 2") == "Mains"
    assert resolve_bucket(None, "Duck Pancakes") == "Mains"
    assert resolve_bucket(None, "Chicken Milanese with Fries") == "Mains"
    assert resolve_bucket(None, "House Fries") == "Sides"
    assert resolve_bucket("Cambridge", "Tiramisu") == "Desserts"


def test_a_vague_legacy_drink_stays_a_soft_drink_and_a_kids_meal_does_not() -> None:
    assert keep_legacy_drink("RETURNING: Pistachio", "Mains") == NON_ALCOHOLIC_DRINKS
    assert keep_legacy_drink("Kids Burger With Soda", "Mains") == "Mains"


def test_saved_drinks_filter_becomes_both_drink_buckets() -> None:
    assert migrate_bucket_list(["Mains", "Drinks", "Desserts"]) == [
        "Mains",
        NON_ALCOHOLIC_DRINKS,
        ALCOHOLIC_DRINKS,
        "Desserts",
    ]
