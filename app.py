"""Wagamama Menu Intelligence — navigation entrypoint."""

from __future__ import annotations

import streamlit as st

from src.branding import ANALYTICS_TITLE, APP_TITLE, DATA_PAGE_TITLE

st.set_page_config(page_title=APP_TITLE, page_icon="🍜", layout="wide")

data_page = st.Page("pages/data.py", title=DATA_PAGE_TITLE, default=True, icon="🗂️")
analytics_page = st.Page(
    "pages/1_Price_Analytics.py",
    title=ANALYTICS_TITLE,
    icon="📊",
)

pg = st.navigation([data_page, analytics_page])
pg.run()
