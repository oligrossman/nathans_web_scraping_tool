"""Contents-page facts stay tied to the registry and the price file."""

from datetime import date

import pandas as pd

from src.guide import menu_sources, price_date_coverage, restaurant_menu_kind
from src.restaurants.config import Restaurant, load_restaurants


def _restaurant(**overrides) -> Restaurant:
    fields = dict(
        brand="Example",
        location="Brighton",
        slug="example-brighton",
        restaurant_url="https://example.test/menu",
        delivery_url="https://deliveroo.test/menu",
        delivery_platform="Deliveroo",
        postcode="BN1 1AA",
        site_count=3,
    )
    fields.update(overrides)
    return Restaurant(**fields)


def test_pdf_menus_are_labelled_as_pdfs() -> None:
    web = _restaurant()
    pdf = _restaurant(restaurant_url="https://example.test/menu.pdf")
    assert restaurant_menu_kind(web) == "Restaurant site"
    assert restaurant_menu_kind(pdf) == "PDF menu"


def test_registry_row_follows_the_restaurant_file() -> None:
    frame = menu_sources()
    stored = next(row for row in load_restaurants() if row.brand == "Wagamama")
    shown = frame.loc[frame["Brand"] == "Wagamama"].iloc[0]
    assert shown["Location"] == stored.location
    assert shown["Delivery"] == stored.delivery_platform
    assert shown["Restaurant menu"] == "Restaurant site"
    assert shown["Tracker sheet"] == "wagamama"
    assert shown["Market weight"] == "Excluded"

    pho = frame.loc[frame["Brand"] == "Pho"].iloc[0]
    assert pho["Restaurant menu"] == "PDF menu"
    assert pho["Tracker sheet"] == "—"


def test_archive_dates_are_scrapes_and_other_dates_are_tracker() -> None:
    prices = pd.DataFrame(
        {
            "date": ["2026-04-01", "2026-07-08", "2026-02-01"],
            "source": ["Wagamama", "Wagamama", "Wagamama"],
        }
    )
    covered = price_date_coverage(prices, [date(2026, 7, 8)], ["Wagamama", "Pho"])
    wagamama = covered.loc[covered["Brand"] == "Wagamama"].iloc[0]
    pho = covered.loc[covered["Brand"] == "Pho"].iloc[0]
    assert wagamama["Tracker dates"] == "2026-02-01 to 2026-04-01 (2 dates)"
    assert wagamama["Scrape dates"] == "2026-07-08"
    assert pho["Tracker dates"] == "—"
    assert pho["Scrape dates"] == "—"
