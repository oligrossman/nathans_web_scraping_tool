"""Streamlit page: comparison configuration.

Controls that shape the Price Analysis charts: the grouping dimension, dish
attribute mappings (with user overrides), and custom market definitions. All
settings are shared with the analytics page via ``st.session_state`` and the
SQLite mappings database.
"""

from __future__ import annotations

import streamlit as st

from src import analytics
from src.branding import (
    APP_TITLE,
    CONFIG_PAGE_TITLE,
    OWN_BRAND,
    apply_sidebar_branding,
    brand_css,
)
from src.enrichment.schema import Cuisine, DishType, MarketDefinition
from src.enrichment.store import (
    DEFAULT_DB_PATH,
    delete_market,
    enum_options,
    get_resolved_mappings,
    init_db,
    list_markets,
    save_market,
    set_override,
)
from src.synthetic import BUCKETS

st.set_page_config(
    page_title=f"{CONFIG_PAGE_TITLE} | {APP_TITLE}",
    page_icon="⚙️",
    layout="wide",
)

apply_sidebar_branding()
st.markdown(brand_css(), unsafe_allow_html=True)

st.title(CONFIG_PAGE_TITLE)
st.caption(
    "Configure how the Price Analysis charts group and benchmark prices. "
    "All market benchmarks are competitor-only and never include Wagamama dishes."
)

init_db()

# Competitors feed the "limit to companies" option on custom markets.
history_path = analytics.DEFAULT_HISTORY_PATH
if history_path.exists():
    competitors = analytics.competitor_sources(analytics.load_history(history_path))
else:
    competitors = []

saved_markets = list_markets()

# --- Dish mappings ---------------------------------------------------------
st.subheader("Dish mappings")

if not DEFAULT_DB_PATH.exists() or get_resolved_mappings().empty:
    st.warning(
        "No dish mappings found. Run `python -m scripts.enrich_menus` "
        "(requires `OPENAI_API_KEY`) to classify menu items offline."
    )
else:
    mappings = get_resolved_mappings()
    options = enum_options()
    column_config = {
        "dish_id": st.column_config.TextColumn("ID", disabled=True),
        "source": st.column_config.TextColumn("Restaurant", disabled=True),
        "name": st.column_config.TextColumn("Item", disabled=True),
        "bucket": st.column_config.SelectboxColumn("Bucket", options=options["bucket"]),
        "cuisine": st.column_config.SelectboxColumn(
            "Cuisine", options=options["cuisine"]
        ),
        "dish_type": st.column_config.SelectboxColumn(
            "Dish type", options=options["dish_type"]
        ),
        "is_vegetarian": st.column_config.CheckboxColumn("Vegetarian"),
        "is_vegan": st.column_config.CheckboxColumn("Vegan"),
        "is_override": st.column_config.CheckboxColumn(
            "Override active", disabled=True
        ),
    }
    edited = st.data_editor(
        mappings,
        column_config=column_config,
        hide_index=True,
        use_container_width=True,
        key="mapping_editor",
    )

    if st.button("Save mapping overrides", type="primary"):
        for _, row in edited.iterrows():
            original = mappings[mappings["dish_id"] == row["dish_id"]].iloc[0]
            changed = any(
                row[col] != original[col]
                for col in (
                    "bucket",
                    "cuisine",
                    "dish_type",
                    "is_vegetarian",
                    "is_vegan",
                )
            )
            if changed:
                set_override(
                    row["dish_id"],
                    bucket=row["bucket"],
                    cuisine=row["cuisine"],
                    dish_type=row["dish_type"],
                    is_vegetarian=bool(row["is_vegetarian"]),
                    is_vegan=bool(row["is_vegan"]),
                )
        st.success("Overrides saved.")
        st.rerun()

st.divider()

# --- Custom markets --------------------------------------------------------
st.subheader("Define a custom market")
st.caption(
    "Markets are competitor benchmarks — Wagamama dishes are always excluded. "
    f"Saved markets can then be plotted on the **{OWN_BRAND} vs market** tab."
)

with st.form("market_form"):
    market_name = st.text_input("Market name", placeholder="Asian noodle dishes")
    col1, col2 = st.columns(2)
    with col1:
        filter_cuisines = st.multiselect(
            "Cuisines",
            [member.value for member in Cuisine],
        )
        filter_dish_types = st.multiselect(
            "Dish types",
            [member.value for member in DishType],
        )
    with col2:
        filter_buckets = st.multiselect("Buckets", list(BUCKETS))
        filter_companies = st.multiselect(
            "Limit to companies (optional)",
            competitors,
        )
    submitted = st.form_submit_button("Save market")

if submitted:
    if not market_name.strip():
        st.error("Enter a market name.")
    else:
        definition = MarketDefinition(
            name=market_name.strip(),
            cuisines=filter_cuisines,
            dish_types=filter_dish_types,
            companies=filter_companies,
            buckets=filter_buckets,
        )
        save_market(definition)
        st.success(f"Saved market **{definition.name}**.")
        st.rerun()

if saved_markets:
    st.markdown("#### Saved markets")
    for market in saved_markets:
        cols = st.columns([4, 1])
        with cols[0]:
            st.markdown(f"**{market.name}** — {market.to_dict()}")
        with cols[1]:
            if st.button("Delete", key=f"delete_market_{market.name}"):
                delete_market(market.name)
                st.rerun()
