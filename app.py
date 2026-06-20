"""Streamlit UI for Nathan's menu scraper.

The UI deliberately knows nothing about any specific site. It reads the list of
chains from the scraper registry, runs whichever scraper the user picks, and
renders the result. Adding a chain never requires touching this file.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from src.export import dataframe_to_csv_bytes, items_to_dataframe
from src.scrapers.registry import SCRAPERS, get_scraper

st.set_page_config(
    page_title="Nathan's Menu Scraper",
    page_icon=":fork_and_knife:",
    layout="wide",
)

st.title("Nathan's Menu Scraper")
st.caption(
    "Pick a food chain, scrape its live menu, and download the items and prices "
    "as a CSV."
)

site_name = st.selectbox("Choose a website to scrape", options=list(SCRAPERS))

if st.button("Scrape menu", type="primary"):
    scraper = get_scraper(site_name)
    try:
        with st.spinner(f"Scraping {site_name}... this can take up to a minute."):
            items = scraper.scrape()
    except Exception as exc:  # surfaced to the user instead of crashing the app
        st.error(f"Scraping failed: {exc}")
    else:
        if not items:
            st.warning(
                "No menu items were found. The site layout may have changed - "
                "the scraper's selectors likely need updating."
            )
        else:
            frame = items_to_dataframe(items)
            st.success(f"Found {len(frame)} menu items.")
            st.dataframe(frame, use_container_width=True)

            stamp = datetime.now().strftime("%Y%m%d_%H%M")
            slug = "".join(c if c.isalnum() else "_" for c in site_name).lower()
            st.download_button(
                label="Download CSV",
                data=dataframe_to_csv_bytes(frame),
                file_name=f"{slug}_menu_{stamp}.csv",
                mime="text/csv",
            )
