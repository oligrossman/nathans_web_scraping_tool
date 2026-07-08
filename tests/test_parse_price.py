"""Regression tests for price parsing from scraped menu text."""

from src.scrapers.base import parse_price


def test_parse_price_prefers_gbp_over_volume_in_name() -> None:
    text = "Peroni 660ml\n5% ABV\n£7.95"
    assert parse_price(text) == 7.95


def test_parse_price_prefers_gbp_over_abv_in_description() -> None:
    text = "Beyond Belief Brewing Pasta Beer Pale Ale\nPale Ale 4.7% ABV 330ml\n£6.45"
    assert parse_price(text) == 6.45


def test_parse_price_ignores_protein_claim_before_gbp() -> None:
    text = "Pepperoni Campagna\nOver 50g of protein.\n£20.25"
    assert parse_price(text) == 20.25


def test_parse_price_ignores_measure_in_spirit_name() -> None:
    text = "Jamesons Irish Whiskey 25ml\n£4.50"
    assert parse_price(text) == 4.50


def test_parse_price_plain_number_fallback() -> None:
    assert parse_price("12.95") == 12.95
