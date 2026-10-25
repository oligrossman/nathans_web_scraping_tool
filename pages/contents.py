"""Contents page — where the menus come from, and how the app treats them."""

from __future__ import annotations

import streamlit as st

from src import auth
from src.app_reload import reload_branding
from src.github_data import render_sync_banner
from src.guide import (
    WORKBOOK_NAME,
    load_price_date_coverage,
    menu_sources,
    tracker_notes_in_registry_order,
)
from src.menu_buckets import BUCKETS

_branding = reload_branding()
APP_TAGLINE = _branding.APP_TAGLINE
APP_TITLE = _branding.APP_TITLE
CONTENTS_PAGE_TITLE = _branding.CONTENTS_PAGE_TITLE
MARKET_LABEL = _branding.MARKET_LABEL
apply_sidebar_branding = _branding.apply_sidebar_branding
brand_css = _branding.brand_css

auth.require_login()

apply_sidebar_branding()
st.markdown(brand_css(), unsafe_allow_html=True)
st.title(CONTENTS_PAGE_TITLE)
st.caption(APP_TAGLINE)
render_sync_banner()

st.markdown(
    "This page is the map of the data. The tables are read from the restaurant "
    "registry and the price file, so they change when those files change."
)

st.subheader("Where each menu comes from")
st.markdown(
    "Every brand is one restaurant location, scraped twice: the restaurant menu, "
    "and the delivery menu at that same location. The postcode is the store that "
    "was scraped. **UK sites** is the chain's restaurant count from the restaurant "
    "list. **Market weight** is the count used for "
    f"**{MARKET_LABEL}**. It starts as the UK site count, and you change it on "
    "**Comparison Configuration**. A blank weight leaves that company out. "
    "Wagamama is left out of that average."
)
st.dataframe(menu_sources(), use_container_width=True, hide_index=True)

notes = tracker_notes_in_registry_order()
if notes:
    st.markdown(
        f"The tracker sheet is the tab in `{WORKBOOK_NAME}` that **Update price "
        "history** matches onto the scraped menu. A few brands need an extra line:"
    )
    for brand, note in notes:
        st.markdown(f"- **{brand}** — {note}")

st.subheader("Where each price date comes from")
st.markdown(
    f"**Tracker dates** come from the Wagamama pricing team, via "
    f"`{WORKBOOK_NAME}`. **Update price history** matches each dish on that "
    "workbook onto the current scraped menu and stores the matched dates in "
    "`data/scrapes/prior/price_history.csv`. A tracker dish that is not the same "
    "dish as a scraped item is left out. Brands start on different dates, so the "
    "table shows each brand's own span rather than one cutoff for the whole file."
)
st.markdown(
    "**Scrape dates** are live restaurant-menu snapshots. Each scrape is filed "
    "under the calendar day it ran, in `data/scrapes/<YYYY-MM-DD>/`. Scraping "
    "the same site again on that same day replaces that day's file. Scraping it "
    "on another day adds another date, including another day in the same month. "
    "Both dates stay until you delete that folder on the **Data** page and run "
    "**Update price history** again."
)
st.markdown(
    "Delivery snapshots are kept and used for the restaurant-versus-delivery "
    "view. They are not added as dates in the price history. Only the restaurant "
    "menu is stacked onto the tracker."
)

coverage = load_price_date_coverage()
if coverage["Tracker dates"].eq("—").all() and coverage["Scrape dates"].eq("—").all():
    st.info("Price history has not been built yet. On Data, click **Update price history**.")
else:
    st.dataframe(coverage, use_container_width=True, hide_index=True)
st.caption(
    "On Streamlit Cloud, a `DATA_UPLOAD` secret copies these saves back to the "
    "repository so they are still there after the app restarts."
)

st.subheader("Classifying dishes")
st.markdown(
    "A dish carries a course, a cuisine, a dish type, and vegetarian and vegan "
    f"flags. The courses are: {', '.join(BUCKETS)}."
)
st.markdown(
    "- **OpenAI.** **Run enrichment** on the Data page sends dishes that have no "
    "row in `menu_mappings.db` to OpenAI. Dishes that are already classified are "
    "left as they are. This needs `OPENAI_API_KEY` and it uses the paid API. "
    "The same job can be run locally with `python -m scripts.enrich_menus`."
)
st.markdown(
    "- **By hand.** Comparison Configuration edits one dish in the grid. That "
    "edit is stored as an override, so a later enrichment of new dishes does "
    "not replace it."
)
st.markdown(
    "- **From a spreadsheet.** Download the dish CSV on Comparison Configuration, "
    "edit it, and import it. You can add a dish, change its classification, or "
    "remove a dish. A value outside the allowed lists is reported as a warning "
    "and that row is left unchanged. An empty file, or a file missing a required "
    "column, is rejected and does not delete the stored dishes. Leave `dish_id` "
    "blank when you add a row. Prices are not edited through this CSV."
)
st.caption(
    "`dish_id` is a stable id for the restaurant and the dish name. A spreadsheet "
    "sometimes turns an id that contains the letter e into a number. The import "
    "puts the stored id back when that number matches exactly one known dish."
)

st.subheader("What Price Analysis shows")
st.markdown(
    f"The charts use the observed prices only. **{MARKET_LABEL}** multiplies each "
    "competitor's average price by its market weight, then divides by the sum "
    "of those weights. Ticking a company on Price Analysis draws that company's "
    "line. It does not change the weights. **Total** is a different number: "
    "once each course has an average, Total is the equal mean of those course "
    "averages, so Mains counts the same as Sides. A custom market, saved on "
    "Comparison Configuration, uses the same weights on the dishes it keeps."
)
st.markdown(
    "Restaurant versus delivery compares the scraped restaurant menu with the "
    "scraped delivery menu for the same location. Deliveroo delivery and service "
    "fees at checkout are not in those prices. High versus low reads the latest "
    "restaurant-menu snapshots."
)

st.subheader("The pages")
st.markdown(
    "- **Contents** — this page.\n"
    "- **Data** — scrape the menus, classify new dishes, update the price file, "
    "and delete a scrape date.\n"
    "- **Price Analysis** — trends, market comparison, delivery markup, and "
    "high versus low.\n"
    "- **Comparison Configuration** — manual classification, the CSV import, "
    "market weights, and custom markets."
)
