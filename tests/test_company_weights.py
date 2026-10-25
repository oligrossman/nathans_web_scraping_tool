"""Market whole is a restaurant-count weighted average, and the counts can be saved."""

import pandas as pd
import pytest

from src.analytics import (
    build_market_series,
    load_company_weights,
    normalise_company_weights,
    save_company_weights,
    weighted_company_mean,
    with_derived_market,
)
from src.branding import MARKET_LABEL, OWN_BRAND
from src.enrichment.schema import MarketDefinition
from src.guide import market_weight_rows
from src.restaurants.config import Restaurant


def _history() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-01"],
            "bucket": ["Mains", "Mains"],
            "source": ["Nando's", "Pho"],
            "item": ["Chicken", "Pho house"],
            "price": [12.0, 10.0],
        }
    )


def _restaurant(brand: str, site_count: int) -> Restaurant:
    return Restaurant(
        brand=brand,
        location="Brighton",
        slug=brand.casefold().replace(" ", "-"),
        restaurant_url="https://example.test/menu",
        delivery_url="https://deliveroo.test/menu",
        delivery_platform="Deliveroo",
        postcode="BN1 1AA",
        site_count=site_count,
    )


def test_market_pulls_toward_the_larger_estate() -> None:
    means = pd.DataFrame(
        {"source": ["Nando's", "Pho"], "price": [12.0, 10.0]}
    )
    # 400 restaurants at £12 and 100 at £10: (12×400 + 10×100) / 500 = £11.60.
    assert weighted_company_mean(means, {"Nando's": 400, "Pho": 100}) == pytest.approx(11.6)

    market = with_derived_market(_history(), weights={"Nando's": 400, "Pho": 100})
    point = market.loc[market["source"] == MARKET_LABEL, "price"].item()
    assert point == 11.6


def test_a_company_with_no_count_is_left_out_of_the_market() -> None:
    market = with_derived_market(_history(), weights={"Nando's": 400})
    point = market.loc[market["source"] == MARKET_LABEL, "price"].item()
    assert point == 12.0
    assert "Pho" in set(market["source"])

    custom = build_market_series(
        _history(),
        MarketDefinition(name="Casual", companies=["Nando's", "Pho"]),
        weights={"Nando's": 400},
    )
    assert custom["price"].item() == 12.0


def test_equal_average_when_nobody_has_a_count() -> None:
    custom = build_market_series(
        _history(),
        MarketDefinition(name="Casual", companies=["Nando's", "Pho"]),
        weights={},
    )
    assert custom["price"].item() == 11.0


def test_save_drops_a_blank_count_and_refuses_the_own_brand(tmp_path) -> None:
    path = tmp_path / "company_weights.csv"
    frame = pd.DataFrame(
        {
            "company": ["Pho", "Rosa's Thai"],
            "restaurants": [43, pd.NA],
        }
    )
    saved = save_company_weights(frame, path)
    assert saved["company"].tolist() == ["Pho"]
    assert saved["restaurants"].tolist() == [43]
    assert load_company_weights(path) == {"Pho": 43.0}
    assert "company,restaurants" in path.read_text(encoding="utf-8")

    refused = pd.DataFrame({"company": [OWN_BRAND], "restaurants": [173]})
    with pytest.raises(ValueError, match="left out"):
        normalise_company_weights(refused)

    zero = pd.DataFrame({"company": ["Pho"], "restaurants": [0]})
    with pytest.raises(ValueError, match="greater than zero"):
        normalise_company_weights(zero)

    duplicate = pd.DataFrame(
        {"company": ["Pho", "Pho"], "restaurants": [10, 12]}
    )
    with pytest.raises(ValueError, match="more than once"):
        normalise_company_weights(duplicate)


def test_editor_lists_a_missing_weight_and_keeps_a_retired_company(tmp_path) -> None:
    path = tmp_path / "company_weights.csv"
    path.write_text(
        "company,restaurants\nPho,43\nRetired,7\nWagamama,173\n",
        encoding="utf-8",
    )
    rows = market_weight_rows(
        [
            _restaurant("Pho", 43),
            _restaurant("Rosa's Thai", 40),
            _restaurant(OWN_BRAND, 173),
        ],
        path,
    )
    assert OWN_BRAND not in set(rows["company"])
    pho = rows.loc[rows["company"] == "Pho"].iloc[0]
    assert pho["uk_sites"] == 43
    assert pho["restaurants"] == 43
    rosa = rows.loc[rows["company"] == "Rosa's Thai"].iloc[0]
    assert pd.isna(rosa["restaurants"])
    retired = rows.loc[rows["company"] == "Retired"].iloc[0]
    assert pd.isna(retired["uk_sites"])
    assert retired["restaurants"] == 7
