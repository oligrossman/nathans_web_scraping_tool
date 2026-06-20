"""Wagamama brand constants — single source for UI copy and chart colours."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

OWN_BRAND = "Wagamama"

# Previous prototype label — normalised to OWN_BRAND when loading history.
LEGACY_OWN_BRAND = "WagaNath"

# Logo sourced from Wikimedia Commons (public domain text logo):
# https://commons.wikimedia.org/wiki/File:Wagamama-Logo.svg
_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "wagamama_logo.svg"
# Bowl + star brand mark from Visit Tampa Bay Wagamama listing gallery:
# https://www.visittampabay.com/listings/wagamama/19082/
_SIDEBAR_FOOTER_LOGO_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "wagamama_sidebar_logo.png"
)
WAGAMAMA_WEB = "https://www.wagamama.com/"

# Brand palette
WAGAMAMA_RED = "#D11317"
DELIVEROO_TEAL = "#00CCBC"
MARKET_GREY = "#6C757D"
TEXT_DARK = "#1A1A1A"
SURFACE_GREY = "#F5F5F5"

APP_TITLE = "Wagamama Market Analysis"
DATA_PAGE_TITLE = "Data"
APP_TAGLINE = (
    "Competitive menu pricing intelligence — scrape live competitor menus, "
    "track price trends, and benchmark Wagamama against the market."
)
SCRAPE_SECTION = "Menu data collection"
ANALYTICS_TITLE = "Price Analysis"
ANALYTICS_TAGLINE = (
    "Synthetic quarterly price history (5 years) for Wagamama, competitor "
    "companies and the wider market. Market is a restaurant-count-weighted "
    "average of competitor companies (see `data/company_weights.csv`). "
    "Synthetic data is for demonstration."
)

# Chart source colours — Wagamama red is always the hero; competitors stay distinct.
SOURCE_COLOURS: dict[str, str] = {
    OWN_BRAND: WAGAMAMA_RED,
    LEGACY_OWN_BRAND: WAGAMAMA_RED,
    "Market": MARKET_GREY,
    "Nando's": "#B8860B",
    "Pizza Express": "#0E7C61",
}


def apply_sidebar_logo() -> None:
    """Show the Wagamama wordmark at the top of the sidebar."""
    st.logo(str(_LOGO_PATH), link=WAGAMAMA_WEB, size="large")


def apply_sidebar_footer_logo() -> None:
    """Show the Wagamama bowl logo beneath the page navigation links."""
    with st.sidebar:
        st.divider()
        st.image(str(_SIDEBAR_FOOTER_LOGO_PATH), use_container_width=True)


def apply_sidebar_branding() -> None:
    """Apply all sidebar branding (top wordmark + footer logo)."""
    apply_sidebar_logo()
    apply_sidebar_footer_logo()


def brand_css() -> str:
    """Inject Wagamama-themed styles into Streamlit pages."""
    return f"""
    <style>
    /* Accent bar beneath the main title */
    h1 {{
        color: {TEXT_DARK};
        font-weight: 700;
        letter-spacing: -0.02em;
        border-bottom: 3px solid {WAGAMAMA_RED};
        padding-bottom: 0.4rem;
        margin-bottom: 0.25rem;
    }}
    h2, h3 {{
        color: {TEXT_DARK};
        font-weight: 600;
    }}
    /* Primary buttons inherit theme primaryColor (#D11317) via config.toml */
    [data-testid="stMetricValue"] {{
        color: {TEXT_DARK};
    }}
    div[data-testid="stMetricDelta"] svg {{
        display: none;
    }}
    [data-testid="stPlotlyChart"] svg .scatterpoints path,
    [data-testid="stPlotlyChart"] svg .points path,
    [data-testid="stPlotlyChart"] svg .scatterlayer path {{
        cursor: pointer !important;
    }}
    </style>
    """
