"""Pytest the MCP tool functions against committed data/ files."""

from __future__ import annotations

from mcp_server import tools
from mcp_server.serialize import HARD_MAX, clamp_limit, dataframe_to_records
from src.branding import MARKET_LABEL, OWN_BRAND
from src.enrichment.schema import GroupingDimension


def test_list_companies_happy_path():
    result = tools.list_companies()
    assert result["own_brand"] == OWN_BRAND
    assert OWN_BRAND not in result["competitors"]
    assert "Nando's" in result["competitors"] or len(result["competitors"]) >= 1


def test_price_kpis_happy_path():
    result = tools.price_kpis()
    assert "overall_cagr_pct" in result
    assert "latest_avg_by_source" in result
    assert result["source"] == "all"
    own = tools.price_kpis(OWN_BRAND)
    assert own["source"] == OWN_BRAND
    assert "error" not in own


def test_price_trends_and_compare_happy_path():
    trends = tools.price_trends(OWN_BRAND, dimension=GroupingDimension.BUCKET.value)
    assert trends["source"] == OWN_BRAND
    assert trends["rows"]
    assert "pct_change" in trends["rows"][0]

    compare = tools.compare_to_market(dimension=GroupingDimension.BUCKET.value)
    assert compare["market"] == MARKET_LABEL
    assert compare["rows"]
    sources = {row["source"] for row in compare["rows"]}
    assert OWN_BRAND in sources
    assert MARKET_LABEL in sources


def test_delivery_markup_and_price_range_happy_path():
    delivery = tools.delivery_markup()
    assert delivery["level"] == "company"
    assert delivery["rows"]

    high_low = tools.price_range()
    assert high_low["level"] == "company"
    assert high_low["rows"]


def test_find_dishes_happy_path():
    result = tools.find_dishes("chicken")
    assert result["rows"]
    assert all("chicken" in row["item"].lower() for row in result["rows"])


def test_find_dishes_uses_each_companys_own_latest_date():
    result = tools.find_dishes("duck soba")
    assert result["rows"]
    duck = next(row for row in result["rows"] if "duck soba" in row["item"].lower())
    assert duck["source"] == OWN_BRAND
    assert duck["price"] == 20.4
    assert duck["date"] == "2026-07-08"


def test_dish_history_returns_the_series():
    result = tools.dish_history("saku saku duck soba", source=OWN_BRAND)
    assert result["items"] == 1
    assert len(result["rows"]) > 1
    assert result["rows"][0]["pct_change"] == 0.0
    assert result["rows"][-1]["price"] == 20.4
    assert result["rows"][-1]["date"] == "2026-07-08"


def test_latest_prices_ranks_mains_by_average():
    result = tools.latest_prices("Mains")
    assert result["group"] == "Mains"
    assert result["rows"]
    averages = [row["average"] for row in result["rows"]]
    assert averages == sorted(averages, reverse=True)
    wagamama = next(row for row in result["rows"] if row["source"] == OWN_BRAND)
    assert wagamama["date"] == "2026-07-08"
    assert wagamama["dishes"] >= 1
    assert wagamama["average"] == max(averages)
    assert "median" in wagamama


def test_latest_prices_unknown_group_lists_valid_values():
    result = tools.latest_prices("Not a course")
    assert "error" in result
    assert "Mains" in result["valid_groups"]


def test_list_markets_and_company_weights():
    markets = tools.list_markets()
    assert isinstance(markets["markets"], list)
    assert markets["count"] == len(markets["markets"])

    weights = tools.company_weights()
    assert weights["rows"]
    assert OWN_BRAND not in {row["company"] for row in weights["rows"]}
    nandos = next(row for row in weights["rows"] if row["company"] == "Nando's")
    assert nandos["restaurants"] == 473
    assert abs(sum(row["weight_pct"] for row in weights["rows"]) - 100) < 0.2


def test_dish_attributes_returns_classification():
    result = tools.dish_attributes("duck soba", source=OWN_BRAND)
    assert result["rows"]
    row = result["rows"][0]
    assert "duck soba" in row["name"].lower()
    assert row["cuisine"]
    assert row["dish_type"]
    assert "is_vegan" in row


def test_data_freshness_happy_path():
    result = tools.data_freshness()
    assert result["latest_history_date"]
    assert isinstance(result["scrape_status"], list)


def test_unknown_company_returns_valid_list():
    result = tools.price_kpis("Not A Real Chain")
    assert "error" in result
    assert OWN_BRAND in result["valid_companies"]
    assert "Nando's" in result["valid_companies"] or len(result["valid_companies"]) > 1

    trends = tools.price_trends("Not A Real Chain")
    assert "error" in trends
    assert "valid_companies" in trends


def test_row_cap_default_and_hard_max():
    assert clamp_limit(None) == 50
    assert clamp_limit(9999) == HARD_MAX
    assert clamp_limit(0) == 1

    capped = tools.find_dishes("a", limit=3)
    assert capped["row_count"] <= 3
    if capped["truncated"]:
        assert capped["row_count"] == 3

    forced = tools.find_dishes("e", limit=500)
    assert forced["row_count"] <= HARD_MAX


def test_missing_mappings_db_clear_message(monkeypatch, tmp_path):
    missing = tmp_path / "does_not_exist.db"
    monkeypatch.setattr(tools, "_mappings_db_path", lambda: missing)

    result = tools.price_trends(
        OWN_BRAND,
        dimension=GroupingDimension.CUISINE.value,
    )
    assert result["mappings_present"] is False
    assert "missing" in result["error"].lower()

    markets = tools.compare_to_market(market_name="Grill dishes")
    assert markets["mappings_present"] is False
    assert "missing" in markets["error"].lower()


def test_glossary_resource_text():
    text = tools.glossary()
    assert OWN_BRAND in text
    assert MARKET_LABEL in text
    assert "observed only" in text.lower()
    assert "synthetic" not in text.lower()


def test_dataframe_to_records_rounds_and_caps():
    import pandas as pd

    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-04-01", "2024-07-01"]),
            "price": [1.234, 2.345, 3.456],
        }
    )
    records = dataframe_to_records(frame, limit=2)
    assert len(records) == 2
    assert records[0]["date"] == "2024-01-01"
    assert records[0]["price"] == 1.23
