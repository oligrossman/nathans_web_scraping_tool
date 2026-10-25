"""The left chart stays a restaurant comparison when a group is chosen."""

from __future__ import annotations

import pandas as pd

from src.drilldown import (
    DISTRIBUTION,
    GROUPS,
    ITEMS,
    RESTAURANTS,
    charts_for,
    items_for_drivers,
    items_for_restaurant_comparison,
    latest_menu_note,
)


def test_a_bucket_keeps_restaurants_on_the_left_and_moves_dishes_right() -> None:
    assert charts_for(None, "Mains") == (RESTAURANTS, ITEMS)
    assert charts_for("Nando's", "Mains") == (RESTAURANTS, ITEMS)


def test_no_bucket_keeps_the_distribution_on_the_right() -> None:
    assert charts_for(None, None) == (RESTAURANTS, DISTRIBUTION)
    assert charts_for("Nando's", None) == (GROUPS, DISTRIBUTION)


def test_restaurant_comparison_ignores_the_company_and_keeps_the_group() -> None:
    frame = pd.DataFrame(
        {
            "company": ["Nando's", "Nando's", "Wagamama"],
            "bucket": ["Mains", "Sides", "Mains"],
            "price": [10.0, 4.0, 12.0],
        }
    )

    compared = items_for_restaurant_comparison(frame, group="Mains", dim="bucket")

    assert compared["company"].tolist() == ["Nando's", "Wagamama"]
    assert compared["bucket"].tolist() == ["Mains", "Mains"]


def test_drivers_follow_both_filters() -> None:
    frame = pd.DataFrame(
        {
            "company": ["Nando's", "Nando's", "Wagamama"],
            "bucket": ["Mains", "Sides", "Mains"],
            "item": ["Chicken", "Chips", "Ramen"],
        }
    )

    drivers = items_for_drivers(
        frame, company="Nando's", group="Mains", dim="bucket"
    )

    assert drivers["item"].tolist() == ["Chicken"]


def test_latest_menu_note_names_a_single_scrape_date() -> None:
    frame = pd.DataFrame({"as_of": ["2026-07-08", "2026-07-08"]})

    note = latest_menu_note(frame)

    assert "8 Jul 2026" in note
    assert "not an average" in note
